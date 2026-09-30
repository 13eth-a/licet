"""Deterministic Phase 3 findings.

Rules only promote facts when the portal supplies the required evidence. In
particular (Astra review P1 #1/#5, contract "What counts as a blocker"):

- an unpaid fee is a money fact; it is a gate only with explicit gate evidence
  (fee gate text or an explicit payment-required condition), never from the
  existence of a balance;
- a failed inspection is an observed problem, never authority to schedule —
  no reinspection candidate is emitted when a follow-up is already scheduled
  or when attempt ordering is unknown;
- offered inspection types are never required work; only ACA's own
  ``(required)`` marker (carried as a ``required_type`` fact) can produce an
  unmet-requirement candidate, and even that stays a candidate, not a blocker;
- coverage states (unavailable/parse-failed sections) are uncertainties about
  knowledge, never permit defects.

Blockers carry an explainable ``rank`` (lower = presented first) and are
ordered by classification, then rank — presentation order, never evidence that
the first item is legally required first (reasoning contract, ranking policy).
"""
from __future__ import annotations

import datetime as _dt
import re
from dataclasses import dataclass, field

from licet.phase3.state import (
    Blocker,
    CoverageStatus,
    Inspection,
    NextActionCandidate,
    PermitState,
    Uncertainty,
)


@dataclass
class DeterministicFindings:
    blockers: list[Blocker] = field(default_factory=list)
    next_actions: list[NextActionCandidate] = field(default_factory=list)
    contradictions: list[str] = field(default_factory=list)
    uncertainties: list[str] = field(default_factory=list)


# Deterministic, explainable ranking (reasoning contract: explicit
# administrative holds, failed prerequisites, explicit missing required
# documents, explicit outstanding required inspections, payment gates,
# informational issues). Ranks order presentation; they do not claim that the
# first item is operationally first.
_RANK: dict[str, int] = {
    "active_condition": 10,
    "failed_inspection": 20,
    "missing_required_document": 30,
    "required_inspection_unmet": 40,
    "expired_permit": 15,
    "unpaid_fee": 50,
}


def _sort_key(blocker: Blocker) -> tuple[int, int]:
    class_order = {"confirmed_gate": 0, "observed_problem": 1, "potential_impediment": 2}
    return (class_order.get(blocker.classification, 3), _RANK.get(blocker.type, 60))


# --- condition activity ----------------------------------------------------
# Agency labels are matched exactly after harmless normalization, and an
# unrecognized label stays unknown (Astra review P1 #2, applied to conditions).
# Substring matching here previously promoted "Hold released" to a confirmed
# gate and "No warning" to an observed problem (DeepSeek review A1).
_ACTIVE_CONDITION_LABELS = {
    "active", "open", "outstanding", "in force", "in effect", "in place",
    "not satisfied", "unsatisfied", "unresolved", "violation", "warning",
    "deficiency", "action required", "corrections required", "hold", "on hold",
    "active hold", "administrative hold", "suspended", "suspension",
    "stop work", "stop work order",
}
_INACTIVE_CONDITION_LABELS = {
    "released", "hold released", "lifted", "cleared", "satisfied", "resolved",
    "closed", "complete", "completed", "waived", "void", "cancelled",
    "canceled", "inactive", "withdrawn", "expired", "superseded",
    "no longer applies", "not applicable", "n/a", "none",
}
# Only an explicit administrative hold is a *gate*; other active conditions are
# observed problems with unknown scope.
_GATE_CONDITION_LABELS = {
    "hold", "on hold", "active hold", "administrative hold", "suspended",
    "suspension", "stop work", "stop work order",
}


def _condition_label(condition) -> str:
    return " ".join((condition.status or "").split()).strip().lower().rstrip(".")


def _condition_activity(condition) -> str:
    """``active`` / ``inactive`` / ``unknown`` from the portal's own label."""
    label = _condition_label(condition)
    if not label:
        return "unknown"
    if label in _INACTIVE_CONDITION_LABELS:
        return "inactive"
    if label in _ACTIVE_CONDITION_LABELS:
        return "active"
    return "unknown"


def _condition_is_gate(condition) -> bool:
    return _condition_label(condition) in _GATE_CONDITION_LABELS


# A required document is a blocker only when the portal states the requirement
# is unmet. "Pending"/"Under review" say nothing about satisfaction (review A11).
_MISSING_DOCUMENT_LABELS = {
    "missing", "not received", "not submitted", "not uploaded", "outstanding",
    "incomplete", "required", "delinquent", "void",
}
_AMBIGUOUS_DOCUMENT_LABELS = {
    "pending", "pending review", "under review", "in review", "processing",
    "awaiting", "expired",
}


# --- money -----------------------------------------------------------------

def _fee_outstanding(fee) -> float | None:
    """The amount that is actually still owed, number first.

    ``amount_text`` is a raw capture and can disagree with the parsed balance
    (review A8): "$74.50" was reported for a row whose balance was $100.00.
    """
    return fee.balance if fee.balance is not None else fee.amount


def format_fee_amount(fee) -> str:
    outstanding = _fee_outstanding(fee)
    if outstanding is not None:
        return f"${outstanding:,.2f}"
    return fee.amount_text or "an unquantified amount"


# --- payment gates ---------------------------------------------------------
# A gate is a portal statement. Two adversarial failures are guarded here: an
# explicit negation ("No payment is required before issuance") must not become a
# gate, and the gated stage must come from the wording rather than being
# assumed to be issuance (review A2/A3).
_NEGATED_PAYMENT_RE = re.compile(
    r"\b(no|not|never|without)\b[^.;]{0,40}"
    r"\b(pay|paid|payment|payments|fee|fees|balance|charge|charges|money)\b"
)
_PAYMENT_GATE_RE = re.compile(
    r"\b(pay|paid|payment|payments|balance|fee|fees|charge|charges)\b[^.;]{0,40}"
    r"\b(before|prior to|required for|required before|prerequisite|must be paid|due before)\b"
)


def _stage_from_text(text: str) -> str | None:
    lowered = (text or "").lower()
    if "issu" in lowered:
        return "issuance"
    if "occupancy" in lowered:
        return "certificate of occupancy"
    if "final" in lowered:
        return "final inspection"
    if "inspection" in lowered:
        return "inspection"
    return None


def _payment_gate_evidence(fee, state) -> tuple[str, str | None] | None:
    """Return ``(gate wording, affected stage)`` when the portal states one."""
    if fee.gate_text and not _NEGATED_PAYMENT_RE.search(fee.gate_text.lower()):
        return fee.gate_text, _stage_from_text(fee.gate_text)
    for condition in state.conditions:
        text = " ".join(f"{condition.description} {condition.status or ''}".split()).lower()
        if _NEGATED_PAYMENT_RE.search(text):
            continue
        if _PAYMENT_GATE_RE.search(text):
            return text, _stage_from_text(text)
    return None


# --- comment and history wording -------------------------------------------
# "Corrections Required" beside a Passed result is conflicting evidence; "No
# corrections required" beside a Passed result is a clean pass. The former
# substring test (``"correction" in comments``) flagged both (review A5).
_NEGATED_CORRECTION_RE = re.compile(
    r"\b(no|not|none|without|zero|free of|clear of)\b[^.;]{0,24}\bcorrections?\b"
)
_CORRECTION_REQUIRED_RE = re.compile(
    r"\bcorrections?\b[^.;]{0,24}\b(required|needed|outstanding|pending|must|shall|requested)\b"
)


def comment_requests_correction(text: str) -> bool:
    """Whether a linked comment states that corrections are *outstanding*."""
    lowered = (text or "").lower()
    if _NEGATED_CORRECTION_RE.search(lowered):
        return False
    return bool(_CORRECTION_REQUIRED_RE.search(lowered))


# "Permit expired" is an explicit expiration event; "permit not expired" is the
# opposite statement and must not be read as one (review A6).
_NEGATED_EXPIRY_RE = re.compile(r"\b(no|not|never|without|before|prior to)\b[^.;]{0,24}\bexpir")


def explicit_expiration_event(text: str) -> bool:
    lowered = (text or "").lower()
    if _NEGATED_EXPIRY_RE.search(lowered):
        return False
    return bool(re.search(r"\bexpired\b", lowered))


# --- attempt ordering -------------------------------------------------------

def _iso_date(value: str | None):
    if not value:
        return None
    try:
        return _dt.date.fromisoformat(str(value).strip())
    except ValueError:
        return None


def _attempt_order(failed, passed) -> str:
    """``later`` / ``earlier`` / ``unknown`` / ``incomparable``.

    Only a *later* pass can supersede a failure. The inherited resolution step
    ignored chronology and silently dropped a failure that followed a pass
    (review A4). Unknown or non-ISO dates stay ``unknown`` rather than being
    ordered by screen position.
    """
    if failed.scope and passed.scope and failed.scope != passed.scope:
        return "incomparable"
    earlier, later = _iso_date(failed.completed_date), _iso_date(passed.completed_date)
    if earlier is None or later is None:
        return "unknown"
    if later > earlier:
        return "later"
    if later < earlier:
        return "earlier"
    return "unknown"


def derive_deterministic_findings(state: PermitState) -> DeterministicFindings:
    result = DeterministicFindings(contradictions=list(state.contradictions))
    # Explicit expiration is a portal fact; a configured date alone is not an
    # expired state (Astra case S02).
    if state.status_normalized == "EXPIRED":
        result.blockers.append(
            Blocker("expired_permit", "Portal reports the permit as expired", "Overview",
                    1.0, False, "observed_problem", "permit", _ids(state, "status"), _RANK["expired_permit"])
        )
    for condition in state.conditions:
        activity = _condition_activity(condition)
        if activity == "inactive":
            # "Hold released", "Corrections complete", "Satisfied": a resolved
            # condition is history, never a current blocker (review A1).
            continue
        if activity == "unknown":
            if condition.status:
                # An unrecognized agency label cannot become a blocker, and it
                # must never become a confirmed gate; it stays an uncertainty.
                result.uncertainties.append(
                    Uncertainty(
                        f"Condition {condition.description!r} has status "
                        f"{condition.status!r}, which is not a recognized agency "
                        "label; whether it is active is not established.",
                        "may change the blocker answer",
                        "conditions",
                        condition.evidence_ids,
                        False,
                    )
                )
            continue
        classification = (
            "confirmed_gate" if _condition_is_gate(condition) else "observed_problem"
        )
        result.blockers.append(
            Blocker("active_condition", condition.description, condition.source or "Conditions",
                    .95 if classification == "confirmed_gate" else .8, False, classification,
                    condition.affects_stage, condition.evidence_ids, _RANK["active_condition"])
        )
    for inspection in state.inspections:
        if not inspection.failed:
            # A passed result with a correction-required comment on the same
            # attempt is unresolved conflicting evidence, not a pass to report
            # or a failure to invent (Astra case F04).
            if inspection.passed and comment_requests_correction(inspection.comments):
                result.contradictions.append(
                    f"{inspection.type}: result is {inspection.raw_result!r} but the "
                    f"linked comment says {inspection.comments!r}; readiness suppressed"
                )
            continue
        raw_result = f" (result: {inspection.raw_result})" if inspection.raw_result and inspection.raw_result.lower() != "failed" else ""
        result.blockers.append(
            Blocker("failed_inspection", f"{inspection.type} inspection did not pass{raw_result}",
                    "Inspections", .95, False, "observed_problem", "inspection",
                    inspection.evidence_ids + inspection.comment_evidence_ids, _RANK["failed_inspection"])
        )
        same_type = [x for x in state.inspections if x.type.lower() == inspection.type.lower()]
        # Latest-attempt honesty (Astra case H05): same-type fail+pass attempts
        # leave "which outcome is current" unresolved only when ordering is NOT
        # establishable — a missing date on either attempt, or a date tie.
        # Differing known dates DO establish order (H01); known dates equal
        # remain ambiguous. No scheduling or correction candidates may be
        # emitted while ordering is unresolved — both would presume the failure
        # is the current outcome.
        unordered_conflict = any(
            x is not inspection
            and x.passed
            # different explicit scopes are different requirements (H04) —
            # their outcomes cannot resolve or order this attempt's
            and (
                inspection.scope is None
                or x.scope == inspection.scope
                or x.scope is None
            )
            and (
                not x.completed_date
                or not inspection.completed_date
                or x.completed_date == inspection.completed_date
            )
            for x in same_type
        )
        if unordered_conflict and not any(x.lifecycle_normalized == "SCHEDULED" for x in same_type):
            result.uncertainties.append(
                Uncertainty(
                    f"Multiple {inspection.type} attempts exist, but attempt order is not "
                    "established; which outcome is the latest is not established by the "
                    "available evidence.",
                    "affects current-outcome claims",
                    None,
                    inspection.evidence_ids,
                    True,
                )
            )
            continue
        reason = f"Address the cited {inspection.type} correction"
        if inspection.comments:
            reason += f" ({inspection.comments})"
        result.next_actions.append(
            NextActionCandidate(f"Address {inspection.type} correction", reason, .91, False,
                                "likely", ["correction completion"],
                                inspection.evidence_ids + inspection.comment_evidence_ids)
        )
        # A bare failure is not authority to schedule (Astra case H02): if a
        # same-type attempt is already scheduled, reinspection is arranged.
        if any(x.lifecycle_normalized == "SCHEDULED" for x in same_type):
            result.uncertainties.append(
                Uncertainty(
                    f"A {inspection.type} attempt is already scheduled; whether it addresses "
                    "the failed attempt is not established.",
                    "qualifies the recommendation",
                    None,
                    inspection.evidence_ids,
                    False,
                )
            )
        else:
            result.next_actions.append(
                NextActionCandidate(
                    f"Request reinspection: {inspection.type}",
                    "Consider a new attempt after the correction; eligibility and "
                    "requirement are not established by the failure alone.",
                    .72, False, "possible", ["correction completion"], inspection.evidence_ids,
                )
            )
    # A completed attempt with no recorded outcome (Astra case S04): the
    # lifecycle is a fact, the result is unknown — never guessed either way,
    # and the unknown outcome can change any current-outcome answer.
    for inspection in state.inspections:
        if inspection.lifecycle_normalized == "COMPLETED" and not inspection.result_normalized:
            result.uncertainties.append(
                Uncertainty(
                    f"The outcome of the completed {inspection.type} attempt is not shown "
                    "in the record.",
                    "affects outcome claims",
                    "inspections",
                    inspection.evidence_ids,
                    True,
                )
            )
    # Explicitly required inspections with no completing attempt: a candidate,
    # never a blocker (the catalog itself never becomes work).
    for fact in state.facts:
        if fact.field != "required_type" or not fact.value:
            continue
        name = str(fact.value)
        completed = any(
            x.type.lower() == name.lower()
            and (x.result_normalized in {"PASSED", "PARTIAL"} or x.lifecycle_normalized == "SCHEDULED")
            for x in state.inspections
        )
        if not completed:
            result.next_actions.append(
                NextActionCandidate(
                    f"Complete required inspection: {name}",
                    "The scheduling form marks this type (required) and no completed or "
                    "scheduled attempt for it is recorded.",
                    .75, False, "likely", [], fact.evidence_ids,
                )
            )
    for fee in state.fees:
        # Non-payment is a portal statement, never an assumption: a fee whose
        # payment state is absent stays unknown. The inherited rule asserted
        # "remains unpaid" beside its own "payment state not shown" fact
        # (review A7).
        if fee.paid is not False:
            if fee.due is True and fee.paid is None:
                result.uncertainties.append(
                    Uncertainty(
                        f"The record shows a fee ({fee.description}, "
                        f"{format_fee_amount(fee)}) as due, but does not show whether it "
                        "was paid.",
                        "qualifies the payment conclusion",
                        "fees",
                        fee.evidence_ids,
                        False,
                    )
                )
            continue
        outstanding = _fee_outstanding(fee)
        if outstanding is not None and outstanding <= 0:
            # A zeroed balance is not an outstanding amount (review A10).
            continue
        # Explicit gate evidence only: the portal words the gate on the fee row
        # or in a condition (Astra case B02). Words like "balance due" describe
        # money; they do not gate a stage (Astra case B01).
        gate = _payment_gate_evidence(fee, state)
        classification = "confirmed_gate" if gate else "potential_impediment"
        stage = gate[1] if gate else None
        result.blockers.append(
            Blocker("unpaid_fee",
                    f"{fee.description} remains unpaid ({format_fee_amount(fee)})",
                    "Fees", .95 if gate else .75, False, classification,
                    stage, fee.evidence_ids, _RANK["unpaid_fee"])
        )
        if gate is not None:
            result.next_actions.append(
                NextActionCandidate("Resolve outstanding fee",
                                    f"The portal gates a stage on payment ({gate[0]}).",
                                    .9, True, "required", [], fee.evidence_ids)
            )
            if stage is None:
                result.uncertainties.append(
                    Uncertainty(
                        "The record requires payment for a stage, but the wording does not "
                        "state which stage it gates.",
                        "qualifies the payment conclusion",
                        None,
                        fee.evidence_ids,
                        False,
                    )
                )
        else:
            result.uncertainties.append(
                Uncertainty(
                    "The fee is unpaid, but no portal evidence establishes that it blocks a "
                    "particular stage.",
                    "qualifies the payment conclusion",
                    None,
                    fee.evidence_ids,
                    False,
                )
            )
    for document in state.documents:
        if document.required is not True:
            continue
        status = " ".join((document.status or "").split()).strip().lower().rstrip(".")
        if status in _MISSING_DOCUMENT_LABELS:
            result.blockers.append(
                Blocker("missing_required_document", f"Required document not satisfied: {document.name}",
                        "Documents", .9, False, "observed_problem", None,
                        document.evidence_ids, _RANK["missing_required_document"])
            )
        elif status in _AMBIGUOUS_DOCUMENT_LABELS:
            # "Pending" is not "missing": the document requirement stays open
            # as an uncertainty until the portal states an outcome (review A11).
            result.uncertainties.append(
                Uncertainty(
                    f"Required document {document.name!r} is shown as {document.status!r}; "
                    "whether it satisfies the requirement is not established.",
                    "qualifies the document conclusion",
                    "documents",
                    document.evidence_ids,
                    False,
                )
            )
    for section, coverage in state.coverage.items():
        # Coverage states are knowledge limitations, never permit defects
        # (Astra case U02): they become uncertainties, not blockers.
        if coverage.status in {CoverageStatus.UNAVAILABLE, CoverageStatus.PARSE_FAILED}:
            result.uncertainties.append(
                Uncertainty(
                    f"{section} data is {coverage.status.value}; conclusions depending on it "
                    "are unavailable.",
                    "may change the answer",
                    section,
                    [],
                    True,
                )
            )
    # A pass resolves a prior failure only when the same scope is explicit
    # (Astra cases H01/H04) AND the pass is established as later (review A4).
    # With unknown ordering the failure stays open; with an *earlier* pass the
    # failure is the latest established outcome and must stay a blocker.
    for failed in (x for x in state.inspections if x.failed):
        matching_pass = next(
            (
                x
                for x in state.inspections
                if x.passed
                and x.type.lower() == failed.type.lower()
                and (not failed.inspection_id or failed.inspection_id != x.inspection_id)
            ),
            None,
        )
        if matching_pass is None:
            continue
        order = _attempt_order(failed, matching_pass)
        if order == "incomparable":
            continue
        if order == "later" and failed.scope and matching_pass.scope == failed.scope:
            result.blockers = [
                b
                for b in result.blockers
                if not (b.type == "failed_inspection" and failed.type in b.description)
            ]
            result.next_actions = [
                a
                for a in result.next_actions
                if failed.type.lower() not in a.action.lower()
            ]
        elif order == "earlier":
            # The portal's dates establish the failure as the current outcome;
            # the earlier pass neither resolves it nor removes its candidates.
            continue
        else:
            # Order may be fully established here (e.g. both dates known, pass
            # later) with only the *scope* unknown — or ordering itself may be
            # unknown. Name the actual missing premise instead of asserting a
            # fact-free "same scope": with dates now reliably extracted, the
            # wording must not contradict the evidence it cites (GLM review H07).
            if failed.scope is None and matching_pass.scope is None and order == "later":
                premise = ("both attempts' scopes are not shown, so whether the pass "
                           "addresses the failed attempt is not established")
            elif order == "unknown":
                premise = ("attempt order is not established by the available evidence "
                           "(missing or tied dates)")
            else:
                premise = (f"the pass's scope is shown as {matching_pass.scope!r} while the "
                           f"failed attempt's scope is shown as {failed.scope!r}, so whether "
                           "the pass addresses the failed attempt is not established")
            result.uncertainties.append(
                Uncertainty(
                    f"A {failed.type} pass exists, but {premise}; the earlier failure "
                    "cannot be resolved safely.",
                    "affects current-outcome claims",
                    None,
                    failed.evidence_ids + matching_pass.evidence_ids,
                    True,
                )
            )
    result.blockers.sort(key=_sort_key)
    return result


def _ids(state: PermitState, field: str) -> list[str]:
    return [fact.evidence_ids[0] for fact in state.facts if fact.field == field and fact.evidence_ids]
