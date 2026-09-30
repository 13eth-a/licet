"""the model boundary: one place that talks to an llm"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any, Protocol, Sequence

from licet.config import Config, load_config


class ModelError(RuntimeError):
    """the model call failed in a way the caller must handle"""


@dataclass(frozen=True)
class ToolCall:
    """one tool the model asked for"""

    name: str
    args: dict[str, Any] = field(default_factory=dict)
    call_id: str = ""
    raw_args: str = ""

    @classmethod
    def from_raw(cls, name: str, raw: str, call_id: str = "") -> "ToolCall":
        try:
            args = json.loads(raw) if raw else {}
        except json.JSONDecodeError as exc:
            raise ModelError(f"tool arguments for '{name}' were not valid JSON: {exc}") from exc
        if not isinstance(args, dict):
            raise ModelError(f"tool arguments for '{name}' must be a JSON object")
        return cls(name=name, args=args, call_id=call_id, raw_args=raw)


@dataclass
class ModelReply:
    """what the model said: text and/or tool calls, plus what it cost"""

    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    stop_reason: str = ""
    # set when the primary model failed and the fallback served the reply
    used_fallback: bool = False
    fallback_from: str = ""

    @property
    def wants_tools(self) -> bool:
        return bool(self.tool_calls)


class ModelClient(Protocol):
    """the only surface the planner sees"""

    async def reply(
        self,
        *,
        system: str,
        messages: Sequence[dict[str, Any]],
        tools: Sequence[dict[str, Any]] | None = None,
    ) -> ModelReply: ...


def tool_schema(definition: Any) -> dict[str, Any]:
    """`licet/browser/tools.py` definitions > openai function tool schema"""
    if isinstance(definition, dict) and definition.get("type") == "function":
        return definition
    if isinstance(definition, dict):
        name = definition.get("name", "")
        description = definition.get("description", "")
        parameters = definition.get("parameters", {})
    else:
        name = getattr(definition, "name", "")
        description = getattr(definition, "description", "")
        parameters = getattr(definition, "parameters", {})
    return {
        "type": "function",
        "name": name,
        "description": description,
        "parameters": parameters,
    }


class OpenAIModel:
    """openai backed client"""

    def __init__(
        self,
        model: str,
        *,
        api_key: str | None = None,
        fallback_model: str | None = None,
        reasoning_effort: str | None = None,
        timeout_seconds: float = 120.0,
        max_retries: int = 2,
        max_output_tokens: int = 4096,
    ) -> None:
        self.model = model
        self.fallback_model = fallback_model
        self.reasoning_effort = reasoning_effort
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.max_output_tokens = max_output_tokens
        self._api_key = api_key
        self._client: Any = None

    def _ensure_client(self) -> Any:
        if self._client is None:
            try:
                from openai import AsyncOpenAI
            except ImportError as exc:  # pragma: no cover exercised only without the dep
                raise ModelError(
                    "the openai package is required for the live model: "
                    'pip install -e ".[dev]"'
                ) from exc
            self._api_key = self._api_key or load_config().openai_api_key
            if not self._api_key:
                raise ModelError("OPENAI_API_KEY is not set (see .env.example)")
            self._client = AsyncOpenAI(api_key=self._api_key, timeout=self.timeout_seconds)
        return self._client

    async def reply(
        self,
        *,
        system: str,
        messages: Sequence[dict[str, Any]],
        tools: Sequence[dict[str, Any]] | None = None,
    ) -> ModelReply:
        client = self._ensure_client()
        request: dict[str, Any] = {
            "model": self.model,
            "instructions": system,
            "input": list(messages),
            "max_output_tokens": self.max_output_tokens,
        }
        if tools:
            request["tools"] = [tool_schema(tool) for tool in tools]
        if self.reasoning_effort:
            request["reasoning"] = {"effort": self.reasoning_effort}

        failures: list[str] = []
        order = self.attempt_order
        for index, model in enumerate(order):
            request["model"] = model
            for attempt in range(self.max_retries + 1):
                try:
                    response = await client.responses.create(**request)
                except Exception as exc:  # noqa: BLE001 classified below
                    if attempt < self.max_retries:
                        await asyncio.sleep(1.0 * (attempt + 1))
                        continue
                    failures.append(
                        f"model call failed after {attempt + 1} attempt(s) on "
                        f"'{model}': {exc}"
                    )
                    break
                # deliberately outside the try: a malformed argument blob is a recoverable model error the
                # caller must see as such, not something to retry into a generic transport failure
                reply = self._parse(response)
                reply.used_fallback = index > 0
                if index > 0:
                    reply.fallback_from = order[0]
                return reply
        raise ModelError("; ".join(failures) if failures else "model call failed")

    @property
    def attempt_order(self) -> list[str]:
        """primary first, then the fallback when it is set and different"""
        order = [self.model]
        if self.fallback_model and self.fallback_model != self.model:
            order.append(self.fallback_model)
        return order

    @staticmethod
    def _parse(response: Any) -> ModelReply:
        text_parts: list[str] = []
        calls: list[ToolCall] = []
        for item in getattr(response, "output", None) or []:
            item_type = getattr(item, "type", "")
            if item_type == "function_call":
                calls.append(
                    ToolCall.from_raw(
                        getattr(item, "name", ""),
                        getattr(item, "arguments", "") or "",
                        call_id=getattr(item, "call_id", "") or getattr(item, "id", ""),
                    )
                )
            elif item_type == "message":
                for part in getattr(item, "content", None) or []:
                    if getattr(part, "type", "") in ("output_text", "text"):
                        text_parts.append(getattr(part, "text", "") or "")
        usage = getattr(response, "usage", None)
        return ModelReply(
            text="\n".join(part for part in text_parts if part).strip(),
            tool_calls=calls,
            model=getattr(response, "model", "") or "",
            input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
            output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
            stop_reason=str(getattr(response, "status", "") or ""),
        )


class ScriptedModel:
    """replays canned replies how the planner loop is tested without a key"""

    def __init__(self, replies: Sequence[ModelReply | dict[str, Any]]) -> None:
        self._replies = [
            reply if isinstance(reply, ModelReply) else _reply_from_dict(reply)
            for reply in replies
        ]
        self.calls: list[dict[str, Any]] = []

    async def reply(
        self,
        *,
        system: str,
        messages: Sequence[dict[str, Any]],
        tools: Sequence[dict[str, Any]] | None = None,
    ) -> ModelReply:
        self.calls.append({"system": system, "messages": list(messages), "tools": list(tools or [])})
        if not self._replies:
            raise ModelError("ScriptedModel ran out of replies")
        return self._replies.pop(0)


def _reply_from_dict(payload: dict[str, Any]) -> ModelReply:
    calls = [
        ToolCall(name=call["name"], args=dict(call.get("args") or {}))
        for call in payload.get("tool_calls", [])
    ]
    return ModelReply(
        text=payload.get("text", ""),
        tool_calls=calls,
        model=payload.get("model", "scripted"),
        input_tokens=int(payload.get("input_tokens", 0)),
        output_tokens=int(payload.get("output_tokens", 0)),
        stop_reason=payload.get("stop_reason", ""),
    )


def build_model(config: Config | None = None) -> ModelClient:
    """the configured client"""
    config = config or load_config()
    if not config.openai_api_key:
        raise ModelError(
            "OPENAI_API_KEY is not set — add it to .env (see .env.example) before "
            "running a live agent."
        )
    return OpenAIModel(
        config.agent_model,
        api_key=config.openai_api_key,
        fallback_model=config.fallback_model,
        reasoning_effort=config.reasoning_effort,
        timeout_seconds=config.model_timeout_seconds,
        max_retries=config.model_max_retries,
    )
