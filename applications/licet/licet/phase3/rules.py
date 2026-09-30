"""deterministic phase 3 findings"""
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
# only an explicit administrative hold is a *gate*; other active conditions are observed problems with
# unknown scope
_GATE_CONDITION_LABELS = {
    "hold", "on hold", "active hold", "administrative hold", "suspended",
    "suspension", "stop work", "stop work order",
}


def _condition_label(condition) -> str:
    return " ".join((condition.status or "").split()).strip().lower().rstrip(".")


def _condition_activity(condition) -> str:
    """``active`` / ``inactive`` / ``unknown`` from the portal's own label"""
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


# a required document is a blocker only when the portal states the requirement is unmet. "pending"/"under
# review" say nothing about satisfaction (review a11)
_MISSING_DOCUMENT_LABELS = {
    "missing", "not received", "not submitted", "not uploaded", "outstanding",
    "incomplete", "required", "delinquent", "void",
}
_AMBIGUOUS_DOCUMENT_LABELS = {
    "pending", "pending review", "under review", "in review", "processing",
    "awaiting", "expired",
}


def _fee_outstanding(fee) -> float | None:
    """the amount that is actually still owed, number first"""
    return fee.balance if fee.balance is not None else fee.amount


def format_fee_amount(fee) -> str:
    outstanding = _fee_outstanding(fee)
    if outstanding is not None:
        return f"${outstanding:,.2f}"
    return fee.amount_text or "an unquantified amount"


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
    """return ``(gate wording, affected stage)`` when the portal states one"""
    if fee.gate_text and not _NEGATED_PAYMENT_RE.search(fee.gate_text.lower()):
        return fee.gate_text, _stage_from_text(fee.gate_text)
    for condition in state.conditions:
        text = " ".join(f"{condition.description} {condition.status or ''}".split()).lower()
        if _NEGATED_PAYMENT_RE.search(text):
            continue
        if _PAYMENT_GATE_RE.search(text):
            return text, _stage_from_text(text)
    return None


_NEGATED_CORRECTION_RE = re.compile(
    r"\b(no|not|none|without|zero|free of|clear of)\b[^.;]{0,24}\bcorrections?\b"
)
_CORRECTION_REQUIRED_RE = re.compile(
    r"\bcorrections?\b[^.;]{0,24}\b(required|needed|outstanding|pending|must|shall|requested)\b"
)


def comment_requests_correction(text: str) -> bool:
    """whether a linked comment states that corrections are *outstanding*"""
    lowered = (text or "").lower()
    if _NEGATED_CORRECTION_RE.search(lowered):
        return False
    return bool(_CORRECTION_REQUIRED_RE.search(lowered))


# "permit expired" is an explicit expiration event; "permit not expired" is the opposite statement and
# must not be read as one (review a6)
_NEGATED_EXPIRY_RE = re.compile(r"\b(no|not|never|without|before|prior to)\b[^.;]{0,24}\bexpir")


def explicit_expiration_event(text: str) -> bool:
    lowered = (text or "").lower()
    if _NEGATED_EXPIRY_RE.search(lowered):
        return False
    return bool(re.search(r"\bexpired\b", lowered))


def _iso_date(value: str | None):
    if not value:
        return None
    try:
        return _dt.date.fromisoformat(str(value).strip())
    except ValueError:
        return None


def _attempt_order(failed, passed) -> str:
    """``later`` / ``earlier`` / ``unknown`` / ``incomparable``"""
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
    # explicit expiration is a portal fact; a configured date alone is not an expired state (architecture
    # review case s02)
    if state.status_normalized == "EXPIRED":
        result.blockers.append(
            Blocker("expired_permit", "Portal reports the permit as expired", "Overview",
                    1.0, False, "observed_problem", "permit", _ids(state, "status"), _RANK["expired_permit"])
        )
    for condition in state.conditions:
        activity = _condition_activity(condition)
        if activity == "inactive":
            # "hold released", "corrections complete", "satisfied": a resolved condition is history, never
            # a current blocker (review a1)
            continue
        if activity == "unknown":
            if condition.status:
                # an unrecognized agency label cannot become a blocker, and it must never become a
                # confirmed gate; it stays an uncertainty
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
            # a passed result with a correction-required comment on the same attempt is unresolved
            # conflicting evidence, not a pass to report or a failure to invent (architecture review case
            # f04)
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
        # latest-attempt honesty (architecture review case h05): same-type fail+pass attempts leave "which
        # outcome is current" unresolved only when ordering is not establishable — a missing date on
        # either attempt, or a date tie
        unordered_conflict = any(
            x is not inspection
            and x.passed
            # different explicit scopes are different requirements (h04) — their outcomes cannot resolve
            # or order this attempt's
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
        # a bare failure is not authority to schedule (architecture review case h02): if a same-type
        # attempt is already scheduled, reinspection is arranged
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
    # a completed attempt with no recorded outcome (architecture review case s04): the lifecycle is a
    # fact, the result is unknown — never guessed either way, and the unknown outcome can change any
    # current-outcome answer
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
    # explicitly required inspections with no completing attempt: a candidate, never a blocker (the
    # catalog itself never becomes work)
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
        # non-payment is a portal statement, never an assumption: a fee whose payment state is absent
        # stays unknown
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
            # a zeroed balance is not an outstanding amount (review a10)
            continue
        # explicit gate evidence only: the portal words the gate on the fee row or in a condition
        # (architecture review case b02)
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
            # "pending" is not "missing": the document requirement stays open as an uncertainty until the
            # portal states an outcome (review a11)
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
        # coverage states are knowledge limitations, never permit defects (architecture review case u02):
        # they become uncertainties, not blockers
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
    # a pass resolves a prior failure only when the same scope is explicit (architecture review cases
    # h01/h04) and the pass is established as later (review a4)
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
            continue
        else:
            # order may be fully established here (e.g. both dates known, pass later) with only the
            # *scope* unknown — or ordering itself may be unknown
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
