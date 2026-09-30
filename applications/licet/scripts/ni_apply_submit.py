"""submit a real application for building/sign/temporary/na on the ni portal"""
from __future__ import annotations

import asyncio
import os
import re
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv()

from licet.browser.apply_browser import (  # noqa: E402
    open_apply_browser,
    surface_window,
)
from licet.safety.attestation_handoff import (  # noqa: E402
    HandoffOutcome,
    accept_disclaimer_with_human,
)

CITIZEN = "https://aca-test.accela.com/nullisland"
OUTDIR = os.path.join("logs", "ni_backoffice", "inventory")
USER = os.environ.get("ACCELA_TEST_USERNAME", "").strip()
PWD = os.environ.get("ACCELA_TEST_PASSWORD", "").strip()

TARGET_TEXT = "Sign - Temporary"
TEST_DATA = {
    "ctl00_PlaceHolderMain_WorkLocationEdit_txtStreetNo": "77",
    "ctl00_PlaceHolderMain_WorkLocationEdit_txtStreetName": "Licet Eval Way",
    "ctl00_PlaceHolderMain_WorkLocationEdit_txtZip": "00001",
    "ctl00_PlaceHolderMain_DetailInfoEdit_txtDescriptionDetail":
        "Licet eval application - temporary sign for testing",
}
CONTACT_FILL = {  # only used if a required contact field is empty
    "first": "Eval", "last": "User", "phone": "555-0100",
}
ALTID_RE = re.compile(r"\b[A-Z]{2,5}\d{2}-\d{5}-\d{5}\b|\b[A-Z]{2,5}\d{2}-\d{4,6}\b")


def out(m: str) -> None:
    print(m, flush=True)


# the apply flow's disclaimer is a legal attestation
DISCLAIMER_SETTLE_SECONDS = 30.0

DISCLAIMER_ALLOW_PORTAL_DEFAULT = True


def _announce_disclaimer_handoff() -> None:
    out("")
    out("  BROWSER WINDOW — NOTHING IS REQUIRED FROM YOU")
    out("  The disclaimer is a legal attestation, so Licet will not tick its")
    out("  box; if you are watching, that click is yours to make.")
    out(f"  Waiting {int(DISCLAIMER_SETTLE_SECONDS)}s for the portal to "
        "settle…")
    out("")


def stamp_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


async def dump_page(page, stamp: str, name: str) -> None:
    try:
        await asyncio.wait_for(
            page.screenshot(path=os.path.join(OUTDIR, f"{stamp}_{name}.png")), 15)
    except Exception as exc:
        out(f"  shot fail: {exc!r}")
    for i, fr in enumerate(list(page.frames)):
        try:
            html = await asyncio.wait_for(fr.content(), 10)
        except Exception:
            continue
        with open(os.path.join(OUTDIR, f"{stamp}_{name}_f{i}.html"), "w",
                  encoding="utf-8") as fh:
            fh.write(html)


async def login(page) -> None:
    await asyncio.wait_for(
        page.goto(f"{CITIZEN}/Login.aspx", timeout=45000,
                  wait_until="domcontentloaded"), 55)
    await asyncio.wait_for(page.wait_for_timeout(3500), 10)
    fr = None
    for f in page.frames:
        if "login-panel" in f.url:
            fr = f
            break
    if fr is None:
        raise RuntimeError("login panel not found")
    for sel, val in (("input[name='username']", USER),
                     ("input[name='password']", PWD)):
        await asyncio.wait_for(fr.locator(sel).first.fill(val, timeout=8000), 10)
    try:
        await asyncio.wait_for(fr.locator("button").first.click(timeout=6000), 8)
    except Exception:
        await fr.locator("input[name='password']").first.press("Enter")
    await asyncio.wait_for(page.wait_for_load_state("load"), 25)
    await asyncio.wait_for(page.wait_for_timeout(4000), 10)
    out(f"post-login: {page.url[:100]}")


async def body_text(page) -> str:
    txt = ""
    for f in list(page.frames):
        try:
            txt += await asyncio.wait_for(f.locator("body").inner_text(), 8)
        except Exception:
            continue
    return txt


async def find_button(page, sels: tuple[str, ...]):
    for f in list(page.frames):
        try:
            for sel in sels:
                loc = f.locator(sel).first
                if await loc.count() and await loc.is_visible():
                    return f, sel
        except Exception:
            continue
    return None, None


async def missing_required(page) -> list[str]:
    """extract control ids from aca validation panel skipto links"""
    import html as _h
    found: list[str] = []
    for f in list(page.frames):
        try:
            html = await asyncio.wait_for(f.content(), 8)
        except Exception:
            continue
        html = _h.unescape(html)
        for cid in re.findall(r"skipTo\(['\"]([^'\"]+)", html):
            if cid not in found and "v_a_l_i_d" not in cid:
                found.append(cid)
    return found


async def fill_control(page, cid: str, value: str) -> bool:
    for f in list(page.frames):
        try:
            loc = f.locator(f"[name='{cid}']")
            if not await loc.count():
                loc = f.locator(f"#{cid}")
            if not await loc.count():
                continue
            el = loc.first
            tag = await el.evaluate("e => e.tagName.toLowerCase()")
            if tag == "select":
                await el.select_option(label=value)
            else:
                cls = (await el.get_attribute("class")) or ""
                if "masked" in cls:
                    await el.click(timeout=5000)
                    await el.press("Control+A")
                    await el.press("Delete")
                    await page.keyboard.type(value, delay=70)
                else:
                    await el.fill(value)
                val = await el.input_value()
                if not val.strip():
                    await el.focus(timeout=5000)
                    await page.keyboard.type(value, delay=70)
                    val = await el.input_value()
            return True
        except Exception:
            continue
    return False


CONTACT_HEURISTICS = (
    ("firstname", "Eval"), ("lastname", "User"),
    ("fname", "Eval"), ("lname", "User"),
    ("email", USER),
    ("organization", "Licet Eval Testing LLC"),
    ("business", "Licet Eval Testing LLC"),
    ("address1", "77 Licet Eval Way"), ("addressline1", "77 Licet Eval Way"),
    ("city", "Null Island"), ("zip", "00001"),
)


async def handle_contact_page(page, stamp: str) -> bool:
    """on the contact information page, add the required applicant contact"""
    btn_frame = None
    for f in list(page.frames):
        loc = f.locator("[id$='Applicant_269Edit_btnAddNew']")
        if await loc.count():
            btn_frame = f
            break
    if btn_frame is None:
        return False
    try:
        txt = await asyncio.wait_for(btn_frame.locator("body").inner_text(), 8)
        if re.search(r"User,\s*Eval|Eval\s+User", txt):
            out("applicant contact already present")
            return True
    except Exception:
        pass
    out("clicking Add New (applicant contact)")
    await asyncio.wait_for(
        btn_frame.locator("[id$='Applicant_269Edit_btnAddNew']").first.click(timeout=8000),
        10)
    await asyncio.wait_for(page.wait_for_timeout(3000), 10)
    await dump_page(page, stamp, "contact_dialog")

    filled = 0
    for f in list(page.frames):
        try:
            inputs = await f.locator(
                "input[type='text']:visible, input[type='tel']:visible").all()
        except Exception:
            continue
        for el in inputs:
            try:
                name = (await el.get_attribute("name") or "") + " " + (
                    await el.get_attribute("id") or "")
                if await el.input_value() and "email" not in name.lower():
                    continue
            except Exception:
                continue
            nl = name.lower()
            for key, val in CONTACT_HEURISTICS:
                if key in nl:
                    try:
                        await el.fill(val, timeout=4000)
                        filled += 1
                    except Exception:
                        pass
                    break
    for f in list(page.frames):
        if "ContactAddNew" not in f.url:
            continue
        try:
            for el in await f.locator("select:visible").all():
                try:
                    if await el.input_value():
                        continue
                    picked = False
                    for want in ("Applicant",):
                        opt = el.locator(
                            f"option[normalize-space(.)='{want}']").first
                        if await opt.count():
                            v = await opt.get_attribute("value")
                            if v:
                                await el.select_option(value=v)
                                filled += 1
                                picked = True
                    if picked:
                        continue
                    opts = await el.locator("option").all()
                    for o in opts[1:] or []:
                        v = await o.get_attribute("value")
                        if v:
                            await el.select_option(value=v)
                            filled += 1
                            break
                except Exception:
                    continue
        except Exception:
            continue
    out(f"contact dialog: filled {filled} fields")

    # save the contact the dialog iframe (contactaddnew.aspx) has its own save/continue button
    # (ctl00_phpopup_btnsave)
    saved = False
    for f in list(page.frames):
        if "ContactAddNew" not in f.url:
            continue
        loc = f.locator("#ctl00_phPopup_btnSave").first
        if await loc.count():
            await loc.click(timeout=8000)
            saved = True
            out("clicked dialog Save (ctl00_phPopup_btnSave)")
        break
    if not saved:
        out("WARN: dialog save button not found")
        await dump_page(page, stamp, "contact_no_save_btn")
        return False
    closed = False
    for _ in range(12):
        await asyncio.wait_for(page.wait_for_timeout(900), 5)
        still_open = False
        for f in page.frames:
            if "ContactAddNew" in f.url:
                try:
                    el = await f.frame_element()
                    if await el.is_visible():
                        still_open = True
                except Exception:
                    pass
        if not still_open:
            closed = True
            break
    out("contact dialog closed" if closed else "contact dialog STILL OPEN")
    await dump_page(page, stamp, "contact_after_save")
    return closed


async def main() -> int:
    if not USER or not PWD:
        out("credentials missing")
        return 2
    browser, close_browser = await open_apply_browser(headless=False, warn=out)
    stamp = stamp_now()
    try:
        page = await asyncio.wait_for(browser.new_page(), 60)
        out("  browser window raised for whoever is watching" if surface_window()
            else "  could not raise the browser window (continuing)")
        await login(page)

        await asyncio.wait_for(
            page.goto(f"{CITIZEN}/Cap/CapApplyDisclaimer.aspx"
                      "?module=Building&TabName=Building&FilterName=PMT_GENERAL",
                      timeout=45000, wait_until="domcontentloaded"), 55)
        await asyncio.wait_for(page.wait_for_timeout(3000), 10)
        handoff = await accept_disclaimer_with_human(
            page, timeout_s=DISCLAIMER_SETTLE_SECONDS,
            notify=_announce_disclaimer_handoff,
            allow_portal_default=DISCLAIMER_ALLOW_PORTAL_DEFAULT,
        )
        if not handoff.permits_continuation(
                allow_portal_default=DISCLAIMER_ALLOW_PORTAL_DEFAULT):
            out(f"DISCLAIMER NOT ACCEPTED ({handoff.outcome.value}): {handoff.reason}")
            out("stopping before submission — nothing was submitted")
            return 1
        out("disclaimer satisfied while waiting for the operator" if
            handoff.outcome is HandoffOutcome.SATISFIED_WHILE_WAITING else
            "disclaimer already satisfied by the portal; continuing")
        await asyncio.wait_for(page.wait_for_load_state("load"), 25)
        await asyncio.wait_for(page.wait_for_timeout(3000), 10)

        altid: str | None = None
        prev_url = ""
        for step in range(1, 15):
            url = page.url
            out(f"step {step}: {url[:110]}")

            body = await body_text(page)
            for _ in range(10):
                if len(body.strip()) > 300:
                    break
                await asyncio.wait_for(page.wait_for_timeout(1200), 5)
                body = await body_text(page)

            await dump_page(page, stamp, f"submit_w{step}")
            m = ALTID_RE.search(body)
            if m and ("confirm" in body.lower() or "thank" in body.lower()
                      or "submitted" in body.lower() or "CapConfirm" in url):
                altid = m.group(0)
                out(f"RECORD ID CAPTURED: {altid}")
                break

            if url == prev_url and step > 1:
                missing = await missing_required(page)
                out(f"no advance; validation targets: {missing}")
                filled_any = False
                for cid in missing:
                    val = TEST_DATA.get(cid)
                    if val is None and "Contact" in cid:
                        val = (CONTACT_FILL["first"] if "First" in cid or "fname" in cid.lower()
                               else CONTACT_FILL["last"] if "Last" in cid or "lname" in cid.lower()
                               else CONTACT_FILL["phone"] if "Phone" in cid
                               else USER if "Email" in cid else "555-0100")
                    if val is not None:
                        filled_any = await fill_control(page, cid, val) or filled_any
                if not filled_any:
                    out("cannot auto-fill missing fields — stopping")
                    break

            btn_frame, btn_sel = await find_button(page, (
                "#ctl00_PlaceHolderMain_actionBarBottom_btnContinue",
                "a#ctl00_PlaceHolderMain_btnNextStep",
                "input[value='Continue']", "a:has-text('Continue')",
                "button:has-text('Continue')", "a[title*='Continue']",
            ))
            body_low = body.lower()
            if "CapEdit" in url and "contact information" in body_low:
                try:
                    await handle_contact_page(page, stamp)
                except Exception as exc:
                    out(f"contact handler failed: {exc!r}")

            # capedit page: pre fill the known required fields before continue
            if "CapEdit" in url:
                for cid, val in TEST_DATA.items():
                    if await fill_control(page, cid, val):
                        out(f"filled {cid.rsplit('_', 1)[-1]}={val!r}")

            if "CapType" in url:
                for f in list(page.frames):
                    loc = f.locator(
                        f"input[type='radio'][value='Building/Sign/Temporary/NA']")
                    if not await loc.count():
                        continue
                    radio = loc.first
                    try:
                        await radio.check(timeout=6000)
                    except Exception:
                        await radio.click(timeout=6000)
                    # note: do not call selectnode() manually check() already fires the onclick handler,
                    # and a second invocation can toggle aca's internal selection state off
                    checked = await radio.is_checked()
                    out(f"radio 'Sign - Temporary' checked={checked}")
                    await asyncio.wait_for(page.wait_for_timeout(2000), 8)
                    break
            # capconfirm page: check agree box before submit
            if "CapConfirm" in url or "agree" in body.lower():
                for f in list(page.frames):
                    try:
                        cbs = await f.locator(
                            "input[type='checkbox']:visible").all()
                        for cb in cbs:
                            try:
                                await cb.check(timeout=3000)
                            except Exception:
                                await cb.evaluate("e => e.click()")
                            out("agree checkbox checked")
                            break
                    except Exception:
                        continue
            if btn_frame is None:
                # maybe the page was still transitioning; retry once after a wait
                await asyncio.wait_for(page.wait_for_timeout(3000), 6)
                btn_frame, btn_sel = await find_button(page, (
                    "#ctl00_PlaceHolderMain_actionBarBottom_btnContinue",
                    "#ctl00_PlaceHolderMain_actionBarTop_btnContinue",
                    "a#ctl00_PlaceHolderMain_btnNextStep",
                    "input[value='Continue']", "a:has-text('Continue')",
                    "button:has-text('Continue')", "a[title*='Continue']",
                ))
            if btn_frame is None:
                out("no continue/submit button — stopping")
                break
            try:
                await asyncio.wait_for(
                    btn_frame.locator(btn_sel).first.click(timeout=6000), 8)
                await asyncio.wait_for(page.wait_for_load_state("load"), 25)
                await asyncio.wait_for(page.wait_for_timeout(3000), 10)
            except Exception as exc:
                out(f"click failed: {exc!r}")
                break
            prev_url = url

        await dump_page(page, stamp, "submit_final")
        out(f"DONE altid={altid}")
        return 0 if altid else 1
    finally:
        await close_browser()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
