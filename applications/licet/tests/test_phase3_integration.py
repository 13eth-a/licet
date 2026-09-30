"""Phase 3 integration: ACA adapter, read-only retrieval, renderer, errors.

These cover the pieces the Phase 3 checklist adds on top of the golden
reasoning set: the page-to-observation adapter for real ``read_page`` payloads,
the bounded read-only retrieval runner (which must never emit a mutation), the
narrative renderer (which must never invent claims), and the error taxonomy.
"""
from __future__ import annotations

import asyncio

import pytest

from licet.browser import accela
from licet.browser.dispatcher import ToolDispatcher
from licet.browser.errors import BrowserError, ToolError
from licet.browser.solari_client import ToolResult
from licet.phase3 import accela_extract
from licet.phase3.errors import Phase3Error, Phase3ErrorCode
from licet.phase3.extract import extract_partial_state, merge_partial_states
from licet.phase3.reasoning import understand
from licet.phase3.render import render_answer
from licet.phase3.runner import Phase3RetrievalRunner, RetrievalOutcome, run_retrieval
from licet.phase3.state import CoverageStatus, PermitState

# A read_page payload shaped exactly like the real client's (the planning test's
# captured Null Island record detail).
RECORD_URL = (
    "https://aca-test.accela.com/NULLISLAND/Cap/CapDetail.aspx?Module=Building"
    "&TabName=Building&capID1=REC26&capID2=00000&capID3=000QB"
    "&agencyCode=NULLISLAND&IsToShowInspection="
)

RECORD_PAGE_TEXT = (
    "Record\u00a0000000014: \n Commercial Alteration\nRecord Status: Submitted\n"
    "Expiration Date: 01/31/2026\n"
    "Record Info\nPayments\nAttachments\n Inspections\n"
    "Upcoming\n Schedule or Request an Inspection\n"
    " You have not added any inspections.\n"
)

RECORD_PAGE_DATA = {
    "url": RECORD_URL,
    "text": RECORD_PAGE_TEXT,
    "truncated": False,
    "loading": [],
    "fields": [],
    "inspection_types": [],
    "calendar": [],
}


# --- the error taxonomy ------------------------------------------------------


def test_phase3_error_codes_carry_sections():
    error = Phase3Error(Phase3ErrorCode.HISTORY_PARSE_FAILED, "bad rows", section="history")
    assert error.code is Phase3ErrorCode.HISTORY_PARSE_FAILED
    assert error.as_dict() == {
        "code": "HISTORY_PARSE_FAILED",
        "section": "history",
        "message": "bad rows",
    }
    assert accela_extract is not None  # adapter imported alongside


def test_section_error_codes_map_to_sections():
    assert Phase3ErrorCode.INSPECTIONS_NOT_FOUND.value == "INSPECTIONS_NOT_FOUND"
    assert Phase3ErrorCode.FEES_NOT_FOUND.value == "FEES_NOT_FOUND"
    assert Phase3ErrorCode.CONFLICTING_RECORD_STATE.value == "CONFLICTING_RECORD_STATE"
    assert Phase3ErrorCode.INSUFFICIENT_EVIDENCE.value == "INSUFFICIENT_EVIDENCE"
    assert Phase3ErrorCode.UNSUPPORTED_STATUS.value == "UNSUPPORTED_STATUS"
    assert Phase3ErrorCode.STATE_EXTRACTION_FAILED.value == "STATE_EXTRACTION_FAILED"


# --- the ACA adapter: read_page -> observations ------------------------------


def test_adapter_reads_the_record_header_without_inventing_fields():
    observation = accela_extract.overview_observation(RECORD_PAGE_DATA)
    assert observation["record_key"] == "NULLISLAND/Building/REC26/00000/000QB"
    fields = observation["fields"]
    assert fields["record_number"] == "000000014"
    assert fields["record_type"] == "Commercial Alteration"
    assert fields["status"] == "Submitted"
    assert fields["expiration_date"] == "01/31/2026"
    # nothing outside the labeled lines may be guessed
    assert "applicant" not in fields
    assert "address" not in fields


def test_adapter_flags_a_loading_read_as_partial_coverage():
    data = dict(RECORD_PAGE_DATA, loading=["loading..."])
    observation = accela_extract.inspections_observation(data)
    assert observation["coverage"] == "loading"


def test_adapter_treats_declared_empty_inspections_as_explicitly_empty():
    observation = accela_extract.inspections_observation(RECORD_PAGE_DATA)
    assert observation["coverage"] == "explicitly_empty"
    assert observation["rows"] == []


def test_adapter_downgrades_complete_coverage_when_text_was_truncated():
    state = extract_partial_state(
        dict(accela_extract.fees_observation(dict(RECORD_PAGE_DATA, truncated=True)),
             section="fees", coverage="complete",
             rows=[{"name": "Fee", "amount": "$1.00", "paid": False}])
    )
    assert state.coverage["fees"].status is CoverageStatus.PARTIAL
    assert "truncated" in state.coverage["fees"].note


def test_adapter_money_lines_become_fee_rows_and_nothing_else():
    text = "Permit fee | $74.50\nTotal Due: $74.50\nRecord Status: Submitted"
    rows = accela_extract._money_lines_as_rows(text)
    descriptions = [row["description"].lower() for row in rows]
    assert any("permit fee" in d for d in descriptions)
    assert not any("record status" in d for d in descriptions)


def test_adapter_passes_the_wizard_marker_through_as_data():
    data = dict(
        RECORD_PAGE_DATA,
        inspection_types=[{"name": "Floor Deck", "required": True}],
    )
    offers = accela_extract.offered_type_facts(data)
    assert offers == [{"name": "Floor Deck", "required": True}]


def test_adapter_end_to_end_into_phase3_state():
    observation = accela_extract.overview_observation(RECORD_PAGE_DATA)
    state = extract_partial_state(observation)
    assert state.record_number == "000000014"
    assert state.status_normalized is None or state.status == "Submitted"
    assert state.evidence and state.facts


# --- the retrieval runner: read-only, bounded, benign ------------------------


class RetrievalFakeClient:
    """Scripted client: records calls, answers read_page with queued payloads."""

    def __init__(self, url: str, reads: list[dict]) -> None:
        self.url = url
        self.reads = list(reads)
        self.read_index = 0
        self.calls: list[tuple[str, dict]] = []

    async def navigate(self, url: str) -> ToolResult:
        self.calls.append(("navigate", {"url": url}))
        self.url = url
        return ToolResult(ok=True, url=url, data={"url": url}, error=None)

    async def click(self, target) -> ToolResult:
        self.calls.append(("click", {"target": getattr(target, "describe", lambda: target)()}))
        return ToolResult(ok=True, url=self.url, data={}, error=None)

    async def type_text(self, target, value: str) -> ToolResult:
        self.calls.append(("type", {}))
        return ToolResult(ok=True, url=self.url, data={}, error=None)

    async def select(self, target, value: str) -> ToolResult:
        self.calls.append(("select", {}))
        return ToolResult(ok=True, url=self.url, data={}, error=None)

    async def read_page(self, **kwargs) -> ToolResult:
        self.calls.append(("read_page", kwargs))
        payload = dict(self.reads[min(self.read_index, len(self.reads) - 1)])
        self.read_index += 1
        return ToolResult(ok=True, url=payload.get("url", self.url), data=payload, error=None)

    async def screenshot(self, path=None) -> ToolResult:
        return ToolResult(ok=True, url=self.url, data={}, error=None)


def _dispatch(client) -> ToolDispatcher:
    return ToolDispatcher(client)  # type: ignore[arg-type]


def test_runner_drives_benign_clicks_and_merges_observations():
    inspections_read = dict(
        RECORD_PAGE_DATA,
        text=RECORD_PAGE_TEXT + "Electrical Rough | Completed | Corrections Required",
        inspection_types=[{"name": "Rough Electrical", "required": True}],
    )
    client = RetrievalFakeClient(RECORD_URL, [inspections_read])
    dispatcher = _dispatch(client)
    state = PermitState(record_key="NULLISLAND/Building/REC26/00000/000QB")
    runner = Phase3RetrievalRunner(
        dispatcher,
        record_ref={"capID1": "REC26", "capID2": "00000", "capID3": "000QB"},
    )
    runner._current_url = RECORD_URL
    outcome = asyncio.run(
        runner.retrieve_missing_sections(
            state, [{"section": "inspections", "reason": "needed", "stop_when": "read"}]
        )
    )

    assert outcome.sections_retrieved == ["inspections"]
    assert outcome.sections_failed == []
    # the only click is the section's benign label; the guard resolved it
    clicks = [call for call in client.calls if call[0] == "click"]
    assert clicks and "Inspections" in clicks[0][1]["target"]
    # the observation actually landed in the state: the text-line rows
    # validated against the lifecycle/result vocabularies, and the wizard's
    # (required) marker became a fact. Coverage is PARTIAL, not complete: this
    # fixture's declared-empty marker ("You have not added any inspections.")
    # sits beside a parsed row, so the page shape is self-contradicting and the
    # read must not claim complete coverage of it (GLM Phase 5 review H05).
    assert state.coverage["inspections"].status == CoverageStatus.PARTIAL
    assert state.inspections and state.inspections[0].failed
    assert any(f.field == "required_type" for f in state.facts)


def test_runner_fails_closed_without_record_identity():
    client = RetrievalFakeClient("https://aca-test.accela.com/nullisland/Home.aspx", [])
    dispatcher = _dispatch(client)
    state = PermitState(record_key="k")
    runner = Phase3RetrievalRunner(dispatcher)
    outcome = asyncio.run(
        runner.retrieve_missing_sections(
            state, [{"section": "fees", "reason": "needed", "stop_when": "read"}]
        )
    )
    assert outcome.sections_failed == ["fees"]
    assert client.calls == []  # nothing was executed at all


class HiddenWrapperClient(RetrievalFakeClient):
    """Fakes the live 2026-09-25 failure shape: the exact-label anchor exists
    in the DOM but is never visible, so the click reports `not_actionable` —
    the dead-but-rendered wrapper the click-through script documented."""

    def __init__(self, url: str, reads: list[dict]) -> None:
        super().__init__(url, reads)
        self.section_opened = False

    async def click(self, target) -> ToolResult:
        described = str(getattr(target, "describe", lambda: target)())
        raw_text = str(getattr(target, "text", "") or "")
        if not self.section_opened and "text=Inspections" in described:
            message = (
                "no visible element for text=Inspections across 9 frame(s); "
                "present but not visible: [\"a:text-is('Inspections')\"]"
            )
            return ToolResult(
                ok=False, url=self.url, data={},
                error=ToolError(BrowserError.NOT_ACTIONABLE, message),
            )
        self.section_opened = True
        self.calls.append(("click", {"target": described}))
        return ToolResult(ok=True, url=self.url, data={}, error=None)


def test_runner_falls_back_when_exact_label_is_dead_but_rendered():
    """GLM Phase 9: `not_actionable` on 'Inspections' must try the label the
    portal actually renders, not report the whole section unavailable."""
    inspections_read = dict(
        RECORD_PAGE_DATA,
        text=RECORD_PAGE_TEXT + "Electrical Rough | Completed | Corrections Required",
    )
    client = HiddenWrapperClient(RECORD_URL, [inspections_read, inspections_read])
    dispatcher = _dispatch(client)
    state = PermitState(record_key="NULLISLAND/Building/REC26/00000/000QB")
    runner = Phase3RetrievalRunner(
        dispatcher,
        record_ref={"capID1": "REC26", "capID2": "00000", "capID3": "000QB"},
    )
    runner._current_url = RECORD_URL
    outcome = asyncio.run(
        runner.retrieve_missing_sections(
            state, [{"section": "inspections", "reason": "needed", "stop_when": "read"}]
        )
    )
    labels = [call[1]["target"] for call in client.calls if call[0] == "click"]
    assert any("Inspection History" in str(label) for label in labels)
    assert outcome.sections_retrieved == ["inspections"]
    assert outcome.sections_failed == []
    assert state.coverage["inspections"].status == CoverageStatus.PARTIAL


def test_runner_reports_unavailable_after_all_label_variants_fail():
    """When every variant is dead-but-rendered, the section stays failed and
    the loop does not grow an unbounded click budget."""
    client = HiddenWrapperClient(RECORD_URL, [dict(RECORD_PAGE_DATA, text="Record 000000014: Commercial Alteration")])
    client.section_opened = True  # never lets a section click succeed

    async def always_hidden(target):
        described = str(getattr(target, "describe", lambda: target)())
        message = (
            "no visible element for text=" + described + " across 9 frame(s); "
            "present but not visible"
        )
        return ToolResult(
            ok=False, url=client.url, data={},
            error=ToolError(BrowserError.NOT_ACTIONABLE, message),
        )

    client.click = always_hidden  # type: ignore[method-assign]
    dispatcher = _dispatch(client)
    state = PermitState(record_key="NULLISLAND/Building/REC26/00000/000QB")
    runner = Phase3RetrievalRunner(
        dispatcher,
        record_ref={"capID1": "REC26", "capID2": "00000", "capID3": "000QB"},
    )
    runner._current_url = RECORD_URL
    outcome = asyncio.run(
        runner.retrieve_missing_sections(
            state, [{"section": "inspections", "reason": "needed", "stop_when": "read"}]
        )
    )
    labels = [action["click"] for action in outcome.actions if "click" in action]
    assert labels == ["Inspections", "Inspection History"]
    assert outcome.sections_failed == ["inspections"]


def test_runner_refuses_a_non_benign_section_click(monkeypatch):
    """A label that the guard resolves to anything other than a benign read
    target is refused even when the click itself succeeded."""
    from licet.browser import dispatcher as dispatcher_module

    def hostile_resolution(call, state):
        if call.name == "click":
            return dispatcher_module.Resolution(
                "open_record", "target_text", "forced non-benign resolution"
            )
        return dispatcher_module.resolve_action(call, state)

    monkeypatch.setattr(dispatcher_module, "resolve_action", hostile_resolution)
    client = HiddenWrapperClient(RECORD_URL, [dict(RECORD_PAGE_DATA), dict(RECORD_PAGE_DATA)])
    client.section_opened = True  # the click itself succeeds
    dispatcher = _dispatch(client)
    state = PermitState(record_key="NULLISLAND/Building/REC26/00000/000QB")
    runner = Phase3RetrievalRunner(
        dispatcher,
        record_ref={"capID1": "REC26", "capID2": "00000", "capID3": "000QB"},
    )
    runner._current_url = RECORD_URL
    outcome = asyncio.run(
        runner.retrieve_missing_sections(
            state, [{"section": "inspections", "reason": "needed", "stop_when": "read"}]
        )
    )
    assert outcome.sections_failed == ["inspections"]
    refused = [action for action in outcome.actions if action.get("refused")]
    assert refused and not refused[0]["ok"]


def test_runner_never_emits_mutation_intents():
    """Every dispatcher call the runner builds must be a read-path action."""
    client = RetrievalFakeClient(RECORD_URL, [dict(RECORD_PAGE_DATA)])
    dispatcher = _dispatch(client)
    state = PermitState(record_key="k")
    runner = Phase3RetrievalRunner(
        dispatcher,
        record_ref={"capID1": "REC26", "capID2": "00000", "capID3": "000QB"},
    )
    asyncio.run(
        runner.retrieve_missing_sections(
            state,
            [
                {"section": "overview", "reason": "r", "stop_when": "s"},
                {"section": "inspections", "reason": "r", "stop_when": "s"},
                {"section": "fees", "reason": "r", "stop_when": "s"},
                {"section": "documents", "reason": "r", "stop_when": "s"},
            ],
        )
    )
    names = {call[0] for call in client.calls}
    assert names <= {"navigate", "click", "read_page"}


def test_run_retrieval_sync_wrapper():
    client = RetrievalFakeClient(RECORD_URL, [dict(RECORD_PAGE_DATA)])
    state = PermitState(record_key="NULLISLAND/Building/REC26/00000/000QB")
    outcome = run_retrieval(
        state,
        [{"section": "overview", "reason": "r", "stop_when": "s"}],
        _dispatch(client),
        record_ref={"capID1": "REC26", "capID2": "00000", "capID3": "000QB"},
    )
    assert outcome.sections_retrieved == ["overview"]
    assert state.record_number == "000000014"


def test_runner_rejects_a_state_keyed_to_a_different_record():
    """The merge guard still applies to retrieval: no cross-record leakage."""
    client = RetrievalFakeClient(RECORD_URL, [dict(RECORD_PAGE_DATA)])
    state = PermitState(record_key="NULLISLAND/Building/REC26/00000/OTHER")
    outcome = run_retrieval(
        state,
        [{"section": "overview", "reason": "r", "stop_when": "s"}],
        _dispatch(client),
        record_ref={"capID1": "REC26", "capID2": "00000", "capID3": "000QB"},
    )
    assert outcome.sections_retrieved == ["overview"]  # read happened
    assert state.record_number is None  # but nothing merged
    assert state.rejected_observations


# --- the renderer: no new claims, no lost distinctions ----------------------


def _result_for(state: PermitState, question: str):
    return understand(state, question, snapshot_id="render-test")


def test_renderer_states_facts_and_classifies_blockers():
    state = extract_partial_state(
        {"record_key": "k", "section": "inspections", "coverage": "complete",
         "rows": [{"type": "Rough Electrical", "status": "Completed", "result": "Corrections Required",
                   "comments": "Enclose exposed junction box."}]}
    )
    result = _result_for(state, "Why is this permit not moving forward?")
    answer = render_answer(result)
    assert "Rough Electrical inspection did not pass" in answer
    assert "observed problem" in answer
    assert "Enclose exposed junction box." in answer
    # a confirmed gate is never asserted from an observed problem
    assert "confirmed gate" not in answer


def test_renderer_never_upgrades_requirement_strength():
    state = extract_partial_state(
        {"record_key": "k", "section": "fees", "coverage": "complete",
         "rows": [{"name": "Permit balance", "amount": "$74.50", "paid": False, "due": True}]}
    )
    result = _result_for(state, "What is blocking approval?")
    answer = render_answer(result)
    assert "potential impediment" in answer
    assert "Required:" not in answer  # no gate evidence -> no required action


def test_renderer_surfaces_contradictions_and_needed_sections():
    state = extract_partial_state(
        {"record_key": "k", "section": "fees", "coverage": "unavailable", "rows": []}
    )
    result = _result_for(state, "Are there unpaid fees?")
    answer = render_answer(result)
    assert "cannot answer this from the evidence gathered so far" in answer.lower()
    assert "fees" in answer


def test_renderer_partial_answer_names_the_missing_section():
    state = extract_partial_state(
        {"record_key": "k", "section": "overview", "coverage": "complete",
         "fields": {"record_number": "P-1", "status": "Submitted"}}
    )
    result = _result_for(state, "Why is approval blocked?")
    answer = render_answer(result)
    assert "partial" in answer.lower()
    assert "conditions" in answer and "history" in answer


# --- integration: adapter -> state -> understand -> render -------------------


def test_flagship_pipeline_over_real_page_shapes():
    """The Phase 3 flagship on adapter-shaped inputs, end to end."""
    key = "NULLISLAND/Building/REC26/00000/9F001"
    overview = accela_extract.overview_observation(
        dict(RECORD_PAGE_DATA, url=RECORD_URL.replace("000QB", "9F001"),
             text="Record\u00a0BLD-GOLD-001: \n Commercial Alteration\nRecord Status: Issued\n")
    )
    inspections = accela_extract.inspections_observation(
        dict(RECORD_PAGE_DATA, url=RECORD_URL.replace("000QB", "9F001"),
             text="Inspections\nRough Electrical | Completed | Corrections Required | Sept 18\n"
                  "Comment: Enclose exposed junction box.")
    )
    state = merge_partial_states(
        extract_partial_state(overview),
        extract_partial_state(inspections),
    )
    result = understand(state, "Why is this permit not moving forward, and what needs to happen next?")
    answer = render_answer(result)
    # facts are stated, execution stays disabled
    assert "Issued" in answer
    assert "Rough Electrical inspection did not pass" in answer
    assert result.execution_allowed is False
