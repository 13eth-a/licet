"""the model boundary: tool-call plumbing, cost reporting, and failure modes"""

from __future__ import annotations

import asyncio
import json

import pytest

from licet.agent.model import (
    ModelError,
    ModelReply,
    OpenAIModel,
    ScriptedModel,
    ToolCall,
    build_model,
    tool_schema,
)
from licet.config import load_config


class _Item:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class _Response:
    def __init__(self, output, model="gpt-5.6-sol", usage=None, status="completed"):
        self.output = output
        self.model = model
        self.usage = usage
        self.status = status


class _FakeResponses:
    def __init__(self, responses):
        self._responses = list(responses)
        self.requests: list[dict] = []

    async def create(self, **kwargs):
        self.requests.append(kwargs)
        result = self._responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class _FakeOpenAI:
    def __init__(self, responses):
        self.responses = _FakeResponses(responses)


def _client(responses, **kwargs) -> tuple[OpenAIModel, _FakeOpenAI]:
    model = OpenAIModel("gpt-5.6-sol", api_key="sk-test", **kwargs)
    fake = _FakeOpenAI(responses)
    model._client = fake
    return model, fake


def test_function_call_becomes_a_decoded_tool_call():
    model, _ = _client(
        [
            _Response(
                [
                    _Item(
                        type="function_call",
                        name="read_page",
                        arguments=json.dumps({"include": ["text"]}),
                        call_id="call_1",
                    )
                ],
                usage=_Item(input_tokens=1200, output_tokens=80),
            )
        ]
    )

    reply = asyncio.run(model.reply(system="s", messages=[{"role": "user", "content": "hi"}]))

    assert reply.wants_tools is True
    assert reply.tool_calls[0].name == "read_page"
    assert reply.tool_calls[0].args == {"include": ["text"]}
    assert reply.tool_calls[0].call_id == "call_1"
    assert reply.tool_calls[0].raw_args.startswith("{")
    assert (reply.input_tokens, reply.output_tokens) == (1200, 80)
    assert reply.model == "gpt-5.6-sol"


def test_text_only_reply_is_reported_as_text():
    model, _ = _client(
        [_Response([_Item(type="message", content=[_Item(type="output_text", text="No bookable dates.")])])]
    )

    reply = asyncio.run(model.reply(system="s", messages=[]))

    assert reply.text == "No bookable dates."
    assert reply.wants_tools is False


def test_tools_and_reasoning_effort_are_sent_when_configured():
    model, fake = _client([_Response([])], reasoning_effort="high")
    tools = [{"name": "click", "description": "click something", "parameters": {"type": "object"}}]

    asyncio.run(model.reply(system="s", messages=[], tools=tools))

    request = fake.responses.requests[0]
    assert request["tools"][0]["type"] == "function"
    assert request["tools"][0]["name"] == "click"
    assert request["reasoning"] == {"effort": "high"}
    assert request["instructions"] == "s"


def test_tool_schema_accepts_objects_and_passthrough_dicts():
    class Definition:
        name = "click"
        description = "click"
        parameters = {"type": "object", "properties": {"target": {"type": "string"}}}

    assert tool_schema(Definition())["name"] == "click"
    already = {"type": "function", "name": "wait", "parameters": {}}
    assert tool_schema(already) is already


def test_invalid_json_arguments_raise_a_recoverable_model_error():
    model, _ = _client([_Response([_Item(type="function_call", name="click", arguments="{not json")])])

    with pytest.raises(ModelError, match="not valid JSON"):
        asyncio.run(model.reply(system="s", messages=[]))


def test_non_object_arguments_are_rejected():
    model, _ = _client([_Response([_Item(type="function_call", name="click", arguments="[1,2]")])])

    with pytest.raises(ModelError, match="must be a JSON object"):
        asyncio.run(model.reply(system="s", messages=[]))


def test_a_failing_call_raises_instead_of_returning_an_empty_plan():
    """an empty plan reads as 'the agent chose to do nothing' — a silent wrong answer"""
    model, fake = _client([RuntimeError("boom"), RuntimeError("boom"), RuntimeError("boom")], max_retries=2)

    with pytest.raises(ModelError, match="failed after 3 attempt"):
        asyncio.run(model.reply(system="s", messages=[]))
    assert len(fake.responses.requests) == 3


def test_a_retry_can_succeed():
    model, fake = _client(
        [RuntimeError("transient"), _Response([_Item(type="message", content=[_Item(type="output_text", text="ok")])])],
        max_retries=1,
    )

    reply = asyncio.run(model.reply(system="s", messages=[]))

    assert reply.text == "ok"
    assert len(fake.responses.requests) == 2


def test_missing_key_is_reported_before_the_run_starts():
    with pytest.raises(ModelError, match="OPENAI_API_KEY is not set"):
        build_model(load_config({}))


def test_build_model_uses_the_configured_slugs():
    client = build_model(load_config({"OPENAI_API_KEY": "sk-test"}))

    assert isinstance(client, OpenAIModel)
    assert client.model == "gpt-5.6-sol"
    assert client.fallback_model == "gpt-5.4-mini"


def test_scripted_model_replays_replies_and_records_what_it_was_asked():
    model = ScriptedModel(
        [
            {"tool_calls": [{"name": "read_page", "args": {"include": ["text"]}}]},
            {"text": "Nothing bookable."},
        ]
    )

    first = asyncio.run(model.reply(system="s", messages=[{"role": "user", "content": "go"}]))
    second = asyncio.run(model.reply(system="s", messages=[{"role": "user", "content": "go"}]))

    assert first.tool_calls[0].name == "read_page"
    assert second.text == "Nothing bookable."
    assert len(model.calls) == 2
    assert model.calls[0]["system"] == "s"


def test_scripted_model_raises_when_the_loop_overruns_its_script():
    """a looping planner must fail a test rather than silently pass it"""
    model = ScriptedModel([ModelReply(text="done")])
    asyncio.run(model.reply(system="s", messages=[]))

    with pytest.raises(ModelError, match="ran out of replies"):
        asyncio.run(model.reply(system="s", messages=[]))


def test_the_fallback_serves_the_reply_when_the_primary_is_down():
    model, fake = _client(
        [
            RuntimeError("503 upstream"),
            RuntimeError("503 upstream"),
            RuntimeError("503 upstream"),
            _Response(
                [_Item(type="message", content=[_Item(type="output_text", text="ok")])],
                model="gpt-5.4-mini",
            ),
        ],
        fallback_model="gpt-5.4-mini",
        max_retries=2,
    )

    reply = asyncio.run(model.reply(system="s", messages=[]))

    assert reply.text == "ok"
    assert reply.used_fallback is True
    assert reply.fallback_from == "gpt-5.6-sol"
    assert reply.model == "gpt-5.4-mini"
    assert [request["model"] for request in fake.responses.requests] == [
        "gpt-5.6-sol",
        "gpt-5.6-sol",
        "gpt-5.6-sol",
        "gpt-5.4-mini",
    ]


def test_the_fallback_gets_its_own_retry_budget():
    model, fake = _client(
        [RuntimeError("down")] * 3 + [RuntimeError("down"), _Response([], model="gpt-5.4-mini")],
        fallback_model="gpt-5.4-mini",
        max_retries=2,
    )

    reply = asyncio.run(model.reply(system="s", messages=[]))

    assert reply.used_fallback is True
    assert len(fake.responses.requests) == 5


def test_a_primary_success_is_not_reported_as_a_fallback():
    model, _ = _client([_Response([])], fallback_model="gpt-5.4-mini")

    reply = asyncio.run(model.reply(system="s", messages=[]))

    assert reply.used_fallback is False
    assert reply.fallback_from == ""


def test_both_models_failing_names_both_in_the_error():
    model, fake = _client(
        [RuntimeError("primary down")] * 3 + [RuntimeError("fallback down")] * 3,
        fallback_model="gpt-5.4-mini",
        max_retries=2,
    )

    with pytest.raises(ModelError) as excinfo:
        asyncio.run(model.reply(system="s", messages=[]))

    message = str(excinfo.value)
    assert "gpt-5.6-sol" in message and "gpt-5.4-mini" in message
    assert "primary down" in message and "fallback down" in message
    assert len(fake.responses.requests) == 6


def test_a_fallback_identical_to_the_primary_is_not_retried_twice():
    model, fake = _client(
        [RuntimeError("down")] * 3, fallback_model="gpt-5.6-sol", max_retries=2
    )

    with pytest.raises(ModelError):
        asyncio.run(model.reply(system="s", messages=[]))

    assert model.attempt_order == ["gpt-5.6-sol"]
    assert len(fake.responses.requests) == 3
