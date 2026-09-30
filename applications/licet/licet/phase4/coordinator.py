"""phase 4 coordinator: the end to end entry point"""
from __future__ import annotations

import datetime as _dt
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Any, Protocol

from licet.phase3.state import ReasoningResult
from licet.phase4.actions import InspectionAction, InspectionActionResult, MutationAudit
from licet.phase4.metrics import Phase4Metrics
from licet.phase4.runner import ActionRequest, Phase4ActionRunner, action_from_selection, preview
from licet.phase4.selection import ActionSelection, SelectionContext, select_inspection_action


class InspectionPortal(Protocol):
    """structural stand in for the adapter, so the coordinator stays offline testable"""

    def read_inspection_state(self, permit_id: str, inspection_type: str | None = None, inspection_id: str | None = None): ...
    def submit_inspection_action(self, action: InspectionAction, *, portal_type: str, selected_date: str | None = None): ...


@dataclass(frozen=True)
class WorkflowOutcome:
    """the single result of one end to end attempt, whatever stage stopped it"""

    stage: str
    status: str
    message: str
    selection: ActionSelection | None = None
    request: ActionRequest | None = None
    preview: dict[str, Any] | None = None
    result: InspectionActionResult | None = None
    audits: tuple[MutationAudit, ...] = ()
    needed_state: tuple[str, ...] = ()

    @property
    def selected(self) -> bool:
        return self.selection is not None and self.selection.action is not None

    @property
    def executed(self) -> bool:
        return self.result is not None

    @property
    def success(self) -> bool:
        """true only for an executed, independently verified success"""
        return bool(self.result is not None and self.result.success and self.result.verified)

    def as_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "status": self.status,
            "message": self.message,
            "selected": self.selected,
            "executed": self.executed,
            "success": self.success,
            "inspection_type": self.request.inspection_type if self.request else None,
            "permit_id": self.request.permit_id if self.request else None,
            "preview": self.preview,
            "result": self.result.as_dict() if self.result else None,
            "audits": [audit.as_dict() for audit in self.audits],
            "needed_state": list(self.needed_state),
        }


def _execution_message(result: InspectionActionResult) -> str:
    if result.success:
        return "verified: observed portal state matches the requested action"
    return result.error or result.verification_state.value


def run_inspection_workflow(
    reasoning: ReasoningResult,
    *,
    permit_id: str,
    context: SelectionContext,
    portal: InspectionPortal,
    reference: _dt.date | None = None,
    available_dates: Sequence[str] = (),
    required_inputs: dict[str, str] | None = None,
    requested_action: InspectionAction | None = None,
    confirmed: bool = False,
    allow_alternatives: bool = False,
    metrics: Phase4Metrics | None = None,
    logger: Any | None = None,
    expected_action: InspectionAction | None = None,
) -> WorkflowOutcome:
    """select, authorize and execute one inspection action; report honestly"""
    selection = select_inspection_action(
        reasoning, permit_id=permit_id, context=context, requested_action=requested_action
    )
    if metrics is not None:
        metrics.record_selection(selection, expected=expected_action)
    if selection.action is None:
        return WorkflowOutcome(
            "selection", selection.status.value, selection.reason,
            selection=selection, needed_state=selection.needed_state,
        )

    request = replace(
        action_from_selection(selection, confirmed=confirmed),
        required_inputs=required_inputs,
        allow_alternatives=allow_alternatives,
    )
    try:
        shown = preview(request, reference)
    except ValueError as exc:
        # date language that cannot be made concrete stops before any browser step
        return WorkflowOutcome(
            "validation", "INVALID_REQUEST", str(exc), selection=selection, request=request
        )

    eligible_types = tuple(option.name for option in context.options)
    if not eligible_types and request.inspection_type:
        eligible_types = (request.inspection_type,)
    runner = Phase4ActionRunner(portal, metrics=metrics, logger=logger)
    try:
        result = runner.run(
            request, reference=reference, eligible_types=eligible_types, available_dates=available_dates
        )
    except ValueError as exc:
        return WorkflowOutcome(
            "validation", "INVALID_REQUEST", str(exc),
            selection=selection, request=request, preview=shown,
        )
    return WorkflowOutcome(
        "execution", result.verification_state.value, _execution_message(result),
        selection=selection, request=request, preview=shown, result=result,
        audits=tuple(runner.executor.audits),
    )


__all__ = ["WorkflowOutcome", "run_inspection_workflow"]
