"""batch-apply licet eval record types on the ni citizen portal (final)"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

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
OUTDIR = Path("logs/ni_backoffice/inventory")
USER = os.environ.get("ACCELA_TEST_USERNAME", "").strip()
PWD = os.environ.get("ACCELA_TEST_PASSWORD", "").strip()
CHECKPOINT = OUTDIR / "apply_batch_results.json"

APPS = [
    ("Building/Commercial/Alteration/NA", "comm_alt",
     "Licet eval application - commercial alteration for testing",
     "81", "Commerce Ave", "00001"),
    ("Building/Residential/Addition/NA", "res_add",
     "Licet eval application - residential addition for testing",
     "83", "Commerce Ave", "00001"),
    ("Building/Commercial/Electrical/NA", "comm_elec",
     "Licet eval application - commercial electrical for testing",
     "85", "Commerce Ave", "00001"),
    ("Building/Residential/New/SFR", "new_sfr",
     "Licet eval application - new single family residence for testing",
     "87", "Commerce Ave", "00001"),
    ("Building/Solar/NA/NA", "solar",
     "Licet eval application - solar permit for testing",
     "89", "Commerce Ave", "00001"),
    ("Building/Right of Way/NA/NA", "row_use",
     "Licet eval application - right of way use permit for testing",
     "91", "Commerce Ave", "00001"),
    ("Building/Commercial/Demolition/NA", "comm_demo",
     "Licet eval application - commercial demolition for testing",
     "93", "Commerce Ave", "00001"),
    ("Building/Residential/Mechanical/NA", "res_mech",
     "Licet eval application - residential mechanical for testing",
     "95", "Commerce Ave", "00001"),
    # 2026-09-29: further record types, confirmed present in the live citizen catalog
    # (logs/ni_backoffice/inventory/*_catalog.json) and added to widen the availability search
    ("Building/Residential/Alteration/NA", "res_alt",
     "Licet eval application - residential alteration for testing",
     "97", "Commerce Ave", "00001"),
    ("Building/Residential/Electrical/NA", "res_elec",
     "Licet eval application - residential electrical for testing",
     "99", "Commerce Ave", "00001"),
    ("Building/Residential/New/NA", "res_new",
     "Licet eval application - residential new construction for testing",
     "101", "Commerce Ave", "00001"),
    ("Building/Commercial/New/NA", "comm_new",
     "Licet eval application - commercial new construction for testing",
     "103", "Commerce Ave", "00001"),
    ("Building/Fence/NA/NA", "fence",
     "Licet eval application - fence permit for testing",
     "105", "Commerce Ave", "00001"),
    ("Building/Commercial/Plumbing/NA", "comm_plumb",
     "Licet eval application - commercial plumbing for testing",
     "107", "Commerce Ave", "00001"),
    ("Building/Commercial/Re-Roof/NA", "comm_reroof",
     "Licet eval application - commercial re-roof for testing",
     "109", "Commerce Ave", "00001"),
]

RECORD_NO_RE = re.compile(r"Record Number is\s*([A-Za-z0-9\-]+)")

APP_SPEC_VALS = (
    ("txtjobvalue", "25000"),
    ("jobcost", "18000"),
    ("txt_0_0", "500"),
    ("txt_0_2", "15000"),
    ("txt_0_1", "12"),
    ("txt_0_3", "4"),
    ("txt_0_4", "4"),
    ("txt_0_5", "320"),
    ("txt_0_6", "45"),
)

DATE_TOKENS = ("mm/dd/yyyy", "mm/dd/yy")
START_DAYS, END_DAYS = 7, 37
NUMBER_VALS = (
    (("sqft", "square", "area"), "500"),
    (("cost", "value", "amount", "fee"), "25000"),
)

POPUP_VALS = (
    ("firstname", "Eval"), ("lastname", "User"),
    ("organization", "Licet Eval Testing LLC"),
    ("busname", "Licet Eval Testing LLC"),
    ("email", USER),
    ("licensenum", "EVAL-1001"),
    ("address1", "77 Licet Eval Way"),
    ("addressline1", "77 Licet Eval Way"),
    ("city", "Null Island"),
    ("zip", "00001"),
    ("conteducationname", "Eval Course 101"),
    ("educationname", "Eval Course 101"),
    ("classhours", "8"),  # must beat 'txtclass' (longest-first match)
    ("txtclass", "Beginner"),
    ("completeddate", "01152026"),  # masked date: type digits only
    ("provider", "Licet Eval Testing LLC"),
    ("course", "Eval Course 101"),
    ("hours", "8"),
    ("phone1", ""), ("phone2", ""), ("phone3", ""),
)
SELECT_LABEL_PREFS = ("Applicant", "Individual", "General Contractor",
                      "Contractor", "Business")
POSTBACK_HINTS = ("contacttype", "typeflag", "ddlcontacttype",
                  "licensetype", "provider")


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


def load_checkpoint() -> dict:
    if CHECKPOINT.exists():
        return json.loads(CHECKPOINT.read_text())
    return {}


def save_checkpoint(data: dict) -> None:
    CHECKPOINT.write_text(json.dumps(data, indent=2))


async def dump_page(page, tag: str) -> None:
    stamp = stamp_now()
    try:
        await asyncio.wait_for(
            page.screenshot(path=str(OUTDIR / f"{stamp}_{tag}.png")), 15)
    except Exception:
        pass
    for i, fr in enumerate(list(page.frames)):
        try:
            html = await asyncio.wait_for(fr.content(), 10)
        except Exception:
            continue
        (OUTDIR / f"{stamp}_{tag}_f{i}.html").write_text(html, encoding="utf-8")


async def body_text(page) -> str:
    txt = ""
    for f in list(page.frames):
        try:
            txt += await asyncio.wait_for(f.locator("body").inner_text(), 8)
        except Exception:
            continue
    return txt


async def field_attrs(el) -> dict:
    return await el.evaluate(
        "e => ({id: e.id||'', name: e.name||'', cls: e.className||'',"
        " ph: e.placeholder||'', label: e.getAttribute('aria-label')||'',"
        " fname: e.getAttribute('fieldname')||'',"
        " title: e.getAttribute('title')||'',"
        " req: e.getAttribute('aria-required')||''})")


def is_required(a: dict) -> bool:
    return (a["title"].lower() == "required" or a["req"].lower() == "true"
            or "MaskedEditError" in a["cls"])


def guess_value(a: dict, app_fields: dict[str, str]) -> tuple[str | None, bool]:
    """return (value, masked) for a required control, or (none, false)"""
    for key in (a["id"], a["name"]):
        if key in app_fields:
            return app_fields[key], False
    masked = "masked" in a["cls"].lower()
    low = f"{a['id']} {a['name']} {a['fname']}".lower()
    if any(t in f"{a['ph']} {a['label']}".lower() for t in DATE_TOKENS):
        end = re.search(r"complet|end|expir", low) is not None
        d = datetime.now(timezone.utc) + timedelta(
            days=END_DAYS if end else START_DAYS)
        return (d.strftime("%m%d%Y") if masked else d.strftime("%m/%d/%Y")), \
            masked
    if any(t in f"{a['ph']} {a['label']}".lower() for t in ("date",)):
        d = datetime.now(timezone.utc) + timedelta(days=START_DAYS)
        return (d.strftime("%m%d%Y") if masked else d.strftime("%m/%d/%Y")), \
            masked
    for key, val in APP_SPEC_VALS:
        if key in low:
            return val, masked
    for keys, val in NUMBER_VALS:
        if any(k in low for k in keys):
            return val, masked
    return None, False


async def type_field(page, el, val: str, masked: bool) -> None:
    """maskededit fields ignore fill(); they need real keystrokes"""
    if masked:
        await el.click(timeout=4000)
        await el.press("Control+A")
        await el.press("Delete")
        await page.keyboard.type(val, delay=70)
    else:
        await el.fill(val, timeout=4000)


async def fill_required_empty(page, app_fields: dict[str, str]) -> int:
    """pre-fill empty required text/masked fields on the current page"""
    filled = 0
    for f in list(page.frames):
        try:
            els = await f.locator(
                "input[type='text']:visible, input[type='tel']:visible").all()
        except Exception:
            continue
        for el in els:
            try:
                if not await el.is_editable():
                    continue
                a = await field_attrs(el)
                if not is_required(a) or (await el.input_value()).strip():
                    continue
                val, masked = guess_value(a, app_fields)
                if val is None:
                    continue
                await type_field(page, el, val, masked)
                got = (await el.input_value()).strip()
                if masked and re.sub(r"\D", "", got) != re.sub(r"\D", "", val):
                    out(f"  WARN masked fill suspect: "
                        f"{a['fname'] or a['id'][-32:]}={got!r}")
                    continue
                if not got:
                    continue
                filled += 1
                out(f"  filled required "
                    f"'{a['fname'] or a['id'][-32:]}'={got}")
            except Exception:
                continue
    return filled


async def fill_control_generic(page, cid: str,
                               app_fields: dict[str, str]) -> bool:
    """fill a validation-flagged control we have no hardcoded value for"""
    for f in list(page.frames):
        try:
            loc = f.locator(f"[id='{cid}']")
            if not await loc.count():
                loc = f.locator(f"[name='{cid}']")
            if not await loc.count():
                continue
            el = loc.first
            a = await field_attrs(el)
            val, masked = guess_value(a, app_fields)
            if val is None:
                out(f"  no generic value for {cid[-40:]} "
                    f"({a['fname'] or 'unlabeled'})")
                return False
            await type_field(page, el, val, masked)
            return bool((await el.input_value()).strip())
        except Exception:
            continue
    return False


async def wait_content(page, min_len: int = 300, tries: int = 10) -> str:
    body = await body_text(page)
    for _ in range(tries):
        if len(body.strip()) > min_len:
            break
        await asyncio.wait_for(page.wait_for_timeout(1200), 5)
        body = await body_text(page)
    return body


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


async def find_popup_frame(page):
    """the visible aca popup iframe renders ctl00_phpopup_* controls"""
    for f in list(page.frames):
        try:
            if await f.locator("[id^='ctl00_phPopup_']").count():
                return f
        except Exception:
            continue
    return None


async def popup_open(page) -> bool:
    return await find_popup_frame(page) is not None


async def fill_popup_round(page) -> int:
    """one fill pass over the open popup"""
    dlg = await find_popup_frame(page)
    if dlg is None:
        return -1
    filled = 0
    for el in await dlg.locator("select:visible").all():
        try:
            nid = ((await el.get_attribute("id")) or "").lower()
            if await el.input_value():
                continue
            picked = False
            for label in SELECT_LABEL_PREFS:
                try:
                    await el.select_option(label=label, timeout=3000)
                    picked = True
                    filled += 1
                    break
                except Exception:
                    continue
            if not picked:
                for o in await el.locator("option").all():
                    v = await o.get_attribute("value")
                    if v:
                        try:
                            await el.select_option(value=v, timeout=3000)
                            picked = True
                            filled += 1
                        except Exception:
                            pass
                        break
            if picked and any(h in nid for h in POSTBACK_HINTS):
                await asyncio.wait_for(page.wait_for_timeout(2500), 8)
                dlg = await find_popup_frame(page)
                if dlg is None:
                    return filled
        except Exception:
            continue
    try:
        inputs = await dlg.locator(
            "input[type='text']:visible, input[type='email']:visible").all()
    except Exception:
        return filled
    for el in inputs:
        try:
            nl = (((await el.get_attribute("name")) or "") + " " +
                  ((await el.get_attribute("id")) or "")).lower()
            cur = await el.input_value()
        except Exception:
            continue
        val = None
        masked = False
        # longest key first: 'classhours' must win over 'txtclass', 'conteducationname' over 'educationname'
        for key, v in sorted(POPUP_VALS, key=lambda kv: -len(kv[0])):
            if key not in nl:
                continue
            if not v:
                val = None
                break
            val = v
            cls = (await el.get_attribute("class")) or ""
            masked = "masked" in cls
            break
        if val is None:
            continue
        # skip only if already correct: non-masked with content, or masked whose digits already match the
        # target
        if not masked and cur:
            continue
        if masked and cur and re.sub(r"\D", "", cur) == re.sub(r"\D", "", val):
            continue
        try:
            if masked:
                await el.click(timeout=4000)
                try:
                    await el.evaluate("e => e.select && e.select()")
                except Exception:
                    pass
                await el.press("Control+A")
                await el.press("Delete")
                await page.keyboard.type(val, delay=70)
                got = await el.input_value()
                if re.sub(r"\D", "", got) != re.sub(r"\D", "", val):
                    out(f"  WARN masked fill suspect: {nl[-32:]}={got!r}")
            else:
                await el.fill(val, timeout=4000)
            filled += 1
        except Exception:
            continue
    return filled


async def click_popup_save(page) -> bool:
    for f in list(page.frames):
        try:
            for sel in ("#ctl00_phPopup_btnSaveAndClose",
                        "#ctl00_phPopup_btnSave"):
                loc = f.locator(sel).first
                if await loc.count() and await loc.is_visible():
                    await loc.click(timeout=8000)
                    out(f"  popup save via {sel.rsplit('_', 1)[-1]}")
                    return True
        except Exception:
            continue
    return False


async def run_popup_to_close(page, tag: str, rounds: int = 4) -> bool:
    """fill+save rounds until the popup closes"""
    for r in range(1, rounds + 1):
        if not await popup_open(page):
            out("  popup closed")
            return True
        n = await fill_popup_round(page)
        out(f"  popup round {r}: filled {n}")
        if not await click_popup_save(page):
            await dump_page(page, f"{tag}_no_save_btn")
            return False
        for _ in range(10):
            await asyncio.wait_for(page.wait_for_timeout(900), 5)
            if not await popup_open(page):
                out("  popup closed")
                return True
            for f in list(page.frames):
                try:
                    t = await asyncio.wait_for(f.locator("body").inner_text(), 4)
                    if "duplicate" in t.lower():
                        btn = f.locator("a:has-text('Continue')").first
                        if await btn.count() and await btn.is_visible():
                            await btn.click(timeout=4000)
                            out("  accepted duplicate-contact prompt")
                            break
                except Exception:
                    continue
    await dump_page(page, f"{tag}_stuck")
    return False


async def add_applicant_contact(page, btn_id: str) -> bool:
    """open a section's add new contact dialog and complete it"""
    for f in list(page.frames):
        try:
            if not await f.locator(f"[id='{btn_id}']").count():
                continue
        except Exception:
            continue
        if not await click_control(page, f"[id='{btn_id}']"):
            out(f"  Add New click failed: {btn_id[-50:]}")
            return False
        await asyncio.wait_for(page.wait_for_timeout(3000), 10)
        return await run_popup_to_close(page, "contact_dialog")
    out(f"  button {btn_id[-50:]} not found")
    return False


async def add_licensed_professional(page, btn_id: str) -> bool:
    """complete a licensed professional section via its add new dialog"""
    return await add_applicant_contact(page, btn_id)


async def process_one_required_section(page,
                                      processed: set[str]) -> bool | None:
    """complete the first unseen required list section"""
    for f in list(page.frames):
        try:
            for el in await f.locator(
                    "a[id$='btnAddNew'], a[id$='btnAddNewLP']").all():
                try:
                    if not await el.is_visible():
                        continue
                except Exception:
                    continue
                bid = await el.get_attribute("id")
                if not bid or bid in processed:
                    continue
                processed.add(bid)
                out(f"  required section: #{bid[-58:]}")
                try:
                    return await add_applicant_contact(page, bid)
                except Exception as exc:
                    # do not swallow this: a section that silently fails to save is why the next continue
                    # rejects the page
                    out(f"  section {bid[-40:]} failed: "
                        f"{type(exc).__name__}: {str(exc)[:80]}")
                    return False
        except Exception:
            continue
    return None


async def process_required_lists(page, processed: set[str],
                                 max_sections: int = 5) -> bool:
    """complete every unseen required list section on the current step"""
    handled = False
    for _ in range(max_sections):
        outcome = await process_one_required_section(page, processed)
        if outcome is None:
            break
        handled = handled or outcome
    if not handled:
        out("  no unprocessed required sections")
    return handled


async def clear_dialog_overlay(page) -> None:
    """hide aca's leftover dialog layer; it swallows clicks on the action bar"""
    for f in list(page.frames):
        try:
            await asyncio.wait_for(f.evaluate(
                "() => { const el = document.getElementById('dvACADialogLayer');"
                " if (el) { el.style.display = 'none'; } }"), 6)
        except Exception:
            continue


async def wait_no_popup(page, tries: int = 8) -> bool:
    """give an open aca popup a chance to finish and close"""
    for _ in range(tries):
        if not await popup_open(page):
            return True
        await asyncio.wait_for(page.wait_for_timeout(900), 5)
    return not await popup_open(page)


async def click_control(page, sels: str | tuple[str, ...], *,
                        timeout_ms: int = 8000, attempts: int = 3) -> bool:
    """click a control, re-resolving it — and its frame — on every attempt"""
    selectors = (sels,) if isinstance(sels, str) else sels
    for _ in range(attempts):
        await wait_no_popup(page)
        await clear_dialog_overlay(page)
        target = None
        for f in list(page.frames):
            for sel in selectors:
                try:
                    loc = f.locator(sel).first
                    if await loc.count() and await loc.is_visible():
                        target = loc
                        break
                except Exception:
                    continue
            if target is not None:
                break
        if target is None:
            await asyncio.wait_for(page.wait_for_timeout(1200), 6)
            continue
        try:
            await asyncio.wait_for(target.click(timeout=timeout_ms),
                                   timeout_ms / 1000 + 3)
            return True
        except Exception:
            pass
        try:
            await asyncio.wait_for(target.evaluate("e => e.click()"), 6)
            return True
        except Exception:
            await asyncio.wait_for(page.wait_for_timeout(1200), 6)
    return False


async def click_continue(page) -> bool:
    """click the action bar's continue once any dialog has settled"""
    return await click_control(page, CONTINUE_SELS)


async def parcel_search_select(page) -> bool:
    """fulfil a required parcel section: search, pick row 1, select"""
    for f in list(page.frames):
        loc = f.locator("[id$='ParcelEdit_btnSearch']").first
        if not await loc.count():
            continue
        await loc.click(timeout=6000)
        await asyncio.wait_for(page.wait_for_timeout(3500), 8)
        for _ in range(8):
            rf = None
            for f2 in list(page.frames):
                try:
                    r = f2.locator("[id$='ucParcelList_gv_CB_0']").first
                    if await r.count() and await r.is_visible():
                        rf = f2
                        await r.check(timeout=5000)
                        break
                except Exception:
                    continue
            if rf is not None:
                for sel in ("a[title='Select']", "input[value='Select']"):
                    try:
                        s = rf.locator(sel).first
                        if await s.count() and await s.is_visible():
                            await s.click(timeout=5000)
                            out("  parcel row 1 selected")
                            break
                    except Exception:
                        continue
                for _ in range(12):
                    await asyncio.wait_for(page.wait_for_timeout(900), 5)
                    if not await popup_open(page):
                        break
                await asyncio.wait_for(page.wait_for_timeout(1500), 5)
                return True
            await asyncio.wait_for(page.wait_for_timeout(1500), 5)
        out("  parcel result list never appeared")
        return False
    out("  Parcel Search button not found")
    return False


async def missing_required(page) -> list[str]:
    """control ids listed in aca's validation panel (skipto links)"""
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
                if not (await el.input_value()).strip():
                    await el.focus(timeout=5000)
                    await page.keyboard.type(value, delay=70)
            return True
        except Exception:
            continue
    return False


async def select_first_option(page, cid: str) -> bool:
    suffix = cid.split("_")[-1]
    for f in list(page.frames):
        try:
            for el in await f.locator(f"select[id$='{suffix}']").all():
                try:
                    if not await el.is_visible():
                        continue
                except Exception:
                    continue
                for label in SELECT_LABEL_PREFS:
                    try:
                        await el.select_option(label=label, timeout=3000)
                        return True
                    except Exception:
                        continue
                for o in await el.locator("option").all():
                    v = await o.get_attribute("value")
                    if v:
                        try:
                            await el.select_option(value=v, timeout=3000)
                            return True
                        except Exception:
                            break
        except Exception:
            continue
    return False


async def check_radio(page, cid: str, value: str = "Yes") -> bool:
    """check a radio in the group identified by cid"""
    for f in list(page.frames):
        for sel in (f"input[type='radio'][id^='{cid}_']",
                    f"input[type='radio'][name*='{cid}']"):
            try:
                for el in await f.locator(sel).all():
                    try:
                        v = await el.get_attribute("value")
                        if v and v.lower() != value.lower():
                            continue
                        if not await el.is_visible():
                            continue
                        await el.check(timeout=4000)
                        return await el.is_checked()
                    except Exception:
                        continue
            except Exception:
                continue
    return False


async def find_button(page, sels):
    for f in list(page.frames):
        try:
            for sel in sels:
                loc = f.locator(sel).first
                if await loc.count() and await loc.is_visible():
                    return f, sel
        except Exception:
            continue
    return None, None


CONTINUE_SELS = (
    "#ctl00_PlaceHolderMain_actionBarBottom_btnContinue",
    "#ctl00_PlaceHolderMain_actionBarTop_btnContinue",
    "a#ctl00_PlaceHolderMain_btnNextStep",
    "input[value='Continue']", "a:has-text('Continue')",
    "button:has-text('Continue')", "a[title*='Continue']",
)


async def run_one_application(page, app: dict) -> str | None:
    """full wizard for one type"""
    cap_type, key = app["cap_type"], app["key"]
    processed_buttons: set[str] = set()

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
        out(f"  DISCLAIMER NOT ACCEPTED ({handoff.outcome.value}): {handoff.reason}")
        out("  stopping before submission — nothing was submitted")
        return None
    if handoff.outcome is HandoffOutcome.SATISFIED_WHILE_WAITING:
        out("  disclaimer satisfied while waiting for the operator; continuing")
    else:
        out("  disclaimer was already satisfied by the portal "
            f"({handoff.outcome.value}); continuing under the explicit opt-in")
    await asyncio.wait_for(page.wait_for_load_state("load"), 25)
    await asyncio.wait_for(page.wait_for_timeout(3000), 10)

    altid = None
    prev_url = ""
    stall = 0
    for step in range(1, 18):
        url = page.url
        body = await wait_content(page)
        out(f"  w{step} step2={url[url.find('stepNumber'):][:24] if 'stepNumber' in url else url[-40:]}")
        await dump_page(page, f"batch_{key}_w{step}")

        m = RECORD_NO_RE.search(body)
        if m and "successfully submitted" in body.lower():
            altid = m.group(1)
            out(f"  RECORD ID CAPTURED: {altid}")
            return altid

        # stall detection: same url three consecutive steps -> give up
        stall = stall + 1 if url == prev_url else 0
        if stall >= 3:
            out("  stalled 3 rounds on same page — aborting")
            return None
        prev_url = url

        if "CapEdit" in url and ("Please add one record" in body
                                 or "at least one record" in body):
            await process_required_lists(page, processed_buttons)

        if "CapEdit" in url and any("ParcelEdit_txtParcelNo" in c
                                    for c in await missing_required(page)):
            await parcel_search_select(page)

        if "CapEdit" in url:
            for cid, val in app["fields"].items():
                await fill_control(page, cid, val)

        await fill_required_empty(page, app["fields"])

        if "CapType" in url:
            for f in list(page.frames):
                loc = f.locator(
                    f"input[type='radio'][value='{cap_type}']").first
                if await loc.count():
                    try:
                        await loc.check(timeout=6000)
                    except Exception:
                        await loc.click(timeout=6000)
                    out(f"  radio {cap_type} checked={await loc.is_checked()}")
                    await asyncio.wait_for(page.wait_for_timeout(2000), 8)
                    break

        # validation triage when continue doesn't advance (checked after click below via stall counter +
        # missing_required)
        missing = await missing_required(page)
        if missing and url == page.url:
            out(f"  validation targets: {[c[-45:] for c in missing]}")
            for cid in missing:
                cl = cid.lower()
                handled = False
                if "ddl" in cl:
                    handled = await select_first_option(page, cid)
                elif "rdo" in cl:
                    handled = await check_radio(page, cid, "Yes") or \
                        await check_radio(page, cid, "No")
                else:
                    val = app["fields"].get(cid)
                    if val is None:
                        for k, v in APP_SPEC_VALS:
                            if k in cl:
                                val = v
                                break
                    if val is not None:
                        handled = await fill_control(page, cid, val)
                    else:
                        handled = await fill_control_generic(
                            page, cid, app["fields"])
                if handled:
                    out(f"  filled {cid[-40:]}")
            await asyncio.wait_for(page.wait_for_timeout(1200), 6)

        btn_frame, btn_sel = await find_button(page, CONTINUE_SELS)
        if btn_frame is None:
            await asyncio.wait_for(page.wait_for_timeout(3000), 6)
            btn_frame, btn_sel = await find_button(page, CONTINUE_SELS)
        if btn_frame is None:
            out("  no continue button — aborting")
            return None
        if not await click_continue(page):
            out("  continue click failed — aborting")
            return None
        try:
            await asyncio.wait_for(page.wait_for_load_state("load"), 25)
            await asyncio.wait_for(page.wait_for_timeout(3000), 10)
        except Exception as exc:
            out(f"  post-continue wait failed: {str(exc)[:110]}")
    return altid


async def main() -> int:
    only = set(sys.argv[1:])
    if not USER or not PWD:
        out("credentials missing")
        return 2
    browser, close_browser = await open_apply_browser(headless=False, warn=out)
    results = load_checkpoint()
    try:
        page = await asyncio.wait_for(browser.new_page(), 60)
        out("  browser window raised for whoever is watching" if surface_window()
            else "  could not raise the browser window (continuing)")
        await login(page)
        for app in APPS:
            if only and app[1] not in only:
                continue
            a = {
                "cap_type": app[0], "key": app[1],
                "fields": {
                    "ctl00_PlaceHolderMain_WorkLocationEdit_txtStreetNo": app[3],
                    "ctl00_PlaceHolderMain_WorkLocationEdit_txtStreetName": app[4],
                    "ctl00_PlaceHolderMain_WorkLocationEdit_txtZip": app[5],
                    "ctl00_PlaceHolderMain_DetailInfoEdit_txtDescriptionDetail":
                        app[2],
                },
            }
            key = a["key"]
            if results.get(key, {}).get("altid"):
                out(f"[{key}] already done: {results[key]['altid']}")
                continue
            out(f"[{key}] applying for {a['cap_type']}")
            try:
                altid = await run_one_application(page, a)
            except Exception as exc:
                out(f"  EXCEPTION: {exc!r}")
                altid = None
            results.setdefault(key, {})
            results[key].update({"cap_type": a["cap_type"],
                                 "altid": altid or results[key].get("altid"),
                                 "ts": stamp_now()})
            save_checkpoint(results)
            out(f"[{key}] result: {results[key]['altid']}")
        done = sum(1 for v in results.values() if v.get("altid"))
        out(f"BATCH COMPLETE: {done} application(s) issued")
        return 0
    finally:
        await close_browser()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
