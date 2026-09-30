"""environment-driven configuration for licet"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping

from dotenv import load_dotenv

load_dotenv()


# openai model ids, verified against developers.openai.com/api/docs/models (2026-09-20)
DEFAULT_AGENT_MODEL = "gpt-5.6-sol"
DEFAULT_FALLBACK_MODEL = "gpt-5.4-mini"


@dataclass(frozen=True)
class Config:
    agent_model: str = DEFAULT_AGENT_MODEL
    fallback_model: str = DEFAULT_FALLBACK_MODEL
    openai_api_key: str | None = None
    solari_api_key: str | None = None
    solari_base_url: str | None = None
    accela_sandbox_url: str | None = None
    accela_test_username: str | None = None
    accela_test_password: str | None = None
    max_steps: int = 25
    reasoning_effort: str | None = None
    # seconds before a model call is abandoned, and how many times to retry
    model_timeout_seconds: float = 120.0
    model_max_retries: int = 2


def _positive_int(value: str | None, default: int) -> int:
    try:
        parsed = int(value) if value is not None else default
    except ValueError as exc:
        raise ValueError("LICET_MAX_STEPS must be an integer") from exc
    if parsed < 1:
        raise ValueError("LICET_MAX_STEPS must be at least 1")
    return parsed


def _positive_float(value: str | None, default: float) -> float:
    try:
        parsed = float(value) if value is not None else default
    except ValueError as exc:
        raise ValueError("LICET_MODEL_TIMEOUT must be a number") from exc
    if parsed <= 0:
        raise ValueError("LICET_MODEL_TIMEOUT must be greater than 0")
    return parsed


def _non_negative_int(value: str | None, default: int) -> int:
    try:
        parsed = int(value) if value is not None else default
    except ValueError as exc:
        raise ValueError("LICET_MODEL_RETRIES must be an integer") from exc
    if parsed < 0:
        raise ValueError("LICET_MODEL_RETRIES cannot be negative")
    return parsed


def load_config(env: Mapping[str, str] | None = None) -> Config:
    """load settings from ``env`` or the process environment"""
    values = env if env is not None else os.environ
    return Config(
        agent_model=values.get("LICET_AGENT_MODEL", DEFAULT_AGENT_MODEL),
        fallback_model=values.get("LICET_FALLBACK_MODEL", DEFAULT_FALLBACK_MODEL),
        openai_api_key=values.get("OPENAI_API_KEY"),
        reasoning_effort=values.get("LICET_REASONING_EFFORT") or None,
        model_timeout_seconds=_positive_float(values.get("LICET_MODEL_TIMEOUT"), 120.0),
        model_max_retries=_non_negative_int(values.get("LICET_MODEL_RETRIES"), 2),
        solari_api_key=values.get("SOLARI_API_KEY"),
        solari_base_url=values.get("SOLARI_BASE_URL"),
        accela_sandbox_url=values.get("ACCELA_SANDBOX_URL"),
        accela_test_username=values.get("ACCELA_TEST_USERNAME"),
        accela_test_password=values.get("ACCELA_TEST_PASSWORD"),
        max_steps=_positive_int(values.get("LICET_MAX_STEPS"), 25),
    )


CONFIG = load_config()
AGENT_MODEL = CONFIG.agent_model
FALLBACK_MODEL = CONFIG.fallback_model
OPENAI_API_KEY = CONFIG.openai_api_key
REASONING_EFFORT = CONFIG.reasoning_effort
SOLARI_API_KEY = CONFIG.solari_api_key
SOLARI_BASE_URL = CONFIG.solari_base_url
ACCELA_SANDBOX_URL = CONFIG.accela_sandbox_url
ACCELA_TEST_USERNAME = CONFIG.accela_test_username
ACCELA_TEST_PASSWORD = CONFIG.accela_test_password
MAX_STEPS = CONFIG.max_steps
