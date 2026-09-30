"""phase 4 acceptance-runner regression: the planning halves, offline"""
from __future__ import annotations

import asyncio
import importlib.util
from datetime import date
from pathlib import Path

from licet.phase4.dates import available_dates_from_calendar

SCRIPT = Path("scripts/ni_phase4_acceptance.py")


def _runner_module():
    spec = importlib.util.spec_from_file_location("ni_phase4_acceptance", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_runner_defaults_to_plan_only():
    module = _runner_module()
    args = module.build_parser().parse_args(["--record", "BLD26-00469", "--type", "Rough"])
    assert args.execute is False


def test_build_request_carries_type_date_and_window():
    module = _runner_module()
    args = module.build_parser().parse_args([
        "--record", "BLD26-00469", "--type", "Rough", "--date", "next week",
        "--window-start", "2026-09-23", "--window-end", "2026-09-30",
    ])
    request = module.build_request(args)
    assert request.permit_id == "BLD26-00469" and request.inspection_type == "Rough"
    assert request.date_instruction == "next week"
    assert request.date_window_start == "2026-09-23" and request.date_window_end == "2026-09-30"
    assert request.allow_alternatives is True  # advisory dates, never auto-selected


def test_build_request_treats_none_as_no_date_instruction():
    module = _runner_module()
    args = module.build_parser().parse_args([
        "--record", "BLD26-00469", "--type", "Rough", "--date", "none",
    ])
    assert module.build_request(args).date_instruction is None


class _FakeInnerPortal:
    """records which event loop each async call actually ran on"""

    def __init__(self) -> None:
        self.loops: list[asyncio.AbstractEventLoop] = []

    async def read_inspection_state_async(self, permit_id, inspection_type=None, inspection_id=None):
        self.loops.append(asyncio.get_running_loop())
        return {"permit_id": permit_id, "type": inspection_type, "id": inspection_id}

    async def submit_inspection_action_async(self, action, *, portal_type, selected_date=None):
        self.loops.append(asyncio.get_running_loop())
        return "CONF-1"


def test_loop_bridge_runs_executor_calls_on_the_live_loop():
    """the executor is sync; the solari client belongs to one loop"""
    module = _runner_module()

    async def scenario():
        inner = _FakeInnerPortal()
        main = asyncio.get_running_loop()
        bridge = module.LoopBridgePortal(inner, main)

        def worker():
            snapshot = bridge.read_inspection_state("BLD26-00469", "Rough")
            confirmation = bridge.submit_inspection_action(
                object(), portal_type="Rough", selected_date="2026-09-24"
            )
            return snapshot, confirmation

        return inner, main, await asyncio.to_thread(worker)

    inner, main, (snapshot, confirmation) = asyncio.run(scenario())
    assert snapshot == {"permit_id": "BLD26-00469", "type": "Rough", "id": None}
    assert confirmation == "CONF-1"
    assert inner.loops and all(loop is main for loop in inner.loops)


def test_runner_preview_and_action_selection_agree():
    module = _runner_module()
    args = module.build_parser().parse_args([
        "--record", "BLD26-00469", "--type", "Rough", "--date", "earliest available",
    ])
    request = module.build_request(args)
    shown = module.preview(request, date(2026, 9, 21))
    assert shown["proposed_action"] == "schedule"
    assert shown["permit_id"] == "BLD26-00469"


def test_calendar_months_become_sorted_iso_dates():
    months = [(2026, 9, (22, 25)), (2026, 10, (2,))]
    assert available_dates_from_calendar(months, reference=date(2026, 9, 21)) == [
        "2026-09-22", "2026-09-25", "2026-10-02",
    ]


def test_unresolved_month_captions_are_skipped_not_invented():
    assert available_dates_from_calendar([(0, 0, (5,))], reference=date(2026, 9, 21)) == []


def test_no_active_days_yields_no_dates():
    assert available_dates_from_calendar(
        [(2026, 9, ()), (2026, 10, ()), (2026, 11, ())], reference=date(2026, 9, 21)
    ) == []


def test_an_empty_calendar_yields_no_dates():
    assert available_dates_from_calendar(None) == []
    assert available_dates_from_calendar([]) == []
