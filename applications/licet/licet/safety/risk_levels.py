"""action risk classification the core safety boundary for licet"""

from __future__ import annotations

from enum import Enum


class RiskLevel(str, Enum):
    AUTOMATIC = "automatic"
    CONFIRMATION_REQUIRED = "confirmation_required"
    # no approval path at all: the phase 6 checklist's prohibited tier
    PROHIBITED = "prohibited"


# automatic: read only or easily reversible actions
_AUTOMATIC_ACTIONS = {
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
    "navigate",
    "wait",
    "screenshot",
    "login",
    "logout",
    "schedule_inspection",
    "reschedule_inspection",
    # choosing an inspection type on the scheduling form is a form choice, not a commitment (verified
    # live: the wizard still has a date and a confirm step after it)
    "select_inspection_type",
    "add_to_collection",
    "create_collection",
    "copy_record",
    "report_export",
}

# confirmation required: consequential or hard to reverse actions
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

# never allowed, with or without a human click through
_PROHIBITED_ACTIONS = {
    "accept_legal_attestation",
}

KNOWN_ACTIONS = frozenset(_AUTOMATIC_ACTIONS | _CONFIRMATION_REQUIRED_ACTIONS | _PROHIBITED_ACTIONS)

# every action that can change the portal's record state, whichever tier it sits in
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

# intent values that *acknowledge* that the current flow step may commit something
COMMIT_ACKNOWLEDGING_ACTIONS = frozenset(
    {
        "submit_application",
        "schedule_inspection",
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
    # unknown actions default to requiring confirmation never default allow
    return RiskLevel.CONFIRMATION_REQUIRED


def requires_confirmation(action: str) -> bool:
    return classify(action) is RiskLevel.CONFIRMATION_REQUIRED


def changes_state(action: str) -> bool:
    """true when this action can alter the portal's record state"""
    return action in STATE_CHANGING_ACTIONS


def is_known(action: str) -> bool:
    return action in KNOWN_ACTIONS
