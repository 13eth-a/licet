"""null island aca knowledge as data the reconciliation artefact"""

from __future__ import annotations

import html as _html
import re
from dataclasses import dataclass, field
from typing import Iterable
from urllib.parse import parse_qsl


AGENCY_CODE = "NULLISLAND"
# note: the site root and the agency path are different prefixes
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
    """the record search page"""
    return f"{PORTAL_ROOT}/Cap/CapHome.aspx?TabName=Home&module={module}"


# selecting a search mode is an auto postback that swaps the whole form: on ni choosing "search by
# address" makes `txtgsstreetname` and even `txtgspermitnumber` vanish
SEARCH_MODE_DROPDOWN = "ctl00_PlaceHolderMain_ddlSearchType"
SEARCH_BUTTON_TEXT = "Search"

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

SEARCH_MODE_LABELS: dict[str, tuple[str, ...]] = {
    "record_number": ("permit number", "record number"),
    "address": ("address",),
    "parcel": ("parcel",),
    "applicant": ("business name", "owner name", "applicant", "contact name"),
}


def search_mode_option(labels: Iterable[str], method: str) -> str | None:
    """the dropdown label to select for a lookup method, or none when the agency exposes no such mode (the caller must fall back, not guess)"""
    matchers = SEARCH_MODE_LABELS.get(method, ())
    for label in labels:
        lowered = (label or "").strip().lower()
        if any(matcher in lowered for matcher in matchers):
            return (label or "").strip()
    return None


def resolve_search_field(fields: Iterable[object], kind: str) -> str | None:
    """the id of the search form control for `kind`, from a *fresh* inventory"""
    suffixes = SEARCH_FIELD_SUFFIXES.get(kind, ())
    for field in fields:
        control_id = str(field.get("id") or "") if isinstance(field, dict) else str(getattr(field, "id", "") or "")
        if not control_id:
            continue
        for suffix in suffixes:
            if control_id.endswith(suffix):
                return control_id
    return None


# an "address" search can return zero rows purely because the agency pre fills a narrow date window (ni:
# 09/18/2024→09/18/2026) that hides older sandbox data
SEARCH_DATE_START_SUFFIX = "txtGSStartDate"
SEARCH_DATE_START_WIDENED = "01/01/1990"

# true zero results vs a page that never rendered results: aca words the first explicitly, the second
# means the search never executed or the grid failed
ZERO_RESULT_MARKERS: tuple[str, ...] = (
    "no records found",
    "no matching records",
    "there are no records",
    "no data found",
    "0 results",
)
RESULTS_TABLE_HEADER_MARKER = "record number"
RECORD_DETAIL_URL_MARKER = "capdetail.aspx"
# pagination is postback based: "next" fires __dopostback and the url does not change, so "more pages" is
# read from the grid footer text
PAGINATION_NEXT_TEXT = "Next"


def looks_like_zero_results(text: str) -> bool:
    lowered = (text or "").lower()
    return any(marker in lowered for marker in ZERO_RESULT_MARKERS)


def has_results_table(html: str) -> bool:
    return RESULTS_TABLE_HEADER_MARKER in (html or "").lower()


LOGIN_FRAME_MARKER = "login-panel"


# `#divgloballoadingmask` is a silverlight era overlay iframe that stays in the dom "hidden" yet
# intercepts pointer events after every postback
MASK_SELECTOR = "#divGlobalLoadingMask"
MASK_NEUTRALIZER_JS = (
    "() => { const el = document.querySelector('#divGlobalLoadingMask');"
    " if (el) el.style.setProperty('display', 'none', 'important'); }"
)

# `#btnsearch` keeps a `buttondisabled` class no matter what; force clicking it is what fires the search,
# so button styling is advisory only
FORCE_CLICK_FALLBACK = True

# maskededit inputs (`class="... maskedfields ..."`, e.g
MASKED_CLASS_MARKER = "masked"

POPUP_ID_PREFIX = "ctl00_phPopup_"

# the validation panel enumerates missing controls; unescape before matching because dom serialization
# html encodes the quotes inside onclick attributes
VALIDATION_TARGET_RE = re.compile(r"skipTo\(\s*['\"]([^'\"]+)", re.I)
# targets whose id contains this are the error *labels*, not the controls
VALIDATION_LABEL_MARKER = "v_a_l_i_d"

# sections that load over ajax after the page `load` event (observed on the record detail's inspections
# section, 2026 09 20)
LOADING_MARKERS: tuple[str, ...] = ("loading...", "loading…", "please wait")

# message text that arrives as a js notice dialog, not a redirect
NOTICE_PATTERNS: tuple[str, ...] = (
    "please login to continue",
    "you must be logged in",
    "session has expired",
    "session timeout",
)

# where a successful civicid login lands, and how to tell it landed
LOGGED_IN_URL_MARKERS: tuple[str, ...] = ("dashboard.aspx", "myrecordscap.aspx", "caphome.aspx")
LOGGED_IN_TEXT_MARKERS: tuple[str, ...] = ("sign out", "sign-out", "log out", "logoff")


def login_succeeded(url: str, text: str = "") -> bool:
    """whether the page is past the login form"""
    lowered = (url or "").lower()
    if any(marker in lowered for marker in LOGGED_IN_URL_MARKERS):
        return True
    if "login" in lowered and "login-panel" not in lowered:
        # still the login page proper: do not trust body text that may be part of the public site chrome
        body = (text or "").lower()
        return any(marker in body for marker in LOGGED_IN_TEXT_MARKERS)
    body = (text or "").lower()
    return any(marker in body for marker in LOGGED_IN_TEXT_MARKERS)

@dataclass(frozen=True)
class FlowPosition:
    """where in a multi step flow we are"""

    flow: str
    step: str
    page_number: int | None = None


@dataclass(frozen=True)
class Flow:
    """a multi step flow and the step that commits it"""

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

# verified live to the calendar (2026 09 20)
SCHEDULE_FLOW = Flow(
    name="schedule_inspection",
    steps=("select_record", "select_type", "select_date", "select_time", "confirm"),
    commit_step="confirm",
    commit_action="schedule_inspection",
)

SCHEDULE_STEP_MARKERS: tuple[tuple[str, str], ...] = (
    ("available inspection types", "select_type"),
    ("select an appointment date and time range", "select_date"),
    ("inspection type:", "select_date"),
    ("select a time", "select_time"),
    ("appointment time", "select_time"),
    ("confirm", "confirm"),
)

FLOWS: dict[str, Flow] = {flow.name: flow for flow in (APPLY_FLOW, SCHEDULE_FLOW)}

_APPLY_STEP_BY_STEP_NUMBER = {1: "form", 2: "contact", 3: "detail"}


def schedule_step(text: str) -> str | None:
    """which step of the scheduling wizard the popup is showing"""
    haystack = (text or "").lower()
    for marker, step in SCHEDULE_STEP_MARKERS:
        if marker in haystack:
            return step
    return None


def locate(url: str, text: str = "") -> FlowPosition | None:
    """best effort flow position from a url, refined by the visible text"""
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
    """record detail deep link (the read path verified for all 8 owned records)"""
    return (
        f"{SITE_ROOT}/{agency_code}/Cap/CapDetail.aspx"
        f"?Module={module}&TabName={module}"
        f"&capID1={cap_id1}&capID2={cap_id2}&capID3={cap_id3}"
        f"&agencyCode={agency_code}&IsToShowInspection="
    )


def inspection_detail_url(ref: dict[str, str]) -> str:
    """verified record's read only inspection view, observed in p13's live trace"""
    return detail_url(
        ref["capID1"], ref["capID2"], ref["capID3"],
        module=ref.get("module", DEFAULT_MODULE),
        agency_code=ref.get("agency_code", AGENCY_CODE),
    ) + "yes"


@dataclass(frozen=True)
class FieldInfo:
    """a control on the page, enough for the planner to act on it"""

    id: str
    name: str
    kind: str
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
    """one entry from aca's validation panel: which control, and why"""

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


# aca markup is not consistent about attribute quoting; both forms occur in popup and validation fragments
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
    """every labelled control, keyed the way aca actually renders it"""
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
                    postback=True,
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


# types render as radios inside this aca dialog grid
INSPECTION_TYPE_ID_MARKER = "gvInspectionType"
INSPECTION_TYPE_COUNT_RE = re.compile(
    r"Available Inspection Types\s*\(\s*(?P<count>\d+)\s*\)", re.I
)
# `(required)` / `(optional)` is inside the label text and is the only signal for whether aca will let the
# rest be skipped
_TYPE_MARKER_RE = re.compile(r"\(\s*(?P<marker>optional|required)\s*\)\s*$", re.I)


@dataclass(frozen=True)
class InspectionTypeOption:
    """one selectable inspection type from the scheduling wizard"""

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
    """read a key off a `fieldinfo` or the dict `read_page` serialises it to"""
    if isinstance(field, dict):
        return str(field.get(key) or "")
    return str(getattr(field, key, "") or "")


def parse_inspection_types(fields: list[object]) -> list[InspectionTypeOption]:
    """the wizard's selectable inspection types"""
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
    """declared total from `available inspection types (18)` spans all pages"""
    match = INSPECTION_TYPE_COUNT_RE.search(text or "")
    return int(match.group("count")) if match else None


# days are `<td class="calendardayinactive aca_linkbutton" title="cannot schedule inspection on this
# date">1</td>` cells, not anchors, so resolving a date by link text is impossible
CALENDAR_CONTAINER_ID = "_calendar_calendar"
CALENDAR_DAY_CLASS_MARKER = "calendarday"
CALENDAR_INACTIVE_CLASS = "calendardayinactive"
CALENDAR_INACTIVE_TITLE = "cannot schedule inspection on this date"
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
    """one month of the appointment calendar"""

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
    """the appointment calendar, month by month"""
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
    """text of `lblavaliabletimes` (aca's spelling) empty until a day is picked"""
    match = _ID_SPAN_RE.search(_html.unescape(html or ""))
    return _text(match.group(1)) if match else ""


def popup_continue_disabled(html: str) -> bool:
    """whether the wizard's popup continue is the disabled variant"""
    match = re.search(
        rf'<a\b[^>]*id="{re.escape(POPUP_CONTINUE_ID)}"([^>]*)>', _html.unescape(html or ""), re.I
    )
    if not match:
        return False
    attrs = match.group(1)
    # a standalone `disabled` attribute, not the `href_disabled` fallback or the cosmetic `buttondisabled`
    # class (both contain "disabled" as a substring, and matching either would read a disabled button as
    # enabled)
    has_disabled_attr = bool(re.search(r"\bdisabled\s*=", attrs, re.I))
    return has_disabled_attr and "href_disabled" in attrs.lower()


SCHEDULE_LINK_LABELS: tuple[str, ...] = (
    "Schedule or Request an Inspection",
    "Schedule an Inspection",
)
# null island renders the schedule opener as a clickable <div onclick=...>, with the label in a nested
# <span>; it is not an anchor despite its link like presentation
SCHEDULE_LINK_CONTROL_ID = "lnkInspectionSchedule"
# note the opener lives inside the record tabs menu, which aca renders as a *collapsed dropdown*
# (`a[data control="tab inspections"]` sits in a `nav bar > selected > dropdown menu > li` chain and
# measures 0x0)

# the popup's month tables share this id fragment (verified capture 2026 09 20):
# ctl00_phpopup_calendar_calendar1/2/3
CALENDAR_TABLE_ID_MARKER = "calendar_calendar"

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
    """the portal's confirmation/ref number off a success page, or none"""
    match = re.search(
        r"\b(?:confirmation|reference)(?:\s+(?:number|no\.?|ref\.?))?\s*[:#]?\s*"
        r"([A-Za-z0-9][A-Za-z0-9-]{2,})",
        text or "",
        re.I,
    )
    value = match.group(1) if match else None
    if value and not any(ch.isdigit() for ch in value):
        return None
    return value


def resolve_calendar_months(
    months: Iterable[object], *, reference: "object"
) -> list[tuple[int, int, tuple[int, ...]]]:
    """(year, month, active_days) per rendered month table, in render order"""
    import datetime as _dt

    resolved: list[tuple[int, int, tuple[int, ...]]] = []
    implied_index = 0
    for entry in months or ():
        caption = str(getattr(entry, "month", "") or (entry.get("month") if isinstance(entry, dict) else "") or "")
        active = tuple(getattr(entry, "active_days", ()) or (entry.get("active_days") if isinstance(entry, dict) else ()) or ())
        match = _CAPTION_MONTH_RE.search(caption) if caption else None
        if caption and not match:
            # a caption exists but names no parseable month: its days cannot be dated, so they are
            # unusable rather than assumed onto the strip
            resolved.append((0, 0, active))
            continue
        if match:
            month = _MONTH_NAMES.get(match.group(1).lower())
            if month:
                resolved.append((int(match.group(2)), month, active))
                continue
            resolved.append((0, 0, active))
            continue
        total = reference.month - 1 + implied_index
        year = reference.year + total // 12
        month = total % 12 + 1
        implied_index += 1
        resolved.append((year, month, active))
    return resolved


def active_calendar_day_selector(table_index: int, day: int) -> str:
    """selector for one *active* day cell inside the wizard popup's month table"""
    return (
        f'table[id*="{CALENDAR_TABLE_ID_MARKER}{table_index + 1}"] '
        f'td[class*="{CALENDAR_DAY_CLASS_MARKER}"]:'
        f'not([class*="{CALENDAR_INACTIVE_CLASS}"]) >> text="{day}"'
    )


# the citizen portal's complete mutation surface, as far as it is mapped
MUTATION_BOUNDARIES: tuple[dict[str, object], ...] = (
    {
        "flow": "schedule_inspection", "step": "confirm",
        "control": POPUP_CONTINUE_ID, "label": "Continue",
        "action": "schedule_inspection", "risk": "reversible",
        "mutates": True, "commit": True,
        # live captured: the popup's continue is disabled (postback parked in href_disabled) until a date
        # and time are chosen, so a stray click on an uncompleted wizard cannot commit
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
    # cancellation (unmapped: no owned record ever held a scheduled inspection, so the per row controls
    # never rendered on this sandbox)
    {
        "flow": "cancel_inspection", "step": "confirm",
        "control": None, "label": "Cancel (inspection row)",
        "action": "cancel_inspection", "risk": "consequential",
        "mutates": True, "commit": True,
        "evidence": "UNMAPPED on NI: per-row control shape never rendered/captured",
    },
    # payments (unmapped on ni: the apply flow's review step carries no payment gate, and no owned
    # record has been driven to the payments section's controls
    {
        "flow": "payments", "step": "pay",
        "control": None, "label": "Make a Payment / Pay Now (phrase-matched)",
        "action": "enter_payment_details", "risk": "consequential",
        "mutates": True, "commit": True,
        "evidence": "UNMAPPED on NI; dispatcher catches these labels by phrase, "
                    "not by a mapped control id",
    },
    # legal attestation (mapped: the apply flow's disclaimer step
    {
        "flow": "apply_application", "step": "disclaimer",
        "control": "apply disclaimer agree checkbox", "label": "I agree",
        "action": "accept_legal_attestation", "risk": "prohibited",
        "mutates": True, "commit": True,
        "evidence": "live apply flow 2026-09-19 (ni_apply_submit.py); PROHIBITED tier",
        # the supported path is a human handoff, not an approval: the run stops here and the operator
        # ticks the box themselves (licet/safety/attestation_handoff.py)
        "handoff": "licet/safety/attestation_handoff.py:accept_disclaimer_with_human",
    },
    {
        "flow": "apply_application", "step": "review",
        "control": "CapConfirm continue", "label": "Continue",
        "action": "submit_application", "risk": "consequential",
        "mutates": True, "commit": True,
        "evidence": "live apply flow 2026-09-19; commit point of APPLY_FLOW",
    },
    {
        "flow": "attachments", "step": "upload",
        "control": None, "label": "Upload Document (phrase-matched)",
        "action": "upload_document", "risk": "consequential",
        "mutates": True, "commit": True,
        "evidence": "UNMAPPED on NI; phrase-level only (DANGEROUS_PHRASES 'upload')",
    },
)


def mutation_boundaries() -> tuple[dict[str, object], ...]:
    """the mutation boundary map, for the audit doc and the safety panel"""
    return MUTATION_BOUNDARIES


# the citizen detail page renders each existing inspection row with its own per row controls
# (`edit`/`cancel` style action links), which aca addresses by the appointment's own id inside the
# postback target
_ROW_POSTBACK_KEY_RE = re.compile(r"__doPostBack\(['\"]([^'\"]+)", re.I)


def parse_inspection_row_controls(html: str) -> list[dict[str, str]]:
    """per appointment action controls observed in the inspections section html"""
    html = _html.unescape(html or "")
    controls: list[dict[str, str]] = []
    for match in re.finditer(
        r"<a\b(?P<attrs>[^>]*)>(?P<body>.*?)</a>", html, re.I | re.S
    ):
        attrs = _attrs(match.group("attrs"))
        control_id = attrs.get("id", "")
        href = attrs.get("href", "")
        body = _text(match.group("body"))
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


RECORD_HEADER_RE = re.compile(
    r"Record\s*(?P<id>\S+?):\s*\n?\s*(?P<type>[^\n]+)\n\s*Record Status:\s*(?P<status>[^\n]+)",
    re.I,
)
RECORD_EXPIRATION_RE = re.compile(r"Expiration Date:\s*(?P<value>[^\n]+)", re.I)
RECORD_SECTIONS: tuple[str, ...] = (
    "Record Info",
    "Payments",
    "Fees",
    "Attachments",
    "Inspections",
    "Schedule or Request an Inspection",
    "Schedule an Inspection",
)
NO_INSPECTIONS_MARKERS: tuple[str, ...] = (
    "you have not added any inspections",
    "there are no completed inspections on this record",
    "no inspection history",
)


def parse_record_header(text: str) -> dict[str, str]:
    """display id, record type and raw status off a record detail page"""
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
    """capid1/2/3 + module + agencycode out of a capdetail url"""
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
    """which record sections this page renders"""
    lowered = (text or "").lower()
    return [name for name in RECORD_SECTIONS if name.lower() in lowered]


def declares_no_inspections(text: str) -> bool:
    """true when the page says there is no inspection history"""
    lowered = (text or "").lower()
    return any(marker in lowered for marker in NO_INSPECTIONS_MARKERS)


def _select_block(html: str, control_id: str) -> str:
    """the <select> body whose opening tag carries control_id"""
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
    """aca's validation panel as (control_id, message) pairs"""
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
    """control ids the validation panel points at, labels filtered out"""
    found: list[str] = []
    for target in VALIDATION_TARGET_RE.findall(_html.unescape(html or "")):
        target = _html.unescape(target)
        if target and VALIDATION_LABEL_MARKER not in target and target not in found:
            found.append(target)
    return found


def detect_notices(text: str) -> list[str]:
    """js notice dialogs that never show up as navigation events"""
    lowered = (text or "").lower()
    return [pattern for pattern in NOTICE_PATTERNS if pattern in lowered]


def detect_loading(text: str) -> list[str]:
    """loading markers still present, i.e. a section is only half rendered"""
    lowered = (text or "").lower()
    return [marker for marker in LOADING_MARKERS if marker in lowered]


def is_loading(text: str) -> bool:
    return bool(detect_loading(text))


def has_postback_history(html: str) -> bool:
    """true when back navigation would resubmit a webforms postback"""
    marker = (html or "").lower()
    return "__viewstate" in marker or "__dopostback" in marker


# portal integration, phase 7

# an ajax grid that has rendered its chrome but not its rows
EMPTY_TABLE_MARKERS: tuple[str, ...] = (
    "no data available in table",
    "no records to display",
    "no rows to display",
    "loading data",
    "0 of 0",
)

# aca renders unexpected dialogs as bootstrap style modals
MODAL_TEXT_MARKERS: tuple[str, ...] = (
    "dialog", "modal", "please confirm", "are you sure", "warning",
)
# modal wordings that carry a real consequence if accepted
CONSEQUENTIAL_MODAL_MARKERS: tuple[str, ...] = (
    "are you sure", "confirm cancel", "confirm cancellation", "cannot be undone",
    "do you want to delete", "agree to the", "i accept",
)
# session expiry sometimes renders as a modal over the current page instead of a redirect (observed
# wording on aca deployments; ni usually redirects)
SESSION_MODAL_MARKERS: tuple[str, ...] = (
    "your session is about to expire",
    "session about to expire",
    "do you want to stay logged in",
    "session has expired",
)

PORTAL_HOME_URL_MARKERS: tuple[str, ...] = (
    "default.aspx", "dashboard.aspx", "/home.aspx",
)

NEW_TAB_URL_MARKERS: tuple[str, ...] = (
    "printable", "printview", "help.aspx", "downloadattachment", "attachment",
)


def detect_empty_table(text: str) -> list[str]:
    """in flight or empty grid wordings still visible in the page text"""
    lowered = (text or "").lower()
    return [marker for marker in EMPTY_TABLE_MARKERS if marker in lowered]


def detect_modal(text: str) -> dict[str, object] | None:
    """an unexpected dialog in the text, classified informational/consequential"""
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
    """a session expiry *modal* (no redirect) session handling without navigation"""
    lowered = (text or "").lower()
    return next((m for m in SESSION_MODAL_MARKERS if m in lowered), None)


def is_portal_home(url: str) -> bool:
    """whether the url is the aca landing page (record context absent)"""
    lowered = (url or "").lower()
    if not lowered:
        return False
    if "capdetail" in lowered or "caphome" in lowered or "capedit" in lowered:
        return False
    return any(marker in lowered for marker in PORTAL_HOME_URL_MARKERS)


def looks_like_new_tab(url: str) -> bool:
    """whether the url looks like a target aca opened in its own tab/window"""
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
    """the portal weirdness findings for one observation, in stable order"""
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
