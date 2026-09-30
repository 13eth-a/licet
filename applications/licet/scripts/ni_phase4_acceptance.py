"""Phase 4 live acceptance run - the exit-gate harness.

Walks the real stack for one permit and reports what Phase 4 actually does:

    read the record's inspections -> read the wizard's offered types
        -> read the calendar's availability -> select + policy + execute
        -> independently re-read and verify

SAFE BY DEFAULT. A bare run is a *plan*: it reads, builds the action, prints the
preview, and stops. Nothing is submitted unless `--execute` is passed, and even
then the mutation goes through the same policy -> executor -> adapter path the
product uses. This script adds no shortcut around that path, and never retries a
submission (the executor reconciles by re-reading state).

On the current Null Island sandbox every calendar day is inactive (measured
live, `scripts/ni_availability_sweep.py`), so an honest plan run stops at
`DATE_CONSTRAINT_UNSATISFIED` / `NO_AVAILABLE_DATES`. That is a *pass* for the
harness: it proves the availability gate fires against the live portal. A
booking can only be accepted where availability exists.

Run:
    .venv/bin/python scripts/ni_phase4_acceptance.py --record BLD26-00469 \
        --type Rough --date "earliest available"                 # plan only
    .venv/bin/python scripts/ni_phase4_acceptance.py --record BLD26-00469 \
        --type Rough --date "earliest available" --execute        # real submission
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from licet.agent.state import AgentState  # noqa: E402
from licet.browser import accela  # noqa: E402
from licet.browser.dispatcher import ToolDispatcher  # noqa: E402
from licet.browser.solari_client import SolariSession  # noqa: E402
from licet.logging.logger import RunLogger, new_run_id  # noqa: E402
from licet.phase4.accela_portal import AccelaInspectionPortal  # noqa: E402
from licet.phase4.dates import available_dates_from_calendar  # noqa: E402
from licet.phase4.metrics import Phase4Metrics  # noqa: E402
from licet.phase4.runner import ActionRequest, Phase4ActionRunner, preview  # noqa: E402
from licet.safety.policy import ConfirmationRequest  # noqa: E402

OUTDIR = Path("logs/ni_backoffice/phase4")


class LoopBridgePortal:
    """Adapt the adapter's async primitives to the executor's sync protocol.

    The Phase 4 executor is sync by design and the adapter's sync methods wrap
    ``asyncio.run``, but the live Solari client is bound to this script's running
    loop. So the executor runs on a worker thread and its portal calls are
    marshalled back onto the live loop — the same loop that owns the client.
    """

    def __init__(self, inner: AccelaInspectionPortal, loop: asyncio.AbstractEventLoop) -> None:
        self._inner = inner
        self._loop = loop

    def _call(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result()

    def read_inspection_state(
        self, permit_id: str, inspection_type: str | None = None, inspection_id: str | None = None
    ):
        return self._call(
            self._inner.read_inspection_state_async(permit_id, inspection_type, inspection_id)
        )

    def submit_inspection_action(
        self, action, *, portal_type: str, selected_date: str | None = None
    ):
        return self._call(
            self._inner.submit_inspection_action_async(
                action, portal_type=portal_type, selected_date=selected_date
            )
        )


def out(message: str) -> None:
    print(message, flush=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Phase 4 live acceptance run (plan-only unless --execute)")
    parser.add_argument("--record", required=True, help="display record id, e.g. BLD26-00469")
    parser.add_argument("--type", required=True, help="inspection type as the portal spells it")
    parser.add_argument("--action", default="schedule", choices=["schedule", "reschedule", "cancel"])
    parser.add_argument("--date", default="earliest available", help="date instruction, e.g. 'next week'")
    parser.add_argument("--window-start", default=None, help="ISO explicit window start")
    parser.add_argument("--window-end", default=None, help="ISO explicit window end")
    parser.add_argument("--existing-inspection-id", default=None, help="target appointment for reschedule/cancel")
    parser.add_argument("--confirm", action="store_true", help="confirmation for a cancellation")
    parser.add_argument("--execute", action="store_true", help="actually submit (default is plan-only)")
    return parser


def build_request(args: argparse.Namespace) -> ActionRequest:
    """The request this run would execute. Pure: no browser, no mutation.

    `--confirm` is the human at the terminal naming one record, one type and one
    existing inspection, so it produces the scoped, single-use approval for
    exactly that selection. Phase 6 refuses a consequential action that arrives
    with only a boolean: a boolean names no target and could not be checked
    against what the operator approved.
    """
    date_instruction = None
    if args.date and args.date.strip().lower() not in {"", "none"}:
        date_instruction = args.date
    return ActionRequest(
        action_type=args.action,
        permit_id=args.record,
        inspection_type=args.type,
        date_instruction=date_instruction,
        date_window_start=args.window_start,
        date_window_end=args.window_end,
        existing_inspection_id=args.existing_inspection_id,
        confirmed=args.confirm,
        approval=(ConfirmationRequest(
            action_type=args.action,
            permit_id=args.record,
            target=args.type or args.existing_inspection_id or "",
            consequence=f"{args.action} {args.type or args.existing_inspection_id or 'the record'}",
            inspection_id=args.existing_inspection_id,
            record_key=None,  # this CLI names the display record id, not a record key
            date_window_start=args.window_start,
            date_window_end=args.window_end,
        ) if args.confirm else None),
        allow_alternatives=True,
    )


async def _act(dispatcher: ToolDispatcher, state: AgentState, call: dict, note: str) -> dict:
    outcome = await dispatcher.execute(call, state)
    error = (outcome.get("error") or {}).get("kind")
    out(f"  [{call.get('name')}] {note} success={outcome['success']} blocked={outcome['blocked']} err={error}")
    return outcome


async def read_portal_facts(dispatcher: ToolDispatcher, state: AgentState, record: str, expected: str) -> dict:
    """Read-only: the wizard's offered types for one record.

    Nothing is clicked past the wizard's type grid; `read_availability` performs
    the one extra step needed to see the calendar.
    """
    facts: dict = {"types": [], "available_dates": [], "flow": None}
    await _act(dispatcher, state, {"name": "navigate", "args": {"url": accela.INSPECTION_ENTRY_URL}}, "entry")
    await _act(dispatcher, state, {"name": "wait", "args": {"until_absent": "Loading..."}}, "entry settle")
    opened = await _act(
        dispatcher, state,
        {"name": "click", "args": {"target": record, "by": "text", "intent": "open_record"}},
        f"open {record}",
    )
    if not opened["success"]:
        facts["error"] = "record not reachable from the account's scheduling context"
        return facts
    await _act(dispatcher, state, {"name": "wait", "args": {"until_absent": "Loading..."}}, "detail settle")
    detail = await dispatcher.execute({"name": "read_page", "args": {"include": ["text", "form"]}}, state)
    facts["detail_url"] = (detail.get("data") or {}).get("url")

    await _act(
        dispatcher, state,
        {"name": "click", "args": {"target": "Schedule or Request an Inspection", "by": "text", "intent": "navigate"}},
        "open wizard",
    )
    await _act(dispatcher, state, {"name": "wait", "args": {"until_present": "Inspection Type"}}, "wizard settle")
    wizard = await dispatcher.execute({"name": "read_page", "args": {"include": ["text", "form"]}}, state)
    fields = (wizard.get("data") or {}).get("fields") or []
    facts["types"] = [option.name for option in accela.parse_inspection_types(fields)]
    if not facts["types"]:
        facts["note"] = "the wizard offers no inspection types"
        return facts
    if not any(name.casefold() == expected.casefold() for name in facts["types"]):
        facts["note"] = f"{expected!r} is not offered by this record's wizard"
    return facts


async def read_availability(dispatcher: ToolDispatcher, state: AgentState, chosen: str) -> list[str]:
    await _act(
        dispatcher, state,
        {"name": "click", "args": {"target": chosen, "by": "label", "intent": "select_inspection_type"}},
        f"select {chosen!r}",
    )
    await _act(
        dispatcher, state,
        {"name": "click", "args": {"target": "Continue", "by": "text", "intent": "schedule_inspection"}},
        "continue to the calendar (this is NOT the commit)",
    )
    await _act(dispatcher, state, {"name": "wait", "args": {"until_absent": "Please wait..."}}, "calendar settle")
    page = await dispatcher.execute(
        {"name": "read_page", "args": {"include": ["text", "form", "errors", "frames"]}}, state
    )
    months = (page.get("data") or {}).get("calendar") or []
    resolved = accela.resolve_calendar_months(months, reference=date.today())
    return available_dates_from_calendar(resolved, reference=date.today())


async def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    request = build_request(args)
    run_id = new_run_id("phase4-acceptance")
    logger = RunLogger(run_id)
    metrics = Phase4Metrics()
    report: dict = {"run_id": run_id, "record": args.record, "execute": args.execute}

    try:
        planned = preview(request)
    except ValueError as exc:
        out(f"REFUSED before any browser step: {exc}")
        report["refused"] = str(exc)
        return 1

    out("=== proposed action ===")
    for key, value in planned.items():
        out(f"  {key}: {value}")
    if not args.execute:
        out("\nplan only: nothing will be submitted (pass --execute to submit)")

    session = SolariSession()
    state = AgentState(goal=f"Phase 4 acceptance: {args.action} {args.type} on {args.record}")
    try:
        client = await session.client()
        dispatcher = ToolDispatcher(client)
        login = await client.login(
            os.environ.get("ACCELA_TEST_USERNAME", "").strip(),
            os.environ.get("ACCELA_TEST_PASSWORD", "").strip(),
        )
        out(f"login ok={login.ok} authenticated={login.data.get('authenticated')}")

        facts = await read_portal_facts(dispatcher, state, args.record, args.type)
        report["facts"] = facts
        if facts.get("error") or facts.get("note"):
            out(f"\nstopped: {facts.get('error') or facts.get('note')}")
            return 0
        available = await read_availability(dispatcher, state, args.type)
        report["available_dates"] = available
        out(f"\noffered types: {facts['types']}")
        out(f"available dates: {available or 'none (the calendar offers no active day)'}")

        ref = accela.parse_ref_from_url(str(facts.get("detail_url") or ""))
        portal = LoopBridgePortal(
            AccelaInspectionPortal(dispatcher, record_ref=ref or None),
            asyncio.get_running_loop(),
        )
        runner = Phase4ActionRunner(portal, metrics=metrics, logger=logger)
        # The Phase 4 stack is sync by design: the adapter's sync methods wrap
        # ``asyncio.run`` and refuse to run inside a live loop. Hand the executor to a
        # worker thread so it reaches the portal exactly as the product does.
        result = await asyncio.to_thread(
            runner.run,
            request,
            eligible_types=tuple(facts["types"]),
            available_dates=tuple(available),
        )
        report["result"] = result.as_dict()
        report["metrics"] = metrics.as_dict()
        out("\n=== outcome ===")
        out(f"  success={result.success} verified={result.verified} "
            f"state={result.verification_state.value} error={result.error}")
        if result.alternatives:
            out(f"  closest alternatives outside the window: {list(result.alternatives)}")
        out(f"  zero targets: {metrics.zero_targets()}")
        out(f"  within zero targets: {all(v == 0 for v in metrics.zero_targets().values())}")
    except Exception as exc:  # noqa: BLE001 - report, do not mask
        report["error"] = repr(exc)
        out(f"EXCEPTION: {exc!r}")
    finally:
        try:
            await session.close()
        except Exception:
            pass
        logger.log_metrics("phase4_kpis", metrics.as_dict())
        OUTDIR.mkdir(parents=True, exist_ok=True)
        path = OUTDIR / f"{run_id}.json"
        path.write_text(json.dumps(report, indent=2, default=str))
        out(f"\nreport: {path}")
        out(f"run log: {logger.path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
