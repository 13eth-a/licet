"""Expose the existing offline booking replay as reviewable UI evidence.

Uses the regression harness, never a browser connection or live account.
"""
from datetime import datetime, timezone

from licet.browser import accela
from licet.phase4.accela_portal import AccelaInspectionPortal
from licet.phase4.workflow import InspectionActionExecutor
from tests.test_scheduling_portal_replay import (
    BOOKED_DATE, BOOKED_DAY, TIMES, CalendarReplayClient, captured_markup,
    dispatcher_over, inject_active_day, make_action,
)


def run_replay():
    original = captured_markup()
    injected = inject_active_day(original, BOOKED_DAY, first_slot=TIMES.splitlines()[0])
    client = CalendarReplayClient(markup=injected, scheduled_row=True)
    executor = InspectionActionExecutor(AccelaInspectionPortal(dispatcher_over(client)))
    result = executor.execute(
        make_action(date_window_start=BOOKED_DATE, date_window_end=BOOKED_DATE),
        eligible_types=['Rough', 'Electrical Final'], available_dates=[BOOKED_DATE],
    )
    return {
        'mode': 'offline-portal-replay',
        'scope': 'Captured calendar markup + one injected slot. Browser I/O and confirmation are simulated. No live booking.',
        'recorded_at': datetime.now(timezone.utc).isoformat(),
        'fixture_date': BOOKED_DATE,
        'original_active_days': list(accela.parse_calendar(original)[0].active_days),
        'injected_active_days': list(accela.parse_calendar(injected)[0].active_days),
        'original_markup': original, 'injected_markup': injected,
        'result': result.as_dict(), 'clicks': client.clicks,
        'reads': client.reads,
    }
