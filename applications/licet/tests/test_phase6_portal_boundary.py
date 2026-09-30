"""phase 6 portal integration lane: the portal mutation boundary map and appointment identity"""
from __future__ import annotations

import asyncio

import pytest

from licet.browser import accela
from licet.browser.solari_client import ToolResult
from licet.safety import risk_levels
from licet.safety.policy import (
    ACTION_RISKS,
    ActionRisk,
    Environment,
    PolicyEngine,
    ProposedAction,
    RecordIdentity,
    UserConstraints,
    normalize_action,
)


def _entries(action: str):
    return [e for e in accela.MUTATION_BOUNDARIES if e["action"] == action]


def test_every_boundary_action_is_a_known_action():
    for entry in accela.MUTATION_BOUNDARIES:
        action = str(entry["action"])
        assert risk_levels.is_known(action), action
        assert ACTION_RISKS.get(normalize_action(action)) is not None, action


def test_boundary_risk_matches_the_risk_catalogue():
    """the map's tiers must be exactly the catalogue's tiers (no drift)"""
    tier_of = {
        "read_only": ActionRisk.READ_ONLY,
        "reversible": ActionRisk.REVERSIBLE,
        "consequential": ActionRisk.CONSEQUENTIAL,
        "prohibited": ActionRisk.PROHIBITED,
    }
    for entry in accela.MUTATION_BOUNDARIES:
        assert tier_of[str(entry["risk"])] is ACTION_RISKS[normalize_action(str(entry["action"]))]


@pytest.mark.parametrize(
    "action",
    ["schedule_inspection", "cancel_inspection", "enter_payment_details",
     "accept_legal_attestation", "submit_application", "upload_document"],
)
def test_mutating_boundaries_are_state_changing_actions(action):
    """the guard's environment rule keys on state_changing_actions: every map entry that mutates must be in it, so a live host refuses it too"""
    entries = [e for e in _entries(action) if e["mutates"]]
    assert entries, action
    assert risk_levels.changes_state(action)


def test_scheduling_wizard_navigation_does_not_mutate():
    """the invariant architecture review asked for: reaching the gate cannot book"""
    mutating = [e for e in accela.MUTATION_BOUNDARIES
                if e["flow"] == "schedule_inspection" and e["mutates"]]
    assert len(mutating) == 1
    assert mutating[0]["step"] == "confirm"
    assert mutating[0]["commit"] is True


def test_attestation_is_prohibited_and_payments_consequential():
    assert _entries("accept_legal_attestation")[0]["risk"] == "prohibited"
    assert _entries("enter_payment_details")[0]["risk"] == "consequential"
    assert risk_levels.classify("accept_legal_attestation") is risk_levels.RiskLevel.PROHIBITED


def test_unmapped_entries_declare_no_control():
    """an unmapped flow must not pretend to a control id the sandbox never rendered that honesty is what the guard's fail closed answer relies on"""
    for entry in accela.MUTATION_BOUNDARIES:
        if "UNMAPPED" in str(entry["evidence"]):
            assert entry["control"] is None, entry
            assert "UNMAPPED" in str(entry["evidence"])


def test_scheduling_commit_control_is_the_popup_continue():
    entry = _entries("schedule_inspection")[0]
    assert entry["control"] == accela.POPUP_CONTINUE_ID


def _engine(env: Environment) -> PolicyEngine:
    return PolicyEngine(environment=env, constraints=UserConstraints())


def test_map_and_engine_agree_on_scheduling_in_sandbox():
    engine = _engine(Environment.SANDBOX)
    decision = engine.decide(ProposedAction("SCHEDULE_INSPECTION", permit_id="P-1", target="X"),
                             verify_record=False)
    assert decision.allowed


def test_map_and_engine_agree_on_live_and_attestation():
    live = _engine(Environment.LIVE_READ_ONLY)
    assert not live.decide(ProposedAction("SCHEDULE_INSPECTION", permit_id="P-1", target="X"),
                           verify_record=False).allowed
    sandbox = _engine(Environment.SANDBOX)
    att = sandbox.decide(ProposedAction("ACCEPT_LEGAL_ATTESTATION", permit_id="P-1", target="X"),
                         verify_record=False)
    assert not att.allowed and att.reason == "PROHIBITED_ACTION"


def test_parse_row_controls_postback_and_anchor_idioms():
    html = (
        "<table><tr>"
        "<td>Rough Electrical</td><td>Scheduled</td><td>09/25/2026</td>"
        "<td><a id=\"ctl00_phPopup_gvInspections_ctl02_lnkCancel\" "
        "href=\"javascript:__doPostBack('ctl00$phPopup$gvInspections$ctl02$lnkCancel','')\">Cancel</a></td>"
        "</tr></table>"
    )
    controls = accela.parse_inspection_row_controls(html)
    assert len(controls) == 1
    assert controls[0]["verb"] == "cancel"
    assert controls[0]["key"] == "ctl00$phPopup$gvInspections$ctl02$lnkCancel"
    assert "lnkCancel" in controls[0]["control_id"]


def test_parse_row_controls_reschedule_and_edit():
    html = (
        "<a id='a_lnkReschedule' href=\"javascript:__doPostBack('a$r$lnkReschedule','')\">Reschedule</a>"
        "<a id='b_lnkEdit' href='#'>Edit</a>"
    )
    verbs = {c["verb"] for c in accela.parse_inspection_row_controls(html)}
    assert verbs == {"reschedule", "edit"}


def test_parse_row_controls_ignores_read_only_cancel_links():
    html = (
        "<a href='/help/cancellation-policy'>Cancellation Policy</a>"
        "<a href='/fees'>View Fees</a>"
        "<a id='x_lnkCancel' href=\"javascript:__doPostBack('x$lnkCancel','')\">Cancel</a>"
    )
    controls = accela.parse_inspection_row_controls(html)
    assert len(controls) == 1 and controls[0]["verb"] == "cancel"


def test_parse_row_controls_empty_html_is_no_controls():
    assert accela.parse_inspection_row_controls("") == []
    assert accela.parse_inspection_row_controls(None) == []


class _FakePage:
    url = (
        "https://aca-test.accela.com/NULLISLAND/Cap/CapDetail.aspx?Module=Building"
        "&TabName=Building&capID1=REC26&capID2=00000&capID3=000QA"
        "&agencyCode=NULLISLAND&IsToShowInspection="
    )

    async def content(self):
        return ""

    async def inner_text(self):
        return ""


class _RowControlClient:
    """read_page double that ships `inspection_row_controls` like the real one"""

    def __init__(self, text, controls):
        self.page = _FakePage()
        self._text = text
        self._controls = controls

    async def read_page(self, *, include=None, max_text=4000):
        return ToolResult(ok=True, url=self.page.url, data={
            "url": self.page.url,
            "text": self._text,
            "fields": [],
            "inspection_types": [],
            "inspection_row_controls": list(self._controls),
            "loading": [],
            "validation_errors": [],
            "frames": [],
        })

    async def click(self, target):
        return ToolResult(ok=True, url=self.page.url, data={})

    async def navigate(self, url):
        self.page.url = url
        return ToolResult(ok=True, url=url, data={})

    async def wait_for_text(self, **kwargs):
        return ToolResult(ok=True, url=self.page.url, data={})


def _detail_text(rows):
    header = "Record BLD26-00467:\n Building/Sign/Temporary/NA\nRecord Status: Issued"
    return header + "\nInspections\n" + "\n".join(rows)


def _portal(client):
    from licet.phase4.accela_portal import AccelaInspectionPortal
    from licet.browser.dispatcher import ToolDispatcher
    return AccelaInspectionPortal(ToolDispatcher(client))


def _read(portal, permit="BLD26-00467", type_=None, id_=None):
    return asyncio.run(portal.read_inspection_state_async(permit, type_, id_))


CANCEL = {"control_id": "ctl02_lnkCancel",
          "verb": "cancel", "key": "gv$ctl02$lnkCancel"}


def test_adapter_binds_appointment_id_from_row_control():
    text = _detail_text(["Rough Electrical | Scheduled | 09/25/2026"])
    portal = _portal(_RowControlClient(text, [CANCEL]))
    snap = _read(portal, type_="Rough Electrical")
    assert snap.inspection_id == "gv$ctl02$lnkCancel"
    assert snap.status == "Scheduled"
    assert snap.record_key


def test_adapter_refuses_to_bind_on_ambiguous_reads():
    text = _detail_text([
        "Rough Electrical | Scheduled | 09/25/2026",
        "Final Electrical | Scheduled | 09/28/2026",
    ])
    portal = _portal(_RowControlClient(text, [CANCEL]))
    snap = _read(portal, type_="Rough Electrical")
    assert snap.inspection_id is None


def test_adapter_refuses_to_bind_without_controls():
    """no html controls parsed → no id → targeted mutation would be refused by policy (target_inspection_unidentified)"""
    text = _detail_text(["Rough Electrical | Scheduled | 09/25/2026"])
    portal = _portal(_RowControlClient(text, []))
    snap = _read(portal, type_="Rough Electrical")
    assert snap.inspection_id is None


def test_engine_refuses_targeted_mutation_without_appointment_id():
    """the end to end reason the parser exists: the real engine stops an id less cancel even in a sandbox with every other condition satisfied"""
    engine = _engine(Environment.SANDBOX)
    decision = engine.decide(ProposedAction(
        "CANCEL_INSPECTION", permit_id="BLD26-00467", target="Rough Electrical",
        inspection_type="Rough Electrical", inspection_id=None,
    ))
    assert not decision.allowed
    assert decision.reason == "TARGET_INSPECTION_UNIDENTIFIED"


def test_engine_allows_targeted_mutation_with_the_bound_id():
    engine = _engine(Environment.SANDBOX)
    action = ProposedAction(
        "CANCEL_INSPECTION", permit_id="BLD26-00467", target="Rough Electrical",
        inspection_type="Rough Electrical", inspection_id="gv$ctl02$lnkCancel",
    )
    # the observed identity is the adapter's snapshot: it asserts the same appointment id the read bound,
    # so the approval scope and the observation check the same target (the p2 fail closed rule)
    decision = engine.decide(action, observed_identity=RecordIdentity(
        permit_id="BLD26-00467", inspection_type="Rough Electrical",
        inspection_id="gv$ctl02$lnkCancel",
    ))
    # consequential tier: not auto allowed; it must demand a scoped confirmation
    assert decision.requires_confirmation
    assert decision.confirmation is not None
    assert decision.confirmation.inspection_id == "gv$ctl02$lnkCancel"
