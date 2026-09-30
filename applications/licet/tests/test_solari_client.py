"""client tests driven by a fake page no live solari session needed"""

from __future__ import annotations

import asyncio
from typing import Any

from licet.browser import accela
from licet.browser.errors import BrowserError
from licet.browser.solari_client import SolariClient, Target

CAPEDIT_DETAIL_URL = (
    f"{accela.PORTAL_ROOT}/Cap/CapEdit.aspx?module=Building&stepNumber=3&pageNumber=3"
)

ROW_USE_FIELDS = """
<span class="ACA_Label font12px"><label
  for="ctl00_PlaceHolderMain_AppSpec5109C0E9Edit_NULLISLAND_txt_3_0"
  class="ACA_Error_Label">Schedule Start Date: </label></span>
<input name="ctl00$PlaceHolderMain$AppSpec5109C0E9Edit$NULLISLAND_txt_3_0"
  type="text" id="ctl00_PlaceHolderMain_AppSpec5109C0E9Edit_NULLISLAND_txt_3_0"
  title="Required" class="ACA_NLonger maskedfields HighlightCssClass MaskedEditError"
  aria-required="true" aria-label="MM/DD/YYYY" placeholder="MM/DD/YYYY"
  fieldname="Schedule Start Date">
"""

ROW_USE_ERROR_PANEL = """
<a href="javascript:void(0)" onclick="myValidationErrorPanel.skipTo(
   'ctl00_PlaceHolderMain_AppSpec5109C0E9Edit_NULLISLAND_txt_3_0',false)"
   class="ACA_Message_Error_Link">1.Schedule Start Date: Required Enter as MM/dd/yyyy</a>
"""


class FakeKeyboard:
    def __init__(self, page: "FakePage") -> None:
        self.page = page

    async def type(self, text: str, delay: int | None = None) -> None:
        self.page.typed.append(text)
        if self.page.on_type is not None:
            self.page.on_type(text)


class FakeLocator:
    """`config is none` means \"no such element\"; `{}` means present by default"""

    def __init__(self, page: "FakePage", selector: str, config: dict[str, Any] | None) -> None:
        self.page = page
        self.selector = selector
        self.config = config
        self.values = (config or {}).get("value_sequence", [])
        self.value = (config or {}).get("value", "")

    @property
    def first(self) -> "FakeLocator":
        return self

    def filter(self, *, visible):
        config = dict(self.config or {})
        config["count"] = config.get("visible_count", config.get("count", 1)) if config.get("visible", True) else 0
        return FakeLocator(self.page, self.selector, config)

    async def count(self) -> int:
        return 0 if self.config is None else int(self.config.get("count", 1))

    async def is_visible(self) -> bool:
        if self.config is None:
            return False
        return bool(self.config.get("visible", True))

    async def click(self, timeout: int | None = None, force: bool = False) -> None:
        self.page.clicks.append({"selector": self.selector, "force": force})
        config = self.config or {}
        effect = config.get("effect")
        if effect:
            effect(self.page)
        error = config.get("click_error")
        # a force click is the fallback that usually works; it only fails when the test says the element
        # is genuinely unusable
        if error and (not force or config.get("force_fails", False)):
            raise RuntimeError(error)
        navigates_to = config.get("navigates_to")
        if navigates_to:
            self.page.url = navigates_to
            self.page.html = self.page.pages.get(navigates_to, self.page.html)

    async def fill(self, value: str, timeout: int | None = None) -> None:
        self.page.fills.append({"selector": self.selector, "value": value})
        self.value = value
        if self.config is not None:
            self.config["value"] = value
        if (self.config or {}).get("fill_error"):
            raise RuntimeError(self.config["fill_error"])

    async def input_value(self) -> str:
        if self.values:
            value = self.values.pop(0)
            if self.config is not None:
                self.config["_last_readback"] = value
            return value
        if self.config is not None and "_last_readback" in self.config:
            return self.config["_last_readback"]
        return self.value

    async def press(self, key: str) -> None:
        self.page.presses.append(key)

    async def focus(self) -> None:
        self.page.focused.append(self.selector)

    async def get_attribute(self, name: str) -> str | None:
        return ((self.config or {}).get("attrs") or {}).get(name)

    async def inner_text(self) -> str:
        return (self.config or {}).get("text", "")

    async def evaluate(self, script):
        return (self.config or {}).get("options", [
            {"label": "Search by Address", "value": "addr", "disabled": False}])

    async def select_option(
        self, label: str | None = None, value: str | None = None, timeout: int | None = None
    ) -> None:
        config = self.config or {}
        options = await self.evaluate("")
        selected = next(o for o in options if (o["label"] == label if label is not None else o["value"] == value))
        self.value = selected["value"]
        self.config["value"] = self.value
        self.page.selects.append({"selector": self.selector, "label": label, "value": value})
        if config.get("select_effect"):
            config["select_effect"](self.page)
        if config.get("select_error"):
            raise RuntimeError(config["select_error"])


class FakeFrame:
    def __init__(
        self,
        url: str = "",
        html: str = "",
        locators: dict[str, Any] | None = None,
        text: str = "",
        title: str = "Fake",
        text_sequence: list[str] | None = None,
    ) -> None:
        self.url = url
        self.html = html
        self._locators = dict(locators or {})
        self._text = text
        self._title = title
        self._text_sequence = list(text_sequence or [])
        self.page: FakePage | None = None

    async def content(self) -> str:
        return self.html

    async def title(self) -> str:
        return self._title

    def locator(self, selector: str) -> FakeLocator:
        if selector == "body":
            text = self._text
            if self._text_sequence:
                text = (
                    self._text_sequence.pop(0)
                    if len(self._text_sequence) > 1
                    else self._text_sequence[0]
                )
            return FakeLocator(self.page, selector, {"text": text})
        return FakeLocator(self.page, selector, self._locators.get(selector))

    async def evaluate(self, script: str):
        if 'const controls =' in script:
            snapshot_error = getattr(self, "snapshot_error", False)
            if snapshot_error:
                message = snapshot_error if isinstance(snapshot_error, str) else "snapshot unavailable"
                raise RuntimeError(message)
            return {"text": self._text, "controls": [
                [key, config.get("value", ""), config.get("checked", False)]
                for key, config in self._locators.items()], "sections": [],
                "busy": getattr(self, "busy", False),
                "document_id": getattr(self, "document_id", 0),
                "postbacks_completed": getattr(self, "postbacks_completed", 0)}
        if self.page is not None:
            self.page.injected.append(script)


class FakePage(FakeFrame):
    def __init__(
        self,
        url: str = "about:blank",
        html: str = "",
        locators: dict[str, Any] | None = None,
        frames: tuple[FakeFrame, ...] = (),
        text: str = "",
        pages: dict[str, str] | None = None,
        text_sequence: list[str] | None = None,
    ) -> None:
        super().__init__(
            url=url, html=html, locators=locators, text=text, text_sequence=text_sequence
        )
        self.page = self
        self._frames = list(frames)
        for frame in self._frames:
            frame.page = self
        self.keyboard = FakeKeyboard(self)
        self.on_type = None
        self.pages = pages or {}
        self.clicks: list[dict[str, Any]] = []
        self.fills: list[dict[str, Any]] = []
        self.typed: list[str] = []
        self.presses: list[str] = []
        self.focused: list[str] = []
        self.injected: list[str] = []
        self.selects: list[dict[str, Any]] = []
        self.gotos: list[str] = []

    @property
    def frames(self) -> list[FakeFrame]:
        return [self, *self._frames]

    async def goto(self, url: str, timeout: int | None = None, wait_until: str | None = None) -> None:
        self.gotos.append(url)
        self.url = url
        if url in self.pages:
            self.html = self.pages[url]

    async def wait_for_load_state(self, state: str = "load") -> None:
        return None

    async def wait_for_timeout(self, milliseconds: float) -> None:
        return None

    async def evaluate(self, script: str):
        return await super().evaluate(script)

    async def screenshot(self, path: str | None = None, **kwargs: Any) -> None:
        return None


def _client(page: FakePage) -> SolariClient:
    return SolariClient(page, settle_ms=0, verification_timeout_ms=200, verification_poll_ms=1)


def test_click_timeout_is_not_force_clicked_or_retried():
    page = FakePage(locators={"#go": {"click_error": "Timeout waiting for locator"}})
    result = asyncio.run(_client(page).click(Target(selector="#go")))
    assert not result.ok
    assert result.error.kind is BrowserError.ACTION_OUTCOME_UNKNOWN
    assert not result.error.retryable
    assert page.clicks == [{"selector": "#go", "force": False}]


def test_click_detachment_is_not_assumed_safe_to_retry():
    page = FakePage(locators={"#go": {"click_error": "element was detached from the DOM"}})
    result = asyncio.run(_client(page).click(Target(selector="#go")))
    assert not result.ok
    assert result.error.kind is BrowserError.ACTION_OUTCOME_UNKNOWN
    assert len(page.clicks) == result.attempts == 1


def test_click_reports_not_found_without_retrying():
    result = asyncio.run(_client(FakePage()).click(Target(selector="#nope")))
    assert not result.ok
    assert result.error.kind is BrowserError.NOT_FOUND


def test_hidden_match_is_not_actionable_not_missing():
    """aca renders dead nav items and section views hide links that exist"""
    page = FakePage(locators={"a:has-text('Attachments')": {"visible": False}})
    result = asyncio.run(_client(page).click(Target(text="Attachments")))

    assert not result.ok
    assert result.error.kind is BrowserError.NOT_ACTIONABLE
    assert "present but not visible" in result.error.message
    assert "a:has-text('Attachments')" in result.error.message
    # the selectors that simply did not exist are reported separately
    assert "no match" in result.error.message


def test_missing_element_error_is_not_retryable():
    result = asyncio.run(_client(FakePage()).click(Target(selector="#nope")))
    assert not result.error.retryable


def test_semantic_target_escapes_apostrophes_in_accela_labels():
    page = FakePage(
        locators={
            "a:text-is('Owner\\'s Record')": {"effect": lambda p: setattr(p, "_text", "Record details")},
        }
    )
    result = asyncio.run(_client(page).click(Target(text="Owner's Record")))
    assert result.ok, result.as_dict()


def test_explicit_frame_marker_does_not_fall_back_to_the_parent_page():
    page = FakePage(locators={"input[name='username']": {}})
    result = asyncio.run(
        _client(page).click(Target(selector="input[name='username']", frame="login-panel"))
    )
    assert not result.ok
    assert result.error.kind is BrowserError.NOT_FOUND
    assert "frame containing" in result.error.message


def test_multiple_matches_are_reported_as_ambiguous_instead_of_clicking_first():
    page = FakePage(locators={"a:text-is('Search')": {"count": 2}})
    result = asyncio.run(_client(page).click(Target(text="Search")))
    assert not result.ok
    assert result.error.kind is BrowserError.AMBIGUOUS_TARGET
    assert page.clicks == []


def test_disabled_scheduling_continue_is_never_force_clicked():
    html = (
        '<a id="ctl00_phPopup_lnkContinue" disabled="disabled" '
        'href_disabled="javascript:__doPostBack(1)" class="ButtonDisabled">Continue</a>'
    )
    page = FakePage(
        html=html,
        locators={
            "#ctl00_phPopup_lnkContinue": {
                "click_error": "Timeout waiting for locator",
            }
        },
    )
    result = asyncio.run(
        _client(page).click(Target(selector="ctl00_phPopup_lnkContinue"))
    )
    assert not result.ok
    assert result.error.kind is BrowserError.NOT_ACTIONABLE
    assert page.clicks == []


def test_masked_field_uses_keystrokes_not_fill():
    page = FakePage(
        locators={
            "zip": {
                "attrs": {"class": "ACA_NLonger maskedfields"},
                "value": "09/01/2026",
            }
        }
    )
    result = asyncio.run(_client(page).type_text(Target(selector="zip"), "09012026"))

    assert result.ok and result.data["masked"] is True
    assert page.typed == ["09012026"]
    assert page.fills == []
    assert page.presses == ["ControlOrMeta+A", "Delete"]


def test_masked_readback_mismatch_fails_without_appending_more_text():
    page = FakePage(locators={"zip": {"attrs": {"class": "maskedfields"}, "value": "123"}})
    result = asyncio.run(_client(page).type_text(Target(selector="zip"), "09012026"))
    assert not result.ok
    assert page.typed == ["09012026"]


def test_plain_field_readback_mismatch_does_not_report_success():
    page = FakePage(locators={"name": {"value_sequence": ["wrong"] * 100}})
    result = asyncio.run(_client(page).type_text(Target(selector="name"), "Licet Eval"))
    assert not result.ok
    assert page.typed == []


def test_select_reports_auto_postback():
    page = FakePage(locators={"ddl": {}})
    result = asyncio.run(_client(page).select(Target(selector="ddl"), "Search by Address"))

    assert result.ok and result.data["postback"] is False
    assert page.selects[0]["label"] == "Search by Address"
    assert result.data["selected_value"] == "addr"
    assert result.data["verification"]["status"] == "verified"


def test_select_falls_back_to_value_when_label_is_unavailable():
    page = FakePage(locators={"ddl": {"labels_supported": False}})
    result = asyncio.run(_client(page).select(Target(selector="ddl"), "addr"))

    assert result.ok
    assert page.selects[0] == {"selector": "ddl", "label": None, "value": "addr"}


def test_select_fails_when_readback_is_empty():
    # select_option() can succeed (no exception) on a disabled/no op aca option and still leave the
    # control blank that must not read as success
    page = FakePage(locators={"ddl": {"value_sequence": [""] * 100}})
    result = asyncio.run(_client(page).select(Target(selector="ddl"), "Search by Address"))

    assert result.ok is False
    assert result.error.kind is BrowserError.ACTION_OUTCOME_UNKNOWN
    assert accela.MASK_NEUTRALIZER_JS not in page.injected


def test_read_page_reports_flow_fields_validation_and_notices():
    page = FakePage(
        url=CAPEDIT_DETAIL_URL,
        html=ROW_USE_FIELDS + ROW_USE_ERROR_PANEL,
        text="Please login to continue",
    )
    data = asyncio.run(_client(page).read_page()).data

    assert data["flow"] == {"flow": "apply_application", "step": "detail", "page": 3}
    assert data["notices"] == ["please login to continue"]
    field = next(f for f in data["fields"] if f["label"] == "Schedule Start Date")
    assert field["required"] is True and field["masked"] is True
    assert data["validation_errors"][0]["control_id"].endswith("txt_3_0")
    assert data["popup_open"] is False


def test_read_page_flags_popup_and_login_frames():
    popup = FakeFrame(
        url=f"{accela.PORTAL_ROOT}/Cap/People/ContactAddNew.aspx",
        html='<input id="ctl00_phPopup_btnSave" type="button">',
    )
    page = FakePage(url=CAPEDIT_DETAIL_URL, html="<html></html>", frames=(popup,))
    data = asyncio.run(_client(page).read_page()).data

    assert data["popup_open"] is True
    assert any(frame["popup"] for frame in data["frames"])


def test_read_page_flags_a_half_rendered_section():
    """aca sections load over ajax; a mid load read claims there is nothing"""
    page = FakePage(text="Inspections | Loading... | Post")
    data = asyncio.run(_client(page).read_page()).data
    assert data["loading"] == ["loading..."]


def test_wait_for_text_polls_until_content_finishes_loading():
    page = FakePage(
        text_sequence=[
            "Inspections | Loading...",
            "Inspections | Loading...",
            "Inspections | Upcoming | Schedule or Request an Inspection",
        ]
    )
    result = asyncio.run(
        _client(page).wait_for_text(absent="Loading...", poll_ms=10)
    )
    assert result.ok
    assert result.data["waited_ms"] > 0


def test_wait_for_text_times_out_when_content_never_arrives():
    page = FakePage(text="Inspections | Loading...")
    result = asyncio.run(
        _client(page).wait_for_text(absent="Loading...", timeout_ms=30, poll_ms=10)
    )
    assert not result.ok
    assert result.error.kind is BrowserError.TIMEOUT


def test_read_page_rejects_unknown_includes():
    result = asyncio.run(_client(FakePage()).read_page(include=["telepathy"]))
    assert not result.ok
    assert "telepathy" in result.error.message


def test_read_page_truncates_large_bodies():
    page = FakePage(text="x" * 9000)
    data = asyncio.run(_client(page).read_page()).data
    assert data["truncated"] is True
    assert len(data["text"]) == 4000


def test_navigate_recognises_the_aca_error_page():
    page = FakePage(
        html=(
            "<div>An error has occurred. The file "
            "'/nullisland/NULLISLAND/Cap/CapDetail.aspx' does not exist.</div>"
        )
    )
    result = asyncio.run(
        _client(page).navigate(
            "https://aca-test.accela.com/nullisland/NULLISLAND/Cap/CapDetail.aspx"
        )
    )

    assert not result.ok
    assert result.error.kind is BrowserError.PORTAL_ERROR


def test_navigate_rejects_a_plain_accela_missing_file_page():
    page = FakePage(html="<div>The file '/missing.aspx' does not exist.</div>")
    result = asyncio.run(_client(page).navigate("https://aca-test.accela.com/missing.aspx"))
    assert not result.ok
    assert result.error.kind is BrowserError.PORTAL_ERROR


def test_navigate_surfaces_a_login_notice_without_a_redirect():
    page = FakePage(text="Please login to continue")
    result = asyncio.run(_client(page).navigate(accela.MY_RECORDS_URL))
    assert not result.ok
    assert result.error.kind is BrowserError.AUTH_REQUIRED
    assert result.data["notices"] == ["please login to continue"]


def test_navigate_settles_and_neutralizes_the_mask():
    page = FakePage(pages={accela.MY_RECORDS_URL: "<html>My Records</html>"})
    result = asyncio.run(_client(page).navigate(accela.MY_RECORDS_URL))

    assert result.ok
    assert page.gotos == [accela.MY_RECORDS_URL]
    assert accela.MASK_NEUTRALIZER_JS in page.injected


def test_login_operates_the_sso_iframe_without_echoing_credentials():
    dashboard = f"{accela.SITE_ROOT}/NULLISLAND/Dashboard.aspx"
    panel = FakeFrame(
        url=f"{accela.PORTAL_ROOT}/AngularUI/CommunityView/login-panel?inLegacyUI=true",
        html="<form></form>",
        locators={
            "input[name='username']": {},
            "input[name='password']": {},
            "button": {"navigates_to": dashboard},
        },
    )
    page = FakePage(
        url="about:blank",
        html="<html>login</html>",
        frames=(panel,),
        pages={accela.LOGIN_URL: "<html>login</html>"},
    )
    result = asyncio.run(_client(page).login("tester@example.test", "hunter2"))

    assert result.ok
    assert result.data["authenticated"] is True
    assert "hunter2" not in str(result.as_dict())  # credentials never echoed


def test_bare_control_id_resolves_as_an_id_selector():
    candidates = Target(selector="ctl00_phPopup_gvInspectionType_ctl08_rdInspectionType").candidates()

    assert candidates[0] == "#ctl00_phPopup_gvInspectionType_ctl08_rdInspectionType"
    assert '[id="ctl00_phPopup_gvInspectionType_ctl08_rdInspectionType"]' in candidates


def test_real_css_selectors_are_left_alone():
    candidates = Target(selector="#btnSearch").candidates()

    assert candidates == ["#btnSearch"]


def test_css_selector_with_combinators_is_not_treated_as_an_id():
    assert Target(selector="div#x > a").candidates() == ["div#x > a"]


def test_page_text_refines_flow_position_on_read_page():
    """the wizard's steps share one url; the visible text is the signal"""

    async def run():
        page = FakePage(
            url=(
                "https://aca-test.accela.com/NULLISLAND/Cap/CapDetail.aspx"
                "?Module=Building&capID1=REC26&capID2=00000&capID3=000QC"
                "&agencyCode=NULLISLAND&IsToShowInspection=yes"
            ),
            text="Available Inspection Types (13) | Sound (optional) | Continue | Cancel",
        )
        client = SolariClient(page)
        return await client.read_page()

    result = asyncio.run(run())

    assert result.data["flow"] == {"flow": "schedule_inspection", "step": "select_type", "page": None}


# the live planner run on 2026 09 20 authenticated fine and still reported `authenticated=false`, because
# the sso postback leaves the url on login.aspx for a moment


class _DelayedRedirectPage(FakePage):
    """a page whose url only changes after the postback finishes settling"""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.pending_url: str | None = None
        self.waits = 0

    async def wait_for_timeout(self, milliseconds: float) -> None:
        self.waits += 1
        if self.pending_url and self.waits >= 2:
            self.url = self.pending_url
            self.pending_url = None


def _login_frame() -> FakeFrame:
    return FakeFrame(
        url=f"{accela.PORTAL_ROOT}/AngularUI/CommunityView/login-panel?inLegacyUI=true",
        html="<form></form>",
        locators={
            "input[name='username']": {},
            "input[name='password']": {},
            "button": {},  # no navigates_to: the redirect is delayed, not instant
        },
    )


def test_login_waits_for_a_delayed_redirect_instead_of_calling_it_a_failure():
    dashboard = f"{accela.SITE_ROOT}/NULLISLAND/Dashboard.aspx"
    page = _DelayedRedirectPage(
        url="about:blank",
        html="<html>login</html>",
        frames=(_login_frame(),),
        pages={accela.LOGIN_URL: "<html>login</html>"},
    )
    page.pending_url = dashboard

    result = asyncio.run(_client(page).login("tester@example.test", "hunter2"))

    assert page.waits >= 2
    assert result.data["authenticated"] is True
    assert result.data["landed_on"] == dashboard


def test_login_reports_failure_when_the_page_never_leaves_the_login_form():
    page = FakePage(
        url="about:blank",
        html="<html>login</html>",
        frames=(_login_frame(),),
        pages={accela.LOGIN_URL: "<html>login</html>"},
        text="Please sign in with your CivicID account",
    )
    result = asyncio.run(_client(page).login("tester@example.test", "wrong-password"))

    assert result.data["authenticated"] is False
    assert "login" in result.data["landed_on"].lower()
    assert "wrong-password" not in str(result.as_dict())


def test_login_accepts_a_signed_in_page_even_on_an_odd_url():
    """some agencies keep a login.aspx url while rendering the dashboard"""
    page = FakePage(
        url="about:blank",
        html="<html>login</html>",
        frames=(_login_frame(),),
        pages={accela.LOGIN_URL: "<html>login</html>"},
        text="Dashboard | My Records | Sign Out",
    )
    result = asyncio.run(_client(page).login("tester@example.test", "hunter2"))
    assert result.data["authenticated"] is True


def test_click_that_changes_state_before_timeout_is_verified_without_replay():
    page = FakePage(locators={"#go": {
        "effect": lambda p: setattr(p, "_text", "Record details"),
        "click_error": "Timeout waiting for navigation"}})
    result = asyncio.run(_client(page).click(Target(selector="#go")))
    assert result.ok
    assert result.data["recovered_after_error"]
    assert len(page.clicks) == 1


def test_successful_provider_click_with_no_change_is_unverified():
    page = FakePage(locators={"#go": {}})
    result = asyncio.run(_client(page).click(Target(selector="#go")))
    assert not result.ok
    assert len(page.clicks) == 1


def test_click_verifies_checked_state_without_text_change():
    page = FakePage(locators={"#go": {
        "effect": lambda p: p._locators["#go"].update(checked=True)}})
    assert asyncio.run(_client(page).click(Target(selector="#go"))).ok


def test_loading_transition_does_not_count_as_verified():
    def effect(page):
        page._text = "Loading..."
        page.busy = True
    page = FakePage(locators={"#go": {"effect": effect}})
    assert not asyncio.run(_client(page).click(Target(selector="#go"))).ok


def test_read_failure_after_dispatch_is_uncertain_not_replayed():
    page = FakePage(locators={"#go": {
        "effect": lambda p: setattr(p, "snapshot_error", True)}})
    result = asyncio.run(_client(page).click(Target(selector="#go")))
    assert not result.ok
    assert len(page.clicks) == 1
    verification = result.data["verification"]
    assert verification["observation_error"] == "snapshot unavailable"
    assert verification["observation_error_type"] == "RuntimeError"
    assert verification["observation_timed_out"] is True
    assert verification["observation_attempts"] > 0
    assert verification["elapsed_ms"] > 0


def test_verification_diagnostic_redacts_secrets_and_records_observer_state():
    page = FakePage(locators={"#go": {
        "effect": lambda p: setattr(
            p, "snapshot_error", "failed https://example.test/?token=private api_key=abc123"
        )}})
    result = asyncio.run(_client(page).click(Target(selector="#go")))
    verification = result.data["verification"]

    assert not result.ok
    assert "https://" not in verification["observation_error"]
    assert "private" not in verification["observation_error"]
    assert "abc123" not in verification["observation_error"]
    assert "[redacted]" in verification["observation_error"]
    assert verification["last_matched"] is False
    assert verification["stable_samples"] == 0
    assert verification["observation_attempts"] > 0


def test_select_timeout_after_change_is_not_replayed_as_value_fallback():
    page = FakePage(locators={"ddl": {"select_error": "Timeout waiting for navigation"}})
    result = asyncio.run(_client(page).select(Target(selector="ddl"), "Search by Address"))
    assert result.ok
    assert result.data["recovered_after_error"]
    assert len(page.selects) == 1


def test_select_rechecks_replacement_control_after_postback():
    page = FakePage(locators={"ddl": {"select_effect": lambda p:
        p._locators.update(ddl={"value": "wrong"})}})
    result = asyncio.run(_client(page).select(Target(selector="ddl"), "Search by Address"))
    assert not result.ok
    assert result.data["selected_value"] == "wrong"
    assert len(page.selects) == 1


def test_disabled_option_is_rejected_before_dispatch():
    page = FakePage(locators={"ddl": {"options": [
        {"label": "Closed", "value": "closed", "disabled": True}]}})
    result = asyncio.run(_client(page).select(Target(selector="ddl"), "Closed"))
    assert not result.ok
    assert page.selects == []


def test_fill_timeout_is_reconciled_by_readback():
    page = FakePage(locators={"name": {"fill_error": "Timeout"}})
    result = asyncio.run(_client(page).type_text(Target(selector="name"), "Licet"))
    assert result.ok and result.data["recovered_after_error"]
    assert len(page.fills) == 1


def test_empty_input_value_is_a_valid_expected_value():
    page = FakePage(locators={"name": {"value": "existing"}})
    assert asyncio.run(_client(page).type_text(Target(selector="name"), "")).ok


def test_pre_action_snapshot_failure_does_not_dispatch_click():
    page = FakePage(locators={"#go": {}})
    page.snapshot_error = True
    result = asyncio.run(_client(page).click(Target(selector="#go")))
    assert not result.ok
    assert page.clicks == []


def test_verification_deadline_also_bounds_a_hung_observation():
    class HungPage(FakePage):
        async def evaluate(self, script):
            if 'const controls =' in script and self.clicks:
                await asyncio.sleep(10)
            return await super().evaluate(script)

    page = HungPage(locators={"#go": {}})

    async def run():
        return await asyncio.wait_for(_client(page).click(Target(selector="#go")), 0.5)

    result = asyncio.run(run())
    assert not result.ok
    assert result.error.kind is BrowserError.ACTION_OUTCOME_UNKNOWN
    assert len(page.clicks) == 1


def test_click_waits_for_delayed_transition_without_replaying():
    """the transition lands on a later observation, not after a wall clock delay"""

    class LateTransitionPage(FakePage):
        def __init__(self, **kwargs) -> None:
            super().__init__(**kwargs)
            self.observations = 0

        async def evaluate(self, script, **kwargs):
            result = await super().evaluate(script, **kwargs)
            if 'const controls =' in script:
                self.observations += 1
                if self.observations >= 2:
                    self._text = "New section"
            return result

    page = LateTransitionPage(locators={"#go": {"click_error": "Timeout"}})
    result = asyncio.run(_client(page).click(Target(selector="#go")))
    assert result.ok
    assert len(page.clicks) == 1


def test_click_observes_transition_inside_iframe():
    frame = FakeFrame(url="https://example.test/popup", locators={"#go": {}})
    page = FakePage(frames=(frame,))
    frame._locators["#go"]["effect"] = lambda p: setattr(frame, "_text", "Saved")
    result = asyncio.run(_client(page).click(Target(selector="#go", frame="popup")))
    assert result.ok


def test_select_allows_explicit_option_with_empty_value():
    page = FakePage(locators={"ddl": {"options": [
        {"label": "None", "value": "", "disabled": False}]}})
    result = asyncio.run(_client(page).select(Target(selector="ddl"), "None"))
    assert result.ok
    assert result.data["selected_value"] == ""


def test_poll_interval_cannot_extend_verification_deadline():
    async def run():
        client = SolariClient(FakePage(locators={"#go": {}}),
                              verification_timeout_ms=20, verification_poll_ms=10000)
        return await asyncio.wait_for(client.click(Target(selector="#go")), 0.5)
    assert not asyncio.run(run()).ok


def test_semantic_click_uses_exact_title_before_ambiguous_partial_text():
    page = FakePage(locators={
        "a[title='Search']": {"effect": lambda p: setattr(p, "_text", "Search result")},
        "a:has-text('Search')": {"count": 4},
    })
    result = asyncio.run(_client(page).click(Target(text="Search")))
    assert result.ok
    assert page.clicks == [{"selector": "a[title='Search']", "force": False}]


def test_postback_select_does_not_succeed_on_value_change_alone():
    page = FakePage(locators={"ddl": {"attrs": {"onchange": "__doPostBack('ddl','')"}}})
    result = asyncio.run(_client(page).select(Target(selector="ddl"), "Search by Address"))
    assert not result.ok
    assert result.data["selected_value"] == "addr"
    assert len(page.selects) == 1


def test_postback_select_waits_for_delayed_document_replacement():
    """the document replacement lands *after* the first observation, not after a wall clock delay a sleep here raced the 20ms verification deadline under full suite load and failed intermittently (2026 09 20)"""

    class LatePostbackPage(FakePage):
        def __init__(self, **kwargs) -> None:
            super().__init__(**kwargs)
            self.observations = 0

        async def evaluate(self, script, **kwargs):
            result = await super().evaluate(script, **kwargs)
            if 'const controls =' in script:
                self.observations += 1
                if self.observations >= 2:
                    self.document_id = 1
            return result

    page = LatePostbackPage(locators={"ddl": {"attrs": {"onchange": "__doPostBack('ddl','')"}}})
    result = asyncio.run(_client(page).select(Target(selector="ddl"), "Search by Address"))
    assert result.ok
    assert result.data["postback"]


def test_postback_select_verifies_completed_ajax_request():
    page = FakePage(locators={"ddl": {
        "attrs": {"onchange": "__doPostBack('ddl','')"},
        "select_effect": lambda p: setattr(p, "postbacks_completed", 1)}})
    result = asyncio.run(_client(page).select(Target(selector="ddl"), "Search by Address"))
    assert result.ok


def test_patchright_observation_uses_page_world_for_aspnet_state():
    class PatchrightPage(FakePage):
        async def evaluate(self, script, *, isolated_context=True):
            if 'const controls =' in script:
                assert isolated_context is False
            return await super().evaluate(script)
    page = PatchrightPage(locators={"#go": {
        "effect": lambda p: setattr(p, "_text", "Done")}})
    assert asyncio.run(_client(page).click(Target(selector="#go"))).ok


def test_hidden_duplicate_does_not_make_unique_visible_target_ambiguous():
    page = FakePage(locators={"a[title='Search']": {
        "count": 2, "visible_count": 1,
        "effect": lambda p: setattr(p, "_text", "Search result")}})
    result = asyncio.run(_client(page).click(Target(text="Search")))
    assert result.ok
    assert len(page.clicks) == 1


def test_multiple_hidden_matches_are_not_actionable():
    page = FakePage(locators={"#go": {"count": 2, "visible_count": 0}})
    result = asyncio.run(_client(page).click(Target(selector="#go")))
    assert not result.ok
    assert result.error.kind is BrowserError.NOT_ACTIONABLE
    assert page.clicks == []
