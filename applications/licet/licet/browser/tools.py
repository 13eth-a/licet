"""The browser interface exposed to the planning model.

Phase 0 review §2 changed this set:

- `go_back` is **not exposed**. ACA is WebForms: history navigation resubmits
  postbacks, which is how a duplicate record got created (BLD26-00466 beside
  BLD26-00467). `navigate` is the supported way back; the client still has no
  back operation on purpose. `refresh` is excluded for the same reason (a
  reload can replay the last POST); `navigate(current_url)` is the safe
  equivalent.
- `click`/`type`/`select` take a **semantic target** (`by="text"` or
  `by="label"`, with `frame=` for the iframe overlays) plus an optional
  `intent`. ACA ids are ~60-char `ctl00_PlaceHolderMain_...` values that differ
  per agency; every live script ended up resolving by `fieldname`/label/text.
- `read_page` carries the observations the failures demanded: visible text,
  field inventory, ACA's validation panel, and the frame/popup inventory.
- `intent` is enumerated from `risk_levels.KNOWN_ACTIONS`, which is also the
  vocabulary the safety guard classifies — the model cannot invent an action
  the guard has never heard of.

Execution lives in `licet/browser/dispatcher.py`, which maps a call onto a
semantic action, runs the guard, then the client. Module-level bound functions
are gone: they bound config at import time, which made runs unconfigurable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from licet.safety.risk_levels import KNOWN_ACTIONS

TOOL_NAMES = ("navigate", "click", "type", "select", "read_page", "wait", "screenshot")


@dataclass(frozen=True)
class BrowserResult:
    """Provider-independent result contract for a browser action.

    Solari's richer payload remains available under ``data`` in its adapter;
    this compact shape is suitable for logs, alternate providers, and tests.
    """

    success: bool
    action: str
    url: str | None = None
    observation: str = ""
    error: str | None = None
    screenshot_path: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "action": self.action,
            "url": self.url,
            "observation": self.observation,
            "error": self.error,
            "screenshot_path": self.screenshot_path,
        }


READ_PAGE_INCLUDES = ("text", "form", "errors", "frames", "notices", "html")


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    parameters: dict[str, Any]


_TARGET_PROPERTIES: dict[str, Any] = {
    "target": {
        "type": "string",
        "description": (
            "What to act on: visible text, a field label (fieldname/aria-label), "
            "or a CSS selector when nothing semantic is available."
        ),
    },
    "by": {
        "type": "string",
        "enum": ["selector", "text", "label"],
        "default": "selector",
        "description": "How to resolve `target`. Prefer 'text' or 'label'.",
    },
    "frame": {
        "type": "string",
        "description": (
            "Substring of the frame URL when the control lives in an iframe "
            "(e.g. 'login-panel', 'ContactAddNew', 'ParcelList')."
        ),
    },
    "intent": {
        "type": "string",
        "enum": sorted(KNOWN_ACTIONS),
        "description": (
            "Semantic action this call performs. Required for anything "
            "consequential (submit_application, enter_payment_details, "
            "cancel_inspection, accept_legal_attestation, ...): the safety "
            "guard holds it for user approval."
        ),
    },
}


def _tool(
    name: str,
    description: str,
    properties: dict[str, Any] | None = None,
    required: tuple[str, ...] = (),
) -> ToolDefinition:
    schema: dict[str, Any] = {"type": "object", "properties": dict(properties or {})}
    if required:
        schema["required"] = list(required)
    return ToolDefinition(name, description, schema)


TOOL_DEFINITIONS: list[ToolDefinition] = [
    _tool(
        "navigate",
        "Open a URL. The only supported way to move backwards (ACA postbacks "
        "resubmit on history navigation).",
        {"url": {"type": "string"}},
        ("url",),
    ),
    _tool(
        "click",
        "Click a control once and verify a stable observable state change. "
        "An uncertain outcome requires reconciliation before another action.",
        _TARGET_PROPERTIES,
        ("target",),
    ),
    _tool(
        "type",
        "Enter text. MaskedEdit fields (Zip '#####', dates 'MM/DD/YYYY') take real "
        "keystrokes and are verified by read-back.",
        {**_TARGET_PROPERTIES, "text": {"type": "string"}},
        ("target", "text"),
    ),
    _tool(
        "select",
        "Choose a value in a dropdown. ACA dropdowns auto-postback and may replace "
        "the whole form, so re-read the page afterwards.",
        {**_TARGET_PROPERTIES, "value": {"type": "string"}},
        ("target", "value"),
    ),
    _tool(
        "read_page",
        "Read the current page: visible text, current URL and flow position, the "
        "field inventory, ACA's validation panel, and the frame/popup inventory. "
        "Re-read after every postback (mode switches replace the form).",
        {
            "include": {
                "type": "array",
                "items": {"type": "string", "enum": list(READ_PAGE_INCLUDES)},
                "description": "Subset of observations; defaults to all but 'html'.",
            }
        },
    ),
    _tool(
        "wait",
        "Wait for an ACA postback to settle (re-hides the loading mask that "
        "intercepts clicks), or for AJAX-loaded content: ACA sections render "
        "'Loading...' after the page load event, so poll with until_absent before "
        "reading them.",
        {
            "seconds": {"type": "number", "minimum": 0},
            "settle_postback": {"type": "boolean", "default": True},
            "until_present": {
                "type": "string",
                "description": "Poll until this text appears.",
            },
            "until_absent": {
                "type": "string",
                "description": (
                    "Poll until this text disappears (e.g. until_absent='Loading...' "
                    "before reading the Inspections section)."
                ),
            },
        },
    ),
    _tool("screenshot", "Capture the current page for the run log.", {}),
]
