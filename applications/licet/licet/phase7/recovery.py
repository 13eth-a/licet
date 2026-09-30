"""centralized, bounded recovery for browser and semantic execution"""

from __future__ import annotations

import asyncio
import inspect
import re
from collections import Counter, deque
from copy import deepcopy
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Awaitable, Callable, Mapping


class FailureType(StrEnum):
    BROWSER = "browser"
    NAVIGATION = "navigation"
    EXTRACTION = "extraction"
    SEARCH = "search"
    STATE = "state"
    PLANNER = "planner"
    POLICY = "policy"
    MUTATION = "mutation"
    PORTAL = "portal"


class TimeoutType(StrEnum):
    PAGE_LOAD_TIMEOUT = "PAGE_LOAD_TIMEOUT"
    ELEMENT_TIMEOUT = "ELEMENT_TIMEOUT"
    AJAX_TIMEOUT = "AJAX_TIMEOUT"
    MUTATION_VERIFICATION_TIMEOUT = "MUTATION_VERIFICATION_TIMEOUT"
    SESSION_TIMEOUT = "SESSION_TIMEOUT"


class ProgressKind(StrEnum):
    NEW_INFORMATION = "NEW_INFORMATION"
    STATE_CHANGED = "STATE_CHANGED"
    GOAL_PROGRESS = "GOAL_PROGRESS"
    NO_PROGRESS = "NO_PROGRESS"


@dataclass(frozen=True)
class RecoveryBudgets:
    max_browser_retries: int = 2
    max_search_reformulations: int = 3
    max_replans: int = 5
    max_recovery_actions: int = 8
    max_no_progress: int = 2
    backoff_base_seconds: float = 0.0
    action_timeout_seconds: float = 30.0
    max_mutation_retries: int = 1

    def __post_init__(self) -> None:
        if any(value < 0 for value in (
            self.max_browser_retries, self.max_search_reformulations,
            self.max_replans, self.max_recovery_actions, self.max_no_progress, self.max_mutation_retries,
        )) or self.backoff_base_seconds < 0 or self.action_timeout_seconds <= 0:
            raise ValueError("recovery budgets must be non-negative")


@dataclass(frozen=True)
class PageFingerprint:
    url: str | None = None
    record_number: str | None = None
    page_title: str | None = None
    active_section: str | None = None
    important_visible_text: str = ""

    @classmethod
    def from_observation(cls, observation: Mapping[str, Any] | None) -> "PageFingerprint":
        observation = observation or {}
        return cls(
            url=_first(observation, "url", "current_url"),
            record_number=_first(observation, "record_number", "permit_id", "current_permit"),
            page_title=_first(observation, "page_title", "title"),
            active_section=_first(observation, "active_section", "section", "flow_step"),
            important_visible_text=_normalize_text(str(observation.get("important_visible_text")
                                                     or observation.get("text") or "")),
        )

    def matches(self, other: "PageFingerprint", *, include_text: bool = True,
                require_identity: bool = False) -> bool:
        # two observations that both failed to establish *which* page this is (no url, no record number)
        # must not be treated as the same page: an all-none identity is missing evidence, not a match
        if require_identity:
            if self.url is None and self.record_number is None:
                return False
            if other.url is None and other.record_number is None:
                return False
        fields = ("url", "record_number", "page_title", "active_section")
        if any(getattr(self, field) != getattr(other, field) for field in fields):
            return False
        return not include_text or self.important_visible_text == other.important_visible_text

    def as_dict(self) -> dict[str, Any]:
        return {
            "url": self.url, "record_number": self.record_number,
            "page_title": self.page_title, "active_section": self.active_section,
            "important_visible_text": self.important_visible_text,
        }


@dataclass(frozen=True)
class StateConflict:
    field: str
    values: tuple[Any, ...]
    sources: tuple[str, ...]
    resolved: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {"field": self.field, "values": list(self.values),
                "sources": list(self.sources), "resolved": self.resolved}


@dataclass(frozen=True)
class Failure:
    failure_type: FailureType
    message: str
    operation: str = ""
    recoverable: bool = False
    timeout_type: TimeoutType | None = None
    mutation: bool = False
    evidence: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "type": self.failure_type.value, "message": self.message,
            "operation": self.operation, "recoverable": self.recoverable,
            "timeout_type": self.timeout_type.value if self.timeout_type else None,
            "mutation": self.mutation, "evidence": dict(self.evidence),
        }


@dataclass(frozen=True)
class RecoveryResult:
    recovered: bool
    strategy: str
    attempts: int = 0
    new_state: str | None = None
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "recovered": self.recovered, "strategy": self.strategy,
            "attempts": self.attempts, "new_state": self.new_state,
            "error": self.error,
        }


@dataclass(frozen=True)
class Checkpoint:
    name: str
    state: Mapping[str, Any]
    fingerprint: PageFingerprint | None = None
    valid: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "state": dict(self.state),
                "fingerprint": self.fingerprint.as_dict() if self.fingerprint else None,
                "valid": self.valid}


@dataclass
class RecoveryStats:
    failures: Counter[str] = field(default_factory=Counter)
    recovery_attempts: int = 0
    recovery_successes: int = 0
    browser_retries: int = 0
    browser_retry_successes: int = 0
    replans: int = 0
    replan_successes: int = 0
    search_reformulations: int = 0
    loop_detections: int = 0
    unrecoverable_failures: int = 0
    false_recoveries: int = 0
    false_recovery_blocked: int = 0
    mutation_reconciliations: int = 0
    duplicate_mutation_attempts: int = 0
    recovery_actions: int = 0
    additional_browser_actions: int = 0

    @property
    def recovery_success_rate(self) -> float:
        return self.recovery_successes / self.recovery_attempts if self.recovery_attempts else 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "failure_counts": dict(self.failures),
            "recovery_attempts": self.recovery_attempts,
            "recovery_successes": self.recovery_successes,
            "recovery_success_rate": self.recovery_success_rate,
            "browser_retries": self.browser_retries,
            "browser_retry_successes": self.browser_retry_successes,
            "browser_retry_success_rate": self.browser_retry_successes / self.browser_retries if self.browser_retries else None,
            "average_recovery_steps": self.recovery_actions / self.recovery_attempts if self.recovery_attempts else None,
            "replans": self.replans,
            "replan_successes": self.replan_successes,
            "replan_success_rate": self.replan_successes / self.replans if self.replans else 0.0,
            "search_reformulations": self.search_reformulations,
            "loop_detections": self.loop_detections,
            "unrecoverable_failures": self.unrecoverable_failures,
            "false_recoveries": self.false_recoveries,
            "false_recovery_blocked": self.false_recovery_blocked,
            "mutation_reconciliations": self.mutation_reconciliations,
            "duplicate_mutation_attempts": self.duplicate_mutation_attempts,
            "recovery_actions": self.recovery_actions,
            "additional_browser_actions": self.additional_browser_actions,
        }


@dataclass
class ProgressTracker:
    previous_information: frozenset[str] = frozenset()
    previous_state: str | None = None
    previous_goal: frozenset[str] = frozenset()
    consecutive_no_progress: int = 0
    seen_states: set[str] = field(default_factory=set)

    def update(self, *, information: set[str] | frozenset[str] = frozenset(),
               state: str | None = None, goal: set[str] | frozenset[str] = frozenset()) -> ProgressKind:
        information, goal = frozenset(information), frozenset(goal)
        new_information = bool(information - self.previous_information)
        state_changed = self.previous_state is not None and state != self.previous_state and state not in self.seen_states
        goal_progress = bool(goal - self.previous_goal)
        if goal_progress:
            result = ProgressKind.GOAL_PROGRESS
        elif new_information:
            result = ProgressKind.NEW_INFORMATION
        elif state_changed:
            result = ProgressKind.STATE_CHANGED
        else:
            result = ProgressKind.NO_PROGRESS
        if result is ProgressKind.NO_PROGRESS:
            self.consecutive_no_progress += 1
        else:
            self.consecutive_no_progress = 0
        self.previous_information |= information
        self.previous_goal |= goal
        self.previous_state = state
        if state is not None:
            self.seen_states.add(state)
        return result


@dataclass
class LoopDetector:
    limit: int = 3
    seen: Counter[str] = field(default_factory=Counter)

    def observe(self, semantic_action: str, page_state: str, permit_state: str) -> bool:
        key = "|".join((semantic_action, page_state, permit_state))
        self.seen[key] += 1
        return self.seen[key] >= self.limit

    def reset(self) -> None:
        self.seen.clear()


_BROWSER_KINDS = {"postback_race", "timeout", "not_found", "not_actionable",
                  "click_failed", "input_failed", "navigation_timeout", "navigation_failed",
                  "unexpected_modal", "ambiguous_target"}
_PORTAL_KINDS = {"auth_required", "session_timeout", "rate_limited", "gated", "portal_error"}
_TERMINAL_PHRASES = (
    "live mutation blocked", "wrong-record", "identity cannot", "missing required",
    "authentication unavailable", "explicit portal denial", "legal confirmation",
    "violates user", "unrecoverable", "unknown result", "switched records",
    "another record", "foreign record", "identity unverified", "record_identity_unverified",
    "auth_required", "live_mutation_blocked", "missing_required_input", "constraint_conflict",
)
_MUTATION_OPERATION_VERBS = (
    "submit", "schedule", "reschedule", "cancel", "pay", "payment",
    "purchase", "upload", "renew", "attest", "sign", "delete", "edit",
)


def _mutation_risk(*, mutation: bool, operation: str, text: str) -> bool:
    """true when a failure touches a state-changing operation"""
    if mutation:
        return True
    if any(verb in operation.lower() for verb in _MUTATION_OPERATION_VERBS):
        return True
    return "mutation" in text


def classify_failure(error: Any, *, operation: str = "", mutation: bool = False,
                     evidence: Mapping[str, Any] | None = None) -> Failure:
    """classify strings, toolerror-like objects, and semantic exceptions"""
    message = str(getattr(error, "message", error) or "")
    kind = str(getattr(getattr(error, "kind", None), "value", getattr(error, "kind", ""))).lower()
    text = f"{kind} {message}".lower()
    timeout = None
    if "session" in text and ("timeout" in text or "expired" in text):
        failure_type, timeout = FailureType.PORTAL, TimeoutType.SESSION_TIMEOUT
    elif "mutation_verification_timeout" in text:
        failure_type, timeout = FailureType.MUTATION, TimeoutType.MUTATION_VERIFICATION_TIMEOUT
    elif "ajax" in text or "loading" in text:
        failure_type, timeout = FailureType.PORTAL, TimeoutType.AJAX_TIMEOUT
    elif ("element" in text or kind in {"not_found", "not_actionable"}) and "timeout" in text:
        failure_type, timeout = FailureType.BROWSER, TimeoutType.ELEMENT_TIMEOUT
    elif "navigation" in text or "redirect" in text or "wrong page" in text:
        failure_type, timeout = FailureType.NAVIGATION, TimeoutType.PAGE_LOAD_TIMEOUT if "timeout" in text else None
    elif "search" in operation.lower() or "no result" in text or "not found" in text:
        failure_type = FailureType.SEARCH
    elif "extract" in operation.lower() or "parse" in text or "section" in text:
        failure_type = FailureType.EXTRACTION
    elif "planner" in operation.lower() or "precondition" in text or "loop" in text:
        failure_type = FailureType.PLANNER
    elif "policy" in operation.lower() or "blocked" in text or "denied" in text:
        failure_type = FailureType.POLICY
    elif "submit" in operation.lower() or "mutation" in text:
        failure_type = FailureType.MUTATION
    elif kind in _PORTAL_KINDS or "portal" in text or "service unavailable" in text:
        failure_type = FailureType.PORTAL
    elif kind in _BROWSER_KINDS or "element" in text or "browser" in text:
        failure_type = FailureType.BROWSER
    else:
        failure_type = FailureType.STATE
    if mutation:
        # the caller knows this is a mutation; the taxonomy must say so even when the message happened to
        # look like navigation or a search miss
        failure_type = FailureType.MUTATION
    risky = _mutation_risk(mutation=mutation, operation=operation, text=text)
    recoverable = not risky and not any(phrase in text for phrase in _TERMINAL_PHRASES)
    if failure_type in {FailureType.POLICY, FailureType.MUTATION}:
        recoverable = False
    if kind in {"auth_required", "session_timeout"} or timeout is TimeoutType.SESSION_TIMEOUT:
        recoverable = False
    return Failure(failure_type, message, operation, recoverable, timeout, mutation, evidence or {})


class RecoveryController:
    """own all recovery budgets, trace entries, checkpoints, and safe strategies"""

    def __init__(self, *, budgets: RecoveryBudgets | None = None) -> None:
        self.budgets = budgets or RecoveryBudgets()
        self.stats = RecoveryStats()
        self.trace: list[dict[str, Any]] = []
        self.checkpoints: dict[str, Checkpoint] = {}
        self.progress = ProgressTracker()
        self.loops = LoopDetector()
        self._counts: Counter[str] = Counter()
        self._mutation_keys: set[str] = set()
        self._mutation_retries: Counter[str] = Counter()

    def begin_run(self) -> None:
        """start isolated per-run budgets, observations and metrics"""
        self.stats = RecoveryStats()
        self.trace.clear()
        self.checkpoints.clear()
        self._mutation_retries.clear()
        self._counts.clear()
        self._mutation_keys.clear()
        self.loops.reset()
        self.progress = ProgressTracker()
        self.stats.recovery_actions = 0
        self.stats.replans = 0
        self.stats.search_reformulations = 0

    def classify(self, error: Any, **kwargs: Any) -> Failure:
        failure = classify_failure(error, **kwargs)
        self.stats.failures[failure.failure_type.value] += 1
        return failure

    def checkpoint(self, name: str, state: Mapping[str, Any],
                   fingerprint: PageFingerprint | None = None) -> Checkpoint:
        point = Checkpoint(name, deepcopy(dict(state)), fingerprint)
        self.checkpoints[name] = point
        self._trace("CHECKPOINT", checkpoint=point.as_dict())
        return point

    def validate_checkpoint(self, name: str, fingerprint: PageFingerprint | None = None) -> bool:
        """a checkpoint is reusable only with fresh, identity-bearing evidence"""
        point = self.checkpoints.get(name)
        stored = point.fingerprint if point else None
        identity_known = bool(stored and (stored.url or stored.record_number))
        valid = bool(
            point and point.valid and fingerprint is not None and identity_known
            and stored.matches(fingerprint, require_identity=True)
        )
        if point and not valid:
            self.checkpoints[name] = Checkpoint(point.name, point.state, point.fingerprint, False)
        return valid

    def allow_search_reformulation(self, query: str) -> bool:
        """cap reformulations per *run*, not per query string"""
        if self.stats.search_reformulations >= self.budgets.max_search_reformulations:
            self._trace("SEARCH_REFORMULATION_BLOCKED", query=query,
                        attempts=self.stats.search_reformulations)
            return False
        self.stats.search_reformulations += 1
        self._counts[f"search:{query}"] += 1
        self._trace("SEARCH_REFORMULATION", query=query,
                    attempt=self.stats.search_reformulations,
                    for_query=self._counts[f"search:{query}"])
        return True

    def allow_replan(self, *, succeeded: bool = False) -> bool:
        if self.stats.replans >= self.budgets.max_replans:
            self._trace("REPLAN_BLOCKED", reason="budget exhausted")
            return False
        self.stats.replans += 1
        self.stats.replan_successes += int(succeeded)
        self._trace("REPLAN", succeeded=succeeded, attempt=self.stats.replans)
        return True

    def loop_observed(self, semantic_action: str, page_state: str, permit_state: str) -> bool:
        detected = self.loops.observe(semantic_action, page_state, permit_state)
        if detected:
            self.stats.loop_detections += 1
            self._trace("PLAN_LOOP_DETECTED", semantic_action=semantic_action,
                        page_state=page_state, permit_state=permit_state)
        return detected

    def progress_update(self, **kwargs: Any) -> ProgressKind:
        result = self.progress.update(**kwargs)
        self._trace("PROGRESS", kind=result.value,
                    consecutive_no_progress=self.progress.consecutive_no_progress)
        if self.replan_required():
            self._trace("REPLAN_REQUIRED",
                        consecutive_no_progress=self.progress.consecutive_no_progress,
                        budget=self.budgets.max_no_progress)
        return result

    def replan_required(self) -> bool:
        """true when stalled progress has reached ``max_no_progress``"""
        return self.progress.consecutive_no_progress >= self.budgets.max_no_progress

    def mutation_started(self, operation_key: str) -> bool:
        """reserve a mutation key; false means a duplicate attempt was blocked"""
        if operation_key in self._mutation_keys:
            self.stats.duplicate_mutation_attempts += 1
            self._trace("MUTATION_BLOCKED", reason="duplicate mutation key", operation_key=operation_key)
            return False
        self._mutation_keys.add(operation_key)
        self._trace("MUTATION_RESERVED", operation_key=operation_key)
        return True

    def mutation_reconciled(self, operation_key: str, *, occurred: bool | None) -> bool:
        """reconcile a reserved mutation after re-reading the portal"""
        if occurred is not False:
            self.stats.mutation_reconciliations += 1
            self._trace("MUTATION_RECONCILED", operation_key=operation_key, occurred=occurred)
            return False
        if self._mutation_retries[operation_key] >= self.budgets.max_mutation_retries:
            return False
        if operation_key in self._mutation_keys:
            self._mutation_retries[operation_key] += 1
            self._mutation_keys.discard(operation_key)
            self.stats.mutation_reconciliations += 1
            self._trace("MUTATION_RECONCILED", operation_key=operation_key, occurred=False)
            return True
        self._trace("MUTATION_RECONCILED_UNKNOWN_KEY", operation_key=operation_key)
        return False

    async def recover(self, failure: Failure, strategy: str,
                      action: Callable[[], Any] | None = None,
                      *, new_state: str | None = None,
                      validate: Callable[[Any], bool] | None = None) -> RecoveryResult:
        """run at most one bounded, classified recovery sequence"""
        if failure.mutation or failure.failure_type is FailureType.MUTATION:
            result = RecoveryResult(False, "RECONCILE_MUTATION_STATE", 0,
                                    error="mutation outcome must be reconciled before any retry")
            self.stats.unrecoverable_failures += 1
            self._trace("RECOVERY", failure=failure.as_dict(), result=result.as_dict())
            return result
        browser_like = failure.failure_type in {
            FailureType.BROWSER, FailureType.NAVIGATION, FailureType.PORTAL
        }
        max_attempts = self.budgets.max_browser_retries if browser_like else self.budgets.max_recovery_actions
        # budget by (failure type, operation) rather than by strategy string, so renaming the recovery
        # path cannot mint a fresh allowance
        sequence_key = f"{failure.failure_type.value}:{failure.operation}"
        remaining = self.budgets.max_recovery_actions - self.stats.recovery_actions
        if not failure.recoverable or self._counts[sequence_key] >= max_attempts or remaining <= 0:
            result = RecoveryResult(False, "STOP", 0, error=failure.message or "failure is terminal or budget exhausted")
            self.stats.unrecoverable_failures += 1
            self._trace("RECOVERY", failure=failure.as_dict(), result=result.as_dict())
            return result
        if action is None or validate is None:
            # a recovery sequence with nothing to run cannot claim success; a missing action is a caller
            # bug, not a recovered failure
            result = RecoveryResult(False, "NO_RECOVERY_ACTION" if action is None else "NO_RECOVERY_VALIDATOR", 0,
                                    error="recovery requires an action and independent state validator")
            self.stats.false_recovery_blocked += 1
            self._trace("FALSE_RECOVERY_BLOCKED", failure=failure.as_dict(), result=result.as_dict())
            return result
        self.stats.recovery_attempts += 1
        # a single sequence may not spend past the run's global action budget
        max_attempts = min(max_attempts - self._counts[sequence_key], remaining)
        attempts = 0
        last_error = None
        while attempts < max_attempts and self.stats.recovery_actions < self.budgets.max_recovery_actions:
            attempts += 1
            self._counts[sequence_key] += 1
            self.stats.recovery_actions += 1
            self.stats.browser_retries += int(browser_like)
            try:
                if self.budgets.backoff_base_seconds:
                    await asyncio.sleep(self.budgets.backoff_base_seconds * (2 ** (attempts - 1)))
                async with asyncio.timeout(self.budgets.action_timeout_seconds):
                    value = action()
                    if inspect.isawaitable(value):
                        value = await value
                if validate(value) is not True:
                    self.stats.false_recovery_blocked += 1
                    raise RuntimeError("recovery validation failed; state is not known-good")
                result = RecoveryResult(True, strategy, attempts, new_state)
                self.stats.recovery_successes += 1
                self.stats.browser_retry_successes += int(browser_like)
                self._trace("RECOVERY", failure=failure.as_dict(), result=result.as_dict())
                return result
            except Exception as exc:  # recovery itself is bounded
                last_error = str(exc) or type(exc).__name__
        result = RecoveryResult(False, strategy, attempts, error=last_error or failure.message)
        self.stats.unrecoverable_failures += 1
        self._trace("RECOVERY", failure=failure.as_dict(), result=result.as_dict())
        return result

    def record_false_recovery(self, reason: str) -> None:
        self.stats.false_recoveries += 1
        self._trace("FALSE_RECOVERY", reason=reason)

    def _trace(self, event: str, **data: Any) -> None:
        self.trace.append({"event": event, **data})

    def report(self) -> dict[str, Any]:
        return {"budgets": self.budgets.__dict__.copy(), "stats": self.stats.as_dict(),
                "trace": list(self.trace), "checkpoints": [p.as_dict() for p in self.checkpoints.values()]}


def _first(mapping: Mapping[str, Any], *names: str) -> str | None:
    for name in names:
        value = mapping.get(name)
        if value is not None:
            return str(value)
    return None


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()[:1000]
