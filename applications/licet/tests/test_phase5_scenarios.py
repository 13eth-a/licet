"""Regression lock for the Phase 5 planner scenario set.

The scenarios in ``licet.eval.phase5_fixtures.planner_scenarios`` are the
checklist's 30 deterministic planner cases expressed as data: 5 simple goal
completion, 5 replanning, 5 partial completion, 5 constraint handling, 5
external blockers, and 5 loop/error recovery.

This test does not assert a fixed narrative about the planner. It drives the
current ``GoalPlanner`` with each scenario and verifies the recorded outcome,
the exact semantic-action trace, the remaining goal predicates, and the metric
assertions the scenario declares. If a future change silently changes one of
these paths, this test is the regression.
"""
from __future__ import annotations

import pytest

from licet.eval.phase5 import planner_metrics
from licet.eval.phase5_fixtures import planner_scenarios

GROUPS = {"SC": 5, "RP": 5, "PC": 5, "CH": 5, "EB": 5, "LE": 5}

SCENARIOS = planner_scenarios()
SCENARIO_IDS = [scenario.id for scenario in SCENARIOS]


def test_planner_scenario_set_is_the_named_30_case_split():
    by_group: dict[str, list[str]] = {}
    for scenario in SCENARIOS:
        by_group.setdefault(scenario.id.rstrip("0123456789"), []).append(scenario.id)

    assert len(SCENARIOS) == sum(GROUPS.values()) == 30
    # A duplicated id would leave both the count and the split looking right
    # while silently dropping a case, so check uniqueness explicitly.
    assert len({scenario.id for scenario in SCENARIOS}) == 30
    assert by_group.keys() == GROUPS.keys()
    for group, expected in GROUPS.items():
        assert len(by_group[group]) == expected, f"group {group}: {by_group[group]}"
    # Every case carries a locked trace, so the parametrised test never skips one.
    assert all(scenario.expected_actions is not None for scenario in SCENARIOS)


@pytest.mark.parametrize("scenario", SCENARIOS, ids=SCENARIO_IDS)
def test_planner_scenario_resolves_as_recorded(scenario):
    result = scenario.run()

    assert result.status == scenario.expected_status, (
        f"{scenario.id}: expected {scenario.expected_status.value}, got {result.status.value}\n"
        f"  plan revisions: {len(result.plans)}\n"
        f"  reason: {result.reason}"
    )
    assert result.error == scenario.expected_error, (
        f"{scenario.id}: expected error "
        f"{scenario.expected_error.value if scenario.expected_error else None}, "
        f"got {result.error.value if result.error else None}"
    )

    actions = [entry["action"] for entry in result.trace if "step" in entry]
    assert actions == scenario.expected_actions, (
        f"{scenario.id}: expected actions {scenario.expected_actions}, got {actions}"
    )

    remaining = result.report()["remaining_goal"]
    assert set(remaining) == set(scenario.expected_remaining), (
        f"{scenario.id}: expected remaining_goal "
        f"{sorted(scenario.expected_remaining)}, got {remaining}"
    )

    # The negative evidence: a refused or blocked case must not have reached the
    # portal. The planner's own ledger is the assertion, not the fixture.
    metrics = planner_metrics([result], expected_actions=[scenario.expected_actions])
    assert metrics["mutations_attempted"] == result.report()["metrics"]["mutations_attempted"]
    for key, expected in scenario.assert_metrics.items():
        assert metrics[key] == expected, (
            f"{scenario.id}: metric {key} expected {expected!r}, got {metrics[key]!r}"
        )
