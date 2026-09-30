"""Structured semantic recovery decisions; never execution authorization.

Inputs must come from verified observations and the immutable user goal, not
instructions embedded in portal prose. Decisions nominate reads/replanning only.
The caller retains policy, identity checks, mutation ledger, and run budgets.
"""
from dataclasses import dataclass
from enum import StrEnum

from licet.phase7.recovery import StateConflict


class RecoveryChoice(StrEnum):
    REPLAN = 'REPLAN'
    RETRY = 'RETRY'
    STOP = 'STOP'


@dataclass(frozen=True)
class SemanticContext:
    identity_verified: bool = False
    policy_denied: bool = False
    auth_required: bool = False
    mutation_uncertain: bool = False
    mutation_verified: bool = False
    mutation_attempted: bool = False
    permit_status: str | None = None
    conflicts: tuple[StateConflict, ...] = ()
    missing_sections: tuple[str, ...] = ()
    required_sections: tuple[str, ...] = ()
    verified_findings: tuple[str, ...] = ()
    goal_satisfied: bool = False
    fresh: bool = True
    refresh_attempted: bool = False
    transient_read_failure: bool = False
    invalid_plan: bool = False
    repeated_state: bool = False
    replan_remaining: int = 0
    read_retry_remaining: int = 0
    required_input_missing: bool = False


@dataclass(frozen=True)
class SemanticDecision:
    choice: RecoveryChoice
    outcome: str
    reason: str
    read_sections: tuple[str, ...] = ()
    invalidate_plan: bool = True


def decide_semantic_recovery(context: SemanticContext) -> SemanticDecision:
    """Choose a bounded semantic path without granting permission to mutate.

    Goal completion is caller-established from independent, current evidence.
    Missing optional sections do not erase verified findings. An unchanged
    post-submit read never establishes that an asynchronous mutation is absent.
    """
    c = context
    partial = 'PARTIAL_SUCCESS' if c.verified_findings and c.identity_verified else 'BLOCKED'
    def stop(reason):
        return SemanticDecision(RecoveryChoice.STOP, partial, reason)
    def read(reason, sections):
        if c.read_retry_remaining <= 0:
            return stop(reason + '; read recovery budget exhausted')
        return SemanticDecision(RecoveryChoice.RETRY, 'IN_PROGRESS', reason, tuple(sorted(set(sections))))
    def replan(reason):
        if c.replan_remaining <= 0:
            return stop(reason + '; replan budget exhausted')
        return SemanticDecision(RecoveryChoice.REPLAN, 'IN_PROGRESS', reason)

    if not c.identity_verified:
        return SemanticDecision(RecoveryChoice.STOP, 'BLOCKED', 'RECORD_IDENTITY_UNVERIFIED')
    if c.policy_denied:
        return stop('Policy denial cannot be recovered around')
    if c.auth_required:
        return stop('AUTH_REQUIRED')
    if c.mutation_uncertain or (c.mutation_attempted and not c.mutation_verified):
        if c.refresh_attempted:
            return stop('Mutation outcome remains unknown; no submission replay')
        return read('Independently reconcile the attempted mutation; never repeat submit', ('inspections',))
    if c.required_input_missing:
        return stop('MISSING_REQUIRED_INPUT')
    unresolved = tuple(conflict for conflict in c.conflicts if not conflict.resolved)
    if unresolved:
        if c.refresh_attempted:
            return stop('Conflicting state remains unresolved; consequential action blocked')
        sources = tuple(source for conflict in unresolved for source in conflict.sources)
        return read('Refresh conflicting sources; timestamps alone do not resolve field semantics', sources)
    if not c.fresh:
        if c.refresh_attempted:
            return stop('Current state remains unverified after refresh')
        return read('Invalidate cached reasoning and refresh goal-relevant state', c.required_sections or ('overview',))
    missing = set(c.missing_sections) & set(c.required_sections)
    if missing:
        if c.refresh_attempted:
            return stop('Required information remains unknown; do not infer absence')
        return read('Gather only missing information required by the goal', missing)
    if c.goal_satisfied:
        return SemanticDecision(RecoveryChoice.STOP, 'SUCCESS', 'Goal independently verified', invalidate_plan=False)
    if (c.permit_status or '').casefold() in {'expired', 'revoked', 'void', 'withdrawn'}:
        return stop(f'Permit is {c.permit_status.casefold()}; renewal or municipal resolution requires separate authorization')
    if c.repeated_state:
        return replan('Previously observed state is not new information; discard the repeated plan')
    if c.invalid_plan:
        return replan('Reject unavailable action; rebuild from verified preconditions')
    if c.transient_read_failure:
        return read('Re-observe before bounded retry of the read', c.required_sections or ('overview',))
    return replan('Preserve verified findings and choose a path supported by current evidence')
