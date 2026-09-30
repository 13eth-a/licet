"""the architecture review’s action-selection cases; no browser or mutation capability."""
from dataclasses import replace

import pytest

from licet.phase3.state import Evidence, NextActionCandidate, ReasoningResult, Uncertainty
from licet.phase4.actions import InspectionAction, InspectionSnapshot
from licet.phase4.selection import (
    InspectionOption, SelectionContext, SelectionStatus, select_inspection_action,
)

KEY = "NULLISLAND/Building/REC26/00000/00001"


def reasoning(action="Request inspection: Rough Electrical", **kwargs):
    candidate = NextActionCandidate(action, "supported target", .94,
                                    requirement_strength="likely", evidence_ids=["target"])
    return ReasoningResult(KEY, "s1", "what should I schedule", "answered",
                           next_actions=[candidate], **kwargs)


def context(**kwargs):
    base = SelectionContext("P-1", KEY, "s1", True,
        (InspectionOption("Electrical - Rough", True, True, ("eligibility",)),),
        {name: Evidence(name, "inspections", record_key=KEY) for name in ("target", "eligibility")},
        history_complete=True, catalog_complete=True)
    return replace(base, **kwargs)


def select(result=None, ctx=None, **kwargs):
    return select_inspection_action(result or reasoning(), permit_id="P-1",
                                     context=ctx or context(), **kwargs)


def test_supported_choice_preserves_portal_spelling_and_provenance():
    selected = select()
    assert selected.status is SelectionStatus.SELECTED
    assert selected.action == InspectionAction("schedule", "P-1", "Electrical - Rough")
    assert selected.record_key == KEY and selected.snapshot_id == "s1"
    assert selected.evidence_ids == ("target", "eligibility")
    assert not selected.requires_confirmation


@pytest.mark.parametrize("label", ["Rough Electrical", "Electrical Rough", "Electrical - Rough", "Rough Electric"])
def test_only_supported_label_variants_select(label):
    assert select(reasoning(f"Request inspection: {label}")).action.inspection_type == "Electrical - Rough"


@pytest.mark.parametrize("action", [
    "Review inspection: Rough Electrical", "Do not cancel inspection: Rough Electrical",
    "Address inspection correction: Rough Electrical", "Complete required inspection: Rough Electrical",
    "Request inspection: Rough Electrical then pay fee", "Request inspection: Electrical",
    "Request inspection: Rough Electrical\nCancel inspection: Rough Plumbing",
])
def test_prose_or_similarity_cannot_become_mutation(action):
    assert select(reasoning(action)).action is None


@pytest.mark.parametrize("value", [.89, float("nan"), float("inf"), 1.1, -1])
def test_invalid_or_low_candidate_confidence_stops(value):
    result = reasoning()
    result.next_actions[0].confidence = value
    assert select(result).action is None


@pytest.mark.parametrize("threshold", [float("nan"), float("inf"), -1, 1.1])
def test_invalid_threshold_is_rejected(threshold):
    with pytest.raises(ValueError):
        select(confidence_threshold=threshold)


def test_possible_recommendation_never_promoted_by_high_confidence():
    result = reasoning()
    result.next_actions[0].requirement_strength = "possible"
    result.next_actions[0].confidence = 1
    assert select(result).action is None


def test_failed_rough_correction_does_not_authorize_reinspection():
    result = reasoning("Request reinspection: Rough Electrical")
    result.next_actions[0].preconditions = ["correction completion"]
    selected = select(result)
    assert selected.action is None and selected.needed_state


def test_lower_confidence_competitor_is_not_filtered_out():
    result = reasoning()
    result.next_actions.append(NextActionCandidate("Request inspection: Rough Plumbing", "unresolved", .7))
    assert select(result).status is SelectionStatus.AMBIGUOUS


@pytest.mark.parametrize("field,value", [
    ("identity_verified", False), ("snapshot_id", "stale"),
    ("record_key", "another record"), ("permit_id", "P-2"), ("history_complete", False),
])
def test_identity_snapshot_and_coverage_gates(field, value):
    assert select(ctx=context(**{field: value})).action is None


@pytest.mark.parametrize("answerability", ["partial", "needs_data", "conflicting", "invented"])
def test_unanswered_reasoning_stops(answerability):
    result = reasoning()
    result.answerability = answerability
    assert select(result).action is None


def test_needed_section_or_conflict_or_missing_prerequisite_stops():
    for result in [
        reasoning(needed_sections=[{"section": "inspections"}]),
        reasoning(contradictions=["passed result with correction comment"]),
        reasoning(uncertainties=[Uncertainty("missing prerequisite", "blocks", blocks_answer=True)]),
    ]:
        assert select(result).action is None


@pytest.mark.parametrize("missing", ["target", "eligibility"])
def test_dangling_evidence_cannot_support_action(missing):
    ctx = context()
    ctx.evidence.pop(missing)
    assert select(ctx=ctx).action is None


def test_foreign_record_evidence_is_rejected():
    ctx = context()
    ctx.evidence["target"].record_key = "other"
    assert select(ctx=ctx).action is None


@pytest.mark.parametrize("eligible,prerequisites", [(None, True), (False, True), (True, None), (True, False)])
def test_requestable_is_not_prerequisite_satisfied(eligible, prerequisites):
    ctx = context(options=(InspectionOption("Rough Electrical", eligible, prerequisites, ("eligibility",)),))
    assert select(ctx=ctx).action is None


def test_duplicate_exact_portal_options_remain_ambiguous():
    ctx = context()
    assert select(ctx=replace(ctx, options=ctx.options * 2)).status is SelectionStatus.AMBIGUOUS


def appointment(status="Scheduled", inspection_id="i1", permit_id="P-1"):
    return InspectionSnapshot(permit_id, inspection_id, "Rough Electrical", status, "2026-09-24")


def test_schedule_does_not_silently_turn_into_reschedule():
    assert select(ctx=context(inspections=(appointment(),))).status is SelectionStatus.ALREADY_SCHEDULED


@pytest.mark.parametrize("status", ["Pending", "Unknown", "Requested"])
def test_unresolved_attempt_prevents_duplicate_new_request(status):
    assert select(ctx=context(inspections=(appointment(status),))).action is None


@pytest.mark.parametrize("kind", ["cancel", "reschedule"])
def test_existing_action_needs_explicit_unique_scheduled_id(kind):
    result = reasoning(f"{kind.title()} inspection: Rough Electrical")
    ctx = context(inspections=(appointment(),))
    assert select(result, ctx).action is None
    request = InspectionAction(kind, "P-1", "Rough Electrical", existing_inspection_id="i1")
    selected = select(result, ctx, requested_action=request)
    assert selected.action.existing_inspection_id == "i1"
    assert selected.requires_confirmation == (kind == "cancel")
    assert select(result, replace(ctx, inspections=ctx.inspections * 2), requested_action=request).action is None
    assert select(result, context(inspections=(appointment("Passed"),)), requested_action=request).action is None


def test_user_action_constraints_are_preserved_without_date_reasoning():
    request = InspectionAction("schedule", "P-1", "Rough Electric", "2026-09-24",
        "2026-09-21", "2026-09-27", constraints=["AM only", "do not expand window"])
    selected = select(requested_action=request)
    assert selected.action == replace(request, inspection_type="Electrical - Rough")
    assert selected.action.constraints is not request.constraints


@pytest.mark.parametrize("proposed", [
    InspectionAction("schedule", "P-2", "Rough Electrical"),
    InspectionAction("cancel", "P-1", "Rough Electrical"),
    InspectionAction("schedule", "P-1", "Final Electrical"),
    InspectionAction("schedule", "P-1", "Rough Electrical", existing_inspection_id="i1"),
])
def test_reasoning_cannot_change_user_intent(proposed):
    assert select(requested_action=proposed).action is None


def test_partial_readiness_can_identify_only_a_fully_supported_required_type():
    result = reasoning("Complete required inspection: Rough Electrical")
    result.answerability = "partial"
    result.needed_sections = [{"section": "conditions", "reason": "not required for target identity"}]
    result.next_actions[0].requirement_strength = "required"
    ctx = context(options=(InspectionOption(
        "Rough Electrical", True, True, ("eligibility",), required=True,
        requirement_evidence_ids=("requirement",),
    ),), evidence={
        "target": Evidence("target", "inspections", record_key=KEY),
        "eligibility": Evidence("eligibility", "inspections", record_key=KEY),
        "requirement": Evidence("requirement", "inspections", "(required)", record_key=KEY),
    }, catalog_complete=True)
    result.next_actions[0].evidence_ids.append("requirement")
    selected = select(result, ctx)
    assert selected.status is SelectionStatus.SELECTED
    assert selected.action.inspection_type == "Rough Electrical"
    assert "requirement" in selected.evidence_ids


def test_partial_answer_with_inspection_gap_still_stops_selection():
    result = reasoning("Complete required inspection: Rough Electrical")
    result.answerability = "partial"
    result.needed_sections = [{"section": "inspections"}]
    result.next_actions[0].requirement_strength = "required"
    ctx = context(options=(InspectionOption(
        "Rough Electrical", True, True, ("eligibility",), required=True,
        requirement_evidence_ids=("requirement",),
    ),), evidence={
        "target": Evidence("target", "inspections", record_key=KEY),
        "eligibility": Evidence("eligibility", "inspections", record_key=KEY),
        "requirement": Evidence("requirement", "inspections", "(required)", record_key=KEY),
    }, catalog_complete=True)
    result.next_actions[0].evidence_ids.append("requirement")
    assert select(result, ctx).action is None


def test_partial_readiness_does_not_select_from_offer_alone_or_incomplete_catalog():
    result = reasoning("Complete required inspection: Rough Electrical")
    result.answerability = "partial"
    result.next_actions[0].requirement_strength = "required"
    ctx = context(options=(InspectionOption("Rough Electrical", True, True, ("eligibility",),
                                             required=False),))
    assert select(result, ctx).action is None
    ctx = context(options=(InspectionOption(
        "Rough Electrical", True, True, ("eligibility",), required=True,
        requirement_evidence_ids=("requirement",),
    ),), catalog_complete=False)
    ctx.evidence["requirement"] = Evidence("requirement", "inspections", "(required)", record_key=KEY)
    result.next_actions[0].evidence_ids.append("requirement")
    assert select(result, ctx).action is None


def test_read_only_phase3_flag_stays_false():
    result = reasoning()
    assert select(result).action is not None
    assert result.execution_allowed is False


def test_candidate_confirmation_requirement_is_preserved():
    result = reasoning()
    result.next_actions[0].requires_confirmation = True
    assert select(result).requires_confirmation
