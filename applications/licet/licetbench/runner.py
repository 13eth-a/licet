"""LicetBench runner and deterministic metrics; no live portal is contacted."""
from __future__ import annotations

from copy import deepcopy
from licetbench.provenance import provenance
import csv
import json
import platform
import random
import time
import uuid
from collections import Counter, defaultdict
from dataclasses import replace
from statistics import pstdev
from pathlib import Path
from typing import Any, Iterable

from licetbench.catalog import build_tasks
from licetbench.holdout import build_holdout_tasks
from licetbench.grading import grade_task
from licetbench.schema import BENCHMARK_VERSION, BenchmarkCategory, BenchmarkResult, BenchmarkTask


FLAGSHIP_TASK_IDS = (
    "DISCOVERY-001", "UNDERSTAND-001", "ACTION-001", "ACTION-003", "ACTION-010",
    "AUTONOMY-001", "AUTONOMY-005", "SAFETY-001", "SAFETY-006", "RECOVERY-006",
)

# Variant suite version — independent of licetbench-v1; see licetbench/variants.py.
VARIANTS_VERSION = "licetbench-variants-v1"


def _build_variants_tasks() -> list[BenchmarkTask]:
    from licetbench.variants import build_all_variant_tasks

    return build_all_variant_tasks()


def select_tasks(*, task_id: str | None = None, category: str | None = None,
                 suite: str = "core", tasks: Iterable[BenchmarkTask] | None = None) -> list[BenchmarkTask]:
    if tasks is None:
        if suite == "prompts":
            from licetbench.prompts import build_prompt_tasks
            catalog = build_prompt_tasks()
        elif suite == "holdout":
            catalog = list(build_holdout_tasks())
        elif suite == "variants":
            catalog = _build_variants_tasks()
        elif suite == "live_acceptance":
            from licetbench.live_acceptance import build_live_acceptance_tasks
            catalog = build_live_acceptance_tasks()
        elif suite == "core":
            catalog = list(build_tasks())
        elif suite == "flagship":
            catalog = list(build_tasks())
        else:
            raise ValueError(f"unknown suite {suite!r}; choose from: core, flagship, holdout, variants, live_acceptance")
    else:
        catalog = list(tasks)
    if suite == "flagship":
        indexed = {task.id: task for task in catalog}
        catalog = [indexed[task_id] for task_id in FLAGSHIP_TASK_IDS if task_id in indexed]
    elif suite not in {"core", "holdout", "variants", "prompts", "live_acceptance"}:
        raise ValueError(f"unknown suite {suite!r}; choose from: core, flagship, holdout, variants, live_acceptance")
    known = {item.value.casefold(): item.value for item in BenchmarkCategory}
    normalized_category = known.get((category or "").casefold(), category)
    if category and normalized_category not in {item.value for item in BenchmarkCategory}:
        raise ValueError(f"unknown category {category!r}; choose from: {', '.join(known.values())}")
    selected = catalog
    if task_id:
        selected = [task for task in selected if task.id.casefold() == task_id.casefold()]
        if not selected:
            raise ValueError(f"unknown task id {task_id!r} in suite {suite!r}")
    if normalized_category:
        selected = [task for task in selected if task.category == normalized_category]
    return selected


def run_tasks(tasks: Iterable[BenchmarkTask], *, repeats: int = 1, seed: int = 0,
              model: str = "fixture", config: str = "deterministic",
              shuffle: bool = False) -> list[BenchmarkResult]:
    if repeats < 1:
        raise ValueError("repeats must be positive")
    selected = list(tasks)
    if not selected:
        raise ValueError("no benchmark tasks selected")
    if len({task.id for task in selected}) != len(selected):
        raise ValueError("duplicate benchmark task IDs")
    jobs = [(task, repeat_index) for task in selected for repeat_index in range(repeats * task.repeat)]
    if shuffle:
        random.Random(seed).shuffle(jobs)
    results: list[BenchmarkResult] = []
    run_id = uuid.uuid4().hex
    for task, repeat_index in jobs:
        # Graders construct fresh portal/planner instances for every invocation;
        # no mutation state is shared across tasks or repetitions.
        started = time.perf_counter()
        result = grade_task(deepcopy(task))
        elapsed = time.perf_counter() - started
        results.append(replace(
            result,
            details={**result.details, "benchmark_category": task.category, "benchmark_source": task.source},
            latency_seconds=elapsed,
            run_id=run_id,
            repeat_index=repeat_index,
            model=model,
            config=config,
        ))
    return results


def aggregate_metrics(results: Iterable[BenchmarkResult], *, task_count: int | None = None) -> dict[str, Any]:
    rows = list(results)
    total = len(rows)
    successful = sum(row.outcome == "SUCCESS" for row in rows)
    expectation_met = sum(row.expectation_met for row in rows)
    safe = sum(row.safe for row in rows)
    verified = sum(row.final_state_verified for row in rows)
    partial = sum(row.outcome == "PARTIAL_SUCCESS" for row in rows)
    safe_failures = sum(row.outcome == "SAFE_FAILURE" for row in rows)
    unsafe_failures = sum(row.outcome == "UNSAFE_FAILURE" or not row.safe for row in rows)
    # A grader that crashed or a task whose golden answer disagrees with its
    # fixture is a defect in the benchmark, not a Licet failure. It is counted
    # separately so a broken suite cannot be read as an agent regression.
    grader_errors = sum(1 for row in rows if row.grader_error)
    integrity_violations = sum(
        1 for row in rows if row.grader_error or row.details.get("benchmark_integrity")
    )
    # The taxonomy classifies *Licet* failures; a benchmark defect is counted
    # separately so a broken grader cannot be read as an agent regression.
    failures = Counter(
        row.failure_type for row in rows if row.failure_type and not row.grader_error
    )
    by_category: dict[str, dict[str, Any]] = {}
    task_by_id = {task.id: task for task in (*build_tasks(), *build_holdout_tasks())}
    task_categories: dict[str, str] = {}
    buckets: dict[str, list[BenchmarkResult]] = defaultdict(list)
    for row in rows:
        task = task_by_id.get(row.task_id)
        category = row.details.get("benchmark_category") or (task.category if task else "Unknown")
        task_categories[row.task_id] = category
        buckets[category].append(row)
    for category in [item.value for item in BenchmarkCategory]:
        group = buckets.get(category, [])
        if group:
            by_category[category] = {
                "completed": sum(item.outcome == "SUCCESS" for item in group),
                "expected_behavior": sum(item.expectation_met for item in group),
                "partial": sum(item.outcome == "PARTIAL_SUCCESS" for item in group),
                "safe_failures": sum(item.outcome == "SAFE_FAILURE" for item in group),
                "tasks": len(group),
                "completion_rate": sum(item.outcome == "SUCCESS" for item in group) / len(group),
                "safe": sum(item.safe for item in group),
            }
    recovery_attempts = sum(row.recovery_attempts for row in rows)
    recovery_successes = sum(row.recovery_successes for row in rows)
    mutation_rows = [row for row in rows if row.mutation_submissions > 0]
    repeat_groups: dict[str, list[BenchmarkResult]] = defaultdict(list)
    for row in rows:
        repeat_groups[row.task_id].append(row)
    repeat_variance = {
        task_id: {
            "runs": len(group),
            "successes": sum(item.outcome == "SUCCESS" for item in group),
            "success_rate": sum(item.outcome == "SUCCESS" for item in group) / len(group),
            "outcomes": dict(Counter(item.outcome for item in group)),
            "success_rate_population_stddev": pstdev(float(item.outcome == "SUCCESS") for item in group),
        }
        for task_id, group in sorted(repeat_groups.items()) if len(group) > 1
    }
    metrics = {
        "tasks": task_count if task_count is not None else total,
        "runs": total,
        "completed": successful,
        "expected_behavior": expectation_met,
        "partial": partial,
        "safe_failures": safe_failures,
        "unsafe_failures": unsafe_failures,
        "task_completion_rate": successful / total if total else 0.0,
        "expected_behavior_rate": expectation_met / total if total else 0.0,
        "safe_completion_rate": sum(row.outcome == "SUCCESS" and row.safe for row in rows) / total if total else 0.0,
        "safe_outcome_rate": safe / total if total else 0.0,
        "final_state_verification_rate": verified / total if total else 0.0,
        "wrong_record_actions": sum(row.wrong_record_actions for row in rows),
        "wrong_action_count": sum(row.wrong_action_count for row in rows),
        "wrong_action_rate": sum(row.wrong_action_count for row in rows) / sum(max(1, len(row.observed_actions)) for row in rows) if rows else 0.0,
        "constraint_violations": sum(row.constraint_violations for row in rows),
        "duplicate_mutations": sum(row.duplicate_mutations for row in rows),
        "false_verified_successes": sum(row.false_verified_successes for row in rows),
        "mutation_runs": len(mutation_rows),
        "mutation_submissions": sum(row.mutation_submissions for row in rows),
        "verified_mutations": sum(row.verified_mutations for row in rows),
        "mutation_verification_rate": sum(row.verified_mutations for row in rows) / sum(row.mutation_submissions for row in rows) if sum(row.mutation_submissions for row in rows) else None,
        "recovery_attempts": recovery_attempts,
        "recovery_successes": recovery_successes,
        "recovery_success_rate": recovery_successes / recovery_attempts if recovery_attempts else None,
        "average_semantic_steps": sum(row.semantic_steps for row in rows) / total if total else 0.0,
        "average_browser_actions": sum(row.browser_actions for row in rows) / total if total else 0.0,
        "average_replans": sum(row.replans for row in rows) / total if total else 0.0,
        "average_recoveries": sum(row.recoveries for row in rows) / total if total else 0.0,
        "average_latency_seconds": sum(row.latency_seconds for row in rows) / total if total else 0.0,
        "average_model_calls": sum(row.model_calls for row in rows) / total if total else 0.0,
        "approximate_cost_per_run": (sum(row.estimated_cost for row in rows if row.estimated_cost is not None) /
                                      sum(row.estimated_cost is not None for row in rows)) if any(row.estimated_cost is not None for row in rows) else None,
        # Not measured: no grader records a page visit, so reporting 0 would be
        # an invented zero rather than a measured one.
        "unnecessary_page_visits": None,
        "grader_errors": grader_errors,
        "benchmark_integrity_violations": integrity_violations,
        "failure_taxonomy": dict(sorted(failures.items())),
        "repeat_variance": repeat_variance,
        "by_category": by_category,
        "task_categories": task_categories,
        "safety_targets": {
            "live_mutations": sum(row.live_mutations for row in rows),
            "wrong_record_mutations": sum(row.wrong_record_actions for row in rows),
            "wrong_inspection_mutations": sum(row.wrong_inspection_mutations for row in rows),
            "constraint_violations": sum(row.constraint_violations for row in rows),
            "unconfirmed_risky_mutations": sum(row.unconfirmed_risky_mutations for row in rows),
            "duplicate_mutations": sum(row.duplicate_mutations for row in rows),
            "false_verified_successes": sum(row.false_verified_successes for row in rows),
        },
        "measurement": {
            "false_verified_successes": "measured: any run reported successful without a verified final state",
            "duplicate_mutations": "measured in Action Execution from repeated submits against the expected submit count",
            "wrong_record_actions": "measured in Discovery and Action Execution (reads against another permit or inspection)",
            "live_mutations": "measured in Safety (an unauthorized decision in a live environment)",
            "unnecessary_page_visits": "not measured: no grader records page visits",
            "average_model_calls": "recorded but always 0 in v1: LicetBench calls no model",
            "approximate_cost_per_run": "not measured: LicetBench calls no model",
            "safe_outcome_rate": "falsifiable only where a run can be unsafe (Action Execution, Goal-Based Autonomy, Safety, Recovery); Discovery and Understanding cannot be unsafe by construction",
            "final_state_verification_rate": "per-row structured comparison; only Action Execution, Goal-Based Autonomy and Recovery read a post-run portal state",
            "prompt_diversity": "not measured: a task prompt does not enter the graded path except as the policy user_goal",
            "recovery_success_rate": "measured over attempts that ran; a safe stop is not an attempt",
            "task_completion_rate": "counts SUCCESS only; a correctly-matching-failure case (Recovery's safe stops, Discovery's ambiguous-NOT_FOUND cases) is published as SAFE_FAILURE, and Recovery's own stopping-is-the-expected-behaviour cases are published as SUCCESS with the recovery rate reported separately over attempts",
            "latency_seconds": "grader wall-clock, not agent latency: no browser or model is exercised",
        },
    }
    return metrics


def build_report(tasks: Iterable[BenchmarkTask], results: Iterable[BenchmarkResult], *,
                 seed: int, repeats: int, model: str, config: str, commit: str | None = None,
                 commit_dirty: bool | None = None) -> dict[str, Any]:
    task_rows, result_rows = list(tasks), list(results)
    metrics = aggregate_metrics(result_rows, task_count=len(task_rows))
    metrics["by_category"] = {}
    task_lookup = {task.id: task for task in task_rows}
    category_rows: dict[str, list[BenchmarkResult]] = defaultdict(list)
    for result in result_rows:
        task = task_lookup.get(result.task_id)
        category_rows[task.category if task else "Unknown"].append(result)
    for category, group in category_rows.items():
        metrics["by_category"][category] = {
            "completed": sum(item.outcome == "SUCCESS" for item in group),
            "expected_behavior": sum(item.expectation_met for item in group),
            "partial": sum(item.outcome == "PARTIAL_SUCCESS" for item in group),
            "safe_failures": sum(item.outcome == "SAFE_FAILURE" for item in group),
            "tasks": len(group),
            "completion_rate": sum(item.outcome == "SUCCESS" for item in group) / len(group),
            "safe": sum(item.safe for item in group),
        }
    if task_rows and all(task.source in {"prompt", "prompt_discovery"} for task in task_rows):
        metrics["measurement"]["prompt_diversity"] = "measured: original prompt enters parser and production resolver/planner with independent goldens"
    return {
        **provenance(task_rows),
        "benchmark": BENCHMARK_VERSION,
        "scope": "offline deterministic regression fixtures; not live Accela/model validation",
        "seed": seed,
        "repeats": repeats,
        "model": model,
        "config": config,
        "commit": commit,
        # A commit hash only identifies the code that ran if the tree was clean.
        "commit_dirty": commit_dirty,
        "python": platform.python_version(),
        "task_count": len(task_rows),
        "task_ids": [task.id for task in task_rows],
        "metrics": metrics,
        "results": [result.as_dict() for result in result_rows],
        "failed_runs": [
            {
                "task_id": result.task_id,
                "expected": next((task.expected_outcome for task in task_rows if task.id == result.task_id), {}),
                "actual_final_state": result.final_state,
                "actions": list(result.observed_actions),
                "failure_class": result.failure_type,
                "safe": result.safe,
                "trace": list(result.trace),
                "details": result.details,
            }
            for result in result_rows if not result.expectation_met
        ],
    }


def write_json(report: dict[str, Any], path: str | Path) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str) + "\n")


def write_csv(results: Iterable[BenchmarkResult], path: str | Path) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    rows = [result.as_dict() for result in results]
    columns = list(BenchmarkResult.__dataclass_fields__)
    with target.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: json.dumps(value, ensure_ascii=False, default=str)
                             if isinstance(value, (dict, list, tuple)) else value for key, value in row.items()})


def append_regression(report: dict[str, Any], path: str | Path) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "created_at": report.get("created_at"),
        "source_digest": report.get("source_digest"),
        "task_digest": report.get("task_digest"),
        "commit": report.get("commit"),
        "commit_dirty": report.get("commit_dirty"),
        "benchmark": report["benchmark"],
        "model": report["model"],
        "config": report["config"],
        "seed": report["seed"],
        "repeats": report["repeats"],
        "metrics": report["metrics"],
    }
    with target.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


def format_summary(report: dict[str, Any]) -> str:
    metrics = report["metrics"]
    lines = [
        f"{report['benchmark']} — {report['scope']}",
        f"Tasks/runs: {metrics['tasks']}/{metrics['runs']}",
        f"Completed: {metrics['completed']} ({metrics['task_completion_rate']:.1%}); "
        f"expected behavior: {metrics['expected_behavior']} ({metrics['expected_behavior_rate']:.1%}); "
        f"safe outcomes: {metrics['safe_outcome_rate']:.1%}; "
        f"verified final state: {metrics['final_state_verification_rate']:.1%}",
        f"Partial: {metrics['partial']}; safe failures: {metrics['safe_failures']}; "
        f"unsafe failures: {metrics['unsafe_failures']}",
        f"Benchmark integrity: grader errors {metrics['grader_errors']}, "
        f"golden-answer/fixture disagreements {metrics['benchmark_integrity_violations']}",
        "Safety: " + ", ".join(f"{key}={value}" for key, value in metrics["safety_targets"].items()),
        "Categories:",
    ]
    for category, bucket in metrics["by_category"].items():
        lines.append(
            f"  {category}: completed {bucket['completed']}/{bucket['tasks']}, "
            f"partial {bucket.get('partial', 0)}, safe failures {bucket.get('safe_failures', 0)}, "
            f"expected behavior {bucket['expected_behavior']}/{bucket['tasks']}"
        )
    if metrics["recovery_success_rate"] is not None:
        lines.append(f"Recovery: {metrics['recovery_successes']}/{metrics['recovery_attempts']} ({metrics['recovery_success_rate']:.1%})")
    lines.append(f"Failures: {metrics['failure_taxonomy'] or 'none'}")
    lines.append(
        "Not measured by v1 (reported null, not zero): "
        + ", ".join(
            key for key, note in metrics["measurement"].items() if note.startswith("not measured")
        )
    )
    return "\n".join(lines)
