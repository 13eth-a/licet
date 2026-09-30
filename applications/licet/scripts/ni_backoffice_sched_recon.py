"""read only: does the back office offer inspection dates the citizen wizard lacks?"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

AV_URL = "https://nullisland-test-av.accela.com/"
USER, PASSWORD = "developer", "accela"
OUTDIR = Path("logs/ni_backoffice/inventory")

DEFAULT_CAPID = {"ID1": "REC26", "ID2": "00000", "ID3": "000QS"}
DEFAULT_RECORD = "BLD26-00483"

SCHEDULE_HINT = re.compile(r"inspect|schedul|calendar|availab|appointment|time\s*window", re.I)
DATE_RE = re.compile(r"\b(20\d{2}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/20\d{2})\b")


def out(message: str) -> None:
    print(message, flush=True)


def stamp_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


async def find_first_visible(scope, selectors: list[str]):
    for selector in selectors:
        try:
            locator = scope.locator(selector).first
            if await locator.is_visible():
                return locator
        except Exception:
            continue
    return None


async def dump_frames(page, tag: str, stamp: str) -> list[str]:
    saved: list[str] = []
    OUTDIR.mkdir(parents=True, exist_ok=True)
    for index, frame in enumerate(page.frames):
        try:
            html = await asyncio.wait_for(frame.content(), 10)
        except Exception:
            continue
        if len(html) > 1200:
            path = OUTDIR / f"{stamp}_{tag}_f{index}.html"
            path.write_text(html, encoding="utf-8")
            saved.append(str(path))
    try:
        await page.screenshot(path=str(OUTDIR / f"{stamp}_{tag}.png"), full_page=True)
    except Exception:
        pass
    return saved


async def collect_controls(page) -> list[dict]:
    """every interactive control across frames: tag, text, href/onclick, id, name"""
    script = """() => [...document.querySelectorAll('a,button,input,select,[onclick],[role=button]')]
        .map(e => {
            const text = ((e.innerText || e.value || e.title || '') + '').trim().slice(0, 80);
            return {
                tag: e.tagName,
                type: e.getAttribute('type') || '',
                text,
                id: e.id || '',
                name: e.getAttribute('name') || '',
                href: (e.getAttribute('href') || '').slice(0, 200),
                onclick: (e.getAttribute('onclick') || '').slice(0, 200),
                value: ((e.value || '') + '').slice(0, 60),
            };
        })
        .filter(c => c.text || c.href || c.onclick)"""
    controls: list[dict] = []
    for index, scope in enumerate((page, *page.frames)):
        try:
            found = await asyncio.wait_for(scope.evaluate(script), 12)
        except Exception:
            continue
        for item in found or []:
            item["frame"] = index
            controls.append(item)
    return controls


def schedule_candidates(controls: list[dict]) -> list[dict]:
    hits = []
    for control in controls:
        haystack = " ".join(str(control.get(k) or "") for k in ("text", "id", "name", "href", "onclick", "value"))
        if SCHEDULE_HINT.search(haystack):
            hits.append(control)
    return hits


async def goto(page, url: str, *, settle: int = 5000) -> None:
    try:
        await asyncio.wait_for(page.goto(url, timeout=45000, wait_until="domcontentloaded"), 55)
    except Exception as exc:
        out(f"    goto failed ({url[:80]}): {exc!r}")
    await asyncio.wait_for(page.wait_for_timeout(settle), settle / 1000 + 10)


async def av_login(page) -> None:
    await goto(page, AV_URL, settle=5000)
    user = await find_first_visible(page, ['input[name="username"]', 'input[id*="user" i]', 'input[type="text"]'])
    password = await find_first_visible(page, ['input[type="password"]'])
    if user is None or password is None:
        raise RuntimeError("login inputs not found")
    await user.fill(USER)
    await password.fill(PASSWORD)
    submit = await find_first_visible(page, ['input[type="submit"]', "button[type=submit]"])
    if submit is not None:
        await submit.click()
    else:
        await page.keyboard.press("Enter")
    await page.wait_for_timeout(10000)
    out("logged in")


def summary_url(capid: dict, module: str) -> str:
    qs = (f"mode=tabSummary&serviceProviderCode=NULLISLAND"
          f"&ID1={capid['ID1']}&ID2={capid['ID2']}&ID3={capid['ID3']}"
          f"&requireNotice=YES&clearForm=clearForm&module={module}"
          f"&isFromCapList=true&isGeneralCAP=Y")
    return f"{AV_URL}portlets/cap/capsummary/CapTabSummary.do?{qs}"


def inspection_list_url(capid: dict, module: str) -> str:
    qs = (f"serviceProviderCode=NULLISLAND&isCapFrame=Y&module={module}"
          f"&capID1={capid['ID1']}&capID2={capid['ID2']}&capID3={capid['ID3']}")
    return f"{AV_URL}portlets/inspection/inspectionListCapSpecific.do?{qs}"

# the "schedule inspections" menu item calls selectmanageinspection("0", ...) >
# openscheduleinspectionsdialog(), which showmodaldialog()s this url (captured from the record's
# inspection list html)
def schedule_dialog_url(module: str = "Building") -> str:
    return (f"{AV_URL}portlets/inspection/workloadingInspectionList.do"
            f"?value(mode)=doManage&doPending=true&RCAP=true&module={module}&spaceName=null")

def inspection_calendar_url(module: str = "Building") -> str:
    return (f"{AV_URL}portlets/inspection/calendarInspectionList.do"
            f"?calendarInspection=Y&mode=view&module={module}&spaceName=null")

def calendar_weekly_url(module: str = "Building") -> str:
    return f"{AV_URL}portlets/inspection/calendarInspectionWeekly.do?mode=weekly&module={module}"


def calendar_inspections_url(module: str = "Building") -> str:
    return f"{AV_URL}portlets/inspection/calendarInspectionsList.do?mode=inspect&module={module}"


async def try_open_schedule_form(page, errors: list[str]) -> dict:
    """click manage inspection > schedule inspections on the live list page"""
    result: dict = {"menu_clicked": False, "item_clicked": False,
                    "frames_before": len(page.frames), "frames_after": None}
    try:
        await page.locator("#resultMenu1Link").first.click(timeout=8000)
        result["menu_clicked"] = True
    except Exception as exc:
        result["menu_error"] = repr(exc)[:200]
        return result
    await page.wait_for_timeout(1500)
    for scope in (page, *page.frames):
        try:
            item = scope.locator("a.portlet-menu-item", has_text="Schedule Inspections").first
            if await item.count() and await item.is_visible():
                await item.click(timeout=8000)
                result["item_clicked"] = True
                break
        except Exception as exc:
            result.setdefault("item_errors", []).append(repr(exc)[:160])
    await page.wait_for_timeout(6000)
    result["frames_after"] = len(page.frames)
    result["page_errors"] = list(errors)
    return result


async def collect_selects(page) -> list[dict]:
    """every <select> across frames with its options (read only)"""
    script = """() => [...document.querySelectorAll('select')].map(s => ({
        name: s.getAttribute('name') || '', id: s.id || '',
        options: [...s.options].map(o => ((o.text || '') + '').trim()).filter(Boolean).slice(0, 80),
    }))"""
    found: list[dict] = []
    for index, scope in enumerate((page, *page.frames)):
        try:
            for item in await asyncio.wait_for(scope.evaluate(script), 12) or []:
                item["frame"] = index
                found.append(item)
        except Exception:
            continue
    return found


def availability_hints(html_by_frame: dict[int, str]) -> dict:
    """day cell / capacity markers the schedule form or calendar may carry"""
    joined = "\n".join(html_by_frame.values())
    return {
        "date_literals": sorted(set(DATE_RE.findall(joined)))[:20],
        "inactive_day_cells": len(re.findall(r"CalendarDayInactive", joined, re.I)),
        "active_day_cells": len(re.findall(r"CalendarDayActive|CalendarDaySelectable", joined, re.I)),
        "cannot_schedule_titles": len(re.findall(r"Cannot schedule inspection", joined, re.I)),
        "no_availability_markers": len(re.findall(
            r"no (?:available|inspection).{0,20}(?:date|time|schedule)|not available", joined, re.I)),
    }


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--record", default=DEFAULT_RECORD)
    parser.add_argument("--module", default="Building")
    parser.add_argument("--id1", default=DEFAULT_CAPID["ID1"])
    parser.add_argument("--id2", default=DEFAULT_CAPID["ID2"])
    parser.add_argument("--id3", default=DEFAULT_CAPID["ID3"])
    args = parser.parse_args()
    capid = {"ID1": args.id1, "ID2": args.id2, "ID3": args.id3}

    api_key = os.environ.get("SOLARI_API_KEY", "").strip()
    if not api_key:
        out("SOLARI_API_KEY is empty — cannot launch the browser.")
        return 2

    stamp = stamp_now()
    report: dict = {
        "generated": stamp, "host": AV_URL, "mode": "read-only back-office schedule recon",
        "record": args.record, "capid": capid, "pages": [], "errors": [],
    }

    from solari_browser import Solari

    solari = Solari(api_key=api_key)
    out("Launching Solari cloud browser…")
    browser = await solari.launch()
    try:
        page = await browser.new_page()
        await av_login(page)

        page_errors: list[str] = []
        try:
            page.on("pageerror", lambda exc: page_errors.append(str(exc)[:200]))
        except Exception:
            pass

        steps = (
            ("record_summary", summary_url(capid, args.module)),
            ("inspection_list", inspection_list_url(capid, args.module)),
            ("schedule_dialog", schedule_dialog_url(args.module)),
            ("inspection_calendar", inspection_calendar_url(args.module)),
            ("calendar_weekly", calendar_weekly_url(args.module)),
            ("calendar_inspections", calendar_inspections_url(args.module)),
        )

        out("=== schedule menu attempt ===")
        await goto(page, inspection_list_url(capid, args.module), settle=6000)
        menu = await try_open_schedule_form(page, page_errors)
        report["schedule_menu_attempt"] = menu
        out(f"    {menu}")

        all_controls: list[dict] = []
        for tag, url in steps:
            out(f"=== {tag} ===")
            await goto(page, url, settle=6000)
            out(f"    url: {page.url[:130]}  frames={len(page.frames)}")
            saved = await dump_frames(page, tag, stamp)
            html_by_frame: dict[int, str] = {}
            for index, frame in enumerate(page.frames):
                try:
                    html_by_frame[index] = await asyncio.wait_for(frame.content(), 10)
                except Exception:
                    continue
            controls = await collect_controls(page)
            all_controls.extend(controls)
            hits = schedule_candidates(controls)
            selects = await collect_selects(page)
            hints = availability_hints(html_by_frame)
            report["pages"].append({
                "tag": tag, "url": page.url, "requested_url": url,
                "html": saved, "control_count": len(controls),
                "schedule_candidates": hits, "selects": selects, "availability": hints,
            })
            out(f"    controls={len(controls)} schedule_hints={len(hits)} selects={len(selects)}")
            out(f"    availability: {hints}")
            for select in selects[:8]:
                out(f"      <select> {select['name']!r} id={select['id']!r} "
                    f"options[{len(select['options'])}]={select['options'][:10]}")
            for hit in hits[:12]:
                out(f"      [{hit['tag']}] {hit['text'][:50]!r} id={hit['id']!r} "
                    f"href={hit['href'][:60]!r} onclick={hit['onclick'][:60]!r}")
        report["all_schedule_candidates"] = schedule_candidates(all_controls)
        report["completed"] = True
    except BaseException as exc:  # report, never mask
        report["error"] = repr(exc)
        report["errors"].append(repr(exc))
        out(f"EXCEPTION: {exc!r}")
    finally:
        try:
            await solari.close(browser)
        except Exception:
            try:
                await browser.close()
            except Exception:
                pass
        path = OUTDIR / f"{stamp}_backoffice_sched_recon.json"
        path.write_text(json.dumps(report, indent=2, default=str))
        out(f"\nreport: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
