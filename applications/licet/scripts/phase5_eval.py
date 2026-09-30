"""Repeat the scripted flagship goal; publish outcomes, traces and metrics.

No browser, model, credential, or live mutation is used. This is deterministic
planner acceptance, not evidence of ten successful live portal mutations.
"""
import argparse
import asyncio
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from licet.eval.phase5 import planner_metrics
from licet.eval.phase5_fixtures import ScriptedCapabilities
from licet.phase5 import GoalPlanner, Status, parse_goal

GOAL = "Find the permit at 123 Main Street, figure out what is blocking it, and get it ready for its next inspection without paying any fees or signing anything."
PATH = ["FIND_PERMIT", "READ_PERMIT_STATE", "DETERMINE_BLOCKERS", "DETERMINE_NEXT_INSPECTION",
        "CHECK_INSPECTION_AVAILABILITY", "SCHEDULE_INSPECTION", "VERIFY_STATE"]


async def evaluate(count):
    runs = []
    for _ in range(count):
        runs.append(await GoalPlanner(ScriptedCapabilities()).run(parse_goal(GOAL)))
    return {"mode": "scripted, no live mutations", "goal": GOAL,
            "metrics": planner_metrics(runs, expected_actions=[PATH] * count,
                                       necessary_reads=[{"READ_PERMIT_STATE"}] * count),
            "passed": all(r.status == Status.SUCCESS for r in runs),
            "runs": [r.report() for r in runs]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=10)
    parser.add_argument("--output", type=Path, default=Path("logs/phase5/scripted_flagship.json"))
    args = parser.parse_args()
    if not 1 <= args.runs <= 100:
        parser.error("runs must be between 1 and 100")
    report = asyncio.run(evaluate(args.runs))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2))
    print(json.dumps({"passed": report["passed"], **report["metrics"]}, indent=2))
    raise SystemExit(0 if report["passed"] else 1)
