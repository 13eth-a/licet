"""phase 6 safety stop conditions"""

from __future__ import annotations

from enum import StrEnum

from licet.safety.policy import (
    PolicyDecision,
    ProposedAction,
    normalize_action,
)


class SafetyStopCondition(StrEnum):
    LIVE_MUTATION_ATTEMPTED = "LIVE_MUTATION_ATTEMPTED"
    UNKNOWN_ENVIRONMENT = "UNKNOWN_ENVIRONMENT"
    WRONG_PERMIT = "WRONG_PERMIT"
    WRONG_INSPECTION = "WRONG_INSPECTION"
    CONSTRAINT_VIOLATION = "CONSTRAINT_VIOLATION"
    MISSING_CONFIRMATION = "MISSING_CONFIRMATION"
    MISSING_REQUIRED_DATA = "MISSING_REQUIRED_DATA"
    LEGAL_ATTESTATION = "LEGAL_ATTESTATION"
    UNEXPECTED_PAYMENT_SCREEN = "UNEXPECTED_PAYMENT_SCREEN"
    UNVERIFIABLE_MUTATION = "UNVERIFIABLE_MUTATION"
    UNKNOWN_ACTION_RISK = "UNKNOWN_ACTION_RISK"


_CONSTRAINT_CODES = frozenset({
    "READ_ONLY", "SCHEDULING_NOT_ALLOWED", "RESCHEDULING_NOT_ALLOWED",
    "CANCELLATION_NOT_ALLOWED", "PAYMENTS_NOT_ALLOWED", "SUBMISSIONS_NOT_ALLOWED",
    "UPLOADS_NOT_ALLOWED", "APPLICANT_EDITS_NOT_ALLOWED", "RENEWALS_NOT_ALLOWED",
})

_RESULT_STOPS = {
    "LIVE_MUTATION_BLOCKED": SafetyStopCondition.LIVE_MUTATION_ATTEMPTED,
    "UNKNOWN_ENVIRONMENT": SafetyStopCondition.UNKNOWN_ENVIRONMENT,
    "UNKNOWN_ACTION_RISK": SafetyStopCondition.UNKNOWN_ACTION_RISK,
    "MISSING_REQUIRED_INPUT": SafetyStopCondition.MISSING_REQUIRED_DATA,
    "CONSTRAINT_VIOLATION": SafetyStopCondition.CONSTRAINT_VIOLATION,
    "STATE_MISMATCH": SafetyStopCondition.WRONG_PERMIT,
    "RECORD_IDENTITY_UNVERIFIED": SafetyStopCondition.WRONG_PERMIT,
    "TARGET_INSPECTION_UNIDENTIFIED": SafetyStopCondition.WRONG_INSPECTION,
    "ACTION_VERIFICATION_FAILED": SafetyStopCondition.UNVERIFIABLE_MUTATION,
    "UNCERTAIN_SUBMISSION": SafetyStopCondition.UNVERIFIABLE_MUTATION,
}


def _action_name(action: str | ProposedAction | None) -> str:
    if isinstance(action, ProposedAction):
        return normalize_action(action.action_type)
    return normalize_action(action or "")


def stop_for_decision(decision: PolicyDecision,
                      action: str | ProposedAction | None = None) -> SafetyStopCondition | None:
    """the safety stop a policy decision represents, or none if it may proceed"""
    if decision.allowed and not decision.requires_confirmation:
        return None
    code = decision.violated_constraint or ""
    detail = decision.reason or code
    kind = _action_name(action)
    if code == "LIVE_MUTATION_BLOCKED":
        return SafetyStopCondition.LIVE_MUTATION_ATTEMPTED
    if code == "UNKNOWN_ENVIRONMENT":
        return SafetyStopCondition.UNKNOWN_ENVIRONMENT
    if code == "UNKNOWN_ACTION_RISK":
        return SafetyStopCondition.UNKNOWN_ACTION_RISK
    if code == "TARGET_INSPECTION_UNIDENTIFIED":
        return SafetyStopCondition.WRONG_INSPECTION
    if code.startswith("MISSING_REQUIRED_INPUT"):
        return SafetyStopCondition.MISSING_REQUIRED_DATA
    if code.startswith("RECORD_IDENTITY_UNVERIFIED"):
        if "permit mismatch" in detail:
            return SafetyStopCondition.WRONG_PERMIT
        return SafetyStopCondition.WRONG_INSPECTION
    if decision.requires_confirmation or code == "MISSING_CONFIRMATION":
        return SafetyStopCondition.MISSING_CONFIRMATION
    if code == "PROHIBITED_ACTION":
        if kind in {"LEGAL_ATTESTATION", "SIGN_DOCUMENT"}:
            return SafetyStopCondition.LEGAL_ATTESTATION
        return SafetyStopCondition.UNKNOWN_ACTION_RISK
    if code in _CONSTRAINT_CODES:
        return SafetyStopCondition.CONSTRAINT_VIOLATION
    return None


def stop_for_result(result) -> SafetyStopCondition | None:
    """the safety stop an executor result represents, or none on verified success"""
    code = getattr(result, "error_code", None)
    value = getattr(code, "value", code)
    if value in _RESULT_STOPS:
        return _RESULT_STOPS[str(value)]
    if getattr(result, "success", False) and not getattr(result, "verified", False):
        return SafetyStopCondition.UNVERIFIABLE_MUTATION
    return None


def unexpected_payment_screen(requested_action: str | ProposedAction | None,
                              observed_action: str | ProposedAction | None) -> SafetyStopCondition | None:
    """a payment flow reached when no payment was requested is a hard stop"""
    if _action_name(observed_action) == "PAY_FEE" and _action_name(requested_action) != "PAY_FEE":
        return SafetyStopCondition.UNEXPECTED_PAYMENT_SCREEN
    return None


__all__ = [
    "SafetyStopCondition", "stop_for_decision", "stop_for_result",
    "unexpected_payment_screen",
]
