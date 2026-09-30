"""phase 4 scripted-action fixtures and the checklist's 25 action cases"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from licet.phase4.actions import InspectionAction, InspectionActionResult, InspectionSnapshot
from licet.phase4.workflow import InspectionActionExecutor
from licet.safety.policy import ConfirmationRequest, Environment


PERMIT_ID = "BLD-2026-0147"
RECORD_KEY = "NULLISLAND/Building/REC26/00000/9F147"
OTHER_RECORD_KEY = "NULLISLAND/Building/REC26/00000/OTHER"
INSPECTION_TYPE = "Rough Electrical"
INSPECTION_ID = "I-1"
EXISTING_INSPECTION_ID = "I-2"

DEFAULT_DATES = ("2026-09-24",)


class ScriptedPortal:
    # a double states its own environment, exactly as the real adapter derives it from the session url
    environment = Environment.SANDBOX
    """An ``InspectionPortal`` double with a call log.

    The first read returns ``before``; later reads return ``after`` (or ``before``
    again when ``after`` is None, modelling a submission that changed nothing).
    ``error`` makes the submit raise, exercising the uncertain-submission path.
    """

    def __init__(
        self,
        before: InspectionSnapshot,
        *,
        after: InspectionSnapshot | None = None,
        error: Exception | None = None,
    ) -> None:
        self.before = before
        self.after = after
        self.error = error
        self.reads: list[tuple[str, str | None, str | None]] = []
        self.submits: list[tuple[str, str | None]] = []

    def read_inspection_state(
        self, permit_id: str, inspection_type: str | None = None, inspection_id: str | None = None
    ) -> InspectionSnapshot:
        self.reads.append((permit_id, inspection_type, inspection_id))
        if len(self.reads) == 1 or self.after is None:
            return self.before
        return self.after

    def submit_inspection_action(
        self, action: InspectionAction, *, portal_type: str, selected_date: str | None = None
    ) -> str | None:
        self.submits.append((portal_type, selected_date))
        if self.error is not None:
            raise self.error
        return "CNF-FIXTURE-1"


def snapshot(**overrides: object) -> InspectionSnapshot:
    """a not-scheduled, eligible inspection on the default fixture permit"""
    values: dict[str, object] = {
        "permit_id": PERMIT_ID,
        "inspection_id": INSPECTION_ID,
        "inspection_type": INSPECTION_TYPE,
        "status": "Not Scheduled",
        "record_key": RECORD_KEY,
    }
    values.update(overrides)
    return InspectionSnapshot(**values)  # type: ignore[arg-type]


def action(kind: str = "schedule", **overrides: object) -> InspectionAction:
    """a default action for the fixture permit and inspection type"""
    values: dict[str, object] = {
        "action_type": kind,
        "permit_id": PERMIT_ID,
        "inspection_type": INSPECTION_TYPE,
    }
    values.update(overrides)
    return InspectionAction(**values)  # type: ignore[arg-type]


@dataclass(frozen=True)
class ActionCase:
    """one checklist action case: fixture state in, expected result out"""

    case_id: str
    group: str
    description: str
    action: InspectionAction
    before: InspectionSnapshot
    after: InspectionSnapshot | None = None
    error: Exception | None = None
    eligible_types: tuple[str, ...] = (INSPECTION_TYPE,)
    available_dates: tuple[str, ...] = DEFAULT_DATES
    required_inputs: dict[str, str] | None = None
    confirmed: bool = False
    allow_alternatives: bool = False
    expect_success: bool = True
    expect_error: str | None = None
    expect_verification: str = "VERIFIED_SUCCESS"
    expect_submits: int = 1
    expect_portal_type: str | None = None
    expect_read_inspection_id: str | None = None
    expect_alternatives: tuple[str, ...] = ()


def run_case(case: ActionCase) -> tuple[InspectionActionResult, ScriptedPortal]:
    """execute one case through the real executor; return its result and portal"""
    portal = ScriptedPortal(case.before, after=case.after, error=case.error)
    executor = InspectionActionExecutor(portal)
    # a confirmed case means a human approved *this* case, so the caller presents the scoped, single-use
    # approval
    result = executor.execute(
        case.action,
        eligible_types=case.eligible_types,
        available_dates=case.available_dates,
        required_inputs=case.required_inputs,
        confirmed=case.confirmed,
        approval=(ConfirmationRequest(
            action_type=case.action.action_type, permit_id=case.action.permit_id,
            target=case.action.inspection_type or case.action.existing_inspection_id or "",
            consequence="fixture approval", inspection_id=case.action.existing_inspection_id,
        ) if case.confirmed else None),
        allow_alternatives=case.allow_alternatives,
    )
    return result, portal


def _scheduled(today: str) -> InspectionSnapshot:
    return snapshot(status="Scheduled", scheduled_date=today)


def build_cases() -> list[ActionCase]:
    """the phase 4 checklist split: 10 scheduling, 5 rescheduling, 5 cancel, 5 safety"""
    cases: list[ActionCase] = []

    cases += [
        ActionCase(
            "S01", "scheduling", "one eligible inspection schedules and verifies",
            action(), snapshot(), after=_scheduled("2026-09-24"),
        ),
        ActionCase(
            "S02", "scheduling", "the exact type is matched among several eligible ones",
            action(), snapshot(), after=_scheduled("2026-09-24"),
            eligible_types=("Electrical Final", INSPECTION_TYPE),
            expect_portal_type=INSPECTION_TYPE,
        ),
        ActionCase(
            "S03", "scheduling", "an exact requested date that is available is used",
            action(preferred_date="2026-09-24"), snapshot(), after=_scheduled("2026-09-24"),
            available_dates=("2026-09-22", "2026-09-24"),
        ),
        ActionCase(
            "S04", "scheduling", "an unavailable exact requested date fails without substituting",
            action(preferred_date="2026-09-25"), snapshot(),
            available_dates=("2026-09-22", "2026-09-24"),
            expect_success=False, expect_error="DATE_CONSTRAINT_UNSATISFIED",
            expect_verification="VERIFIED_FAILURE", expect_submits=0,
        ),
        ActionCase(
            "S05", "scheduling", "the earliest available date inside the window is chosen",
            action(date_window_start="2026-09-21", date_window_end="2026-09-27"),
            snapshot(), after=_scheduled("2026-09-22"),
            available_dates=("2026-09-25", "2026-09-22", "2026-09-30"),
        ),
        ActionCase(
            "S06", "scheduling", "no availability is reported, not guessed around",
            action(), snapshot(), available_dates=(),
            expect_success=False, expect_error="NO_AVAILABLE_DATES",
            expect_verification="VERIFIED_FAILURE", expect_submits=0,
        ),
        ActionCase(
            "S07", "scheduling", "an already-scheduled inspection is idempotent, not resubmitted",
            action(), _scheduled("2026-09-24"),
            expect_success=False, expect_error="INSPECTION_ALREADY_SCHEDULED",
            expect_verification="VERIFIED_FAILURE", expect_submits=0,
        ),
        ActionCase(
            "S08", "scheduling", "a missing required field stops before submission",
            action(), snapshot(required_fields=("phone",)), required_inputs={},
            expect_success=False, expect_error="MISSING_REQUIRED_INPUT",
            expect_verification="VERIFIED_FAILURE", expect_submits=0,
        ),
        ActionCase(
            "S09", "scheduling", "an inspection type the portal does not offer is refused",
            action(), snapshot(), eligible_types=("Electrical Final",),
            expect_success=False, expect_error="INSPECTION_NOT_ELIGIBLE",
            expect_verification="VERIFIED_FAILURE", expect_submits=0,
        ),
        ActionCase(
            "S10", "scheduling", "an error after submit stays unverified, never success",
            action(), snapshot(), error=TimeoutError(),
            expect_success=False, expect_error="UNCERTAIN_SUBMISSION",
            expect_verification="UNVERIFIED", expect_submits=1,
        ),
    ]

    reschedule = action("reschedule", existing_inspection_id=INSPECTION_ID)
    cases += [
        ActionCase(
            "R01", "rescheduling", "moving an appointment to a later date verifies",
            reschedule, _scheduled("2026-09-20"), after=_scheduled("2026-09-24"),
        ),
        ActionCase(
            "R02", "rescheduling", "moving an appointment to an earlier date verifies",
            reschedule, _scheduled("2026-09-28"), after=_scheduled("2026-09-22"),
            available_dates=("2026-09-22",),
        ),
        ActionCase(
            "R03", "rescheduling", "an unavailable requested reschedule date fails",
            action("reschedule", existing_inspection_id=INSPECTION_ID, preferred_date="2026-09-25"),
            _scheduled("2026-09-20"), available_dates=("2026-09-22",),
            expect_success=False, expect_error="DATE_CONSTRAINT_UNSATISFIED",
            expect_verification="VERIFIED_FAILURE", expect_submits=0,
        ),
        ActionCase(
            "R04", "rescheduling", "the read addresses the exact requested appointment",
            reschedule, _scheduled("2026-09-20"), after=_scheduled("2026-09-24"),
            expect_read_inspection_id=INSPECTION_ID,
        ),
        ActionCase(
            "R05", "rescheduling", "a different inspection id is protected from mutation",
            reschedule, snapshot(inspection_id=EXISTING_INSPECTION_ID, status="Scheduled", scheduled_date="2026-09-20"),
            expect_success=False, expect_error="STATE_MISMATCH",
            expect_verification="VERIFIED_FAILURE", expect_submits=0,
        ),
    ]

    cancel = action("cancel", existing_inspection_id=INSPECTION_ID)
    cases += [
        ActionCase(
            "C01", "cancellation", "a confirmed cancellation is submitted and verified",
            cancel, _scheduled("2026-09-24"), after=snapshot(status="Cancelled"),
            confirmed=True,
        ),
        ActionCase(
            "C02", "cancellation", "an already-cancelled inspection is refused",
            cancel, snapshot(status="Cancelled"), confirmed=True,
            expect_success=False, expect_error="CANCELLATION_FAILED",
            expect_verification="VERIFIED_FAILURE", expect_submits=0,
        ),
        ActionCase(
            "C03", "cancellation", "a completed inspection cannot be cancelled",
            cancel, snapshot(status="Completed"), confirmed=True,
            expect_success=False, expect_error="CANCELLATION_FAILED",
            expect_verification="VERIFIED_FAILURE", expect_submits=0,
        ),
        ActionCase(
            "C04", "cancellation", "the confirmation boundary holds before any portal call",
            cancel, _scheduled("2026-09-24"),
            expect_success=False, expect_error="ACTION_REQUIRES_CONFIRMATION",
            expect_verification="VERIFIED_FAILURE", expect_submits=0,
        ),
        ActionCase(
            "C05", "cancellation", "the cancellation result reaches VERIFIED_SUCCESS",
            cancel, _scheduled("2026-09-24"), after=snapshot(status="Cancelled"),
            confirmed=True, expect_verification="VERIFIED_SUCCESS",
        ),
    ]

    cases += [
        ActionCase(
            "F01", "safety", "an in-flight request blocks a duplicate submission",
            action(), snapshot(status="Requested"),
            expect_success=False, expect_error="INSPECTION_ALREADY_SCHEDULED",
            expect_verification="VERIFIED_FAILURE", expect_submits=0,
        ),
        ActionCase(
            "F02", "safety", "an uncertain submit reconciles to success without replaying",
            action(), snapshot(), after=_scheduled("2026-09-24"), error=TimeoutError(),
            expect_submits=1,
        ),
        ActionCase(
            "F03", "safety", "a post-submit state mismatch is never success",
            action(), snapshot(), after=_scheduled("2026-09-25"),
            expect_success=False, expect_error="ACTION_VERIFICATION_FAILED",
            expect_verification="STATE_MISMATCH", expect_submits=1,
        ),
        ActionCase(
            "F04", "safety", "an action bound to another record key is refused",
            action(record_key=OTHER_RECORD_KEY), snapshot(),
            expect_success=False, expect_error="STATE_MISMATCH",
            expect_verification="VERIFIED_FAILURE", expect_submits=0,
        ),
        ActionCase(
            "F05", "safety", "a same-date reschedule is refused, not reported success",
            action("reschedule", existing_inspection_id=INSPECTION_ID),
            _scheduled("2026-09-24"),
            expect_success=False, expect_error="RESCHEDULE_FAILED",
            expect_verification="VERIFIED_FAILURE", expect_submits=0,
        ),
    ]
    return cases


def case_ids(cases: Iterable[ActionCase]) -> list[str]:
    """parametrisation ids for the case table"""
    return [case.case_id for case in cases]


__all__ = [
    "ActionCase",
    "DEFAULT_DATES",
    "EXISTING_INSPECTION_ID",
    "INSPECTION_ID",
    "INSPECTION_TYPE",
    "OTHER_RECORD_KEY",
    "PERMIT_ID",
    "RECORD_KEY",
    "ScriptedPortal",
    "action",
    "build_cases",
    "case_ids",
    "run_case",
    "snapshot",
]
