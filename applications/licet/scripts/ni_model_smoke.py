"""live smoke test of the model boundary (`licet/agent/model.py`)"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from licet.agent.model import (  # noqa: E402
    ModelError,
    OpenAIModel,
    tool_schema,
)
from licet.browser.tools import TOOL_DEFINITIONS  # noqa: E402
from licet.config import load_config  # noqa: E402
from licet.safety.risk_levels import KNOWN_ACTIONS  # noqa: E402

LOG_ROOT = Path("logs/ni_model_smoke")

SYSTEM = (
    "You are Licet, a browser agent that completes permitting tasks on Accela "
    "Citizen Access portals. Call tools to act; never describe an action in prose "
    "instead of taking it. Call exactly one tool per turn."
)

TOOL_NAMES = {definition.name for definition in TOOL_DEFINITIONS}
TOOL_SCHEMAS = [tool_schema(definition) for definition in TOOL_DEFINITIONS]

RESULTS: list[dict[str, Any]] = []
NOTES: list[str] = []
USAGE: dict[str, int] = {"input_tokens": 0, "output_tokens": 0, "calls": 0}


def check(name: str, ok: bool, note: str = "", hard: bool = True) -> None:
    RESULTS.append({"name": name, "ok": bool(ok), "note": note, "hard": hard})
    label = "PASS" if ok else ("FAIL" if hard else "WARN")
    print(f"[{label}] {name}" + (f"  {note}" if note else ""), flush=True)


def note(text: str) -> None:
    NOTES.append(text)
    print(f"[note] {text}", flush=True)


def account(reply: Any, elapsed: float) -> str:
    USAGE["input_tokens"] += reply.input_tokens
    USAGE["output_tokens"] += reply.output_tokens
    USAGE["calls"] += 1
    served = f" via fallback from {reply.fallback_from}" if reply.used_fallback else ""
    return (
        f"model={reply.model or '?'}{served} {elapsed:.1f}s "
        f"in={reply.input_tokens} out={reply.output_tokens}"
    )


def case_key_present() -> tuple[bool, str]:
    config = load_config()
    key = config.openai_api_key
    if not key:
        return False, "OPENAI_API_KEY is not set in .env"
    detail = f"len={len(key)}"
    if key != key.strip() or any(character.isspace() for character in key):
        detail += " — CONTAINS WHITESPACE (a quoted or line-wrapped paste)"
    return True, detail


async def case_text_reply(client: OpenAIModel) -> bool:
    started = time.monotonic()
    reply = await client.reply(
        system="You reply with a single word and nothing else.",
        messages=[{"role": "user", "content": "Reply with the single word: ready"}],
    )
    elapsed = time.monotonic() - started
    ok = bool(reply.text) and reply.wants_tools is False and reply.input_tokens > 0
    check(
        "plain text reply",
        ok,
        f"text={reply.text[:40]!r} {account(reply, elapsed)}",
    )
    if not reply.model.startswith("gpt-5"):
        note(f"model id reported by the API was {reply.model!r} — check the slug in .env")
    return ok


async def case_tool_call(client: OpenAIModel) -> bool:
    started = time.monotonic()
    reply = await client.reply(
        system=SYSTEM,
        messages=[
            {
                "role": "user",
                "content": (
                    "Find the permit for 801 Windward Way by address. "
                    "Call exactly one tool to begin."
                ),
            }
        ],
        tools=TOOL_DEFINITIONS,
    )
    elapsed = time.monotonic() - started
    detail = account(reply, elapsed)

    if len(reply.tool_calls) != 1:
        check("tool call round-trip", False, f"{len(reply.tool_calls)} calls; {detail}")
        return False

    call = reply.tool_calls[0]
    check("tool name is in the catalogue", call.name in TOOL_NAMES, f"name={call.name!r}")
    check(
        "arguments decode to a dict",
        isinstance(call.args, dict) and bool(call.args),
        f"args={json.dumps(call.args)[:120]}",
    )
    check(
        "raw argument blob is kept for the log",
        call.raw_args.startswith("{") and json.loads(call.raw_args) == call.args,
        f"raw={call.raw_args[:60]!r}",
    )
    check("call id present", bool(call.call_id), f"call_id={call.call_id!r}")
    check("tool call round-trip", True, detail)

    schema_properties = next(
        (schema["parameters"] for schema in TOOL_SCHEMAS if schema["name"] == call.name),
        {},
    )
    unknown = set(call.args) - set(schema_properties.get("properties", {}))
    check(
        "no invented arguments outside the schema",
        not unknown,
        f"unknown={sorted(unknown)}" if unknown else f"keys={sorted(call.args)}",
    )
    return True


async def case_intent_enum(client: OpenAIModel) -> bool:
    """a record link is blocked unless the call carries a classified `intent`"""
    started = time.monotonic()
    reply = await client.reply(
        system=SYSTEM + " The search result links are grid anchors, so name the record.",
        messages=[
            {
                "role": "user",
                "content": (
                    "The search results are on screen. The row for record BLD26-00472 "
                    "is visible and you must open that record. Call exactly one tool."
                ),
            }
        ],
        tools=TOOL_DEFINITIONS,
    )
    elapsed = time.monotonic() - started

    if len(reply.tool_calls) != 1:
        check("intent enum on a record click", False, f"{len(reply.tool_calls)} calls")
        return False

    call = reply.tool_calls[0]
    intent = call.args.get("intent")
    valid = intent in KNOWN_ACTIONS
    ok = valid and intent == "open_record"
    check(
        "intent enum on a record click",
        ok,
        f"tool={call.name!r} intent={intent!r} {account(reply, elapsed)}",
        hard=False,
    )
    if not valid:
        note(
            "the model omitted an invalid/absent `intent`, so the fail-closed resolver "
            "would BLOCK this click — the planner prompt has to require intent="
            "'open_record' for grid links (see docs/accela_ui_map.md)."
        )
    return ok


async def case_fallback(config: Any) -> bool:
    """a bogus primary slug must fall through to the configured fallback"""
    client = OpenAIModel(
        "gpt-5.6-sol-nonexistent-model",
        api_key=config.openai_api_key,
        fallback_model=config.fallback_model,
        timeout_seconds=60.0,
        max_retries=0,
    )
    started = time.monotonic()
    try:
        reply = await client.reply(
            system="You reply with a single word and nothing else.",
            messages=[{"role": "user", "content": "Reply with the single word: fallback"}],
        )
    except ModelError as exc:
        check("fallback serves a dead primary", False, str(exc)[:160])
        return False
    elapsed = time.monotonic() - started

    ok = reply.used_fallback and reply.fallback_from == "gpt-5.6-sol-nonexistent-model"
    check(
        "fallback serves a dead primary",
        ok,
        f"fallback_from={reply.fallback_from!r} {account(reply, elapsed)}",
    )
    return ok


async def case_bad_key() -> bool:
    """authentication failure must surface as a modelerror, never an empty plan"""
    client = OpenAIModel("gpt-5.4-mini", api_key="sk-invalid-0000000000000000", max_retries=0)
    try:
        reply = await client.reply(system="s", messages=[{"role": "user", "content": "hi"}])
    except ModelError as exc:
        check("bad key raises ModelError", True, f"{type(exc).__name__}: {str(exc)[:90]}")
        return True
    check(
        "bad key raises ModelError",
        False,
        f"no error raised; text={reply.text[:40]!r} tool_calls={len(reply.tool_calls)}",
    )
    return False


async def main() -> int:
    config = load_config()
    print("=== Licet model boundary — live smoke test ===")
    print(f"primary : {config.agent_model}")
    print(f"fallback: {config.fallback_model}")
    print(f"tools   : {sorted(TOOL_NAMES)}")
    print()

    ok, detail = case_key_present()
    check("OPENAI_API_KEY resolves", ok, detail)
    if not ok:
        print("\ncannot continue without a key — add OPENAI_API_KEY to .env")
        return 1

    primary = OpenAIModel(
        config.agent_model,
        api_key=config.openai_api_key,
        fallback_model=config.fallback_model,
        timeout_seconds=config.model_timeout_seconds,
        max_retries=config.model_max_retries,
    )

    for name, coroutine in (
        ("text reply", case_text_reply(primary)),
        ("tool call", case_tool_call(primary)),
        ("intent enum", case_intent_enum(primary)),
        ("fallback", case_fallback(config)),
        ("bad key", case_bad_key()),
    ):
        try:
            await coroutine
        except Exception as exc:  # noqa: BLE001 - a case crashing is a result, not a traceback
            check(name, False, f"raised {type(exc).__name__}: {str(exc)[:200]}")

    hard_failures = [result for result in RESULTS if not result["ok"] and result["hard"]]
    warnings = [result for result in RESULTS if not result["ok"] and not result["hard"]]

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    LOG_ROOT.mkdir(parents=True, exist_ok=True)
    path = LOG_ROOT / f"{stamp}_model_smoke.json"
    path.write_text(
        json.dumps(
            {
                "timestamp": stamp,
                "primary_model": config.agent_model,
                "fallback_model": config.fallback_model,
                "checks": RESULTS,
                "notes": NOTES,
                "usage": USAGE,
                "hard_failures": len(hard_failures),
                "warnings": len(warnings),
            },
            indent=2,
        )
    )

    print()
    print(
        f"{len(RESULTS) - len(hard_failures) - len(warnings)}/{len(RESULTS)} checks passed"
        f" (hard failures={len(hard_failures)}, warnings={len(warnings)})"
    )
    print(
        f"tokens: in={USAGE['input_tokens']} out={USAGE['output_tokens']} "
        f"over {USAGE['calls']} calls"
    )
    print(f"log   : {path}")
    return 1 if hard_failures else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
