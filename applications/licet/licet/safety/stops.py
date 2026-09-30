"""Phase 6 safety stop conditions.

The checklist's eleven safety stops, as one closed classification. Every
deterministic decision and every execution result is mapped to at most one of
these so a caller can halt, report and audit the run with a stable reason code
instead of parsing a free-text message.

They are deliberately separate from `licet/agent/stop_conditions.py`: those are
loop/budget conditions (steps, stalls, approvals), while these are *safety*
outcomes — a live mutation attempt, an unknown environment, a wrong target, a
dropped constraint, an unverifiable result. A run may stop for both; the safety
one is the one that must never be overridden.
"""

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


# The user-constraint codes `UserConstraints.contradiction` can return.
_CONSTRAINT_CODES = frozenset({
    "READ_ONLY", "SCHEDULING_NOT_ALLOWED", "RESCHEDULING_NOT_ALLOWED",
    "CANCELLATION_NOT_ALLOWED", "PAYMENTS_NOT_ALLOWED", "SUBMISSIONS_NOT_ALLOWED",
    "UPLOADS_NOT_ALLOWED", "APPLICANT_EDITS_NOT_ALLOWED", "RENEWALS_NOT_ALLOWED",
})

# Execution-result error codes mapped to their safety stop.
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
    """The safety stop a policy decision represents, or None if it may proceed.

    ALLOW is the only outcome that yields no stop. CONFIRM is a controlled pause
    and maps to `MISSING_CONFIRMATION` — the run has not been authorized yet.
    """
    if decision.allowed and not decision.requires_confirmation:
        return None
    # `violated_constraint` is the stable code; `reason` carries the detail (e.g.
    # which identity field mismatched), so the specific checks read the latter.
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
    """The safety stop an executor result represents, or None on verified success.

    A success that was not independently verified is `UNVERIFIABLE_MUTATION`: the
    checklist forbids reporting an unconfirmed mutation as a verified success.
    """
    code = getattr(result, "error_code", None)
    value = getattr(code, "value", code)
    if value in _RESULT_STOPS:
        return _RESULT_STOPS[str(value)]
    if getattr(result, "success", False) and not getattr(result, "verified", False):
        return SafetyStopCondition.UNVERIFIABLE_MUTATION
    return None


def unexpected_payment_screen(requested_action: str | ProposedAction | None,
                              observed_action: str | ProposedAction | None) -> SafetyStopCondition | None:
    """A payment flow reached when no payment was requested is a hard stop.

    The checklist calls this out because a payment screen can appear without a
    supported action naming one; reaching it is not implicit authorization to
    pay, and the run must stop rather than click through.
    """
    if _action_name(observed_action) == "PAY_FEE" and _action_name(requested_action) != "PAY_FEE":
        return SafetyStopCondition.UNEXPECTED_PAYMENT_SCREEN
    return None


__all__ = [
    "SafetyStopCondition", "stop_for_decision", "stop_for_result",
    "unexpected_payment_screen",
]
