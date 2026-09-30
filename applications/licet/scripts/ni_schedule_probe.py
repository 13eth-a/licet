"""map the citizen scheduling wizard — read-only, nothing is scheduled"""

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
DEFAULT_DEEP = "BLD26-00469"


def out(message: str) -> None:
    print(message, flush=True)


def stamp_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _excerpt(data: dict, limit: int = 1200) -> str:
    text = data.get("text") or ""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return " | ".join(lines)[:limit]


class Probe:
    """reads, plus a transcript of every dispatcher call so failures are diagnosable from the report instead of only from stdout"""

    def __init__(self) -> None:
        self.steps: list[dict] = []
        self.actions: list[dict] = []

    async def act(
        self, dispatcher: ToolDispatcher, state: AgentState, call: dict, note: str = ""
    ) -> dict:
        outcome = await dispatcher.execute(call, state)
        entry = {
            "call": call,
            "note": note,
            "success": outcome["success"],
            "blocked": outcome["blocked"],
            "error": outcome.get("error"),
            "resolution": outcome.get("resolution"),
            "authorization": outcome.get("authorization"),
            "url": outcome.get("url"),
        }
        self.actions.append(entry)
        if call.get("name") != "read_page":
            error = (outcome.get("error") or {}).get("kind")
            out(
                f"  [{call.get('name')}] {note} target={call.get('args', {}).get('target')!r} "
                f"success={outcome['success']} blocked={outcome['blocked']} err={error}"
            )
            if not outcome["success"]:
                out(f"      resolution={outcome.get('resolution')}")
                out(f"      error={outcome.get('error')}")
        return outcome

    def record(self, name: str, outcome: dict) -> dict:
        data = outcome.get("data") or {}
        entry = {
            "step": name,
            "url": outcome.get("url"),
            "flow": data.get("flow"),
            "loading": data.get("loading"),
            "popup_open": data.get("popup_open"),
            "notices": data.get("notices"),
            "validation_errors": data.get("validation_errors"),
            "inspection_types": data.get("inspection_types"),
            "inspection_type_total": data.get("inspection_type_total"),
            "controls": [
                field
                for field in (data.get("fields") or [])
                if field.get("kind") in ("select", "radio", "checkbox") or field.get("required")
            ],
            "text": (data.get("text") or "")[:2500],
        }
        self.steps.append(entry)
        out(f"\n--- {name} ---")
        out(f"  url    : {(entry['url'] or '')[:120]}")
        out(f"  flow   : {entry['flow']}  loading={entry['loading']} popup={entry['popup_open']}")
        if entry["notices"]:
            out(f"  notice : {entry['notices']}")
        if entry["validation_errors"]:
            out(f"  errors : {[e.get('message') for e in entry['validation_errors']]}")
        for field in entry["controls"]:
            if field.get("options"):
                out(
                    f"  select : {field.get('label') or field.get('id')} -> "
                    f"{(field.get('options') or [])[:14]}"
                )
            else:
                out(
                    f"  ctrl   : [{field.get('kind')}] {field.get('label') or field.get('id')}"
                    f"{' *required' if field.get('required') else ''} | id={field.get('id')}"
                )
        out(f"  text   : {_excerpt(data)}")
        return data


def record_summary(text: str) -> dict:
    """`record bld26-00469: / commercial electrical / record status: submitted`"""
    summary: dict = {}
    import re

    match = re.search(
        r"Record\s+(?P<id>\S+?):\s*\n?\s*(?P<type>[^\n]+)\nRecord Status:\s*(?P<status>[^\n]+)",
        text or "",
    )
    if match:
        summary = {
            "permit_id": match.group("id").strip(),
            "record_type": match.group("type").strip(),
            "status": match.group("status").strip(),
        }
    expiration = re.search(r"Expiration Date:\s*([^\n]+)", text or "")
    if expiration:
        summary["expiration_date"] = expiration.group(1).strip()
    return summary


async def read(dispatcher, state, probe: Probe, name: str) -> dict:
    outcome = await probe.act(
        dispatcher,
        state,
        {"name": "read_page", "args": {"include": ["text", "form", "errors", "frames"]}},
        note=name,
    )
    return probe.record(name, outcome)


async def open_scheduling(dispatcher, state, probe: Probe, permit_id: str) -> dict:
    """get to the wizard for one record and leave it there (uncommitted)"""
    await probe.act(
        dispatcher,
        state,
        {"name": "navigate", "args": {"url": accela.INSPECTION_ENTRY_URL}},
        note="scheduling entry",
    )
    await probe.act(
        dispatcher,
        state,
        {"name": "wait", "args": {"until_absent": "Loading..."}},
        note="entry settle",
    )
    clicked = await probe.act(
        dispatcher,
        state,
        {"name": "click", "args": {"target": permit_id, "by": "text", "intent": "open_record"}},
        note=f"open {permit_id}",
    )
    if not clicked["success"]:
        return {}
    await probe.act(
        dispatcher,
        state,
        {"name": "wait", "args": {"until_absent": "Loading..."}},
        note="detail settle",
    )
    data = await read(dispatcher, state, probe, f"{permit_id}: record detail (scheduling context)")

    if "Schedule or Request an Inspection" not in (data.get("text") or ""):
        out("  no scheduling link on the record detail")
        return data
    opened = await probe.act(
        dispatcher,
        state,
        {
            "name": "click",
            "args": {
                "target": "Schedule or Request an Inspection",
                "by": "text",
                "intent": "navigate",
            },
        },
        note="open wizard",
    )
    if not opened["success"]:
        return data
    await probe.act(
        dispatcher,
        state,
        {"name": "wait", "args": {"until_present": "Inspection Type"}},
        note="wizard settle",
    )
    return await read(dispatcher, state, probe, f"{permit_id}: available inspection types")


async def dump_type_dialogs(client, stamp: str, tag: str) -> list[str]:
    """save the raw html of any frame holding the type grid"""
    saved: list[str] = []
    frames = list(getattr(client.page, "frames", []) or [])
    for index, frame in enumerate(frames):
        try:
            html = await frame.content()
        except Exception:
            continue
        if accela.INSPECTION_TYPE_ID_MARKER in html:
            path = OUTDIR / f"{stamp}_{tag}_grid_f{index}.html"
            path.write_text(html)
            saved.append(str(path))
            out(f"  grid HTML: {path} ({len(html)} bytes)")
    return saved


async def walk_one_step_deeper(dispatcher, state, probe: Probe, deep: str, data: dict) -> dict | None:
    """choose a type and continue — the commit is later, and is never clicked"""
    options = accela.parse_inspection_types(data.get("fields") or [])
    if not options:
        out("  nothing to select: the wizard offers no inspection types")
        return None
    # prefer a required type: it is the one aca will not let you skip
    choice = next((option for option in options if option.required), options[0])
    out(f"  selecting '{choice.name}' (required={choice.required})")

    chosen = await probe.act(
        dispatcher,
        state,
        {
            "name": "click",
            "args": {
                "target": choice.control_id,
                "by": "selector",
                "intent": "select_inspection_type",
            },
        },
        note=f"select '{choice.name}' by id",
    )
    if not chosen["success"]:
        # the input itself can be hidden and only its label be actionable
        chosen = await probe.act(
            dispatcher,
            state,
            {
                "name": "click",
                "args": {"target": choice.name, "by": "label", "intent": "select_inspection_type"},
            },
            note=f"select '{choice.name}' by label",
        )
    if not chosen["success"]:
        return {"selected": choice.as_dict(), "advanced": False}

    advanced = await probe.act(
        dispatcher,
        state,
        {"name": "click", "args": {"target": "Continue", "by": "text", "intent": "schedule_inspection"}},
        note="continue from the type step (not the commit)",
    )
    if not advanced["success"]:
        return {"selected": choice.as_dict(), "advanced": False}
    await probe.act(
        dispatcher,
        state,
        {"name": "wait", "args": {"until_absent": "Please wait..."}},
        note="next step settle",
    )
    del deep  # only used for labelling in the caller
    return {"selected": choice.as_dict(), "advanced": True}


async def main() -> int:
    args = sys.argv[1:]
    only: tuple[str, ...] = ALL_RECORDS
    deep = DEFAULT_DEEP
    if "--only" in args:
        only = tuple(args[args.index("--only") + 1].split(","))
    if "--deep" in args:
        deep = args[args.index("--deep") + 1]

    probe = Probe()
    session = SolariSession()
    state = AgentState(goal="Map the scheduling wizard (read-only)")
    stamp = stamp_now()
    OUTDIR.mkdir(parents=True, exist_ok=True)
    report: dict = {
        "generated": stamp,
        "records": {},
        "deep_record": deep,
    }

    try:
        client = await session.client()
        dispatcher = ToolDispatcher(client)
        login = await client.login(
            os.environ.get("ACCELA_TEST_USERNAME", "").strip(),
            os.environ.get("ACCELA_TEST_PASSWORD", "").strip(),
        )
        out(f"login ok={login.ok} authenticated={login.data.get('authenticated')}")

        for permit_id in only:
            out(f"\n===== {permit_id} =====")
            data = await open_scheduling(dispatcher, state, probe, permit_id)
            options = accela.parse_inspection_types(data.get("fields") or [])
            total = accela.inspection_type_total(data.get("text") or "")
            entry = {
                "summary": record_summary(data.get("text") or ""),
                "inspection_types": [option.as_dict() for option in options],
                "declared_total": total,
            }
            report["records"][permit_id] = entry
            out(
                f"  => declared={total} page1={len(options)} "
                f"{[(o.name, 'required' if o.required else 'optional') for o in options]}"
            )

            if permit_id == deep and options:
                report["evidence"] = await dump_type_dialogs(client, stamp, "types")
                report["deep_walk"] = await walk_one_step_deeper(
                    dispatcher, state, probe, deep, data
                )
                if report["deep_walk"] and report["deep_walk"]["advanced"]:
                    step = await read(
                        dispatcher, state, probe, f"{deep}: date/time step (NOT confirmed)"
                    )
                    report["date_step"] = {
                        "url": probe.steps[-1]["url"],
                        "flow": probe.steps[-1]["flow"],
                        "controls": probe.steps[-1]["controls"],
                        "validation_errors": probe.steps[-1]["validation_errors"],
                        "text": (step.get("text") or "")[:2000],
                    }
                    report["evidence"] = [
                        *report.get("evidence", []),
                        *await dump_type_dialogs(client, stamp, "date_step"),
                    ]

            # leave the wizard without clicking cancel (the guard holds it)
            await probe.act(
                dispatcher,
                state,
                {"name": "navigate", "args": {"url": accela.MY_RECORDS_URL}},
                note="leave wizard",
            )

        report["blocked_calls"] = [entry for entry in probe.actions if entry["blocked"]]
        report["failed_calls"] = [
            entry for entry in probe.actions if not entry["success"] and not entry["blocked"]
        ]
        out("\n=== available inspection types per record ===")
        for permit_id, entry in report["records"].items():
            names = [t["name"] for t in entry["inspection_types"]]
            out(
                f"  {permit_id:14s} declared={entry['declared_total']} "
                f"page1={len(names)} {names}"
            )
        report["schedulable_records"] = [
            permit_id for permit_id, entry in report["records"].items() if entry["inspection_types"]
        ]
        out(f"  schedulable records: {report['schedulable_records']}")
        out(f"  failed calls: {[e['note'] for e in report['failed_calls']]}")
        out(f"  blocked calls: {[e['note'] for e in report['blocked_calls']]}")
        out("nothing was scheduled")

    except Exception as exc:  # noqa: BLE001 - report, do not mask
        report["error"] = repr(exc)
        out(f"EXCEPTION: {exc!r}")
    finally:
        try:
            await session.close()
        except Exception:
            pass

    report["steps"] = probe.steps
    report["actions"] = probe.actions
    (OUTDIR / f"{stamp}_schedule_probe.json").write_text(json.dumps(report, indent=2))
    out(f"\nreport: {OUTDIR / f'{stamp}_schedule_probe.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
