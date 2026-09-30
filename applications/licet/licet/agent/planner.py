"""the planner loop: goal > model > one classified tool call > observation"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from licet.agent.model import ModelClient, ModelReply
from licet.agent.model import ToolCall as ModelToolCall
from licet.agent.prompts import (
    CONVERGENCE_NUDGE,
    EMPTY_REPLY_NUDGE,
    build_system_prompt,
    observation_payload,
    prune_observations,
    system_report,
)
from licet.agent.state import AgentState
from licet.agent.stop_conditions import (
    StopCondition,
    check_stop_condition,
    describe_stop,
)
from licet.browser import accela
from licet.browser.dispatcher import ToolDispatcher
from licet.browser.errors import BrowserError, ToolError
from licet.browser.tools import TOOL_DEFINITIONS
from licet.config import Config, load_config
from licet.eval.harness import RunRecord
from licet.safety.guard import GuardDecision
from licet.schema.extract import permit_from_page
from licet.schema.permit import Permit

# dead but rendered section wrappers the portal integration lane's root cause finding
# (docs/phase9/portal_read_root_cause.md): the portal renders a section control but never shows it
# ("present but not visible")
_DEAD_WRAPPER_LABEL_VARIANTS: dict[str, tuple[str, ...]] = {
    "read_inspection_history": ("Inspections", "Inspection History"),
}


def _is_dead_wrapper_failure(outcome: Mapping[str, Any]) -> bool:
    """the present but not visible shape only: a decision or a real failure is not"""
    if outcome.get("success") or outcome.get("blocked"):
        return False
    error = outcome.get("error") or {}
    return str(error.get("kind") or "") == "not_actionable" or (
        "present but not visible" in str(error.get("message") or "")
    )


def _recovery_resolves_to(semantic: str, outcome: Mapping[str, Any]) -> bool:
    """a fallback click may open the section only as a read of the same section"""
    if str(outcome.get("semantic_action") or "") != semantic:
        return False
    provenance = str((outcome.get("resolution") or {}).get("provenance") or "")
    return provenance in {"intent", "benign_target"}


# error kinds that mean the run cannot continue until a human intervenes: the session died, or the portal
# is throttling
TERMINAL_ERROR_KINDS = frozenset(
    {
        BrowserError.AUTH_REQUIRED,
        BrowserError.SESSION_TIMEOUT,
        BrowserError.RATE_LIMITED,
        # these are not useful in loop retries: the url is malformed or the portal explicitly gated the page
        BrowserError.PORTAL_ERROR,
        BrowserError.GATED,
        BrowserError.NAVIGATION_FAILED,
    }
)
# a model that twice returns neither a tool call nor an answer has no action to offer; that is
# no_valid_action, not an empty success
MAX_EMPTY_REPLIES = 2
# observations of a record that teach nothing new before licet says "converge"
CONVERGE_AFTER_STALE_FACTS = 4
MAX_CONVERGENCE_NUDGES = 2


@dataclass
class AgentRun:
    """one goal, start to stop and the `runrecord` the scorer consumes"""

    goal: str
    prompt_id: str | None = None
    final_answer: str = ""
    final_answer_source: str = ""
    stop_condition: StopCondition | None = None
    stop_reason: str = ""
    completed: bool = False
    steps: int = 0
    actions: list[dict[str, Any]] = field(default_factory=list)
    state: AgentState | None = None
    permit: Permit | None = None
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: float = 0.0
    used_fallback: bool = False

    @property
    def stop_condition_value(self) -> str | None:
        """the plain string form `str(stop_condition)` on a str enum is not it"""
        return self.stop_condition.value if self.stop_condition else None

    def run_record(self) -> RunRecord:
        """the eval contract (`licet/eval/harness.runrecord`)"""
        return RunRecord(
            prompt_id=self.prompt_id or "adhoc",
            final_answer=self.final_answer,
            actions=self.actions,
            stop_condition=self.stop_condition_value,
            steps=self.steps,
            model=self.model,
        )

    def as_dict(self) -> dict[str, Any]:
        """json serializable run report, for the run log and the cli"""
        return {
            "goal": self.goal,
            "prompt_id": self.prompt_id,
            "final_answer": self.final_answer,
            "final_answer_source": self.final_answer_source,
            "stop_condition": self.stop_condition_value,
            "stop_reason": self.stop_reason,
            "completed": self.completed,
            "steps": self.steps,
            "model": self.model,
            "used_fallback": self.used_fallback,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "latency_ms": round(self.latency_ms, 1),
            "permit": self.permit.model_dump(mode="json") if self.permit else None,
            "state": _state_summary(self.state) if self.state else None,
            "actions": self.actions,
        }


def _state_summary(state: AgentState) -> dict[str, Any]:
    return {
        "current_url": state.current_url,
        "current_page": state.current_page,
        "current_permit": state.current_permit,
        "active_permit": (state.active_permit.model_dump(mode="json") if state.active_permit else None),
        "flow_name": state.flow_name,
        "flow_step": state.flow_step,
        "step_count": state.step_count,
        "stalled_steps": state.stalled_steps,
        "missing_information": list(state.missing_information),
        "no_valid_action_reason": state.no_valid_action_reason,
        "portal_issue": state.portal_issue,
        "ambiguous_candidates": list(state.ambiguous_candidates),
        "pending_approval": (
            {
                "action": state.pending_approval.action,
                "reason": state.pending_approval.reason,
            }
            if state.pending_approval
            else None
        ),
        "failed_actions": [
            {"action": f.action, "error": f.error, "count": f.attempt_count}
            for f in state.failed_actions
        ],
        "completed_steps": list(state.completed_steps),
    }


class Planner:
    """runs one goal to a stop"""

    def __init__(
        self,
        model: ModelClient,
        dispatcher: ToolDispatcher,
        *,
        config: Config | None = None,
        max_steps: int | None = None,
        start_url: str | None = None,
        system_prompt: str | None = None,
        extra_constraints: Sequence[str] = (),
    ) -> None:
        self.config = config or load_config()
        self.model = model
        self.dispatcher = dispatcher
        self.max_steps = max_steps if max_steps is not None else self.config.max_steps
        self.start_url = (
            start_url
            if start_url is not None
            else (self.config.accela_sandbox_url or accela.PORTAL_ROOT)
        )
        self.system_prompt = system_prompt or build_system_prompt(
            self.config, extra_constraints=extra_constraints
        )

    async def run(self, goal: str, *, prompt_id: str | None = None) -> AgentRun:
        """drive one goal to a stop condition and report what happened"""
        run = AgentRun(goal=goal, prompt_id=prompt_id, state=AgentState(goal=goal))
        state = run.state
        started = time.monotonic()
        messages: list[dict[str, Any]] = [{"role": "user", "content": goal}]
        await self._bootstrap(run, messages)

        empty_replies = 0
        nudges = 0
        while True:
            if run.steps >= self.max_steps:
                run.stop_condition = StopCondition.MAX_STEPS_EXCEEDED
                break
            stop = check_stop_condition(state, max_steps=self.max_steps)
            if stop is not None:
                run.stop_condition = stop
                break

            # old page detail is dead weight: a run that reads six pages would otherwise re send all six
            # on every turn (see prompts.py)
            prune_observations(messages)
            if (
                nudges < MAX_CONVERGENCE_NUDGES
                and state.steps_without_new_facts >= CONVERGE_AFTER_STALE_FACTS
            ):
                messages.append(
                    {
                        "role": "user",
                        "content": CONVERGENCE_NUDGE.format(
                            stale=state.steps_without_new_facts,
                            facts="; ".join(_facts_from(run)) or "(no record facts read yet)",
                            remaining=max(0, self.max_steps - run.steps),
                        ),
                    }
                )
                nudges += 1
                state.steps_without_new_facts = 0
            reply = await self.model.reply(
                system=self.system_prompt, messages=messages, tools=TOOL_DEFINITIONS
            )
            run.steps += 1
            self._account(run, reply)

            if not reply.tool_calls:
                if reply.text.strip():
                    run.final_answer = reply.text.strip()
                    run.final_answer_source = "model"
                    run.completed = True
                    run.stop_condition = StopCondition.GOAL_COMPLETED
                    messages.append({"role": "assistant", "content": reply.text.strip()})
                    break
                empty_replies += 1
                if empty_replies >= MAX_EMPTY_REPLIES:
                    state.record_no_valid_action(
                        "the model returned neither a tool call nor a final answer "
                        f"{empty_replies} times in a row"
                    )
                    break
                messages.append({"role": "user", "content": EMPTY_REPLY_NUDGE})
                continue

            empty_replies = 0
            call_ids = [
                call.call_id or f"call_{uuid.uuid4().hex[:12]}"
                for call in reply.tool_calls
            ]
            messages.extend(_assistant_items(reply, call_ids))
            held = await self._perform_calls(run, reply, messages, call_ids)
            if held is not None:
                run.stop_condition = held
                break

        self._finalize(run, started)
        return run


    async def _bootstrap(self, run: AgentRun, messages: list[dict[str, Any]]) -> None:
        """put the run on the configured portal and show the model the page"""
        state = run.state
        current = getattr(self.dispatcher.client.page, "url", None) or ""
        moved = False
        if self.start_url and not _same_host(current, self.start_url):
            await self._perform(
                run, ModelToolCall(name="navigate", args={"url": self.start_url}), "planner"
            )
            run.steps += 1
            moved = True
        entry = await self._perform(run, ModelToolCall(name="read_page", args={}), "planner")
        run.steps += 1
        note = "You are on the starting page." if moved else "You are already on the starting page."
        messages.append(
            {
                "role": "user",
                "content": f"{note} Observation:\n{json.dumps(entry['observation'], ensure_ascii=False)}",
            }
        )
        if state.portal_issue:
            # e.g. the account is not signed in: do not spend model calls on it, the loop's first stop
            # check reports it
            return


    async def _perform_calls(
        self,
        run: AgentRun,
        reply: ModelReply,
        messages: list[dict[str, Any]],
        call_ids: Sequence[str],
    ) -> StopCondition | None:
        """execute the reply's tool calls; return a stop condition when one applies"""
        held: StopCondition | None = None
        for call, call_id in zip(reply.tool_calls, call_ids):
            entry = await self._perform(run, call, "model")
            messages.append(
                {
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": json.dumps(entry["observation"], ensure_ascii=False),
                }
            )
            if entry.get("blocked"):
                decision = (entry.get("authorization") or {}).get("decision")
                if decision == GuardDecision.REQUIRE_APPROVAL.value:
                    # stop and ask
                    held = StopCondition.APPROVAL_REQUIRED
                    break
            if run.state.no_valid_action_reason and held is None:
                held = StopCondition.NO_VALID_ACTION
                break
            if run.state.portal_issue and held is None:
                held = StopCondition.PORTAL_UNAVAILABLE
                break
        return held

    async def _perform(
        self, run: AgentRun, call: ModelToolCall, source: str
    ) -> dict[str, Any]:
        """one call: dispatch it, record it, fold the evidence into state"""
        outcome = await self._dispatch(call, run)
        entry = self._entry(call, outcome, source)
        self._record(run, entry, outcome)
        semantic = str(outcome.get("semantic_action") or "")
        variants = _DEAD_WRAPPER_LABEL_VARIANTS.get(semantic, ())
        if not variants or not _is_dead_wrapper_failure(outcome):
            return entry
        for label in variants:
            fallback = ModelToolCall(
                name="click", args={"target": label, "by": "text", "intent": semantic}
            )
            fb_outcome = await self._dispatch(fallback, run)
            if fb_outcome.get("success") and not _recovery_resolves_to(semantic, fb_outcome):
                # the label resolved outside a benign read of the same section
                fb_outcome = {
                    **fb_outcome,
                    "success": False,
                    "error": ToolError(
                        BrowserError.UNKNOWN,
                        f"fallback label '{label}' resolved outside a benign section read; refused",
                    ).as_dict(),
                }
            if not fb_outcome.get("success"):
                self._record(run, self._entry(fallback, fb_outcome, "recovery"), fb_outcome)
                if _is_dead_wrapper_failure(fb_outcome):
                    continue
                return entry
            self._record(run, self._entry(fallback, fb_outcome, "recovery"), fb_outcome)
            # the label opened the section: read it so the model sees the evidence it asked for
            read = ModelToolCall(name="read_page", args={})
            read_outcome = await self._dispatch(read, run)
            note = (
                f"the original target was a dead section wrapper; the section was "
                f"opened by the visible '{label}' label and re-read"
            )
            recovered = self._entry(read, read_outcome, source, note=note)
            recovered["semantic_action"] = semantic
            recovered["observation"]["semantic_action"] = semantic
            self._record(run, recovered, read_outcome)
            return recovered
        return entry

    async def _dispatch(
        self, call: ModelToolCall, run: AgentRun
    ) -> dict[str, Any]:
        return await self.dispatcher.execute(
            {"name": call.name, "args": call.args}, run.state
        )

    def _entry(
        self,
        call: ModelToolCall,
        outcome: Mapping[str, Any],
        source: str,
        *,
        note: str | None = None,
    ) -> dict[str, Any]:
        observation = observation_payload(call.name, call.args, outcome)
        if note:
            # the model must not mistake a recovered read for its own call having worked
            observation["recovered_via"] = note
        entry: dict[str, Any] = {
            **{key: value for key, value in outcome.items() if key != "data"},
            **call.args,
            "name": call.name,
            "source": source,
            "observation": observation,
        }
        if note:
            entry["recovered_via"] = note
        return entry

    def _record(
        self, run: AgentRun, entry: dict[str, Any], outcome: Mapping[str, Any]
    ) -> None:
        run.actions.append(entry)
        self._absorb(run, entry["observation"], outcome)

    def _absorb(
        self, run: AgentRun, observation: Mapping[str, Any], outcome: Mapping[str, Any]
    ) -> None:
        """fold an observation into `agentstate` so stop conditions can fire"""
        state = run.state
        data = outcome.get("data") or {}
        flow = data.get("flow") or {}
        # the dispatcher has already refreshed the flow position for this result, so fall back to it: a
        # click's own data carries no flow, and without the step the stall detector cannot tell a wizard
        # step apart from a no op
        step = str(flow.get("step") or "") or state.flow_step
        state.observe_page(
            outcome.get("url"), page=step, signature=_fingerprint(observation)
        )

        error = outcome.get("error") or {}
        kind = error.get("kind")
        if kind in {item.value for item in TERMINAL_ERROR_KINDS} and not state.portal_issue:
            state.record_portal_issue(f"{kind}: {error.get('message')}")
        notices = list(data.get("notices") or [])
        if notices and not state.portal_issue:
            # the "please login to continue" dialog changes no url, so this is the only place it can be caught
            state.record_portal_issue("portal notice: " + ", ".join(notices))

        permit = permit_from_page(data)
        if permit is None:
            return
        run.permit = permit
        state.note_facts(_facts_signature(permit))
        if permit.permit_id:
            state.current_permit = permit.permit_id
        state.known_facts["permit"] = permit.model_dump(mode="json")
        if permit.ref is not None:
            state.known_facts.setdefault("records", {})
            state.known_facts["records"][permit.ref.as_key()] = {
                "permit_id": permit.permit_id,
                "status": permit.status,
                "detail_url": permit.ref.detail_url(),
            }


    def _account(self, run: AgentRun, reply: ModelReply) -> None:
        run.model = reply.model or run.model
        run.input_tokens += reply.input_tokens
        run.output_tokens += reply.output_tokens
        run.used_fallback = run.used_fallback or reply.used_fallback

    def _finalize(self, run: AgentRun, started: float) -> None:
        state = run.state
        run.latency_ms = (time.monotonic() - started) * 1000
        if run.stop_condition is None and not run.completed:
            # a break that recorded its reason on the *state* (no_valid_action, a portal issue, a held
            # approval) must not report as "ended for no reason": derive the condition from the state that
            # carries it
            run.stop_condition = check_stop_condition(state, max_steps=self.max_steps)
        if run.stop_condition is not None:
            run.stop_reason = describe_stop(state, run.stop_condition)
        if not run.final_answer:
            run.final_answer = system_report(
                goal=run.goal,
                stop_reason=run.stop_reason or "the run ended",
                steps=run.steps,
                url=state.current_url,
                flow=(
                    f"{state.flow_name}/{state.flow_step}"
                    if state.flow_name and state.flow_step
                    else None
                ),
                facts=_facts_from(run),
                held_action=(
                    state.pending_approval.action if state.pending_approval else None
                ),
            )
            run.final_answer_source = "system"
        if self.dispatcher.logger is not None:
            self.dispatcher.log_outcome(
                state,
                final_outcome=run.final_answer_source or "unknown",
                model_used=run.model or None,
                latency_ms=run.latency_ms,
                stop_condition=run.stop_condition_value,
            )


def _facts_signature(permit: Permit) -> str:
    """fingerprint of the *durable* facts a record page stated"""
    facts = {
        f"id={permit.permit_id}",
        f"status={permit.status}",
        f"type={permit.permit_type}",
    }
    if permit.schedulable_inspection_types:
        facts.add(
            "types="
            + ",".join(sorted(name.strip().lower() for name in permit.schedulable_inspection_types))
        )
    if permit.inspections:
        facts.add("inspections=" + ",".join(sorted(i.type.strip().lower() for i in permit.inspections)))
    facts.update(
        "requirement=" + requirement.value.strip().lower()
        for requirement in permit.outstanding_requirements
    )
    return hashlib.sha1("|".join(sorted(facts)).encode("utf-8")).hexdigest()[:12]


def _fingerprint(observation: Mapping[str, Any]) -> str | None:
    """cheap content signature, so \"nothing changed\" is judged on the page too"""
    text = ((observation.get("page") or {}).get("text")) or ""
    if not text:
        return None
    return hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()[:12]


def _facts_from(run: AgentRun) -> list[str]:
    """up to a few portal facts the report may cite read, never inferred"""
    permit = run.permit
    if permit is None:
        return []
    facts = [
        f"{permit.permit_id}: status {permit.status!r}"
        + (f", type {permit.permit_type!r}" if permit.permit_type else "")
    ]
    if permit.sections:
        facts.append("sections: " + ", ".join(permit.sections))
    if permit.schedulable_inspection_types:
        facts.append(
            f"{len(permit.schedulable_inspection_types)} inspection type(s) offered: "
            + ", ".join(permit.schedulable_inspection_types[:5])
        )
    elif permit.ref is not None:
        facts.append("no inspection records on this page")
    facts.extend(fact.value for fact in permit.outstanding_requirements[:2])
    return facts[:4]


def _assistant_items(reply: ModelReply, call_ids: Sequence[str]) -> list[dict[str, Any]]:
    """the reply, in the shape the responses api expects back in `input`"""
    items: list[dict[str, Any]] = []
    if reply.text:
        items.append({"role": "assistant", "content": reply.text})
    for call, call_id in zip(reply.tool_calls, call_ids):
        items.append(
            {
                "type": "function_call",
                "call_id": call_id,
                "name": call.name,
                "arguments": call.raw_args or json.dumps(call.args),
            }
        )
    return items


def _same_host(current: str, target: str) -> bool:
    """true when the page is already on the portal the run is scoped to"""
    host = target.split("//")[-1].split("/")[0].lower()
    return bool(host) and host in (current or "").lower()
