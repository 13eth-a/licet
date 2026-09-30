"""shared fixtures for phase 2 lookup tests"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from licet.browser import accela
from licet.browser.dispatcher import ToolDispatcher
from licet.browser.solari_client import ToolResult
from licet.lookup import LookupMetrics, PermitLookupRequest
from licet.lookup_runner import LookupRunner
from licet.agent.state import AgentState

SEARCH_URL = f"{accela.PORTAL_ROOT}/Cap/CapHome.aspx?TabName=Home&module=Building"

DETAIL_URL = (
    f"{accela.SITE_ROOT}/NULLISLAND/Cap/CapDetail.aspx?Module=Building&TabName=Building"
    "&capID1=REC26&capID2=00000&capID3=00014&agencyCode=NULLISLAND&IsToShowInspection="
)

# cached link targets used by the existing tests' click assertions
_DETAIL_LINK_PREFIX = f"{accela.SITE_ROOT}/NULLISLAND/Cap/CapDetail.aspx"


def row(
    number: str,
    record_type: str,
    address: str,
    status: str = "In Review",
    applicant: str = "Eval User",
    parcel: str | None = None,
) -> str:
    """one result-grid row, including the empty project name cell the parser must preserve (the my records empty-project-name trap)"""
    parcel_cell = f"<td>{parcel}</td>" if parcel else "<td></td>"
    cap_id = number.lstrip("0").zfill(5) if number.isdigit() else number
    return (
        f"<tr><td>09/20/2026</td><td><a href='{_DETAIL_LINK_PREFIX}?capID1=REC26&capID2=00000&capID3={cap_id}&agencyCode=NULLISLAND&IsToShowInspection='>{number}</a></td>"
        f"<td>{record_type}</td><td></td><td>{address}</td><td>{status}</td>"
        f"<td>{applicant}</td>{parcel_cell}</tr>"
    )


ROW_HTML = """
<table><tr><th>Date</th><th>Record Number</th><th>Record Type</th><th>Project Name</th>
<th>Address</th><th>Status</th><th>Applicant</th><th>Parcel</th>
</tr>
{rows}
</table>
"""


def results_page(
    *rows: str,
    footer: str = "Showing 1-{n} of {total} Next",
    total: int = 0,
    last: int = 0,
) -> str:
    """a complete result grid, with the footer metadata the pagination scan reads"""
    n = len(rows) or 1
    return ROW_HTML.format(rows="".join(rows)) + (
        footer.format(n=n, total=total or n, last=last or n) if footer else ""
    )


class FakeClient:
    """scripted ``solariclient`` stand-in: queued read_page payloads, recorded calls"""

    def __init__(self, reads: list[dict[str, Any]], *, html: str = "") -> None:
        self.reads = list(reads)
        self.read_index = 0
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.html = html
        self.page = types_ns(content=lambda: self.html, url=SEARCH_URL)

    async def navigate(self, url: str) -> ToolResult:
        self.calls.append(("navigate", {"url": url}))
        return _ok(url=url)

    async def click(self, target: Any) -> ToolResult:
        self.calls.append(("click", {"target": target.describe()}))
        return _ok()

    async def type_text(self, target: Any, value: str) -> ToolResult:
        self.calls.append(("type", {"target": target.describe(), "text": value}))
        return _ok()

    async def select(self, target: Any, value: str) -> ToolResult:
        self.calls.append(("select", {"target": target.describe(), "value": value}))
        return _ok()

    async def read_page(self, **kwargs: Any) -> ToolResult:
        self.calls.append(("read_page", kwargs))
        payload = dict(self.reads[min(self.read_index, len(self.reads) - 1)])
        self.read_index += 1
        return _ok(**{**payload, "url": payload.get("url", SEARCH_URL)})

    async def screenshot(self, path: str | None = None) -> ToolResult:
        return _ok(path=None)


    def next_read(self, payload: dict[str, Any]) -> None:
        self.reads.append(payload)


def types_ns(**attrs: Any) -> Any:
    import types

    return types.SimpleNamespace(**attrs)


def _ok(**data: Any) -> ToolResult:
    """real client contract: ``read_page`` carries the url on the result and inside ``data``; the dispatcher's position sync reads it from ``data``"""
    url = data.get("url", SEARCH_URL)
    data.setdefault("url", url)
    return ToolResult(ok=True, url=url, data=data, error=None)


def search_form(
    *,
    url: str = SEARCH_URL,
    fields: list[dict[str, Any]] | None = None,
    options: list[str] | None = None,
    include_date: bool = True,
    text: str = "",
) -> dict[str, Any]:
    """one ``read_page`` payload for a search-form page"""
    fields = list(fields or [])
    if options is not None:
        fields = [
            {
                "id": f"ctl00_PlaceHolderMain_{accela.SEARCH_MODE_DROPDOWN}",
                "kind": "select",
                "options": options,
                "label": options[0] if options else "",
            },
            *fields,
        ]
    if include_date and fields is not None:
        fields = [field_for(accela.SEARCH_DATE_START_SUFFIX), *fields]
    return {"url": url, "text": text, "fields": fields, "flow": None}


def search_results(
    *rows: str,
    url: str = SEARCH_URL,
    text: str | None = None,
    footer: str = "Showing 1-{n} of {total} Next",
    total: int = 0,
    last: int = 0,
) -> dict[str, Any]:
    """one ``read_page`` payload for a rendered result grid"""
    body = text if text is not None else results_page(*rows, footer=footer, total=total, last=last)
    return {
        "url": url,
        "text": body,
        "fields": [],
        "flow": None,
    }


def detail_page(
    number: str = "000000014",
    record_type: str = "Commercial Alteration",
    status: str = "In Review",
    expiration: str = "09/20/2027",
    url: str | None = None,
    address: str | None = None,
) -> dict[str, Any]:
    """one ``read_page`` payload for a record detail page"""
    cap_id = number.lstrip("0").zfill(5) if number.isdigit() else number
    url = url or DETAIL_URL.replace("capID3=00014", f"capID3={cap_id}")
    return {
        "url": url,
        "text": (
            f"Record {number}: {record_type}\n"
            f"Record Status: {status}\n"
            f"Expiration Date: {expiration}"
            + (f"\nWork Location:\n{address}" if address else "")
        ),
        "fields": [],
        "flow": None,
    }


def gs_field(suffix: str, label: str = "") -> dict[str, Any]:
    """one general-search-form text control, in the shape ``parse_fields`` would produce for a ``ctl00_placeholdermain_generalsearchform_*`` id"""
    return {
        "id": f"ctl00_PlaceHolderMain_generalSearchForm_{suffix}",
        "kind": "text",
        "label": label,
    }


def apo_fields() -> list[dict[str, Any]]:
    """the address-mode controls ni actually renders after the mode postback (live-verified: the ``txtapo_search_by_address_*`` family)"""
    return [
        gs_field("txtAPO_Search_by_Address_StreetNumber_ChildControl0"),
        gs_field("txtAPO_Search_by_Address_StreetName"),
    ]


def mode_dropdown(options: list[str]) -> list[dict[str, Any]]:
    """search-mode dropdown rendered as a field the inventory can carry"""
    return [
        {
            "id": f"ctl00_PlaceHolderMain_{accela.SEARCH_MODE_DROPDOWN}",
            "kind": "select",
            "options": options,
        }
    ]


def field_for(suffix: str, label: str = "") -> dict[str, Any]:
    """a general-search-form text control by its id suffix — the shape the runner's ``_bind_fields`` and the live ``resolve_search_field`` both target"""
    return gs_field(suffix, label=label)


def runner_for(client: FakeClient, **kwargs: Any) -> LookupRunner:
    """a ``lookuprunner`` wired to the fake client through the dispatcher"""
    return LookupRunner(
        ToolDispatcher(client),  # type: ignore[arg-type]
        html_source=lambda: client.html,
        **kwargs,
    )


def run(
    request: PermitLookupRequest,
    client: FakeClient,
    *,
    goal: str = "test goal",
    runner_kwargs: dict[str, Any] | None = None,
    state_kwargs: dict[str, Any] | None = None,
) -> tuple[LookupRunner, Any, AgentState]:
    """drive one lookup end to end and return ``(runner, result, state)``"""
    state = AgentState(goal=goal or "test goal", **(state_kwargs or {}))
    runner = runner_for(client, **(runner_kwargs or {}))
    result = asyncio.run(runner.run(goal or "test goal", request, state))
    return runner, result, state
