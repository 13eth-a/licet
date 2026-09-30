"""Offline tests for the read-only calendar horizon survey's durable progress log."""
from __future__ import annotations

import asyncio
import json

import pytest

from scripts import ni_calendar_horizon as survey


class FakePage:
    def __init__(self):
        self.closed = False

    async def close(self):
        self.closed = True


class FakeClient:
    def __init__(self):
        self.page = FakePage()

    async def login(self, username, password):
        return type("Login", (), {"ok": True, "data": {"authenticated": True}})()


class FakeSession:
    def __init__(self):
        self.fake_client = FakeClient()
        self.closed = False

    async def client(self):
        return self.fake_client

    async def close(self):
        self.closed = True


class FakeDispatcher:
    def __init__(self, client):
        self.client = client


class InterruptedSurvey(BaseException):
    pass


def events(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_checkpoint_appends_fsynced_jsonl_events(tmp_path, monkeypatch):
    path = tmp_path / "progress.jsonl"
    flush_calls = []
    monkeypatch.setattr(survey.os, "fsync", lambda fd: flush_calls.append(fd))

    survey.checkpoint(path, "permit_verified", permit_id="BLD26-00469")
    survey.checkpoint(path, "catalog_loaded", offered_types=["Rough"])

    saved = events(path)
    assert [row["event"] for row in saved] == ["permit_verified", "catalog_loaded"]
    assert saved[0]["permit_id"] == "BLD26-00469"
    assert saved[1]["offered_types"] == ["Rough"]
    assert len(flush_calls) == 2
    assert "password" not in path.read_text(encoding="utf-8").casefold()


def test_main_persists_interrupted_step_and_never_launches_a_real_session(tmp_path, monkeypatch):
    session = FakeSession()
    progress_paths = []
    original_checkpoint = survey.checkpoint

    def checkpoint(path, event, **details):
        progress_paths.append(path)
        original_checkpoint(path, event, **details)

    monkeypatch.setattr(survey, "SolariSession", lambda: session)
    monkeypatch.setattr(survey, "OUTDIR", tmp_path)
    monkeypatch.setattr(survey, "checkpoint", checkpoint)

    async def interrupt(*args, **kwargs):
        raise InterruptedSurvey("synthetic interruption after login")

    monkeypatch.setattr(survey, "reach_calendar", interrupt)

    with pytest.raises(InterruptedSurvey, match="synthetic interruption"):
        asyncio.run(_run_main_with_argv([
            "--record", "BLD26-00469", "--types", "Rough", "--next-clicks", "1",
        ]))

    progress_path = next(path for path in progress_paths if path.name.endswith(".progress.jsonl"))
    recorded = [row["event"] for row in events(progress_path)]
    assert "login_complete" in recorded
    assert recorded[-2:] == ["survey_interrupted", "survey_teardown_started"]
    assert session.closed


def _run_main_with_argv(argv):
    import sys

    async def invoke():
        original = sys.argv
        sys.argv = ["ni_calendar_horizon.py", *argv]
        try:
            return await survey.main()
        finally:
            sys.argv = original

    return invoke()
