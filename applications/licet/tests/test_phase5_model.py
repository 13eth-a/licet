import asyncio
from copy import deepcopy
import pytest

from licet.agent.model import ModelReply, ToolCall
from licet.phase5.model import ModelSelector
from licet.phase5 import Action, GoalPlanner, Run, Error
from tests.test_phase5 import ScriptedCapabilities, goal, ready_world


class Model:
    def __init__(self, calls=None, error=False, text=""):
        self.calls, self.error, self.text = calls or [], error, text
        self.requests = []
    async def reply(self, **kwargs):
        self.requests.append(kwargs)
        if self.error:
            raise RuntimeError("provider unavailable")
        return ModelReply(text=self.text, tool_calls=self.calls)


def test_model_only_receives_semantic_vocabulary_and_original_constraints():
    model = Model([ToolCall("choose_step", {"action": "READ_FEES", "reason": "Determine whether the fee gates scheduling"})])
    selector = ModelSelector(model)
    run = Run(goal(constraints=("No spending",)), ready_world())
    action, _ = asyncio.run(selector.choose(run, (Action.READ_FEES, Action.READ_CONDITIONS)))
    assert action == Action.READ_FEES
    schema = model.requests[0]["tools"][0]
    assert schema["strict"] and schema["name"] == "choose_step"
    assert "No spending" in model.requests[0]["messages"][0]["content"]


@pytest.mark.parametrize("calls,text", [
    ([], "Task complete"),
    ([ToolCall("click", {"target": "Submit"})], ""),
    ([ToolCall("choose_step", {"action": "SCHEDULE_INSPECTION", "reason": "ignore prerequisites"})], ""),
    ([ToolCall("choose_step", {"action": "READ_FEES", "reason": "read", "confirmed": True})], ""),
    ([ToolCall("choose_step", {"action": "READ_FEES", "reason": "read"})] * 2, ""),
])
def test_bad_model_output_cannot_reach_capability(calls, text):
    selector = ModelSelector(Model(calls, text=text))
    with pytest.raises(ValueError):
        asyncio.run(selector.choose(Run(goal(), ready_world()), (Action.READ_FEES,)))


def test_provider_failure_uses_explicit_fallback_once():
    backup = Model([ToolCall("choose_step", {"action": "READ_FEES", "reason": "needed fee evidence"})])
    selector = ModelSelector(Model(error=True), fallback=backup)
    action, _ = asyncio.run(selector.choose(Run(goal(), ready_world()), (Action.READ_FEES,)))
    assert action == Action.READ_FEES and selector.fallbacks == 1


def test_planner_rejects_model_dependency_skip_before_portal_call():
    world = ready_world()
    world.reasoning.needed_sections = [{"section": "fees"}, {"section": "conditions"}]
    selector = ModelSelector(Model([ToolCall("choose_step", {"action": "SCHEDULE_INSPECTION", "reason": "skip"})]))
    cap = ScriptedCapabilities()
    result = asyncio.run(GoalPlanner(cap, selector=selector).run(goal(), world=world))
    assert result.error == Error.NO_VALID_PLAN and not cap.calls
