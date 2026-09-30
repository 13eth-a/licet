"""stop conditions for the agent loop"""

from __future__ import annotations

from enum import Enum

from licet.agent.state import AgentState
from licet.config import MAX_STEPS

# a given (page, action, args) may fail this many times before we stop
MAX_REPEATED_FAILURES = 2
# consecutive steps that observed the same url before we call it a stall
MAX_STALLED_STEPS = 3


class StopCondition(str, Enum):
    GOAL_COMPLETED = "goal_completed"
    APPROVAL_REQUIRED = "approval_required"
    MISSING_INFORMATION = "missing_information"
    NO_VALID_ACTION = "no_valid_action"
    PORTAL_UNAVAILABLE = "portal_unavailable"
    MAX_STEPS_EXCEEDED = "max_steps_exceeded"
    REPEATED_ACTION_FAILED = "repeated_action_failed"
    AMBIGUOUS_RECORD = "ambiguous_record"


def check_stop_condition(
    state: AgentState,
    *,
    goal_completed: bool = False,
    max_steps: int | None = None,
    max_repeated_failures: int = MAX_REPEATED_FAILURES,
    max_stalled_steps: int = MAX_STALLED_STEPS,
) -> StopCondition | None:
    """return the first stop condition that applies, or none to keep going"""
    if goal_completed:
        return StopCondition.GOAL_COMPLETED

    if state.pending_approval is not None:
        return StopCondition.APPROVAL_REQUIRED

    if state.missing_information:
        return StopCondition.MISSING_INFORMATION

    if state.ambiguous_candidates:
        return StopCondition.AMBIGUOUS_RECORD

    if state.no_valid_action_reason:
        return StopCondition.NO_VALID_ACTION

    if state.portal_issue:
        return StopCondition.PORTAL_UNAVAILABLE

    step_limit = max_steps if max_steps is not None else MAX_STEPS
    if state.step_count >= step_limit:
        return StopCondition.MAX_STEPS_EXCEEDED

    for failure in state.failed_actions:
        if failure.attempt_count > max_repeated_failures:
            return StopCondition.REPEATED_ACTION_FAILED

    if state.stalled_steps >= max_stalled_steps:
        return StopCondition.REPEATED_ACTION_FAILED

    return None


def describe_stop(
    state: AgentState,
    condition: StopCondition,
    *,
    max_repeated_failures: int = MAX_REPEATED_FAILURES,
) -> str:
    """explain a stop in one line, for the final report and the run log"""
    if condition is StopCondition.GOAL_COMPLETED:
        return f"Goal completed: {state.goal}"

    if condition is StopCondition.APPROVAL_REQUIRED:
        approval = state.pending_approval
        if approval is None:
            return "Waiting for user approval."
        return f"Waiting for user approval before '{approval.action}': {approval.reason}"

    if condition is StopCondition.MISSING_INFORMATION:
        return "Missing required information: " + ", ".join(state.missing_information)

    if condition is StopCondition.AMBIGUOUS_RECORD:
        return (
            "Cannot confidently identify the correct record; candidates: "
            + ", ".join(state.ambiguous_candidates)
        )

    if condition is StopCondition.NO_VALID_ACTION:
        return f"No valid action available: {state.no_valid_action_reason}"

    if condition is StopCondition.PORTAL_UNAVAILABLE:
        return f"Portal unavailable: {state.portal_issue}"

    if condition is StopCondition.MAX_STEPS_EXCEEDED:
        return f"Step limit reached after {state.step_count} steps."

    if condition is StopCondition.REPEATED_ACTION_FAILED:
        worst = max(state.failed_actions, key=lambda f: f.attempt_count, default=None)
        if worst is not None and worst.attempt_count > max_repeated_failures:
            return (
                f"Action '{worst.action}' failed {worst.attempt_count} times on "
                f"{worst.page or 'an unidentified page'}: {worst.error}"
            )
        return (
            f"No progress after {state.stalled_steps} consecutive steps on "
            f"{state.current_url or 'the same page'}."
        )

    return condition.value
