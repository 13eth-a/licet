"""Does ANY offered inspection type have capacity anywhere in the calendar horizon?

This bounded read-only survey never clicks a day, a time, or the commit. Progress
is appended and fsynced after each browser operation so an interrupted process
leaves a useful trace even when the final JSON report cannot be written.

Run:
    .venv/bin/python scripts/ni_calendar_horizon.py --record BLD26-00469
    .venv/bin/python scripts/ni_calendar_horizon.py --record BLD26-00469 \
        --types Rough,Service --next-clicks 10
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from licet.agent.state import AgentState  # noqa: E402
from licet.browser import accela  # noqa: E402
from licet.browser.dispatcher import ToolDispatcher  # noqa: E402
from licet.browser.solari_client import SolariSession  # noqa: E402

OUTDIR = Path("logs/ni_backoffice/schedule")
CALENDAR_NEXT_SELECTOR = "#ctl00_phPopup_calendar_AccelaLinkButton2"


def out(message: str) -> None:
    print(message, flush=True)


def stamp_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def checkpoint(path: Path, event: str, **details) -> None:
    """Append and flush one credential-free progress event immediately."""
    record = {"timestamp": datetime.now(timezone.utc).isoformat(), "event": event, **details}
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


async def act(
    dispatcher: ToolDispatcher,
    state: AgentState,
    call: dict,
    note: str,
    progress_path: Path,
) -> dict:
    checkpoint(progress_path, "browser_action_started", step=note, tool=call.get("name"))
    outcome = await dispatcher.execute(call, state)
    ok = outcome.get("success")
    error = outcome.get("error") or {}
    checkpoint(
        progress_path,
        "browser_action",
        step=note,
        tool=call.get("name"),
        success=bool(ok),
        blocked=bool(outcome.get("blocked")),
        url=outcome.get("url"),
        error_kind=error.get("kind"),
    )
    out(f"  [{note}] success={ok} blocked={outcome.get('blocked')} err={outcome.get('error')}")
    return outcome


async def read_page(dispatcher: ToolDispatcher, state: AgentState, note: str,
                    progress_path: Path) -> dict:
    checkpoint(progress_path, "page_read_started", step=note)
    result = await dispatcher.execute(
        {"name": "read_page", "args": {"include": ["text", "form", "errors"]}}, state
    )
    data = result.get("data") or {}
    checkpoint(progress_path, "page_read_complete", step=note,
               success=bool(result.get("success")), url=result.get("url"),
               has_calendar=bool(data.get("calendar")),
               field_count=len(data.get("fields") or ()))
    return result


async def reach_calendar(
    dispatcher: ToolDispatcher,
    state: AgentState,
    record: str,
    progress_path: Path,
) -> list[str]:
    await act(dispatcher, state, {"name": "navigate", "args": {"url": accela.INSPECTION_ENTRY_URL}}, "entry", progress_path)
    await act(dispatcher, state, {"name": "wait", "args": {"until_absent": "Loading..."}}, "entry settle", progress_path)
    await act(
        dispatcher,
        state,
        {"name": "click", "args": {"target": record, "by": "text", "intent": "navigate"}},
        f"open {record}",
        progress_path,
    )
    await act(dispatcher, state, {"name": "wait", "args": {"until_absent": "Loading..."}}, "detail settle", progress_path)

    page = await read_page(dispatcher, state, "verified record detail", progress_path)
    data = page.get("data") or {}
    header = accela.parse_record_header(str(data.get("text") or ""))
    identity_ok = str(header.get("permit_id") or "").casefold() == record.casefold()
    checkpoint(
        progress_path,
        "permit_verified",
        permit_id=record,
        identity_verified=identity_ok,
        record_key=(data.get("page_identity") or {}).get("record_number"),
    )
    if not identity_ok:
        raise RuntimeError(f"record identity was not verified for {record}")

    await act(
        dispatcher,
        state,
        {
            "name": "click",
            "args": {"target": "Schedule or Request an Inspection", "by": "text", "intent": "navigate"},
        },
        "open wizard",
        progress_path,
    )
    await act(
        dispatcher,
        state,
        {"name": "wait", "args": {"until_present": "Inspection Type"}},
        "wizard settle",
        progress_path,
    )
    checkpoint(progress_path, "wizard_step_1", flow_step="select_type")
    page = await read_page(dispatcher, state, "inspection type catalog", progress_path)
    options = accela.parse_inspection_types((page.get("data") or {}).get("fields") or [])
    offered = [option.name for option in options]
    checkpoint(progress_path, "catalog_loaded", offered_types=offered)
    return offered


async def walk_type(
    dispatcher: ToolDispatcher,
    state: AgentState,
    portal_type: str,
    next_clicks: int,
    progress_path: Path,
) -> dict:
    selected = await act(
        dispatcher,
        state,
        {
            "name": "click",
            "args": {"target": portal_type, "by": "label", "intent": "select_inspection_type"},
        },
        f"select '{portal_type}'",
        progress_path,
    )
    checkpoint(progress_path, "inspection_selected", inspection_type=portal_type,
               success=bool(selected.get("success")))
    if not selected.get("success"):
        return {"type": portal_type, "error": "type not selectable"}
    advanced = await act(
        dispatcher,
        state,
        {"name": "click", "args": {"target": "Continue", "by": "text", "intent": "navigate"}},
        "continue to the calendar (not the commit)",
        progress_path,
    )
    checkpoint(progress_path, "wizard_step_2", flow_step="select_date",
               success=bool(advanced.get("success")))
    await act(
        dispatcher,
        state,
        {"name": "wait", "args": {"until_absent": "Please wait..."}},
        "calendar settle",
        progress_path,
    )

    seen: dict[str, int] = {}
    bookable = False
    for step in range(next_clicks + 1):
        page = await read_page(dispatcher, state, f"calendar window {step}", progress_path)
        data = page.get("data") or {}
        months = data.get("calendar") or []
        for month in months:
            active = len(month.get("active_days") or ())
            seen.setdefault(month["month"], active)
            if active:
                bookable = True
                out(f"  *** {month['month']}: {active} ACTIVE day(s) {month['active_days'][:5]} ***")
        checkpoint(progress_path, "dates_parsed", inspection_type=portal_type,
                   window=step, calendar_loaded=bool(months), months=months)
        if step == 0 and months:
            checkpoint(progress_path, "calendar_loaded", inspection_type=portal_type,
                       months=[month.get("month") for month in months])
        if bookable or step == next_clicks:
            break
        advanced = await act(
            dispatcher,
            state,
            {
                "name": "click",
                "args": {"target": CALENDAR_NEXT_SELECTOR, "by": "selector", "intent": "navigate"},
            },
            "calendar next month",
            progress_path,
        )
        if not advanced.get("success"):
            out("  no further month navigation — horizon ends here")
            break
        await act(
            dispatcher,
            state,
            {"name": "wait", "args": {"until_absent": "Please wait..."}},
            "month settle",
            progress_path,
        )

    return {"type": portal_type, "months": seen, "bookable": bookable}


async def main() -> int:
    argv = sys.argv[1:]

    def value(flag: str, default=None):
        return argv[argv.index(flag) + 1] if flag in argv else default

    record = value("--record", "BLD26-00469")
    types = [item.strip() for item in (value("--types") or "").split(",") if item.strip()]
    next_clicks = int(value("--next-clicks", "8"))

    OUTDIR.mkdir(parents=True, exist_ok=True)
    stamp = stamp_now()
    progress_path = OUTDIR / f"{stamp}_calendar_horizon.progress.jsonl"
    checkpoint(progress_path, "survey_started", record=record, next_clicks=next_clicks,
               requested_types=types)
    state = AgentState(goal="Find capacity anywhere in the calendar horizon (read-only)")
    report: dict = {"generated": stamp, "record": record, "next_clicks": next_clicks, "types": {}}
    session = SolariSession()

    try:
        checkpoint(progress_path, "browser_session_starting")
        client = await session.client()
        checkpoint(progress_path, "browser_session_started")
        dispatcher = ToolDispatcher(client)
        checkpoint(progress_path, "login_started")
        login = await client.login(
            os.environ.get("ACCELA_TEST_USERNAME", "").strip(),
            os.environ.get("ACCELA_TEST_PASSWORD", "").strip(),
        )
        login_data = login.data or {}
        authenticated = bool(login.ok and login_data.get("authenticated"))
        out(f"login ok={login.ok} authenticated={authenticated}")
        checkpoint(progress_path, "login_complete", success=bool(login.ok), authenticated=authenticated)
        if not authenticated:
            raise RuntimeError("sandbox survey login did not authenticate")

        out(f"\n===== {record} =====")
        offered = await reach_calendar(dispatcher, state, record, progress_path)
        out(f"  offered types: {offered}")
        report["offered_types"] = offered

        wanted = types or offered
        for portal_type in wanted:
            if portal_type not in offered:
                out(f"  '{portal_type}' is not offered — skipping")
                continue
            out(f"\n  --- type: {portal_type} ---")
            if report["types"]:
                await reach_calendar(dispatcher, state, record, progress_path)
            entry = await walk_type(dispatcher, state, portal_type, next_clicks, progress_path)
            report["types"][portal_type] = entry
            checkpoint(progress_path, "type_complete", inspection_type=portal_type,
                       months=entry.get("months", {}), bookable=entry.get("bookable"))
            for month, active in sorted(entry.get("months", {}).items(), key=lambda pair: pair[0]):
                out(f"    {month}: active={active}")
            out(f"    => bookable={entry.get('bookable')}")
            if entry.get("bookable"):
                out(f"\n*** CAPACITY FOUND for '{portal_type}' — this record is bookable ***")
                break
        report["any_bookable"] = any(item.get("bookable") for item in report["types"].values())
        if not report["any_bookable"]:
            out(f"\nno capacity in {next_clicks}+1 rendered windows, for any inspected type")
        report["completed"] = True
        checkpoint(progress_path, "survey_complete", any_bookable=report["any_bookable"],
                   inspected_types=list(report["types"]))
    except BaseException as exc:
        checkpoint(progress_path, "survey_interrupted", error_type=type(exc).__name__,
                   error=str(exc)[:500])
        raise
    finally:
        checkpoint(progress_path, "survey_teardown_started")
        try:
            await session.close()
        except Exception:
            pass
        path = OUTDIR / f"{stamp}_calendar_horizon.json"
        path.write_text(json.dumps(report, indent=2, default=str))
        out(f"\nreport: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))