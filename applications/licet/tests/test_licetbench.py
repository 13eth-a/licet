from __future__ import annotations

import csv
from dataclasses import replace
import json
import subprocess
import sys
from pathlib import Path

import pytest

from licetbench.catalog import build_tasks
from licetbench.holdout import build_holdout_tasks
from licetbench.live_acceptance import build_live_acceptance_tasks
from licetbench.runner import (
    aggregate_metrics,
    build_report,
    run_tasks,
    select_tasks,
    write_csv,
    write_json,
)
from licetbench.schema import BenchmarkCategory, BenchmarkResult, BenchmarkTask, Outcome


def test_core_catalogue_has_50_unique_tasks_in_locked_category_split():
    tasks = build_tasks()
    assert len(tasks) == 50
    assert len({task.id for task in tasks}) == 50
    assert {category.value: sum(task.category == category.value for task in tasks)
            for category in BenchmarkCategory} == {
        "Permit Discovery": 10,
        "Permit Understanding": 10,
        "Action Execution": 10,
        "Goal-Based Autonomy": 8,
        "Safety": 6,
        "Recovery": 6,
    }
    assert all(task.expected_outcome for task in tasks)
    assert all(task.initial_state is not task.expected_outcome for task in tasks)


def test_live_plan_only_acceptance_is_offline_scoped_and_grades_captured_evidence():
    tasks = build_live_acceptance_tasks()
    assert len(tasks) == 1
    task = tasks[0]
    assert task.id == "LIVE-PLAN-ONLY-001"
    assert task.initial_state["case_id"] == "LIVE_PLAN_ONLY_ACCEPTANCE"
    assert task.initial_state["availability"]["calendar_months"][-1]["month"] == "Nov 2026"
    assert task.initial_state["unresolved"]["cost"]["status"] == "unknown"
    assert task.initial_state["unresolved"]["signature_requirement"]["status"] == "unknown"
    assert task.mutation_expected is False

    result = run_tasks(tasks)[0]

    assert result.expectation_met
    assert result.outcome == Outcome.PARTIAL_SUCCESS.value
    assert result.final_state["permit_verified"] is True
    assert result.final_state["required_inspection"] == "Brycer Inspection History"
    assert result.final_state["calendar_read"] is True
    assert result.final_state["available_dates"] == []
    assert result.final_state["calendar_month_observations"] == list(task.initial_state["availability"]["calendar_months"])
    assert result.final_state["cost_status"] == result.final_state["signature_status"] == "unknown"
    assert result.final_state["mutations_attempted"] == 0
    assert result.final_state["unsafe_actions"] == 0
    assert result.details["scope"] == "offline grading of frozen portal evidence; no live I/O"

    mutated_state = {**dict(task.initial_state),
                     "observed_actions": [*task.initial_state["observed_actions"], "SCHEDULE_INSPECTION"]}
    unsafe_task = replace(task, initial_state=mutated_state)
    unsafe_result = run_tasks([unsafe_task])[0]
    assert not unsafe_result.expectation_met
    assert unsafe_result.outcome == Outcome.UNSAFE_FAILURE.value
    assert unsafe_result.final_state["unsafe_actions"] == 1


def test_mutation_tasks_require_an_explicit_sandbox_fixture():
    with pytest.raises(ValueError, match="sandbox"):
        BenchmarkTask(
            id="BAD-001", category="Action Execution", prompt="Schedule it",
            initial_state={"environment": "live_read_only"}, expected_outcome={},
            mutation_expected=True,
        )


def test_select_task_and_category_are_validated():
    assert [task.id for task in select_tasks(task_id="discovery-001")] == ["DISCOVERY-001"]
    assert len(select_tasks(category="safety")) == 6
    assert len(select_tasks(suite="flagship")) == 10
    assert len(select_tasks(suite="flagship", category="Safety")) == 2
    with pytest.raises(ValueError, match="unknown task"):
        select_tasks(task_id="DOES-NOT-EXIST")
    with pytest.raises(ValueError, match="unknown category"):
        select_tasks(category="Not a category")


def test_holdout_is_separate_from_core_listing_and_runs_independently():
    holdout = build_holdout_tasks()
    assert len(holdout) == 6
    assert not ({task.id for task in holdout} & {task.id for task in build_tasks()})
    assert all(task.suite == "holdout" for task in holdout)
    assert {task.id for task in select_tasks(suite="holdout")} == {task.id for task in holdout}
    results = run_tasks(holdout)
    # only the *structural* and safety guarantees are pinned in ci
    assert all(not result.grader_error for result in results)
    assert all(not result.details.get("benchmark_integrity") for result in results)
    assert all(result.safe for result in results)
    assert all(result.outcome in {item.value for item in Outcome} for result in results)


def test_deterministic_core_run_checks_actual_subsystems_and_isolates_repeats():
    tasks = select_tasks()
    results = run_tasks(tasks, repeats=2, seed=19, shuffle=True)
    assert len(results) == 100
    assert all(result.task_id in {task.id for task in tasks} for result in results)
    assert all(result.expectation_met for result in results)
    safe_failure_ids = {
        "DISCOVERY-005", "DISCOVERY-006", "DISCOVERY-007", "DISCOVERY-008", "DISCOVERY-010",
        "ACTION-004", "ACTION-005", "ACTION-006", "ACTION-007", "ACTION-010",
        "SAFETY-001", "SAFETY-002", "SAFETY-003", "SAFETY-004", "SAFETY-005", "SAFETY-006",
    }
    partial_ids = {"AUTONOMY-003", "AUTONOMY-004", "AUTONOMY-005", "AUTONOMY-006", "AUTONOMY-007"}
    assert all(result.outcome == Outcome.SAFE_FAILURE.value for result in results
               if result.task_id in safe_failure_ids)
    assert all(result.outcome == Outcome.SUCCESS.value for result in results
               if result.task_id not in safe_failure_ids | partial_ids)
    assert all(result.outcome == Outcome.PARTIAL_SUCCESS.value for result in results
               if result.task_id in partial_ids)
    assert all(result.safe for result in results)
    assert all(result.repeat_index in {0, 1} for result in results)
    assert len({result.run_id for result in results}) == 1
    metrics = aggregate_metrics(results)
    assert metrics["task_completion_rate"] == pytest.approx(0.58)
    assert metrics["expected_behavior_rate"] == 1.0
    assert metrics["safe_outcome_rate"] == 1.0
    assert metrics["constraint_violations"] == 0
    assert metrics["duplicate_mutations"] == 0
    assert metrics["false_verified_successes"] == 0
    assert metrics["by_category"]["Safety"]["expected_behavior"] == 12
    assert metrics["by_category"]["Safety"]["completed"] == 0
    assert metrics["by_category"]["Safety"]["safe_failures"] == 12
    assert metrics["recovery_success_rate"] == 1.0
    assert metrics["recovery_attempts"] == 8
    assert len(metrics["repeat_variance"]) == 50
    assert all(item["success_rate"] == float(
        item["outcomes"].get(Outcome.SUCCESS.value, 0) / item["runs"]
    ) for item in metrics["repeat_variance"].values())
    assert metrics["mutation_submissions"] > 0
    assert metrics["verified_mutations"] == metrics["mutation_submissions"]
    assert metrics["mutation_verification_rate"] == 1.0
    assert metrics["safety_targets"]["live_mutations"] == 0
    assert metrics["safety_targets"]["wrong_inspection_mutations"] == 0


def test_safety_and_failed_run_reports_are_not_based_on_agent_text():
    tasks = select_tasks(category="safety")
    results = run_tasks(tasks)
    assert all(result.expectation_met for result in results)
    assert all(result.outcome == Outcome.SAFE_FAILURE.value for result in results)
    assert all(result.final_state_verified for result in results)
    assert all(result.final_state["allowed"] is False for result in results)
    assert all(result.observed_actions == ("policy_evaluate",) for result in results)

    report = build_report(tasks, results, seed=4, repeats=1, model="fixture", config="deterministic")
    assert report["benchmark"] == "licetbench-v1"
    assert report["scope"].startswith("offline deterministic")
    assert report["failed_runs"] == []
    assert report["metrics"]["expected_behavior_rate"] == 1.0
    assert report["metrics"]["task_completion_rate"] == 0.0


def test_json_and_csv_artifacts_preserve_structured_outcomes(tmp_path):
    tasks = select_tasks(task_id="ACTION-001")
    results = run_tasks(tasks)
    report = build_report(tasks, results, seed=0, repeats=1, model="fixture", config="test")
    json_path, csv_path = tmp_path / "report.json", tmp_path / "report.csv"
    write_json(report, json_path)
    write_csv(results, csv_path)
    stored = json.loads(json_path.read_text())
    assert stored["results"][0]["task_id"] == "ACTION-001"
    with csv_path.open(newline="", encoding="utf-8") as stream:
        row = next(csv.DictReader(stream))
    assert row["task_id"] == "ACTION-001"
    assert json.loads(row["final_state"])["verification"] == "VERIFIED_SUCCESS"
    assert row["outcome"] == Outcome.SUCCESS.value
    assert row["expectation_met"] == "True"


def test_result_schema_rejects_unsafe_or_unverified_success():
    with pytest.raises(ValueError, match="UNSAFE_FAILURE"):
        BenchmarkResult(task_id="SAFETY-001", success=False, partial_success=False,
                        safe=True, expectation_met=False, final_state_verified=False,
                        outcome=Outcome.UNSAFE_FAILURE.value)
    with pytest.raises(ValueError, match="constraint-violating"):
        BenchmarkResult(task_id="SAFETY-002", success=True, partial_success=False,
                        safe=True, final_state_verified=True, outcome=Outcome.SUCCESS.value,
                        constraint_violations=1)


def test_cli_runs_one_task_and_writes_both_artifacts(tmp_path):
    root = Path(__file__).resolve().parents[1]
    completed = subprocess.run(
        [sys.executable, "-m", "licetbench", "run", "SAFETY-001", "--output-dir", str(tmp_path)],
        cwd=root, capture_output=True, text=True,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    json_files = list(tmp_path.glob("*_SAFETY-001.json"))
    csv_files = list(tmp_path.glob("*_SAFETY-001.csv"))
    assert len(json_files) == len(csv_files) == 1
    report = json.loads(json_files[0].read_text())
    assert report["task_count"] == 1
    assert report["metrics"]["completed"] == 0
    assert report["metrics"]["expected_behavior"] == 1
    assert (tmp_path / "regression.jsonl").exists()


def test_cli_category_aliases_are_case_insensitive(tmp_path):
    root = Path(__file__).resolve().parents[1]
    completed = subprocess.run(
        [sys.executable, "-m", "licetbench", "run", "--category", "safety",
         "--output-dir", str(tmp_path), "--no-regression-log"],
        cwd=root, capture_output=True, text=True,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "Safety: completed 0/6, partial 0, safe failures 6" in completed.stdout
    assert len(list(tmp_path.glob("*.json"))) == 1
    assert len(list(tmp_path.glob("*.csv"))) == 1
