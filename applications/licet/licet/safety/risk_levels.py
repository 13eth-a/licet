"""Action risk classification — the core safety boundary for Licet.

Every action the agent might take is classified into one of two buckets:
AUTOMATIC (Licet can just do it) or CONFIRMATION_REQUIRED (Licet must stop
and get explicit user approval first, via `AgentState.request_approval`).

New action types must be classified here before the planner is allowed to
use them — there is no default-allow.

Phase 0 review §1: this module is only a boundary if something *calls* it with
the action actually being taken. It used to be unreachable, because the
planner's verbs are generic (`click`) while risk was keyed to semantic actions
(`submit_application`). The catalogue below is the vocabulary the browser
dispatcher maps tool calls onto, and `licet/safety/guard.py` is the choke point
that consults it. Keep the two in sync: an action that is not listed here
classifies as CONFIRMATION_REQUIRED, which blocks the run rather than
silently allowing it.
"""

from __future__ import annotations

from enum import Enum


class RiskLevel(str, Enum):
    AUTOMATIC = "automatic"
    CONFIRMATION_REQUIRED = "confirmation_required"
    # No approval path at all: the Phase 6 checklist's prohibited tier. Signing a
    # legal attestation for the user is the primitive-layer member of it — the
    # central policy engine already classifies `LEGAL_ATTESTATION` as prohibited,
    # and a layer that would allow it after a click-through would contradict the
    # gate that is supposed to be authoritative.
    PROHIBITED = "prohibited"


# Automatic: read-only or easily-reversible actions.
_AUTOMATIC_ACTIONS = {
    # reading / searching
    "search_permit",
    "search_records",
    "open_record",
    "read_record",
    "read_status",
    "read_inspection_history",
    "read_comments",
    "read_form",
    "list_records",
    "download_document",
    # navigation and session
    "navigate",
    "wait",
    "screenshot",
    "login",
    "logout",
    # reversible, per the Phase 0 checklist: scheduling can be rescheduled
    "schedule_inspection",
    "reschedule_inspection",
    # choosing an inspection type on the scheduling form is a form choice, not
    # a commitment (verified live: the wizard still has a date and a confirm
    # step after it)
    "select_inspection_type",
    # harmless local UI state (verified present on the record page)
    "add_to_collection",
    "create_collection",
    "copy_record",
    "report_export",
}

# Confirmation required: consequential or hard-to-reverse actions.
# Cancellation is here (not AUTOMATIC like schedule/reschedule) because
# cancelling can forfeit a slot or delay compliance in a way Licet cannot
# undo on its own — treat it like the other consequential actions.
_CONFIRMATION_REQUIRED_ACTIONS = {
    "cancel_inspection",
    "submit_payment",
    "enter_payment_details",
    "accept_legal_attestation",
    "submit_application",
    "sign_document",
    "upload_document",
    "register_account",
    "delete_record",
    "withdraw_application",
}

# Never allowed, with or without a human click-through.
_PROHIBITED_ACTIONS = {
    "accept_legal_attestation",
}

# Every semantic action the dispatcher is allowed to map a tool call onto.
KNOWN_ACTIONS = frozenset(_AUTOMATIC_ACTIONS | _CONFIRMATION_REQUIRED_ACTIONS | _PROHIBITED_ACTIONS)

# Every action that can change the portal's record state, whichever tier it sits
# in. The guard needs this set, not the tier, to apply the environment rule to
# all of them: a live municipal record must not be mutated through a primitive
# click merely because a human approved it (Phase 6: live blocking is not
# prompt-based), and an environment that cannot be established must not mutate
# at all.
STATE_CHANGING_ACTIONS = frozenset(
    {
        "schedule_inspection",
        "reschedule_inspection",
        "cancel_inspection",
        "submit_payment",
        "enter_payment_details",
        "accept_legal_attestation",
        "submit_application",
        "sign_document",
        "upload_document",
        "register_account",
        "delete_record",
        "withdraw_application",
    }
)

# Intent values that *acknowledge* that the current flow step may commit
# something. On a commit step, any other intent is overridden with the flow's
# commit action (see `dispatcher.resolve_action`) so that a benign-looking label
# cannot submit an application or schedule an inspection by accident.
COMMIT_ACKNOWLEDGING_ACTIONS = frozenset(
    {
        "submit_application",
        "schedule_inspection",
        # Rescheduling moves an existing appointment, so the wizard's confirm
        # step *is* a reschedule commit: without this, a reschedule intent would
        # be silently relabelled as the flow's schedule commit in the audit.
        "reschedule_inspection",
        "cancel_inspection",
        "submit_payment",
        "enter_payment_details",
        "accept_legal_attestation",
        "sign_document",
        "upload_document",
        "withdraw_application",
        "delete_record",
        "register_account",
    }
)


def classify(action: str) -> RiskLevel:
    if action in _PROHIBITED_ACTIONS:
        return RiskLevel.PROHIBITED
    if action in _CONFIRMATION_REQUIRED_ACTIONS:
        return RiskLevel.CONFIRMATION_REQUIRED
    if action in _AUTOMATIC_ACTIONS:
        return RiskLevel.AUTOMATIC
    # Unknown actions default to requiring confirmation — never default-allow.
    return RiskLevel.CONFIRMATION_REQUIRED


def requires_confirmation(action: str) -> bool:
    return classify(action) is RiskLevel.CONFIRMATION_REQUIRED


def changes_state(action: str) -> bool:
    """True when this action can alter the portal's record state."""
    return action in STATE_CHANGING_ACTIONS


def is_known(action: str) -> bool:
    return action in KNOWN_ACTIONS
