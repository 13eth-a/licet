"""phase 4 mutation metrics: the numbers phase 4's zero-targets are measured by"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date

from licet.phase4.actions import (
    ActionVerificationState,
    InspectionAction,
    InspectionActionResult,
    MutationAudit,
)
from licet.phase4.dates import within_constraints
from licet.phase4.matching import match_inspection_type
from licet.phase4.selection import ActionSelection


def _kind(action_type: str) -> str:
    normalized = (action_type or "").casefold().strip().removesuffix("_inspection")
    return normalized


def _date_within(action: InspectionAction, scheduled_date: str | None) -> bool:
    """whether a verified appointment date satisfies the action's own window"""
    if not (action.date_window_start or action.date_window_end or action.preferred_date):
        return True
    if not scheduled_date:
        return False
    try:
        value = date.fromisoformat(scheduled_date)
    except ValueError:
        return False
    return within_constraints(value, action.date_constraints)


@dataclass
class Phase4Metrics:
    """counters for one run (or an aggregate of runs)"""

    actions_attempted: int = 0
    submission_attempts: int = 0
    verified_successes: int = 0
    verified_failures: int = 0
    unverified: int = 0
    state_mismatches: int = 0
    scheduling_attempts: int = 0
    scheduling_successes: int = 0
    rescheduling_attempts: int = 0
    rescheduling_successes: int = 0
    cancellation_attempts: int = 0
    cancellation_successes: int = 0

    selection_attempts: int = 0
    actions_selected: int = 0
    expected_selections: int = 0
    selection_matches: int = 0

    # invariants — every one of these must be 0
    duplicate_submissions: int = 0
    wrong_record_mutations: int = 0
    wrong_inspection_mutations: int = 0
    constraint_violations: int = 0
    unverified_successes: int = 0

    # per-code detail, for diagnosing a non-zero invariant
    refusals: dict[str, int] = field(default_factory=dict)
    selection_stops: dict[str, int] = field(default_factory=dict)


    def record_selection(
        self, selection: ActionSelection, *, expected: InspectionAction | None = None
    ) -> None:
        """count one selection outcome, optionally against a known-correct target"""
        self.selection_attempts += 1
        if selection.action is None:
            key = selection.status.value
            self.selection_stops[key] = self.selection_stops.get(key, 0) + 1
        else:
            self.actions_selected += 1
        if expected is not None:
            self.expected_selections += 1
            if selection.action is not None and self._same_operation(selection.action, expected):
                self.selection_matches += 1

    def record(
        self,
        action: InspectionAction,
        result: InspectionActionResult,
        audits: Iterable[MutationAudit] = (),
    ) -> None:
        """count one executed action and check the executor's safety invariants"""
        audits = tuple(audits)
        kind = _kind(action.action_type)
        self.actions_attempted += 1
        if kind == "schedule":
            self.scheduling_attempts += 1
        elif kind == "reschedule":
            self.rescheduling_attempts += 1
        elif kind == "cancel":
            self.cancellation_attempts += 1

        # a submission is inferred from the audit's recorded browser steps: the executor logs "submit"
        # only on a path that attempted the mutation
        submitted = any("submit" in audit.browser_steps for audit in audits)
        if submitted:
            self.submission_attempts += 1

        state = result.verification_state
        if state is ActionVerificationState.VERIFIED_SUCCESS:
            self.verified_successes += 1
            if kind == "schedule":
                self.scheduling_successes += 1
            elif kind == "reschedule":
                self.rescheduling_successes += 1
            elif kind == "cancel":
                self.cancellation_successes += 1
        elif state is ActionVerificationState.STATE_MISMATCH:
            self.state_mismatches += 1
        elif state is ActionVerificationState.UNVERIFIED:
            self.unverified += 1
        else:
            self.verified_failures += 1

        if result.error_code is not None:
            code = result.error_code.value
            self.refusals[code] = self.refusals.get(code, 0) + 1

        self._check_invariants(action, kind, result, submitted)

    def _check_invariants(
        self, action: InspectionAction, kind: str, result: InspectionActionResult, submitted: bool
    ) -> None:
        if result.success and not result.verified:
            self.unverified_successes += 1
        before = result.before
        if not submitted or before is None:
            return
        if kind == "schedule" and (before.is_scheduled or before.is_pending):
            self.duplicate_submissions += 1
        if action.record_key and before.record_key and before.record_key != action.record_key:
            self.wrong_record_mutations += 1
        if (
            action.existing_inspection_id
            and before.inspection_id
            and before.inspection_id != action.existing_inspection_id
        ):
            self.wrong_inspection_mutations += 1
        if result.success and result.after is not None and not _date_within(action, result.after.scheduled_date):
            self.constraint_violations += 1

    @staticmethod
    def _same_operation(selected: InspectionAction, expected: InspectionAction) -> bool:
        if _kind(selected.action_type) != _kind(expected.action_type):
            return False
        return match_inspection_type(
            expected.inspection_type or "", [selected.inspection_type or ""]
        ) is not None


    def _rate(self, numerator: int, denominator: int) -> float:
        return numerator / denominator if denominator else 0.0

    @property
    def verification_success_rate(self) -> float:
        return self._rate(self.verified_successes, self.submission_attempts)

    @property
    def scheduling_success_rate(self) -> float:
        return self._rate(self.scheduling_successes, self.scheduling_attempts)

    @property
    def rescheduling_success_rate(self) -> float:
        return self._rate(self.rescheduling_successes, self.rescheduling_attempts)

    @property
    def cancellation_success_rate(self) -> float:
        return self._rate(self.cancellation_successes, self.cancellation_attempts)

    @property
    def action_selection_accuracy(self) -> float:
        return self._rate(self.selection_matches, self.expected_selections)

    @property
    def duplicate_action_rate(self) -> float:
        return self._rate(self.duplicate_submissions, self.submission_attempts)

    @property
    def wrong_record_mutation_rate(self) -> float:
        return self._rate(self.wrong_record_mutations, self.submission_attempts)

    @property
    def wrong_inspection_mutation_rate(self) -> float:
        return self._rate(self.wrong_inspection_mutations, self.submission_attempts)

    @property
    def constraint_violation_rate(self) -> float:
        return self._rate(self.constraint_violations, self.submission_attempts)

    @property
    def unverified_success_rate(self) -> float:
        return self._rate(self.unverified_successes, self.actions_attempted)

    @property
    def safety_violations(self) -> int:
        """total observations that contradict an executor guarantee"""
        return (
            self.duplicate_submissions
            + self.wrong_record_mutations
            + self.wrong_inspection_mutations
            + self.constraint_violations
            + self.unverified_successes
        )

    def zero_targets(self) -> dict[str, int]:
        """the phase 4 checklist's strict targets, named as they are stated"""
        return {
            "wrong_permit_mutation": self.wrong_record_mutations,
            "wrong_inspection_mutation": self.wrong_inspection_mutations,
            "duplicate_submission": self.duplicate_submissions,
            "user_constraint_violation": self.constraint_violations,
            "unverified_success": self.unverified_successes,
        }


    _COUNTERS = (
        "actions_attempted",
        "submission_attempts",
        "verified_successes",
        "verified_failures",
        "unverified",
        "state_mismatches",
        "scheduling_attempts",
        "scheduling_successes",
        "rescheduling_attempts",
        "rescheduling_successes",
        "cancellation_attempts",
        "cancellation_successes",
        "selection_attempts",
        "actions_selected",
        "expected_selections",
        "selection_matches",
        "duplicate_submissions",
        "wrong_record_mutations",
        "wrong_inspection_mutations",
        "constraint_violations",
        "unverified_successes",
    )

    def merge(self, other: "Phase4Metrics") -> "Phase4Metrics":
        """fold another run's counters in, returning self"""
        for name in self._COUNTERS:
            setattr(self, name, getattr(self, name) + getattr(other, name))
        for name in ("refusals", "selection_stops"):
            target = getattr(self, name)
            for key, value in getattr(other, name).items():
                target[key] = target.get(key, 0) + value
        return self

    @classmethod
    def combine(cls, metrics: Iterable["Phase4Metrics"]) -> "Phase4Metrics":
        total = cls()
        for item in metrics:
            total.merge(item)
        return total

    def as_dict(self) -> dict[str, object]:
        """counters, derived rates and the zero-target verdict, json-serializable"""
        snapshot: dict[str, object] = {name: getattr(self, name) for name in self._COUNTERS}
        snapshot.update(
            refusals=dict(self.refusals),
            selection_stops=dict(self.selection_stops),
            verification_success_rate=self.verification_success_rate,
            scheduling_success_rate=self.scheduling_success_rate,
            rescheduling_success_rate=self.rescheduling_success_rate,
            cancellation_success_rate=self.cancellation_success_rate,
            action_selection_accuracy=self.action_selection_accuracy,
            duplicate_action_rate=self.duplicate_action_rate,
            wrong_record_mutation_rate=self.wrong_record_mutation_rate,
            wrong_inspection_mutation_rate=self.wrong_inspection_mutation_rate,
            constraint_violation_rate=self.constraint_violation_rate,
            unverified_success_rate=self.unverified_success_rate,
            safety_violations=self.safety_violations,
            zero_targets=self.zero_targets(),
            within_zero_targets=self.safety_violations == 0,
        )
        return snapshot


__all__ = ["Phase4Metrics"]
