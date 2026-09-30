"""Phase 5 live acceptance run — the exit-gate harness.

Runs the *real* stack for one goal: Phase 2 lookup -> Phase 3 read/reason ->
Phase 4 selection/preflight/execution, with the goal-based planner choosing each
semantic action. No shortcut around the product path is added here.

SAFE BY DEFAULT. A bare run cannot execute a mutation: every mutation action is
put behind an explicit approval pause (`confirmation_required`), and the harness
never resumes one. `--execute` restores the product defaults (schedule/reschedule
automatic, cancellation still needs confirmation). Even then, the policy layer,
preflight and executor gate the action exactly as in the product — the harness
adds no permission.

The captured `LIVE_PLAN_ONLY_ACCEPTANCE` evidence is from Accela's `aca-test`
host (classified by Licet as sandbox), run plan-only: its identity-verified
calendar had no active dates in Sep–Nov 2026. Cost and signature disclosure
remain unknown, and no mutation was attempted. The acceptance summary reports
those facts separately; it does not imply that the calendar was checked beyond
those observed months or that a real booking occurred.

Run:
    .venv/bin/python scripts/ni_phase5_acceptance.py \
        "Get permit BLD26-00469 ready for its next inspection."          # plan-only
    .venv/bin/python scripts/ni_phase5_acceptance.py \
        "Get permit BLD26-00469 ready for its next inspection." --execute
"""
from __future__ import annotations

import argparse
import asyncio
import json
from datetime import date
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from licet.eval.phase5_live import build_live_capabilities  # noqa: E402
from licet.logging.logger import RunLogger, new_run_id  # noqa: E402
from licet.phase5 import GoalPlanner, Status, parse_goal  # noqa: E402
from licet.phase5.acceptance import (  # noqa: E402
    format_live_plan_only_summary,
    live_plan_only_summary,
)
from licet.phase5.state import MUTATIONS  # noqa: E402

OUTDIR = Path("logs/ni_backoffice/phase5")


def out(message: str) -> None:
    print(message, flush=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Phase 5 live acceptance run (plan-only unless --execute)")
    parser.add_argument("goal", help="outcome-oriented goal, e.g. 'Get permit BLD26-00469 ready for its next inspection.'")
    parser.add_argument("--reference", default=None, help="local calendar date, YYYY-MM-DD")
    parser.add_argument("--max-steps", type=int, default=20)
    parser.add_argument("--attempts", type=int, default=3,
                        help="retry a BLOCKED run this many times, each in a fresh session"
                             " (the live tool layer is intermittently flaky)")
    parser.add_argument("--timeout", type=float, default=90.0, help="per semantic step timeout, seconds")
    parser.add_argument("--execute", action="store_true",
                        help="allow product-default mutations (cancellation still needs confirmation)")
    parser.add_argument("--output", type=Path, default=None)
    return parser


def _print_trace(report: dict) -> None:
    out("\n=== semantic trace ===")
    for entry in report.get("trace", []):
        if "step" not in entry:
            out(f"  [approval] {entry.get('reason')}")
            continue
        mark = "ok " if entry.get("success") else "FAIL"
        out(f"  {entry['step']:>10} {mark} {entry['action']:<30} progress={entry.get('progress')} "
            f"remaining={entry.get('remaining_goal')} ({entry.get('message')})")


async def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    reference = date.fromisoformat(args.reference) if args.reference else None
    goal = parse_goal(args.goal, reference=reference)
    out(f"goal: {goal.objective}")
    out(f"autonomous={goal.autonomous} permit={goal.permit_id} operation={goal.operation} "
        f"type={goal.inspection_type} prohibited={list(goal.prohibited_actions)}")
    if goal.clarification:
        out(f"goal needs clarification before execution: {goal.clarification}")

    output = args.output or (OUTDIR / f"{date.today().isoformat()}_phase5_live.json")
    log_dir = output.parent / f"{new_run_id('phase5-live')}"
    logger = RunLogger(log_dir.name, log_dir=log_dir)
    report: dict = {"goal": args.goal, "execute": args.execute, "reference": args.reference,
                    "attempts": []}
    try:
        confirmation = frozenset() if args.execute else frozenset(MUTATIONS)
        run = None
        for attempt in range(1, args.attempts + 1):
            # A fresh session per attempt: a session left mid-flow by a flaky
            # step wedges the next run (observed live), and re-using one would
            # make the retry itself untrustworthy.
            live = await build_live_capabilities(logger=logger)
            out(f"login: authenticated  (run log: {logger.path})")
            try:
                planner = GoalPlanner(live.capabilities, max_steps=args.max_steps,
                                      timeout_seconds=args.timeout,
                                      confirmation_required=confirmation,
                                      trace_path=output.with_suffix(".trace.jsonl"))
                run = await planner.run(goal)
            finally:
                await live.close()
            record = run.report()
            report["attempts"].append({
                "attempt": attempt, "status": record["status"], "error": record["error"],
                "reason": record["reason"], "semantic_steps": record["metrics"]["semantic_steps"],
                "useful_steps": record["metrics"]["useful_steps"], "trace": record["trace"],
            })
            out(f"attempt {attempt}: status={run.status.value} error={run.error.value if run.error else None}"
                f" steps={record['metrics']['semantic_steps']}")
            # A BLOCKED run here is how an intermittent live tool failure surfaces;
            # any other status is a real planner outcome and is not retried.
            if run.status != Status.BLOCKED:
                break
        final = run.report()
        report.update(final)
        if not args.execute and not run.attempted_mutations:
            acceptance = live_plan_only_summary(final)
            if acceptance["blocker"] and acceptance["availability_checked"]:
                report["acceptance_summary"] = acceptance
        _print_trace(final)
        if report.get("acceptance_summary"):
            out("\n=== plan-only acceptance ===")
            out(format_live_plan_only_summary(report["acceptance_summary"]))
        out("\n=== outcome ===")
        out(f"  status={run.status.value} error={run.error.value if run.error else None}")
        out(f"  reason={run.reason}")
        out(f"  remaining_goal={final['remaining_goal']}")
        out(f"  metrics={final['metrics']}")
        out(f"  mutations_attempted={final['metrics']['mutations_attempted']}")
        if run.status == Status.NEEDS_APPROVAL:
            out(f"  approval paused for: {run.world.proposal.as_dict() if run.world.proposal else None}")
    except Exception as exc:  # noqa: BLE001 - report, do not mask
        report["error"] = repr(exc)
        out(f"EXCEPTION: {exc!r}")
    finally:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2, default=str))
        out(f"\nreport: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
