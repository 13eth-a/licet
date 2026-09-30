"""offline tests for the human-attestation handoff"""
from __future__ import annotations

import asyncio

import pytest

from licet.safety.attestation_handoff import (
    DISCLAIMER_CONTINUE_CONTROL,
    AttestationProbe,
    HandoffOutcome,
    HandoffReport,
    accept_disclaimer_with_human,
    await_human_attestation,
    disclaimer_frames,
    is_attestation_disclaimer_url,
    is_disclaimer_page,
)
from licet.safety.stops import SafetyStopCondition


DISCLAIMER_URL = (
    "https://aca-test.accela.com/nullisland/Cap/"
    "CapApplyDisclaimer.aspx?module=Building&TabName=Building"
)
NEXT_STEP_URL = (
    "https://aca-test.accela.com/nullisland/Cap/"
    "CapEdit.aspx?stepNumber=2&pageNumber=1&Module=Building"
)


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def sleep_factory(clock: FakeClock, on_tick=None):
    async def sleep(seconds: float) -> None:
        clock.now += seconds
        if on_tick is not None:
            on_tick()
    return sleep


def test_disclaimer_url_detection_ignores_query_and_case():
    assert is_attestation_disclaimer_url(DISCLAIMER_URL)
    assert is_attestation_disclaimer_url(DISCLAIMER_URL.upper())
    assert not is_attestation_disclaimer_url(NEXT_STEP_URL)
    assert not is_attestation_disclaimer_url("")


def test_is_disclaimer_page_reads_top_level_and_frames():
    frame = FakeFrame(DISCLAIMER_URL)
    page = FakePage("https://aca-test.accela.com/nullisland/Cap/CapHome.aspx", [frame])

    assert disclaimer_frames(page) == [frame]
    assert is_disclaimer_page(page) is True

    frame.url = NEXT_STEP_URL
    assert disclaimer_frames(page) == []
    assert is_disclaimer_page(page) is False


def test_waits_for_the_operator_then_reports_satisfied_while_waiting_once():
    clock = FakeClock()
    ticks = {"n": 0}
    notified = {"n": 0}

    def probe() -> AttestationProbe:
        return AttestationProbe(present=True, satisfied=ticks["n"] >= 2)

    report = asyncio.run(await_human_attestation(
        probe,
        sleep=sleep_factory(clock, lambda: ticks.__setitem__("n", ticks["n"] + 1)),
        monotonic=clock, timeout_s=30, poll_s=1,
        notify=lambda: notified.__setitem__("n", notified["n"] + 1),
    ))

    assert report.outcome is HandoffOutcome.SATISFIED_WHILE_WAITING
    assert report.permits_continuation() is True
    assert report.stop is None
    assert report.polls == 3
    assert notified["n"] == 1


def test_timeout_is_a_legal_attestation_stop_not_a_silent_pass():
    clock = FakeClock()

    report = asyncio.run(await_human_attestation(
        lambda: AttestationProbe(present=True),
        sleep=sleep_factory(clock), monotonic=clock, timeout_s=3, poll_s=1,
    ))

    assert report.outcome is HandoffOutcome.TIMED_OUT
    assert report.permits_continuation() is False
    assert report.stop is SafetyStopCondition.LEGAL_ATTESTATION
    assert report.elapsed_s >= 3


def test_missing_control_returns_immediately_without_asking_the_human():
    clock = FakeClock()
    notified: list[int] = []

    report = asyncio.run(await_human_attestation(
        lambda: AttestationProbe(present=False),
        sleep=sleep_factory(clock), monotonic=clock,
        notify=lambda: notified.append(1),
    ))

    assert report.outcome is HandoffOutcome.NO_ATTESTATION_CONTROL
    assert report.polls == 1
    assert report.stop is None
    assert notified == []


def test_declined_attestation_stops_instead_of_waiting_out():
    clock = FakeClock()

    report = asyncio.run(await_human_attestation(
        lambda: AttestationProbe(present=True, declined=True),
        sleep=sleep_factory(clock), monotonic=clock, timeout_s=60, poll_s=1,
    ))

    assert report.outcome is HandoffOutcome.DECLINED_BY_HUMAN
    assert report.stop is SafetyStopCondition.LEGAL_ATTESTATION
    assert report.elapsed_s == 0


def test_sync_probe_and_async_notify_are_both_supported():
    clock = FakeClock()
    seen: list[str] = []
    ticks = {"n": 0}

    async def notify() -> None:
        seen.append("notified")

    def probe() -> AttestationProbe:
        return AttestationProbe(present=True, satisfied=ticks["n"] >= 1)

    report = asyncio.run(await_human_attestation(
        probe,
        sleep=sleep_factory(clock, lambda: ticks.__setitem__("n", 1)),
        monotonic=clock, notify=notify,
    ))

    assert report.permits_continuation() is True
    assert seen == ["notified"]


def test_a_pre_satisfied_control_is_not_reported_as_human_acceptance():
    """ni ships its agree box pre-ticked, so 'checked' proves nobody's action"""
    clock = FakeClock()
    notified: list[int] = []

    report = asyncio.run(await_human_attestation(
        lambda: AttestationProbe(present=True, satisfied=True),
        sleep=sleep_factory(clock), monotonic=clock,
        notify=lambda: notified.append(1),
    ))

    assert report.outcome is HandoffOutcome.SATISFIED_WITHOUT_HUMAN_ACTION
    assert report.permits_continuation() is False
    assert report.stop is SafetyStopCondition.LEGAL_ATTESTATION
    assert report.polls == 1
    assert notified == []


@pytest.mark.parametrize("kwargs", [{"timeout_s": 0}, {"poll_s": 0}, {"timeout_s": -1}])
def test_non_positive_budgets_are_rejected(kwargs):
    async def run():
        await await_human_attestation(
            lambda: AttestationProbe(present=False),
            sleep=sleep_factory(FakeClock()), monotonic=FakeClock(), **kwargs,
        )

    with pytest.raises(ValueError):
        asyncio.run(run())


class FakeBox:
    """the attestation checkbox, counting *agent* interactions separately"""

    def __init__(self) -> None:
        self.present = True
        self.checked = False
        self.agent_clicks = 0

    async def count(self) -> int:
        return 1 if self.present else 0

    async def is_checked(self) -> bool:
        return self.checked

    async def check(self, **kwargs) -> None:
        self.agent_clicks += 1
        self.checked = True

    async def click(self, **kwargs) -> None:
        self.agent_clicks += 1
        self.checked = True


class FakeLocator:
    def __init__(self, frame: "FakeFrame", selector: str) -> None:
        self.frame = frame
        self.selector = selector

    @property
    def first(self) -> "FakeLocator":
        return self

    async def count(self) -> int:
        if DISCLAIMER_CONTINUE_CONTROL in self.selector:
            return 1 if self.frame.continue_present else 0
        return await self.frame.box.count()

    async def is_checked(self) -> bool:
        return await self.frame.box.is_checked()

    async def check(self, **kwargs) -> None:
        await self.frame.box.check(**kwargs)

    async def click(self, **kwargs) -> None:
        if DISCLAIMER_CONTINUE_CONTROL in self.selector:
            self.frame.continue_clicks += 1
        else:
            await self.frame.box.click(**kwargs)


class FakeFrame:
    def __init__(self, url: str, box: FakeBox | None = None,
                 continue_present: bool = True) -> None:
        self.url = url
        self.box = box or FakeBox()
        self.continue_present = continue_present
        self.continue_clicks = 0

    def locator(self, selector: str) -> FakeLocator:
        return FakeLocator(self, selector)


class FakePage:
    def __init__(self, url: str, frames: list[FakeFrame]) -> None:
        self.url = url
        self.frames = frames


def _disclaimer_page(url: str = DISCLAIMER_URL):
    box = FakeBox()
    frame = FakeFrame(url, box=box)
    return FakePage(url, [frame]), frame, box


def test_adapter_reports_no_control_when_not_on_the_disclaimer():
    box = FakeBox()
    plain = FakeFrame(NEXT_STEP_URL, box=box)
    page = FakePage(NEXT_STEP_URL, [plain])

    report = asyncio.run(accept_disclaimer_with_human(
        page, sleep=sleep_factory(FakeClock()), monotonic=FakeClock(),
    ))

    assert report.outcome is HandoffOutcome.NO_ATTESTATION_CONTROL
    assert box.agent_clicks == 0
    assert plain.continue_clicks == 0


def test_adapter_reports_no_control_when_checkbox_is_absent():
    page, frame, box = _disclaimer_page()
    box.present = False

    report = asyncio.run(accept_disclaimer_with_human(
        page, sleep=sleep_factory(FakeClock()), monotonic=FakeClock(),
    ))

    assert report.outcome is HandoffOutcome.NO_ATTESTATION_CONTROL
    assert "checkbox" in report.reason
    assert box.agent_clicks == 0
    assert frame.continue_clicks == 0


def test_adapter_never_checks_the_box_and_advances_when_it_flips_while_waiting():
    page, frame, box = _disclaimer_page()
    clock = FakeClock()
    ticks = {"n": 0}

    def human_acts() -> None:
        ticks["n"] += 1
        if ticks["n"] >= 2:
            box.checked = True  # the human's click, not the agent's

    report = asyncio.run(accept_disclaimer_with_human(
        page, sleep=sleep_factory(clock, human_acts), monotonic=clock,
        timeout_s=30, poll_s=1,
    ))

    assert report.permits_continuation() is True
    assert box.agent_clicks == 0, "the agent must never operate the attestation control"
    assert frame.continue_clicks == 1


def test_adapter_does_not_advance_when_the_human_never_accepts():
    page, frame, box = _disclaimer_page()
    clock = FakeClock()

    report = asyncio.run(accept_disclaimer_with_human(
        page, sleep=sleep_factory(clock), monotonic=clock, timeout_s=3, poll_s=1,
    ))

    assert report.outcome is HandoffOutcome.TIMED_OUT
    assert report.stop is SafetyStopCondition.LEGAL_ATTESTATION
    assert box.agent_clicks == 0
    assert frame.continue_clicks == 0


def test_adapter_can_be_told_not_to_advance():
    page, frame, box = _disclaimer_page()
    clock = FakeClock()
    ticks = {"n": 0}

    def human_acts() -> None:
        ticks["n"] += 1
        box.checked = True  # the human's click, not the agent's

    report = asyncio.run(accept_disclaimer_with_human(
        page, sleep=sleep_factory(clock, human_acts), monotonic=clock,
        timeout_s=30, poll_s=1, advance=False,
    ))

    assert report.permits_continuation() is True
    assert box.agent_clicks == 0
    assert frame.continue_clicks == 0


def test_adapter_does_not_advance_a_pre_ticked_portal_default():
    """the live ni page renders its agree box already ticked and self-advances"""
    page, frame, box = _disclaimer_page()
    box.checked = True

    report = asyncio.run(accept_disclaimer_with_human(
        page, sleep=sleep_factory(FakeClock()), monotonic=FakeClock(),
        timeout_s=30,
    ))

    assert report.outcome is HandoffOutcome.SATISFIED_WITHOUT_HUMAN_ACTION
    assert report.permits_continuation() is False
    assert box.agent_clicks == 0
    assert frame.continue_clicks == 0


def test_adapter_treats_navigating_past_the_disclaimer_as_satisfied():
    page, frame, box = _disclaimer_page()
    clock = FakeClock()
    ticks = {"n": 0}

    def portal_advances() -> None:
        ticks["n"] += 1
        if ticks["n"] >= 1:
            # the attestation was satisfied and aca moved the wizard on; the checkbox is gone because the
            # page is no longer the disclaimer
            box.present = False
            page.url = NEXT_STEP_URL
            frame.url = NEXT_STEP_URL

    report = asyncio.run(accept_disclaimer_with_human(
        page, sleep=sleep_factory(clock, portal_advances), monotonic=clock,
        timeout_s=30, poll_s=1,
    ))

    assert report.permits_continuation() is True
    assert box.agent_clicks == 0


def test_portal_default_opt_in_permits_continuation_without_relabelling_it():
    """the opt-in changes what the caller may do, not what happened"""
    page, frame, box = _disclaimer_page()
    box.checked = True

    report = asyncio.run(accept_disclaimer_with_human(
        page, sleep=sleep_factory(FakeClock()), monotonic=FakeClock(),
        timeout_s=30, allow_portal_default=True,
    ))

    assert report.permits_continuation(allow_portal_default=True) is True
    assert frame.continue_clicks == 1
    assert report.outcome is HandoffOutcome.SATISFIED_WITHOUT_HUMAN_ACTION
    assert report.permits_continuation() is False
    assert box.agent_clicks == 0


def test_portal_default_opt_in_is_not_granted_by_default():
    page, frame, box = _disclaimer_page()
    box.checked = True

    report = asyncio.run(accept_disclaimer_with_human(
        page, sleep=sleep_factory(FakeClock()), monotonic=FakeClock(),
        timeout_s=30, allow_portal_default=True,
    ))

    # a caller that did not ask for the opt-in still may not continue
    assert report.permits_continuation() is False
    assert frame.continue_clicks == 1


def test_portal_default_opt_in_does_not_rescue_a_real_stop():
    """opting in must not turn a timeout or a decline into a pass"""
    page, frame, box = _disclaimer_page()
    clock = FakeClock()

    timed_out = asyncio.run(accept_disclaimer_with_human(
        page, sleep=sleep_factory(clock), monotonic=clock,
        timeout_s=3, poll_s=1, allow_portal_default=True,
    ))

    assert timed_out.outcome is HandoffOutcome.TIMED_OUT
    assert timed_out.permits_continuation(allow_portal_default=True) is False
    assert box.agent_clicks == 0
    assert frame.continue_clicks == 0


def test_continuation_is_gated_on_what_the_run_actually_observed():
    waited = HandoffReport(
        HandoffOutcome.SATISFIED_WHILE_WAITING, 1, 0.0, None, "")
    portal_default = HandoffReport(
        HandoffOutcome.SATISFIED_WITHOUT_HUMAN_ACTION, 1, 0.0,
        SafetyStopCondition.LEGAL_ATTESTATION, "")

    assert waited.permits_continuation() is True
    # nothing in the run supports the operator having been involved, so only the explicit opt-in carries it
    assert portal_default.permits_continuation() is False
    assert portal_default.permits_continuation(allow_portal_default=True) is True


def test_adapter_keeps_waiting_when_only_the_box_rerenders():
    # a re-render that drops the checkbox while the page is still the disclaimer must not be mistaken for
    # acceptance
    page, frame, box = _disclaimer_page()
    clock = FakeClock()
    ticks = {"n": 0}

    def rerender() -> None:
        ticks["n"] += 1
        box.present = False

    report = asyncio.run(accept_disclaimer_with_human(
        page, sleep=sleep_factory(clock, rerender), monotonic=clock,
        timeout_s=2, poll_s=1,
    ))

    assert report.outcome is HandoffOutcome.TIMED_OUT
    assert box.agent_clicks == 0
    assert frame.continue_clicks == 0
