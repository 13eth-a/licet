"""question-specific deterministic interpretation before any model reasoning"""
from __future__ import annotations

import hashlib

from licet.phase3.routing import needed_sections, route_question
from licet.phase3.rules import derive_deterministic_findings, explicit_expiration_event, format_fee_amount
from licet.phase3.state import (
    Claim,
    ConfidenceBand,
    CoverageStatus,
    FactKind,
    Fee,
    PermitState,
    ReasoningResult,
    Section,
    Uncertainty,
)


def understand(state: PermitState, question: str, *, snapshot_id: str = "snapshot") -> ReasoningResult:
    result = ReasoningResult(state.record_key, snapshot_id, question, "answered")
    route = route_question(question, state)
    missing = needed_sections(question, state)
    result.needed_sections = missing
    findings = derive_deterministic_findings(state)
    result.blockers = findings.blockers
    result.next_actions = findings.next_actions
    result.contradictions = findings.contradictions
    # uncertainties carry their own blocking flag and affected section; the route's first section is only
    # a default when none was attributed
    result.uncertainties = list(findings.uncertainties)

    lowered = question.lower()
    status_relevant = (
        "status" in lowered
        or route.sections[0] == Section.OVERVIEW.value
        or "why" in lowered
        or "block" in lowered
        or "moving forward" in lowered
    )
    if state.status is not None and status_relevant:
        result.claims.append(
            _claim(FactKind.FACT, f"The portal reports permit status {state.status}.", _ids(state, "status"), "portal_status")
        )

    fee_relevant = "fee" in lowered or "balance" in lowered or "paid" in lowered or "payment" in lowered or "block" in lowered or "approval" in lowered or "why" in lowered
    if fee_relevant:
        for fee in state.fees:
            result.claims.append(_claim(FactKind.FACT, _fee_statement(fee), fee.evidence_ids, "fee_state"))
        for blocker in findings.blockers:
            if blocker.type == "unpaid_fee":
                # the classification stays attached to the blocker; the claim is only the money fact
                # (reasoning contract b01/b02)
                result.claims.append(_claim(FactKind.FACT, blocker.description, blocker.evidence_ids, "unpaid_fee"))

    inspection_relevant = (
        "inspection" in lowered
        or "inspector" in lowered
        or "ready" in lowered
        or findings.blockers
        or "why" in lowered
    )
    documents_relevant = any(
        word in lowered
        for word in ("document", "documents", "plan", "plans", "attachment", "attachments", "upload", "uploads")
    )
    # blocker/readiness/next-step questions route through conditions + history; that is the same relevance
    # signal the missing-section cases use
    blocker_relevant = Section.CONDITIONS.value in route.sections and Section.HISTORY.value in route.sections
    if inspection_relevant:
        for inspection in state.inspections:
            if inspection.result_normalized or inspection.lifecycle_normalized:
                result.claims.append(
                    _claim(FactKind.FACT, _inspection_statement(inspection), inspection.evidence_ids, "inspection_lifecycle_result")
                )
            if inspection.comments:
                result.claims.append(
                    _claim(FactKind.FACT, f'{inspection.type} inspector comment: "{inspection.comments}"', inspection.comment_evidence_ids, "linked_inspector_comment")
                )
        for fact in state.facts:
            if fact.field == "required_type" and fact.value:
                result.claims.append(
                    _claim(FactKind.FACT, f'The scheduling form marks {fact.value} as (required).', fact.evidence_ids, "explicit_requirement")
                )

    # a complete observed-empty section supports "no entries shown" — a positive claim about coverage, not
    # about requirements and never about a global "no blockers" (checklist/u01/u05)
    for section, relevant in (
        (Section.INSPECTIONS.value, inspection_relevant),
        (Section.FEES.value, fee_relevant),
        (Section.DOCUMENTS.value, documents_relevant),
        (Section.CONDITIONS.value, blocker_relevant),
        (Section.HISTORY.value, blocker_relevant),
    ):
        if not relevant or _section_items(state, section):
            continue
        coverage = state.coverage.get(section)
        if coverage is None or coverage.status not in {CoverageStatus.COMPLETE, CoverageStatus.EXPLICITLY_EMPTY}:
            continue
        result.claims.append(
            _claim(
                FactKind.FACT,
                f"No {_EMPTY_ENTRY_LABEL[section]} entries are shown in the record's "
                f"{section} section as of the latest read.",
                _section_evidence_ids(state, section),
                "observed_empty_section",
            )
        )

    # a stale-overview contradiction (overview issued + dated explicit expiration event, oracle s03): a
    # conflict the sources themselves do not resolve
    if (
        state.status_normalized == "ISSUED"
        and any(explicit_expiration_event(event.event) for event in state.history)
        and not any(
            "renew" in (event.event or "").lower() or "reissu" in (event.event or "").lower()
            or "reinstate" in (event.event or "").lower()
            for event in state.history
        )
    ):
        result.contradictions.append(
            "overview reports the permit Issued while history records an explicit "
            "expiration event; no renewal evidence resolves them (possible stale overview)"
        )

    # rejected foreign observations are data-hygiene events the answer must surface (oracle u04): evidence
    # was refused, so say so explicitly
    for rejection in state.rejected_observations:
        if rejection not in result.contradictions:
            result.contradictions.append(
                "rejected cross-record evidence: " + rejection
            )

    if result.contradictions:
        result.answerability = "conflicting"
    elif any(u.blocks_answer for u in result.uncertainties):
        # uncertainties that prevent answering (missing premises, unresolved ordering, unavailable data)
        # make the answer partial even when some supported claims exist; they always remain listed
        status_question = (
            "status" in lowered and not any(word in lowered for word in ("block", "ready", "why", "next"))
        )
        # a quote question ("what did the inspector say?") is answered by the quoted evidence itself;
        # outcome uncertainty qualifies, not blocks (f03)
        quote_question = "say" in lowered or "comment" in lowered
        result.answerability = (
            "answered"
            if (status_question or quote_question) and result.claims
            else ("partial" if result.claims else "needs_data")
        )
    elif missing and not result.claims and not result.next_actions and not result.blockers and not state.fees:
        result.answerability = "needs_data"
    elif missing and not result.next_actions and not result.blockers:
        result.answerability = "partial"
    # a readiness verdict ("is this ready to move forward?") is a publication gate, not a coverage
    # question: it needs conditions/history covered and no blocking uncertainties, whatever route the
    # phrasing took (oracle u05)
    if ("ready" in lowered or "move forward" in lowered) and result.answerability != "conflicting":
        covered = all(
            state.coverage.get(section) is not None
            and state.coverage[section].complete
            for section in (Section.CONDITIONS.value, Section.HISTORY.value)
        )
        if not covered or any(u.blocks_answer for u in result.uncertainties):
            result.answerability = "partial" if result.claims else "needs_data"
    return result


_EMPTY_ENTRY_LABEL = {
    Section.INSPECTIONS.value: "inspection",
    Section.FEES.value: "fee",
    Section.DOCUMENTS.value: "document",
    Section.CONDITIONS.value: "condition",
    Section.HISTORY.value: "history",
}


def _section_evidence_ids(state: PermitState, section: str) -> list[str]:
    return [eid for eid, evidence in state.evidence.items() if evidence.section == section]


def _section_items(state: PermitState, section: str) -> list:
    return {
        Section.INSPECTIONS.value: state.inspections,
        Section.FEES.value: state.fees,
        Section.DOCUMENTS.value: state.documents,
        Section.CONDITIONS.value: state.conditions,
        Section.HISTORY.value: state.history,
    }[section]


def _claim(kind: FactKind, statement: str, evidence_ids: list[str], reason: str) -> Claim:
    return Claim(
        "claim_" + hashlib.sha1(statement.encode()).hexdigest()[:10],
        kind,
        statement,
        evidence_ids,
        [],
        ConfidenceBand.HIGH if evidence_ids else ConfidenceBand.LOW,
        reason,
    )


def _ids(state: PermitState, field: str) -> list[str]:
    return [eid for fact in state.facts if fact.field == field for eid in fact.evidence_ids]


def _inspection_statement(inspection: object) -> str:
    parts = [inspection.type]
    if inspection.lifecycle_normalized:
        parts.append(inspection.lifecycle_normalized.lower())
    if inspection.result_normalized:
        parts.append(inspection.result_normalized.lower())
    return "Inspection " + " — ".join(parts)


def _fee_statement(fee: Fee) -> str:
    state = "unpaid" if fee.paid is False else ("paid" if fee.paid is True else "payment state not shown")
    return f"Fee {fee.description}: {format_fee_amount(fee)}, {state}."
