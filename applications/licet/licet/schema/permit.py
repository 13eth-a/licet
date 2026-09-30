"""Permit data schema, rebuilt against the records we actually created.

Phase 0 review §4: the first cut could not represent the eight test records on
Null Island. Concretely:

- `permit_id: str` cannot address a record. The *displayed* record number is
  per cap type (`000000014` for Commercial Alteration, `BLD26-004xx` for every
  other type) while the stable identity is capID1/capID2/capID3 + module +
  agency code, which is what a deep link needs — hence `RecordRef`.
- There was no created/submitted date, and `expiration_date` reads
  `01/31/2026` on a record that is **Submitted, not issued**: that value is
  agency configuration, so "is this permit expired?" would have been wrong.
  Both dates now exist and provenance keeps them distinguishable.
- There was no home for the scheduling form's inspection-type list, so the
  checklist's central question ("what inspection needs to happen next" =
  required types minus history) was unrepresentable.
- `Inspection` dropped the `inspectionID` (needed to reschedule/cancel),
  conflated scheduled/completed dates, and stored only the portal's raw status
  string with no normalized counterpart.
- `Fee.amount: float` was required even though fees arrive as portal text and
  payment is out of scope; `documents: list[str]` could not express an
  attachment that is a postback link rather than a filename.

Plain strings are still accepted for the fields that were lists of strings in
the first cut (documents / outstanding_requirements / next_action) and are
coerced, so existing callers keep working.
"""

from __future__ import annotations

import datetime as dt
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from licet.browser.accela import AGENCY_CODE, DEFAULT_MODULE, detail_url


class PermitStatus(str, Enum):
    """Normalized permit status, alongside the portal's raw string."""

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
    """Where a fact came from — evals score these differently.

    Phase 3 (architecture review review P1 #3): unknown provenance must stay unknown. A bare
    string with no source is coerced to UNATTRIBUTED — never silently promoted
    to a portal fact. PORTAL is reserved for values read off a page with
    evidence attached.
    """

    PORTAL = "portal"  # read verbatim off a portal page, evidence attached
    DERIVED = "derived"  # inferred by Licet (e.g. "next inspection required")
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
    # Exact vocabulary is intentional: Completed is lifecycle, not a result,
    # and negative phrases must not be swallowed by positive substrings.
    return _INSPECTION_STATUS_LOOKUP.get((raw or "").strip().lower(), InspectionStatus.UNKNOWN)


class Fact(BaseModel):
    """A claim plus how we know it."""

    value: str
    provenance: Provenance = Provenance.UNATTRIBUTED
    evidence_url: str | None = None


class RecordRef(BaseModel):
    """Stable record identity — what a deep link is actually made of."""

    cap_id1: str
    cap_id2: str
    cap_id3: str
    module: str = DEFAULT_MODULE
    agency_code: str = AGENCY_CODE
    display_id: str | None = None  # e.g. "BLD26-00472" or "000000014"

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
    """An attachment. `url=None` means the portal shows a section, not a file."""

    name: str
    url: str | None = None
    requires_click: bool = True  # ACA attachments are postback links, not hrefs

    @classmethod
    def from_value(cls, value: Any) -> "Document":
        if isinstance(value, Document):
            return value
        if isinstance(value, str):
            return cls(name=value)
        return cls(**value)


class Inspection(BaseModel):
    type: str
    status: str  # portal's raw string, e.g. "Insp Scheduled"
    status_normalized: InspectionStatus | None = None
    inspection_id: str | None = None
    scheduled_date: dt.date | None = None
    completed_date: dt.date | None = None
    # Kept for convenience/back-compat: whichever of the two dates was present.
    date: dt.date | None = None
    comments: str | None = None

    @model_validator(mode="after")
    def _fill_normalized(self) -> "Inspection":
        # A model validator, not a field validator: `mode="before"` validators
        # never run for a field that was simply not supplied.
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
    permit_id: str  # displayed record number, for humans and prompts
    address: str
    ref: RecordRef | None = None  # stable identity; required for deep links
    permit_type: str | None = None
    status: str | None = None  # portal's raw string
    status_normalized: PermitStatus | None = None
    applicant: str | None = None
    description: str | None = None

    submitted_date: dt.date | None = None  # "Date" column in My Records
    issued_date: dt.date | None = None
    expiration_date: dt.date | None = None

    inspections: list[Inspection] = Field(default_factory=list)
    # Types the scheduling form offers, so "what inspection is next" can be
    # computed as (required types) minus (history) instead of guessed.
    schedulable_inspection_types: list[str] = Field(default_factory=list)
    # Coverage/observation facts: empty-but-observed sections, loading markers,
    # calendar scope, truncation. Never treated as unmet obligations.
    coverage_notes: list[Fact] = Field(default_factory=list)
    # Types the record's scheduler marks `(required)` — explicit portal
    # requirement evidence, distinct from the offered catalog.
    required_inspection_types: list[str] = Field(default_factory=list)
    fees: list[Fee] = Field(default_factory=list)
    documents: list[Document] = Field(default_factory=list)
    outstanding_requirements: list[Fact] = Field(default_factory=list)
    next_action: Fact | None = None
    sections: list[str] = Field(default_factory=list)  # e.g. "Schedule an Inspection"

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
        """Explicitly REQUIRED types with no matching inspection attempt yet.

        Phase 3 (architecture review review P1 #1): the offered catalog is not a checklist.
        Only types the portal itself marks `(required)` may become outstanding
        work. Offered-but-unseen types are available options, not obligations —
        `offered_inspection_types()` carries them under a name that implies no
        requirement. For history, outcome matters: a failed attempt means the
        type is not "done", and a cancelled attempt satisfies nothing.
        """
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
        """Types the scheduling form offers — availability, not obligation."""
        return list(self.schedulable_inspection_types)
