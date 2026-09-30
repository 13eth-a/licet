"""Live Solari verification pass for the Accela UI map.

Phase 0 checklist: open the portal through Solari, read content, click,
fill, submit, handle dropdowns, test screenshots, and record anything
Solari struggles with.

Run:  .venv/bin/python scripts/solari_verify.py

Each step prints a PASS/FAIL line; nothing is scheduled or submitted —
this is read-only exploration plus the anonymous search postback (which
is an Automatic-risk action per licet/safety/risk_levels.py).
Transcribe results into docs/accela_ui_map.md (Solari compatibility
table) afterwards.
"""

from __future__ import annotations

import asyncio
import os
import re
import sys
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv()

PORTALS = {
    "omaha": {
        "search": "https://aca-prod.accela.com/OMAHA/Cap/CapHome.aspx?TabName=Home&module=Permits",
        "example": "PLB-10-00951",  # example format shown on Omaha's search page
    },
    "nullisland": {
        # Accela's official public sandbox (see developer.accela.com/docs/construct-appSandbox.html)
        "search": "https://aca-test.accela.com/nullisland/Cap/CapHome.aspx?TabName=Home&module=Building",
        "example": "",  # no known record number yet; filled interactively if found
    },
}
# Public probe record from licet/eval/records.py (Meridian, anonymous-readable).
RECORD_URL = (
    "https://aca-prod.accela.com/meridian?Module=Dev-Services&TabName=Dev-Services"
    "&capID1=22CAP&capID2=00000&capID3=006RZ&agencyCode=MERIDIAN&IsToShowInspection="
)
PERMIT_INPUT = "#ctl00_PlaceHolderMain_generalSearchForm_txtGSPermitNumber"
# Address mode swaps the whole form for APO-style controls (live-verified:
# Omaha and Null Island both drop txtGSStreetName when Search-by-Address is
# selected). Match either ID family by suffix.
STREET_SELECTORS = (
    'input[id$="txtAPO_Search_by_Address_StreetName"], '
    'input[id$="txtGSStreetName"]'
)
SEARCH_TYPE = "#ctl00_PlaceHolderMain_ddlSearchType"
SEARCH_BUTTON = "a#btnSearch"
SHOTS_DIR = os.path.join("logs", "solari_verify")

Results = list[tuple[str, bool, str]]


def log_step(results: Results, name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


async def settle(page, seconds: float = 8.0) -> None:
    """Wait for a WebForms postback to finish without relying on networkidle."""
    try:
        await page.wait_for_load_state("load", timeout=seconds * 1000)
    except Exception:
        pass  # slow postback — proceed; step checks validate what rendered


MASK_CSS = ".ACA_MaskDiv, #divGlobalLoadingMask { display: none !important; }"


async def neutralize_loading_mask(page) -> None:
    """ACA's global loading mask (a Silverlight-era overlay iframe) stays in
    the DOM 'hidden' but still intercepts pointer events, breaking clicks
    (live-verified 2026-09-18). Remove it after every postback."""
    try:
        await page.add_style_tag(content=MASK_CSS)
    except Exception:
        pass  # navigation raced the injection — next call retries


async def safe_click(page, selector: str, timeout: int = 8000) -> None:
    """Click the first match, with a short normal attempt then force (bypasses
    the overlay hit-target check and postback races)."""
    loc = page.locator(selector).first
    try:
        await loc.click(timeout=timeout)
    except Exception:
        await loc.click(force=True, timeout=timeout)


async def safe_submit(page, field_selector: str, timeout: int = 10000) -> str:
    """Submit an ACA search the way a human would, with fallbacks.

    ACA's #btnSearch anchor carries a ButtonDisabled class whose enabling
    trigger we haven't identified (live-verified: typing + blur does not
    enable it). Chain: Enter key in the field → JS-un-disable + click →
    force click. Returns which path fired, or 'none'."""
    await page.keyboard.press("Enter")
    await page.wait_for_timeout(1500)
    btn_class = ""
    try:
        btn_class = await page.locator(SEARCH_BUTTON).get_attribute("class") or ""
    except Exception:
        pass
    if "ButtonDisabled" not in btn_class:
        return "enter-key (button enabled itself)"
    try:  # JS-un-disable then click
        await page.evaluate(
            "() => { const b = document.querySelector('a#btnSearch');"
            " if (b) { b.classList.remove('ButtonDisabled'); b.disabled = false; } }"
        )
        await page.click(SEARCH_BUTTON, timeout=4000)
        return "js-undisable + click"
    except Exception:
        try:
            await page.click(SEARCH_BUTTON, force=True, timeout=4000)
            return "force-click"
        except Exception:
            return "none"


async def main() -> int:
    portal_name = sys.argv[1] if len(sys.argv) > 1 else "omaha"
    portal = PORTALS.get(portal_name)
    if portal is None:
        print(f"Unknown portal {portal_name!r}; choose one of: {', '.join(PORTALS)}")
        return 2
    search_url = portal["search"]

    api_key = os.environ.get("SOLARI_API_KEY", "").strip()
    if not api_key:
        print("SOLARI_API_KEY is empty — paste your slr_live_ key into .env first.")
        return 2

    from solari_browser import Solari

    results: Results = []
    os.makedirs(SHOTS_DIR, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    solari = Solari(api_key=api_key)
    print("Launching Solari cloud browser…")
    browser = await solari.launch()
    try:
        page = await browser.new_page()

        # 0. Neutralize ACA's pointer-eating loading mask for this session.
        #    (Re-applied after every navigation below.)
        await neutralize_loading_mask(page)
        page.on("load", lambda _: asyncio.ensure_future(
            neutralize_loading_mask(page)))

        # 1. Open the portal through Solari
        try:
            await page.goto(search_url, wait_until="domcontentloaded", timeout=45000)
            await settle(page)
            log_step(results, "open_portal", True, f"title={await page.title()!r}")
        except Exception as exc:
            log_step(results, "open_portal", False, repr(exc))
            return _summarize(results)

        # 2. Read visible page content
        try:
            body = await page.locator("body").inner_text()
            has_form = await page.locator(PERMIT_INPUT).count() > 0
            log_step(
                results,
                "read_page",
                has_form and len(body) > 500,
                f"{len(body)} chars visible, permit-number input present={has_form}",
            )
        except Exception as exc:
            log_step(results, "read_page", False, repr(exc))

        # 3. Fill a search field — like a human. ACA enables the search button
        #    from key events, not value-set events: page.fill() leaves the
        #    button disabled (live-verified 2026-09-18); page.type() doesn't.
        try:
            probe_number = portal["example"] or "22CAP"  # generic probe if no known number
            await page.click(PERMIT_INPUT)
            await page.type(PERMIT_INPUT, probe_number, delay=60)
            await page.keyboard.press("Tab")  # blur — ACA enables controls on blur too
            await page.wait_for_timeout(800)
            typed = await page.input_value(PERMIT_INPUT)
            btn = page.locator(SEARCH_BUTTON)
            btn_class = await btn.get_attribute("class") or ""
            log_step(results, "fill_field", typed == probe_number,
                     f"value={typed!r}; search button class={btn_class!r}")
        except Exception as exc:
            log_step(results, "fill_field", False, repr(exc))

        # 4. Submit the search (anchor triggers a JS postback). Record which
        #    submission path actually fired — the disabled-button mystery is a
        #    documented finding.
        try:
            before = await page.content()
            path = await safe_submit(page, PERMIT_INPUT)
            await settle(page, 12)
            after = await page.content()
            changed = after != before
            body = await page.locator("body").inner_text()
            # A real executed search must surface the probe record number
            # (Omaha's own documented example) in the results region.
            saw_probe = probe_number in body
            has_rows = bool(re.search(r"(Records? Found|1 - \d+|Record #)", body))
            outcome = (
                f"search executed: probe record visible={saw_probe}, row markers={has_rows}"
                if (saw_probe or has_rows)
                else "page changed but search execution NOT confirmed "
                     "(no probe record, no row markers) — body starts: " + repr(body[:120])
            )
            log_step(results, "submit_search", saw_probe or has_rows, f"path={path}; {outcome}")
        except Exception as exc:
            log_step(results, "submit_search", False, repr(exc))

        # 5. Screenshot / page-state capture
        try:
            shot = os.path.join(SHOTS_DIR, f"{stamp}_search.png")
            await page.screenshot(path=shot, full_page=False)
            log_step(results, "screenshot", os.path.getsize(shot) > 1000, shot)
        except Exception as exc:
            log_step(results, "screenshot", False, repr(exc))

        # 6. Dropdown handling (auto-postback select — done last: it reloads the form)
        try:
            await page.goto(search_url, wait_until="domcontentloaded", timeout=45000)
            await settle(page)
            options = await page.locator(f"{SEARCH_TYPE} option").all_text_contents()
            target = next(
                (o for o in options if re.search(r"address", o, re.I)), None
            )
            if target is None:
                log_step(results, "dropdown_postback", True,
                         f"skipped — no Address option in {options!r}")
            else:
                await page.select_option(SEARCH_TYPE, label=target)
                await settle(page, 12)
                still_there = await page.locator(SEARCH_TYPE).count() > 0
                street_there = await page.locator(STREET_SELECTORS).count() > 0
                log_step(results, "dropdown_postback", still_there,
                         f"selected {target!r}; form reloaded={still_there}, "
                         f"street field present={street_there}")
        except Exception as exc:
            log_step(results, "dropdown_postback", False, repr(exc))

        # 7. Real user flow on the Null Island sandbox: address search →
        #    click a result row → record detail. (The Meridian deep link was
        #    verified separately: Meridian gates anonymous record views behind
        #    an "approved Address/Parcel Verification" — documented finding.)
        try:
            ni = PORTALS["nullisland"]["search"]
            await page.goto(ni, wait_until="domcontentloaded", timeout=45000)
            await settle(page)
            # a) try address mode; fall back to permit-number wildcard if this
            #    agency's search-type dropdown lacks an address option
            opts = await page.locator(f"{SEARCH_TYPE} option").all_text_contents()
            addr = next((o for o in opts if re.search(r"address", o, re.I)), None)
            mode = "permit-number (default)"
            if addr:
                await page.select_option(SEARCH_TYPE, label=addr)
                await settle(page, 12)
                await neutralize_loading_mask(page)
                mode = f"address ({addr!r})"
            # b) widen the default date window — sandbox test data often sits
            #    outside it (the invisible date-filter gotcha)
            start = "#ctl00_PlaceHolderMain_generalSearchForm_txtGSStartDate"
            if await page.locator(start).count():
                await page.fill(start, "01/01/1990")
            # discover the street field at runtime: agency address modes use
            # different ID families (txtGSStreetName / txtAPO_Search_by_Address_*
            # / others), so grep the visible text inputs for a Street-ish ID
            input_ids = await page.eval_on_selector_all(
                '#PlaceHolderMain input[type="text"]',
                "els => els.map(e => e.id).filter(Boolean)"
            )
            street_sel = next(
                (f'#{i}' for i in input_ids if "street" in i.lower()), None
            )
            if street_sel:
                field, term = street_sel, "Main"
            else:  # nothing street-like — wildcard the permit number instead
                field, term = PERMIT_INPUT, "%"
                mode += f" → no street field (saw {input_ids[:6]}…); fell back to permit wildcard"
            await safe_click(page, field)
            await page.locator(field).first.press_sequentially(term, delay=40)
            path = await safe_submit(page, field)
            await settle(page, 15)
            body = await page.locator("body").inner_text()
            result_links = page.locator(
                'a[href*="capID1="], a[onclick*="capID1"], a[title*="Select"]'
            )
            n = await result_links.count()
            if n > 0:
                await result_links.first.click()
                await settle(page, 15)
                body2 = await page.locator("body").inner_text()
                ok = (
                    "Record Information" in body2
                    or "Application Information" in body2
                    or "capID1=" in page.url
                )
                log_step(results, "search_to_record", ok,
                         f"mode={mode}; submit-path={path}; {n} result links; "
                         f"landed on {page.url!r}")
            else:
                log_step(results, "search_to_record", False,
                         f"mode={mode}; submit-path={path}; 0 result links; "
                         f"body starts {body[:200]!r}")
        except Exception as exc:
            log_step(results, "search_to_record", False, repr(exc))

        # 8. Auth/session note — anonymous run; login survival needs the test account.
        cookies = await page.context.cookies()
        aca_cookies = [c["name"] for c in cookies if "accela" in c.get("domain", "")]
        log_step(results, "session_cookies", len(aca_cookies) > 0,
                 f"{len(aca_cookies)} accela cookies set (login-survival test needs ACCELA_TEST_USERNAME)")

        # Final screenshot of whatever state we ended in
        try:
            shot = os.path.join(SHOTS_DIR, f"{stamp}_final.png")
            await page.screenshot(path=shot, full_page=False)
            print(f"final screenshot: {shot}")
        except Exception:
            pass
    finally:
        await browser.close()  # releases the session slot (see solari-cookbook)

    return _summarize(results)


def _summarize(results: Results) -> int:
    failed = [name for name, ok, _ in results if not ok]
    print("\n=== SUMMARY ===")
    for name, ok, detail in results:
        print(f"  {'PASS' if ok else 'FAIL':4} {name:20} {detail}")
    print(f"\n{len(results) - len(failed)}/{len(results)} steps passed")
    print("Next: transcribe into docs/accela_ui_map.md (Solari compatibility table).")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
