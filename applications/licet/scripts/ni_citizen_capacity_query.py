"""Find an active appointment date reachable from the signed-in citizen account.

The query starts from My Records (never from a random permit search), verifies
its record-grid coverage, then checks only wizard-offered inspection types on
those account-owned records. It stops at the first identity-bound active date.

Hard limits: 5 My Records pages, 20 records, 200 offered-type checks, and 36
calendar windows per type. Defaults are deliberately smaller. The wizard may
select a type and advance to the calendar, and the scan then clicks the
calendar's own ``Next »`` control forward month by month until it reaches the
``--horizon`` month (default the end of 2028) or the window cap — so availability
in 2027 and beyond is actually observed, not assumed away. This script never
selects a day or time and never reaches the confirm/submit step. All browser
operations go through ToolDispatcher; the portal remains read-only.

Run only when a fresh authenticated read-only check is authorized:
    .venv/bin/python scripts/ni_citizen_capacity_query.py
    .venv/bin/python scripts/ni_citizen_capacity_query.py \
        --max-records 12 --max-types 60 --horizon 2028-12

A negative result means no active date was observed within the completed
record/type/calendar scope, not that availability cannot later change.
"""
from __future__ import annotations

import argparse
import asyncio
import calendar
import html
import json
import os
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qsl, urljoin, urlparse

from dotenv import load_dotenv

load_dotenv()

from licet.agent.state import AgentState  # noqa: E402
from licet.browser import accela  # noqa: E402
from licet.browser.dispatcher import ToolCall, ToolDispatcher  # noqa: E402
from licet.browser.solari_client import SolariSession  # noqa: E402
from licet.eval.phase5_live import MAX_CALENDAR_WINDOWS, LiveInspectionPortal  # noqa: E402
from licet.phase4.accela_portal import PortalObservation  # noqa: E402
from licet.phase4.dates import DateConstraints  # noqa: E402
from licet.safety.policy import Environment, detect_environment  # noqa: E402
try:  # Support both `python scripts/...py` and `python -m scripts....`.
    from ni_my_records import parse_grid  # type: ignore[import-not-found]  # noqa: E402
except ModuleNotFoundError:  # noqa: E402
    from scripts.ni_my_records import parse_grid  # noqa: E402

OUTDIR = Path("logs/ni_backoffice/schedule")
MAX_RECORD_PAGES = 5
MAX_RECORDS = 20
MAX_TYPE_CHECKS = 200
DEFAULT_RECORDS = 12
DEFAULT_TYPES = 40
# The scan pages the appointment calendar forward until it reaches the end of
# this month, then stops with `requested_window_exhausted` — which is what lets a
# negative result mean "no active day through 2028", not merely "we gave up
# after three months". Live 2026-09-30 the query reported Sep-Nov 2026 only
# because it was run with a one-window budget; the horizon is now the knob that
# decides how far the calendar is clicked forward.
DEFAULT_HORIZON = "2028-12"
# Programmatic default when a caller names no horizon; the CLI derives a deeper
# count from --horizon instead, so the shipped command reaches past 2027.
DEFAULT_CALENDAR_WINDOWS = 12
_HORIZON_RE = re.compile(r"^(?P<year>\d{4})-(?P<month>\d{1,2})$")

_SHOWING_RE = re.compile(r"showing\s+(\d+)\s*[-\u2013]\s*(\d+)\s+of\s+(\d+)", re.I)
_RECORD_TYPE_RE = re.compile(
    r"(?P<module>[A-Za-z0-9_-]+)/(?P<group>[A-Za-z0-9 _-]+)/"
    r"(?P<type>[A-Za-z0-9 _-]+)/(?P<subtype>[A-Za-z0-9 _-]+)"
)

_RECORD_TYPE_DETAIL_LABELS = {
    "building/commercial/alteration/na": "Commercial Alteration",
    "building/commercial/electrical/na": "Commercial Electrical",
    "building/residential/addition/na": "Residential Addition",
    "building/residential/mechanical/na": "Residential Mechanical",
    "building/residential/new/sfr": "New Single Family Residence",
    "building/solar/na/na": "Solar Permit",
    "building/right of way/na/na": "Right of Way Use Permit",
    "building/sign/temporary/na": "Sign - Temporary",
    "building/commercial/demolition/na": "Commercial Demolition",
    # 2026-09-30: record types created to widen the availability search. Labels
    # are the citizen-facing ones from the live catalog
    # (logs/ni_backoffice/inventory/20260929T223214Z_catalog.json), not ones
    # inferred from the cap-type path. Without these the query fails closed
    # with `owned_row_record_type_path_is_unmapped` on any of these records.
    "building/residential/alteration/na": "Residential Alteration",
    "building/residential/electrical/na": "Residential Electrical",
    "building/residential/new/na": "Residential New",
    "building/commercial/new/na": "Commercial New",
    "building/fence/na/na": "Fence Permit",
    "building/commercial/plumbing/na": "Commercial Plumbing",
    "building/commercial/re-roof/na": "Commercial Re-Roof",
    # Live 2026-09-30: an unfinished Residential Demolition application
    # (26TMP-000072) showed this label in My Records, and the tenant catalog
    # (logs/ni_backoffice/inventory/20260929T223214Z_catalog.json) carries both
    # demolition paths below the top-level Building module.
    "building/residential/demolition/na": "Residential Demolition",
    "building/multi-family/demolition/na": "Multi-Family Demolition",
}
_RECORD_TYPE_LABEL_ALIASES = {
    "commercial alteration": "Commercial Alteration",
    "commercial electrical": "Commercial Electrical",
    "residential addition": "Residential Addition",
    "residential mechanical": "Residential Mechanical",
    "new single family residence": "New Single Family Residence",
    "solar permit": "Solar Permit",
    "right of way use permit": "Right of Way Use Permit",
    "sign - temporary": "Sign - Temporary",
    "commercial demolition": "Commercial Demolition",
    "residential alteration": "Residential Alteration",
    "residential electrical": "Residential Electrical",
    "residential new": "Residential New",
    "commercial new": "Commercial New",
    "fence permit": "Fence Permit",
    "commercial plumbing": "Commercial Plumbing",
    "commercial re-roof": "Commercial Re-Roof",
    "residential demolition": "Residential Demolition",
    "multi-family demolition": "Multi-Family Demolition",
}
_BUILTIN_RECORD_MODULE = "Building"
_ZERO_RECORD_RANGE_RE = re.compile(r"showing\s+0\s*[-\u2013]\s*0\s+of\s+0", re.I)

# An unfinished application sits in My Records under a temporary number, with a
# resume action and no CapDetail link. Live 2026-09-30
# (logs/ni_backoffice/schedule/mydiag_f0.html): 26TMP-000071/072 rendered as
# Action="Resume Application" with an empty href list. Such a row has no detail
# page, no offered-type catalog and no calendar, so there is nothing to query —
# and it is not an evidence gap about the account's real records. That is a
# different thing from a record we failed to parse, which stays fail-closed.
_TEMPORARY_RECORD_NUMBER_RE = re.compile(r"^[A-Za-z0-9]*TMP-", re.I)
_INCOMPLETE_APPLICATION_ACTIONS = frozenset({
    "resume application",
    "continue application",
    "incomplete application",
    "unfinished application",
})


@dataclass(frozen=True)
class OwnedPage:
    records: tuple[dict[str, Any], ...]
    first: int | None
    last: int | None
    total: int | None
    has_next: bool
    complete_page: bool
    error: str | None = None


@dataclass
class QuerySummary:
    status: str = "unknown"
    reason: str = "query did not complete"
    records_seen: int = 0
    type_checks: int = 0
    calendar_windows: int = 0
    skipped_incomplete_applications: int = 0
    match: dict[str, Any] | None = None
    records: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "reason": self.reason,
            "records_seen": self.records_seen,
            "type_checks": self.type_checks,
            "calendar_windows": self.calendar_windows,
            "skipped_incomplete_applications": self.skipped_incomplete_applications,
            "match": self.match,
            "records": self.records,
        }


def parse_owned_page(text: str, html_frames: list[str]) -> OwnedPage:
    """Parse one My Records page, requiring a coherent declared row range."""
    match = _SHOWING_RE.search(text or "")
    if not match:
        if _ZERO_RECORD_RANGE_RE.search(text or "") and accela.looks_like_zero_results(text):
            return OwnedPage((), 0, 0, 0, False, True)
        return OwnedPage((), None, None, None, False, False, "record_grid_range_missing")
    first, last, total = map(int, match.groups())
    if (first, last, total) == (0, 0, 0):
        if accela.looks_like_zero_results(text):
            return OwnedPage((), 0, 0, 0, False, True)
        return OwnedPage((), 0, 0, 0, False, False, "zero_record_range_without_explicit_empty_notice")
    rows: list[dict[str, Any]] = []
    saw_grid = False
    for source in html_frames:
        if re.search(r"record\s+number", source or "", re.I):
            saw_grid = True
            rows.extend(parse_grid(source))
    # Repeated frame snapshots can contain the same ACA grid; keep first row,
    # preserving My Records display order.
    unique: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        number = str(row.get("Record Number") or "").strip()
        if number and number.casefold() not in seen:
            unique.append(row)
            seen.add(number.casefold())
    page_count = last - first + 1
    has_next = bool(re.search(r"\bnext\b", text or "", re.I)) and last < total
    valid = (saw_grid and first >= 1 and last >= first and total >= last
             and len(unique) == page_count and (last == total or has_next))
    return OwnedPage(tuple(unique), first, last, total, has_next, valid,
                     None if valid else "record_grid_rows_do_not_match_declared_range")


def record_reference(row: dict[str, Any]) -> dict[str, str] | None:
    """Resolve only a same-tenant CapDetail link into an addressable record ref."""
    row_type = str(row.get("Record Type") or "").strip()
    expected_label = record_type_display_label(row_type)
    if expected_label is None:
        return None
    type_parts = [part.strip() for part in row_type.split("/")]
    expected_module = type_parts[0] if len(type_parts) == 4 else _BUILTIN_RECORD_MODULE
    for href in row.get("hrefs") or ():
        target = urljoin(accela.SITE_ROOT + "/", html.unescape(str(href)))
        parsed = urlparse(target)
        ref = accela.parse_ref_from_url(target)
        query_items = parse_qsl(parsed.query)
        query = {key.casefold(): value for key, value in query_items}
        query_keys = [key.casefold() for key, _ in query_items]
        path_parts = parsed.path.strip("/").split("/")
        path_agency = path_parts[0] if len(path_parts) == 3 else ""
        if (parsed.scheme == "https" and parsed.hostname == "aca-test.accela.com"
                and len(path_parts) == 3
                and [part.casefold() for part in path_parts[1:]] == ["cap", "capdetail.aspx"]
                and path_agency.casefold() == accela.AGENCY_CODE.casefold()
                and path_agency.casefold() == ref.get("agency_code", "").casefold()
                and ref.get("agency_code", "").casefold() == accela.AGENCY_CODE.casefold()
                and query_keys.count("module") == 1
                and query_keys.count("capid1") == 1
                and query_keys.count("capid2") == 1
                and query_keys.count("capid3") == 1
                and query_keys.count("agencycode") == 1
                and query.get("agencycode", "").casefold() == ref.get("agency_code", "").casefold()
                and ref.get("module", "").casefold() == expected_module.casefold()
                and ref.get("module", "").casefold() == str(query.get("module") or "").casefold()
                and all(ref.get(key) and re.fullmatch(r"[A-Za-z0-9_-]+", ref[key])
                        for key in ("capID1", "capID2", "capID3", "module"))):
            return ref
    return None


def is_incomplete_application_row(row: dict[str, Any]) -> bool:
    """True only for an unfinished application, never for an inspectable record.

    Requires all three signals ACA renders together for a draft: an
    incomplete-application action, a temporary record number, and no CapDetail
    link to open. Anything less stays on the fail-closed path, so a real record
    with a parsing problem can never be silently skipped.
    """
    action = " ".join(str(row.get("Action") or "").casefold().split())
    if action not in _INCOMPLETE_APPLICATION_ACTIONS:
        return False
    if not _TEMPORARY_RECORD_NUMBER_RE.match(str(row.get("Record Number") or "").strip()):
        return False
    return not any("capdetail" in str(href).casefold() for href in (row.get("hrefs") or ()))


def _record_key(ref: dict[str, str]) -> str:
    return "/".join((ref["agency_code"], ref["module"], ref["capID1"], ref["capID2"], ref["capID3"]))


def record_type_display_label(grid_type: str) -> str | None:
    """Resolve a known My Records type path or display label to its detail label."""
    parts = [part.strip() for part in grid_type.split("/")]
    if len(parts) == 4 and all(parts):
        key = "/".join(part.casefold() for part in parts)
        return _RECORD_TYPE_DETAIL_LABELS.get(key)
    if len(parts) == 1 and parts[0]:
        return _RECORD_TYPE_LABEL_ALIASES.get(" ".join(parts[0].casefold().split()))
    return None


def _record_type_matches(grid_type: str, detail_type: str) -> bool | None:
    """Compare the mapped My Records hierarchy with its detail-page label."""
    expected = record_type_display_label(grid_type)
    if expected is None:
        return None
    return " ".join(expected.casefold().split()) == " ".join(detail_type.casefold().split())


def _date_is_in_calendar_evidence(active_date: str, availability: dict[str, Any],
                                  *, today: Callable[[], date]) -> bool:
    """Require the exact active date in the identity-bound calendar result."""
    try:
        day = date.fromisoformat(active_date)
    except (TypeError, ValueError):
        return False
    if active_date not in availability.get("available_dates", []):
        return False
    for observed_month in availability.get("calendar_months", []):
        resolved = accela.resolve_calendar_months([observed_month], reference=today())
        if resolved and resolved[0][0] == day.year and resolved[0][1] == day.month:
            return True
    return False


def parse_horizon_month(text: str) -> date:
    """The last day of the ``YYYY-MM`` month the calendar scan must reach."""
    match = _HORIZON_RE.match((text or "").strip())
    if not match:
        raise ValueError(f"unsupported horizon month: {text!r} (expected YYYY-MM)")
    year, month = int(match.group("year")), int(match.group("month"))
    if not 1 <= month <= 12:
        raise ValueError(f"unsupported horizon month: {text!r} (expected YYYY-MM)")
    return date(year, month, calendar.monthrange(year, month)[1])


def windows_for_horizon(end: date, *, today: date) -> int:
    """Calendar windows a scan needs to reach `end`'s month.

    Each window renders three months and the calendar's `Next »` control advances
    the strip by one month, so `end` is reached once the last rendered month
    catches up; two extra windows cover the initial strip's remaining months.
    """
    return (end.year - today.year) * 12 + (end.month - today.month) + 2


async def read_observation(dispatcher: ToolDispatcher, state: AgentState) -> PortalObservation:
    """Read structured page evidence through the ordinary guarded dispatcher."""
    outcome = await dispatcher.execute(ToolCall("read_page", {"include": ["text", "form", "errors"]}), state)
    if not outcome.get("success"):
        raise RuntimeError("portal page could not be read")
    return PortalObservation.from_payload(outcome.get("data") or {})


async def read_owned_page(dispatcher: ToolDispatcher, state: AgentState) -> tuple[OwnedPage, str]:
    """Read My Records through the dispatcher and collect each rendered frame."""
    observation = await read_observation(dispatcher, state)
    text = observation.text
    client = dispatcher.client
    page = getattr(client, "page", None)
    if "myrecordscap.aspx" not in str(getattr(page, "url", "") or "").casefold():
        raise RuntimeError("current page is not the citizen My Records list")
    frames = list(getattr(page, "frames", []) or [])
    html_frames: list[str] = []
    for frame in frames:
        try:
            async with asyncio.timeout(10):
                html_frames.append(await frame.content())
        except Exception:
            continue
    return parse_owned_page(text, html_frames), text


async def _dispatch(dispatcher: ToolDispatcher, state: AgentState, call: ToolCall) -> dict[str, Any]:
    result = await dispatcher.execute(call, state)
    if not result.get("success"):
        detail = (result.get("error") or {}).get("message") or "browser action failed"
        raise RuntimeError(f"{call.name} failed: {detail}")
    return result


async def _assert_test_sandbox(client: Any) -> None:
    url = str(getattr(getattr(client, "page", None), "url", "") or "")
    if (Environment(detect_environment(client)) is not Environment.SANDBOX
            or urlparse(url).hostname != "aca-test.accela.com"):
        raise RuntimeError("refusing operation outside the Accela test sandbox")


async def _inspect_owned_record(
    dispatcher: ToolDispatcher,
    state: AgentState,
    row: dict[str, Any],
    *,
    type_budget: int,
    calendar_windows: int,
    today: Callable[[], date],
    horizon: date | None = None,
    type_offset: int = 0,
) -> dict[str, Any]:
    """Verify the owned row, enumerate its offered types, then check calendars."""
    permit_id = str(row.get("Record Number") or "").strip()
    record_type = str(row.get("Record Type") or "").strip()
    ref = record_reference(row)
    result: dict[str, Any] = {
        "permit_id": permit_id, "record_type": record_type,
        "status": "unknown", "types_checked": 0, "calendar_windows": 0,
    }
    if is_incomplete_application_row(row):
        # Not an error and not an evidence gap: an unfinished application has no
        # detail page, no offered-type catalog and no calendar to read. Reporting
        # it as a missing identity made a clean account look like a parser bug.
        result.update(status="not_a_record", reason=(
            "incomplete draft application: no detail page, offered-type catalog "
            "or calendar exists to inspect"))
        return result
    if not permit_id or not record_type:
        result["reason"] = "owned_row_missing_record_identity_or_safe_detail_link"
        return result

    await _assert_test_sandbox(dispatcher.client)
    if record_type_display_label(record_type) is None:
        result["reason"] = "owned_row_record_type_path_is_unmapped"
        return result
    ref = record_reference(row)
    if ref is None:
        # The type resolved, so what is missing is the CapDetail link itself.
        result["reason"] = "owned_row_missing_safe_detail_link"
        return result
    target = accela.detail_url(ref["capID1"], ref["capID2"], ref["capID3"],
                              module=ref["module"], agency_code=ref["agency_code"])
    await _dispatch(dispatcher, state, ToolCall("navigate", {"url": target}))
    await _assert_test_sandbox(dispatcher.client)
    detail = await read_observation(dispatcher, state)
    key = _record_key(ref)
    if (detail.record_key != key
            or str(detail.record_header.get("permit_id") or "").casefold() != permit_id.casefold()
            or _record_type_matches(record_type, str(detail.record_header.get("permit_type") or "").strip()) is not True):
        result["reason"] = "owned_record_identity_or_type_mismatch"
        return result
    result["identity_verified"] = True
    result["record_key"] = key

    if not any(label.casefold() in detail.text.casefold() for label in accela.SCHEDULE_LINK_LABELS):
        result.update(status="not_citizen_schedulable", reason="schedule affordance absent on verified owned record")
        return result

    portal = LiveInspectionPortal(dispatcher, record_ref=ref, today=today)
    wizard = await portal._open_wizard()
    if (wizard.record_key != key
            or str(wizard.record_header.get("permit_id") or "").casefold() != permit_id.casefold()
            or _record_type_matches(record_type, str(wizard.record_header.get("permit_type") or "").strip()) is not True):
        result["reason"] = "wizard_record_identity_or_type_mismatch"
        return result
    catalog, complete = await portal.inspection_catalog(permit_id, key, wizard=wizard)
    result["catalog"] = {
        "complete": complete,
        "declared_count": portal.last_catalog.get("declared_count"),
        "observed_count": portal.last_catalog.get("observed_count"),
        "pages_read": portal.last_catalog.get("pages_read"),
        "failure": portal.last_catalog.get("failure"),
    }
    if not complete:
        result["reason"] = "offered_type_catalog_incomplete"
        return result
    if not catalog:
        result.update(status="no_offered_inspection_types", reason="verified complete catalog is empty")
        return result

    # A full 13-type sweep at a multi-year horizon is far more calendar traffic
    # than one browser session budget allows, so types can be checked in chunks
    # by skipping the first `type_offset` offered types. The offset is only a
    # slice of the verified catalog — it never widens what is inspected.
    selected = list(catalog[type_offset:type_offset + type_budget])
    if not selected:
        result.update(status="unknown", reason=(
            f"type-check offset {type_offset} is at or past the "
            f"{len(catalog)} offered inspection types"))
        return result
    result["type_offset"] = type_offset
    result["catalog_types_remaining"] = max(0, len(catalog) - type_offset)
    type_results = []
    for entry in selected:
        inspection_type = str(entry.get("name") or "").strip()
        if not inspection_type:
            result["reason"] = "catalog_contains_empty_type"
            return result
        # Reset to the verified record detail before each independent type
        # check. This prevents a second iteration from inheriting calendar or
        # postback state from the preceding type's read.
        await _dispatch(dispatcher, state, ToolCall("navigate", {"url": target}))
        await _assert_test_sandbox(dispatcher.client)
        portal = LiveInspectionPortal(dispatcher, record_ref=ref, today=today)
        detail = await portal._read()
        if (detail.record_key != key
                or str(detail.record_header.get("permit_id") or "").casefold() != permit_id.casefold()
                or _record_type_matches(record_type, str(detail.record_header.get("permit_type") or "").strip()) is not True):
            result["reason"] = "record_identity_changed_before_type_check"
            return result
        dates = await portal.available_dates(
            inspection_type,
            constraints=DateConstraints(end=horizon) if horizon is not None else None,
            max_windows=calendar_windows)
        availability = dict(getattr(portal, "last_availability", {}))
        expected_key = _record_key(ref)
        if (availability.get("record_key") != expected_key
                or str(availability.get("permit_id") or "").casefold() != permit_id.casefold()
                or str(availability.get("inspection_type") or "").casefold() != inspection_type.casefold()):
            availability.update(availability_status="unknown", calendar_read=False,
                                identity_verified=False, failure="record_or_type_identity_mismatch")
        result["types_checked"] += 1
        result["calendar_windows"] += int(availability.get("windows_read") or 0)
        type_result = {
            "inspection_type": inspection_type,
            "availability_status": availability.get("availability_status", "unknown"),
            "calendar_read": availability.get("calendar_read") is True,
            "identity_verified": availability.get("identity_verified") is True,
            "months": availability.get("calendar_months", []),
            "search_stop": availability.get("search_stop"),
            "failure": availability.get("failure"),
        }
        type_results.append(type_result)
        # Long sweeps were indistinguishable from hangs; one line per type.
        print(f"    {inspection_type}: {type_result['availability_status']}"
              f" ({len(type_result['months'])} months, stop={type_result['search_stop']})",
              flush=True)
        if (availability.get("calendar_read") is not True
                or availability.get("identity_verified") is not True
                or availability.get("availability_status") == "unknown"
                or bool(dates) != (availability.get("availability_status") == "available")
                or availability.get("failure")):
            result.update(status="unknown", reason="calendar identity or availability could not be verified", type_results=type_results)
            return result
        if dates:
            active_date = str(dates[0])
            if not _date_is_in_calendar_evidence(active_date, availability, today=today):
                result.update(status="unknown", reason="active date was not present in its identity-bound calendar evidence",
                              type_results=type_results)
                return result
            result.update(status="active_date_found", reason="identity-verified citizen calendar contains an active date",
                          type_results=type_results,
                          match={"permit_id": permit_id, "record_type": record_type,
                                 "record_key": key, "inspection_type": inspection_type,
                                 "active_date": active_date, "calendar_months": availability.get("calendar_months", [])})
            return result
    result["type_results"] = type_results
    if len(catalog) - type_offset > type_budget:
        # The catalog is complete by here (an incomplete one returns earlier) —
        # what ran out is the *check* budget. The old wording said "before
        # catalog was complete", which reads as a catalog failure and is what
        # made a complete 13-type catalog look broken on 2026-09-30.
        result.update(status="unknown", reason=(
            f"type-check budget exhausted after {len(type_results)} of "
            f"{len(catalog) - type_offset} offered inspection types"
            + (f" from offset {type_offset}" if type_offset else "")))
    elif any(item.get("search_stop") == "search_limit" for item in type_results):
        result.update(status="unknown", reason="calendar window limit reached before the inspected horizon was complete"
                      + (f" (requested through {horizon.isoformat()})" if horizon is not None else ""))
    else:
        result.update(status="no_active_date_observed", reason="all verified offered types checked within the bounded calendar horizon")
    return result


async def run_capacity_query(
    dispatcher: ToolDispatcher,
    state: AgentState,
    *,
    max_records: int = DEFAULT_RECORDS,
    max_types: int = DEFAULT_TYPES,
    calendar_windows: int = DEFAULT_CALENDAR_WINDOWS,
    max_record_pages: int = MAX_RECORD_PAGES,
    today: Callable[[], date] = date.today,
    horizon: date | None = None,
    type_offset: int = 0,
) -> QuerySummary:
    """Bounded read-only query over records owned by the authenticated account."""
    if not (1 <= max_records <= MAX_RECORDS
            and 1 <= max_types <= MAX_TYPE_CHECKS
            and 0 <= type_offset <= MAX_TYPE_CHECKS
            and 1 <= calendar_windows <= MAX_CALENDAR_WINDOWS
            and 1 <= max_record_pages <= MAX_RECORD_PAGES):
        raise ValueError("query budgets exceed hard safety limits")

    client = dispatcher.client
    await _dispatch(dispatcher, state, ToolCall("navigate", {"url": accela.MY_RECORDS_URL}))
    await _assert_test_sandbox(client)
    owned: list[dict[str, Any]] = []
    expected_total: int | None = None
    expected_next = 1
    page_count = 0
    while page_count < max_record_pages:
        page, text = await read_owned_page(dispatcher, state)
        page_count += 1
        if not page.complete_page:
            return QuerySummary(status="unknown", reason=page.error or "My Records page incomplete",
                                records_seen=len(owned), records=owned)
        if page.first == 0 and page.last == 0 and page.total == 0:
            return QuerySummary(status="no_owned_records", reason="My Records explicitly reports that the authenticated account has no records")
        if page.first != expected_next:
            return QuerySummary(status="unknown", reason="My Records pagination skipped or repeated rows",
                                records_seen=len(owned), records=owned)
        if expected_total is None:
            expected_total = page.total
            if expected_total is None:
                return QuerySummary(status="unknown", reason="My Records total is unavailable",
                                    records_seen=len(owned), records=owned)
        elif page.total != expected_total:
            return QuerySummary(status="unknown", reason="My Records total changed during pagination",
                                records_seen=len(owned), records=owned)
        remaining_records = max_records - len(owned)
        owned.extend(page.records[:remaining_records])
        if len(owned) >= max_records and page.last < expected_total:
            # Partial ownership coverage remains useful for a positive hit, but
            # must be labelled incomplete if no active date is found.
            break
        if page.last == expected_total:
            break
        if not page.has_next or page.last is None:
            return QuerySummary(status="unknown", reason="My Records pages ended before declared total",
                                records_seen=len(owned), records=owned)
        if page_count >= max_record_pages:
            # Search the verified records already captured, but preserve the
            # incomplete-ownership verdict if no positive result is found.
            break
        expected_next = page.last + 1
        await _dispatch(dispatcher, state, ToolCall("click", {
            "target": accela.PAGINATION_NEXT_TEXT, "by": "text", "intent": "list_records",
        }))
        await _assert_test_sandbox(client)

    ownership_complete = (expected_total is not None and expected_total <= MAX_RECORDS
                          and len(owned) == expected_total)
    if expected_total is None or not owned:
        return QuerySummary(status="unknown", reason="My Records coverage incomplete",
                            records_seen=len(owned), records=owned)
    print(f"My Records: {len(owned)} owned row(s), declared total {expected_total}",
          flush=True)
    if len({str(row.get("Record Number") or "").casefold() for row in owned}) != len(owned):
        return QuerySummary(status="unknown", reason="My Records contains duplicate display record numbers",
                            records_seen=len(owned), records=owned)

    summary = QuerySummary(status="unknown" if not ownership_complete else "no_active_date_observed",
                           reason=("no active date observed in partial My Records coverage (scanned owned-record prefix); remaining account records were not inspected"
                                   if not ownership_complete else
                                   f"no active date observed across at most {max_types} type checks and "
                                   f"{calendar_windows} calendar windows per type"
                                   + (f" through {horizon.isoformat()}" if horizon is not None else "")
                                   + "; availability beyond scanned windows is unknown"),
                           records_seen=len(owned))
    remaining_types = max_types
    unknown_reasons: list[str] = []
    skipped_applications = 0
    if not ownership_complete:
        unknown_reasons.append(summary.reason)
    for index, row in enumerate(owned):
        try:
            inspected = await _inspect_owned_record(
                dispatcher, state, row, type_budget=remaining_types,
                calendar_windows=calendar_windows, today=today, horizon=horizon,
                type_offset=type_offset,
            )
        except Exception as exc:
            # A per-record read failure must not hide a positive result on a
            # later account-owned record. If none is found, retain UNKNOWN.
            inspected = {
                "permit_id": str(row.get("Record Number") or ""),
                "record_type": str(row.get("Record Type") or ""),
                "status": "unknown",
                "reason": f"{type(exc).__name__}: {exc}",
                "types_checked": 0,
                "calendar_windows": 0,
            }
        summary.records.append(inspected)
        # Progress as we go: this query runs for many minutes and each record is
        # a full wizard walk, so a silent run is indistinguishable from a hang.
        print(f"[{index + 1}/{len(owned)}] {inspected.get('permit_id')}: "
              f"{inspected.get('status')} — {str(inspected.get('reason') or '')[:150]}",
              flush=True)
        summary.type_checks += int(inspected.get("types_checked") or 0)
        summary.calendar_windows += int(inspected.get("calendar_windows") or 0)
        remaining_types -= int(inspected.get("types_checked") or 0)
        if inspected.get("status") == "active_date_found":
            summary.status, summary.reason, summary.match = "active_date_found", inspected["reason"], inspected.get("match")
            return summary
        if inspected.get("status") == "not_a_record":
            skipped_applications += 1
        if inspected.get("status") == "unknown":
            unknown_reasons.append(f"{inspected.get('permit_id') or 'record'}: "
                                   f"{inspected.get('reason', 'record/type/calendar evidence incomplete')}")
        if remaining_types <= 0 and index + 1 < len(owned):
            unknown_reasons.append(
                "global inspection-type check budget exhausted before the remaining "
                f"{len(owned) - index - 1} owned record(s) were inspected")
            break
    summary.skipped_incomplete_applications = skipped_applications
    if unknown_reasons:
        summary.status = "unknown"
        summary.reason = "; ".join(unknown_reasons)
    if skipped_applications and skipped_applications == len(summary.records):
        # Every owned row was an unfinished application, so nothing was actually
        # inspected. Claiming "no active date observed" would assert a check that
        # never happened.
        summary.status = "unknown"
        summary.reason = (
            f"no inspectable owned record: all {skipped_applications} owned row(s) are "
            "incomplete draft applications with no detail page, offered-type catalog or calendar")
    elif skipped_applications:
        # Say so, so a clean "no active date" is not silently missing rows.
        summary.reason = (
            f"{summary.reason} | skipped {skipped_applications} incomplete draft "
            "application(s): no detail page, offered-type catalog or calendar exists")
    return summary


def _budgets(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-records", type=int, default=DEFAULT_RECORDS)
    parser.add_argument("--max-types", type=int, default=DEFAULT_TYPES)
    parser.add_argument(
        "--horizon", default=DEFAULT_HORIZON, metavar="YYYY-MM",
        help="month the calendar scan pages forward to (default: %(default)s)")
    parser.add_argument(
        "--calendar-windows", type=int, default=None,
        help="window cap per type; defaults to the count --horizon needs")
    parser.add_argument(
        "--type-offset", type=int, default=0,
        help="skip the first N offered inspection types (chunked full sweeps)")
    parser.add_argument("--max-record-pages", type=int, default=MAX_RECORD_PAGES)
    args = parser.parse_args(argv)
    try:
        args.horizon_end = parse_horizon_month(args.horizon)
    except ValueError as exc:
        parser.error(str(exc))
    today = date.today()
    if args.horizon_end <= today:
        parser.error("--horizon must name a month after today")
    needed = windows_for_horizon(args.horizon_end, today=today)
    if needed > MAX_CALENDAR_WINDOWS:
        parser.error(
            f"--horizon {args.horizon} needs {needed} calendar windows, above the "
            f"{MAX_CALENDAR_WINDOWS}-window scan limit")
    if args.calendar_windows is None:
        args.calendar_windows = needed
    if not (1 <= args.max_records <= MAX_RECORDS
            and 1 <= args.max_types <= MAX_TYPE_CHECKS
            and 0 <= args.type_offset <= MAX_TYPE_CHECKS
            and 1 <= args.calendar_windows <= MAX_CALENDAR_WINDOWS
            and 1 <= args.max_record_pages <= MAX_RECORD_PAGES):
        parser.error("budgets must be positive and within the hard safety limits")
    return args


async def main(argv: list[str] | None = None) -> int:
    args = _budgets(argv)
    user = os.environ.get("ACCELA_TEST_USERNAME", "").strip()
    password = os.environ.get("ACCELA_TEST_PASSWORD", "").strip()
    api_key = os.environ.get("SOLARI_API_KEY", "").strip()
    if not user or not password or not api_key:
        print("missing ACCELA_TEST_USERNAME, ACCELA_TEST_PASSWORD, or SOLARI_API_KEY")
        return 2

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    OUTDIR.mkdir(parents=True, exist_ok=True)
    session = SolariSession()
    report: dict[str, Any] = {
        "generated": stamp,
        "portal": "aca-test.accela.com",
        "mode": "read-only citizen-owned capacity query",
        "budgets": {"max_records": args.max_records, "max_record_pages": args.max_record_pages,
                    "max_types": args.max_types, "type_offset": args.type_offset,
                    "calendar_windows_per_type": args.calendar_windows,
                    "calendar_horizon": args.horizon_end.isoformat()},
    }
    try:
        client = await session.client()
        login = await client.login(user, password)
        if not login.ok or not (login.data or {}).get("authenticated"):
            raise RuntimeError("test-account login did not authenticate")
        dispatcher = ToolDispatcher(client)
        state = AgentState(goal="Read-only: find an active appointment date on an account-owned record")
        summary = await run_capacity_query(
            dispatcher, state, max_records=args.max_records, max_types=args.max_types,
            calendar_windows=args.calendar_windows, max_record_pages=args.max_record_pages,
            horizon=args.horizon_end, type_offset=args.type_offset,
        )
        report.update(summary.as_dict())
        print(f"{summary.status}: {summary.reason}")
        if summary.match:
            print(f"{summary.match['permit_id']} · {summary.match['record_type']} · "
                  f"{summary.match['inspection_type']} · {summary.match['active_date']}")
        print(f"records={summary.records_seen} type_checks={summary.type_checks} "
              f"calendar_windows={summary.calendar_windows}; no date/time selection or submission")
        return 0 if summary.status in {"active_date_found", "no_active_date_observed", "no_owned_records"} else 1
    except Exception as exc:
        report.update(status="unknown", reason=f"{type(exc).__name__}: {exc}")
        print(f"UNKNOWN: {type(exc).__name__}: {exc}")
        return 1
    finally:
        try:
            await session.close()
        except Exception:
            pass
        path = OUTDIR / f"{stamp}_citizen_capacity_query.json"
        path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        print(f"report: {path}")


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
