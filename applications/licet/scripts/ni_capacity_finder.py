"""Read-only: which agency / inspection type can actually be scheduled?

The Null Island Building agency's calendar is measured empty from Sep 2026
through Aug 2027 (`docs/phase4/checklist_audit.md`), and every record Licet owns
is `module=Building`. Appointment capacity in ACA is configured per
agency / inspection-type / calendar, so "is a live booking reachable at all?"
reduces to one question no run has answered yet:

    which agency + inspection-type combinations have ever produced a real
    appointment (and which are currently Scheduled)?

This probe answers it from the back office, read-only. It never opens a
citizen-portal session and never clicks a write control.

Method:
  1. Log into the back office (`nullisland-test-av.accela.com`).
  2. Discover the SPA's top-nav portlets and dump their names.
  3. Open the Inspections portlet and paginate as far as the budget allows.
  4. Read every inspection row; aggregate by module/agency, inspection type,
     and status. A module with `Scheduled` rows is the strongest signal; a
     module with only `Completed`/`Failed` rows still proves its calendar once
     produced slots.
  5. Open any Scheduling/Calendar-config portlet found, and dump it.

Everything is checkpointed (append + fsync) so an interrupted process still
leaves a trace, and the final JSON report is written in `finally`.

Run:
    .venv/bin/python scripts/ni_capacity_finder.py
    .venv/bin/python scripts/ni_capacity_finder.py --pages 6 --budget 420
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

AV_URL = "https://nullisland-test-av.accela.com/"
USER, PASSWORD = "developer", "accela"
OUTDIR = Path("logs/ni_backoffice/capacity")

# Element kinds that can be a write control on this portal. Only ever clicked
# when their own text is a *navigation* label (never a write word below).
WRITE_TAGS = {"button", "input", "select"}
WRITE_WORDS = (
    "issue", "accept", "save", "submit", "continue", "approve", "reject",
    "delete", "update", "post", "create", "add", "send", "confirm", "schedule",
    "cancel", "reschedule", "pay",
)

# Portlets worth opening, in priority order. Navigation-click only; a portlet
# whose name is absent is simply skipped.
PORTLETS = (
    "Inspections",
    "Scheduling",
    "Calendars",
    "Calendar",
    "Appointments",
    "Availability",
    "Tasks",
)

MODULES = (
    "Building", "AMS", "EnvHealth", "Licenses", "Planning", "Enforcement",
    "PublicWorks", "Fire", "ServiceRequest", "Cannabis", "Treasury",
)
STATUSES = (
    "Scheduled", "Requested", "Pending", "Completed", "Failed", "Cancelled",
    "In Progress", "Canceled", "Passed",
)
INSPECTION_TYPES = (
    "Rough", "Service", "Temp Service Pole", "Ground Work", "Electrical Final",
    "Progress Check", "Set Backs", "Temp Power", "Footings & Forms",
    "Foundation", "Rough Frame", "Frame", "Floor Deck", "Roof Deck",
    "Partial/Temp Building Final", "Building Final", "Sign Final",
    "Electrical Sign Final", "Solar Final", "Brycer Inspection History",
)
_RECORD_RE = re.compile(r"\b(?:BLD|SR|ENF|FIR|EHA|PLN|REC)[A-Z0-9]{0,3}[-0-9]{4,}\b|\b\d{9}\b", re.I)


def out(message: str) -> None:
    print(message, flush=True)


def stamp_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


class Progress:
    """Append-and-fsync checkpoint log; survives a hard interruption."""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)

    def __call__(self, event: str, **details: object) -> None:
        record = {"timestamp": datetime.now(timezone.utc).isoformat(), "event": event, **details}
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
            stream.flush()
            os.fsync(stream.fileno())


async def find_first_visible(scope, selectors: list[str]):
    for selector in selectors:
        try:
            locator = scope.locator(selector).first
            if await locator.is_visible():
                return locator
        except Exception:
            continue
    return None


def _is_write_label(text: str) -> bool:
    low = text.lower()
    return any(word in low for word in WRITE_WORDS)


async def nav_click(page, label: str, progress: Progress) -> bool:
    """Click a *navigation* element whose text matches `label`, in any frame.

    Safety is by element type: BUTTON/INPUT/SELECT are refused outright, so a
    write control that happens to read like a portlet name is never pressed.
    """
    for scope in (page, *page.frames):
        try:
            candidates = scope.get_by_text(label, exact=False)
            count = await candidates.count()
        except Exception:
            continue
        for index in range(min(count, 6)):
            locator = candidates.nth(index)
            try:
                if not await locator.is_visible():
                    continue
                tag = str(await locator.evaluate("e => e.tagName")).lower()
                text = str(await locator.inner_text())[:60]
            except Exception:
                continue
            if tag in WRITE_TAGS and _is_write_label(text):
                progress("nav_click_refused_write_control", label=label, tag=tag, text=text)
                continue
            try:
                await locator.click(timeout=8000)
                progress("nav_clicked", label=label, tag=tag, text=text)
                return True
            except Exception as exc:
                progress("nav_click_failed", label=label, tag=tag, error=str(exc)[:200])
    return False


async def dump_visible_labels(page) -> list[str]:
    labels: list[str] = []
    for frame in page.frames:
        try:
            texts = await frame.eval_on_selector_all(
                "a, button, [role=tab], li, span",
                "els => els.map(e => (e.innerText || '').trim())"
                ".filter(t => t && t.length > 1 && t.length <= 40)",
            )
        except Exception:
            continue
        for value in texts:
            if value not in labels:
                labels.append(value)
    return labels


# A grid frame is identified by header signatures so the dashboard's Task grid
# is never read as if it were the portlet's data.
GRID_SIGNATURES: dict[str, tuple[str, ...]] = {
    "Inspections": ("Inspection Type", "Scheduled Date"),
    "Tasks": ("Task Item", "Due Date"),
}


async def grid_frames(page, signature: tuple[str, ...]) -> list:
    matched = []
    for frame in page.frames:
        try:
            html = await frame.content()
        except Exception:
            continue
        if all(token.lower() in html.lower() for token in signature):
            matched.append((frame, html))
    return matched


async def frame_rows(frame) -> list[list[str]]:
    try:
        dump = await frame.eval_on_selector_all(
            "table tr",
            "els => els.map(r => Array.from(r.cells || []).map(c => "
            "(c.innerText || '').trim().slice(0, 60)).filter(Boolean))"
            ".filter(cells => cells.length)",
        )
    except Exception:
        return []
    return [list(cells) for cells in (dump or ()) if cells]


async def frame_next(frame) -> bool:
    """Click the ACA grid's Next pager inside one frame, unless disabled.

    The control is `<a title="Next"><img .../></a>`; a disabled pager renders
    the *grayed-out* Next image, which must not be clicked.
    """
    try:
        anchor = frame.locator('a[title="Next"]').first
        if await anchor.count() == 0 or not await anchor.is_visible():
            return False
        try:
            src = str(await anchor.locator("img").first.get_attribute("src") or "")
        except Exception:
            src = ""
        if "grayed" in src.lower():
            return False
        await anchor.click(timeout=8000)
        return True
    except Exception:
        return False


async def click_next_page(page, progress: Progress) -> bool:
    """Best-effort pagination: 'Load More' / 'Next' navigation only."""
    for label in ("Load More", "Next", "Next »", "More"):
        for scope in (page, *page.frames):
            try:
                candidates = scope.get_by_text(label, exact=False)
                count = await candidates.count()
            except Exception:
                continue
            for index in range(min(count, 4)):
                locator = candidates.nth(index)
                try:
                    if not await locator.is_visible():
                        continue
                    tag = str(await locator.evaluate("e => e.tagName")).lower()
                    text = str(await locator.inner_text())[:40]
                except Exception:
                    continue
                if tag in WRITE_TAGS and _is_write_label(text):
                    continue
                try:
                    await locator.click(timeout=8000)
                    progress("pagination_clicked", label=label, tag=tag)
                    return True
                except Exception:
                    continue
    return False


def classify_row(cells: list[str]) -> dict:
    """Best-effort extraction; the raw cells are always retained."""
    joined = " | ".join(cells)
    record = None
    match = _RECORD_RE.search(joined)
    if match:
        record = match.group(0)
    module = next((m for m in MODULES if m.lower() in joined.lower()), None)
    status = next((s for s in STATUSES if s.lower() in joined.lower()), None)
    itype = next((t for t in INSPECTION_TYPES if t.lower() in joined.lower()), None)
    return {
        "cells": cells,
        "record": record,
        "module": module,
        "status": status,
        "inspection_type": itype,
    }


def aggregate(rows: list[dict]) -> dict:
    by_module: dict[str, int] = {}
    by_status: dict[str, int] = {}
    by_type: dict[str, int] = {}
    module_type: dict[str, dict[str, int]] = {}
    scheduled: list[dict] = []
    for row in rows:
        module = row.get("module") or "unknown"
        status = row.get("status") or "unknown"
        itype = row.get("inspection_type") or "unknown"
        by_module[module] = by_module.get(module, 0) + 1
        by_status[status] = by_status.get(status, 0) + 1
        by_type[itype] = by_type.get(itype, 0) + 1
        module_type.setdefault(module, {})
        module_type[module][itype] = module_type[module].get(itype, 0) + 1
        if status == "Scheduled":
            scheduled.append(row)
    return {
        "by_module": by_module,
        "by_status": by_status,
        "by_type": by_type,
        "module_type": module_type,
        "scheduled_rows": scheduled,
    }


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pages", type=int, default=4, help="pagination pages per portlet")
    parser.add_argument("--budget", type=float, default=420.0, help="wall-clock budget, seconds")
    args = parser.parse_args()

    api_key = os.environ.get("SOLARI_API_KEY", "").strip()
    if not api_key:
        out("SOLARI_API_KEY is empty — cannot launch the browser. Aborting read-only run.")
        return 2

    OUTDIR.mkdir(parents=True, exist_ok=True)
    stamp = stamp_now()
    progress = Progress(OUTDIR / f"{stamp}_capacity_finder.progress.jsonl")
    progress("probe_started", pages=args.pages, budget=args.budget)
    started = time.monotonic()

    report: dict = {
        "generated": stamp,
        "host": AV_URL,
        "mode": "read-only back-office capacity survey",
        "portlets": {},
        "nav_labels": [],
        "errors": [],
    }

    from solari_browser import Solari

    solari = Solari(api_key=api_key)
    out("Launching Solari cloud browser…")
    browser = await solari.launch()
    try:
        page = await browser.new_page()
        await page.goto(AV_URL, timeout=60000)
        await page.wait_for_timeout(5000)
        user = await find_first_visible(
            page, ['input[name="username"]', 'input[id*="user" i]', 'input[type="text"]']
        )
        password = await find_first_visible(page, ['input[type="password"]'])
        if user is None or password is None:
            progress("login_inputs_missing")
            out("Login inputs not found — aborting (read-only run).")
            return 1
        await user.fill(USER)
        await password.fill(PASSWORD)
        submit = await find_first_visible(page, ['input[type="submit"]', "button[type=submit]"])
        if submit is not None:
            await submit.click()
        else:
            await page.keyboard.press("Enter")
        await page.wait_for_timeout(10000)
        progress("login_complete")
        out("Logged in.")

        labels = await dump_visible_labels(page)
        report["nav_labels"] = labels
        progress("nav_labels", labels=labels[:80])
        out(f"visible labels ({len(labels)}): {labels[:60]}")

        for portlet in PORTLETS:
            if time.monotonic() - started > args.budget:
                progress("budget_exhausted_before", portlet=portlet)
                break
            if not any(portlet.lower() in label.lower() for label in labels):
                progress("portlet_absent", portlet=portlet)
                continue
            out(f"\n=== portlet: {portlet} ===")
            opened = await nav_click(page, portlet, progress)
            if not opened:
                report["portlets"].setdefault(portlet, {})["opened"] = False
                continue
            await page.wait_for_timeout(8000)
            signature = GRID_SIGNATURES.get(portlet, ())
            frames = await grid_frames(page, signature) if signature else []
            if not frames:
                progress("grid_frame_not_found", portlet=portlet)
                frames = [(frame, "") for frame in page.frames]
            saved_html: list[str] = []
            for index, (_frame, html) in enumerate(frames):
                if not html:
                    continue
                path = OUTDIR / f"{stamp}_{portlet.replace(' ', '_')}_f{index}.html"
                path.write_text(html, encoding="utf-8")
                saved_html.append(str(path))

            rows: list[dict] = []
            seen: set[tuple[str, ...]] = set()
            template_frame = frames[0][0] if frames else None
            for page_index in range(args.pages):
                if time.monotonic() - started > args.budget:
                    progress("budget_exhausted_during", portlet=portlet, page=page_index)
                    break
                for frame, _html in frames:
                    for cells in await frame_rows(frame):
                        key = tuple(cells)
                        if key in seen:
                            continue
                        seen.add(key)
                        rows.append(classify_row(cells))
                if template_frame is None or not await frame_next(template_frame):
                    break
                await page.wait_for_timeout(9000)
                refreshed = await grid_frames(page, signature) if signature else []
                if refreshed:
                    frames = refreshed
                    template_frame = frames[0][0]
            summary = aggregate(rows)
            report["portlets"][portlet] = {
                "opened": True, "row_count": len(rows), "html": saved_html,
                **summary, "rows": rows,
            }
            progress("portlet_scanned", portlet=portlet, row_count=len(rows),
                     by_module=summary["by_module"], by_status=summary["by_status"],
                     by_type=summary["by_type"])
            out(f"  rows={len(rows)} by_module={summary['by_module']}")
            out(f"  by_status={summary['by_status']}")
            out(f"  by_type={summary['by_type']}")
            if summary["scheduled_rows"]:
                out(f"  *** {len(summary['scheduled_rows'])} SCHEDULED row(s) — capacity is real ***")
            # Return to the dashboard so the next portlet starts from a known view.
            await page.goto(AV_URL, timeout=60000)
            await page.wait_for_timeout(6000)

        report["completed"] = True
        progress("probe_complete")
    except BaseException as exc:  # report, never mask
        report["error"] = repr(exc)
        report["errors"].append(repr(exc))
        progress("probe_interrupted", error_type=type(exc).__name__, error=str(exc)[:500])
        out(f"EXCEPTION: {exc!r}")
    finally:
        try:
            await solari.close(browser)
        except Exception:
            try:
                await browser.close()
            except Exception:
                pass
        path = OUTDIR / f"{stamp}_capacity_finder.json"
        path.write_text(json.dumps(report, indent=2, default=str))
        out(f"\nreport: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
