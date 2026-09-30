"""Phase 6 deterministic safety boundary.

Models may propose actions, but this module is deliberately not model-driven:
unknown actions, unknown environments, stale approvals, wrong records, and
unverified mutation retries are denied here before a browser call is possible.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import IntEnum, StrEnum
import hashlib
import json
import re
import uuid
from typing import Any, Callable, Iterable, Mapping
from urllib.parse import urlparse


class Environment(StrEnum):
    SANDBOX = "sandbox"
    LIVE_READ_ONLY = "live_read_only"
    UNKNOWN = "unknown"


class ActionRisk(IntEnum):
    READ_ONLY = 0
    REVERSIBLE = 1
    CONSEQUENTIAL = 2
    PROHIBITED = 3


class PolicyVerdict(StrEnum):
    ALLOW = "ALLOW"
    CONFIRM = "CONFIRM"
    DENY = "DENY"


class MutationState(StrEnum):
    NOT_STARTED = "NOT_STARTED"
    SUBMITTED = "SUBMITTED"
    VERIFIED_SUCCESS = "VERIFIED_SUCCESS"
    VERIFIED_FAILURE = "VERIFIED_FAILURE"
    UNKNOWN_RESULT = "UNKNOWN_RESULT"


# Closed semantic vocabulary. Aliases are accepted only at the boundary and
# normalize to one canonical action; arbitrary planner verbs never do.
_ACTION_ALIASES = {
    "SEARCH_PERMIT": "FIND_PERMIT", "SEARCH_RECORD": "FIND_PERMIT",
    "OPEN_RECORD": "READ_PERMIT_STATE", "READ_STATUS": "READ_PERMIT_STATE",
    "READ_INSPECTION_HISTORY": "READ_INSPECTIONS", "READ_COMMENTS": "READ_HISTORY",
    "READ_FEE": "READ_FEES", "AVAILABILITY": "CHECK_AVAILABILITY",
    "CHECK_INSPECTION_AVAILABILITY": "CHECK_AVAILABILITY",
    "REQUEST_USER_APPROVAL": "REQUEST_CONFIRMATION",
    "SCHEDULE": "SCHEDULE_INSPECTION", "RESCHEDULE": "RESCHEDULE_INSPECTION",
    "CANCEL": "CANCEL_INSPECTION", "PAY": "PAY_FEE", "PAYMENT": "PAY_FEE",
    "SUBMIT": "SUBMIT_APPLICATION", "UPLOAD": "UPLOAD_DOCUMENT",
    "EDIT": "EDIT_APPLICANT",
    # The browser dispatcher's vocabulary (`licet/safety/risk_levels.py`) is the
    # other half of the same action space, and a semantic action may arrive from
    # either layer. Mapping the dispatcher's names onto the canonical ones means a
    # payment named at the primitive layer is classified as a payment here too,
    # rather than falling through to "unclassified, therefore prohibited".
    "SUBMIT_PAYMENT": "PAY_FEE", "ENTER_PAYMENT_DETAILS": "PAY_FEE",
    "ACCEPT_LEGAL_ATTESTATION": "LEGAL_ATTESTATION", "SIGN_DOCUMENT": "LEGAL_ATTESTATION",
}

ACTION_RISKS: dict[str, ActionRisk] = {
    "FIND_PERMIT": ActionRisk.READ_ONLY,
    "READ_PERMIT_STATE": ActionRisk.READ_ONLY,
    "READ_INSPECTIONS": ActionRisk.READ_ONLY,
    "READ_FEES": ActionRisk.READ_ONLY,
    "READ_DOCUMENTS": ActionRisk.READ_ONLY,
    "READ_HISTORY": ActionRisk.READ_ONLY,
    "CHECK_AVAILABILITY": ActionRisk.READ_ONLY,
    # The planner's own read/planning verbs. Classifying them keeps the vocabulary
    # closed against the component that actually emits it: an action the planner
    # can name must get a deterministic decision rather than `UNKNOWN_ACTION_RISK`.
    "READ_CONDITIONS": ActionRisk.READ_ONLY,
    "DETERMINE_BLOCKERS": ActionRisk.READ_ONLY,
    "DETERMINE_NEXT_INSPECTION": ActionRisk.READ_ONLY,
    "VERIFY_STATE": ActionRisk.READ_ONLY,
    "REQUEST_CONFIRMATION": ActionRisk.READ_ONLY,
    "STOP": ActionRisk.READ_ONLY,
    "SCHEDULE_INSPECTION": ActionRisk.REVERSIBLE,
    "RESCHEDULE_INSPECTION": ActionRisk.REVERSIBLE,
    "CANCEL_INSPECTION": ActionRisk.CONSEQUENTIAL,
    "PAY_FEE": ActionRisk.CONSEQUENTIAL,
    "SUBMIT_APPLICATION": ActionRisk.CONSEQUENTIAL,
    "SUBMIT_CORRECTIONS": ActionRisk.CONSEQUENTIAL,
    "UPLOAD_DOCUMENT": ActionRisk.CONSEQUENTIAL,
    "EDIT_APPLICANT": ActionRisk.CONSEQUENTIAL,
    "RENEW_PERMIT": ActionRisk.CONSEQUENTIAL,
    "LEGAL_ATTESTATION": ActionRisk.PROHIBITED,
    "FABRICATE_APPLICANT": ActionRisk.PROHIBITED,
    "INVENT_IDENTIFIER": ActionRisk.PROHIBITED,
    "BYPASS_AUTHENTICATION": ActionRisk.PROHIBITED,
    "OVERRIDE_AUTHORIZATION": ActionRisk.PROHIBITED,
    "MUTATE_LIVE_RECORD": ActionRisk.PROHIBITED,
    # Dispatcher-side verbs whose canonical equivalent is above, plus the
    # navigation/session/UI verbs the dispatcher can emit. Listed so that both
    # layers give the same answer for the same action name; the parity test in
    # tests/test_phase6_adversarial.py fails if they drift apart.
    "NAVIGATE": ActionRisk.READ_ONLY,
    "LOGIN": ActionRisk.READ_ONLY,
    "LOGOUT": ActionRisk.READ_ONLY,
    "WAIT": ActionRisk.READ_ONLY,
    "SCREENSHOT": ActionRisk.READ_ONLY,
    "SEARCH_RECORDS": ActionRisk.READ_ONLY,
    "LIST_RECORDS": ActionRisk.READ_ONLY,
    "READ_RECORD": ActionRisk.READ_ONLY,
    "READ_FORM": ActionRisk.READ_ONLY,
    "DOWNLOAD_DOCUMENT": ActionRisk.READ_ONLY,
    "SELECT_INSPECTION_TYPE": ActionRisk.READ_ONLY,
    "ADD_TO_COLLECTION": ActionRisk.READ_ONLY,
    "CREATE_COLLECTION": ActionRisk.READ_ONLY,
    "COPY_RECORD": ActionRisk.READ_ONLY,
    "REPORT_EXPORT": ActionRisk.READ_ONLY,
    # The dispatcher's catalogue names these as known actions (and the guard
    # therefore reaches them), but they are not portal operations Licet is being
    # built to perform. The Phase 6 vocabulary rule wants one closed space with
    # one explicit decision per action, not a policy layer that inherits its
    # answer from the guard's CONFIRMATION_REQUIRED fallback. These three are
    # therefore given an explicit PROHIBITED fixture decision rather than being
    # left as "unlisted, therefore fall back". That keeps the parity invariant
    # readable as a design choice and keeps the vocabulary closed.
    "REGISTER_ACCOUNT": ActionRisk.PROHIBITED,
    "DELETE_RECORD": ActionRisk.PROHIBITED,
    "WITHDRAW_APPLICATION": ActionRisk.PROHIBITED,
}

# Mutations that act on one already-existing appointment. Without that
# appointment's id the action cannot be bound to the row a human approved: the
# confirmation's scope, the identity check and the post-action verification all
# key on it, so an id-less proposal would be resolved against whichever row the
# portal happens to render. Such a proposal is refused, not guessed at.
_TARGETED_MUTATIONS = frozenset({"CANCEL_INSPECTION", "RESCHEDULE_INSPECTION"})

_MUTATIONS = frozenset(name for name, risk in ACTION_RISKS.items() if risk > ActionRisk.READ_ONLY)
# The last six are policy-level prohibited intents, not executable mutations,
# but keeping them in the vocabulary lets the engine produce an explicit deny.
_EXECUTABLE_MUTATIONS = frozenset({
    "SCHEDULE_INSPECTION", "RESCHEDULE_INSPECTION", "CANCEL_INSPECTION",
    "PAY_FEE", "SUBMIT_APPLICATION", "SUBMIT_CORRECTIONS", "UPLOAD_DOCUMENT",
    "EDIT_APPLICANT", "RENEW_PERMIT",
})


def normalize_action(action: str | Any) -> str:
    value = getattr(action, "value", action)
    value = str(value or "").strip().upper()
    return _ACTION_ALIASES.get(value, value)


def is_mutation(action: str | Any) -> bool:
    return normalize_action(action) in _EXECUTABLE_MUTATIONS


@dataclass(frozen=True)
class RecordIdentity:
    permit_id: str
    record_key: str | None = None
    inspection_type: str | None = None
    inspection_id: str | None = None
    existing_date: str | None = None
    status: str | None = None


@dataclass(frozen=True)
class ProposedAction:
    action_type: str
    permit_id: str | None = None
    target: str | None = None
    inspection_type: str | None = None
    inspection_id: str | None = None
    existing_date: str | None = None
    date_window_start: str | None = None
    date_window_end: str | None = None
    required_inputs: tuple[str, ...] = ()
    amount: float | None = None
    record_key: str | None = None
    snapshot_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "action_type", normalize_action(self.action_type))
        object.__setattr__(self, "required_inputs", tuple(self.required_inputs))

    @property
    def mutates_state(self) -> bool:
        return is_mutation(self.action_type)

    @property
    def risk(self) -> ActionRisk:
        return ACTION_RISKS.get(self.action_type, ActionRisk.PROHIBITED)

    def fingerprint(self) -> str:
        payload = {k: v for k, v in self.__dict__.items() if k != "snapshot_id"}
        return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


@dataclass(frozen=True)
class UserConstraints:
    """Immutable run-wide permissions. Planner output cannot replace this object."""

    read_only: bool = False
    allow_scheduling: bool = True
    allow_rescheduling: bool = True
    allow_cancellation: bool = True
    allow_payments: bool = True
    allow_submissions: bool = True
    allow_uploads: bool = True
    allow_applicant_edits: bool = True
    allow_renewals: bool = True
    no_existing_inspection_changes: bool = False
    source_text: str = ""

    def __post_init__(self) -> None:
        # Permission objects are intentionally immutable and normalize booleans.
        for name in ("read_only", "allow_scheduling", "allow_rescheduling", "allow_cancellation",
                     "allow_payments", "allow_submissions", "allow_uploads",
                     "allow_applicant_edits", "allow_renewals", "no_existing_inspection_changes"):
            object.__setattr__(self, name, bool(getattr(self, name)))

    # Capabilities the constraint vocabulary can deny. `allow_<capability>` is
    # False exactly when the instruction forbids it.
    CAPABILITIES = ("scheduling", "rescheduling", "cancellation", "payments",
                    "submissions", "uploads", "applicant_edits", "renewals")

    @classmethod
    def from_text(cls, text: str | None) -> "UserConstraints":
        """Parse an instruction into permissions, conservatively.

        Prohibitions are collected first and then no affirmative phrase may
        re-grant them: "do everything possible, but don't submit anything" must
        not permit submissions just because the broad phrase came first, and
        clause order must not decide permission. Broad language only fills in
        what the instruction did not forbid.
        """
        raw = (text or "").strip()
        low = raw.casefold().replace("’", "'")
        denied: set[str] = set()

        def forbid(pattern: str, *capabilities: str) -> None:
            if re.search(pattern, low):
                denied.update(capabilities)

        forbid(r"\b(read[- ]?only|no changes?|don't make any changes?|without (?:making\s+)?(?:any\s+)?changes?)\b",
               "read_only", "scheduling", "rescheduling", "cancellation")
        forbid(r"(?:don't|do not|never|no)\s+(?:spend|pay|make payments?|use payment)", "payments")
        forbid(r"(?:except|but|without|no)\s+(?:payment|pay(?:ing)?|spend(?:ing)?)", "payments")
        forbid(r"(?:don't|do not|never|no)\s+(?:submit|submitting)", "submissions")
        forbid(r"(?:except|but|without|no)\s+(?:submit(?:ting)?|submissions?)", "submissions")
        forbid(r"(?:don't|do not|never|no)\s+(?:upload|uploads?)", "uploads")
        forbid(r"(?:don't|do not|never|no)\s+(?:cancel|cancellation)", "cancellation")
        forbid(r"(?:except|but|without|no)\s+(?:cancel(?:lation)?|cancell?ing)", "cancellation")
        forbid(r"(?:don't|do not|never|no)\s+reschedul", "rescheduling")
        forbid(r"(?:don't|do not|never|no)\s+schedul", "scheduling")
        forbid(r"(?:don't|do not|never|no)\s+(?:change|modify|touch|alter|reschedule)\b[^.;\n]*\binspection",
               "rescheduling", "cancellation", "existing_inspection_changes")
        forbid(r"(?:don't|do not|never|no)\s+(?:change|modify|update)\b[^.;\n]*\b(?:applicant|contact|phone|address|email)",
               "applicant_edits")
        forbid(r"(?:don't|do not|never|no)\s+(?:renew|extend)", "renewals")

        values: dict[str, Any] = {
            "source_text": raw,
            "read_only": "read_only" in denied,
            "no_existing_inspection_changes": "existing_inspection_changes" in denied,
        }
        for capability in cls.CAPABILITIES:
            values[f"allow_{capability}"] = capability not in denied
        return cls(**values)

    def contradiction(self, action: str | ProposedAction) -> str | None:
        kind = normalize_action(action.action_type if isinstance(action, ProposedAction) else action)
        if self.read_only and kind in _EXECUTABLE_MUTATIONS:
            return "READ_ONLY"
        if kind == "SCHEDULE_INSPECTION" and not self.allow_scheduling:
            return "SCHEDULING_NOT_ALLOWED"
        if kind == "RESCHEDULE_INSPECTION" and (not self.allow_rescheduling or self.no_existing_inspection_changes):
            return "RESCHEDULING_NOT_ALLOWED"
        if kind == "CANCEL_INSPECTION" and (not self.allow_cancellation or self.no_existing_inspection_changes):
            return "CANCELLATION_NOT_ALLOWED"
        if kind == "PAY_FEE" and not self.allow_payments:
            return "PAYMENTS_NOT_ALLOWED"
        if kind in {"SUBMIT_APPLICATION", "SUBMIT_CORRECTIONS"} and not self.allow_submissions:
            return "SUBMISSIONS_NOT_ALLOWED"
        if kind == "UPLOAD_DOCUMENT" and not self.allow_uploads:
            return "UPLOADS_NOT_ALLOWED"
        if kind == "EDIT_APPLICANT" and not self.allow_applicant_edits:
            return "APPLICANT_EDITS_NOT_ALLOWED"
        if kind == "RENEW_PERMIT" and not self.allow_renewals:
            return "RENEWALS_NOT_ALLOWED"
        return None

    def allows(self, action: str | ProposedAction) -> bool:
        return self.contradiction(action) is None


# Contradiction detection. The Phase 6 checklist requires that an instruction
# which both *requests* and *forbids* the same operation resolve to
# `CONSTRAINT_CONFLICT`, never to an arbitrary interpretation — e.g. "Schedule
# the inspection, but don't make any changes." This is deterministic and reads
# the same permission object the run will enforce: take the positive clause
# (everything before the first restriction word), then ask whether the text's
# own prohibitions forbid an operation that clause requested.
CONSTRAINT_CONFLICT = "CONSTRAINT_CONFLICT"

_RESTRICTION_BOUNDARY = re.compile(r"\b(?:without|but|do not|don't|never|except|unless)\b", re.I)
_REQUESTED_OPERATIONS: tuple[tuple[str, str], ...] = (
    (r"\breschedul", "RESCHEDULE_INSPECTION"),
    (r"\bschedul", "SCHEDULE_INSPECTION"),
    (r"\bcancell?", "CANCEL_INSPECTION"),
    (r"\bsubmit", "SUBMIT_APPLICATION"),
    (r"\bupload", "UPLOAD_DOCUMENT"),
    (r"\bpay", "PAY_FEE"),
    (r"\b(?:renew|extend)\b", "RENEW_PERMIT"),
)


def detect_constraint_conflict(text: str | None) -> str | None:
    """Return `CONSTRAINT_CONFLICT` when an instruction requests what it forbids.

    "Schedule the inspection, but don't make any changes." is the canonical
    case: the positive clause asks to schedule and the restriction forbids every
    change, so there is no single reading to act on. A prohibition that is not
    contradicted by a request ("Read only") is a constraint, not a conflict, and
    returns ``None``.
    """
    raw = (text or "").strip()
    if not raw:
        return None
    positive = _RESTRICTION_BOUNDARY.split(raw.casefold().replace("\u2019", "'"), maxsplit=1)[0]
    constraints = UserConstraints.from_text(raw)
    for pattern, action in _REQUESTED_OPERATIONS:
        if re.search(pattern, positive) and constraints.contradiction(action) is not None:
            return CONSTRAINT_CONFLICT
    return None


@dataclass
class ConfirmationRequest:
    action_type: str
    permit_id: str
    target: str
    consequence: str
    amount: float | None = None
    inspection_id: str | None = None
    # Every field the operation actually names is bound, so an approval cannot be
    # re-pointed at another record or another date window between the approval and
    # the execution. (The *observed* existing date of a reschedule/cancel cannot
    # be bound here: at approval time the run has not read the appointment yet,
    # and the executor compares it against the same read it authorizes from. That
    # residual is named in docs/phase6/adversarial_review.md.)
    record_key: str | None = None
    date_window_start: str | None = None
    date_window_end: str | None = None
    issued_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime | None = None
    confirmation_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    used: bool = False

    def __post_init__(self) -> None:
        self.action_type = normalize_action(self.action_type)
        if self.expires_at is None:
            self.expires_at = self.issued_at + timedelta(minutes=10)

    @property
    def expired(self) -> bool:
        return datetime.now(timezone.utc) >= self.expires_at  # type: ignore[operator]

    def matches(self, action: ProposedAction) -> bool:
        """Whether this approval authorizes *exactly* this action.

        Nullable fields are compared, not treated as wildcards: an approval issued
        without an inspection id or an amount must not silently authorize an
        action that names one.
        """
        return (
            not self.used and not self.expired
            and self.action_type == normalize_action(action.action_type)
            and self.permit_id == (action.permit_id or "")
            and self.target == (action.target or action.inspection_type or "")
            and self.inspection_id == action.inspection_id
            and self.amount == action.amount
            and self.record_key == action.record_key
            and self.date_window_start == action.date_window_start
            and self.date_window_end == action.date_window_end
        )

    def consume(self, action: ProposedAction) -> bool:
        if not self.matches(action):
            return False
        self.used = True
        return True


@dataclass(frozen=True)
class IdentityCheck:
    verified: bool
    reason: str
    observed: RecordIdentity | None = None


def same_inspection_type(requested: str, observed: str) -> bool:
    """Whether two spellings name the same inspection type.

    One definition, shared with the planner's own target gate: punctuation, case
    and word order vary between Accela agencies ("Rough Electrical" is
    "Electrical - Rough" on some), but a substring is not a match. Reusing
    `licet.phase4.matching` here keeps the identity gate from contradicting the
    eligibility gate that already accepted the same pair.
    """
    from licet.phase4.matching import match_inspection_type
    return match_inspection_type(requested, [observed]) is not None


def verify_identity(action: ProposedAction, observed: RecordIdentity | Mapping[str, Any] | None) -> IdentityCheck:
    """Bind an action to the record/inspection an independent read observed.

    Every field the action *asserts* must be present in the observation and
    agree with it. An observation that is silent about a target the action names
    is unverified, not a pass: a portal read that lost the inspection type or
    date must never authorize a mutation of whatever row is on screen. (`permit_id`
    and `record_key` already failed closed this way; the inspection fields were
    the gap.)
    """
    if observed is None:
        return IdentityCheck(False, "RECORD_IDENTITY_UNVERIFIED")
    if isinstance(observed, Mapping):
        observed = RecordIdentity(
            permit_id=str(observed.get("permit_id") or ""),
            record_key=observed.get("record_key"), inspection_type=observed.get("inspection_type"),
            inspection_id=observed.get("inspection_id"), existing_date=observed.get("existing_date"),
            status=observed.get("status"),
        )
    if not action.permit_id or not observed.permit_id or action.permit_id != observed.permit_id:
        return IdentityCheck(False, "RECORD_IDENTITY_UNVERIFIED: permit mismatch", observed)
    if action.record_key and not observed.record_key:
        return IdentityCheck(False, "RECORD_IDENTITY_UNVERIFIED: observed record key missing", observed)
    if action.record_key and action.record_key != observed.record_key:
        return IdentityCheck(False, "RECORD_IDENTITY_UNVERIFIED: record key mismatch", observed)
    if action.inspection_id and action.inspection_id != observed.inspection_id:
        return IdentityCheck(False, "RECORD_IDENTITY_UNVERIFIED: inspection mismatch", observed)
    if action.inspection_type:
        if not observed.inspection_type:
            return IdentityCheck(False, "RECORD_IDENTITY_UNVERIFIED: observed inspection type missing", observed)
        if not same_inspection_type(action.inspection_type, observed.inspection_type):
            return IdentityCheck(False, "RECORD_IDENTITY_UNVERIFIED: inspection type mismatch", observed)
    if action.existing_date:
        if not observed.existing_date:
            return IdentityCheck(False, "RECORD_IDENTITY_UNVERIFIED: observed existing date missing", observed)
        if action.existing_date != observed.existing_date:
            return IdentityCheck(False, "RECORD_IDENTITY_UNVERIFIED: existing inspection date mismatch", observed)
    return IdentityCheck(True, "identity verified", observed)


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    requires_confirmation: bool
    risk_level: ActionRisk
    reason: str
    violated_constraint: str | None = None
    verdict: PolicyVerdict = PolicyVerdict.DENY
    confirmation: ConfirmationRequest | None = None

    @property
    def risk(self) -> ActionRisk:
        return self.risk_level

    @property
    def denied(self) -> bool:
        return not self.allowed and not self.requires_confirmation


@dataclass
class MutationRecord:
    mutation_id: str
    fingerprint: str
    action_type: str
    permit_id: str
    target: str
    state: MutationState = MutationState.NOT_STARTED
    attempts: int = 0
    before_state: Any = None
    after_state: Any = None
    error: str | None = None


@dataclass(frozen=True)
class MutationDecision:
    allowed: bool
    mutation_id: str | None
    reason: str
    state: MutationState | None = None


class MutationLedger:
    """Run-local idempotency ledger. UNKNOWN_RESULT is never replayable."""

    def __init__(self, max_mutations: int = 2) -> None:
        if max_mutations < 1:
            raise ValueError("max_mutations must be positive")
        self.max_mutations = max_mutations
        self.records: dict[str, MutationRecord] = {}
        self.by_fingerprint: dict[str, str] = {}

    @property
    def attempted_count(self) -> int:
        # A reservation consumes a run mutation slot even if validation later
        # fails; otherwise callers could reserve unlimited distinct mutations
        # without respecting MAX_MUTATIONS_PER_RUN.
        return len(self.records)

    def begin(self, action: ProposedAction, *, before_state: Any = None) -> MutationDecision:
        fingerprint = action.fingerprint()
        existing_id = self.by_fingerprint.get(fingerprint)
        if existing_id:
            record = self.records[existing_id]
            if record.state in {MutationState.SUBMITTED, MutationState.UNKNOWN_RESULT}:
                return MutationDecision(False, existing_id, "UNKNOWN_RESULT_REQUIRES_RECONCILIATION", record.state)
            if record.state is MutationState.VERIFIED_SUCCESS:
                return MutationDecision(False, existing_id, "MUTATION_ALREADY_COMPLETED", record.state)
            if record.state is MutationState.VERIFIED_FAILURE:
                return MutationDecision(False, existing_id, "MUTATION_ALREADY_FAILED", record.state)
        if existing_id:
            # A reservation is a reservation even before submission: a second
            # begin() of the same operation must not mint a second mutation id
            # (or displace the first) just because nothing was submitted yet.
            return MutationDecision(False, existing_id, "MUTATION_ALREADY_RESERVED",
                                    self.records[existing_id].state)
        if self.attempted_count >= self.max_mutations:
            return MutationDecision(False, None, "MAX_MUTATIONS_PER_RUN", None)
        mutation_id = uuid.uuid4().hex
        self.records[mutation_id] = MutationRecord(
            mutation_id, fingerprint, action.action_type, action.permit_id or "",
            action.target or action.inspection_type or "", before_state=before_state,
        )
        self.by_fingerprint[fingerprint] = mutation_id
        return MutationDecision(True, mutation_id, "mutation slot reserved", MutationState.NOT_STARTED)

    # Explicit aliases make the lifecycle readable at call sites and in tests.
    reserve = begin

    def mark_submitted(self, mutation_id: str) -> None:
        record = self._get(mutation_id)
        record.state, record.attempts = MutationState.SUBMITTED, record.attempts + 1

    def mark_verified_success(self, mutation_id: str, after_state: Any = None) -> None:
        record = self._get(mutation_id)
        record.state, record.after_state, record.error = MutationState.VERIFIED_SUCCESS, after_state, None

    def mark_verified_failure(self, mutation_id: str, error: str | None = None, after_state: Any = None) -> None:
        record = self._get(mutation_id)
        record.state, record.error, record.after_state = MutationState.VERIFIED_FAILURE, error, after_state

    def mark_unknown(self, mutation_id: str, error: str | None = None) -> None:
        record = self._get(mutation_id)
        record.state, record.error = MutationState.UNKNOWN_RESULT, error

    def reconcile(self, mutation_id: str, observed_success: bool | None, *, after_state: Any = None) -> MutationState:
        if observed_success is True:
            self.mark_verified_success(mutation_id, after_state)
        elif observed_success is False:
            self.mark_verified_failure(mutation_id, after_state=after_state)
        else:
            self.mark_unknown(mutation_id)
        return self.records[mutation_id].state

    def _get(self, mutation_id: str) -> MutationRecord:
        if mutation_id not in self.records:
            raise KeyError(f"unknown mutation id: {mutation_id}")
        return self.records[mutation_id]


@dataclass(frozen=True)
class SafetyAuditEvent:
    run_id: str
    environment: Environment
    user_goal: str
    user_constraints: UserConstraints
    permit_id: str | None
    proposed_action: str
    risk_level: ActionRisk
    policy_decision: str
    confirmation: str | None = None
    pre_action_state: Any = None
    execution_result: Any = None
    post_action_state: Any = None
    mutation_id: str | None = None
    reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id, "environment": self.environment.value,
            "user_goal": self.user_goal, "user_constraints": self.user_constraints.__dict__.copy(),
            "permit_id": self.permit_id, "proposed_action": self.proposed_action,
            "risk_level": self.risk_level.name, "policy_decision": self.policy_decision,
            "confirmation": self.confirmation, "pre_action_state": self.pre_action_state,
            "execution_result": self.execution_result, "post_action_state": self.post_action_state,
            "mutation_id": self.mutation_id, "reason": self.reason,
        }


class SafetyAuditLog:
    def __init__(self, *, logger: Any | None = None) -> None:
        self.events: list[SafetyAuditEvent] = []
        self.logger = logger

    def record(self, event: SafetyAuditEvent) -> None:
        self.events.append(event)
        if self.logger is not None:
            self.logger.log_event("safety_audit", **event.as_dict())

    def blocked(self) -> list[SafetyAuditEvent]:
        return [event for event in self.events if event.policy_decision == PolicyVerdict.DENY.value]


def safety_panel(decision: PolicyDecision, action: ProposedAction, *,
                 environment: Environment, permit_id: str | None = None) -> str:
    """The one-screen debug/demo view of a single policy decision.

    Shows the caller and the human the same five facts the audit records, in the
    order they are decided, so a run can be explained without reading the log.
    """
    return "\n".join((
        f"Environment: {Environment(environment).value}",
        f"Permit: {permit_id or action.permit_id or '-'}",
        f"Action: {action.action_type}",
        f"Risk: {ActionRisk(decision.risk_level).name}",
        f"Policy: {decision.verdict.value}",
        f"Reason: {decision.reason}",
    ))


def environment_from_url(url: str | None, *, sandbox_hosts: Iterable[str] = ("aca-test.accela.com",)) -> Environment:
    """Classify only known hosts; portal data never participates in detection."""
    if not url:
        return Environment.UNKNOWN
    host = (urlparse(url).hostname or "").casefold().rstrip(".")
    known_sandbox = {h.casefold().rstrip(".") for h in sandbox_hosts}
    if host in known_sandbox:
        return Environment.SANDBOX
    if "accela" in host or host in {"aca-prod.accela.com", "aca.accela.com"}:
        return Environment.LIVE_READ_ONLY
    return Environment.UNKNOWN


def detect_environment(source: Any, *, sandbox_hosts: Iterable[str] = ("aca-test.accela.com",)) -> Environment:
    explicit = getattr(source, "environment", None)
    if explicit is not None:
        try:
            return Environment(str(getattr(explicit, "value", explicit)))
        except ValueError:
            return Environment.UNKNOWN
    for value in (getattr(source, "current_url", None), getattr(getattr(source, "page", None), "url", None), getattr(source, "url", None)):
        result = environment_from_url(value, sandbox_hosts=sandbox_hosts)
        if result is not Environment.UNKNOWN:
            return result
    return Environment.UNKNOWN


class PolicyEngine:
    """Central deterministic Planner -> Policy -> Executor decision point."""

    def __init__(self, *, environment: Environment = Environment.UNKNOWN,
                 constraints: UserConstraints | None = None,
                 ledger: MutationLedger | None = None,
                 audit_log: SafetyAuditLog | None = None,
                 run_id: str = "run", user_goal: str = "",
                 now: Callable[[], datetime] | None = None,
                 metrics: Any | None = None) -> None:
        self.environment = Environment(environment)
        self.constraints = constraints or UserConstraints()
        self.ledger = ledger or MutationLedger()
        self.audit_log = audit_log or SafetyAuditLog()
        # Optional Phase 6 outcome tracker (`licet.safety.metrics.SafetyMetrics`).
        # Typed loosely to avoid a policy<->metrics import cycle; the only contract
        # is a `record_decision(decision)` method.
        self.metrics = metrics
        self.run_id, self.user_goal, self._now = run_id, user_goal, now or (lambda: datetime.now(timezone.utc))
        self._current_pre_action_state: Any = None
        # Issued-and-spent approvals, by id. The object's own `used` flag is
        # mutable state a caller can copy; the engine's record of what it has
        # already authorized is not.
        self._consumed_confirmations: set[str] = set()

    def decide(self, action: ProposedAction | str, *, permit_id: str | None = None,
               target: str | None = None, observed_identity: RecordIdentity | Mapping[str, Any] | None = None,
               confirmation: ConfirmationRequest | None = None,
               required_inputs: Mapping[str, Any] | None = None,
               pre_action_state: Any = None, verify_record: bool = True) -> PolicyDecision:
        if isinstance(action, str) or not isinstance(action, ProposedAction):
            action = ProposedAction(normalize_action(action), permit_id=permit_id, target=target)
        self._current_pre_action_state = pre_action_state
        name = normalize_action(action.action_type)
        risk = ACTION_RISKS.get(name)
        if risk is None:
            return self._decision(action, False, False, ActionRisk.PROHIBITED, "UNKNOWN_ACTION_RISK", "UNKNOWN_ACTION_RISK")
        if name in _EXECUTABLE_MUTATIONS and self.environment is Environment.UNKNOWN:
            return self._decision(action, False, False, risk, "UNKNOWN_ENVIRONMENT", "UNKNOWN_ENVIRONMENT")
        if action.mutates_state and self.environment is not Environment.SANDBOX:
            return self._decision(action, False, False, risk, "LIVE_MUTATION_BLOCKED", "LIVE_MUTATION_BLOCKED")
        constraint = self.constraints.contradiction(action)
        if constraint:
            return self._decision(action, False, False, risk, constraint, constraint)
        if risk is ActionRisk.PROHIBITED:
            return self._decision(action, False, False, risk, "PROHIBITED_ACTION", "PROHIBITED_ACTION")
        if name in _TARGETED_MUTATIONS and not action.inspection_id:
            return self._decision(action, False, False, risk, "TARGET_INSPECTION_UNIDENTIFIED",
                                  "TARGET_INSPECTION_UNIDENTIFIED")
        if action.mutates_state:
            # Action completeness, not identity: a proposal that needs a phone
            # number is refused here whether or not the record was re-verified.
            missing = [field for field in action.required_inputs if not (required_inputs or {}).get(field)]
            if missing:
                return self._decision(action, False, False, risk, "MISSING_REQUIRED_INPUT: " + ", ".join(missing), "MISSING_REQUIRED_INPUT")
        if action.mutates_state and verify_record:
            identity = verify_identity(action, observed_identity)
            if not identity.verified:
                return self._decision(action, False, False, risk, identity.reason, "RECORD_IDENTITY_UNVERIFIED")
        if risk is ActionRisk.CONSEQUENTIAL:
            usable = (confirmation is not None and confirmation.matches(action)
                      and confirmation.confirmation_id not in self._consumed_confirmations)
            if not usable:
                request = ConfirmationRequest(
                    action_type=name, permit_id=action.permit_id or "",
                    target=action.target or action.inspection_type or "",
                    consequence=self._consequence(action), amount=action.amount,
                    inspection_id=action.inspection_id, record_key=action.record_key,
                    date_window_start=action.date_window_start, date_window_end=action.date_window_end,
                )
                return self._decision(action, False, True, risk, "CONFIRMATION_REQUIRED", "MISSING_CONFIRMATION", request)
            # Approval is consumed at the policy boundary, not by the model or
            # browser adapter. Consuming it in the registry as well as on the
            # object means a copied or deserialized approval cannot authorize a
            # second execution of the same operation.
            confirmation.consume(action)
            self._consumed_confirmations.add(confirmation.confirmation_id)
        return self._decision(action, True, False, risk, "policy allows action", None)

    evaluate = decide

    def _consequence(self, action: ProposedAction) -> str:
        if action.action_type == "PAY_FEE":
            return f"pay ${action.amount:.2f}" if action.amount is not None else "pay a portal fee"
        return f"perform {action.action_type.lower().replace('_', ' ')}"

    def _decision(self, action, allowed, requires, risk, reason, violated, confirmation=None, pre_action_state=None):
        verdict = PolicyVerdict.ALLOW if allowed else PolicyVerdict.CONFIRM if requires else PolicyVerdict.DENY
        decision = PolicyDecision(allowed, requires, risk, reason, violated, verdict, confirmation)
        if self.metrics is not None:
            self.metrics.record_decision(decision, action=action.action_type)
        self.audit_log.record(SafetyAuditEvent(
            self.run_id, self.environment, self.user_goal, self.constraints,
            action.permit_id, action.action_type, risk, verdict.value,
            confirmation.confirmation_id if confirmation else None,
            pre_action_state=(pre_action_state if pre_action_state is not None else self._current_pre_action_state), reason=reason,
        ))
        return decision

    def record_execution(self, action: ProposedAction, *, mutation_id: str | None,
                         execution_result: Any = None, post_action_state: Any = None,
                         reason: str = "") -> None:
        """Append the execution/verification half of a mutation audit."""
        self.audit_log.record(SafetyAuditEvent(
            self.run_id, self.environment, self.user_goal, self.constraints,
            action.permit_id, action.action_type, action.risk, "EXECUTION",
            mutation_id=mutation_id, execution_result=execution_result,
            post_action_state=post_action_state, reason=reason,
        ))

    def consume_confirmation(self, confirmation: ConfirmationRequest, action: ProposedAction) -> bool:
        return confirmation.consume(action)


__all__ = [
    "ActionRisk", "ACTION_RISKS", "Environment", "PolicyDecision", "PolicyEngine",
    "CONSTRAINT_CONFLICT", "detect_constraint_conflict",
    "PolicyVerdict", "ProposedAction", "UserConstraints", "ConfirmationRequest",
    "RecordIdentity", "IdentityCheck", "verify_identity", "same_inspection_type",
    "MutationState", "MutationRecord",
    "MutationDecision", "MutationLedger", "SafetyAuditEvent", "SafetyAuditLog",
    "environment_from_url", "detect_environment", "is_mutation", "normalize_action",
    "safety_panel",
]
