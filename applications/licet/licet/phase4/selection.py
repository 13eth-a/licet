"""evidence-bound action selection"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Mapping

from licet.phase3.state import Evidence, ReasoningResult
from licet.phase4.actions import InspectionAction, InspectionSnapshot
from licet.phase4.matching import match_inspection_type


class SelectionStatus(str, Enum):
    SELECTED = "selected"
    NEEDS_DATA = "needs_data"
    AMBIGUOUS = "ambiguous"
    CONFLICTING = "conflicting"
    UNSUPPORTED = "unsupported"
    ALREADY_SCHEDULED = "already_scheduled"


@dataclass(frozen=True)
class InspectionOption:
    """observed portal option, with independently established prerequisites"""
    name: str
    eligible: bool | None = None
    prerequisites_satisfied: bool | None = None
    evidence_ids: tuple[str, ...] = ()
    required: bool | None = None
    requirement_evidence_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class SelectionContext:
    """one verified snapshot supplied by the coordinator, never by model prose"""
    permit_id: str
    record_key: str
    snapshot_id: str
    identity_verified: bool = False
    options: tuple[InspectionOption, ...] = ()
    evidence: Mapping[str, Evidence] = field(default_factory=dict)
    inspections: tuple[InspectionSnapshot, ...] = ()
    history_complete: bool = False
    catalog_complete: bool = False


@dataclass(frozen=True)
class ActionSelection:
    action: InspectionAction | None
    reason: str
    status: SelectionStatus = SelectionStatus.NEEDS_DATA
    record_key: str | None = None
    snapshot_id: str | None = None
    evidence_ids: tuple[str, ...] = ()
    needed_state: tuple[str, ...] = ()
    requires_confirmation: bool = False


# deliberately narrow migration grammar for phase 3's existing string field. “review inspection”, “do not
# cancel”, and “complete required inspection” are not executable verbs
_ACTION = re.compile(
    r"^(request (?:inspection|reinspection)|schedule inspection|"
    r"reschedule inspection|cancel inspection)\s*:\s*([^\n:]+)$", re.I
)
_REQUIRED_ACTION = re.compile(r"^complete required inspection\s*:\s*([^\n:]+)$", re.I)


def select_inspection_action(
    reasoning: ReasoningResult,
    *,
    permit_id: str,
    confidence_threshold: float = 0.90,
    context: SelectionContext | None = None,
    requested_action: InspectionAction | None = None,
) -> ActionSelection:
    """select one supported operation; preserve constraints and abstain on gaps"""
    if not math.isfinite(confidence_threshold) or not 0 <= confidence_threshold <= 1:
        raise ValueError("confidence threshold must be finite and between zero and one")

    def stop(status, reason, *needed):
        return ActionSelection(None, reason, status, needed_state=tuple(needed))

    if reasoning.contradictions or reasoning.answerability == "conflicting":
        return stop(SelectionStatus.CONFLICTING, "conflicting evidence requires reconciliation", "conflicting inspection evidence")
    if context is None or not context.identity_verified:
        return stop(SelectionStatus.NEEDS_DATA, "verified selection context is required", "verified record and snapshot")
    if (not context.record_key or not context.snapshot_id or permit_id != context.permit_id
            or reasoning.record_key != context.record_key or reasoning.snapshot_id != context.snapshot_id):
        return stop(SelectionStatus.CONFLICTING, "record or snapshot does not match the verified selection context")
    if reasoning.answerability not in {"answered", "partial"} or (
        reasoning.answerability != "partial" and any(u.blocks_answer for u in reasoning.uncertainties)
    ):
        return stop(SelectionStatus.NEEDS_DATA, "required inspection state is incomplete", "outstanding Phase 3 retrieval")
    if reasoning.answerability == "partial" and any(
        u.blocks_answer and (u.needed_section or "").casefold() == "inspections"
        for u in reasoning.uncertainties
    ):
        return stop(SelectionStatus.NEEDS_DATA, "inspection evidence is incomplete", "complete current inspection history")
    # a broad readiness interpretation can be partial because non-inspection sections remain unread
    if reasoning.answerability == "answered" and reasoning.needed_sections:
        return stop(SelectionStatus.NEEDS_DATA, "required inspection state is incomplete", "outstanding Phase 3 retrieval")
    if reasoning.answerability == "partial" and any(
        str(section.get("section") or "").casefold() == "inspections"
        for section in reasoning.needed_sections
    ):
        return stop(SelectionStatus.NEEDS_DATA, "inspection evidence is incomplete", "complete current inspection history")
    if requested_action and requested_action.permit_id != permit_id:
        return stop(SelectionStatus.CONFLICTING, "requested action addresses a different permit")

    # do not remove low-confidence or conditional competitors to manufacture a unique winner
    candidates = []
    for candidate in reasoning.next_actions:
        match = _ACTION.fullmatch(candidate.action.strip())
        required_match = _REQUIRED_ACTION.fullmatch(candidate.action.strip())
        if match:
            verb, name = match.groups()
            kind = "cancel" if verb.lower().startswith("cancel") else (
                "reschedule" if verb.lower().startswith("reschedule") else "schedule")
            candidates.append((candidate, kind, name.strip(), False))
        elif required_match:
            candidates.append((candidate, "schedule", required_match.group(1).strip(), True))
    if not candidates:
        return stop(SelectionStatus.UNSUPPORTED, "no explicit supported inspection operation was proposed")
    if len(candidates) != 1:
        return stop(SelectionStatus.AMBIGUOUS, "multiple inspection operations remain plausible", "unique inspection target and operation")
    candidate, kind, name, explicit_required_candidate = candidates[0]
    if (not math.isfinite(candidate.confidence) or not 0 <= candidate.confidence <= 1
            or candidate.preconditions or candidate.requirement_strength not in {"required", "likely"}
            or (explicit_required_candidate and candidate.confidence < 0.75)
            or (not explicit_required_candidate and candidate.confidence < confidence_threshold)):
        return stop(SelectionStatus.NEEDS_DATA, "candidate remains conditional, possible, or insufficiently supported", "candidate prerequisites and support")
    if reasoning.answerability == "partial" and not (
        explicit_required_candidate or requested_action is not None
    ):
        return stop(SelectionStatus.NEEDS_DATA,
                    "partial reasoning does not support an executable inspection candidate",
                    "complete readiness evidence")

    def supported(ids):
        return bool(ids) and all(
            eid in context.evidence and context.evidence[eid].record_key == context.record_key
            for eid in ids
        )

    if not supported(candidate.evidence_ids):
        return stop(SelectionStatus.NEEDS_DATA, "candidate lacks same-record supporting evidence", "candidate evidence")
    portal_name = match_inspection_type(name, [option.name for option in context.options])
    if portal_name is None:
        return stop(SelectionStatus.AMBIGUOUS, "inspection type has no unique exact portal match", "unique portal inspection option")
    option = next(option for option in context.options if option.name == portal_name)
    if option.eligible is not True or option.prerequisites_satisfied is not True or not supported(option.evidence_ids):
        return stop(SelectionStatus.NEEDS_DATA, "eligibility or prerequisites are not independently established", "eligibility and prerequisite evidence")
    if not context.history_complete:
        return stop(SelectionStatus.NEEDS_DATA, "current inspection history is incomplete", "complete current inspection history")
    if explicit_required_candidate and (
        option.required is not True
        or not context.catalog_complete
        or not option.requirement_evidence_ids
        or not set(option.requirement_evidence_ids) <= set(candidate.evidence_ids)
        or not supported(option.requirement_evidence_ids)
    ):
        return stop(SelectionStatus.NEEDS_DATA,
                    "required inspection candidate lacks a complete, explicit portal marker",
                    "complete catalog and same-record required-type evidence")
    if reasoning.answerability == "partial" and not explicit_required_candidate and requested_action is None:
        return stop(SelectionStatus.NEEDS_DATA, "partial reasoning lacks an evidence-bound inspection requirement",
                    "complete readiness evidence")

    operation = requested_action.action_type.strip().lower().removesuffix("_inspection") if requested_action else kind
    if requested_action and (operation != kind or match_inspection_type(
            requested_action.inspection_type or "", [portal_name]) is None):
        return stop(SelectionStatus.CONFLICTING, "proposal would change the user's operation or inspection target")
    existing_id = requested_action.existing_inspection_id if requested_action else None
    if any(row.permit_id != permit_id for row in context.inspections):
        return stop(SelectionStatus.CONFLICTING, "inspection history contains a different permit")
    matching = [row for row in context.inspections if match_inspection_type(row.inspection_type, [portal_name])]
    if kind == "schedule":
        if existing_id:
            return stop(SelectionStatus.CONFLICTING, "scheduling a new attempt cannot target an existing appointment")
        if any(row.is_scheduled for row in matching):
            return stop(SelectionStatus.ALREADY_SCHEDULED, "inspection already scheduled; do not change schedule into reschedule")
        known = {"passed", "failed", "completed", "closed", "cancelled", "canceled"}
        if any(row.status.strip().lower() not in known for row in matching):
            return stop(SelectionStatus.NEEDS_DATA, "pending or unknown attempt could duplicate a request", "pending attempt disposition")
    else:
        if not existing_id:
            return stop(SelectionStatus.NEEDS_DATA, "reschedule/cancel requires an explicit existing inspection ID", "existing inspection ID")
        targets = [row for row in matching if row.inspection_id == existing_id]
        if len(targets) != 1 or not targets[0].is_scheduled:
            return stop(SelectionStatus.AMBIGUOUS, "existing scheduled inspection is not uniquely established", "current target appointment")

    # preserve all user constraints verbatim; the implementation’s date layer resolves them
    action = replace(requested_action, action_type=kind, inspection_type=portal_name,
                     constraints=list(requested_action.constraints)) if requested_action else InspectionAction(kind, permit_id, portal_name)
    return ActionSelection(action, "one evidence-supported target; policy and pre-action verification still required",
        SelectionStatus.SELECTED, context.record_key, context.snapshot_id,
        tuple(dict.fromkeys([*candidate.evidence_ids, *option.evidence_ids, *option.requirement_evidence_ids])),
        requires_confirmation=candidate.requires_confirmation or kind == "cancel")
