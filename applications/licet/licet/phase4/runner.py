"""phase 4 runner: reasoning output -> policy -> executor -> verified result"""
from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass
from typing import Any

from licet.phase4.accela_portal import AccelaInspectionPortal
from licet.phase4.actions import InspectionAction, InspectionActionResult
from licet.phase4.dates import DateConstraints, normalize_date_constraints
from licet.phase4.metrics import Phase4Metrics
from licet.phase4.selection import ActionSelection
from licet.phase4.workflow import InspectionActionExecutor
from licet.safety.policy import ConfirmationRequest


@dataclass(frozen=True)
class ActionRequest:
    """what the caller asked for, before normalization"""

    action_type: str
    permit_id: str
    inspection_type: str | None = None
    date_instruction: str | None = None
    preferred_date: str | None = None
    existing_inspection_id: str | None = None
    constraints: tuple[str, ...] = ()
    confirmed: bool = False
    # the scoped, single-use approval for a consequential action
    approval: ConfirmationRequest | None = None
    required_inputs: dict[str, str] | None = None
    date_window_start: str | None = None
    date_window_end: str | None = None
    record_key: str | None = None
    snapshot_id: str | None = None
    evidence_ids: tuple[str, ...] = ()
    requires_confirmation: bool = False
    # report (never select) portal dates outside the window when selection fails
    allow_alternatives: bool = False


def _dates(request: ActionRequest, reference: _dt.date | None) -> DateConstraints:
    """intersect explicit and interpreted constraints; never discard either"""
    parsed = normalize_date_constraints(request.date_instruction, reference=reference)
    iso = lambda value: _dt.date.fromisoformat(value) if value else None
    starts = [d for d in (parsed.start, iso(request.date_window_start)) if d is not None]
    ends = [d for d in (parsed.end, iso(request.date_window_end)) if d is not None]
    start, end = max(starts, default=None), min(ends, default=None)
    preferred = iso(request.preferred_date) or parsed.preferred
    if request.preferred_date and parsed.preferred and preferred != parsed.preferred:
        raise ValueError("explicit date conflicts with the date instruction")
    if start and end and start > end:
        raise ValueError("date constraints have no overlapping window")
    if preferred and ((start and preferred < start) or (end and preferred > end)):
        raise ValueError("requested date lies outside the allowed window")
    return DateConstraints(start, end, preferred, parsed.earliest)


def preview(request: ActionRequest, reference: _dt.date | None = None) -> dict[str, Any]:
    """the human-readable proposed action, with the date window made concrete"""
    constraints = _dates(request, reference)
    return {
        "proposed_action": request.action_type,
        "permit_id": request.permit_id,
        "inspection_type": request.inspection_type,
        "date_window_start": constraints.start.isoformat() if constraints.start else None,
        "date_window_end": constraints.end.isoformat() if constraints.end else None,
        "preferred_date": constraints.preferred.isoformat() if constraints.preferred else None,
        "requires_confirmation": request.requires_confirmation or request.action_type.casefold().strip() in {"cancel", "cancel_inspection"},
        "constraints": list(request.constraints),
        "record_key": request.record_key, "snapshot_id": request.snapshot_id,
        "evidence_ids": list(request.evidence_ids),
        "allow_alternatives": request.allow_alternatives,
    }


def action_from_selection(selection: ActionSelection, confirmed: bool = False) -> ActionRequest:
    """a phase 3 `actionselection` becomes a normalized request (no browser)"""
    if selection.action is None:
        raise ValueError("Phase 3 reasoning did not select an action; nothing to preview")
    for name in ("record_key", "snapshot_id"):
        selected_value, action_value = getattr(selection, name), getattr(selection.action, name)
        if selected_value and action_value and selected_value != action_value:
            raise ValueError(f"selection and action disagree on {name}")
    return ActionRequest(
        action_type=selection.action.action_type,
        permit_id=selection.action.permit_id,
        inspection_type=selection.action.inspection_type,
        date_window_start=selection.action.date_window_start,
        date_window_end=selection.action.date_window_end,
        record_key=selection.record_key or selection.action.record_key,
        snapshot_id=selection.snapshot_id or selection.action.snapshot_id,
        evidence_ids=tuple(dict.fromkeys((*selection.evidence_ids, *selection.action.evidence_ids))),
        requires_confirmation=selection.requires_confirmation or selection.action.requires_confirmation,
        preferred_date=selection.action.preferred_date,
        existing_inspection_id=selection.action.existing_inspection_id,
        constraints=tuple(selection.action.constraints or ()),
        confirmed=confirmed,
        # the caller (a human at a cli, naming this record and type) approves the selection in front of
        # them; the approval is scoped to exactly that action, so it cannot travel to another record or
        # inspection
        approval=(ConfirmationRequest(
            action_type=selection.action.action_type,
            permit_id=selection.action.permit_id,
            target=selection.action.inspection_type or selection.action.existing_inspection_id or "",
            consequence=f"{selection.action.action_type} "
                        f"{selection.action.inspection_type or selection.action.existing_inspection_id or 'the record'}",
            inspection_id=selection.action.existing_inspection_id,
            record_key=selection.record_key or selection.action.record_key,
            date_window_start=selection.action.date_window_start,
            date_window_end=selection.action.date_window_end,
        ) if confirmed else None),
    )


class Phase4ActionRunner:
    """executes one inspection action end to end and reports honestly"""

    def __init__(
        self,
        portal: AccelaInspectionPortal,
        *,
        executor: InspectionActionExecutor | None = None,
        metrics: Phase4Metrics | None = None,
        logger: Any | None = None,
    ) -> None:
        self.portal = portal
        self.executor = executor or InspectionActionExecutor(portal)
        self.metrics = metrics
        self.logger = logger

    def run(
        self,
        request: ActionRequest,
        *,
        reference: _dt.date | None = None,
        eligible_types: list[str] | tuple[str, ...] = (),
        available_dates: list[str] | tuple[str, ...] = (),
    ) -> InspectionActionResult:
        """preview -> policy -> execute"""
        constraints = _dates(request, reference)
        action = InspectionAction(
            action_type=request.action_type,
            permit_id=request.permit_id,
            inspection_type=request.inspection_type,
            preferred_date=constraints.preferred.isoformat() if constraints.preferred else None,
            # exact requested dates must not fall back to a different day
            date_window_start=(constraints.preferred or constraints.start).isoformat() if (constraints.preferred or constraints.start) else None,
            date_window_end=(constraints.preferred or constraints.end).isoformat() if (constraints.preferred or constraints.end) else None,
            existing_inspection_id=request.existing_inspection_id,
            constraints=list(request.constraints),
            record_key=request.record_key, snapshot_id=request.snapshot_id,
            evidence_ids=tuple(request.evidence_ids),
            requires_confirmation=request.requires_confirmation,
        )
        recorded = len(getattr(self.executor, "audits", ()))
        result = self.executor.execute(
            action,
            eligible_types=eligible_types,
            available_dates=available_dates,
            required_inputs=request.required_inputs,
            confirmed=request.confirmed,
            approval=request.approval,
            allow_alternatives=request.allow_alternatives,
        )
        if self.metrics is not None or self.logger is not None:
            audits = tuple(getattr(self.executor, "audits", ())[recorded:])
            if self.metrics is not None:
                self.metrics.record(action, result, audits)
            if self.logger is not None:
                for audit in audits:
                    self.logger.log_event("mutation_audit", audit=audit.as_dict())
        return result


__all__ = ["ActionRequest", "Phase4ActionRunner", "action_from_selection", "preview"]
