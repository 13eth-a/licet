"""permit data schema, rebuilt against the records we actually created"""

from __future__ import annotations

import datetime as dt
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from licet.browser.accela import AGENCY_CODE, DEFAULT_MODULE, detail_url


class PermitStatus(str, Enum):
    """normalized permit status, alongside the portal's raw string"""

    DRAFT = "draft"
    SUBMITTED = "submitted"
    IN_REVIEW = "in_review"
    APPROVED = "approved"
    ISSUED = "issued"
    EXPIRED = "expired"
    CLOSED = "closed"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


class InspectionStatus(str, Enum):
    PENDING = "pending"
    SCHEDULED = "scheduled"
    PASSED = "passed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    PARTIAL = "partial"
    UNKNOWN = "unknown"


class Provenance(str, Enum):
    """where a fact came from evals score these differently"""

    PORTAL = "portal"
    DERIVED = "derived"
    UNATTRIBUTED = "unattributed"  # origin unknown; not a verified portal fact


_STATUS_LOOKUP: dict[str, PermitStatus] = {
    "submitted": PermitStatus.SUBMITTED,
    "application received": PermitStatus.SUBMITTED,
    "in review": PermitStatus.IN_REVIEW,
    "processing": PermitStatus.IN_REVIEW,
    "under review": PermitStatus.IN_REVIEW,
    "pending review": PermitStatus.IN_REVIEW,
    "issued": PermitStatus.ISSUED,
    "permit issued": PermitStatus.ISSUED,
    "active": PermitStatus.ISSUED,
    "expired": PermitStatus.EXPIRED,
    "permit expired": PermitStatus.EXPIRED,
    "closed": PermitStatus.CLOSED,
    "finaled": PermitStatus.CLOSED,
    "complete": PermitStatus.CLOSED,
    "completed": PermitStatus.CLOSED,
    "cancelled": PermitStatus.CANCELLED,
    "canceled": PermitStatus.CANCELLED,
    "void": PermitStatus.CANCELLED,
    "approved": PermitStatus.APPROVED,
    "draft": PermitStatus.DRAFT,
    "incomplete": PermitStatus.DRAFT,
}

_INSPECTION_STATUS_LOOKUP: dict[str, InspectionStatus] = {
    "scheduled": InspectionStatus.SCHEDULED,
    "insp scheduled": InspectionStatus.SCHEDULED,
    "passed": InspectionStatus.PASSED,
    "approved": InspectionStatus.PASSED,
    "failed": InspectionStatus.FAILED,
    "denied": InspectionStatus.FAILED,
    "not approved": InspectionStatus.FAILED,
    "corrections required": InspectionStatus.FAILED,
    "cancelled": InspectionStatus.CANCELLED,
    "canceled": InspectionStatus.CANCELLED,
    "void": InspectionStatus.CANCELLED,
    "pending": InspectionStatus.PENDING,
    "not scheduled": InspectionStatus.PENDING,
    "awaiting": InspectionStatus.PENDING,
    "partial": InspectionStatus.PARTIAL,
    "partial pass": InspectionStatus.PARTIAL,
}


def normalize_permit_status(raw: str | None) -> PermitStatus:
    return _STATUS_LOOKUP.get((raw or "").strip().lower(), PermitStatus.UNKNOWN)


def normalize_inspection_status(raw: str | None) -> InspectionStatus:
    # exact vocabulary is intentional: completed is lifecycle, not a result, and negative phrases must not
    # be swallowed by positive substrings
    return _INSPECTION_STATUS_LOOKUP.get((raw or "").strip().lower(), InspectionStatus.UNKNOWN)


class Fact(BaseModel):
    """a claim plus how we know it"""

    value: str
    provenance: Provenance = Provenance.UNATTRIBUTED
    evidence_url: str | None = None


class RecordRef(BaseModel):
    """stable record identity what a deep link is actually made of"""

    cap_id1: str
    cap_id2: str
    cap_id3: str
    module: str = DEFAULT_MODULE
    agency_code: str = AGENCY_CODE
    display_id: str | None = None

    def detail_url(self, **overrides: Any) -> str:
        return detail_url(
            self.cap_id1,
            self.cap_id2,
            self.cap_id3,
            module=overrides.get("module", self.module),
            agency_code=overrides.get("agency_code", self.agency_code),
        )

    def as_key(self) -> str:
        return f"{self.agency_code}/{self.module}/{self.cap_id1}/{self.cap_id2}/{self.cap_id3}"


class Document(BaseModel):
    """an attachment"""

    name: str
    url: str | None = None
    requires_click: bool = True  # aca attachments are postback links, not hrefs

    @classmethod
    def from_value(cls, value: Any) -> "Document":
        if isinstance(value, Document):
            return value
        if isinstance(value, str):
            return cls(name=value)
        return cls(**value)


class Inspection(BaseModel):
    type: str
    status: str
    status_normalized: InspectionStatus | None = None
    inspection_id: str | None = None
    scheduled_date: dt.date | None = None
    completed_date: dt.date | None = None
    date: dt.date | None = None
    comments: str | None = None

    @model_validator(mode="after")
    def _fill_normalized(self) -> "Inspection":
        # a model validator, not a field validator: `mode="before"` validators never run for a field that
        # was simply not supplied
        if self.status_normalized is None:
            self.status_normalized = normalize_inspection_status(self.status)
        return self


class Fee(BaseModel):
    description: str
    amount: float | None = None
    amount_text: str | None = None
    paid: bool | None = None
    balance_text: str | None = None

    @model_validator(mode="after")
    def _text_from_amount(self) -> "Fee":
        if self.amount_text is None and self.amount is not None:
            self.amount_text = f"{self.amount:.2f}"
        return self


class Permit(BaseModel):
    permit_id: str
    address: str
    ref: RecordRef | None = None
    permit_type: str | None = None
    status: str | None = None
    status_normalized: PermitStatus | None = None
    applicant: str | None = None
    description: str | None = None

    submitted_date: dt.date | None = None
    issued_date: dt.date | None = None
    expiration_date: dt.date | None = None

    inspections: list[Inspection] = Field(default_factory=list)
    # types the scheduling form offers, so "what inspection is next" can be computed as (required types)
    # minus (history) instead of guessed
    schedulable_inspection_types: list[str] = Field(default_factory=list)
    # coverage/observation facts: empty but observed sections, loading markers, calendar scope, truncation
    coverage_notes: list[Fact] = Field(default_factory=list)
    required_inspection_types: list[str] = Field(default_factory=list)
    fees: list[Fee] = Field(default_factory=list)
    documents: list[Document] = Field(default_factory=list)
    outstanding_requirements: list[Fact] = Field(default_factory=list)
    next_action: Fact | None = None
    sections: list[str] = Field(default_factory=list)

    @field_validator("documents", mode="before")
    @classmethod
    def _coerce_documents(cls, value: Any) -> Any:
        if value is None:
            return []
        return [Document.from_value(item) for item in value]

    @field_validator("outstanding_requirements", mode="before")
    @classmethod
    def _coerce_requirements(cls, value: Any) -> Any:
        if value is None:
            return []
        return [
            item
            if isinstance(item, Fact)
            else Fact(value=str(item), provenance=Provenance.UNATTRIBUTED)
            for item in value
        ]

    @field_validator("coverage_notes", mode="before")
    @classmethod
    def _coerce_coverage_notes(cls, value: Any) -> Any:
        if value is None:
            return []
        return [
            item
            if isinstance(item, Fact)
            else Fact(value=str(item), provenance=Provenance.UNATTRIBUTED)
            for item in value
        ]

    @field_validator("next_action", mode="before")
    @classmethod
    def _coerce_next_action(cls, value: Any) -> Any:
        if value is None or isinstance(value, Fact):
            return value
        return Fact(value=str(value), provenance=Provenance.DERIVED)

    @model_validator(mode="after")
    def _fill_normalized_status(self) -> "Permit":
        if self.status_normalized is None:
            self.status_normalized = normalize_permit_status(self.status)
        return self

    def missing_inspections(self) -> list[str]:
        """explicitly required types with no matching inspection attempt yet"""
        done = {
            inspection.type.strip().lower()
            for inspection in self.inspections
            if inspection.status_normalized in (InspectionStatus.PASSED, InspectionStatus.PARTIAL, InspectionStatus.SCHEDULED)
        }
        return [
            name
            for name in self.required_inspection_types
            if name.strip().lower() not in done
        ]

    def offered_inspection_types(self) -> list[str]:
        """types the scheduling form offers availability, not obligation"""
        return list(self.schedulable_inspection_types)
