"""the choke point that makes the safety boundary real"""

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
    """the run's permissions, parsed from text the user actually supplied"""
    parts = [getattr(state, "goal", "") or "", *getattr(state, "user_constraints", ())]
    return UserConstraints.from_text("\n".join(str(part) for part in parts if part))


def authorize(action: str, state: AgentState, *, context: str = "") -> Authorization:
    """decide whether `action` may run now, recording approval requests"""
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
    # the guard is the primitive layer: it clicks
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
        # the user's own instruction outranks anything downstream of it, at this layer too: a click that
        # would pay a fee is refused in a sandbox when the goal said not to spend money, exactly as the
        # semantic layer refuses it
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
    state.request_approval(action, reason, context=context, url=state.current_url,
                           permit=state.current_permit, flow_step=state.flow_step)
    return Authorization(
        action=action, decision=GuardDecision.REQUIRE_APPROVAL, risk=risk, reason=reason
    )


def grant_approval(state: AgentState, action: str | None = None) -> bool:
    """mark a pending approval as granted"""
    pending = state.pending_approval
    if pending is None:
        return False
    if action is not None and pending.action != action:
        return False
    state.grant_approval()
    return True


def deny_approval(state: AgentState) -> None:
    state.clear_approval()
