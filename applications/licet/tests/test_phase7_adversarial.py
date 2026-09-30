"""phase 7 adversarial recovery regressions adversarial review"""

from __future__ import annotations

import asyncio

from licet.browser.errors import BrowserError, ToolError
from licet.phase7 import (
    Failure,
    FailureType,
    LoopDetector,
    PageFingerprint,
    ProgressKind,
    RecoveryBudgets,
    RecoveryController,
    TimeoutType,
    classify_failure,
)


def run(coro):
    return asyncio.run(coro)


def broken(*_args):
    raise RuntimeError("still unavailable")


def test_recovery_without_an_action_cannot_claim_success():
    controller = RecoveryController()
    failure = controller.classify("element was detached", operation="click")
    assert failure.recoverable

    result = run(controller.recover(failure, "re-observe and retry", None))

    assert not result.recovered
    assert result.strategy == "NO_RECOVERY_ACTION"
    assert controller.stats.recovery_successes == 0
    assert controller.stats.false_recovery_blocked == 1
    assert any(item["event"] == "FALSE_RECOVERY_BLOCKED" for item in controller.report()["trace"])


def test_a_recovery_is_not_successful_unless_its_validation_passes():
    controller = RecoveryController(budgets=RecoveryBudgets(max_browser_retries=2, max_recovery_actions=2))
    failure = controller.classify("element was detached", operation="click")
    calls = []

    result = run(controller.recover(
        failure, "re-observe", lambda: calls.append("read") or "wrong page",
        validate=lambda value: value == "expected page",
    ))

    assert not result.recovered
    assert result.attempts == 2
    assert len(calls) == 2
    assert "not known-good" in result.error


def test_a_declared_mutation_timeout_is_reconciled_never_retried():
    controller = RecoveryController()
    failure = controller.classify(
        ToolError(BrowserError.TIMEOUT, "navigation timeout while submitting inspection"),
        operation="submit_inspection", mutation=True,
    )
    assert failure.failure_type is FailureType.MUTATION
    assert not failure.recoverable

    called = []
    result = run(controller.recover(failure, "resubmit", lambda: called.append(1) or True))

    assert not result.recovered
    assert result.strategy == "RECONCILE_MUTATION_STATE"
    assert called == []


def test_a_forgotten_mutation_flag_still_fails_safe():
    """the controller cannot trust the caller to set ``mutation=true``"""
    controller = RecoveryController()
    failure = controller.classify(
        TimeoutError("navigation timeout while submitting inspection"),
        operation="SCHEDULE_INSPECTION",
    )

    assert not failure.recoverable
    called = []
    result = run(controller.recover(failure, "re-observe", lambda: called.append(1) or True))
    assert not result.recovered
    assert result.strategy == "STOP"
    assert called == []


def test_the_word_mutation_alone_is_a_terminal_signal():
    failure = classify_failure("page timeout during mutation submit", operation="click")
    assert not failure.recoverable


def test_scenario_9_timeout_then_verified_success_never_resubmits():
    """schedule submitted → timeout → re read shows it succeeded: no replay"""
    controller = RecoveryController()
    key = "permit:P-1:rough:2026-10-01"
    assert controller.mutation_started(key)

    assert controller.mutation_reconciled(key, occurred=True) is False
    assert not controller.mutation_started(key)
    assert controller.stats.duplicate_mutation_attempts == 1
    assert controller.stats.mutation_reconciliations == 1


def test_scenario_10_proven_absent_mutation_may_be_retried_after_reconciliation():
    """schedule submitted → timeout → re read shows nothing: a bounded retry is safe"""
    controller = RecoveryController()
    key = "permit:P-1:rough:2026-10-01"
    assert controller.mutation_started(key)

    # re reading proves the mutation did not occur, which releases the key
    assert controller.mutation_reconciled(key, occurred=False) is True
    assert controller.mutation_started(key)
    assert controller.stats.duplicate_mutation_attempts == 0


def test_reconciling_a_key_that_was_never_reserved_is_not_a_retry_licence():
    controller = RecoveryController()
    assert controller.mutation_reconciled("never:seen", occurred=False) is False
    assert controller.mutation_started("never:seen")


def test_a_mutation_failure_object_is_reconciled_even_without_the_flag():
    controller = RecoveryController()
    direct = Failure(FailureType.MUTATION, "submit timed out", operation="submit")
    result = run(controller.recover(direct, "retry", lambda: True))
    assert not result.recovered
    assert result.strategy == "RECONCILE_MUTATION_STATE"


def test_the_global_recovery_action_budget_is_a_hard_ceiling():
    controller = RecoveryController(
        budgets=RecoveryBudgets(max_browser_retries=2, max_recovery_actions=4)
    )
    # distinct operations so the per sequence cap does not bind first; each sequence burns its two
    # attempts failing
    for index in range(5):
        failure = controller.classify("element was detached", operation=f"click_{index}")
        run(controller.recover(failure, "re-observe", broken, validate=lambda value: value is True))

    # pre review, the guard was checked once per call and the inner loop could overshoot the ceiling; the
    # budget is now a hard bound
    assert controller.stats.recovery_actions == 4
    assert any(item["event"] == "RECOVERY" and item["result"]["strategy"] == "STOP"
               for item in controller.report()["trace"])


def test_renaming_the_strategy_cannot_mint_a_fresh_retry_budget():
    controller = RecoveryController(
        budgets=RecoveryBudgets(max_browser_retries=2, max_recovery_actions=100)
    )
    outcomes = []
    for strategy in ("re-observe", "go back", "reload section"):
        failure = controller.classify("element was detached", operation="click")
        outcomes.append(run(controller.recover(failure, strategy, lambda: True, validate=lambda value: value is True)))

    assert [outcome.recovered for outcome in outcomes] == [True, True, False]
    assert outcomes[-1].strategy == "STOP"


def test_the_search_reformulation_budget_is_per_run_not_per_query():
    controller = RecoveryController(budgets=RecoveryBudgets(max_search_reformulations=2))
    assert controller.allow_search_reformulation("123 Main St, 00000")
    assert controller.allow_search_reformulation("123 Main St")
    assert not controller.allow_search_reformulation("123 Main Street")
    assert controller.stats.search_reformulations == 2


def test_cascading_recovery_for_recovery_cannot_exceed_the_run_budget():
    controller = RecoveryController(
        budgets=RecoveryBudgets(max_browser_retries=3, max_recovery_actions=3)
    )
    for index in range(10):
        failure = controller.classify("navigation timeout", operation=f"navigate_{index}")
        run(controller.recover(failure, "return to known-good state", broken, validate=lambda value: value is True))
    assert controller.stats.recovery_actions <= 3


def test_two_anonymous_fingerprints_are_not_the_same_page():
    blank = PageFingerprint()
    other = PageFingerprint()
    assert blank.matches(other)
    # identity bearing comparison refuses to call two unknown pages identical
    assert not blank.matches(other, require_identity=True)


def test_a_checkpoint_without_a_fingerprint_is_never_reusable():
    controller = RecoveryController()
    controller.checkpoint("permit_verified", {"record": "P-1"})
    assert not controller.validate_checkpoint(
        "permit_verified", PageFingerprint(url="/record", record_number="P-1")
    )


def test_a_checkpoint_with_anonymous_identity_is_never_reusable():
    controller = RecoveryController()
    controller.checkpoint("permit_verified", {"record": "P-1"}, PageFingerprint())
    assert not controller.validate_checkpoint(
        "permit_verified", PageFingerprint(url="/record", record_number="P-1")
    )


def test_checkpoint_validation_requires_the_current_page_to_be_supplied():
    controller = RecoveryController()
    fingerprint = PageFingerprint(url="/record", record_number="P-1", active_section="Overview")
    controller.checkpoint("permit_verified", {"record": "P-1"}, fingerprint)
    # no fingerprint supplied means the portal was not re read; refuse to trust
    assert not controller.validate_checkpoint("permit_verified")


def test_two_consecutive_no_progress_signals_a_forced_replan():
    controller = RecoveryController(budgets=RecoveryBudgets(max_no_progress=2))
    assert controller.progress_update(information={"status"}, state="s1") is ProgressKind.NEW_INFORMATION
    assert controller.progress_update(information={"status"}, state="s1") is ProgressKind.NO_PROGRESS
    assert not controller.replan_required()
    assert controller.progress_update(information={"status"}, state="s1") is ProgressKind.NO_PROGRESS
    assert controller.replan_required()
    assert any(item["event"] == "REPLAN_REQUIRED" for item in controller.report()["trace"])


def test_an_oscillating_state_triggers_the_no_progress_counter():
    """revisiting a semantic state consumes the no progress budget"""
    controller = RecoveryController(budgets=RecoveryBudgets(max_no_progress=2))
    controller.progress_update(information={"status"}, state="A")
    assert controller.progress_update(information={"status"}, state="B") is ProgressKind.STATE_CHANGED
    for state in ("A", "B", "A", "B"):
        assert controller.progress_update(information={"status"}, state=state) is ProgressKind.NO_PROGRESS
    assert controller.replan_required()

    detector = LoopDetector(limit=3)
    observations = [
        detector.observe("READ_INSPECTIONS", page, "P-1")
        for page in ("A", "B", "A", "B", "A")
    ]
    assert observations == [False, False, False, False, True]


def test_a_transient_page_state_string_defeats_loop_detection():
    """residual, named: the caller must pass a *stable* page state"""
    detector = LoopDetector(limit=3)
    for tick in range(6):
        assert not detector.observe("READ_INSPECTIONS", f"record/inspections@{tick}", "P-1")


def test_the_full_timeout_taxonomy_is_reachable():
    element = classify_failure(
        ToolError(BrowserError.TIMEOUT, "Timeout waiting for element"), operation="click"
    )
    assert element.failure_type is FailureType.BROWSER
    assert element.timeout_type is TimeoutType.ELEMENT_TIMEOUT

    page = classify_failure(TimeoutError("navigation timeout"), operation="navigate")
    assert page.failure_type is FailureType.NAVIGATION
    assert page.timeout_type is TimeoutType.PAGE_LOAD_TIMEOUT

    ajax = classify_failure("ajax loading never finished", operation="read")
    assert ajax.failure_type is FailureType.PORTAL
    assert ajax.timeout_type is TimeoutType.AJAX_TIMEOUT

    verification = classify_failure("mutation_verification_timeout after submit", operation="verify")
    assert verification.failure_type is FailureType.MUTATION
    assert verification.timeout_type is TimeoutType.MUTATION_VERIFICATION_TIMEOUT

    session = classify_failure("session expired", operation="read")
    assert session.failure_type is FailureType.PORTAL
    assert session.timeout_type is TimeoutType.SESSION_TIMEOUT


def test_terminal_stops_do_not_inflate_the_recovery_attempt_rate():
    controller = RecoveryController()
    policy = controller.classify("live mutation blocked by policy", operation="schedule")
    result = run(controller.recover(policy, "retry"))
    assert result.strategy == "STOP"
    # a stop is not a recovery attempt; counting it would corrupt the success rate
    assert controller.stats.recovery_attempts == 0
    assert controller.stats.unrecoverable_failures == 1
    assert controller.stats.recovery_success_rate == 0.0
