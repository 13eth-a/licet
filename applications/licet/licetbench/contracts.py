"""Freeze the complete reused fixture contract, not only its visible summary."""
from dataclasses import asdict
from pathlib import Path
import json
from licetbench.provenance import digest


def fixture_contract(task):
    if task.source=='understanding':
        from licet.eval.phase3_fixtures import build_cases
        case=next(c for c in build_cases() if c.case_id==task.initial_state['fixture_id'])
        return asdict(case)
    if task.source=='action':
        from licet.eval.phase4_fixtures import build_cases
        case=next(c for c in build_cases() if c.case_id==task.initial_state['fixture_id'])
        return asdict(case)
    if task.source=='autonomy':
        from licet.eval.phase5_fixtures import planner_scenarios
        s=next(c for c in planner_scenarios() if c.id==task.initial_state['scenario_id'])
        return {'goal':asdict(s.resolved_goal()),'planner_kwargs':s.planner_kwargs,
                'initial_capability':s.resolve_capability().__dict__,
                'status':s.expected_status.value,'error':s.expected_error,'actions':s.expected_actions,
                'remaining':s.expected_remaining}
    return {'initial_state':task.initial_state,'expected_outcome':task.expected_outcome}


def frozen_contract_matches(task):
    path=Path(__file__).with_name('core-contracts-v1.json')
    saved=json.loads(path.read_text())
    return task.id not in saved or digest(fixture_contract(task))==saved[task.id]
