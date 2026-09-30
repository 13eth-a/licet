"""phase 2-6 flagship integration, executed against fake i/o"""
from __future__ import annotations

import asyncio

from licet.phase3.runner import Phase3RetrievalRunner
from licet.phase3.state import Evidence
from licet.phase4.actions import InspectionSnapshot
from licet.phase4.selection import InspectionOption, SelectionContext
from licet.phase5 import GoalPlanner, LicetCapabilities, Preflight, Status, parse_goal
from licet.phase5.state import operation_key
from licet.safety.policy import (
    Environment,
    PolicyEngine,
    ProposedAction,
    UserConstraints,
)
from tests.conftest import (
    DETAIL_URL,
    FakeClient,
    apo_fields,
    detail_page,
    mode_dropdown,
    row,
    runner_for,
    search_form,
    search_results,
)

ADDRESS = "123 Main Street"
NUMBER = "000000014"
RECORD_KEY = "NULLISLAND/Building/REC26/00000/00014"
TYPE = "Rough Electrical"
DATE = "2026-09-26"

GOAL = (
    "Find the permit at 123 Main Street, figure out what is blocking it, and "
    "schedule the Rough Electrical inspection without spending money."
)


class Portal:
    """scripted inspection portal that identifies its own environment"""

    def __init__(self, environment: Environment) -> None:
        self.environment = environment
        self.submits: list[InspectionSnapshot] = []
        self.current: InspectionSnapshot | None = None

    def read_inspection_state(self, permit, inspection_type, inspection_id=None):
        if self.current is not None:
            return self.current
        return InspectionSnapshot(permit, None, inspection_type or TYPE, "Not Scheduled",
                                  record_key=RECORD_KEY)

    def submit_inspection_action(self, action, *, portal_type, selected_date=None):
        self.submits.append(action)
        self.current = InspectionSnapshot(action.permit_id, "I-1", portal_type, "Scheduled",
                                          selected_date, record_key=RECORD_KEY)
        return "CONF-1"


def _client() -> FakeClient:
    detail = detail_page(number=NUMBER, status="Issued", address=ADDRESS)
    detail["text"] += "\nYou have not added any inspections."
    return FakeClient([
        search_form(fields=mode_dropdown(["Permit Number", "Search by Address", "Search by Parcel"])),
        search_form(fields=apo_fields()),
        search_results(row(NUMBER, "Commercial Alteration", ADDRESS)),
        detail,
    ])


async def _flagship(environment: Environment):
    client = _client()
    lookup = runner_for(client)
    retrieval = Phase3RetrievalRunner(lookup.dispatcher)
    portal = Portal(environment)

    def context(world):
        return SelectionContext(
            world.permit_id, world.record_key, world.snapshot_id, True,
            (InspectionOption(TYPE, True, True, ("eligibility",)),),
            {"eligibility": Evidence("eligibility", "inspections",
                                     f"{TYPE} is eligible; no unmet prerequisites",
                                     record_key=RECORD_KEY)},
            history_complete=True,
        )

    def preflight(world):
        return Preflight(RECORD_KEY, world.snapshot_id, operation_key(world), True, (DATE,), 0, False)

    capabilities = LicetCapabilities(lookup=lookup, retrieval=retrieval,
                                     selection_context=context, preflight=preflight,
                                     portal=portal, environment=environment,
                                     user_goal=GOAL)
    run = await GoalPlanner(capabilities).run(parse_goal(GOAL))
    return run, portal


def test_flagship_task_completes_in_a_sandbox_without_paying():
    run, portal = asyncio.run(_flagship(Environment.SANDBOX))

    assert run.world.permit_verified
    assert run.world.record_key == RECORD_KEY
    assert run.world.permit is not None and run.world.reasoning is not None
    assert run.status is Status.SUCCESS, run.report()
    assert len(portal.submits) == 1
    assert portal.submits[0].action_type == "schedule"
    assert run.world.verified_inspection is not None
    assert run.world.verified_inspection.record_key == run.world.record_key

    # the no-spend constraint: no payment action was ever part of the run, and a payment proposal is
    # refused by the same instruction the run was started with — the $74.50 fee remains unpaid
    assert all("pay" not in action.action_type for action in portal.submits)
    engine = PolicyEngine(environment=Environment.SANDBOX,
                          constraints=UserConstraints.from_text(GOAL))
    decision = engine.decide(ProposedAction("PAY_FEE", permit_id=NUMBER, target="fee", amount=74.5))
    assert not decision.allowed
    assert decision.violated_constraint == "PAYMENTS_NOT_ALLOWED"


def test_flagship_task_on_a_live_portal_prepares_but_does_not_execute():
    run, portal = asyncio.run(_flagship(Environment.LIVE_READ_ONLY))

    assert portal.submits == [], "a live municipal record was mutated"
    assert run.status is not Status.SUCCESS
    assert run.world.result is None or not run.world.result.success

    # the proposed action would have been blocked by the environment gate, not by a missing plan: the run
    # prepared the scheduling action and stopped
    engine = PolicyEngine(environment=Environment.LIVE_READ_ONLY)
    decision = engine.decide(ProposedAction("SCHEDULE_INSPECTION", permit_id=NUMBER,
                                            target=TYPE, inspection_type=TYPE))
    assert not decision.allowed and decision.violated_constraint == "LIVE_MUTATION_BLOCKED"
