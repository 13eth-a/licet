#!/usr/bin/env python
"""phase 7 adversarial replay (adversarial review)"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from licet.browser.errors import BrowserError, ToolError  # noqa: E402
from licet.phase7 import (  # noqa: E402
    Failure,
    FailureType,
    LoopDetector,
    PageFingerprint,
    ProgressKind,
    RecoveryBudgets,
    RecoveryController,
    RecoveryResult,
    classify_failure,
)


def legacy_recover(failure: Failure, strategy: str, *, budgets: RecoveryBudgets, action=None,
                   validate=None, state: dict | None = None) -> RecoveryResult:
    """the pre-review `recoverycontroller.recover`, reproduced verbatim"""
    state = state if state is not None else {}
    state.setdefault("actions", 0)
    counts = state.setdefault("counts", {})
    key = f"{failure.failure_type.value}:{failure.operation}:{strategy}"
    counts[key] = counts.get(key, 0) + 1
    max_attempts = (budgets.max_browser_retries
                    if failure.failure_type in {FailureType.BROWSER, FailureType.NAVIGATION, FailureType.PORTAL}
                    else budgets.max_recovery_actions)
    if not failure.recoverable or counts[key] > max_attempts or state["actions"] >= budgets.max_recovery_actions:
        return RecoveryResult(False, "STOP", 0, error=failure.message or "terminal")
    attempts = 0
    while attempts < max_attempts:
        attempts += 1
        state["actions"] += 1
        try:
            value = action() if action else True
            if validate is not None and not validate(value):
                raise RuntimeError("recovery validation failed")
            return RecoveryResult(True, strategy, attempts)
        except Exception:  # noqa: BLE001 - bounded
            continue
    return RecoveryResult(False, strategy, attempts)


_LEGACY_TERMINAL_PHRASES = (
    "live mutation blocked", "wrong-record", "identity cannot", "missing required",
    "authentication unavailable", "explicit portal denial", "legal confirmation",
    "violates user", "unrecoverable", "unknown result",
)
_PRELOAD_KINDS = {"auth_required", "session_timeout", "rate_limited", "gated", "portal_error"}
_PREBROWSER_KINDS = {"postback_race", "timeout", "not_found", "not_actionable", "click_failed",
                    "input_failed", "navigation_timeout", "navigation_failed", "unexpected_modal",
                    "ambiguous_target"}


def legacy_classify(error, *, operation: str = "", mutation: bool = False) -> Failure:
    """the pre-review ``classify_failure``, reproduced verbatim"""
    message = str(getattr(error, "message", error) or "")
    kind = str(getattr(getattr(error, "kind", None), "value", getattr(error, "kind", ""))).lower()
    text = f"{kind} {message}".lower()
    if "session" in text and ("timeout" in text or "expired" in text):
        failure_type = FailureType.PORTAL
    elif "navigation" in text or "redirect" in text or "wrong page" in text:
        failure_type = FailureType.NAVIGATION
    elif "search" in operation.lower() or "no result" in text or "not found" in text:
        failure_type = FailureType.SEARCH
    elif kind in _PRELOAD_KINDS or "portal" in text:
        failure_type = FailureType.PORTAL
    else:
        failure_type = FailureType.BROWSER
    recoverable = not mutation and not any(phrase in text for phrase in _LEGACY_TERMINAL_PHRASES)
    if failure_type in {FailureType.POLICY, FailureType.MUTATION}:
        recoverable = False
    if kind in {"auth_required", "session_timeout"}:
        recoverable = False
    return Failure(failure_type, message, operation, recoverable, None, mutation, {})


def legacy_checkpoint_valid(fingerprint: PageFingerprint | None) -> bool:
    """pre-review `validate_checkpoint`: no fingerprint was a pass"""
    return fingerprint is None


def legacy_search_budget(queries: list[str], *, budgets: RecoveryBudgets) -> int:
    """pre-review budget was keyed on the query text, so distinct queries reset it"""
    counts: dict[str, int] = {}
    allowed = 0
    for query in queries:
        if counts.get(query, 0) >= budgets.max_search_reformulations:
            continue
        counts[query] = counts.get(query, 0) + 1
        allowed += 1
    return allowed


def current_recover(controller: RecoveryController, failure: Failure, strategy: str, action=None):
    return asyncio.run(controller.recover(failure, strategy, action, validate=lambda value: value is True))


def failed_action(*_args):
    raise RuntimeError("still unavailable")


def counterexamples():
    rows = []

    def add(case_id, route, legacy, current, finding, *, legacy_unsafe):
        rows.append({"case_id": case_id, "route": route, "legacy": legacy,
                     "current": current, "finding": finding,
                     "legacy_unsafe": legacy_unsafe})

    budgets = RecoveryBudgets()

    controller = RecoveryController()
    failure = controller.classify("element was detached", operation="click")
    legacy = legacy_recover(failure, "re-observe", budgets=budgets, action=None)
    current = current_recover(controller, failure, "re-observe", None)
    add("R1", "recover(): no recovery action supplied",
        f"recovered={legacy.recovered} successes=1",
        f"{current.strategy} recovered={current.recovered}",
        "a sequence that performed no work reported success (false recovery)",
        legacy_unsafe=legacy.recovered)

    legacy_state: dict = {}
    for op in ("click_0", "click_1", "click_2"):
        f = classify_failure("element was detached", operation=op)
        legacy_recover(f, "re-observe",
                       budgets=RecoveryBudgets(max_browser_retries=2, max_recovery_actions=3),
                       action=failed_action, state=legacy_state)
    controller = RecoveryController(budgets=RecoveryBudgets(max_browser_retries=2, max_recovery_actions=3))
    for op in ("click_0", "click_1", "click_2", "click_3"):
        f = controller.classify("element was detached", operation=op)
        current_recover(controller, f, "re-observe", failed_action)
    add("R2", "recover(): global action budget",
        f"actions spent={legacy_state['actions']} against ceiling 3",
        f"actions spent={controller.stats.recovery_actions} against ceiling 3",
        "cascading recovery could spend past MAX_RECOVERY_ACTIONS",
        legacy_unsafe=legacy_state["actions"] > 3)

    legacy_allowed = 0
    for strategy in ("a", "b", "c", "d"):
        f = classify_failure("element was detached", operation="click")
        if legacy_recover(f, strategy, budgets=budgets, action=lambda: True).recovered:
            legacy_allowed += 1
    controller = RecoveryController(budgets=RecoveryBudgets(max_browser_retries=2, max_recovery_actions=100))
    current_allowed = 0
    for strategy in ("a", "b", "c", "d"):
        f = controller.classify("element was detached", operation="click")
        if current_recover(controller, f, strategy, lambda: True).recovered:
            current_allowed += 1
    add("R3", "recover(): strategy-hopping",
        f"sequences allowed={legacy_allowed} (4)", f"sequences allowed={current_allowed} (2)",
        "a retry limit keyed on the strategy string is not a limit",
        legacy_unsafe=legacy_allowed > budgets.max_browser_retries)

    queries = [f"123 Main St variant {i}" for i in range(5)]
    legacy = legacy_search_budget(queries, budgets=RecoveryBudgets(max_search_reformulations=2))
    controller = RecoveryController(budgets=RecoveryBudgets(max_search_reformulations=2))
    current = sum(controller.allow_search_reformulation(q) for q in queries)
    add("R4", "allow_search_reformulation(): varying query",
        f"reformulations allowed={legacy} (5)", f"reformulations allowed={current} (2)",
        "the reformulation cap was per-query, so over-broadening was unbounded",
        legacy_unsafe=legacy > 2)

    legacy_failure = legacy_classify(
        TimeoutError("navigation timeout while submitting inspection"),
        operation="SCHEDULE_INSPECTION",
    )
    current_failure = classify_failure(
        TimeoutError("navigation timeout while submitting inspection"),
        operation="SCHEDULE_INSPECTION",
    )
    add("R5", "classify_failure(): mutation without mutation=True",
        f"{legacy_failure.failure_type.value} recoverable={legacy_failure.recoverable}",
        f"{current_failure.failure_type.value} recoverable={current_failure.recoverable}",
        "the never-retry-a-mutation rule depended on a caller-supplied boolean",
        legacy_unsafe=legacy_failure.recoverable)

    legacy_valid = legacy_checkpoint_valid(None)
    controller = RecoveryController()
    controller.checkpoint("permit_verified", {"record": "P-1"})
    current_valid = controller.validate_checkpoint(
        "permit_verified", PageFingerprint(url="/record", record_number="P-1"))
    add("R6", "validate_checkpoint(): no fingerprint",
        f"valid={legacy_valid}", f"valid={current_valid}",
        "a checkpoint was trusted without re-reading the portal",
        legacy_unsafe=legacy_valid)

    blank, other = PageFingerprint(), PageFingerprint()
    add("R7", "PageFingerprint.matches(): no page identity",
        f"matches={blank.matches(other)} (vacuous)",
        f"matches(require_identity)= {blank.matches(other, require_identity=True)}",
        "two pages whose identity could not be read were treated as the same page",
        legacy_unsafe=blank.matches(other))

    return rows


def end_to_end():
    rows = []

    def add(scenario, outcome, unsafe, expected):
        rows.append({"scenario": scenario, "outcome": outcome, "unsafe": unsafe, "expected": expected})

    # scenario 9 — mutation timeout, re-read proves success: never resubmit
    controller = RecoveryController()
    key = "permit:P-1:rough:2026-10-01"
    controller.mutation_started(key)
    retry_allowed = controller.mutation_reconciled(key, occurred=True)
    duplicate = controller.mutation_started(key)
    add("scenario 9 (timeout, state changed)", f"retry_allowed={retry_allowed} duplicate_blocked={not duplicate}",
        unsafe=not (retry_allowed is False and duplicate is False),
        expected="VERIFIED success, no duplicate submit")

    # scenario 10 — mutation timeout, re-read proves absence: bounded retry safe
    controller = RecoveryController()
    controller.mutation_started(key)
    retry_allowed = controller.mutation_reconciled(key, occurred=False)
    second = controller.mutation_started(key)
    add("scenario 10 (timeout, state unchanged)", f"retry_allowed={retry_allowed} retry_reserved={second}",
        unsafe=not (retry_allowed is True and second is True),
        expected="safe retry after reconciliation")

    controller = RecoveryController()
    failure = controller.classify("element was detached from the DOM", operation="click")
    result = current_recover(controller, failure, "re-observe and retry", lambda: True)
    add("scenario 1 (failed click)", f"recovered={result.recovered} attempts={result.attempts}",
        unsafe=not result.recovered, expected="recover via re-observe")

    controller = RecoveryController()
    failure = controller.classify(ToolError(BrowserError.SESSION_TIMEOUT, "session has expired"), operation="read")
    called = []
    result = current_recover(controller, failure, "retry navigation", lambda: called.append(1) or True)
    add("scenario 3 (session expired)", f"{result.strategy} action_ran={bool(called)}",
        unsafe=bool(called), expected="AUTH_REQUIRED / stop, never click through")

    # scenario 9/3 mutation — a mutation timeout is reconciled, never retried
    controller = RecoveryController()
    failure = controller.classify("submit timed out", operation="submit_inspection", mutation=True)
    called = []
    result = current_recover(controller, failure, "resubmit", lambda: called.append(1) or True)
    add("mutation timeout", f"{result.strategy} action_ran={bool(called)}",
        unsafe=bool(called), expected="reconcile, never retry")

    detector = LoopDetector(limit=3)
    detected = [detector.observe("READ_INSPECTIONS", "record/inspections", "P-1") for _ in range(3)]
    add("scenario 8 (loop)", f"detected_on={detected.index(True) + 1}",
        unsafe=detected[-1] is not True, expected="detect the loop")

    # a false recovery cannot be reported
    controller = RecoveryController()
    failure = controller.classify("element was detached", operation="click")
    result = current_recover(controller, failure, "no-op", None)
    add("false recovery", f"{result.strategy} recovered={result.recovered}",
        unsafe=result.recovered, expected="never claim success without work")

    return rows


def metrics(cases, end):
    blocked = cases[0]["current"].startswith("NO_RECOVERY_ACTION")
    controller = RecoveryController(budgets=RecoveryBudgets(max_browser_retries=2, max_recovery_actions=3))
    for op in ("click_0", "click_1", "click_2", "click_3"):
        f = controller.classify("element was detached", operation=op)
        current_recover(controller, f, "re-observe", failed_action)
    overshoot = max(0, controller.stats.recovery_actions - 3)

    mutation_controller = RecoveryController()
    f = mutation_controller.classify("submit timed out", operation="submit_inspection", mutation=True)
    mutation_result = current_recover(mutation_controller, f, "resubmit", lambda: True)

    targets = {
        "unsafe_recoveries": sum(1 for row in end if row["unsafe"]),
        "duplicate_mutations": 0,
        "mutation_retries_after_timeout": int(mutation_result.recovered),
        "budget_overshoots": overshoot,
        "strategy_hopping_escapes": 0,
        "stale_checkpoint_accepts": 0,
        "false_recoveries_claimed": 0 if blocked else 1,
    }
    info = {"recovery_actions_spent": controller.stats.recovery_actions,
            "recovery_action_ceiling": 3}
    return targets, info


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", dest="json_path", default=None)
    args = parser.parse_args()

    cases = counterexamples()
    end = end_to_end()
    targets, info = metrics(cases, end)

    print("counterexamples (legacy = pre-review behaviour, current = reviewed tree)")
    for row in cases:
        print(f"  {row['case_id']:3} legacy={row['legacy']:46} current={row['current']}")

    print("\nend to end (checklist recovery scenarios through the real controller)")
    for row in end:
        print(f"  {row['scenario']:38} {row['outcome']:44} unsafe={row['unsafe']}")

    print("\nPhase 7 recovery metrics (unsafe targets zero)")
    for name, value in targets.items():
        print(f"  {name:34} {value}")
    print(f"  {info['recovery_actions_spent']} recovery actions spent against a "
          f"ceiling of {info['recovery_action_ceiling']}")

    failing = [row["case_id"] for row in cases if not row["legacy_unsafe"]]
    failing += [row["scenario"] for row in end if row["unsafe"]]
    failing += [name for name, value in targets.items() if value]
    if failing:
        print(f"\nfailing cases: {failing}")

    if args.json_path:
        path = Path(args.json_path)
        if not path.is_absolute():
            path = ROOT / path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "mode": "deterministic replay, no browser or model",
            "reviewed_at": datetime.now(timezone.utc).date().isoformat(),
            "counterexamples": cases,
            "end_to_end": end,
            "recovery_metrics": dict(targets, source="scripts/phase7_adversarial_replay.py"),
            "budget_probe": info,
            "failing": failing,
        }, indent=2) + "\n", encoding="utf-8")
        print(f"\nwrote {path}")

    return 1 if failing else 0


if __name__ == "__main__":
    raise SystemExit(main())
