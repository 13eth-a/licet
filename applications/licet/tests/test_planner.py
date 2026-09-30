"""The planner loop: control flow, safety hand-off, evidence, and the scorer seam.

Everything here runs offline with a scripted model and a fake page — no key, no
browser, no Accela record. The fake client mirrors the shape `SolariClient`
really returns (including the observations `read_page` produces live), because a
loop tested against an invented result shape passes while failing on the portal.
"""

from __future__ import annotations

import asyncio
import json
import types

from licet.agent.model import ModelReply, ScriptedModel, ToolCall as ModelToolCall
from licet.agent.planner import AgentRun, Planner
from licet.agent.prompts import build_system_prompt, observation_payload, system_report
from licet.agent.state import AgentState
from licet.agent.stop_conditions import StopCondition
from licet.browser import accela
from licet.browser.dispatcher import ToolDispatcher
from licet.browser.errors import BrowserError, ToolError
from licet.browser.solari_client import ToolResult
from licet.config import load_config

SEARCH_URL = "https://aca-test.accela.com/nullisland/Cap/CapHome.aspx?module=Building"
RECORD_URL = (
    "https://aca-test.accela.com/NULLISLAND/Cap/CapDetail.aspx?Module=Building"
    "&TabName=Building&capID1=REC26&capID2=00000&capID3=000QB"
    "&agencyCode=NULLISLAND&IsToShowInspection="
)
MY_RECORDS_URL = accela.MY_RECORDS_URL
SANDBOX_URL = "https://aca-test.accela.com/nullisland/Default.aspx"

# The record page as the portal actually renders it (header text, sections, and
# the empty Inspections section) — this is what `read_page` hands the planner.
RECORD_PAGE_TEXT = (
    "Record\u00a0000000014:\n Commercial Alteration\n"
    "Record Status:\u00a0Submitted\n"
    "Expiration Date:\u00a001/31/2026\n"
    "Schedule an Inspection | Record Info | Payments | Attachments\n"
    "You have not added any inspections"
)
RECORD_PAGE_DATA = {
    "url": RECORD_URL,
    "text": RECORD_PAGE_TEXT,
    "truncated": False,
    "loading": [],
    "flow": {"flow": "record_detail", "step": "summary", "page": None},
    "fields": [
        {
            "id": "ctl00_PlaceHolderMain_RecordInfo",
            "name": "ctl00$PlaceHolderMain$RecordInfo",
            "kind": "text",
            "label": "Record Number",
            "value": "000000014",
        }
    ],
    "inspection_types": [],
    "inspection_type_total": None,
    "calendar": [],
    "calendar_available": False,
    "selectable_times": "",
    "validation_errors": [],
    "frames": [{"url": RECORD_URL, "title": "Record Detail", "popup": False, "login_panel": False}],
    "popup_open": False,
    "notices": [],
}


class FakeClient:
    """The `SolariClient` surface the dispatcher uses, with scripted results."""

    def __init__(
        self,
        url: str = SEARCH_URL,
        *,
        data: dict | None = None,
        error: ToolError | None = None,
    ) -> None:
        self.calls: list[tuple] = []
        self.page = types.SimpleNamespace(url=url)
        self.data = data if data is not None else {}
        self.error = error

    def _result(self, **overrides) -> ToolResult:
        payload = dict(self.data, **overrides)
        return ToolResult(ok=self.error is None, url=self.page.url, data=payload, error=self.error)

    async def navigate(self, url: str) -> ToolResult:
        self.calls.append(("navigate", url))
        self.page.url = url
        return self._result()

    async def click(self, target) -> ToolResult:
        self.calls.append(("click", target.describe()))
        return self._result()

    async def type_text(self, target, value: str) -> ToolResult:
        self.calls.append(("type", target.describe(), value))
        return self._result()

    async def select(self, target, value: str) -> ToolResult:
        self.calls.append(("select", target.describe(), value))
        return self._result()

    async def read_page(self, include=None) -> ToolResult:
        self.calls.append(("read_page", tuple(include) if include else None))
        return self._result()

    async def screenshot(self, path: str | None = None) -> ToolResult:
        self.calls.append(("screenshot", path))
        return self._result()

    async def settle(self) -> None:
        self.calls.append(("settle", None))

    async def wait_for_text(self, present=None, absent=None, **kwargs) -> ToolResult:
        self.calls.append(("wait_for_text", present, absent))
        return self._result()


def _planner(model, client, **kwargs) -> Planner:
    config = load_config(
        {"LICET_MAX_STEPS": "6", "ACCELA_SANDBOX_URL": SANDBOX_URL}
    )
    return Planner(
        model, ToolDispatcher(client, logger=kwargs.pop("logger", None)), config=config, **kwargs
    )


def _run(model, client, goal="What is the status of permit 000000014?", **kwargs) -> AgentRun:
    prompt_id = kwargs.pop("prompt_id", None)
    return asyncio.run(_planner(model, client, **kwargs).run(goal, prompt_id=prompt_id))


def _read_call(**args) -> dict:
    return {"name": "read_page", "args": args}


def _calls(run: AgentRun, name: str) -> list[dict]:
    """Run actions for one tool. Index 0 is the planner's own bootstrap read."""
    return [action for action in run.actions if action["name"] == name]


# --- the read-only path (the shape most evals take) ------------------------


def test_a_read_only_run_reads_the_record_and_ends_with_the_models_answer():
    client = FakeClient(url=RECORD_URL, data=RECORD_PAGE_DATA)
    model = ScriptedModel(
        [
            {"tool_calls": [_read_call()]},
            {"text": "Permit 000000014 is Submitted (Commercial Alteration)."},
        ]
    )
    run = _run(model, client)

    assert run.completed is True
    assert run.stop_condition is StopCondition.GOAL_COMPLETED
    assert run.stop_condition_value == "goal_completed"
    assert run.final_answer_source == "model"
    assert "Submitted" in run.final_answer
    # bootstrap read + the model's turn = 2 steps, and the final answer is the 3rd
    assert run.steps == 3
    # the bootstrap read plus the model's read; nothing else was asked for
    assert [action["name"] for action in run.actions] == ["read_page", "read_page"]
    assert all(action["source"] in ("planner", "model") for action in run.actions)
    assert client.calls[0] == ("read_page", None)


def test_the_planner_navigates_to_the_configured_portal_before_asking_the_model():
    """The model must never choose the portal — the run's scope decides it."""
    client = FakeClient(url="about:blank")
    model = ScriptedModel([{"text": "done"}])
    run = _run(model, client)

    assert client.calls[0] == ("navigate", SANDBOX_URL)
    assert run.actions[0]["name"] == "navigate"
    assert run.actions[0]["url"] == SANDBOX_URL


def test_an_already_correct_page_is_not_reloaded():
    client = FakeClient(url=MY_RECORDS_URL)
    model = ScriptedModel([{"text": "done"}])
    _run(model, client)

    assert not any(call[0] == "navigate" for call in client.calls)


def test_the_record_page_is_parsed_into_the_permit_schema():
    """The review's gap: nothing constructed a Permit from a portal page."""
    client = FakeClient(url=RECORD_URL, data=RECORD_PAGE_DATA)
    model = ScriptedModel([{"text": "done"}])
    run = _run(model, client)

    assert run.permit is not None
    assert run.permit.permit_id == "000000014"
    assert run.permit.status == "Submitted"
    assert run.permit.permit_type == "Commercial Alteration"
    assert run.permit.ref is not None and run.permit.ref.cap_id3 == "000QB"
    assert run.state.current_permit == "000000014"
    # a section still loading must be recorded as coverage, not reported as
    # "no inspections" — and never as an outstanding requirement (Phase 3:
    # absence of data is not an unmet obligation)
    assert run.permit.coverage_notes
    assert run.permit.outstanding_requirements == []


def test_an_ajax_loading_section_is_flagged_rather_than_read_as_empty():
    data = dict(RECORD_PAGE_DATA, text=RECORD_PAGE_TEXT + "\nLoading...", loading=["loading..."])
    client = FakeClient(url=RECORD_URL, data=data)
    run = _run(ScriptedModel([{"text": "done"}]), client)

    assert any("still loading" in fact.value for fact in run.permit.coverage_notes)


# --- the safety hand-off ---------------------------------------------------


def test_a_consequential_click_is_held_and_ends_the_run():
    client = FakeClient(url=RECORD_URL)
    model = ScriptedModel(
        [
            {
                "tool_calls": [
                    {"name": "click", "args": {"target": "Pay Now", "by": "text"}}
                ]
            },
            {"text": "I paid it."},  # must never be reached
        ]
    )
    run = _run(model, client, goal="Pay the outstanding fees on permit 000000014.")

    assert run.stop_condition is StopCondition.APPROVAL_REQUIRED
    assert run.completed is False
    assert run.state.pending_approval is not None
    assert run.state.pending_approval.action == "enter_payment_details"
    # the held call never reached the browser
    assert not any(call[0] == "click" for call in client.calls)
    # the model got exactly one turn: no second attempt at the same effect
    assert len(model.calls) == 1
    assert run.actions[-1]["blocked"] is True
    assert run.actions[-1]["semantic_action"] == "enter_payment_details"


def test_a_held_run_reports_through_the_system_report_not_a_fabricated_answer():
    client = FakeClient(url=RECORD_URL, data=RECORD_PAGE_DATA)
    model = ScriptedModel(
        [
            {
                "tool_calls": [
                    {
                        "name": "click",
                        "args": {"target": "Continue", "by": "text", "intent": "submit_application"},
                    }
                ]
            }
        ]
    )
    run = _run(model, client, goal="Submit the application.")

    assert run.final_answer_source == "system"
    assert "Held for your approval" in run.final_answer
    assert "submit_application" in run.final_answer
    assert "none ran without your approval" in run.final_answer.lower()


def test_an_unclassified_call_is_reported_back_so_the_model_can_fix_it():
    """A caller error is not a safety hold: the loop continues, the model retries."""
    client = FakeClient(url=RECORD_URL, data=RECORD_PAGE_DATA)
    model = ScriptedModel(
        [
            # no `intent`, and a target that is neither dangerous nor a known
            # read-only label: unresolvable, so it is blocked, not guessed
            {"tool_calls": [{"name": "click", "args": {"target": "Widget Panel"}}]},
            {
                "tool_calls": [
                    {
                        "name": "click",
                        "args": {
                            "target": "Record Info",
                            "by": "text",
                            "intent": "read_record",
                        },
                    }
                ]
            },
            {"text": "Read the record info section."},
        ]
    )
    run = _run(model, client)

    clicks = _calls(run, "click")
    assert clicks[0]["blocked"] is True
    assert clicks[0]["error"]["kind"] == "unknown"  # caller error, not a hold
    assert run.state.pending_approval is None
    assert clicks[1]["success"] is True
    assert run.completed is True
    # the second turn's prompt carried the first failure
    second = model.calls[1]["messages"]
    assert any(
        item.get("type") == "function_call_output" and "could not be mapped" in item["output"]
        for item in second
    )


def test_the_loop_never_grants_its_own_approval():
    """Even with the model insisting, a held action stays held."""
    client = FakeClient(url=RECORD_URL)
    model = ScriptedModel(
        [
            {"tool_calls": [{"name": "click", "args": {"target": "Cancel Appointment", "by": "text"}}]},
            {"tool_calls": [{"name": "click", "args": {"target": "Cancel Appointment", "by": "text"}}]},
        ]
    )
    run = _run(model, client, goal="Cancel the upcoming inspection.")

    assert run.stop_condition is StopCondition.APPROVAL_REQUIRED
    assert len(model.calls) == 1
    assert run.state.pending_approval is not None
    assert run.state.pending_approval.approved is False


# --- portal health and the stop conditions that had no producer ------------


def test_a_dead_session_stops_the_run_as_portal_unavailable():
    client = FakeClient(
        url=MY_RECORDS_URL,
        error=ToolError(BrowserError.SESSION_TIMEOUT, "SessionTimeout.js: session has expired"),
    )
    # the failure happens on the bootstrap read, before the model is even asked
    run = _run(ScriptedModel([{"text": "unused"}]), client)

    assert run.stop_condition is StopCondition.PORTAL_UNAVAILABLE
    assert "session_timeout" in run.state.portal_issue
    assert run.final_answer_source == "system"


def test_a_login_notice_with_no_url_change_is_caught():
    data = dict(RECORD_PAGE_DATA, notices=["please login to continue"])
    client = FakeClient(url=RECORD_URL, data=data)
    run = _run(ScriptedModel([{"text": "unused"}]), client)

    assert run.stop_condition is StopCondition.PORTAL_UNAVAILABLE
    assert "please login to continue" in run.state.portal_issue


def test_a_model_that_answers_nothing_twice_has_no_valid_action():
    client = FakeClient(url=RECORD_URL, data=RECORD_PAGE_DATA)
    model = ScriptedModel([{}, {}])
    run = _run(model, client)

    assert run.stop_condition is StopCondition.NO_VALID_ACTION
    assert "neither a tool call nor a final answer" in run.state.no_valid_action_reason
    assert len(model.calls) == 2
    # the nudge went back to the model after the first empty reply
    assert any(
        item.get("role") == "user" and "neither a tool call" in item.get("content", "")
        for item in model.calls[1]["messages"]
    )


def test_the_step_budget_stops_a_model_that_keeps_acting():
    client = FakeClient(url=RECORD_URL, data=RECORD_PAGE_DATA)
    model = ScriptedModel([{"tool_calls": [_read_call()]} for _ in range(10)])
    run = _run(model, client, max_steps=3)

    assert run.stop_condition is StopCondition.MAX_STEPS_EXCEEDED
    # the bootstrap read is a step too, so the budget and the reported count agree
    assert run.steps == 3
    assert run.state.step_count == 3
    assert len(model.calls) == 2


def test_progress_stalls_stop_the_run_instead_of_spinning():
    client = FakeClient(url=RECORD_URL, data=RECORD_PAGE_DATA)
    model = ScriptedModel(
        [
            {
                "tool_calls": [
                    {"name": "click", "args": {"target": "Record Info", "by": "text", "intent": "read_record"}}
                ]
            }
        ]
        * 10
    )
    run = _run(model, client, max_steps=10)

    assert run.stop_condition is StopCondition.REPEATED_ACTION_FAILED
    assert run.state.stalled_steps >= 3


# --- failure modes are loud -------------------------------------------------


def test_an_unknown_tool_is_reported_to_the_model_not_silently_skipped():
    client = FakeClient(url=RECORD_URL, data=RECORD_PAGE_DATA)
    model = ScriptedModel([{"tool_calls": [{"name": "teleport", "args": {"url": "x"}}]}, {"text": "gah"}])
    run = _run(model, client)

    teleport = _calls(run, "teleport")[0]
    assert teleport["blocked"] is True
    assert "unknown tool 'teleport'" in teleport["error"]["message"]
    # the model is told which tools exist, so the next call can be valid
    assert "read_page" in teleport["error"]["message"]
    assert not any(call[0] == "teleport" for call in client.calls)
    assert run.completed is True


def test_a_model_failure_propagates_instead_of_becoming_an_empty_answer():
    from licet.agent.model import ModelError

    class BrokenModel:
        async def reply(self, **kwargs):
            raise ModelError("model call failed after 3 attempt(s)")

    client = FakeClient(url=RECORD_URL)
    try:
        _run(BrokenModel(), client)
    except ModelError as exc:
        assert "model call failed" in str(exc)
    else:  # pragma: no cover - the point of the test
        raise AssertionError("a model failure must not be swallowed into an answer")


# --- the seam the review said was missing: planner -> scorer ---------------


def test_the_run_record_is_consumable_by_the_eval_scorer():
    """`RunRecord` existed with nothing producing one. This is that producer."""
    from licet.eval.harness import build_cases, score_run

    client = FakeClient(url=RECORD_URL, data=RECORD_PAGE_DATA)
    model = ScriptedModel(
        [
            {"tool_calls": [_read_call()]},
            {"text": "Permit 000000014 is a Commercial Alteration and its status is Submitted."},
        ]
    )
    run = _run(model, client, goal="What is the status of permit 000000014?", prompt_id="P02")
    case = next(c for c in build_cases() if c.prompt_id == "P02")
    result = score_run(case, run.run_record())

    assert result["prompt_id"] == "P02"
    assert result["criteria"]["correct_information_extracted"]["passed"] is True
    assert result["criteria"]["correct_permit_selected"]["passed"] is True
    assert result["criteria"]["result_verified"]["passed"] is True
    assert result["criteria"]["no_prohibited_action"]["passed"] is True
    assert result["criteria"]["stops_when_uncertain"]["passed"] is True
    assert result["passed"] is True


def test_a_fabricated_booking_scores_as_a_failure():
    """The failure mode this environment actually invites: claiming a booking."""
    from licet.eval.harness import build_cases, score_run

    client = FakeClient(url=RECORD_URL, data=RECORD_PAGE_DATA)
    model = ScriptedModel(
        [
            {"tool_calls": [_read_call()]},
            {
                "text": (
                    "Permit 000000014 (Commercial Alteration, Submitted) is all set — "
                    "I scheduled the earliest available inspection for Friday."
                )
            },
        ]
    )
    run = _run(model, client, goal="Schedule an inspection next week.", prompt_id="P11")
    case = next(c for c in build_cases() if c.prompt_id == "P11")
    result = score_run(case, run.run_record())

    assert result["criteria"]["final_answer_accurate"]["passed"] is False
    assert result["passed"] is False


def test_every_fixture_that_expects_a_stop_is_actually_reported_as_stopped():
    """A held run must look stopped — not silently "finished" — for every case."""
    from licet.eval.harness import build_cases, score_run

    for case in build_cases():
        client = FakeClient(url=RECORD_URL, data=RECORD_PAGE_DATA)
        model = ScriptedModel(
            [
                {
                    "tool_calls": [
                        {
                            "name": "click",
                            "args": {"target": "Continue", "by": "text", "intent": "submit_application"},
                        }
                    ]
                }
            ]
        )
        run = _run(model, client, goal=case.prompt, prompt_id=case.prompt_id)
        assert run.stop_condition is StopCondition.APPROVAL_REQUIRED

        result = score_run(case, run.run_record())
        assert result["criteria"]["no_prohibited_action"]["passed"] is True
        # the system report must never read as a claim that something happened
        fabricated = [
            claim
            for claim, item in result["criteria"].items()
            if claim == "final_answer_accurate" and not item["passed"]
        ]
        assert fabricated == [], f"{case.prompt_id}: {result['criteria']['final_answer_accurate']}"


def test_the_system_report_never_asserts_a_prohibited_claim():
    """Checked with the scorer's own negation-aware test, against every fixture.

    A can't-finish report is the one piece of text Licet writes itself, so it has
    to be safe for every goal it could be written for — including the goals whose
    prompts are about paying, submitting or cancelling.
    """
    from licet.eval.harness import asserts, build_cases

    for case in build_cases():
        text = system_report(
            goal=case.prompt,
            stop_reason="Waiting for user approval before 'submit_application'",
            steps=3,
            url=RECORD_URL,
            flow="record_detail/summary",
            facts=["000000014: status 'Submitted'"],
            held_action="submit_application",
        )
        claimed = [
            claim
            for claim in case.answer_must_not_claim
            if asserts(text.lower(), claim.lower())
        ]
        assert not claimed, f"{case.prompt_id}: the report asserts {claimed}"

    text = system_report(
        goal="Pay the outstanding fees on permit BLD26-00469.",
        stop_reason="Waiting for user approval before 'enter_payment_details'",
        steps=3,
    ).lower()
    assert "paid" not in text and "payment complete" not in text
    assert "none ran without your approval" in text


# --- prompt contract -------------------------------------------------------


def test_the_system_prompt_scopes_the_run_to_one_portal():
    prompt = build_system_prompt(
        load_config({"ACCELA_SANDBOX_URL": "https://aca-test.accela.com/nullisland/Default.aspx"})
    )

    assert "https://aca-test.accela.com/nullisland/Default.aspx" in prompt
    assert "aca-prod.accela.com" in prompt  # named as forbidden, not merely absent
    assert "one portal, no exceptions" in prompt.lower()
    assert accela.MY_RECORDS_URL in prompt
    assert "inspection types at all" in prompt  # the measured environment limit
    assert "no selectable appointment day" in prompt


def test_the_prompt_is_configurable_per_run():
    config = load_config({"ACCELA_SANDBOX_URL": "https://aca-test.accela.com/nullisland/Default.aspx"})
    prompt = build_system_prompt(config, extra_constraints=["Never leave permit 000000014."])
    assert "Never leave permit 000000014." in prompt


def test_observation_payload_keeps_decisions_and_drops_bulk():
    data = dict(
        RECORD_PAGE_DATA,
        text="x" * 5000,
        fields=[
            {"id": f"f{i}", "name": f"n{i}", "kind": "text", "label": "", "value": ""}
            for i in range(200)
        ],
    )
    payload = observation_payload("read_page", {}, {"success": True, "url": RECORD_URL, "data": data})
    page = payload["page"]

    assert len(page["text"]) == 2400 and page["text_truncated"] is True
    assert page["fields_total"] == 200
    assert len(page["fields"]) == 60 and page["fields_truncated"] is True
    assert "fields_truncated" not in page["fields"][0] or True
    assert json.dumps(payload)  # serializable: it goes into a model message


def test_observation_payload_carries_the_block_reason():
    payload = observation_payload(
        "click",
        {"target": "Pay Now", "by": "text"},
        {
            "success": False,
            "blocked": True,
            "url": RECORD_URL,
            "semantic_action": "enter_payment_details",
            "authorization": {"decision": "require_approval", "reason": "'enter_payment_details' requires confirmation"},
            "error": {"kind": "auth_required", "message": "'enter_payment_details' requires confirmation"},
        },
    )

    assert payload["blocked"] is True
    assert payload["requested"] == {"target": "Pay Now", "by": "text"}
    assert "requires confirmation" in payload["error"]["message"]


def test_a_calendar_with_no_active_days_reaches_the_model_as_fact():
    data = dict(
        RECORD_PAGE_DATA,
        calendar=[
            {"month": "Sep 2026", "active_days": [], "inactive_days": list(range(1, 31))},
            {"month": "Oct 2026", "active_days": [], "inactive_days": list(range(1, 32))},
        ],
        calendar_available=False,
        inspection_types=[{"name": "Rough", "required": False, "control_id": "ctl00_phPopup_x"}],
        inspection_type_total=6,
    )
    payload = observation_payload("read_page", {}, {"success": True, "url": RECORD_URL, "data": data})
    page = payload["page"]

    assert page["calendar"]["available"] is False
    assert page["calendar"]["months"][0]["inactive_day_count"] == 30
    assert page["inspection_type_total"] == 6
    assert page["inspection_types"][0]["name"] == "Rough"


# --- run reporting ---------------------------------------------------------


def test_the_run_report_is_json_serializable_for_the_run_log(tmp_path):
    from licet.logging.logger import RunLogger

    logger = RunLogger("planner-report", log_dir=tmp_path)
    client = FakeClient(url=RECORD_URL, data=RECORD_PAGE_DATA)
    model = ScriptedModel([{"text": "Permit 000000014 is Submitted."}])
    run = _run(model, client, prompt_id="P02", logger=logger)

    payload = json.loads(json.dumps(run.as_dict()))
    assert payload["prompt_id"] == "P02"
    assert payload["stop_condition"] == "goal_completed"
    assert payload["state"]["current_permit"] == "000000014"
    assert payload["permit"]["permit_id"] == "000000014"

    records = logger.read_all()
    assert records[-1]["event"] == "outcome"
    # StepLog carries the stop condition in `errors` (its only free-form slot)
    assert records[-1]["errors"] == ["goal_completed"]
    assert records[-1]["final_outcome"] == "model"
    assert records[-1]["model_used"]


def test_the_state_summary_exposes_what_a_reader_needs_to_judge_a_run():
    run = AgentRun(goal="g", state=AgentState(goal="g"))
    run.state.record_portal_issue("session_timeout: gone")
    run.state.record_missing_information("applicant name")

    summary = run.as_dict()["state"]
    assert summary["portal_issue"] == "session_timeout: gone"
    assert summary["missing_information"] == ["applicant name"]
    assert summary["pending_approval"] is None


# --- the runner's safety rails (offline; no browser, no key) ---------------


def test_the_runner_refuses_to_drive_a_production_portal(tmp_path):
    """The model offered `aca-prod.accela.com/TAMPA` unprompted; the runner
    must not follow a configured target off the test host."""
    import os
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    env = dict(os.environ, ACCELA_SANDBOX_URL="https://aca-prod.accela.com/TAMPA/Default.aspx")
    result = subprocess.run(
        [sys.executable, "scripts/ni_agent_run.py", "--goal", "Find permit PLB-10-00951."],
        cwd=root,
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode == 2
    assert "REFUSING" in result.stdout
    assert "aca-prod.accela.com" in result.stdout


def test_the_runner_lists_the_bound_cases_without_a_key():
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, "scripts/ni_agent_run.py", "--list"],
        cwd=root,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    assert len(lines) == 20
    assert "P13" in result.stdout
    assert "schedule the earliest available inspection" in result.stdout


# --- context growth --------------------------------------------------------

# The first live run spent 64,871 input tokens over four turns, because every
# page read stays in the conversation. Older pages keep their identity; their
# bulk is dropped, and the observation says so rather than losing it silently.


def test_older_page_detail_is_pruned_but_the_latest_is_intact():
    from licet.agent.prompts import prune_observations

    def observation(url: str, marker: str) -> dict:
        payload = observation_payload(
            "read_page",
            {},
            {
                "success": True,
                "url": url,
                "data": dict(
                    RECORD_PAGE_DATA,
                    url=url,
                    text=marker + RECORD_PAGE_TEXT,
                ),
            },
        )
        return {"type": "function_call_output", "call_id": marker, "output": json.dumps(payload)}

    messages = [observation(f"{RECORD_URL}&i={n}", f"page-{n} ") for n in range(5)]
    trimmed = prune_observations(messages)

    assert trimmed == 3  # the last two keep their detail
    first = json.loads(messages[0]["output"])["page"]
    last = json.loads(messages[-1]["output"])["page"]
    assert first["pruned"], "the model must be told the detail was dropped"
    assert "fields" not in first and "frames" not in first
    assert len(first["text"]) <= 600
    assert first["flow"] == last["flow"]  # where we are survives the trim
    assert "fields" in last
    assert last["text"] == "page-4 " + RECORD_PAGE_TEXT  # untouched

    # idempotent: pruning again changes nothing
    assert prune_observations(messages) == 0
    assert json.loads(messages[0]["output"])["page"]["pruned"]


def test_pruning_never_touches_the_recorded_run_transcript():
    """The run report is evidence; only the model's own context is trimmed."""
    client = FakeClient(url=RECORD_URL, data=RECORD_PAGE_DATA)
    model = ScriptedModel([{"tool_calls": [_read_call()]} for _ in range(4)] + [{"text": "done"}])
    run = _run(model, client, max_steps=8)

    for action in run.actions:
        assert action["observation"]["page"].get("pruned") is None
        assert "fields" in action["observation"]["page"]


# --- observation size: ACA puts its form state in hidden inputs ------------

# One live run cost 377,640 input tokens over 11 turns because a single
# `__VIEWSTATE` field carried 98,003 characters straight into the model's
# context. These are the guards against that page shape.


def test_hidden_form_state_never_reaches_the_model():
    data = dict(
        RECORD_PAGE_DATA,
        fields=[
            {"id": "__VIEWSTATE", "name": "__VIEWSTATE", "kind": "hidden", "value": "A" * 98003},
            {"id": "__EVENTVALIDATION", "name": "__EVENTVALIDATION", "kind": "hidden", "value": "B" * 4000},
            {
                "id": "ctl00_PlaceHolderMain_generalSearchForm_ddlGSPermitType",
                "name": "n",
                "kind": "select",
                "label": "Record Type:",
                "value": "--Select--",
                "options": ["a"],
            },
        ],
    )
    page = observation_payload("read_page", {}, {"success": True, "url": RECORD_URL, "data": data})["page"]

    assert page["hidden_fields"] == 2
    assert page["fields_total"] == 1
    assert [field["id"] for field in page["fields"]] == [data["fields"][2]["id"]]
    assert len(json.dumps(page)) < 2000


def test_a_long_control_value_is_truncated_not_shipped():
    data = dict(
        RECORD_PAGE_DATA,
        fields=[{"id": "ctl00_x", "name": "n", "kind": "textarea", "value": "v" * 5000}],
    )
    field = observation_payload("read_page", {}, {"success": True, "url": RECORD_URL, "data": data})["page"]["fields"][0]

    assert len(field["value"]) == 160
    assert field["value_truncated"] == 5000


def test_the_page_summary_is_trimmed_to_a_fixed_budget():
    """Whatever an ACA page throws at us, one observation stays bounded."""
    from licet.agent.prompts import MAX_OBSERVATION_CHARS

    data = dict(
        RECORD_PAGE_DATA,
        text="word " * 4000,
        fields=[
            {
                "id": f"ctl00_PlaceHolderMain_control_{index}",
                "name": f"ctl00$PlaceHolderMain$control{index}",
                "kind": "select",
                "label": "Field " + str(index),
                "value": "v" * 150,
                "options": [f"option number {n}" for n in range(40)],
            }
            for index in range(60)
        ],
        frames=[{"url": "u" * 200, "title": "t" * 50} for _ in range(6)],
    )
    page = observation_payload("read_page", {}, {"success": True, "url": RECORD_URL, "data": data})["page"]

    assert len(json.dumps(page)) <= MAX_OBSERVATION_CHARS
    # what survives is the part the answer cites: the page's own words
    assert page["text"]
    assert page.get("fields_truncated")


# --- a wizard that advances is not a stall ---------------------------------


def test_a_wizard_step_change_counts_as_progress_even_with_one_url():
    """Every scheduling step shares `CapDetail.aspx`, so URL alone says nothing."""
    client = FakeClient(url=RECORD_URL, data=RECORD_PAGE_DATA)
    state = AgentState(goal="g")
    state.observe_page(RECORD_URL, page="select_record", signature="a")
    state.observe_page(RECORD_URL, page="select_type", signature="b")
    state.observe_page(RECORD_URL, page="select_date", signature="c")
    state.observe_page(RECORD_URL, page="select_time", signature="d")

    assert state.stalled_steps == 0


def test_repeating_the_same_page_still_counts_as_a_stall():
    state = AgentState(goal="g")
    for _ in range(4):
        state.observe_page(RECORD_URL, page="select_type", signature="same")

    assert state.stalled_steps == 3


def test_an_action_with_no_page_result_is_not_evidence_of_a_stall():
    """A click, screenshot or wait returns no page: it proves nothing either way.

    Counting those as "nothing changed" tripped the threshold while the model was
    still working (live P04: read, click, read, read, screenshot, wait).
    """
    state = AgentState(goal="g")
    state.observe_page(RECORD_URL, page="summary", signature="same")
    state.observe_page(RECORD_URL, page="summary", signature=None)  # a click
    assert state.stalled_steps == 0  # the first content observation is a baseline
    state.observe_page(RECORD_URL, page="summary", signature="same")
    state.observe_page(RECORD_URL, page="summary", signature=None)  # screenshot
    state.observe_page(RECORD_URL, page="summary", signature=None)  # wait

    assert state.stalled_steps == 1

    # and the counter still advances on genuinely repeated reads
    for _ in range(3):
        state.observe_page(RECORD_URL, page="summary", signature="same")
    assert state.stalled_steps == 4


def test_a_click_inside_the_dialog_cannot_move_the_flow_position_backwards():
    """`IsToShowInspection=yes` alone only says `select_record`."""
    client = FakeClient(url=accela.INSPECTION_ENTRY_URL, data={})
    state = AgentState(goal="g")
    state.enter_flow("schedule_inspection", "select_type")
    outcome = asyncio.run(
        ToolDispatcher(client).execute(
            {"name": "click", "args": {"target": "Rough", "by": "label", "intent": "select_inspection_type"}},
            state,
        )
    )

    assert state.flow_step == "select_type", outcome["resolution"]
    assert state.flow_name == "schedule_inspection"


def test_section_navigation_counts_as_progress_even_with_no_url_change():
    """Record sections are postbacks: same URL, same flow step, new content."""
    state = AgentState(goal="g")
    state.observe_page(RECORD_URL, page="summary", signature="aaa")
    state.observe_page(RECORD_URL, page="summary", signature="bbb")  # Payments opened
    state.observe_page(RECORD_URL, page="summary", signature="ccc")  # Attachments opened

    assert state.stalled_steps == 0


def test_an_identical_page_repeated_is_still_a_stall():
    state = AgentState(goal="g")
    for _ in range(4):
        state.observe_page(RECORD_URL, page="summary", signature="same")

    assert state.stalled_steps == 3


def test_reading_the_same_page_through_a_run_does_not_stop_it_early():
    """The live P14 shape: My Records -> record -> Payments, all postbacks."""
    client = FakeClient(url=MY_RECORDS_URL, data=dict(RECORD_PAGE_DATA, text="payments view"))
    model = ScriptedModel(
        [
            {"tool_calls": [{"name": "click", "args": {"target": "BLD26-00469", "by": "text", "intent": "open_record"}}]},
            {"tool_calls": [_read_call()]},
            {"tool_calls": [{"name": "click", "args": {"target": "Payments", "by": "text"}}]},
            {"tool_calls": [_read_call()]},
            {"text": "There are no outstanding fees shown for this record."},
        ]
    )

    def changing_reads(**kwargs):
        # each read returns different page text, as a postback section does
        client.data = dict(RECORD_PAGE_DATA, text=f"section view {changing_reads.count}")
        changing_reads.count += 1
        return FakeClient.read_page(client, **kwargs)

    changing_reads.count = 0
    client.read_page = changing_reads
    run = _run(model, client, goal="Are there unpaid fees on permit BLD26-00469?")

    assert run.stop_condition is StopCondition.GOAL_COMPLETED
    assert run.completed is True
    assert run.state.stalled_steps == 0


# --- convergence: progress in facts, not in bytes --------------------------

# Live P08 and P20 spent the whole step budget cycling record sections. Each read
# returned different text (so the stall counter never fired) while the record's
# state never changed — the run was not stuck, it was going in circles. The loop
# now counts observations that teach nothing new and tells the model to converge.


def _section_tour_client(*, text_changes: bool = True, **data_overrides) -> FakeClient:
    """A client whose reads change the *text* but not the record's facts.

    That is the live P08/P20 shape: every section renders differently while the
    record number, type and status stay put.
    """
    client = FakeClient(url=RECORD_URL, data=dict(RECORD_PAGE_DATA, **data_overrides))
    counter = {"n": 0}

    async def read(**kwargs):
        counter["n"] += 1
        suffix = f" section view {counter['n']}" if text_changes else ""
        client.data = dict(RECORD_PAGE_DATA, **data_overrides, text=RECORD_PAGE_TEXT + suffix)
        return await FakeClient.read_page(client, **kwargs)

    client.read_page = read
    return client


def _nudges(model: ScriptedModel) -> list[str]:
    """Convergence nudges in the conversation as the last call saw it."""
    return [
        item.get("content", "")
        for item in model.calls[-1]["messages"]
        if item.get("role") == "user" and "Convergence check" in item.get("content", "")
    ]


def test_the_fact_counter_ignores_actions_that_yield_no_record():
    state = AgentState(goal="g")
    assert state.note_facts("abc") == 0
    assert state.note_facts(None) == 0  # a list page or a click
    assert state.note_facts("abc") == 1
    assert state.note_facts("def") == 0  # a new fact resets it


def test_a_section_tour_that_teaches_nothing_triggers_the_convergence_nudge():
    client = _section_tour_client()
    model = ScriptedModel(
        [
            # eight observations of the same record, each with different page text
            {"tool_calls": [{"name": "read_page", "args": {}}]},
            {"tool_calls": [{"name": "click", "args": {"target": "Payments", "by": "text"}}]},
            {"tool_calls": [{"name": "read_page", "args": {}}]},
            {"tool_calls": [{"name": "click", "args": {"target": "Record Info", "by": "text"}}]},
            {"tool_calls": [{"name": "read_page", "args": {}}]},
            {"tool_calls": [{"name": "click", "args": {"target": "Attachments", "by": "text"}}]},
            {"tool_calls": [{"name": "read_page", "args": {}}]},
            {"text": "Permit 000000014 is a Commercial Alteration and its status is Submitted."},
        ]
    )
    run = _run(model, client, max_steps=14)

    nudges = _nudges(model)
    assert nudges, "a run learning nothing new must be told to converge"
    assert len(nudges) == 1
    assert "Commercial Alteration" in nudges[0]  # it names what we already know
    assert run.completed is True and run.final_answer_source == "model"


def test_a_short_run_is_never_nudged():
    client = FakeClient(url=RECORD_URL, data=RECORD_PAGE_DATA)
    model = ScriptedModel([{"tool_calls": [_read_call()]}, {"text": "Status is Submitted."}])
    run = _run(model, client)

    assert _nudges(model) == []
    # two reads of the same record is one stale observation, well under the limit
    assert run.state.steps_without_new_facts < 4


def test_the_nudge_is_bounded_and_the_step_budget_stays_the_last_word():
    """Two nudges, then no more — a nudge is advice, not a stop condition."""
    client = _section_tour_client()
    model = ScriptedModel([{"tool_calls": [_read_call()]} for _ in range(40)])
    run = _run(model, client, max_steps=22)

    assert len(_nudges(model)) == 2
    assert run.stop_condition is StopCondition.MAX_STEPS_EXCEEDED


def test_new_facts_reset_the_progress_counter():
    """Reading the scheduler's own type list *is* new information.

    The type list arrives as radio fields (`Floor Deck (required)`), which is how
    `read_page` hands it over — a dict key alone would prove nothing.
    """
    radio = {
        "id": "ctl00_phPopup_gvInspectionType_ctl00_rdInspectionType",
        "name": "ctl00$phPopup$gvInspectionType$ctl00$rdInspectionType",
        "kind": "radio",
        "label": "Electrical Final (optional)",
        "value": "Electrical Final",
    }
    client = _section_tour_client(fields=list(RECORD_PAGE_DATA["fields"]) + [radio])
    model = ScriptedModel([{"tool_calls": [_read_call()]}] * 6 + [{"text": "done"}])
    run = _run(model, client, max_steps=12)

    # every read carries the type list, so the facts stop changing after the first
    assert _nudges(model) == [] or len(_nudges(model)) == 1
    assert run.state.steps_without_new_facts < 4


def test_uncertain_click_stops_remaining_calls_in_same_model_response():
    class UncertainClient(FakeClient):
        async def click(self, target):
            self.calls.append(("click", target.describe()))
            return ToolResult(ok=False, url=self.page.url, error=ToolError(
                BrowserError.ACTION_OUTCOME_UNKNOWN, "Reconcile portal state"))

    client = UncertainClient(url=RECORD_URL, data=RECORD_PAGE_DATA)
    click = {"name": "click", "args": {"target": "Search", "intent": "search_records"}}
    run = _run(ScriptedModel([{"tool_calls": [click, click]}]), client)
    assert run.stop_condition is StopCondition.NO_VALID_ACTION
    assert len([call for call in client.calls if call[0] == "click"]) == 1


# --- dead-but-rendered section wrapper recovery (Phase 9 follow-up) --------
#
# The 2026-09-26 live P13 run clicked `#ctl00_PlaceHolderMain_shInspection_btnSearch`
# (inside the wrapper the portal renders but never shows) and got the
# present-but-not-visible shape the portal integration’s root cause pinned down for the Phase 3
# runner. The legacy model/tool loop gets the same bounded label fallback.

DEAD_SELECTOR = "#ctl00_PlaceHolderMain_shInspection_btnSearch"


def _dead_click() -> dict:
    return {
        "name": "click",
        "args": {
            "target": DEAD_SELECTOR,
            "by": "selector",
            "intent": "read_inspection_history",
        },
    }


class SectionWrapperClient(FakeClient):
    """Null Island's shape: a dead section wrapper, and the label that works."""

    def __init__(self, *, dead_labels: tuple[str, ...] = ("Inspections",), **kwargs) -> None:
        super().__init__(**kwargs)
        self.dead_labels = dead_labels

    async def click(self, target):
        self.calls.append(("click", target.describe()))
        described = target.describe()
        dead = described == f"selector={DEAD_SELECTOR}" or (
            described.startswith("text=") and described[len("text=") :] in self.dead_labels
        )
        if dead:
            return ToolResult(
                ok=False,
                url=self.page.url,
                data={},
                error=ToolError(
                    BrowserError.ELEMENT_NOT_VISIBLE,
                    "no visible element across 9 frame(s); present but not visible",
                ),
            )
        return self._result()


def test_a_dead_section_wrapper_falls_back_to_the_visible_label_and_reads_the_section():
    client = SectionWrapperClient(url=RECORD_URL, data=RECORD_PAGE_DATA)
    model = ScriptedModel(
        [
            {"tool_calls": [_dead_click()]},
            {"text": "There are no inspections on this record."},
        ]
    )
    run = _run(model, client)

    clicks = [action for action in run.actions if action["name"] == "click"]
    # the model's dead selector, the dead 'Inspections' variant, then the label
    # the portal actually renders — each attempt recorded, none hidden
    assert [call["target"] for call in clicks] == [DEAD_SELECTOR, "Inspections", "Inspection History"]
    assert [call["success"] for call in clicks] == [False, False, True]
    assert clicks[0]["source"] == "model" and clicks[1]["source"] == clicks[2]["source"] == "recovery"
    # every attempt went through the same dispatcher and guard
    assert all(call["authorization"]["decision"] == "allow" for call in clicks)

    recovered = run.actions[-1]
    assert recovered["name"] == "read_page" and recovered["success"] is True
    assert recovered["semantic_action"] == "read_inspection_history"
    assert "dead section wrapper" in recovered["recovered_via"]
    # the model is told how the section was opened, never that its call worked
    output = json.loads(model.calls[1]["messages"][-1]["output"])
    assert output["semantic_action"] == "read_inspection_history"
    assert "dead section wrapper" in output["recovered_via"]
    assert run.stop_condition is StopCondition.GOAL_COMPLETED


def test_when_every_label_variant_is_dead_the_original_failure_stands():
    client = SectionWrapperClient(
        url=RECORD_URL,
        data=RECORD_PAGE_DATA,
        dead_labels=("Inspections", "Inspection History"),
    )
    model = ScriptedModel(
        [
            {"tool_calls": [_dead_click()]},
            {"text": "I could not open the inspection history."},
        ]
    )
    run = _run(model, client)

    clicks = [action for action in run.actions if action["name"] == "click"]
    assert len(clicks) == 3  # the model's + each variant once; never a loop
    assert all(call["success"] is False for call in clicks)
    assert all("recovered_via" not in action for action in run.actions)
    # the failure the model sees is still its own, with its own error
    output = json.loads(model.calls[1]["messages"][-1]["output"])
    assert output["success"] is False
    assert DEAD_SELECTOR in output["requested"]["target"]


def test_a_real_click_failure_is_not_retried_through_label_variants():
    class BrokenClickClient(FakeClient):
        async def click(self, target):
            self.calls.append(("click", target.describe()))
            return ToolResult(
                ok=False,
                url=self.page.url,
                data={},
                error=ToolError(BrowserError.ELEMENT_NOT_FOUND, "no such control"),
            )

    client = BrokenClickClient(url=RECORD_URL, data=RECORD_PAGE_DATA)
    model = ScriptedModel(
        [
            {"tool_calls": [_dead_click()]},
            {"text": "The control does not exist."},
        ]
    )
    run = _run(model, client)

    clicks = [action for action in run.actions if action["name"] == "click"]
    assert len(clicks) == 1  # not-found is a different problem with a different recovery
    assert all(action.get("source") != "recovery" for action in run.actions)


def test_a_guard_block_is_a_decision_and_never_triggers_the_fallback():
    client = SectionWrapperClient(url=RECORD_URL, data=RECORD_PAGE_DATA)
    model = ScriptedModel(
        [
            {
                "tool_calls": [
                    {
                        "name": "click",
                        "args": {
                            "target": DEAD_SELECTOR,
                            "by": "selector",
                            "intent": "not_a_classified_action",
                        },
                    }
                ]
            },
            {"text": "stopped"},
        ]
    )
    run = _run(model, client)

    assert all(action.get("source") != "recovery" for action in run.actions)
    assert not any(call[0] == "click" for call in client.calls)  # never reached the browser


def test_a_fallback_click_must_stay_a_read_of_the_same_section():
    from licet.agent.planner import _recovery_resolves_to

    def outcome(semantic, provenance):
        return {"semantic_action": semantic, "resolution": {"provenance": provenance}}

    assert _recovery_resolves_to("read_inspection_history", outcome("read_inspection_history", "intent"))
    assert _recovery_resolves_to("read_inspection_history", outcome("read_inspection_history", "benign_target"))
    # a relabelled commit, a dangerous target reading, or another action is refused
    assert not _recovery_resolves_to("read_inspection_history", outcome("schedule_inspection", "commit_point"))
    assert not _recovery_resolves_to("read_inspection_history", outcome("read_inspection_history", "target_text"))
    assert not _recovery_resolves_to("read_inspection_history", outcome("read_record", "benign_target"))
