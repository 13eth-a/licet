"""The choke point that makes the safety boundary real.

Phase 0 review §1: `classify()` and `request_approval()` had zero callers, so
an agent could submit an application or accept a legal attestation without a
confirmation check — because the only string in flight was `click`. The guard
takes a *semantic* action (resolved from the tool call by
`licet/browser/dispatcher.py`) and decides whether it may run.

Rules:

- AUTOMATIC actions are allowed.
- CONFIRMATION_REQUIRED actions are held: the guard records
  `AgentState.pending_approval` (which trips the APPROVAL_REQUIRED stop
  condition) and refuses execution until a human grants it.
- Unknown actions are treated as confirmation-required by
  `risk_levels.classify`, so they hold as well rather than silently running.
- A state-changing action needs a positively identified sandbox (Phase 6's core
  rule applies at this layer too) *and* must not contradict the user's own
  instruction, which `run_constraints` parses from trusted text only.
- A granted approval is scoped to the page it was asked on: `AgentState.is_approved`
  refuses a grant whose recorded URL, record or flow step has since moved.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from licet.agent.state import AgentState
from licet.safety.policy import Environment, UserConstraints, environment_from_url
from licet.safety.risk_levels import RiskLevel, changes_state, classify, is_known


class GuardDecision(str, Enum):
    ALLOW = "allow"
    REQUIRE_APPROVAL = "require_approval"
    BLOCK = "block"


@dataclass(frozen=True)
class Authorization:
    action: str
    decision: GuardDecision
    risk: RiskLevel
    reason: str
    # True when this ALLOW came from a human-granted approval, so the caller
    # knows to consume the grant once the action has run.
    approved: bool = False

    @property
    def allowed(self) -> bool:
        return self.decision is GuardDecision.ALLOW

    def as_dict(self) -> dict[str, str | bool]:
        return {
            "action": self.action,
            "decision": self.decision.value,
            "risk": self.risk.value,
            "reason": self.reason,
            "approved": self.approved,
        }


def run_constraints(state: AgentState) -> UserConstraints:
    """The run's permissions, parsed from text the user actually supplied.

    Two trusted sources only: the goal (the instruction this run exists to
    carry out) and `AgentState.user_constraints` (explicit constraint lines the
    caller attached). Nothing a model emits mid-run and nothing read off a
    portal page enters here — that is what makes the Phase 6 constraint rule
    deterministic rather than prompt-based.
    """
    parts = [getattr(state, "goal", "") or "", *getattr(state, "user_constraints", ())]
    return UserConstraints.from_text("\n".join(str(part) for part in parts if part))


def authorize(action: str, state: AgentState, *, context: str = "") -> Authorization:
    """Decide whether `action` may run now, recording approval requests.

    `context` is the human-readable provenance of the action (which tool,
    which flow step) so the approval prompt explains itself.
    """
    if not is_known(action):
        return Authorization(
            action=action,
            decision=GuardDecision.BLOCK,
            risk=classify(action),
            reason=(
                f"'{action}' is not a classified action"
                + (f" ({context})" if context else "")
                + " — refusing to guess its risk."
            ),
        )

    risk = classify(action)
    if risk is RiskLevel.PROHIBITED:
        return Authorization(
            action=action,
            decision=GuardDecision.BLOCK,
            risk=risk,
            reason=(
                f"'{action}' is prohibited, not merely consequential — no user "
                "approval can authorize Licet to do it."
            ),
        )
    # The guard is the primitive layer: it clicks. Phase 6's core rule therefore
    # applies here too — a state-changing action needs a positively identified
    # sandbox, whatever the tier says and whoever approved it. A live municipal
    # record is never mutated through a primitive, and an environment that cannot
    # be established must not mutate at all (unknown is not a synonym for
    # sandbox). Reads remain unrestricted: this is about mutations only.
    if changes_state(action):
        environment = environment_from_url(state.current_url)
        if environment is not Environment.SANDBOX:
            return Authorization(
                action=action,
                decision=GuardDecision.BLOCK,
                risk=risk,
                reason=(
                    f"'{action}' changes record state and the environment is "
                    f"{environment.value} ({state.current_url or 'no page observed yet'}) — "
                    "only a positively identified sandbox may be mutated."
                ),
            )
        # The user's own instruction outranks anything downstream of it, at this
        # layer too: a click that would pay a fee is refused in a sandbox when the
        # goal said not to spend money, exactly as the semantic layer refuses it.
        violated = run_constraints(state).contradiction(action)
        if violated:
            return Authorization(
                action=action,
                decision=GuardDecision.BLOCK,
                risk=risk,
                reason=(
                    f"'{action}' is forbidden by the user's instruction "
                    f"({violated.lower().replace('_', ' ')}); no approval can "
                    "overrule the constraint the run was started with."
                ),
            )
    if risk is RiskLevel.AUTOMATIC:
        return Authorization(
            action=action,
            decision=GuardDecision.ALLOW,
            risk=risk,
            reason=f"'{action}' is an automatic (read-only or reversible) action.",
        )

    # Already approved for this exact action: consume the grant and proceed.
    if state.is_approved(action):
        return Authorization(
            action=action,
            decision=GuardDecision.ALLOW,
            risk=risk,
            reason=f"'{action}' was explicitly approved by the user.",
            approved=True,
        )

    reason = (
        f"'{action}' requires confirmation"
        + (f" ({context})" if context else "")
        + " because it is consequential or hard to reverse."
    )
    # The request records the page it was asked on: approval authorises this
    # operation on this record, and navigation invalidates it (see
    # `AgentState.is_approved`).
    state.request_approval(action, reason, context=context, url=state.current_url,
                           permit=state.current_permit, flow_step=state.flow_step)
    return Authorization(
        action=action, decision=GuardDecision.REQUIRE_APPROVAL, risk=risk, reason=reason
    )


def grant_approval(state: AgentState, action: str | None = None) -> bool:
    """Mark a pending approval as granted. False when nothing matches.

    A human (or an explicit user instruction in the goal) calls this; it does
    not execute anything by itself.
    """
    pending = state.pending_approval
    if pending is None:
        return False
    if action is not None and pending.action != action:
        return False
    state.grant_approval()
    return True


def deny_approval(state: AgentState) -> None:
    state.clear_approval()
