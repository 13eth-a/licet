"""portal realism replay: the real adapter books a day on real captured markup"""
from __future__ import annotations

import asyncio
import datetime as dt
import re
from pathlib import Path

import pytest

from licet.browser import accela
from licet.phase4.accela_portal import AccelaInspectionPortal
from licet.phase4.actions import ActionVerificationState, InspectionAction
from licet.phase4.dates import (
    DateConstraints,
    available_dates_from_calendar,
    select_date,
)
from licet.phase4.workflow import InspectionActionExecutor
from tests.test_phase4_accela_portal import FakeClient, dispatcher_over, make_action

FIXTURE = Path(__file__).parent / "fixtures" / "ni_schedule_calendar.html"
INACTIVE_CELL = 'title="Cannot schedule inspection on this date" class="CalendarDayInactive ACA_LinkButton"'
TIMES = "8:00AM - 10:00AM\n1:00PM - 3:00PM"
BOOKED_DAY = 22
BOOKED_DATE = "2026-09-22"


def captured_markup() -> str:
    return FIXTURE.read_text(encoding="utf-8")


def inject_active_day(markup: str, day: int, *, first_slot: str) -> str:
    """flip exactly one inactive cell active, and fill what a real day click fills"""
    inactive = f'{INACTIVE_CELL} align="center" style="width:14%;">{day}</td>'
    assert markup.count(inactive) == 1, f"expected one inactive cell for day {day}"
    markup = markup.replace(
        inactive,
        f'class="CalendarDayActive ACA_LinkButton" align="center" style="width:14%;">{day}</td>',
    )
    markup = markup.replace(
        'class="ACA_Title_Color"></span>',
        f'class="ACA_Title_Color">{first_slot}</span>',
    )
    return markup.replace(' disabled="disabled" class="ButtonDisabled"', ' class="ButtonEnabled"')


def active_days_in_month_table(markup: str, table_index: int) -> list[int]:
    """active day numbers in the nth month table, mirroring the selector's scope"""
    marker = f"calendar_calendar{table_index + 1}"
    table = re.search(
        r'<table\b[^>]*id="[^"]*' + re.escape(marker) + r'[^"]*"[^>]*>(.*?)</table>',
        markup,
        re.S | re.I,
    )
    assert table, f"no month table carrying {marker!r}"
    cells = re.findall(
        r'<td\b([^>]*\bclass="[^"]*calendarday[^"]*"[^>]*)>(.*?)</td>', table.group(1), re.I | re.S
    )
    active: list[int] = []
    for attrs, body in cells:
        text = re.sub(r"<[^>]+>", "", body).strip()
        if text.isdigit() and "calendardayinactive" not in attrs.lower():
            active.append(int(text))
    return active


class CalendarReplayClient(FakeClient):
    """the adapter's state machine, with its calendar rendered from real markup"""

    def __init__(self, *, markup: str, **kwargs) -> None:
        super().__init__(times=TIMES, **kwargs)
        self.markup = markup

    def payload(self):
        data = dict(super().payload())
        if self.state in (self.STATE_CALENDAR, self.STATE_TIMES):
            data["calendar"] = [month.as_dict() for month in accela.parse_calendar(self.markup)]
            data["selectable_times"] = self.times if self.state == self.STATE_TIMES else ""
        elif self.state == self.STATE_RESULT:
            # the base fake's acknowledgement row is hardcoded to 09/24/2026; re date it to the day this
            # replay actually books so the executor's independent verification compares like with like
            booked_us = dt.date.fromisoformat(BOOKED_DATE).strftime("%m/%d/%Y")
            data["text"] = str(data.get("text") or "").replace("09/24/2026", booked_us)
        return data


def test_captured_calendar_really_renders_no_bookable_day():
    """the fixture is the sandbox's actual state: every cell inactive"""
    months = accela.parse_calendar(captured_markup())
    assert [month.month for month in months] == ["Sep 2026"]
    assert months[0].inactive_days == tuple(range(1, 31))
    assert months[0].active_days == ()
    resolved = accela.resolve_calendar_months(months, reference=BOOKED_DATE)
    assert available_dates_from_calendar(resolved, reference=BOOKED_DATE) == []
    assert accela.popup_continue_disabled(captured_markup()) is True


def test_injected_day_is_the_only_active_cell_the_selector_names():
    markup = inject_active_day(captured_markup(), BOOKED_DAY, first_slot=TIMES.splitlines()[0])
    assert active_days_in_month_table(markup, 0) == [BOOKED_DAY]
    selector = accela.active_calendar_day_selector(0, BOOKED_DAY)
    assert 'table[id*="calendar_calendar1"]' in selector
    assert f'text="{BOOKED_DAY}"' in selector
    assert accela.selectable_times_text(markup) == TIMES.splitlines()[0]
    assert accela.popup_continue_disabled(markup) is False


def test_real_parser_turns_the_injected_cell_into_a_selectable_date():
    markup = inject_active_day(captured_markup(), BOOKED_DAY, first_slot=TIMES.splitlines()[0])
    resolved = accela.resolve_calendar_months(accela.parse_calendar(markup), reference=BOOKED_DATE)
    assert resolved == [(2026, 9, (BOOKED_DAY,))]
    available = available_dates_from_calendar(resolved, reference=BOOKED_DATE)
    assert available == [BOOKED_DATE]
    assert select_date(available, DateConstraints(earliest=True)) == BOOKED_DATE


def test_an_injected_day_outside_the_requested_window_is_still_refused():
    """injection must not widen the request: the date layer still gates"""
    markup = inject_active_day(captured_markup(), 30, first_slot=TIMES.splitlines()[0])
    resolved = accela.resolve_calendar_months(accela.parse_calendar(markup), reference=BOOKED_DATE)
    available = available_dates_from_calendar(resolved, reference=BOOKED_DATE)
    assert available == ["2026-09-30"]
    assert select_date(available, DateConstraints(end=dt.date(2026, 9, 22))) is None


def test_adapter_books_the_injected_day_through_the_real_wizard_walk():
    markup = inject_active_day(captured_markup(), BOOKED_DAY, first_slot=TIMES.splitlines()[0])
    client = CalendarReplayClient(markup=markup)
    portal = AccelaInspectionPortal(dispatcher_over(client))
    confirmation = asyncio.run(
        portal.submit_inspection_action_async(
            make_action(), portal_type="Electrical Final", selected_date=BOOKED_DATE
        )
    )
    assert confirmation == "CNF-77K9"
    joined = " | ".join(click["target"] or "" for click in client.clicks)
    assert "rdInspectionType" in joined
    assert f'text="{BOOKED_DAY}"' in joined
    assert "8:00AM - 10:00AM" in joined


def test_executor_reaches_verified_success_on_the_injected_day():
    markup = inject_active_day(captured_markup(), BOOKED_DAY, first_slot=TIMES.splitlines()[0])
    client = CalendarReplayClient(markup=markup, scheduled_row=True)
    portal = AccelaInspectionPortal(dispatcher_over(client))
    executor = InspectionActionExecutor(portal)
    result = executor.execute(
        make_action(date_window_start=BOOKED_DATE, date_window_end=BOOKED_DATE),
        eligible_types=["Rough", "Electrical Final"],
        available_dates=[BOOKED_DATE],
    )
    assert result.success, result.error
    assert result.verification_state is ActionVerificationState.VERIFIED_SUCCESS
    assert result.scheduled_date == BOOKED_DATE
    assert result.confirmation_number == "CNF-77K9"
    audits = executor.audits
    assert audits and audits[0].previous_state.status == "Not Scheduled"
    assert audits[0].verified_final_state.is_scheduled


def test_executor_still_refuses_a_day_the_calendar_does_not_offer():
    """injecting one day must not let the adapter invent another"""
    markup = inject_active_day(captured_markup(), BOOKED_DAY, first_slot=TIMES.splitlines()[0])
    client = CalendarReplayClient(markup=markup)
    portal = AccelaInspectionPortal(dispatcher_over(client))
    with pytest.raises(RuntimeError, match="renders inactive"):
        asyncio.run(
            portal.submit_inspection_action_async(
                InspectionAction(
                    action_type="schedule", permit_id="BLD26-00469",
                    inspection_type="Electrical Final",
                ),
                portal_type="Electrical Final",
                selected_date="2026-09-23",
            )
        )
