"""Run the eval fixtures without a browser.

The fixtures existed for the whole of Phase 0 with no caller, which is how they
drifted into being unrunnable (unsupported placeholders, expectations the
environment cannot satisfy). This is the entry point:

    .venv/bin/python scripts/ni_eval_fixtures.py --validate   # is the suite runnable?
    .venv/bin/python scripts/ni_eval_fixtures.py --list       # prompt -> expectation
    .venv/bin/python scripts/ni_eval_fixtures.py --explain P13
    .venv/bin/python scripts/ni_eval_fixtures.py --score runs.json
    .venv/bin/python scripts/ni_eval_fixtures.py --merge all.json a.json b.json

`--score` takes `{prompt_id: {final_answer, actions, stop_condition, steps, model}}`
recorded by an agent run and prints a per-criterion verdict. Exit code is 0 only
when validation passes and every scored case passes.

`--merge` exists because the suite is run in batches (each case is a real browser
session, and one bad case should not cost the others): merge the batch files into
one and score that.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from licet.eval.harness import RunRecord, build_cases, explain_case, score_runs, validate_fixtures
from licet.eval.records import SCHEDULING_GROUND_TRUTH, record_for


def out(message: str = "") -> None:
    print(message, flush=True)


def cmd_validate() -> int:
    problems = validate_fixtures()
    cases = build_cases()
    out(f"cases: {len(cases)}")
    by_category: dict[str, int] = {}
    by_expects: dict[str, int] = {}
    for case in cases:
        by_category[case.category] = by_category.get(case.category, 0) + 1
        by_expects[case.expects] = by_expects.get(case.expects, 0) + 1
    out(f"  by category: {by_category}")
    out(f"  by expectation: {by_expects}")
    out()
    out("scheduling ground truth (measured 2026-09-20):")
    for permit_id, truth in SCHEDULING_GROUND_TRUTH.items():
        record = record_for(permit_id)
        out(
            f"  {permit_id:14s} bookable={truth['schedulable']} "
            f"types={truth['declared_total']:>2} "
            f"active_days={truth['calendar_active_days']} "
            f"{(record.address if record else '')}"
        )
    out()
    if problems:
        out(f"FIXTURE PROBLEMS ({len(problems)}):")
        for problem in problems:
            out(f"  - {problem}")
        return 1
    out("fixtures OK: every placeholder renders, every record has ground truth,")
    out("every expectation is achievable on this environment.")
    return 0


def cmd_list() -> int:
    for case in build_cases():
        out(f"{case.prompt_id} [{case.category:>9} / {case.expects:<13}] {case.prompt}")
    return 0


def cmd_explain(prompt_id: str) -> int:
    out(explain_case(prompt_id))
    return 0


def cmd_score(path: str) -> int:
    payload = json.loads(Path(path).read_text())
    runs = {
        prompt_id: RunRecord(
            prompt_id=prompt_id,
            final_answer=entry.get("final_answer", ""),
            actions=entry.get("actions", []),
            stop_condition=entry.get("stop_condition"),
            steps=int(entry.get("steps", 0)),
            model=entry.get("model", ""),
        )
        for prompt_id, entry in payload.items()
    }
    report = score_runs(build_cases(), runs)
    for result in report["results"]:
        mark = "PASS" if result["passed"] else "FAIL"
        out(f"[{mark}] {result['prompt_id']} {result['prompt'][:70]}")
        for key, item in (result.get("criteria") or {}).items():
            out(f"        {'ok  ' if item['passed'] else 'FAIL'} {key}: {item['detail']}")
        if result.get("error"):
            out(f"        {result['error']}")
    out()
    out(f"scored {report['passed']}/{report['total']}")
    for category, bucket in report["by_category"].items():
        out(f"  {category:>9}: {bucket['passed']}/{bucket['total']}")
    return 0 if report["passed"] == report["total"] else 1


def cmd_merge(output: str, inputs: list[str]) -> int:
    merged: dict[str, dict] = {}
    for path in inputs:
        payload = json.loads(Path(path).read_text())
        for prompt_id, entry in payload.items():
            if prompt_id in merged:
                out(f"  {prompt_id}: keeping the first run, ignoring {path}")
                continue
            merged[prompt_id] = entry
    Path(output).write_text(json.dumps(merged, indent=2, ensure_ascii=False))
    out(f"merged {len(inputs)} file(s) -> {output} ({len(merged)} case(s))")
    return 0


def main() -> int:
    args = sys.argv[1:]
    if "--list" in args:
        return cmd_list()
    if "--merge" in args:
        rest = args[args.index("--merge") + 1 :]
        if len(rest) < 2:
            out("--merge needs an output path and at least one input file")
            return 2
        return cmd_merge(rest[0], rest[1:])
    if "--explain" in args:
        return cmd_explain(args[args.index("--explain") + 1])
    if "--score" in args:
        return cmd_score(args[args.index("--score") + 1])
    return cmd_validate()


if __name__ == "__main__":
    raise SystemExit(main())
