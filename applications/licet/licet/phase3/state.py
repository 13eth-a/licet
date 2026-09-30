"""Stable, serializable data contracts for Phase 3.

The models keep raw portal wording beside normalized values.  A missing value is
never represented by a fabricated default, and a complete empty section is
represented by coverage rather than by a blocker.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from enum import Enum
from typing import Any


class Section(str, Enum):
    OVERVIEW = "overview"
    INSPECTIONS = "inspections"
    FEES = "fees"
    DOCUMENTS = "documents"
    CONDITIONS = "conditions"
    HISTORY = "history"
    COMMENTS = "comments"


class CoverageStatus(str, Enum):
    NOT_REQUESTED = "not_requested"
    LOADING = "loading"
    PARTIAL = "partial"
    COMPLETE = "complete"
    EXPLICITLY_EMPTY = "explicitly_empty"
    UNAVAILABLE = "unavailable"
    PARSE_FAILED = "parse_failed"


class ConfidenceBand(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class FactKind(str, Enum):
    FACT = "FACT"
    INFERENCE = "INFERENCE"
    UNCERTAIN = "UNCERTAIN"


@dataclass
class Evidence:
    id: str
    section: str
    raw_text: str = ""
    source_url: str | None = None
    observed_at: str | None = None
    effective_at: str | None = None
    confidence: ConfidenceBand = ConfidenceBand.MEDIUM
    record_key: str | None = None


@dataclass
class Coverage:
    status: CoverageStatus = CoverageStatus.NOT_REQUESTED
    complete_through: str | None = None
    pages_seen: int = 0
    total_pages: int | None = None
    note: str | None = None

    @property
    def complete(self) -> bool:
        return self.status in (CoverageStatus.COMPLETE, CoverageStatus.EXPLICITLY_EMPTY)


@dataclass
class Inspection:
    type: str
    status: str | None = None
    result: str | None = None
    requested_date: str | None = None
    scheduled_date: str | None = None
    completed_date: str | None = None
    inspector: str | None = None
    comments: str | None = None
    inspection_id: str | None = None
    scope: str | None = None
    evidence_ids: list[str] = field(default_factory=list)
    comment_evidence_ids: list[str] = field(default_factory=list)
    raw_status: str | None = None
    raw_result: str | None = None
    lifecycle_normalized: str | None = None
    result_normalized: str | None = None

    @property
    def failed(self) -> bool:
        return self.result_normalized == "FAILED"

    @property
    def passed(self) -> bool:
        return self.result_normalized == "PASSED"


@dataclass
class Fee:
    description: str
    amount: float | None = None
    amount_text: str | None = None
    paid: bool | None = None
    due: bool | None = None
    balance: float | None = None
    gate_text: str | None = None
    evidence_ids: list[str] = field(default_factory=list)


@dataclass
class Document:
    name: str
    type: str | None = None
    status: str | None = None
    date: str | None = None
    downloadable: bool = False
    required: bool | None = None
    evidence_ids: list[str] = field(default_factory=list)


@dataclass
class Condition:
    description: str
    status: str | None = None
    severity: str | None = None
    source: str | None = None
    affects_stage: str | None = None
    evidence_ids: list[str] = field(default_factory=list)


@dataclass
class HistoryEvent:
    event: str
    date: str | None = None
    status: str | None = None
    details: str | None = None
    evidence_ids: list[str] = field(default_factory=list)


@dataclass
class Fact:
    field: str
    value: Any
    kind: FactKind = FactKind.FACT
    evidence_ids: list[str] = field(default_factory=list)
    confidence: ConfidenceBand = ConfidenceBand.MEDIUM
    entity_id: str | None = None
    raw_value: str | None = None


@dataclass
class Blocker:
    type: str
    description: str
    source: str
    confidence: float
    resolvable_by_licet: bool = False
    classification: str = "observed_problem"
    affects_stage: str | None = None
    evidence_ids: list[str] = field(default_factory=list)
    # Deterministic, explainable priority (lower = more important). It orders
    # presentation only; it is never evidence that a blocker is "first required".
    rank: int = 40

    @property
    def sort_key(self) -> tuple[int, str]:
        class_order = {"confirmed_gate": 0, "observed_problem": 1, "potential_impediment": 2}
        return (class_order.get(self.classification, 3), self.rank)


@dataclass
class NextActionCandidate:
    action: str
    reason: str
    confidence: float
    requires_confirmation: bool = False
    requirement_strength: str = "possible"
    preconditions: list[str] = field(default_factory=list)
    evidence_ids: list[str] = field(default_factory=list)


@dataclass
class Uncertainty:
    description: str
    impact: str
    needed_section: str | None = None
    evidence_ids: list[str] = field(default_factory=list)
    # True when this uncertainty prevents answering the question (missing
    # premises, unresolved ordering, unavailable data), not merely qualifies
    # the answer's classification. Drives the partial-answer decision.
    blocks_answer: bool = False


@dataclass
class Claim:
    id: str
    kind: FactKind
    statement: str
    evidence_ids: list[str] = field(default_factory=list)
    premise_claim_ids: list[str] = field(default_factory=list)
    confidence: ConfidenceBand = ConfidenceBand.MEDIUM
    reason_code: str = ""


@dataclass
class PermitState:
    record_number: str | None = None
    record_type: str | None = None
    address: str | None = None
    status: str | None = None
    status_normalized: str | None = None
    application_date: str | None = None
    issued_date: str | None = None
    expiration_date: str | None = None
    applicant: str | None = None
    contact: str | None = None
    parcel_number: str | None = None
    description: str | None = None
    record_key: str | None = None
    inspections: list[Inspection] = field(default_factory=list)
    fees: list[Fee] = field(default_factory=list)
    documents: list[Document] = field(default_factory=list)
    conditions: list[Condition] = field(default_factory=list)
    history: list[HistoryEvent] = field(default_factory=list)
    comments: list[str] = field(default_factory=list)
    outstanding_requirements: list[str] = field(default_factory=list)
    evidence: dict[str, Evidence] = field(default_factory=dict)
    coverage: dict[str, Coverage] = field(default_factory=dict)
    facts: list[Fact] = field(default_factory=list)
    contradictions: list[str] = field(default_factory=list)
    # Observations refused at merge time (foreign record keys). These are data
    # hygiene events, not record-state conflicts: they must be visible but must
    # never flip `answerability` to "conflicting".
    rejected_observations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return _jsonable(asdict(self))


@dataclass
class ReasoningResult:
    record_key: str | None
    snapshot_id: str
    question: str
    answerability: str
    claims: list[Claim] = field(default_factory=list)
    blockers: list[Blocker] = field(default_factory=list)
    next_actions: list[NextActionCandidate] = field(default_factory=list)
    contradictions: list[str] = field(default_factory=list)
    uncertainties: list[Uncertainty] = field(default_factory=list)
    needed_sections: list[dict[str, str]] = field(default_factory=list)
    execution_allowed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return _jsonable(asdict(self))


def _jsonable(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if is_dataclass(value) and not isinstance(value, type):
        return {k: _jsonable(v) for k, v in asdict(value).items()}
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)
