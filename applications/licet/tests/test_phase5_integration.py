"""real phase 2 lookup, phase 3 retrieval/reasoning and phase 4 executor, fake i/o"""
import asyncio
from dataclasses import replace

import pytest

from licet.phase3.runner import Phase3RetrievalRunner
from licet.phase3.state import Coverage, CoverageStatus, Evidence, PermitState, ReasoningResult
from licet.phase4.actions import InspectionSnapshot
from licet.phase4.selection import InspectionOption, SelectionContext
from licet.phase5 import *
from licet.phase5.state import World, operation_key
from licet.phase5.planner import next_actions
from licet.eval.phase5_live import LicetCapabilities, Preflight
from licet.safety.policy import Environment
from tests.conftest import FakeClient, runner_for, detail_page, search_form, gs_field


class Portal:
    # declares the sandbox explicitly: phase 6 refuses every mutation from an unidentified portal, so the
    # integration portal must identify itself
    environment = Environment.SANDBOX

    def __init__(self, key):
        self.key, self.submits, self.current = key, [], None

    def read_inspection_state(self, permit, inspection_type, inspection_id=None):
        return self.current or InspectionSnapshot(permit, None, inspection_type, "Not Scheduled", record_key=self.key)

    def submit_inspection_action(self, action, *, portal_type, selected_date=None):
        self.submits.append(action)
        self.current = InspectionSnapshot(action.permit_id, "i1", portal_type, "Scheduled", selected_date, record_key=self.key)
        return "CONF-1"


async def integrated_run():
    detail = detail_page(number="000000014", status="Issued")
    detail["text"] += "\nYou have not added any inspections."
    client = FakeClient([search_form(fields=[gs_field("txtGSPermitNumber")]), detail, detail, detail])
    lookup = runner_for(client)
    retrieval = Phase3RetrievalRunner(lookup.dispatcher)
    key = "NULLISLAND/Building/REC26/00000/00014"
    portal = Portal(key)

    def context(world):
        return SelectionContext(world.permit_id, world.record_key, world.snapshot_id, True,
            (InspectionOption("Rough Electrical", True, True, ("eligibility",)),),
            {"eligibility": Evidence("eligibility", "inspections", "Rough Electrical is eligible; no unmet prerequisites", record_key=key)},
            history_complete=True)

    def preflight(world):
        return Preflight(key, world.snapshot_id, operation_key(world), True,
                         ("2026-09-24",), 0, False)

    capabilities = LicetCapabilities(lookup=lookup, retrieval=retrieval, selection_context=context,
                                    preflight=preflight, portal=portal)
    goal = parse_goal("Schedule Rough Electrical inspection for permit 000000014.")
    run = await GoalPlanner(capabilities).run(goal)
    return run, portal, capabilities


def test_discovery_understanding_selection_execution_and_verification():
    run, portal, capabilities = asyncio.run(integrated_run())
    assert run.status == Status.SUCCESS, run.report()
    assert len(portal.submits) == 1
    assert run.world.result.success and run.world.result.verified
    assert run.world.verified_inspection.record_key == run.world.record_key
    assert capabilities.audits[-1].snapshot_id == run.world.snapshot_id
    assert run.trace[-1]["action"] == "VERIFY_STATE"


@pytest.mark.parametrize(
    ("inspection_type", "portal_required", "should_check"),
    [("Rough", False, True), (None, True, True), (None, False, False)],
)
def test_bld26_empty_history_maps_supported_target_to_read_only_preflight(
    inspection_type, portal_required, should_check,
):
    """replay bld26's empty inspection history and the wizard's offer/requirement evidence offline"""
    permit_id = "BLD26-00469"
    key = "NULLISLAND/Building/REC26/00000/000QD"
    snapshot = "bld26-empty-history-snapshot"
    goal = Goal(
        objective=(f"Read only: inspect permit {permit_id} and its {inspection_type} availability."
                   if inspection_type else f"Read only: inspect permit {permit_id} and its next-inspection eligibility."),
        permit_id=permit_id,
        success_conditions=("permit_verified", "availability_checked") if should_check
                          else ("permit_verified", "next_inspection_identified"),
        autonomous=False,
        inspection_type=inspection_type,
    )
    permit = PermitState(
        record_number=permit_id,
        record_key=key,
        status_normalized="ISSUED",
        coverage={"inspections": Coverage(status=CoverageStatus.EXPLICITLY_EMPTY)},
    )
    world = World(
        permit_id=permit_id,
        record_key=key,
        snapshot_id=snapshot,
        permit_verified=True,
        permit=permit,
        reasoning=ReasoningResult(key, snapshot, goal.objective, "answered"),
    )
    type_evidence = Evidence("portal-option:Rough", "inspections", "wizard offers Rough", record_key=key)
    requirement_ids = ("portal-required:Rough",) if portal_required else ()
    evidence = {type_evidence.id: type_evidence}
    if portal_required:
        evidence[requirement_ids[0]] = Evidence(
            requirement_ids[0], "inspections", "wizard marks Rough (required)", record_key=key,
        )
    context = SelectionContext(
        permit_id, key, snapshot, True,
        (InspectionOption(
            "Rough", True, True, (type_evidence.id,), required=portal_required,
            requirement_evidence_ids=requirement_ids,
        ),),
        evidence,
        inspections=(),
        history_complete=True,
        catalog_complete=True,
    )
    preflight_calls = []

    async def preflight(observed_world):
        preflight_calls.append(observed_world.proposal.inspection_type if observed_world.proposal else None)
        return Preflight(
            key, snapshot, operation_key(observed_world), True, (),
            details={"availability": {
                "calendar_read": True,
                "identity_verified": True,
                "availability_status": "none_in_observed_calendar",
                "calendar_months": [],
            }},
        )

    capabilities = LicetCapabilities(
        lookup=None,
        retrieval=None,
        selection_context=lambda _: context,
        preflight=preflight,
        portal=Portal(key),
    )
    result = asyncio.run(GoalPlanner(capabilities).run(goal, world=world))

    if should_check:
        assert preflight_calls == ["Rough"]
        assert Action.CHECK_INSPECTION_AVAILABILITY in [item["action"] for item in result.trace]
        assert result.world.availability_checked
        assert result.world.proposal.inspection_type == "Rough"
        assert result.world.available_dates == ()
        assert result.status is Status.SUCCESS, result.report()
        assert not result.attempted_mutations
        assert not capabilities.portal.submits
        assert Action.SCHEDULE_INSPECTION not in next_actions(result)
    else:
        assert preflight_calls == []
        assert result.reason == "no explicit supported inspection operation was proposed"
        assert not result.world.availability_checked
        assert not result.attempted_mutations
        assert Action.CHECK_INSPECTION_AVAILABILITY not in [item["action"] for item in result.trace]
