"""human attestation handoff: the agent pauses, the human accepts"""
from __future__ import annotations

import asyncio
import inspect
import time
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Awaitable, Callable
from urllib.parse import urlparse

from licet.safety.stops import SafetyStopCondition

# how long the run holds the browser open waiting for the human
DEFAULT_HANDOFF_TIMEOUT_S = 300.0
DEFAULT_POLL_S = 1.0

# the aca apply flow's disclaimer step
DISCLAIMER_PATH_MARKER = "capapplydisclaimer"

# the control that continues *past* the disclaimer once the human has accepted it
DISCLAIMER_CONTINUE_CONTROL = "#ctl00_PlaceHolderMain_btnNextStep"

CHECKBOX_SELECTOR = "input[type='checkbox']"


class HandoffOutcome(StrEnum):
    # the control was unsatisfied when the run first looked, the run asked the operator to act, and the
    # control was satisfied by the time the wait ended
    SATISFIED_WHILE_WAITING = "satisfied_while_waiting"
    DECLINED_BY_HUMAN = "declined_by_human"
    TIMED_OUT = "timed_out"
    NO_ATTESTATION_CONTROL = "no_attestation_control"
    # the control was already satisfied the first time it was read, so this run never saw a human do anything
    SATISFIED_WITHOUT_HUMAN_ACTION = "satisfied_without_human_action"


@dataclass(frozen=True)
class AttestationProbe:
    """one reading of the attestation control's state"""

    present: bool
    satisfied: bool = False
    declined: bool = False


@dataclass(frozen=True)
class HandoffReport:
    outcome: HandoffOutcome
    polls: int
    elapsed_s: float
    stop: SafetyStopCondition | None
    reason: str

    def permits_continuation(self, *, allow_portal_default: bool = False) -> bool:
        """whether the caller may proceed past the disclaimer"""
        if self.outcome is HandoffOutcome.SATISFIED_WHILE_WAITING:
            return True
        return (allow_portal_default
                and self.outcome is HandoffOutcome.SATISFIED_WITHOUT_HUMAN_ACTION)

    def as_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome.value,
            "polls": self.polls,
            "elapsed_s": round(self.elapsed_s, 3),
            "stop": self.stop.value if self.stop else None,
            "reason": self.reason,
        }


def is_attestation_disclaimer_url(url: str) -> bool:
    """true for aca's apply flow disclaimer page"""
    return DISCLAIMER_PATH_MARKER in urlparse(url or "").path.casefold()


def disclaimer_frames(page: Any) -> list[Any]:
    """frames currently rendering the disclaimer, if any"""
    frames = list(getattr(page, "frames", ()) or ())
    return [frame for frame in frames
            if is_attestation_disclaimer_url(str(getattr(frame, "url", "") or ""))]


def is_disclaimer_page(page: Any) -> bool:
    """true while the page (top level or any frame) is the disclaimer"""
    return (is_attestation_disclaimer_url(str(getattr(page, "url", "") or ""))
            or bool(disclaimer_frames(page)))


async def await_human_attestation(
    probe: Callable[[], AttestationProbe | Awaitable[AttestationProbe]],
    *,
    sleep: Callable[[float], Awaitable[None]],
    monotonic: Callable[[], float],
    timeout_s: float = DEFAULT_HANDOFF_TIMEOUT_S,
    poll_s: float = DEFAULT_POLL_S,
    notify: Callable[[], Any] | None = None,
) -> HandoffReport:
    """wait for a human to satisfy an attestation control"""
    if timeout_s <= 0:
        raise ValueError("handoff timeout must be positive")
    if poll_s <= 0:
        raise ValueError("handoff poll interval must be positive")

    started = monotonic()
    polls = 0
    notified = False
    # true once we have seen the attestation still unsatisfied, i.e. once there was something for a human
    # to do
    awaited_human = False
    while True:
        snapshot = probe()
        if inspect.isawaitable(snapshot):
            snapshot = await snapshot
        polls += 1
        elapsed = monotonic() - started

        # acceptance is read before absence: a control that disappears *because* the human accepted and
        # the portal advanced is an accepted state, not a missing one
        if snapshot.satisfied:
            if awaited_human:
                # the run asked the operator to act and the control was satisfied by the time the wait ended
                return HandoffReport(
                    HandoffOutcome.SATISFIED_WHILE_WAITING, polls, elapsed, None,
                    "the attestation was satisfied while the run waited for "
                    "the operator; the run cannot verify whose action did it",
                )
            return HandoffReport(
                HandoffOutcome.SATISFIED_WITHOUT_HUMAN_ACTION, polls, elapsed,
                SafetyStopCondition.LEGAL_ATTESTATION,
                "the attestation was already satisfied when first read and no "
                "human action was observed",
            )
        if snapshot.declined:
            return HandoffReport(
                HandoffOutcome.DECLINED_BY_HUMAN, polls, elapsed,
                SafetyStopCondition.LEGAL_ATTESTATION,
                "the attestation was declined or abandoned; the run does not "
                "accept it on the user's behalf",
            )
        if not snapshot.present:
            return HandoffReport(
                HandoffOutcome.NO_ATTESTATION_CONTROL, polls, elapsed, None,
                "no attestation control is present to wait for",
            )
        awaited_human = True
        if notify is not None and not notified:
            notified = True
            pending = notify()
            if inspect.isawaitable(pending):
                await pending
        if elapsed >= timeout_s:
            return HandoffReport(
                HandoffOutcome.TIMED_OUT, polls, elapsed,
                SafetyStopCondition.LEGAL_ATTESTATION,
                f"the human did not accept the attestation within {timeout_s:g}s",
            )
        await sleep(poll_s)


async def accept_disclaimer_with_human(
    page: Any,
    *,
    sleep: Callable[[float], Awaitable[None]] | None = None,
    monotonic: Callable[[], float] = time.monotonic,
    timeout_s: float = DEFAULT_HANDOFF_TIMEOUT_S,
    poll_s: float = DEFAULT_POLL_S,
    notify: Callable[[], Any] | None = None,
    advance: bool = True,
    allow_portal_default: bool = False,
) -> HandoffReport:
    """hold the apply flow at aca's disclaimer until the human accepts it"""
    sleep = sleep or asyncio.sleep
    frames = disclaimer_frames(page)
    frame = frames[0] if frames else None
    if frame is None:
        return HandoffReport(
            HandoffOutcome.NO_ATTESTATION_CONTROL, 0, 0.0, None,
            "current page is not an attestation disclaimer",
        )

    try:
        box = frame.locator(CHECKBOX_SELECTOR).first
        if not await box.count():
            return HandoffReport(
                HandoffOutcome.NO_ATTESTATION_CONTROL, 0, 0.0, None,
                "disclaimer page renders no attestation checkbox",
            )
    except Exception as exc:  # a detached frame is a missing control, not a wait
        return HandoffReport(
            HandoffOutcome.NO_ATTESTATION_CONTROL, 0, 0.0, None,
            f"attestation checkbox could not be read: {type(exc).__name__}",
        )

    async def probe() -> AttestationProbe:
        on_disclaimer = is_disclaimer_page(page)
        try:
            checked = bool(await box.count()) and bool(await box.is_checked())
        except Exception:
            checked = False
        if checked:
            return AttestationProbe(present=True, satisfied=True)
        if on_disclaimer:
            # still the disclaimer, so the run is still waiting on the human
            return AttestationProbe(present=True, satisfied=False)
        return AttestationProbe(present=False, satisfied=True)

    report = await await_human_attestation(
        probe, sleep=sleep, monotonic=monotonic,
        timeout_s=timeout_s, poll_s=poll_s, notify=notify,
    )
    if advance and report.permits_continuation(
            allow_portal_default=allow_portal_default):
        await _click_disclaimer_continue(frame)
    return report


async def _click_disclaimer_continue(frame: Any) -> None:
    """click past the disclaimer after the *human* has accepted it"""
    try:
        control = frame.locator(DISCLAIMER_CONTINUE_CONTROL).first
        if await control.count():
            await control.click(timeout=8000)
    except Exception:
        # navigation failing is not an attestation problem; the caller's own stall detection deals with a
        # page that did not advance
        pass


__all__ = [
    "DEFAULT_HANDOFF_TIMEOUT_S", "DEFAULT_POLL_S", "DISCLAIMER_CONTINUE_CONTROL",
    "AttestationProbe", "HandoffOutcome", "HandoffReport",
    "accept_disclaimer_with_human", "await_human_attestation",
    "disclaimer_frames", "is_attestation_disclaimer_url", "is_disclaimer_page",
]
