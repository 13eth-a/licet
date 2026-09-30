"""the single choke point between the planner and the browser"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable

from licet.agent.state import AgentState
from licet.browser import accela
from licet.browser.errors import BrowserError, ToolError
from licet.browser.solari_client import SolariClient, Target, ToolResult
from licet.browser.tools import READ_PAGE_INCLUDES, TOOL_NAMES
from licet.logging.logger import RunLogger, StepLog
from licet.safety.guard import Authorization, GuardDecision, authorize
from licet.safety.risk_levels import (
    COMMIT_ACKNOWLEDGING_ACTIONS,
    KNOWN_ACTIONS,
    RiskLevel,
    classify,
)

TOOL_DEFAULT_ACTIONS: dict[str, str] = {
    "navigate": "navigate",
    "read_page": "read_record",
    "screenshot": "screenshot",
    "wait": "wait",
}

# actions whose own success flag is not trusted: the dispatcher re reads the portal instead of the planner
# having to remember to, matching `docs/architecture.md`'s "verify outcome" stage
VERIFY_AFTER_ACTIONS: frozenset[str] = frozenset(
    {"schedule_inspection", "reschedule_inspection"}
    | {action for action in KNOWN_ACTIONS if classify(action) is RiskLevel.CONFIRMATION_REQUIRED}
)

# read only ui labels that look risky to a substring match but are not
BENIGN_TARGETS: dict[str, str] = {
    "inspections": "read_record",
    "payments": "read_record",
    "payment": "read_record",
    "fees": "read_record",
    "attachments": "read_record",
    "documents": "read_record",
    "record info": "read_record",
    "inspection history": "read_record",
    "my records": "list_records",
    "my account": "read_record",
    "dashboard": "navigate",
    "search applications": "search_records",
    "create an application": "navigate",
    "logout": "logout",
}

# dangerous phrases > semantic action
DANGEROUS_PHRASES: tuple[tuple[str, str], ...] = (
    ("withdraw", "withdraw_application"),
    ("cancel", "cancel_inspection"),
    ("delete", "delete_record"),
    ("make a payment", "enter_payment_details"),
    ("pay now", "enter_payment_details"),
    ("submit payment", "enter_payment_details"),
    ("continue to payment", "enter_payment_details"),
    ("i agree", "accept_legal_attestation"),
    ("agree and submit", "accept_legal_attestation"),
    ("accept terms", "accept_legal_attestation"),
    ("sign", "sign_document"),
    ("upload", "upload_document"),
    ("submit application", "submit_application"),
    ("submit", "submit_application"),
)


@dataclass(frozen=True)
class ToolCall:
    name: str
    args: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def parse(cls, raw: "ToolCall | dict[str, Any]") -> "ToolCall":
        if isinstance(raw, ToolCall):
            return raw
        name = str(raw.get("name") or raw.get("tool") or "")
        args = raw.get("args") or raw.get("input") or {}
        return cls(name=name, args=dict(args) if isinstance(args, dict) else {})


@dataclass(frozen=True)
class Resolution:
    """what a tool call means, and how we decided"""

    action: str | None
    provenance: str
    context: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "provenance": self.provenance,
            "context": self.context,
        }


_RISK_ORDER: dict[RiskLevel, int] = {
    RiskLevel.AUTOMATIC: 0,
    RiskLevel.CONFIRMATION_REQUIRED: 1,
    RiskLevel.PROHIBITED: 2,
}


def _risk_rank(action: str | None) -> int:
    return _RISK_ORDER[classify(action)] if action else -1


def _target_resolution(call: ToolCall, state: AgentState, on_commit_step: bool, flow: Any) -> Resolution | None:
    """what the control being operated *is*, read from its own text"""
    if call.name not in {"click", "type", "select"}:
        return None
    target = str(call.args.get("target") or "").strip().lower()
    haystack = f"{target} {str(call.args.get('value') or '').lower()}"
    for phrase, action in DANGEROUS_PHRASES:
        if re.search(rf"\b{re.escape(phrase)}\b", haystack):
            return Resolution(action, "target_text", f"target text contains '{phrase}'")
    if target in BENIGN_TARGETS:
        return Resolution(BENIGN_TARGETS[target], "benign_target", f"'{target}' is a read-only section")
    if on_commit_step:
        return Resolution(flow.commit_action, "commit_point",
                          f"{flow.name} step '{state.flow_step}' is its commit point")
    return None


def resolve_action(call: ToolCall, state: AgentState) -> Resolution:
    # a tool that does not exist is a different error from an action that cannot be classified: telling a
    # model to "supply intent=" on a tool name it invented is advice it cannot follow (found by the
    # planner's retry test)
    if call.name not in TOOL_NAMES:
        return Resolution(
            None,
            "unresolved",
            f"unknown tool '{call.name}' — this run exposes: {', '.join(TOOL_NAMES)}",
        )

    flow = accela.FLOWS.get(state.flow_name or "")
    on_commit_step = bool(flow and state.flow_step == flow.commit_step)

    intent = str(call.args.get("intent") or "")
    if intent:
        if intent not in KNOWN_ACTIONS:
            return Resolution(
                None, "unresolved", f"intent '{intent}' is not a classified action"
            )
        # a commit step cannot be satisfied by an intent that does not acknowledge the commit: otherwise
        # `intent="read_record"` on the review step would submit the application under a harmless label
        if on_commit_step and intent not in COMMIT_ACKNOWLEDGING_ACTIONS:
            return Resolution(
                flow.commit_action,
                "commit_point",
                f"{flow.name} step '{state.flow_step}' commits, and intent "
                f"'{intent}' does not acknowledge that",
            )
        # an intent may refine an ambiguous control, but it may never *lower* what the control's own text
        # says it does
        from_target = _target_resolution(call, state, on_commit_step, flow)
        if from_target is not None and _risk_rank(from_target.action) > _risk_rank(intent):
            return Resolution(
                from_target.action,
                from_target.provenance,
                f"{from_target.context}; intent '{intent}' cannot lower that",
            )
        return Resolution(intent, "intent", f"caller intent on '{call.name}'")

    from_target = _target_resolution(call, state, on_commit_step, flow)
    if from_target is not None:
        return from_target

    default = TOOL_DEFAULT_ACTIONS.get(call.name)
    if default:
        return Resolution(default, "tool_default", f"'{call.name}' is a read/wait verb")

    return Resolution(
        None,
        "unresolved",
        (
            f"'{call.name}' on target "
            f"{call.args.get('target')!r} could not be mapped to a classified action — "
            "supply intent= (unclassified calls are blocked rather than guessed)"
        ),
    )


def _key_args(args: dict[str, Any]) -> dict[str, str]:
    return {str(key): str(value) for key, value in args.items() if key != "include"}


class ToolDispatcher:
    """guard + execute + record"""

    def __init__(
        self,
        client: SolariClient,
        *,
        authorize_fn: Callable[..., Authorization] = authorize,
        logger: RunLogger | None = None,
    ) -> None:
        self.client = client
        self.authorize_fn = authorize_fn
        self.logger = logger

    async def execute(
        self, raw: "ToolCall | dict[str, Any]", state: AgentState
    ) -> dict[str, Any]:
        call = ToolCall.parse(raw)
        key_args = _key_args(call.args)
        resolution = resolve_action(call, state)

        if resolution.action is None:
            auth = Authorization(
                action=f"unclassified:{call.name}",
                decision=GuardDecision.BLOCK,
                risk=RiskLevel.CONFIRMATION_REQUIRED,
                reason=resolution.context,
            )
        else:
            auth = self.authorize_fn(resolution.action, state, context=resolution.context)

        if not auth.allowed:
            state.record_failure(call.name, auth.reason, args=key_args)
            kind = (
                BrowserError.AUTH_REQUIRED
                if auth.decision is GuardDecision.REQUIRE_APPROVAL
                else BrowserError.UNKNOWN
            )
            blocked = {
                "success": False,
                "action": call.name,
                "url": state.current_url,
                "observation": "",
                "blocked": True,
                "semantic_action": resolution.action,
                "authorization": auth.as_dict(),
                "resolution": resolution.as_dict(),
                "error": ToolError(kind, auth.reason).as_dict(),
            }
            self._log(state, call, blocked)
            return blocked

        result = await self._invoke(call)
        if result.error and result.error.kind is BrowserError.ACTION_OUTCOME_UNKNOWN:
            state.record_no_valid_action(result.error.message)

        verification: ToolResult | None = None
        if result.ok and resolution.action in VERIFY_AFTER_ACTIONS:
            verification = await self.client.read_page()
        self._sync_position(state, verification or result)

        action_ok = result.ok and (verification is None or verification.ok)
        if action_ok:
            state.record_success(call.name, args=key_args)
            state.record_step(f"{call.name} {call.args.get('target') or call.args.get('url') or ''}".strip())
            if auth.approved:
                # consume the grant so approval_required does not fire on the action we just performed
                state.clear_approval()
            failure_screenshot = None
        else:
            message = (
                verification.error.message
                if result.ok and verification is not None and verification.error
                else result.error.message if result.error else "unknown tool failure"
            )
            state.record_failure(call.name, message, args=key_args)
            # capture the failure state for the run log
            try:
                failure_screenshot = await self.client.screenshot()
            except Exception:  # noqa: BLE001
                failure_screenshot = None

        outcome = {
            **result.as_dict(),
            "action": call.name,
            "observation": str((result.data or {}).get("text") or ""),
            "blocked": False,
            "semantic_action": resolution.action,
            "authorization": auth.as_dict(),
            "resolution": resolution.as_dict(),
        }
        if verification is not None:
            outcome["verification"] = verification.as_dict()
            if not verification.ok:
                # a state changing action is not a success merely because the provider accepted the click
                verification_error = verification.error or ToolError(
                    BrowserError.UNKNOWN, "post-action verification failed"
                )
                outcome["success"] = False
                outcome["verified"] = False
                outcome["verification_error"] = verification_error.as_dict()
                outcome["error"] = verification_error.as_dict()
            else:
                outcome["verified"] = True
        if failure_screenshot is not None:
            outcome["failure_screenshot"] = failure_screenshot.as_dict()
            outcome["screenshot_path"] = (failure_screenshot.data or {}).get("path")
        self._log(state, call, outcome)
        return outcome

    def _log(
        self, state: AgentState, call: ToolCall, outcome: dict[str, Any]
    ) -> None:
        """record one step: what was asked, what it meant, what happened"""
        if self.logger is None:
            return
        error = (outcome.get("error") or {}).get("message")
        target = call.args.get("target") or call.args.get("url") or ""
        self.logger.log(
            StepLog(
                user_request=state.goal,
                observation=f"{state.flow_name or '-'}/{state.flow_step or '-'} at {outcome.get('url') or state.current_url or ''}",
                reasoning_summary=f"{call.name} -> {outcome.get('semantic_action')} "
                f"({(outcome.get('resolution') or {}).get('provenance')})",
                browser_action=f"{call.name} {target}".strip(),
                browser_result={
                    "success": outcome.get("success"),
                    "blocked": outcome.get("blocked"),
                    "url": outcome.get("url"),
                    "error": outcome.get("error"),
                    "authorization": outcome.get("authorization"),
                    # solariclient's bounded post action verifier lives inside toolresult.data; retain
                    # only its compact diagnostics, not the dom snapshot or full provider payload
                    "action_verification": (outcome.get("data") or {}).get("verification"),
                    "failure_screenshot": outcome.get("screenshot_path"),
                    "verified": "verification" in outcome,
                },
                errors=[str(error)] if error else [],
                step_count=state.step_count,
                model_used=None,
            )
        )

    def log_outcome(
        self,
        state: AgentState,
        *,
        final_outcome: str,
        model_used: str | None = None,
        latency_ms: float | None = None,
        stop_condition: str | None = None,
    ) -> None:
        """close out a run with its verdict (and any stop condition)"""
        if self.logger is None:
            return
        self.logger.log(
            StepLog(
                user_request=state.goal,
                final_outcome=final_outcome,
                errors=[stop_condition] if stop_condition else [],
                step_count=state.step_count,
                model_used=model_used,
                latency_ms=latency_ms,
                event="outcome",
            )
        )

    async def _invoke(self, call: ToolCall) -> ToolResult:
        args = call.args
        if call.name == "navigate":
            return await self.client.navigate(str(args.get("url") or ""))
        if call.name == "click":
            return await self.client.click(Target.from_args(args))
        if call.name == "type":
            return await self.client.type_text(Target.from_args(args), str(args.get("text") or ""))
        if call.name == "select":
            return await self.client.select(Target.from_args(args), str(args.get("value") or ""))
        if call.name == "read_page":
            include = args.get("include")
            if include is not None and not isinstance(include, (list, tuple)):
                include = [include]
            if include is not None:
                unknown = set(map(str, include)) - set(READ_PAGE_INCLUDES)
                if unknown:
                    return ToolResult(
                        ok=False,
                        url=state_url(self.client),
                        error=ToolError(
                            BrowserError.UNKNOWN, f"unknown include(s): {sorted(unknown)}"
                        ),
                    )
            return await self.client.read_page(include=include)
        if call.name == "wait":
            until_present = args.get("until_present")
            until_absent = args.get("until_absent")
            if until_present or until_absent:
                return await self.client.wait_for_text(
                    present=until_present, absent=until_absent
                )
            if args.get("settle_postback", True):
                await self.client.settle()
            seconds = args.get("seconds")
            if seconds:
                await self.client.page.wait_for_timeout(float(seconds) * 1000)
            return ToolResult(ok=True, url=state_url(self.client))
        if call.name == "screenshot":
            return await self.client.screenshot(path=args.get("path"))
        return ToolResult(
            ok=False,
            url=state_url(self.client),
            error=ToolError(BrowserError.UNKNOWN, f"unknown tool '{call.name}'"),
        )

    def _sync_position(self, state: AgentState, result: ToolResult) -> None:
        """keep flow position in state urls alone cannot express it"""
        # the url of the page actually being driven
        url = result.url or (result.data or {}).get("url")
        if url:
            state.current_url = str(url)
        flow = (result.data or {}).get("flow") or {}
        if flow.get("flow") and flow.get("step"):
            state.enter_flow(str(flow["flow"]), str(flow["step"]), flow.get("page"))
            return
        position = accela.locate(result.url or "")
        if position is None:
            return
        if _is_regression(state, position):
            # a click inside the scheduling dialog cannot refine the step (every step shares
            # `capdetail.aspx?istoshowinspection=yes`, which only says `select_record`), so a url only
            # position must not overwrite the content derived one
            return
        state.enter_flow(position.flow, position.step, position.page_number)


def _is_regression(state: AgentState, position: accela.FlowPosition) -> bool:
    """true when applying `position` would move an existing flow backwards"""
    flow = accela.FLOWS.get(state.flow_name or "")
    if flow is None or flow.name != position.flow:
        return False
    if state.flow_step not in flow.steps or position.step not in flow.steps:
        return False
    return flow.steps.index(position.step) < flow.steps.index(state.flow_step)


def state_url(client: SolariClient) -> str | None:
    return getattr(client.page, "url", None)
