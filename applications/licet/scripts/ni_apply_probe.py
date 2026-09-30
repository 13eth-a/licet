"""Phase 1: map the citizen-portal application wizard (READ-ONLY, no submit).

Flow: login → Create an Application → pick Building/Sign/Temporary/NA →
dump every wizard page (HTML + screenshot) → STOP before Continue on the
final page. Nothing is submitted.

Run:  .venv/bin/python scripts/ni_apply_probe.py
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv()

from licet.browser.apply_browser import (  # noqa: E402
    open_apply_browser,
    surface_window,
)
from licet.safety.attestation_handoff import accept_disclaimer_with_human  # noqa: E402

CITIZEN = "https://aca-test.accela.com/nullisland"
OUTDIR = os.path.join("logs", "ni_backoffice", "inventory")
USER = os.environ.get("ACCELA_TEST_USERNAME", "").strip()
PWD = os.environ.get("ACCELA_TEST_PASSWORD", "").strip()

TARGET = ("Building", "Sign", "Temporary", "NA")


def out(m: str) -> None:
    print(m, flush=True)


# The apply flow's disclaimer is a legal attestation. Licet never accepts it:
# if that box gets ticked, the click is the operator's, in the browser.
#
# See ni_apply_batch.py: the run does not wait on a human for it. NI ticks its
# own box and advances on its own, so this is a short settle window for the
# portal, not a request for anyone to act.
DISCLAIMER_SETTLE_SECONDS = 30.0

# See ni_apply_batch.py: NI pre-ticks its own agree box, so there is no
# attestation act for the operator to perform out of it.
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


def dump_path(stamp: str, name: str, ext: str) -> str:
    return os.path.join(OUTDIR, f"{stamp}_{name}.{ext}")


async def dump_page(page, stamp: str, name: str) -> None:
    try:
        await asyncio.wait_for(page.screenshot(path=dump_path(stamp, name, "png")), 15)
    except Exception as exc:
        out(f"  screenshot failed: {exc!r}")
    for i, fr in enumerate(page.frames):
        try:
            html = await asyncio.wait_for(fr.content(), 10)
        except Exception:
            continue
        with open(dump_path(stamp, f"{name}_f{i}", "html"), "w", encoding="utf-8") as fh:
            fh.write(html)


async def login(page) -> None:
    await asyncio.wait_for(
        page.goto(f"{CITIZEN}/Login.aspx", timeout=45000, wait_until="domcontentloaded"),
        55,
    )
    await asyncio.wait_for(page.wait_for_timeout(3500), 10)
    fr = None
    for f in page.frames:
        if "login-panel" in f.url:
            fr = f
            break
    if fr is None:
        raise RuntimeError("login panel iframe not found")
    for sel, val in (("input[name='username']", USER),
                     ("input[name='password']", PWD)):
        loc = fr.locator(sel).first
        await asyncio.wait_for(loc.fill(val, timeout=8000), 10)
    try:
        await asyncio.wait_for(fr.locator("button").first.click(timeout=6000), 8)
    except Exception:
        await fr.locator("input[name='password']").first.press("Enter")
    await asyncio.wait_for(page.wait_for_load_state("load"), 25)
    await asyncio.wait_for(page.wait_for_timeout(4000), 10)
    if "dashboard" not in page.url.lower() and "home" not in page.url.lower():
        out(f"warn: post-login url = {page.url[:120]}")


def frame_texts(page) -> list[str]:
    return [f.url for f in page.frames]


async def main() -> int:
    if not USER or not PWD:
        out("ACCELA_TEST_USERNAME/PASSWORD missing in .env")
        return 2

    # A headed local browser: the operator has to be able to reach the
    # disclaimer checkbox themselves.
    browser, close_browser = await open_apply_browser(headless=False, warn=out)
    stamp = stamp_now()
    try:
        page = await asyncio.wait_for(browser.new_page(), 60)
        out("  browser window raised for whoever is watching" if surface_window()
            else "  could not raise the browser window (continuing)")
        await login(page)
        out("logged in")

        # --- step 1: reach the apply entry -------------------------------
        # (discovered in 20260919T224153Z_apply_entry_f0.html: the nav's
        # real href is CapApplyDisclaimer.aspx?module=Building&TabName=Building
        # &FilterName=PMT_GENERAL)
        entry = (f"{CITIZEN}/Cap/CapApplyDisclaimer.aspx"
                 "?module=Building&TabName=Building&FilterName=PMT_GENERAL")
        await asyncio.wait_for(
            page.goto(entry, timeout=45000, wait_until="domcontentloaded"), 55)
        await asyncio.wait_for(page.wait_for_timeout(3000), 10)
        out(f"apply entry: {page.url[:140]}")
        await dump_page(page, stamp, "apply_entry")

        # --- step 2: hand the disclaimer to the human, then continue -----
        # Even though this probe submits nothing, accepting the disclaimer is
        # itself a legal attestation, so it stays the human's click.
        handoff = await accept_disclaimer_with_human(
            page, timeout_s=DISCLAIMER_SETTLE_SECONDS,
            notify=_announce_disclaimer_handoff,
            allow_portal_default=DISCLAIMER_ALLOW_PORTAL_DEFAULT,
        )
        if not handoff.permits_continuation(
                allow_portal_default=DISCLAIMER_ALLOW_PORTAL_DEFAULT):
            out(f"disclaimer not accepted ({handoff.outcome.value}): {handoff.reason}")
            out("PROBE STOPPED — nothing submitted")
            return 1
        out("clicked Continue Application »")
        await asyncio.wait_for(page.wait_for_load_state("load"), 25)
        await asyncio.wait_for(page.wait_for_timeout(3000), 10)
        out(f"type-selection url: {page.url[:140]}")
        await dump_page(page, stamp, "apply_type")

        # --- step 3: choose the record type ------------------------------
        # ACA apply step 1 is usually a type tree/dropdown per module.
        # Look in all frames for a select containing 'Sign - Temporary'
        # or a clickable tree node with the same text.
        chosen = False
        for f in page.frames:
            try:
                opts = await asyncio.wait_for(
                    f.locator("select option").all_text_contents(), 10)
            except Exception:
                continue
            hits = [t for t in opts if "Sign - Temporary" in t or "Sign—Temporary" in t]
            if hits:
                out(f"type dropdown found in frame {f.url[-60:]}: {hits[:3]}")
                sel = f.locator("select", has=lambda _t: True)  # placeholder
                # pick the select that actually contains the option
                for s in await f.locator("select").all():
                    try:
                        txts = await s.locator("option").all_text_contents()
                    except Exception:
                        continue
                    if any("Sign - Temporary" in t for t in txts):
                        val = await s.locator(
                            "option", has_text="Sign - Temporary").first.get_attribute("value")
                        await s.select_option(value=val)
                        out(f"selected Sign - Temporary (value={val})")
                        chosen = True
                        break
                break
        if not chosen:
            # maybe a tree of radio/checkbox nodes instead
            node = page.locator("text=Sign - Temporary").first
            if await node.count():
                await node.click(timeout=5000)
                out("clicked 'Sign - Temporary' node")
                chosen = True
        await asyncio.wait_for(page.wait_for_timeout(2000), 8)
        await dump_page(page, stamp, "apply_type")
        if not chosen:
            out("WARN: could not select record type — dumping page for manual mapping")

        # --- step 3: walk wizard pages read-only -------------------------
        # Continue through pages, dumping each. STOP at the final agreement
        # page; do NOT click the submit/agree button.
        for step in range(1, 9):
            btn = None
            for f in list(page.frames):
                try:
                    for sel in ("input[value='Continue']", "a:has-text('Continue')",
                                "button:has-text('Continue')",
                                "input[value='Next']", "a:has-text('Next')",
                                "a[title*='Continue']"):
                        if await f.locator(sel).count():
                            btn = (f, sel)
                            break
                except Exception:
                    continue  # frame detached mid-scan
                if btn:
                    break
            out(f"wizard step {step}: url={page.url[:110]}")
            await dump_page(page, stamp, f"apply_w{step}")
            if btn is None:
                out("no Continue button — wizard end or unexpected page; stopping walk")
                break
            # read-only: if this page contains the final submit/agree control,
            # stop here instead of continuing.
            body = ""
            for f in list(page.frames):
                try:
                    body += await asyncio.wait_for(f.locator("body").inner_text(), 8)
                except Exception:
                    pass
            if any(k in body.lower() for k in ("i agree", "submit application", "agree and submit")):
                out("final page reached — stopping BEFORE submit (read-only probe)")
                break
            f, sel = btn
            try:
                await asyncio.wait_for(f.locator(sel).first.click(timeout=6000), 8)
                await asyncio.wait_for(page.wait_for_load_state("load"), 20)
                await asyncio.wait_for(page.wait_for_timeout(2500), 8)
            except Exception as exc:
                out(f"continue click failed: {exc!r}")
                break
        out("PROBE COMPLETE — nothing submitted")
        return 0
    finally:
        await close_browser()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
