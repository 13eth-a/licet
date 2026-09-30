"""Human-attestation handoff: the agent pauses, the human accepts.

Licet must never accept a legal attestation for the user. The guard enforces
that (`accept_legal_attestation` is `PROHIBITED` — no approval path), but a
flow that simply *needs* the attestation done has nowhere to go if the answer
is only "blocked". This module is that answer: the agent drives the wizard up
to the attestation, explains what the human must do, waits for the human to do
it in the browser, and then resumes.

The invariant is the whole point: **this module never operates the attestation
control.** It reads that control's state and nothing else. The click that
accepts the disclaimer is the human's, made by the human, in their own browser
session — after which the agent may continue with the non-attestation steps.

Being honest about *whose* action it was is the second half of that invariant.
A control that is already satisfied the first time it is read was satisfied by
nobody in particular: NI ships its agree box pre-ticked and advances past the
disclaimer on its own, so "checked" there is the portal's default, not an
attestation the operator made. That case reports
`HandoffOutcome.SATISFIED_WITHOUT_HUMAN_ACTION` and stops, rather than
borrowing the human's name for a state this run never watched them create.

Detection and waiting are browser-free so they can be tested deterministically;
`accept_disclaimer_with_human` is the one Playwright-shaped adapter, and it
duck-types the page rather than importing Playwright, so a fake page can drive
every branch of it offline.
"""
from __future__ import annotations

import asyncio
import inspect
import time
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Awaitable, Callable
from urllib.parse import urlparse

from licet.safety.stops import SafetyStopCondition

# How long the run holds the browser open waiting for the human. ACA forms
# time out server-side long before this, so it bounds the wait for the human,
# not for the portal.
DEFAULT_HANDOFF_TIMEOUT_S = 300.0
DEFAULT_POLL_S = 1.0

# The ACA apply flow's disclaimer step. Matched on the path, not the query, so
# a stepNumber/pageNumber suffix does not defeat it.
DISCLAIMER_PATH_MARKER = "capapplydisclaimer"

# The control that continues *past* the disclaimer once the human has accepted
# it. Clicking this is navigation, not attestation; the attestation is the
# checkbox, which stays entirely the human's.
DISCLAIMER_CONTINUE_CONTROL = "#ctl00_PlaceHolderMain_btnNextStep"

# The disclaimer's agree checkbox. There is exactly one checkbox on this page.
CHECKBOX_SELECTOR = "input[type='checkbox']"


class HandoffOutcome(StrEnum):
    # The control was unsatisfied when the run first looked, the run asked the
    # operator to act, and the control was satisfied by the time the wait
    # ended. Deliberately *not* named after the operator: NI ticks its own
    # agree box on load, so this same transition can be the portal's own doing,
    # and a run that cannot tell the two apart must not claim the human's name
    # for it.
    SATISFIED_WHILE_WAITING = "satisfied_while_waiting"
    DECLINED_BY_HUMAN = "declined_by_human"
    TIMED_OUT = "timed_out"
    NO_ATTESTATION_CONTROL = "no_attestation_control"
    # The control was already satisfied the first time it was read, so this run
    # never saw a human do anything. NI renders its agree box pre-ticked and
    # advances past the disclaimer on its own, so "the box is checked" is the
    # portal's default rather than an attestation anyone made. Reporting this as
    # ACCEPTED_BY_HUMAN would be a claim the run cannot support.
    SATISFIED_WITHOUT_HUMAN_ACTION = "satisfied_without_human_action"


@dataclass(frozen=True)
class AttestationProbe:
    """One reading of the attestation control's state.

    `present` False means the control is gone — either the page moved on (the
    human accepted and ACA advanced) or it was never an attestation page.
    Callers distinguish the two via `satisfied`.
    """

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
        """Whether the caller may proceed past the disclaimer.

        This is the report's only go/no-go, on purpose: a bare boolean named
        after acceptance invites a caller to treat an unverified attestation as
        a verified one.

        `SATISFIED_WHILE_WAITING` permits it — the run asked the operator to
        act and the control was satisfied by the time the wait ended. That is
        an observation about the run, not proof of who acted.

        `SATISFIED_WITHOUT_HUMAN_ACTION` permits it only under the explicit
        opt-in: the control was already satisfied before the run asked anyone,
        so nothing in the run supports the operator having been involved.
        """
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
    """True for ACA's apply-flow disclaimer page. Path-only, case-insensitive."""
    return DISCLAIMER_PATH_MARKER in urlparse(url or "").path.casefold()


def disclaimer_frames(page: Any) -> list[Any]:
    """Frames currently rendering the disclaimer, if any."""
    frames = list(getattr(page, "frames", ()) or ())
    return [frame for frame in frames
            if is_attestation_disclaimer_url(str(getattr(frame, "url", "") or ""))]


def is_disclaimer_page(page: Any) -> bool:
    """True while the page (top-level or any frame) is the disclaimer."""
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
    """Wait for a human to satisfy an attestation control. Never operates it.

    `probe` reads the control's state; it is never a way to change it. The
    first probe decides whether there is anything to wait for, so a page with no
    attestation control returns immediately rather than stalling. `notify` runs
    once, on the first probe that still needs the human.
    """
    if timeout_s <= 0:
        raise ValueError("handoff timeout must be positive")
    if poll_s <= 0:
        raise ValueError("handoff poll interval must be positive")

    started = monotonic()
    polls = 0
    notified = False
    # True once we have seen the attestation still unsatisfied, i.e. once there
    # was something for a human to do. Only then can a later satisfied reading
    # be attributed to them. Deliberately independent of `notify`, which is an
    # optional courtesy callback and may be absent.
    awaited_human = False
    while True:
        snapshot = probe()
        if inspect.isawaitable(snapshot):
            snapshot = await snapshot
        polls += 1
        elapsed = monotonic() - started

        # Acceptance is read before absence: a control that disappears
        # *because* the human accepted and the portal advanced is an accepted
        # state, not a missing one.
        if snapshot.satisfied:
            if awaited_human:
                # The run asked the operator to act and the control was
                # satisfied by the time the wait ended. Whose action satisfied
                # it is not something this run can verify: NI ticks its own
                # agree box on load, so the identical transition can come from
                # the portal, and asserting a human here would be a claim the
                # run cannot support.
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
    """Hold the apply flow at ACA's disclaimer until the human accepts it.

    The caller continues on `report.permits_continuation(...)`. When `advance`
    is set and continuation is permitted, the Continue control past the
    disclaimer is clicked — that is plain navigation; the attestation checkbox
    itself is never touched by this module.

    `allow_portal_default` is an explicit, documented opt-in for portals that
    present their own attestation as already accepted (NI pre-ticks its agree
    box and self-advances). It permits continuation over such a control; it
    does not, and must not, relabel that state as a human acceptance.
    """
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
            # Still the disclaimer, so the run is still waiting on the human.
            # A checkbox that is momentarily absent (a postback re-render) is
            # not an abort: only leaving the disclaimer is acceptance.
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
    """Click past the disclaimer after the *human* has accepted it."""
    try:
        control = frame.locator(DISCLAIMER_CONTINUE_CONTROL).first
        if await control.count():
            await control.click(timeout=8000)
    except Exception:
        # Navigation failing is not an attestation problem; the caller's own
        # stall detection deals with a page that did not advance.
        pass


__all__ = [
    "DEFAULT_HANDOFF_TIMEOUT_S", "DEFAULT_POLL_S", "DISCLAIMER_CONTINUE_CONTROL",
    "AttestationProbe", "HandoffOutcome", "HandoffReport",
    "accept_disclaimer_with_human", "await_human_attestation",
    "disclaimer_frames", "is_attestation_disclaimer_url", "is_disclaimer_page",
]
