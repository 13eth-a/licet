from __future__ import annotations

from dataclasses import dataclass

from licet.phase4.actions import InspectionAction


@dataclass(frozen=True)
class ActionPolicyDecision:
    allowed: bool
    requires_confirmation: bool
    reason: str


def decide_action_policy(action: InspectionAction, *, confirmed: bool = False) -> ActionPolicyDecision:
    kind = action.action_type.casefold().strip()
    if kind in {"schedule", "schedule_inspection", "reschedule", "reschedule_inspection"}:
        if action.requires_confirmation and not confirmed:
            return ActionPolicyDecision(False, True, "selected action requires explicit confirmation")
        return ActionPolicyDecision(True, action.requires_confirmation, "inspection scheduling is reversible")
    if kind in {"cancel", "cancel_inspection"}:
        if confirmed:
            return ActionPolicyDecision(True, True, "cancellation explicitly confirmed")
        return ActionPolicyDecision(False, True, "cancellation requires explicit confirmation")
    return ActionPolicyDecision(False, False, f"unsupported inspection action: {action.action_type}")
