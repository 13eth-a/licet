"""Run a goal with an explicit capability factory and bounded semantic planner.

Factories own portal/session setup and provide the trusted Phase 2–4 read and
execution adapters. Use the scripted factory for an entirely offline demo.
"""
import argparse
import asyncio
from dataclasses import replace
from datetime import date
import importlib
import inspect
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from licet.phase5 import GoalPlanner, ModelSelector, Status, parse_goal


async def main(args):
    module, sep, attribute = args.factory.partition(":")
    if not sep:
        raise ValueError("factory must be module:callable")
    capabilities = getattr(importlib.import_module(module), attribute)()
    if inspect.isawaitable(capabilities):
        capabilities = await capabilities
    selector = None
    if args.model:
        from licet.agent.model import build_model
        from licet.config import load_config
        selector = ModelSelector(build_model(replace(load_config(), agent_model=args.model)))
    goal = parse_goal(args.goal, reference=date.fromisoformat(args.reference) if args.reference else None)
    planner = GoalPlanner(capabilities, selector=selector, max_steps=args.max_steps,
                          trace_path=args.output.with_suffix(".jsonl"))
    run = await planner.run(goal)
    report = run.report()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
    # A CLI run cannot assume approval. Resume is an explicit in-process API.
    return 0 if run.status == Status.SUCCESS else 2 if run.status == Status.PARTIAL_SUCCESS else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("goal")
    parser.add_argument("--factory", required=True)
    parser.add_argument("--model", help="optional configured semantic-choice model; deterministic by default")
    parser.add_argument("--reference", help="local calendar date, YYYY-MM-DD")
    parser.add_argument("--max-steps", type=int, default=20)
    parser.add_argument("--output", type=Path, default=Path("logs/phase5/goal.json"))
    raise SystemExit(asyncio.run(main(parser.parse_args())))
