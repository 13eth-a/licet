"""Null Island ACA knowledge as data — the reconciliation artefact.

Phase 0 review §5: `scripts/` (the only code that has ever touched the real
portal) imported `licet/` zero times, so the package encoded pre-recon guesses
while the proven behaviour lived in 25 ad-hoc scripts. Everything verified
below was lifted out of those scripts and the live findings in
`docs/accela_ui_map.md`, so the runtime and the guard share one source of
truth instead of each re-deriving it.

Nothing here launches a browser; it is pure data plus parsing helpers, which
makes it testable offline against captured HTML.
"""

from __future__ import annotations

import html as _html
import re
from dataclasses import dataclass, field
from typing import Iterable
from urllib.parse import parse_qsl

# --- environment (verified live 2026-09-19/20) ------------------------------

AGENCY_CODE = "NULLISLAND"
# NOTE: the site root and the agency path are different prefixes. Row hrefs are
# site-absolute (/NULLISLAND/...); prefixing the agency path yields
# /nullisland/NULLISLAND/... -> "The file does not exist".
SITE_ROOT = "https://aca-test.accela.com"
PORTAL_ROOT = f"{SITE_ROOT}/nullisland"
DEFAULT_MODULE = "Building"

LOGIN_URL = f"{PORTAL_ROOT}/Login.aspx"
MY_RECORDS_URL = f"{PORTAL_ROOT}/Cap/MyRecordsCap.aspx?TabName=Home"
APPLY_ENTRY_URL = (
    f"{PORTAL_ROOT}/Cap/CapApplyDisclaimer.aspx"
    f"?module={DEFAULT_MODULE}&TabName={DEFAULT_MODULE}&FilterName=PMT_GENERAL"
)
INSPECTION_ENTRY_URL = (
    f"{PORTAL_ROOT}/Cap/CapHome.aspx?IsToShowInspection=yes&module={DEFAULT_MODULE}"
)


def search_url(module: str = DEFAULT_MODULE) -> str:
    """The record-search page. Results render as a postback of this same URL."""
    return f"{PORTAL_ROOT}/Cap/CapHome.aspx?TabName=Home&module={module}"


# --- record search forms (verified live 2026-09-18/20) ----------------------

# Selecting a search mode is an auto-postback that swaps the whole form: on NI
# choosing "Search by Address" makes `txtGSStreetName` AND even
# `txtGSPermitNumber` vanish. Never cache field ids across a mode switch —
# resolve fields from a fresh field inventory every time.
SEARCH_MODE_DROPDOWN = "ctl00_PlaceHolderMain_ddlSearchType"
SEARCH_BUTTON_TEXT = "Search"

# Address mode on NI uses the APO control family (live-verified: Omaha and NI
# both drop txtGSStreetName when Search by Address is selected). Other agencies
# keep the GS family, so every field is matched by id suffix across both.
SEARCH_FIELD_SUFFIXES: dict[str, tuple[str, ...]] = {
    "record_number": ("txtGSPermitNumber",),
    "street_number": (
        "txtAPO_Search_by_Address_StreetNumber_ChildControl0",
        "txtGSNumber_ChildControl0",
    ),
    "street_name": (
        "txtAPO_Search_by_Address_StreetName",
        "txtGSStreetName",
    ),
    "unit": ("txtAPO_Search_by_Address_UnitNo", "txtGSUnitNo"),
    "zip_code": ("txtAPO_Search_by_Address_Zip", "txtGSZip"),
    "parcel_number": ("txtGSParcelNo", "txtAPO_Search_by_Parcel_ParcelNumber"),
    "applicant_name": ("txtGSBusiName", "txtGSLastName"),
}

# Search-mode dropdown labels, matched case-insensitively as substrings (the
# exact wording is agency-configured). First match wins in tuple order.
SEARCH_MODE_LABELS: dict[str, tuple[str, ...]] = {
    "record_number": ("permit number", "record number"),
    "address": ("address",),
    "parcel": ("parcel",),
    "applicant": ("business name", "owner name", "applicant", "contact name"),
}


def search_mode_option(labels: Iterable[str], method: str) -> str | None:
    """The dropdown label to select for a lookup method, or None when the
    agency exposes no such mode (the caller must fall back, not guess)."""
    matchers = SEARCH_MODE_LABELS.get(method, ())
    for label in labels:
        lowered = (label or "").strip().lower()
        if any(matcher in lowered for matcher in matchers):
            return (label or "").strip()
    return None


def resolve_search_field(fields: Iterable[object], kind: str) -> str | None:
    """The id of the search-form control for `kind`, from a *fresh* inventory.

    `fields` is the read_page field list (dicts or FieldInfo). Returns None when
    the control is absent — after a mode switch that is the expected shape of
    the page, not an error.
    """
    suffixes = SEARCH_FIELD_SUFFIXES.get(kind, ())
    for field in fields:
        control_id = str(field.get("id") or "") if isinstance(field, dict) else str(getattr(field, "id", "") or "")
        if not control_id:
            continue
        for suffix in suffixes:
            if control_id.endswith(suffix):
                return control_id
    return None


# An "address" search can return zero rows purely because the agency
# pre-fills a narrow date window (NI: 09/18/2024→09/18/2026) that hides older
# sandbox data. Widening the start date is a standard retry, not a hack.
SEARCH_DATE_START_SUFFIX = "txtGSStartDate"
SEARCH_DATE_START_WIDENED = "01/01/1990"

# True zero results vs a page that never rendered results: ACA words the first
# explicitly, the second means the search never executed or the grid failed.
ZERO_RESULT_MARKERS: tuple[str, ...] = (
    "no records found",
    "no matching records",
    "there are no records",
    "no data found",
    "0 results",
)
RESULTS_TABLE_HEADER_MARKER = "record number"
RECORD_DETAIL_URL_MARKER = "capdetail.aspx"
# Pagination is postback-based: "Next" fires __doPostBack and the URL does not
# change, so "more pages" is read from the grid footer text.
PAGINATION_NEXT_TEXT = "Next"


def looks_like_zero_results(text: str) -> bool:
    lowered = (text or "").lower()
    return any(marker in lowered for marker in ZERO_RESULT_MARKERS)


def has_results_table(html: str) -> bool:
    return RESULTS_TABLE_HEADER_MARKER in (html or "").lower()


# The CivicId SSO credential form renders inside this iframe; it has
# input[name='username'] / input[name='password'] and no ids.
LOGIN_FRAME_MARKER = "login-panel"

# --- the behaviours that actually make ACA clicks work ----------------------

# `#divGlobalLoadingMask` is a Silverlight-era overlay iframe that stays in the
# DOM "hidden" yet intercepts pointer events after every postback. Injecting
# this after each settle is what turns a timing-out click into a working one.
MASK_SELECTOR = "#divGlobalLoadingMask"
MASK_NEUTRALIZER_JS = (
    "() => { const el = document.querySelector('#divGlobalLoadingMask');"
    " if (el) el.style.setProperty('display', 'none', 'important'); }"
)

# `#btnSearch` keeps a `ButtonDisabled` class no matter what; force-clicking it
# is what fires the search, so button styling is advisory only.
FORCE_CLICK_FALLBACK = True

# MaskedEdit inputs (`class="... maskedfields ..."`, e.g. Zip `#####`, dates
# `MM/DD/YYYY`) ignore fill(); they only accept real keystrokes.
MASKED_CLASS_MARKER = "masked"

# Every ACA popup (contact, licensed professional, education, parcel list)
# renders its controls under this id prefix in its own iframe overlay, and
# saves via ctl00_phPopup_btnSave / ctl00_phPopup_btnSaveAndClose. The
# parent's btnSave is "save and resume later" — a trap.
POPUP_ID_PREFIX = "ctl00_phPopup_"

# The validation panel enumerates missing controls; unescape before matching
# because DOM serialization HTML-encodes the quotes inside onclick attributes.
VALIDATION_TARGET_RE = re.compile(r"skipTo\(\s*['\"]([^'\"]+)", re.I)
# targets whose id contains this are the error *labels*, not the controls
VALIDATION_LABEL_MARKER = "v_a_l_i_d"

# Sections that load over AJAX after the page `load` event (observed on the
# record detail's Inspections section, 2026-09-20). Catching this state matters:
# a mid-load read says "You have not added any inspections", which a planner
# would happily report as fact.
LOADING_MARKERS: tuple[str, ...] = ("loading...", "loading…", "please wait")

# Message text that arrives as a JS notice dialog, not a redirect.
NOTICE_PATTERNS: tuple[str, ...] = (
    "please login to continue",
    "you must be logged in",
    "session has expired",
    "session timeout",
)

# Where a successful CivicId login lands, and how to tell it landed. The SSO
# postback can leave the URL on Login.aspx while the redirect is still in
# flight, so a URL-only check reported a *successful* login as failed on the very
# first live planner run (2026-09-20) — the kind of false alarm that makes a
# whole eval suite run anonymously while looking fine. Hence both signals:
# the URL, then a marker only a signed-in page renders (never "my records",
# which the public header can show).
LOGGED_IN_URL_MARKERS: tuple[str, ...] = ("dashboard.aspx", "myrecordscap.aspx", "caphome.aspx")
LOGGED_IN_TEXT_MARKERS: tuple[str, ...] = ("sign out", "sign-out", "log out", "logoff")


def login_succeeded(url: str, text: str = "") -> bool:
    """Whether the page is past the login form."""
    lowered = (url or "").lower()
    if any(marker in lowered for marker in LOGGED_IN_URL_MARKERS):
        return True
    if "login" in lowered and "login-panel" not in lowered:
        # still the login page proper: do not trust body text that may be part of
        # the public site chrome
        body = (text or "").lower()
        return any(marker in body for marker in LOGGED_IN_TEXT_MARKERS)
    body = (text or "").lower()
    return any(marker in body for marker in LOGGED_IN_TEXT_MARKERS)

@dataclass(frozen=True)
class FlowPosition:
    """Where in a multi-step flow we are.

    ACA postback wizards barely change the URL, so position must be tracked
    explicitly; the `stepNumber` / `pageNumber` query params are the only
    reliable signal, and they are agency/cap-type configured.
    """

    flow: str
    step: str
    page_number: int | None = None


@dataclass(frozen=True)
class Flow:
    """A multi-step flow and the step that commits it.

    `commit_action` is what the safety guard sees instead of a generic
    `click`: on NI the review step issues the record immediately (no payment
    gate, no agree checkbox), so it *is* an application submission.
    """

    name: str
    steps: tuple[str, ...]
    commit_step: str
    commit_action: str


APPLY_FLOW = Flow(
    name="apply_application",
    steps=("disclaimer", "type", "form", "contact", "detail", "review"),
    commit_step="review",
    commit_action="submit_application",
)

# Verified live to the calendar (2026-09-20). CapDetail's URL does not change
# across the wizard's steps, so each step is identified by what the popup says,
# not by the URL — see `schedule_step`. Without that, `flow_step` would sit at
# "select_record" forever and the commit-point rule could never fire.
SCHEDULE_FLOW = Flow(
    name="schedule_inspection",
    steps=("select_record", "select_type", "select_date", "select_time", "confirm"),
    commit_step="confirm",
    commit_action="schedule_inspection",
)

# Ordered: the first marker found in the visible text wins. All verified on the
# record detail's scheduling dialog.
SCHEDULE_STEP_MARKERS: tuple[tuple[str, str], ...] = (
    ("available inspection types", "select_type"),
    ("select an appointment date and time range", "select_date"),
    ("inspection type:", "select_date"),
    ("select a time", "select_time"),
    ("appointment time", "select_time"),
    ("confirm", "confirm"),
)

FLOWS: dict[str, Flow] = {flow.name: flow for flow in (APPLY_FLOW, SCHEDULE_FLOW)}

# CapEdit `stepNumber` -> wizard step. Verified against the Sign - Temporary
# and Right of Way applications; other cap types add their own AppSpec sections
# but keep this page sequence.
_APPLY_STEP_BY_STEP_NUMBER = {1: "form", 2: "contact", 3: "detail"}


def schedule_step(text: str) -> str | None:
    """Which step of the scheduling wizard the popup is showing.

    The wizard's own wording is the only signal: `CapDetail.aspx` is the URL for
    every step, so a URL-only position would keep reporting `select_record` and
    the guard's commit-point rule would never see `confirm`.
    """
    haystack = (text or "").lower()
    for marker, step in SCHEDULE_STEP_MARKERS:
        if marker in haystack:
            return step
    return None


def locate(url: str, text: str = "") -> FlowPosition | None:
    """Best-effort flow position from a URL, refined by the visible text.

    `text` is optional so URL-only callers (and the guard) keep working.
    """
    if not url:
        return None
    path = url.split("?")[0].lower()
    query = url.lower()
    page_number = _int_param(query, "pagenumber")

    if "capapplydisclaimer" in path:
        return FlowPosition(APPLY_FLOW.name, "disclaimer")
    if "captype" in path:
        return FlowPosition(APPLY_FLOW.name, "type")
    if "capedit" in path:
        step_number = _int_param(query, "stepnumber")
        step = _APPLY_STEP_BY_STEP_NUMBER.get(step_number, "form")
        return FlowPosition(APPLY_FLOW.name, step, page_number=page_number)
    if "capconfirm" in path:
        return FlowPosition(APPLY_FLOW.name, "review", page_number=page_number)
    if "istoshowinspection=yes" in query:
        return FlowPosition(SCHEDULE_FLOW.name, schedule_step(text) or "select_record")
    if "myrecordscap" in path:
        return FlowPosition("my_records", "list")
    if "capdetail" in path:
        return FlowPosition("record_detail", "summary")
    if "caphome" in path:
        return FlowPosition("search", "form")
    return None


def _int_param(query: str, name: str) -> int | None:
    match = re.search(rf"[?&]{name}=(\d+)", query)
    return int(match.group(1)) if match else None


def detail_url(
    cap_id1: str,
    cap_id2: str,
    cap_id3: str,
    *,
    module: str = DEFAULT_MODULE,
    agency_code: str = AGENCY_CODE,
) -> str:
    """Record detail deep link (the read path verified for all 8 owned records)."""
    return (
        f"{SITE_ROOT}/{agency_code}/Cap/CapDetail.aspx"
        f"?Module={module}&TabName={module}"
        f"&capID1={cap_id1}&capID2={cap_id2}&capID3={cap_id3}"
        f"&agencyCode={agency_code}&IsToShowInspection="
    )


def inspection_detail_url(ref: dict[str, str]) -> str:
    """Verified record's read-only inspection view, observed in P13's live trace.

    This changes the displayed panel only; it does not open or submit a booking.
    Build from a verified record reference, never an arbitrary portal-supplied URL.
    """
    return detail_url(
        ref["capID1"], ref["capID2"], ref["capID3"],
        module=ref.get("module", DEFAULT_MODULE),
        agency_code=ref.get("agency_code", AGENCY_CODE),
    ) + "yes"


# --- HTML parsing helpers (regex, as the scripts proved necessary) ----------


@dataclass(frozen=True)
class FieldInfo:
    """A control on the page, enough for the planner to act on it."""

    id: str
    name: str
    kind: str  # text | select | checkbox | radio | textarea | other
    label: str = ""
    required: bool = False
    masked: bool = False
    value: str = ""
    options: tuple[str, ...] = ()
    postback: bool = False

    def as_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "name": self.name,
            "kind": self.kind,
            "label": self.label,
            "required": self.required,
            "masked": self.masked,
            "value": self.value,
            "options": list(self.options),
            "postback": self.postback,
        }


@dataclass(frozen=True)
class ValidationError:
    """One entry from ACA's validation panel: which control, and why."""

    control_id: str
    message: str

    def as_dict(self) -> dict[str, str]:
        return {"control_id": self.control_id, "message": self.message}


@dataclass(frozen=True)
class FrameInfo:
    url: str = ""
    title: str = ""
    popup: bool = False
    login_panel: bool = False
    text: str = ""
    errors: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, object]:
        return {
            "url": self.url,
            "title": self.title,
            "popup": self.popup,
            "login_panel": self.login_panel,
            "text": self.text,
            "errors": list(self.errors),
        }


# ACA markup is not consistent about attribute quoting; both forms occur in
# popup and validation fragments. Keep this parser deliberately small, but do
# not silently lose all fields when a response uses single quotes.
_ATTR_RE = re.compile(r"([a-zA-Z_:][-a-zA-Z0-9_:.]*)\s*=\s*(['\"])(.*?)\2", re.S)
_TAG_RE = re.compile(r"<(input|select|textarea)\b([^>]*)>", re.I)
_SELECT_RE = re.compile(r"<select\b([^>]*)>(.*?)</select>", re.I | re.S)
_OPTION_RE = re.compile(r"<option\b([^>]*)>(.*?)</option>", re.I | re.S)
_LABEL_RE = re.compile(r"<label\b([^>]*)>(.*?)</label>", re.I | re.S)
_ANCHOR_RE = re.compile(r"<a\b[^>]*?" + VALIDATION_TARGET_RE.pattern + r"[^>]*>(.*?)</a>", re.I | re.S)
_TAG_STRIP_RE = re.compile(r"<[^>]+>")


def _attrs(raw: str) -> dict[str, str]:
    return {name.lower(): value for name, _quote, value in _ATTR_RE.findall(raw or "")}


def _text(fragment: str) -> str:
    return re.sub(r"\s+", " ", _html.unescape(_TAG_STRIP_RE.sub(" ", fragment or ""))).strip()


def _is_required(attrs: dict[str, str]) -> bool:
    return (
        attrs.get("title", "").lower() == "required"
        or attrs.get("aria-required", "").lower() == "true"
        or "maskedediterror" in attrs.get("class", "").lower()
    )


def parse_fields(html: str) -> list[FieldInfo]:
    """Every labelled control, keyed the way ACA actually renders it.

    `fieldname` / `aria-label` / the `<label for=...>` text are the stable
    handles; the `ctl00_...` ids are ~60 chars and agency-config driven.
    """
    html = _html.unescape(html or "")
    labels: dict[str, str] = {}
    for attrs_raw, body in _LABEL_RE.findall(html):
        attrs = _attrs(attrs_raw)
        target = attrs.get("for")
        if target:
            labels[target] = _text(body)

    fields: list[FieldInfo] = []
    for tag, raw in _TAG_RE.findall(html):
        attrs = _attrs(raw)
        control_id = attrs.get("id", "")
        name = attrs.get("name", "")
        tag = tag.lower()
        if tag == "select":
            fields.append(
                FieldInfo(
                    id=control_id,
                    name=name,
                    kind="select",
                    label=attrs.get("fieldname") or labels.get(control_id, ""),
                    required=_is_required(attrs),
                    value=_select_value(html, control_id),
                    options=_select_options(html, control_id),
                    postback=True,  # ACA dropdowns auto-postback
                )
            )
            continue
        kind = {
            "input": attrs.get("type", "text").lower(),
            "textarea": "textarea",
        }[tag]
        fields.append(
            FieldInfo(
                id=control_id,
                name=name,
                kind=kind,
                label=(
                    attrs.get("fieldname")
                    or attrs.get("aria-label")
                    or labels.get(control_id, "")
                ),
                required=_is_required(attrs),
                masked=MASKED_CLASS_MARKER in attrs.get("class", "").lower(),
                value=attrs.get("value", ""),
            )
        )
    return fields


# --- the scheduling wizard's inspection types (verified live 2026-09-20) ---

# Types render as radios inside this ACA dialog grid. The declared total comes
# from the section heading, not from the rows: the grid paginates at 10 rows
# (`< Prev 1 2 Next >`), so a page-1 read shows 10 of Commercial Alteration's 18.
INSPECTION_TYPE_ID_MARKER = "gvInspectionType"
INSPECTION_TYPE_COUNT_RE = re.compile(
    r"Available Inspection Types\s*\(\s*(?P<count>\d+)\s*\)", re.I
)
# `(required)` / `(optional)` is inside the label text and is the only signal
# for whether ACA will let the rest be skipped.
_TYPE_MARKER_RE = re.compile(r"\(\s*(?P<marker>optional|required)\s*\)\s*$", re.I)


@dataclass(frozen=True)
class InspectionTypeOption:
    """One selectable inspection type from the scheduling wizard."""

    name: str
    required: bool
    control_id: str = ""
    value: str = ""

    def as_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "required": self.required,
            "control_id": self.control_id,
            "value": self.value,
        }


def _field_get(field: object, key: str) -> str:
    """Read a key off a `FieldInfo` or the dict `read_page` serialises it to."""
    if isinstance(field, dict):
        return str(field.get(key) or "")
    return str(getattr(field, key, "") or "")


def parse_inspection_types(fields: list[object]) -> list[InspectionTypeOption]:
    """The wizard's selectable inspection types.

    Names come from the radio's label (`floor deck (required)`, `set backs
    (optional)`), and the grid's own id marker is preferred. If ACA ever renders
    the grid elsewhere, fall back to any radio whose label carries the
    `(required)`/`(optional)` marker so this degrades to the same answer.
    """
    radios = [f for f in fields if _field_get(f, "kind") == "radio"]
    marked = [
        f
        for f in radios
        if INSPECTION_TYPE_ID_MARKER in _field_get(f, "id")
        or _TYPE_MARKER_RE.search(_field_get(f, "label"))
    ]
    options: list[InspectionTypeOption] = []
    for field in marked:
        label = _field_get(field, "label").strip()
        match = _TYPE_MARKER_RE.search(label)
        name = _TYPE_MARKER_RE.sub("", label).strip()
        if not name:
            continue
        options.append(
            InspectionTypeOption(
                name=name,
                required=bool(match) and match.group("marker").lower() == "required",
                control_id=_field_get(field, "id"),
                value=_field_get(field, "value"),
            )
        )
    return options


def inspection_type_total(text: str) -> int | None:
    """Declared total from `Available Inspection Types (18)` — spans all pages."""
    match = INSPECTION_TYPE_COUNT_RE.search(text or "")
    return int(match.group("count")) if match else None


# --- the scheduling calendar (verified live 2026-09-20) ---------------------

# Days are `<td class="CalendarDayInactive ACA_LinkButton" title="Cannot
# schedule inspection on this date">1</td>` — cells, not anchors, so resolving a
# date by link text is impossible. Active days drop the Inactive class.
CALENDAR_CONTAINER_ID = "_calendar_calendar"
CALENDAR_DAY_CLASS_MARKER = "calendarday"
CALENDAR_INACTIVE_CLASS = "calendardayinactive"
CALENDAR_INACTIVE_TITLE = "cannot schedule inspection on this date"
# Popup Continue is rendered disabled until a date *and* time are chosen, with
# the real postback stashed in `href_disabled` — a force-click would fire a
# postback the portal has explicitly disabled.
POPUP_CONTINUE_ID = "ctl00_phPopup_lnkContinue"
_SELECTABLE_TIMES_ID = "lblavaliabletimes"

_CALENDAR_TABLE_RE = re.compile(
    r"<table\b([^>]*id=\"[^\"]*" + CALENDAR_CONTAINER_ID + r"[^\"]*\"[^>]*)>(.*?)</table>",
    re.I | re.S,
)
_CAPTION_RE = re.compile(r"<caption[^>]*>(.*?)</caption>", re.I | re.S)
_CALENDAR_CELL_RE = re.compile(r"<td\b([^>]*\bclass=\"[^\"]*" + CALENDAR_DAY_CLASS_MARKER + r"[^\"]*\"[^>]*)>(.*?)</td>", re.I | re.S)
_ID_SPAN_RE = re.compile(
    rf'id="[^"]*{_SELECTABLE_TIMES_ID}"[^>]*>(.*?)</span>', re.I | re.S
)


@dataclass(frozen=True)
class CalendarMonth:
    """One month of the appointment calendar."""

    month: str
    active_days: tuple[int, ...]
    inactive_days: tuple[int, ...]

    @property
    def any_available(self) -> bool:
        return bool(self.active_days)

    def as_dict(self) -> dict[str, object]:
        return {
            "month": self.month,
            "active_days": list(self.active_days),
            "inactive_days": list(self.inactive_days),
            "any_available": self.any_available,
        }


def parse_calendar(html: str) -> list[CalendarMonth]:
    """The appointment calendar, month by month.

    `any_available` is the honest answer to "can this be scheduled?": on a
    sandbox where every cell is `CalendarDayInactive`, no date can be chosen at
    all, so the flagship "book the earliest slot" goal is unachievable there and
    the agent must say so rather than hunt the calendar forever.
    """
    html = _html.unescape(html or "")
    months: list[CalendarMonth] = []
    for attrs_raw, body in _CALENDAR_TABLE_RE.findall(html):
        caption = _CAPTION_RE.search(body)
        active: list[int] = []
        inactive: list[int] = []
        for cell_attrs, cell_body in _CALENDAR_CELL_RE.findall(body):
            text = _text(cell_body)
            if not text.isdigit():
                continue
            day = int(text)
            if (
                CALENDAR_INACTIVE_CLASS in cell_attrs.lower()
                or CALENDAR_INACTIVE_TITLE in cell_attrs.lower()
            ):
                inactive.append(day)
            else:
                active.append(day)
        months.append(
            CalendarMonth(
                month=_text(caption.group(1)) if caption else "",
                active_days=tuple(active),
                inactive_days=tuple(inactive),
            )
        )
    return months


def selectable_times_text(html: str) -> str:
    """Text of `lblAvaliableTimes` (ACA's spelling) — empty until a day is picked."""
    match = _ID_SPAN_RE.search(_html.unescape(html or ""))
    return _text(match.group(1)) if match else ""


def popup_continue_disabled(html: str) -> bool:
    """Whether the wizard's popup Continue is the disabled variant."""
    match = re.search(
        rf'<a\b[^>]*id="{re.escape(POPUP_CONTINUE_ID)}"([^>]*)>', _html.unescape(html or ""), re.I
    )
    if not match:
        return False
    attrs = match.group(1)
    # A standalone `disabled` attribute, not the `href_disabled` fallback or the
    # cosmetic `ButtonDisabled` class (both contain "disabled" as a substring,
    # and matching either would read a disabled button as enabled).
    has_disabled_attr = bool(re.search(r"\bdisabled\s*=", attrs, re.I))
    return has_disabled_attr and "href_disabled" in attrs.lower()


# --- scheduling wizard: section links, confirmation, calendar dates --------

# The record-detail link(s) that open the scheduling wizard. Null Island
# rendered the short label (2026-09-20 captures); the long one is ACA's
# standard wording. Matched case-insensitively; first hit wins.
SCHEDULE_LINK_LABELS: tuple[str, ...] = (
    "Schedule or Request an Inspection",
    "Schedule an Inspection",
)
# Null Island renders the schedule opener as a clickable <div onclick=...>,
# with the label in a nested <span>; it is not an anchor despite its link-like
# presentation. Resolve the actual JavaScript-backed control by stable id.
SCHEDULE_LINK_CONTROL_ID = "lnkInspectionSchedule"
# NOTE the opener lives inside the record-tabs menu, which ACA renders as a
# *collapsed dropdown* (`a[data-control="tab-inspections"]` sits in a
# `nav-bar > selected > dropdown-menu > li` chain and measures 0x0). On the
# plain detail URL the opener is therefore present in the DOM but laid out at
# 0x0, and a click is refused as `not_actionable` — the wizard never opens and
# the failure only surfaces later as a missing declared type count. Measured
# live 2026-09-30. Land on the record's inspection view first (`detail_url(...)
# + "yes"`, i.e. ACA's own `IsToShowInspection=yes` flag, as
# `inspection_detail_url` builds) — that renders the panel, after which the
# opener is genuinely clickable and the dialog opens with e.g.
# "Available Inspection Types (13)".

# The popup's month tables share this id fragment (verified capture
# 2026-09-20): ctl00_phPopup_calendar_calendar1/2/3. Selectors anchor on the
# fragment so a control-id prefix change does not break the day click.
CALENDAR_TABLE_ID_MARKER = "calendar_calendar"

# The month tables carry NO caption on Null Island (verified: the capture has
# no <caption> and no month name anywhere in the popup) — the three rendered
# tables are the reference month and the two following it. When an agency
# *does* render a caption ("September 2026"), it wins over the assumption.
_CAPTION_MONTH_RE = re.compile(r"\b([A-Za-z]+)\s+(\d{4})\b")
_MONTH_NAMES: dict[str, int] = {}
for _i, _name in enumerate(
    (
        "january february march april may june july august september october november december"
    ).split(),
    start=1,
):
    _MONTH_NAMES[_name] = _i
    _MONTH_NAMES[_name[:3]] = _i


def parse_confirmation_number(text: str) -> str | None:
    """The portal's confirmation/ref number off a success page, or None.

    ACA words these pages per-agency; the phrase family covers the standard
    renderings (Confirmation Number: X / Confirmation #X / Reference No. X).
    None means no number was printed: the caller must rely on the state
    re-read for verification, never on the absence of the word "success".
    """
    match = re.search(
        r"\b(?:confirmation|reference)(?:\s+(?:number|no\.?|ref\.?))?\s*[:#]?\s*"
        r"([A-Za-z0-9][A-Za-z0-9-]{2,})",
        text or "",
        re.I,
    )
    # Exclude the phrase itself being matched as the value (e.g. a heading
    # "Confirmation Number" followed by nothing) — require a digit somewhere.
    value = match.group(1) if match else None
    if value and not any(ch.isdigit() for ch in value):
        return None
    return value


def resolve_calendar_months(
    months: Iterable[object], *, reference: "object"
) -> list[tuple[int, int, tuple[int, ...]]]:
    """(year, month, active_days) per rendered month table, in render order.

    `months` is the `calendar` list `read_page` assembles (dicts with
    `month`/`active_days`, or CalendarMonth objects). A table whose caption
    names a month uses it; a caption-less table is the implied strip: the
    reference month plus each following one (the only rendering observed live).
    A caption that names no known month yields `(0, 0, ...)` — its days are
    unusable because their dates would be invented.
    """
    import datetime as _dt

    resolved: list[tuple[int, int, tuple[int, ...]]] = []
    implied_index = 0
    for entry in months or ():
        caption = str(getattr(entry, "month", "") or (entry.get("month") if isinstance(entry, dict) else "") or "")
        active = tuple(getattr(entry, "active_days", ()) or (entry.get("active_days") if isinstance(entry, dict) else ()) or ())
        match = _CAPTION_MONTH_RE.search(caption) if caption else None
        if caption and not match:
            # A caption exists but names no parseable month: its days cannot be
            # dated, so they are unusable rather than assumed onto the strip.
            resolved.append((0, 0, active))
            continue
        if match:
            month = _MONTH_NAMES.get(match.group(1).lower())
            if month:
                resolved.append((int(match.group(2)), month, active))
                continue
            resolved.append((0, 0, active))
            continue
        # Implied strip: reference month + implied_index, rolling the year.
        total = reference.month - 1 + implied_index
        year = reference.year + total // 12
        month = total % 12 + 1
        implied_index += 1
        resolved.append((year, month, active))
    return resolved


def active_calendar_day_selector(table_index: int, day: int) -> str:
    """Selector for one *active* day cell inside the wizard popup's month table.

    `table_index` is 0-based render order (the implied month strip). Inactive
    days are `<td class="CalendarDayInactive ACA_LinkButton" ...>` — cells, not
    anchors — so the class filter is what makes an active day addressable, and
    the trailing `text=` engine binds the day number without substring-matching
    other cells. Live-verified markup: logs/ni_backoffice/schedule/*_calendar_f10.html.
    """
    return (
        f'table[id*="{CALENDAR_TABLE_ID_MARKER}{table_index + 1}"] '
        f'td[class*="{CALENDAR_DAY_CLASS_MARKER}"]:'
        f'not([class*="{CALENDAR_INACTIVE_CLASS}"]) >> text="{day}"'
    )


# --- Phase 6 mutation boundaries: what actually mutates on this portal ------

# The citizen portal's complete mutation surface, as far as it is mapped. This
# is the portal-side half of the Phase 6 boundary map: the policy layer says
# WHETHER an action may run, this says WHAT on the page would run it, so the
# dispatcher's resolution (and the audit of unmapped flows) rests on observed
# controls rather than on label guesses. Each entry's provenance is stated; a
# control observed only on other agencies is marked so, never asserted for NI.
#
# `commit` = the click that submits the operation. `mutates` = True only when
# the portal's own handler performs a record-state change; wizard steps that
# merely advance the dialog (select type/date/time, Continue before the confirm
# step) are navigation and are classified READ-ONLY, which is what makes
# driving the wizard to a gate lawful without a confirmation.
MUTATION_BOUNDARIES: tuple[dict[str, object], ...] = (
    # -- scheduling (mapped live to the calendar, 2026-09-20) -----------------
    {
        "flow": "schedule_inspection", "step": "confirm",
        "control": POPUP_CONTINUE_ID, "label": "Continue",
        "action": "schedule_inspection", "risk": "reversible",
        "mutates": True, "commit": True,
        # Live-captured: the popup's Continue is disabled (postback parked in
        # href_disabled) until a date AND time are chosen, so a stray click on
        # an uncompleted wizard cannot commit. SolariClient refuses the
        # disabled variant outright (see click()).
        "evidence": "live capture 2026-09-20; disabled until date+time chosen",
    },
    {
        "flow": "schedule_inspection", "step": "select_type",
        "control": "gvInspectionType radios", "label": "(type radio)",
        "action": "select_inspection_type", "risk": "read_only",
        "mutates": False, "commit": False,
        "evidence": "live capture 2026-09-20; choosing a type does not advance or book",
    },
    {
        "flow": "schedule_inspection", "step": "select_date",
        "control": "calendar day cells", "label": "(day cell)",
        "action": "select_inspection_type", "risk": "read_only",
        "mutates": False, "commit": False,
        "evidence": "live capture 2026-09-20; clicking a day only fills lblAvaliableTimes",
    },
    # -- cancellation (UNMAPPED: no owned record ever held a scheduled
    #    inspection, so the per-row controls never rendered on this sandbox).
    #    Fail-closed is the contract (see the adapter's _UNMAPPED_REASONS); the
    #    guard holds a resolved cancel_inspection before the browser either way.
    {
        "flow": "cancel_inspection", "step": "confirm",
        "control": None, "label": "Cancel (inspection row)",
        "action": "cancel_inspection", "risk": "consequential",
        "mutates": True, "commit": True,
        "evidence": "UNMAPPED on NI: per-row control shape never rendered/captured",
    },
    # -- payments (UNMAPPED on NI: the apply flow's review step carries no
    #    payment gate, and no owned record has been driven to the Payments
    #    section's controls. The record detail DOES render a "Payments" section
    #    link — that link is a READ (benign target in the dispatcher); whatever
    #    controls live inside the section are not classified, so the guard's
    #    answer for them is "unclassified, therefore blocked".)
    {
        "flow": "payments", "step": "pay",
        "control": None, "label": "Make a Payment / Pay Now (phrase-matched)",
        "action": "enter_payment_details", "risk": "consequential",
        "mutates": True, "commit": True,
        "evidence": "UNMAPPED on NI; dispatcher catches these labels by phrase, "
                    "not by a mapped control id",
    },
    # -- legal attestation (mapped: the APPLY flow's disclaimer step. Its agree
    #    checkbox + btnNextStep is the only attestation control observed on NI;
    #    the guard classifies accept_legal_attestation as PROHIBITED — no
    #    approval path — and the apply wizard's review Continue is separately
    #    resolved as submit_application via the flow's commit point.)
    {
        "flow": "apply_application", "step": "disclaimer",
        "control": "apply disclaimer agree checkbox", "label": "I agree",
        "action": "accept_legal_attestation", "risk": "prohibited",
        "mutates": True, "commit": True,
        "evidence": "live apply flow 2026-09-19 (ni_apply_submit.py); PROHIBITED tier",
        # The supported path is a human handoff, not an approval: the run stops
        # here and the operator ticks the box themselves
        # (licet/safety/attestation_handoff.py).
        "handoff": "licet/safety/attestation_handoff.py:accept_disclaimer_with_human",
    },
    {
        "flow": "apply_application", "step": "review",
        "control": "CapConfirm continue", "label": "Continue",
        "action": "submit_application", "risk": "consequential",
        "mutates": True, "commit": True,
        # Verified live: this one Continue issues the record — no payment gate,
        # no agree checkbox on NI. That is why it is the flow's commit step.
        "evidence": "live apply flow 2026-09-19; commit point of APPLY_FLOW",
    },
    # -- document upload (UNMAPPED: Attachments section renders; no upload
    #    control has been captured on an owned record.)
    {
        "flow": "attachments", "step": "upload",
        "control": None, "label": "Upload Document (phrase-matched)",
        "action": "upload_document", "risk": "consequential",
        "mutates": True, "commit": True,
        "evidence": "UNMAPPED on NI; phrase-level only (DANGEROUS_PHRASES 'upload')",
    },
)


def mutation_boundaries() -> tuple[dict[str, object], ...]:
    """The mutation boundary map, for the audit doc and the safety panel.

    Read-only data; callers must treat absent controls as unmapped (fail-closed
    at the guard) rather than as permission to improvise a flow.
    """
    return MUTATION_BOUNDARIES


# --- appointment identity from the record's inspection rows -----------------

# The citizen detail page renders each existing inspection row with its own
# per-row controls (`Edit`/`Cancel` style action links), which ACA addresses by
# the appointment's own id inside the postback target. Those per-row controls
# are the only appointment identity the citizen portal exposes: the row text
# itself is `Type | Status | Date` with no id column. Parsing them is what lets
# a cancel/reschedule proposal name the appointment it acts on
# (TARGET_INSPECTION_UNIDENTIFIED exists precisely because nothing else does).
# Verified shape: phase3 fixtures (record_detail_fees.html, ni_schedule_probe
# captures). Both idioms ACA uses for per-row action links are handled:
#   1. `javascript:__doPostBack('ctlNN$lnkCancel','')`  (WebForms postback)
#   2. `<a href="..." id="ctl00_..._lnkCancel" ...>`     (server-side anchor)
# The postback target carries the row's grid identity: extract the trailing
# sequence so a grid re-render that renumbers ctlNN does not silently rebind a
# stored approval. (The id string itself, not the number, is what snapshots
# carry; the suffix is for the audit's stable-label comparison.)
_ROW_POSTBACK_KEY_RE = re.compile(r"__doPostBack\(['\"]([^'\"]+)", re.I)


def parse_inspection_row_controls(html: str) -> list[dict[str, str]]:
    """Per-appointment action controls observed in the inspections section HTML.

    Returns one entry per row-level control: {"control_id", "verb", "key"},
    where verb is cancel/reschedule/edit and key is the postback target or the
    control id fragment that distinguishes this row's control from every other
    row's. Read-only parsing; the caller (the phase4 adapter) binds a row's id
    to its Type|Status|Date line by document order — one control per row, in
    the order the portal rendered them.
    """
    html = _html.unescape(html or "")
    controls: list[dict[str, str]] = []
    for match in re.finditer(
        r"<a\b(?P<attrs>[^>]*)>(?P<body>.*?)</a>", html, re.I | re.S
    ):
        attrs = _attrs(match.group("attrs"))
        control_id = attrs.get("id", "")
        href = attrs.get("href", "")
        body = _text(match.group("body"))
        # Which mutation the control would perform: the id suffix is ACA's own
        # naming (lnkCancel / lnkReschedule / lnkEdit); the label text is the
        # fallback, matched whole-word so a read-only link like "Cancellation
        # Policy" is not misread as a cancel control.
        verb = None
        for marker, name in (
            ("cancel", "cancel"), ("reschedule", "reschedule"),
            ("edit", "edit"), ("withdraw", "cancel"),
        ):
            if marker in control_id.lower() or re.search(rf"\b{marker}\b", body.lower()):
                verb = name
                break
        if verb is None:
            continue
        postback = _ROW_POSTBACK_KEY_RE.search(href)
        key = postback.group(1) if postback else control_id
        if not key:
            continue
        controls.append({"control_id": control_id, "verb": verb, "key": key})
    return controls


# --- record detail: reading the page into facts (verified 2026-09-20) -------

# The header reads `Record<nbsp>BLD26-00472:<space>\n Right of Way Use Permit
# \nRecord Status:<nbsp>Submitted`. Note the altID format is per record type
# (Commercial Alteration renders `000000014`), so nothing may assume BLD26-.
RECORD_HEADER_RE = re.compile(
    r"Record\s*(?P<id>\S+?):\s*\n?\s*(?P<type>[^\n]+)\n\s*Record Status:\s*(?P<status>[^\n]+)",
    re.I,
)
RECORD_EXPIRATION_RE = re.compile(r"Expiration Date:\s*(?P<value>[^\n]+)", re.I)
# Sections render as links on the detail page; only Record Info/Payments/
# Attachments were click-through-verified (all three open from the summary).
# "Attachments" is listed here because it renders, not because it is reachable
# only one way — the earlier "dead link" reading was our own resolver's bug.
RECORD_SECTIONS: tuple[str, ...] = (
    "Record Info",
    "Payments",
    "Fees",  # some ACA configurations render Fees separately from Payments
    "Attachments",
    "Inspections",
    "Schedule or Request an Inspection",
    "Schedule an Inspection",  # Null Island's shorter live label
)
NO_INSPECTIONS_MARKERS: tuple[str, ...] = (
    "you have not added any inspections",
    "there are no completed inspections on this record",
    "no inspection history",
)


def parse_record_header(text: str) -> dict[str, str]:
    """Display id, record type and raw status off a record detail page."""
    match = RECORD_HEADER_RE.search(text or "")
    if not match:
        return {}
    header = {
        "permit_id": match.group("id").strip(),
        "permit_type": match.group("type").strip(),
        "status": match.group("status").strip(),
    }
    expiration = RECORD_EXPIRATION_RE.search(text or "")
    if expiration:
        header["expiration_text"] = expiration.group("value").strip()
    return header


def parse_ref_from_url(url: str) -> dict[str, str]:
    """capID1/2/3 + module + agencyCode out of a CapDetail URL.

    This is the identity that actually addresses a record; the displayed record
    number is only what the agency chose to print.
    """
    query = (url or "").split("?", 1)[-1]
    raw_params = dict(parse_qsl(query, keep_blank_values=True))
    params = {key.lower(): _html.unescape(value) for key, value in raw_params.items()}
    cap_ids = {
        key: params[key.lower()]
        for key in ("capID1", "capID2", "capID3")
        if params.get(key.lower())
    }
    if len(cap_ids) < 3:
        return {}
    return {
        **cap_ids,
        "module": params.get("module", DEFAULT_MODULE),
        "agency_code": params.get("agencycode", AGENCY_CODE),
    }


def parse_sections(text: str) -> list[str]:
    """Which record sections this page renders."""
    lowered = (text or "").lower()
    return [name for name in RECORD_SECTIONS if name.lower() in lowered]


def declares_no_inspections(text: str) -> bool:
    """True when the page says there is no inspection history.

    Distinguish this from a half-rendered AJAX section: `detect_loading` says
    whether the section is still coming, and a reader must apply both or it will
    report "no inspections" about a section that simply had not loaded.
    """
    lowered = (text or "").lower()
    return any(marker in lowered for marker in NO_INSPECTIONS_MARKERS)


def _select_block(html: str, control_id: str) -> str:
    """The <select> body whose opening tag carries control_id.

    Do not search for the literal ``id=\"...\"``: ACA fragments returned by
    different controls legitimately switch between single and double quotes.
    """
    for attrs_raw, body in _SELECT_RE.findall(html):
        if control_id and _attrs(attrs_raw).get("id") == control_id:
            return body
    return ""


def _select_options(html: str, control_id: str) -> tuple[str, ...]:
    block = _select_block(html, control_id)
    labels = []
    for attrs_raw, body in _OPTION_RE.findall(block):
        label = _text(body)
        if label and label not in labels and _attrs(attrs_raw).get("value"):
            labels.append(label)
    return tuple(labels)


def _select_value(html: str, control_id: str) -> str:
    block = _select_block(html, control_id)
    for attrs_raw, body in _OPTION_RE.findall(block):
        if "selected" in attrs_raw.lower():
            return _text(body)
    return ""


def parse_validation_errors(html: str) -> list[ValidationError]:
    """ACA's validation panel as (control_id, message) pairs.

    These are how we learn what a Continue click was missing — the scripts
    relied on them for every required-field discovery.
    """
    html = _html.unescape(html or "")
    errors: list[ValidationError] = []
    seen: set[str] = set()
    for raw in _ANCHOR_RE.findall(html):
        target = _html.unescape(raw[0])
        message = re.sub(r"^\d+\.\s*", "", _text(raw[1]))
        if not target or VALIDATION_LABEL_MARKER in target or target in seen:
            continue
        seen.add(target)
        errors.append(ValidationError(control_id=target, message=message))
    return errors


def validation_targets(html: str) -> list[str]:
    """Control ids the validation panel points at, labels filtered out."""
    found: list[str] = []
    for target in VALIDATION_TARGET_RE.findall(_html.unescape(html or "")):
        target = _html.unescape(target)
        if target and VALIDATION_LABEL_MARKER not in target and target not in found:
            found.append(target)
    return found


def detect_notices(text: str) -> list[str]:
    """JS notice dialogs that never show up as navigation events."""
    lowered = (text or "").lower()
    return [pattern for pattern in NOTICE_PATTERNS if pattern in lowered]


def detect_loading(text: str) -> list[str]:
    """Loading markers still present, i.e. a section is only half-rendered."""
    lowered = (text or "").lower()
    return [marker for marker in LOADING_MARKERS if marker in lowered]


def is_loading(text: str) -> bool:
    return bool(detect_loading(text))


def has_postback_history(html: str) -> bool:
    """True when back-navigation would resubmit a WebForms postback."""
    marker = (html or "").lower()
    return "__viewstate" in marker or "__dopostback" in marker


# --- Phase 7 portal weirdness: the states recovery must recognise -----------

# portal integration, Phase 7. The recovery controller (licet/phase7/recovery.py)
# decides *whether* a failure may be recovered; this vocabulary says *what ACA
# actually did*. Plain data + one classifier, same charter as the rest of this
# module: testable offline against captured text/URLs, no browser required.

# An AJAX grid that has rendered its chrome but not its rows. Distinct from a
# declared-empty section: these wordings mean "the request is still in flight
# or returned nothing yet", so a reader must re-settle instead of recording a
# fact. (Search-grid zero-results stays ZERO_RESULT_MARKERS above; these are
# the in-page grid idioms, including the DataTables default ACA ships.)
EMPTY_TABLE_MARKERS: tuple[str, ...] = (
    "no data available in table",
    "no records to display",
    "no rows to display",
    "loading data",
    "0 of 0",
)

# ACA renders unexpected dialogs as Bootstrap-style modals. A modal the plan
# did not open is either informational (safe to close) or consequential (a
# confirmation that mutates something — never dismissed; routed to policy).
MODAL_TEXT_MARKERS: tuple[str, ...] = (
    "dialog", "modal", "please confirm", "are you sure", "warning",
)
# Modal wordings that carry a real consequence if accepted. Anything else that
# merely *looks* like a modal is treated as informational, and even these are
# only ever routed, never clicked, by the recovery layer.
CONSEQUENTIAL_MODAL_MARKERS: tuple[str, ...] = (
    "are you sure", "confirm cancel", "confirm cancellation", "cannot be undone",
    "do you want to delete", "agree to the", "i accept",
)
# Session expiry sometimes renders as a modal over the current page instead of
# a redirect (observed wording on ACA deployments; NI usually redirects).
SESSION_MODAL_MARKERS: tuple[str, ...] = (
    "your session is about to expire",
    "session about to expire",
    "do you want to stay logged in",
    "session has expired",
)

# The ACA landing page a session bounce or a dead deep link drops the agent
# on. "Unexpectedly on home" is only reportable when the record context is
# gone (no CapDetail in the URL) — CapHome.aspx *is* the search page, not home.
PORTAL_HOME_URL_MARKERS: tuple[str, ...] = (
    "default.aspx", "dashboard.aspx", "/home.aspx",
)

# Targets ACA opens in a new tab/window. Phrase-level and partly other-agency
# observed (provenance stated per the mutation-boundary convention): NI renders
# printable views, attachments and help on the citizen portal.
NEW_TAB_URL_MARKERS: tuple[str, ...] = (
    "printable", "printview", "help.aspx", "downloadattachment", "attachment",
)


def detect_empty_table(text: str) -> list[str]:
    """In-flight or empty grid wordings still visible in the page text."""
    lowered = (text or "").lower()
    return [marker for marker in EMPTY_TABLE_MARKERS if marker in lowered]


def detect_modal(text: str) -> dict[str, object] | None:
    """An unexpected dialog in the text, classified informational/consequential.

    Returns {"kind": "informational"|"consequential", "marker": ...} or None.
    Textual detection is conservative: ACA modals render standard dialog chrome
    and stock wordings. Session-expiry wordings are excluded — they have their
    own, stronger finding (`detect_session_modal`) and must not double-report
    as a generic consequential modal. When in doubt the caller must treat a
    modal as consequential (route to policy) rather than dismiss it.
    """
    lowered = (text or "").lower()
    if any(marker in lowered for marker in SESSION_MODAL_MARKERS):
        return None
    if not any(marker in lowered for marker in MODAL_TEXT_MARKERS):
        return None
    for marker in CONSEQUENTIAL_MODAL_MARKERS:
        if marker in lowered:
            return {"kind": "consequential", "marker": marker}
    return {"kind": "informational", "marker": next(m for m in MODAL_TEXT_MARKERS if m in lowered)}


def detect_session_modal(text: str) -> str | None:
    """A session-expiry *modal* (no redirect) — session handling without navigation."""
    lowered = (text or "").lower()
    return next((m for m in SESSION_MODAL_MARKERS if m in lowered), None)


def is_portal_home(url: str) -> bool:
    """Whether the URL is the ACA landing page (record context absent)."""
    lowered = (url or "").lower()
    if not lowered:
        return False
    if "capdetail" in lowered or "caphome" in lowered or "capedit" in lowered:
        return False
    return any(marker in lowered for marker in PORTAL_HOME_URL_MARKERS)


def looks_like_new_tab(url: str) -> bool:
    """Whether the URL looks like a target ACA opened in its own tab/window."""
    lowered = (url or "").lower()
    return any(marker in lowered for marker in NEW_TAB_URL_MARKERS)


def detect_weirdness(
    text: str,
    url: str = "",
    *,
    popup_open: bool = False,
    loading: Iterable[str] | None = None,
    notices: Iterable[str] | None = None,
) -> tuple[str, ...]:
    """The portal-weirdness findings for one observation, in stable order.

    Values are the Phase 7 portal finding vocabulary (mirrored 1:1 by
    ``licet.phase7.portal.PortalFinding``; the enum lives there, this module
    stays dependency-free). Detection order is fixed so reports are comparable
    across runs.
    """
    findings: list[str] = []
    session_modal = detect_session_modal(text)
    if session_modal:
        findings.append("session_expired_modal")
    if notices:
        findings.append("session_expired")
    if loading:
        findings.append("ajax_section_loading")
    if detect_empty_table(text):
        findings.append("empty_table_pending_rows")
    modal = detect_modal(text)
    if modal:
        findings.append("unexpected_modal" if modal["kind"] == "informational" else "consequential_modal")
    if popup_open:
        findings.append("popup_open")
    if looks_like_new_tab(url):
        findings.append("new_tab_opened")
    if is_portal_home(url):
        findings.append("portal_home_redirect")
    return tuple(findings)
