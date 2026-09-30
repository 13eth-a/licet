from licet.browser.tools import BrowserResult, READ_PAGE_INCLUDES, TOOL_DEFINITIONS, TOOL_NAMES
from licet.safety.risk_levels import KNOWN_ACTIONS


def test_tool_set_matches_the_audited_contract():
    assert TOOL_NAMES == (
        "navigate",
        "click",
        "type",
        "select",
        "read_page",
        "wait",
        "screenshot",
    )
    assert [definition.name for definition in TOOL_DEFINITIONS] == list(TOOL_NAMES)


def test_browser_result_has_the_provider_independent_envelope():
    result = BrowserResult(True, "read_page", "/record", "Record Details")
    assert result.as_dict() == {
        "success": True,
        "action": "read_page",
        "url": "/record",
        "observation": "Record Details",
        "error": None,
        "screenshot_path": None,
    }


def test_go_back_is_not_exposed():
    """ACA is WebForms: history navigation resubmits postbacks.

    That is how a duplicate record was created live (BLD26-00466 beside
    BLD26-00467), so the model gets `navigate` instead.
    """
    assert "go_back" not in TOOL_NAMES


def test_every_tool_is_an_object_schema_with_a_description():
    for definition in TOOL_DEFINITIONS:
        assert definition.parameters["type"] == "object"
        assert definition.description


def test_intent_vocabulary_is_the_guard_catalogue():
    click = next(
        definition for definition in TOOL_DEFINITIONS if definition.name == "click"
    )
    properties = click.parameters["properties"]
    assert set(properties["intent"]["enum"]) == set(KNOWN_ACTIONS)
    assert click.parameters["required"] == ["target"]


def test_action_tools_accept_semantic_targets_and_frames():
    for name in ("click", "type", "select"):
        definition = next(d for d in TOOL_DEFINITIONS if d.name == name)
        properties = definition.parameters["properties"]
        assert set(properties) >= {"target", "by", "frame", "intent"}
        assert properties["by"]["enum"] == ["selector", "text", "label"]


def test_read_page_enumerates_its_includes():
    definition = next(
        d for d in TOOL_DEFINITIONS if d.name == "read_page"
    )
    assert set(definition.parameters["properties"]["include"]["items"]["enum"]) == set(
        READ_PAGE_INCLUDES
    )


def test_wait_defaults_to_settling_the_postback():
    definition = next(d for d in TOOL_DEFINITIONS if d.name == "wait")
    settle = definition.parameters["properties"]["settle_postback"]
    assert settle["default"] is True


def test_wait_exposes_polling_for_ajax_sections():
    definition = next(d for d in TOOL_DEFINITIONS if d.name == "wait")
    properties = definition.parameters["properties"]
    assert "until_absent" in properties and "until_present" in properties
    assert "Loading" in properties["until_absent"]["description"]
