"""command line entry point: ``python m licetbench run``"""
from __future__ import annotations

import argparse
from dataclasses import replace
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from licetbench.catalog import build_tasks
from licetbench.runner import (
    append_regression,
    build_report,
    format_summary,
    run_tasks,
    select_tasks,
    write_csv,
    write_json,
)


def _git(*args: str) -> str | None:
    try:
        completed = subprocess.run(
            ["git", *args], capture_output=True, text=True, check=False, timeout=2,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return completed.stdout if completed.returncode == 0 else None


def _commit() -> str | None:
    return (_git("rev-parse", "HEAD") or "").strip() or None


def _commit_dirty() -> bool | None:
    """whether the recorded commit describes the code that actually ran"""
    status = _git("status", "--porcelain")
    return None if status is None else bool(status.strip())


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m licetbench", description="Reproducible LicetBench v1 evaluation")
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("list", help="list task IDs and categories")
    run = subparsers.add_parser("run", help="run the deterministic offline benchmark")
    run.add_argument("task_id", nargs="?", help="run one unique task, e.g. DISCOVERY-001")
    run.add_argument("--category", help="run one benchmark category")
    run.add_argument("--suite", choices=("core", "flagship", "holdout", "variants", "prompts", "live_acceptance"), default="core", help="task suite (live_acceptance grades captured evidence offline; it never contacts a portal)")
    run.add_argument("--seed", type=int, default=0, help="seed recorded with this run and used for optional shuffling")
    run.add_argument("--repeat", type=int, default=1, help="repeat every selected task from fresh fixture state")
    run.add_argument("--shuffle", action="store_true", help="shuffle runs deterministically using --seed")
    run.add_argument("--max-steps", type=int, help="actual planner step ceiling for autonomy/prompt experiments")
    run.add_argument("--model", default="fixture", help="model label recorded in artifacts")
    run.add_argument("--config", default="deterministic", help="configuration label recorded in artifacts")
    run.add_argument("--commit", default=None, help="commit label (defaults to current HEAD when available)")
    run.add_argument("--output-dir", type=Path, default=Path("logs/licetbench"), help="artifact directory")
    run.add_argument("--no-regression-log", action="store_true", help="do not append the JSONL regression record")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.command == "list":
        for task in build_tasks():
            print(f"{task.id:16} {task.category:24} {task.tags[0] if task.tags else ''}")
        return 0
    if args.command not in {None, "run"}:
        parser.error(f"unknown command {args.command!r}")

    task_id = getattr(args, "task_id", None)
    category = getattr(args, "category", None)
    suite = getattr(args, "suite", "core")
    seed = getattr(args, "seed", 0)
    repeats = getattr(args, "repeat", 1)
    model = getattr(args, "model", "fixture")
    config = getattr(args, "config", "deterministic")
    shuffle = getattr(args, "shuffle", False)
    output_dir = getattr(args, "output_dir", Path("logs/licetbench"))
    commit = getattr(args, "commit", None) or _commit()
    commit_dirty = _commit_dirty() if commit else None
    no_regression_log = getattr(args, "no_regression_log", False)

    try:
        tasks = select_tasks(task_id=task_id, category=category, suite=suite)
        if getattr(args, "max_steps", None) is not None:
            tasks = [replace(task, max_steps=min(task.max_steps, args.max_steps)) for task in tasks]
        results = run_tasks(tasks, repeats=repeats, seed=seed, model=model, config=config, shuffle=shuffle)
    except ValueError as exc:
        parser.error(str(exc))
    report = build_report(tasks, results, seed=seed, repeats=repeats, model=model,
                          config=config, commit=commit, commit_dirty=commit_dirty)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    stem = task_id or (category.lower().replace(" ", "-") if category else suite)
    base = output_dir / f"{stamp}_{stem}"
    write_json(report, base.with_suffix(".json"))
    write_csv(results, base.with_suffix(".csv"))
    if not no_regression_log:
        append_regression(report, output_dir / "regression.jsonl")
    print(format_summary(report))
    if report.get("commit_dirty"):
        print(
            f"Note: the working tree is dirty, so commit {report['commit']} does not "
            "identify the code this run used."
        )
    print(f"JSON: {base.with_suffix('.json')}\nCSV:  {base.with_suffix('.csv')}")
    return 0 if not report["failed_runs"] and report["metrics"]["unsafe_failures"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
