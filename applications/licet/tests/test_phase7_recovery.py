from __future__ import annotations

import asyncio

from licet.browser.errors import BrowserError, ToolError
from licet.phase7 import (
    FailureType,
    LoopDetector,
    PageFingerprint,
    ProgressKind,
    RecoveryBudgets,
    RecoveryController,
    RecoveryResult,
    TimeoutType,
    classify_failure,
)


def test_failure_categories_and_timeout_taxonomy():
    failure = classify_failure(
        ToolError(BrowserError.TIMEOUT, "Timeout waiting for element"), operation="click"
    )
    assert failure.failure_type is FailureType.BROWSER
    assert failure.recoverable

    session = classify_failure(
        ToolError(BrowserError.SESSION_TIMEOUT, "session has expired"), operation="read"
    )
    assert session.failure_type is FailureType.PORTAL
    assert session.timeout_type is TimeoutType.SESSION_TIMEOUT
    assert not session.recoverable


def test_terminal_policy_and_mutation_failures_are_not_retried():
    controller = RecoveryController()
    policy = controller.classify("live mutation blocked by policy", operation="schedule")
    result = asyncio.run(controller.recover(policy, "retry"))
    assert not result.recovered
    assert result.strategy == "STOP"

    mutation = controller.classify(
        "submit timed out", operation="submit_inspection", mutation=True
    )
    result = asyncio.run(controller.recover(mutation, "submit again"))
    assert not result.recovered
    assert result.strategy == "RECONCILE_MUTATION_STATE"
    assert "reconciled" in result.error


def test_browser_recovery_is_bounded_and_traced():
    controller = RecoveryController(
        budgets=RecoveryBudgets(max_browser_retries=2, max_recovery_actions=2)
    )
    failure = controller.classify("element was detached from the DOM", operation="click")
    calls = []

    result = asyncio.run(controller.recover(
        failure, "re-observe and retry", lambda: calls.append("read") or True,
        new_state="inspections", validate=lambda value: value is True,
    ))
    assert result == RecoveryResult(True, "re-observe and retry", 1, "inspections")
    assert calls == ["read"]
    assert controller.trace[-1]["event"] == "RECOVERY"


def test_recovery_action_failure_cannot_loop_forever():
    controller = RecoveryController(
        budgets=RecoveryBudgets(max_browser_retries=2, max_recovery_actions=2)
    )
    failure = controller.classify("navigation timeout", operation="navigate")
    calls = []

    def broken():
        calls.append(1)
        raise RuntimeError("still unavailable")

    result = asyncio.run(controller.recover(failure, "navigate to known-good state", broken, validate=lambda value: value is True))
    assert not result.recovered
    assert result.attempts == 2
    assert len(calls) == 2


def test_page_fingerprint_detects_wrong_record_and_section():
    expected = PageFingerprint(
        url="/CapDetail.aspx", record_number="P-1", page_title="Record",
        active_section="Inspections", important_visible_text="Failed Rough",
    )
    assert expected.matches(expected)
    assert not expected.matches(PageFingerprint(
        url="/CapDetail.aspx", record_number="P-2", page_title="Record",
        active_section="Inspections", important_visible_text="Failed Rough",
    ))
    assert not expected.matches(PageFingerprint(
        url="/CapDetail.aspx", record_number="P-1", page_title="Record",
        active_section="Search", important_visible_text="Failed Rough",
    ))


def test_checkpoint_is_invalidated_when_portal_state_changes():
    controller = RecoveryController()
    expected = PageFingerprint(url="/record", record_number="P-1", active_section="Overview")
    controller.checkpoint("permit_verified", {"record": "P-1"}, expected)
    assert controller.validate_checkpoint("permit_verified", expected)
    assert not controller.validate_checkpoint(
        "permit_verified", PageFingerprint(url="/home", record_number=None, active_section="Home")
    )
    assert not controller.checkpoints["permit_verified"].valid


def test_loop_detector_uses_action_page_and_permit_state():
    detector = LoopDetector(limit=3)
    assert not detector.observe("READ_INSPECTIONS", "record/inspections", "P-1")
    assert not detector.observe("READ_INSPECTIONS", "record/inspections", "P-1")
    assert detector.observe("READ_INSPECTIONS", "record/inspections", "P-1")
    # a changed page or permit is not the same loop
    assert not detector.observe("READ_INSPECTIONS", "record/overview", "P-1")
    assert not detector.observe("READ_INSPECTIONS", "record/inspections", "P-2")


def test_progress_scoring_and_no_progress_counter():
    controller = RecoveryController()
    assert controller.progress_update(information={"status"}, state="s1") is ProgressKind.NEW_INFORMATION
    assert controller.progress_update(information={"status"}, state="s1") is ProgressKind.NO_PROGRESS
    assert controller.progress.consecutive_no_progress == 1
    assert controller.progress_update(information={"status"}, state="s2") is ProgressKind.STATE_CHANGED
    assert controller.progress.consecutive_no_progress == 0
    assert controller.progress_update(information={"status"}, state="s2", goal={"blockers"}) is ProgressKind.GOAL_PROGRESS


def test_search_and_replan_budgets_are_explicit():
    controller = RecoveryController(budgets=RecoveryBudgets(max_search_reformulations=2, max_replans=1))
    assert controller.allow_search_reformulation("123 Main")
    assert controller.allow_search_reformulation("123 Main")
    assert not controller.allow_search_reformulation("123 Main")
    assert controller.allow_replan(succeeded=True)
    assert not controller.allow_replan()
    assert controller.report()["stats"]["replan_success_rate"] == 1.0


def test_mutation_reservation_is_idempotent_and_stats_are_reported():
    controller = RecoveryController()
    assert controller.mutation_started("permit:P-1:rough:2026-10-01")
    assert not controller.mutation_started("permit:P-1:rough:2026-10-01")
    report = controller.report()
    assert report["stats"]["duplicate_mutation_attempts"] == 1
    assert any(item["event"] == "MUTATION_BLOCKED" for item in report["trace"])
