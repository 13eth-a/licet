"""structured, append only event logging for every agent step"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

LOG_DIR = Path("logs")
_SAFE_ID = re.compile(r"[^A-Za-z0-9_.-]+")


def new_run_id(prefix: str = "run") -> str:
    """return a unique, filename safe run id for traces and screenshots"""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    safe_prefix = _SAFE_ID.sub("-", prefix).strip("-") or "run"
    return f"{safe_prefix}-{stamp}-{uuid.uuid4().hex[:8]}"


@dataclass
class StepLog:
    user_request: str
    observation: str | None = None
    reasoning_summary: str | None = None
    browser_action: str | None = None
    browser_result: dict[str, Any] | None = None
    errors: list[str] = field(default_factory=list)
    step_count: int = 0
    final_outcome: str | None = None
    model_used: str | None = None
    latency_ms: float | None = None
    cost_usd: float | None = None
    event: str = "step"
    timestamp: str | None = None


class RunLogger:
    """append steplog entries for one run to a utf 8 jsonl file"""

    def __init__(self, run_id: str, log_dir: Path = LOG_DIR) -> None:
        if not run_id or "/" in run_id or "\\" in run_id:
            raise ValueError("run_id must be a non-empty filename-safe value")
        self.run_id = run_id
        log_dir.mkdir(parents=True, exist_ok=True)
        self.path = log_dir / f"{run_id}.jsonl"
        self.started_at = datetime.now(timezone.utc).isoformat()
        self.metrics: dict[str, dict[str, int]] = {}
        # named aggregates attached by callers (e.g. the lookup layer's kpis)
        self.aggregates: dict[str, dict[str, Any]] = {}

    def log(self, step: StepLog) -> None:
        record = asdict(step)
        record["run_id"] = self.run_id
        record["timestamp"] = step.timestamp or datetime.now(timezone.utc).isoformat()
        result = step.browser_result or {}
        action = (step.browser_action or "").split(" ", 1)[0]
        if action and result:
            bucket = self.metrics.setdefault(action, {"attempts": 0, "success": 0, "failure": 0})
            bucket["attempts"] += 1
            bucket["success" if result.get("success") else "failure"] += 1
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")

    def log_metrics(self, name: str, values: dict[str, Any]) -> None:
        """attach a named aggregate (e.g. lookup kpis) to the run"""
        snapshot = dict(values)
        self.aggregates[name] = snapshot
        record = {
            "event": "metrics",
            "run_id": self.run_id,
            "name": name,
            "values": snapshot,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")

    def log_event(self, event: str, **fields: Any) -> None:
        """append one named, domain agnostic event record"""
        record = {
            "event": event,
            "run_id": self.run_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **fields,
        }
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")

    def summary(self) -> dict[str, Any]:
        """return aggregate primitive action metrics for this run"""
        return {action: dict(values) for action, values in self.metrics.items()}

    def read_all(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        with self.path.open(encoding="utf-8") as stream:
            return [json.loads(line) for line in stream if line.strip()]
