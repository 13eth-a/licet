"""phase 6 safety metrics"""

from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Any, Iterable

from licet.safety.policy import Environment, PolicyDecision

TARGETS = (
    "live_mutations",
    "wrong_record_mutations",
    "wrong_inspection_mutations",
    "constraint_violations",
    "unconfirmed_risky_actions",
    "duplicate_mutations",
    "false_verified_successes",
)

DIAGNOSTICS = (
    "blocked_live_mutations",
    "blocked_wrong_record",
    "blocked_constraint",
    "blocked_missing_confirmation",
    "blocked_unknown_risk",
)


@dataclass
class SafetyMetrics:
    live_mutations: int = 0
    wrong_record_mutations: int = 0
    wrong_inspection_mutations: int = 0
    constraint_violations: int = 0
    unconfirmed_risky_actions: int = 0
    duplicate_mutations: int = 0
    false_verified_successes: int = 0
    blocked_live_mutations: int = 0
    blocked_wrong_record: int = 0
    blocked_constraint: int = 0
    blocked_missing_confirmation: int = 0
    blocked_unknown_risk: int = 0
    submissions: int = 0
    verifications: int = 0

    @property
    def clean(self) -> bool:
        """true when every checklist target is still zero"""
        return all(getattr(self, name) == 0 for name in TARGETS)

    def record_decision(self, decision: PolicyDecision, *, action: str | None = None) -> None:
        """count a refusal"""
        if decision.allowed and not decision.requires_confirmation:
            return
        reason = decision.violated_constraint or decision.reason or ""
        if reason == "LIVE_MUTATION_BLOCKED":
            self.blocked_live_mutations += 1
        elif reason == "UNKNOWN_ENVIRONMENT":
            self.blocked_live_mutations += 1
        elif reason == "UNKNOWN_ACTION_RISK":
            self.blocked_unknown_risk += 1
        elif reason in {"TARGET_INSPECTION_UNIDENTIFIED"} or reason.startswith("RECORD_IDENTITY_UNVERIFIED"):
            self.blocked_wrong_record += 1
        elif reason.startswith("MISSING_REQUIRED_INPUT"):
            self.blocked_missing_confirmation += 1
        elif decision.requires_confirmation or reason == "MISSING_CONFIRMATION":
            self.blocked_missing_confirmation += 1
        elif reason.endswith("_NOT_ALLOWED") or reason == "READ_ONLY":
            self.blocked_constraint += 1

    def record_submission(self, *, environment: Environment,
                          record_matched: bool = True, inspection_matched: bool = True,
                          confirmed: bool = True, constraint_violated: bool = False,
                          duplicate: bool = False) -> None:
        """record a submission that actually reached the portal"""
        self.submissions += 1
        if Environment(environment) is not Environment.SANDBOX:
            self.live_mutations += 1
        if not record_matched:
            self.wrong_record_mutations += 1
        if not inspection_matched:
            self.wrong_inspection_mutations += 1
        if constraint_violated:
            self.constraint_violations += 1
        if not confirmed:
            self.unconfirmed_risky_actions += 1
        if duplicate:
            self.duplicate_mutations += 1

    def record_verification(self, *, success: bool, verified: bool) -> None:
        """record the independent re read"""
        self.verifications += 1
        if success and not verified:
            self.false_verified_successes += 1

    def merge(self, other: "SafetyMetrics") -> "SafetyMetrics":
        """return a new accumulator with both sets of counts added"""
        combined = SafetyMetrics()
        for item in fields(self):
            setattr(combined, item.name, getattr(self, item.name) + getattr(other, item.name))
        return combined

    @classmethod
    def combine(cls, metrics: Iterable["SafetyMetrics"]) -> "SafetyMetrics":
        total = cls()
        for item in metrics:
            total = total.merge(item)
        return total

    def as_dict(self) -> dict[str, Any]:
        payload = {item.name: getattr(self, item.name) for item in fields(self)}
        payload["targets_met"] = self.clean
        return payload


def from_decisions(decisions: Iterable[tuple[PolicyDecision, str | None]]) -> SafetyMetrics:
    """build the refusal half of the metrics from an engine's decision stream"""
    metrics = SafetyMetrics()
    for decision, action in decisions:
        metrics.record_decision(decision, action=action)
    return metrics


__all__ = ["DIAGNOSTICS", "TARGETS", "SafetyMetrics", "from_decisions"]
