"""is anything actually bookable? — read-only availability sweep"""

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
from licet.eval.records import KNOWN_RECORDS  # noqa: E402

OUTDIR = Path("logs/ni_backoffice/schedule")
ALL_RECORDS = tuple(record.permit_id for record in KNOWN_RECORDS)


def out(message: str) -> None:
    print(message, flush=True)


def stamp_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


class Sweep:
    def __init__(self, client, state: AgentState) -> None:
        self.client = client
        self.state = state
        self.actions: list[dict] = []

    async def act(self, dispatcher, call: dict, note: str) -> dict:
        outcome = await dispatcher.execute(call, self.state)
        self.actions.append(
            {
                "note": note,
                "call": call,
                "success": outcome["success"],
                "blocked": outcome["blocked"],
                "error": outcome.get("error"),
                "resolution": outcome.get("resolution"),
            }
        )
        error = (outcome.get("error") or {}).get("kind")
        out(
            f"  [{call.get('name')}] {note} target={call.get('args', {}).get('target')!r} "
            f"success={outcome['success']} blocked={outcome['blocked']} err={error}"
        )
        return outcome

    async def dump_calendar_html(self, stamp: str, tag: str) -> list[str]:
        saved: list[str] = []
        for index, frame in enumerate(list(getattr(self.client.page, "frames", []) or [])):
            try:
                html = await frame.content()
            except Exception:
                continue
            if accela.CALENDAR_CONTAINER_ID in html:
                path = OUTDIR / f"{stamp}_{tag}_f{index}.html"
                path.write_text(html)
                saved.append(str(path))
        return saved

    async def walk_to_calendar(self, dispatcher, record: str) -> dict:
        """select a type and continue; stop at the calendar"""
        await self.act(
            dispatcher,
            {"name": "navigate", "args": {"url": accela.INSPECTION_ENTRY_URL}},
            "scheduling entry",
        )
        await self.act(
            dispatcher, {"name": "wait", "args": {"until_absent": "Loading..."}}, "entry settle"
        )
        opened = await self.act(
            dispatcher,
            {"name": "click", "args": {"target": record, "by": "text", "intent": "open_record"}},
            f"open {record}",
        )
        if not opened["success"]:
            return {"error": "could not open the record in scheduling context"}
        await self.act(
            dispatcher, {"name": "wait", "args": {"until_absent": "Loading..."}}, "detail settle"
        )
        await self.act(
            dispatcher,
            {
                "name": "click",
                "args": {
                    "target": "Schedule or Request an Inspection",
                    "by": "text",
                    "intent": "navigate",
                },
            },
            "open wizard",
        )
        await self.act(
            dispatcher,
            {"name": "wait", "args": {"until_present": "Inspection Type"}},
            "wizard settle",
        )

        types = await dispatcher.execute(
            {"name": "read_page", "args": {"include": ["text", "form", "errors"]}}, self.state
        )
        options = accela.parse_inspection_types((types.get("data") or {}).get("fields") or [])
        if not options:
            return {"inspection_types": [], "note": "no inspection types offered"}
        choice = next((o for o in options if o.required), options[0])
        selected = await self.act(
            dispatcher,
            {
                "name": "click",
                "args": {"target": choice.name, "by": "label", "intent": "select_inspection_type"},
            },
            f"select '{choice.name}'",
        )
        if not selected["success"]:
            return {"inspection_types": [o.as_dict() for o in options], "note": "type not selectable"}
        await self.act(
            dispatcher,
            {
                "name": "click",
                "args": {"target": "Continue", "by": "text", "intent": "navigate"},
            },
            "continue to the calendar (not the commit)",
        )
        await self.act(
            dispatcher, {"name": "wait", "args": {"until_absent": "Please wait..."}}, "calendar settle"
        )
        return {"inspection_types": [o.as_dict() for o in options], "chosen": choice.as_dict()}


async def main() -> int:
    args = sys.argv[1:]
    records = (
        tuple(args[args.index("--records") + 1].split(",")) if "--records" in args else ALL_RECORDS
    )
    click_day = "--click-day" in args

    stamp = stamp_now()
    OUTDIR.mkdir(parents=True, exist_ok=True)
    state = AgentState(goal="Measure appointment availability (read-only)")
    report: dict = {"generated": stamp, "records": {}}
    session = SolariSession()

    try:
        client = await session.client()
        dispatcher = ToolDispatcher(client)
        sweep = Sweep(client, state)
        login = await client.login(
            os.environ.get("ACCELA_TEST_USERNAME", "").strip(),
            os.environ.get("ACCELA_TEST_PASSWORD", "").strip(),
        )
        out(f"login ok={login.ok} authenticated={login.data.get('authenticated')}")

        for record in records:
            out(f"\n===== {record} =====")
            entry = await sweep.walk_to_calendar(dispatcher, record)
            if entry.get("note"):
                report["records"][record] = entry
                out(f"  {entry['note']}")
                continue

            step = await dispatcher.execute(
                {"name": "read_page", "args": {"include": ["text", "form", "errors", "frames"]}},
                state,
            )
            data = step.get("data") or {}
            months = data.get("calendar") or []
            entry.update(
                {
                    "flow": data.get("flow"),
                    "calendar": months,
                    "calendar_available": data.get("calendar_available"),
                    "selectable_times": data.get("selectable_times"),
                    "text": (data.get("text") or "")[:1500],
                    "validation_errors": data.get("validation_errors"),
                }
            )
            report["records"][record] = entry
            if not months:
                out("  no calendar rendered")
                continue
            for month in months:
                out(
                    f"  {month['month']}: active={len(month['active_days'])} "
                    f"inactive={len(month['inactive_days'])} "
                    f"{'first active=' + str(month['active_days'][:3]) if month['active_days'] else ''}"
                )
            out(
                f"  => bookable={data.get('calendar_available')} "
                f"times={data.get('selectable_times')!r}"
            )

            if click_day and data.get("calendar_available"):
                first_active = next(
                    (m for m in months if m["active_days"]), None
                )
                day = first_active["active_days"][0]
                out(f"  clicking the first active day ({day}) to read the time slots")
                await sweep.act(
                    dispatcher,
                    {
                        "name": "click",
                        "args": {
                            "target": f"td[class*='CalendarDay']:not([class*='Inactive']):text-is('{day}')",
                            "by": "selector",
                            "intent": "schedule_inspection",
                        },
                    },
                    f"pick day {day}",
                )
                after = await dispatcher.execute(
                    {"name": "read_page", "args": {"include": ["text", "form", "errors"]}}, state
                )
                entry["day_click"] = {
                    "day": day,
                    "selectable_times": (after.get("data") or {}).get("selectable_times"),
                    "text": ((after.get("data") or {}).get("text") or "")[:1200],
                }
                out(f"  after day click: {entry['day_click']['selectable_times']!r}")

            report.setdefault("evidence", []).extend(
                await sweep.dump_calendar_html(stamp, record)
            )

            await sweep.act(
                dispatcher,
                {"name": "navigate", "args": {"url": accela.MY_RECORDS_URL}},
                "leave wizard",
            )

        out("\n=== availability per record ===")
        for record, entry in report["records"].items():
            if entry.get("note"):
                out(f"  {record:14s} {entry['note']}")
                continue
            active = sum(len(m["active_days"]) for m in entry.get("calendar", []))
            out(
                f"  {record:14s} bookable={entry.get('calendar_available')} "
                f"active_days={active} type={(entry.get('chosen') or {}).get('name')!r}"
            )
        report["any_bookable"] = any(
            entry.get("calendar_available") for entry in report["records"].values()
        )
        out(f"  any bookable date on this sandbox: {report['any_bookable']}")
        out("nothing was confirmed or scheduled")
    except Exception as exc:  # noqa: BLE001 - report, do not mask
        report["error"] = repr(exc)
        out(f"EXCEPTION: {exc!r}")
    finally:
        try:
            await session.close()
        except Exception:
            pass

    (OUTDIR / f"{stamp}_availability_sweep.json").write_text(json.dumps(report, indent=2))
    out(f"\nreport: {OUTDIR / f'{stamp}_availability_sweep.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
