"""Stable LicetBench v1 task and result contracts."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
import math
from typing import Any

BENCHMARK_VERSION = "licetbench-v1"

_IMMUTABLE_MESSAGE = "benchmark goldens are immutable; derive a new task with dataclasses.replace"


class FrozenDict(dict):
    """A dict that refuses in-place mutation.

    Goldens are the thing under test, so a caller must not be able to edit one
    in place and change a later run in the same process. Subclassing `dict` and
    `list` keeps JSON, CSV and digest output byte-identical to plain containers,
    and immutability means a copy is free to share the same object.
    """

    def _reject_mutation(self, *args, **kwargs):
        raise TypeError(_IMMUTABLE_MESSAGE)

    __setitem__ = _reject_mutation
    __delitem__ = _reject_mutation
    __ior__ = _reject_mutation
    clear = _reject_mutation
    pop = _reject_mutation
    popitem = _reject_mutation
    setdefault = _reject_mutation
    update = _reject_mutation

    def __copy__(self):
        return self

    def __deepcopy__(self, memo):
        return self


class FrozenList(list):
    """A list that refuses in-place mutation; see `FrozenDict`."""

    def _reject_mutation(self, *args, **kwargs):
        raise TypeError(_IMMUTABLE_MESSAGE)

    __setitem__ = _reject_mutation
    __delitem__ = _reject_mutation
    __iadd__ = _reject_mutation
    __imul__ = _reject_mutation
    append = _reject_mutation
    clear = _reject_mutation
    extend = _reject_mutation
    insert = _reject_mutation
    pop = _reject_mutation
    remove = _reject_mutation
    reverse = _reject_mutation
    sort = _reject_mutation

    def __copy__(self):
        return self

    def __deepcopy__(self, memo):
        return self


def freeze_golden(value):
    """Recursively make a golden value read-only, preserving container types."""
    if isinstance(value, (FrozenDict, FrozenList)):
        return value
    if isinstance(value, dict):
        return FrozenDict({key: freeze_golden(item) for key, item in value.items()})
    if isinstance(value, list):
        return FrozenList(freeze_golden(item) for item in value)
    if isinstance(value, tuple):
        return tuple(freeze_golden(item) for item in value)
    return value


class BenchmarkCategory(StrEnum):
    PERMIT_DISCOVERY = "Permit Discovery"
    PERMIT_UNDERSTANDING = "Permit Understanding"
    ACTION_EXECUTION = "Action Execution"
    GOAL_BASED_AUTONOMY = "Goal-Based Autonomy"
    SAFETY = "Safety"
    RECOVERY = "Recovery"


class Outcome(StrEnum):
    SUCCESS = "SUCCESS"
    PARTIAL_SUCCESS = "PARTIAL_SUCCESS"
    SAFE_FAILURE = "SAFE_FAILURE"
    UNSAFE_FAILURE = "UNSAFE_FAILURE"
    FAILURE = "FAILURE"


@dataclass(frozen=True)
class BenchmarkTask:
    id: str
    category: str
    prompt: str
    initial_state: dict[str, Any]
    expected_outcome: dict[str, Any]
    allowed_actions: tuple[str, ...] = ()
    prohibited_actions: tuple[str, ...] = ()
    max_steps: int = 20
    mutation_expected: bool = False
    seed: int = 0
    suite: str = "core"
    source: str = "fixture"
    tags: tuple[str, ...] = ()
    repeat: int = 1

    def __post_init__(self) -> None:
        for name in ("allowed_actions", "prohibited_actions", "tags"):
            object.__setattr__(self, name, tuple(getattr(self, name)))
        # Frozen dataclass fields are not enough: the goldens themselves are
        # dicts, so they are frozen recursively too.
        object.__setattr__(self, "initial_state", freeze_golden(self.initial_state))
        object.__setattr__(self, "expected_outcome", freeze_golden(self.expected_outcome))
        if not self.id.strip():
            raise ValueError("benchmark task id must not be empty")
        if not self.category.strip() or not self.prompt.strip():
            raise ValueError("benchmark task category and prompt must not be empty")
        if self.max_steps < 1:
            raise ValueError("max_steps must be positive")
        if self.repeat < 1:
            raise ValueError("repeat must be positive")
        if self.mutation_expected and self.initial_state.get("environment") != "sandbox":
            raise ValueError("mutation tasks must explicitly declare sandbox initial_state")

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BenchmarkResult:
    task_id: str
    success: bool
    partial_success: bool
    safe: bool
    final_state_verified: bool
    expectation_met: bool = True
    semantic_steps: int = 0
    browser_actions: int = 0
    replans: int = 0
    recoveries: int = 0
    recovery_attempts: int = 0
    recovery_successes: int = 0
    latency_seconds: float = 0.0
    model_calls: int = 0
    estimated_cost: float | None = None
    failure_type: str | None = None
    outcome: str = Outcome.FAILURE.value
    run_id: str = ""
    repeat_index: int = 0
    model: str = ""
    config: str = ""
    final_state: dict[str, Any] = field(default_factory=dict)
    observed_actions: tuple[str, ...] = ()
    trace: tuple[dict[str, Any], ...] = ()
    details: dict[str, Any] = field(default_factory=dict)
    wrong_record_actions: int = 0
    wrong_action_count: int = 0
    constraint_violations: int = 0
    duplicate_mutations: int = 0
    false_verified_successes: int = 0
    unnecessary_page_visits: int = 0
    mutation_submissions: int = 0
    verified_mutations: int = 0
    live_mutations: int = 0
    wrong_inspection_mutations: int = 0
    unconfirmed_risky_mutations: int = 0
    grader_error: bool = False

    def __post_init__(self) -> None:
        if not self.task_id.strip():
            raise ValueError("benchmark result task_id must not be empty")
        for name in (
            "semantic_steps", "browser_actions", "replans", "recoveries",
            "recovery_attempts", "recovery_successes", "model_calls", "wrong_record_actions", "wrong_action_count",
            "constraint_violations", "duplicate_mutations", "false_verified_successes",
            "unnecessary_page_visits", "mutation_submissions", "verified_mutations",
            "live_mutations", "wrong_inspection_mutations", "unconfirmed_risky_mutations",
        ):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must not be negative")
        if not math.isfinite(self.latency_seconds) or self.latency_seconds < 0:
            raise ValueError("latency_seconds must not be negative")
        if self.estimated_cost is not None and (not math.isfinite(self.estimated_cost) or self.estimated_cost < 0):
            raise ValueError("estimated_cost must not be negative")
        if self.outcome not in {item.value for item in Outcome}:
            raise ValueError(f"unknown benchmark outcome: {self.outcome}")
        if self.success and (not self.expectation_met or not self.safe):
            raise ValueError("success requires safe, met expectations")
        if self.safe and any((self.live_mutations, self.wrong_record_actions, self.constraint_violations,
                             self.duplicate_mutations, self.wrong_inspection_mutations, self.unconfirmed_risky_mutations)):
            raise ValueError("constraint-violating or otherwise unsafe results cannot be safe")
        if self.success and self.false_verified_successes:
            raise ValueError("false verified success cannot pass")
        if self.success and self.outcome != Outcome.SUCCESS.value:
            raise ValueError("success=True requires SUCCESS outcome")
        if self.expectation_met and self.outcome in {Outcome.FAILURE.value, Outcome.UNSAFE_FAILURE.value}:
            raise ValueError("a met expectation must not have FAILURE outcome")
        if self.partial_success and self.outcome != Outcome.PARTIAL_SUCCESS.value:
            raise ValueError("partial_success=True requires PARTIAL_SUCCESS outcome")
        if self.outcome == Outcome.UNSAFE_FAILURE.value and self.safe:
            raise ValueError("UNSAFE_FAILURE must have safe=False")
        if not self.safe and self.outcome != Outcome.UNSAFE_FAILURE.value:
            raise ValueError("an unsafe result must carry the UNSAFE_FAILURE outcome")
        if self.outcome == Outcome.SAFE_FAILURE.value and not self.safe:
            raise ValueError("SAFE_FAILURE requires safe=True")
        if self.success and not self.final_state_verified:
            raise ValueError("a successful result requires a verified final state")
        if self.success and self.grader_error:
            raise ValueError("a grader error cannot be reported as a success")
        if self.success and self.constraint_violations:
            raise ValueError("a constraint-violating result cannot be successful")
        if self.verified_mutations > self.mutation_submissions:
            raise ValueError("verified mutations cannot exceed submitted mutations")
        if self.recovery_successes > self.recovery_attempts:
            raise ValueError("recovery successes cannot exceed recovery attempts")

    def as_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["trace"] = list(self.trace)
        result["observed_actions"] = list(self.observed_actions)
        return result
