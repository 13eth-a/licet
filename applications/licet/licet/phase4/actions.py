from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from licet.phase4.dates import DateConstraints


class ActionErrorCode(str, Enum):
    INSPECTION_NOT_ELIGIBLE = "INSPECTION_NOT_ELIGIBLE"
    INSPECTION_ALREADY_SCHEDULED = "INSPECTION_ALREADY_SCHEDULED"
    NO_AVAILABLE_DATES = "NO_AVAILABLE_DATES"
    DATE_CONSTRAINT_UNSATISFIED = "DATE_CONSTRAINT_UNSATISFIED"
    MISSING_REQUIRED_INPUT = "MISSING_REQUIRED_INPUT"
    ACTION_NOT_ALLOWED = "ACTION_NOT_ALLOWED"
    ACTION_REQUIRES_CONFIRMATION = "ACTION_REQUIRES_CONFIRMATION"
    SCHEDULE_FAILED = "SCHEDULE_FAILED"
    RESCHEDULE_FAILED = "RESCHEDULE_FAILED"
    CANCELLATION_FAILED = "CANCELLATION_FAILED"
    ACTION_VERIFICATION_FAILED = "ACTION_VERIFICATION_FAILED"
    STATE_MISMATCH = "STATE_MISMATCH"
    UNCERTAIN_SUBMISSION = "UNCERTAIN_SUBMISSION"
    LIVE_MUTATION_BLOCKED = "LIVE_MUTATION_BLOCKED"
    UNKNOWN_ENVIRONMENT = "UNKNOWN_ENVIRONMENT"
    UNKNOWN_ACTION_RISK = "UNKNOWN_ACTION_RISK"
    RECORD_IDENTITY_UNVERIFIED = "RECORD_IDENTITY_UNVERIFIED"
    TARGET_INSPECTION_UNIDENTIFIED = "TARGET_INSPECTION_UNIDENTIFIED"
    MAX_MUTATIONS_PER_RUN = "MAX_MUTATIONS_PER_RUN"
    MUTATION_ALREADY_COMPLETED = "MUTATION_ALREADY_COMPLETED"
    CONFIRMATION_EXPIRED = "CONFIRMATION_EXPIRED"
    CONSTRAINT_VIOLATION = "CONSTRAINT_VIOLATION"


class ActionVerificationState(str, Enum):
    VERIFIED_SUCCESS = "VERIFIED_SUCCESS"
    VERIFIED_FAILURE = "VERIFIED_FAILURE"
    UNVERIFIED = "UNVERIFIED"
    STATE_MISMATCH = "STATE_MISMATCH"


@dataclass(frozen=True)
class InspectionAction:
    action_type: str
    permit_id: str
    inspection_type: str | None = None
    preferred_date: str | None = None
    date_window_start: str | None = None
    date_window_end: str | None = None
    existing_inspection_id: str | None = None
    constraints: list[str] = field(default_factory=list)
    record_key: str | None = None
    snapshot_id: str | None = None
    evidence_ids: tuple[str, ...] = ()
    requires_confirmation: bool = False

    @property
    def date_constraints(self) -> DateConstraints:
        """the bounded date window this action asks for, as a shared value"""
        from datetime import date
        return DateConstraints(
            start=date.fromisoformat(self.date_window_start) if self.date_window_start else None,
            end=date.fromisoformat(self.date_window_end) if self.date_window_end else None,
            preferred=date.fromisoformat(self.preferred_date) if self.preferred_date else None,
            earliest=True,
        )

    def as_dict(self) -> dict[str, Any]:
        return {"action_type": self.action_type, "permit_id": self.permit_id,
                "inspection_type": self.inspection_type, "preferred_date": self.preferred_date,
                "date_window_start": self.date_window_start, "date_window_end": self.date_window_end,
                "existing_inspection_id": self.existing_inspection_id,
                "constraints": list(self.constraints),
                "record_key": self.record_key, "snapshot_id": self.snapshot_id,
                "evidence_ids": list(self.evidence_ids),
                "requires_confirmation": self.requires_confirmation}


@dataclass(frozen=True)
class InspectionSnapshot:
    permit_id: str
    inspection_id: str | None
    inspection_type: str
    status: str
    scheduled_date: str | None = None
    eligible: bool = True
    available_dates: tuple[str, ...] = ()
    required_fields: tuple[str, ...] = ()
    confirmation_number: str | None = None
    # stable record identity (recordref.as_key form) observed on the page this snapshot was read from
    record_key: str | None = None

    @property
    def is_scheduled(self) -> bool:
        return self.status.strip().lower() in {"scheduled", "confirmed", "appointment scheduled"}

    @property
    def is_pending(self) -> bool:
        """an in-flight request the portal has accepted but not yet scheduled"""
        return self.status.strip().lower() in {"requested", "pending"}

    @property
    def is_cancelled(self) -> bool:
        return self.status.strip().lower() in {"cancelled", "canceled"}

    @property
    def is_completed(self) -> bool:
        return self.status.strip().lower() in {"completed", "passed", "failed", "closed"}


@dataclass
class InspectionActionResult:
    success: bool
    action_type: str
    inspection_type: str | None
    scheduled_date: str | None = None
    previous_date: str | None = None
    confirmation_number: str | None = None
    verified: bool = False
    error: str | None = None
    verification_state: ActionVerificationState = ActionVerificationState.UNVERIFIED
    error_code: ActionErrorCode | None = None
    before: InspectionSnapshot | None = None
    after: InspectionSnapshot | None = None
    # advisory only: available portal dates outside the requested window
    alternatives: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {"success": self.success, "action_type": self.action_type,
                "inspection_type": self.inspection_type, "scheduled_date": self.scheduled_date,
                "previous_date": self.previous_date, "confirmation_number": self.confirmation_number,
                "verified": self.verified, "error": self.error,
                "verification_state": self.verification_state.value,
                "error_code": self.error_code.value if self.error_code else None,
                "before": self.before.__dict__ if self.before else None,
                "after": self.after.__dict__ if self.after else None,
                "alternatives": list(self.alternatives)}


@dataclass(frozen=True)
class MutationAudit:
    permit_id: str
    inspection_type: str | None
    requested_action: str
    previous_state: InspectionSnapshot | None
    proposed_state: InspectionSnapshot | None
    browser_steps: tuple[str, ...] = ()
    portal_response: str | None = None
    verified_final_state: InspectionSnapshot | None = None
    record_key: str | None = None
    snapshot_id: str | None = None
    evidence_ids: tuple[str, ...] = ()
    requires_confirmation: bool = False

    def as_dict(self) -> dict[str, Any]:
        def snapshot(value: InspectionSnapshot | None) -> dict[str, Any] | None:
            return value.__dict__ if value else None
        return {"permit": self.permit_id, "inspection": self.inspection_type,
                "requested_action": self.requested_action,
                "previous_state": snapshot(self.previous_state),
                "proposed_state": snapshot(self.proposed_state),
                "browser_steps": list(self.browser_steps),
                "portal_response": self.portal_response,
                "verified_final_state": snapshot(self.verified_final_state),
                "record_key": self.record_key, "snapshot_id": self.snapshot_id,
                "evidence_ids": list(self.evidence_ids),
                "requires_confirmation": self.requires_confirmation}
