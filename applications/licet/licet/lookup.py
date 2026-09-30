"""deterministic phase 2 permit discovery primitives"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable
from html.parser import HTMLParser

from licet.browser import accela
from licet.schema.permit import Permit
from pydantic import BaseModel, ConfigDict, Field, model_validator


class LookupStatus(str, Enum):
    CANDIDATE = "CANDIDATE"
    FOUND = "FOUND"
    FAILED = "FAILED"
    INCOMPLETE = "INCOMPLETE"
    NOT_FOUND = "NOT_FOUND"
    AMBIGUOUS = "AMBIGUOUS"
    INVALID = "INVALID"


class LookupErrorCode(str, Enum):
    RECORD_NOT_FOUND = "record_not_found"
    AMBIGUOUS_RECORD = "ambiguous_record"
    RECORD_MISMATCH = "record_mismatch"
    INVALID_LOOKUP_INPUT = "invalid_lookup_input"
    SEARCH_FORM_FAILED = "search_form_failed"
    SEARCH_RESULTS_PARSE_FAILED = "search_results_parse_failed"
    TOO_MANY_RESULTS = "too_many_results"
    INCOMPLETE_RESULTS = "incomplete_results"
    IDENTITY_UNVERIFIED = "identity_unverified"
    BROWSER_ACTION_UNCERTAIN = "browser_action_uncertain"
    RECORD_OPEN_FAILED = "record_open_failed"


class LookupMethod(str, Enum):
    RECORD_NUMBER = "record_number"
    PARCEL = "parcel_number"
    FULL_ADDRESS = "full_address"
    PARTIAL_ADDRESS = "partial_address"
    APPLICANT = "applicant_name"


WEIGHT_RECORD_NUMBER = 1.0
WEIGHT_PARCEL = 0.8
WEIGHT_STREET_NUMBER = 0.4
WEIGHT_STREET_NAME = 0.4
WEIGHT_ZIP = 0.2
WEIGHT_PERMIT_TYPE = 0.2
WEIGHT_APPLICANT = 0.15

DEFAULT_MIN_CONFIDENCE = 0.75
DEFAULT_AMBIGUITY_MARGIN = 0.10
DEFAULT_MAX_CANDIDATES = 50

STRONG_IDENTITY_REASONS = frozenset({"exact record number"})

# applicant/contact is not a property identity: a name is not unique and the portal may return several
# records for one person
APPLICANT_UNIQUE_CONFIDENCE = 0.80


class ConfidenceBand(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


def confidence_band(
    score: float,
    reasons: Iterable[str] = (),
    *,
    min_confidence: float = DEFAULT_MIN_CONFIDENCE,
) -> ConfidenceBand:
    """map a score plus its evidence onto the high/medium/low vocabulary"""
    if any(reason in STRONG_IDENTITY_REASONS for reason in reasons):
        return ConfidenceBand.HIGH
    return ConfidenceBand.MEDIUM if score >= min_confidence else ConfidenceBand.LOW


class PermitLookupRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    record_number: str | None = None
    street_number: str | None = None
    street_name: str | None = None
    zip_code: str | None = None
    parcel_number: str | None = None
    applicant_name: str | None = None
    permit_type: str | None = None
    unit: str | None = None
    city: str | None = None
    state: str | None = None
    status: str | None = None
    raw_text: str | None = None
    source_spans: dict[str, list[tuple[int, int]]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def at_least_one_lookup_key(self) -> "PermitLookupRequest":
        if not any(getattr(self, name) for name in LOOKUP_FIELDS):
            raise ValueError("lookup request must contain at least one supported field")
        return self


LOOKUP_FIELDS = ("record_number", "street_number", "street_name", "zip_code",
                 "parcel_number", "applicant_name", "permit_type", "unit", "city", "state", "status")


class MatchState(str, Enum):
    MATCH = "MATCH"
    CONTRADICTION = "CONTRADICTION"
    UNKNOWN = "UNKNOWN"


class SearchResult(BaseModel):
    record_number: str
    record_type: str | None = None
    address: str | None = None
    status: str | None = None
    applicant: str | None = None
    parcel_number: str | None = None
    href_or_target: str | None = None
    row_id: str | None = None
    score: float = 0.0
    match_reasons: list[str] = Field(default_factory=list)
    constraints: dict[str, MatchState] = Field(default_factory=dict)
    source_page: int = 1
    source_url: str | None = None
    evidence: dict[str, Any] = Field(default_factory=dict)

    @property
    def identity_key(self) -> str | None:
        ref = accela.parse_ref_from_url(self.href_or_target or "")
        if ref:
            return "/".join(ref[k].casefold() for k in
                            ("agency_code", "module", "capID1", "capID2", "capID3"))
        return None

    @property
    def normalized_record_number(self) -> str:
        return compact(self.record_number)


class LookupResult(BaseModel):
    status: LookupStatus
    confidence: float = 0.0
    matches: list[SearchResult] = Field(default_factory=list)
    selected: SearchResult | None = None
    error_code: LookupErrorCode | None = None
    message: str | None = None
    method: LookupMethod | None = None
    attempts: int = 0
    permit: Permit | None = None
    identity_verified: bool = False
    evidence: dict[str, Any] = Field(default_factory=dict)
    results_complete: bool = True
    browser_error: dict[str, Any] | None = None

    @model_validator(mode="after")
    def consistent_selection(self) -> "LookupResult":
        selected_status = self.status in {LookupStatus.CANDIDATE, LookupStatus.FOUND}
        if selected_status != (self.selected is not None):
            raise ValueError("only CANDIDATE and FOUND results require a selection")
        if self.status is LookupStatus.FOUND and (not self.identity_verified or self.permit is None):
            raise ValueError("FOUND requires a verified permit")
        if self.status is not LookupStatus.FOUND and (self.identity_verified or self.permit is not None):
            raise ValueError("unverified results cannot publish an active permit")
        return self

    @property
    def band(self) -> ConfidenceBand:
        """high/medium/low for the evidence behind this result"""
        reasons = self.selected.match_reasons if self.selected else ()
        return confidence_band(self.confidence, reasons)


class CandidateSet(BaseModel):
    rows: list[SearchResult] = Field(default_factory=list)
    complete: bool = False
    pages_scanned: int = 0
    total: int | None = None
    reason: str | None = None
    source_url: str | None = None


class CurrentPermitState(BaseModel):
    permit: Permit
    lookup_method: LookupMethod
    lookup_confidence: float
    source_query: PermitLookupRequest
    search_results_seen: int = 0


@dataclass(frozen=True)
class SearchAttempt:
    method: LookupMethod
    fields: dict[str, str]
    reason: str
    # set on the retry built by `empty_result_retry`: the same query with the search date window widened,
    # because an agency pre filled window (ni: 09/18/2024→09/18/2026) can hide records that a wider search
    # finds
    widen_dates: bool = False


_SEARCH_MODE_BY_METHOD: dict[LookupMethod, str | None] = {
    LookupMethod.RECORD_NUMBER: None,
    LookupMethod.FULL_ADDRESS: "address",
    LookupMethod.PARTIAL_ADDRESS: "address",
    LookupMethod.PARCEL: "parcel",
    LookupMethod.APPLICANT: "applicant",
}


def _type_action(target: str, text: str) -> dict[str, Any]:
    return {"name": "type", "args": {"target": target, "text": text, "intent": "search_records"}}


def search_actions(attempt: SearchAttempt) -> list[dict[str, Any]]:
    """translate a planned attempt into dispatcher calls, without browser i/o"""
    actions: list[dict[str, Any]] = []
    mode = _SEARCH_MODE_BY_METHOD[attempt.method]
    if mode:
        actions.append({"name": "select", "args": {"target": f"#{accela.SEARCH_MODE_DROPDOWN}", "value": mode, "intent": "search_records"}})
    if attempt.widen_dates:
        actions.append(_type_action(f'input[id$="{accela.SEARCH_DATE_START_SUFFIX}"]', accela.SEARCH_DATE_START_WIDENED))
    if attempt.method is LookupMethod.RECORD_NUMBER:
        actions.append(_type_action('input[id$="txtGSPermitNumber"]', attempt.fields["record_number"]))
    elif attempt.method in {LookupMethod.FULL_ADDRESS, LookupMethod.PARTIAL_ADDRESS}:
        if attempt.fields.get("street_number"):
            for bound in ("ChildControl0", "ChildControl1"):
                actions.append(_type_action(
                    f'input[id$="txtAPO_Search_by_Address_StreetNumber_{bound}"], '
                    f'input[id$="txtGSNumber_{bound}"]',
                    attempt.fields["street_number"],
                ))
        if attempt.fields.get("street_name"):
            actions.append(_type_action(
                'input[id$="txtAPO_Search_by_Address_StreetName"], input[id$="txtGSStreetName"]',
                street_name_search_form_value(attempt.fields["street_name"]),
            ))
        if attempt.fields.get("zip_code"):
            actions.append(_type_action(
                'input[id$="txtAPO_Search_by_Address_Zip"], input[id$="txtGSZip"]',
                attempt.fields["zip_code"],
            ))
    elif attempt.method is LookupMethod.PARCEL:
        actions.append(_type_action(
            'input[id$="txtGSParcelNo"], input[id$="txtAPO_Search_by_Parcel_ParcelNumber"]',
            attempt.fields["parcel_number"],
        ))
    elif attempt.method is LookupMethod.APPLICANT:
        actions.append(_type_action(
            'input[id$="txtGSBusiName"], input[id$="txtGSLastName"]',
            attempt.fields["applicant_name"],
        ))
    actions.append({"name": "click", "args": {"target": accela.SEARCH_BUTTON_TEXT, "by": "text", "intent": "search_records"}})
    return actions


_ORDINAL_RE = re.compile(r"\b(\d+)(?:st|nd|rd|th)\b", re.I)


def street_name_search_form_value(value: str | None) -> str:
    """aca's street name field wants digits only for numbered streets (`72nd` → `72`, per the recorded ui map)"""
    return _ORDINAL_RE.sub(r"\1", normalize_street_name(value))


def empty_result_retry(attempt: SearchAttempt) -> SearchAttempt:
    """the bounded reformulation for a search that executed and returned zero rows: same fields, but widen the pre filled date window"""
    return SearchAttempt(attempt.method, dict(attempt.fields), "widen pre-filled date window", widen_dates=True)


@dataclass
class LookupMetrics:
    attempts: int = 0
    successful: int = 0
    exact_matches: int = 0
    ambiguous: int = 0
    wrong_records: int = 0
    browser_actions: int = 0
    retries: int = 0

    @property
    def success_rate(self) -> float:
        return self.successful / self.attempts if self.attempts else 0.0

    @property
    def exact_match_accuracy(self) -> float:
        """share of successes that came from an exact identity match"""
        return self.exact_matches / self.successful if self.successful else 0.0

    @property
    def ambiguity_detection_rate(self) -> float:
        return self.ambiguous / self.attempts if self.attempts else 0.0

    @property
    def wrong_record_rate(self) -> float:
        """the number that must be 0: selected records that were not the target"""
        return self.wrong_records / self.attempts if self.attempts else 0.0

    @property
    def search_retry_rate(self) -> float:
        return self.retries / self.attempts if self.attempts else 0.0

    @property
    def average_browser_actions(self) -> float:
        return self.browser_actions / self.attempts if self.attempts else 0.0

    def merge(self, other: "LookupMetrics") -> "LookupMetrics":
        """fold another lookup's counters into this one, returning self"""
        self.attempts += other.attempts
        self.successful += other.successful
        self.exact_matches += other.exact_matches
        self.ambiguous += other.ambiguous
        self.wrong_records += other.wrong_records
        self.browser_actions += other.browser_actions
        self.retries += other.retries
        return self

    @classmethod
    def combine(cls, metrics: Iterable["LookupMetrics"]) -> "LookupMetrics":
        """one aggregate over any number of per lookup metrics"""
        total = cls()
        for item in metrics:
            total.merge(item)
        return total

    def as_dict(self) -> dict[str, Any]:
        """counters plus derived kpis, json serializable for run logs"""
        return {
            "attempts": self.attempts,
            "successful": self.successful,
            "exact_matches": self.exact_matches,
            "ambiguous": self.ambiguous,
            "wrong_records": self.wrong_records,
            "browser_actions": self.browser_actions,
            "retries": self.retries,
            "success_rate": self.success_rate,
            "exact_match_accuracy": self.exact_match_accuracy,
            "ambiguity_detection_rate": self.ambiguity_detection_rate,
            "wrong_record_rate": self.wrong_record_rate,
            "search_retry_rate": self.search_retry_rate,
            "average_browser_actions": self.average_browser_actions,
        }


@dataclass
class LookupTrace:
    goal: str
    parsed: PermitLookupRequest | None = None
    attempts: list[SearchAttempt] = field(default_factory=list)
    result: LookupResult | None = None

    def lines(self) -> list[str]:
        lines = ["GOAL", self.goal]
        if self.parsed:
            lines += ["PARSED", _request_summary(self.parsed)]
        for attempt in self.attempts:
            lines += ["SEARCH", f"{attempt.method.value}: {attempt.fields}"]
        if self.result:
            lines += [
                "RESULT",
                f"{self.result.status.value} confidence={self.result.confidence:.2f} "
                f"band={self.result.band.value}",
            ]
            for match in self.result.matches:
                lines.append(f"- {match.record_number} score={match.score:.2f}")
        return lines


_STREET_ABBREVIATIONS = {
    "street": "st", "st": "st", "st.": "st",
    "road": "rd", "rd": "rd", "rd.": "rd",
    "avenue": "ave", "ave": "ave", "ave.": "ave",
    "boulevard": "blvd", "blvd": "blvd", "blvd.": "blvd",
    "drive": "dr", "dr": "dr", "dr.": "dr",
    "lane": "ln", "ln": "ln", "ln.": "ln",
    "court": "ct", "ct": "ct", "ct.": "ct",
    "place": "pl", "pl": "pl", "pl.": "pl",
    "highway": "hwy", "hwy": "hwy", "hwy.": "hwy",
    "parkway": "pkwy", "pkwy": "pkwy", "pkwy.": "pkwy",
}
_DIRECTIONALS = {"north": "n", "south": "s", "east": "e", "west": "w", "n": "n", "s": "s", "e": "e", "w": "w"}


def normalize_whitespace(value: str | None) -> str:
    return re.sub(r"\s+", " ", (value or "").replace("\u00a0", " ")).strip()


def normalize_record_number(value: str | None) -> str:
    return normalize_whitespace(value).upper()


def compact(value: str | None) -> str:
    return re.sub(r"[^A-Z0-9]", "", (value or "").upper())


def normalize_street_name(value: str | None) -> str:
    """case, whitespace, directionals, suffixes and numeric ordinals"""
    words = normalize_whitespace(value).lower().replace(",", "").split()
    normalized = [
        _ORDINAL_RE.sub(r"\1", _DIRECTIONALS.get(word, _STREET_ABBREVIATIONS.get(word, word)))
        for word in words
    ]
    return " ".join(normalized)


def normalize_zip(value: str | None) -> str:
    raw = normalize_whitespace(value)
    if not raw:
        return ""
    if not re.fullmatch(r"\d{5}(?:[- ]?\d{4})?", raw):
        raise ValueError("invalid_lookup_input: ZIP must contain five or nine digits")
    digits = re.sub(r"\D", "", raw)
    return digits[:5] + ("-" + digits[5:] if len(digits) == 9 else "")


def normalize_parcel(value: str | None) -> str:
    return compact(value)


def normalize_person(value: str | None) -> str:
    return normalize_whitespace(value).casefold()


_PERMIT_TYPE_CANONICAL = {
    "commercial alteration": "Commercial Alteration",
    "commercial electrical": "Commercial Electrical",
    "residential addition": "Residential Addition",
    "new single family residence": "New Single Family Residence",
    "solar permit": "Solar Permit",
    "right of way permit": "Right of Way Use Permit",
    "right of way use permit": "Right of Way Use Permit",
    "sign - temporary": "Sign - Temporary",
    "sign temporary": "Sign - Temporary",
}


def normalize_permit_type(value: str | None) -> str:
    text = normalize_whitespace(value)
    return _PERMIT_TYPE_CANONICAL.get(text.casefold(), text)


@dataclass(frozen=True)
class AddressParts:
    street_number: str | None = None
    street_name: str | None = None
    unit: str | None = None
    city: str | None = None
    state: str | None = None
    zip_code: str | None = None


_UNIT_RE = re.compile(r"(?:\s+#\s*|\s+\b(?:apt|apartment|unit|ste|suite|bldg|building|fl|floor|rm|room)\.?\s+)([\w-]+)\b", re.I)
_HOUSE_RE = re.compile(r"^(\d+[A-Za-z]?(?:-\d+[A-Za-z]?)?(?:\s+\d+/\d+)?)\s+(.+)$")
_STREET_SUFFIXES = frozenset(_STREET_ABBREVIATIONS.values())


def address_parts(value: str | None) -> AddressParts:
    """parse components; a unit/zip can never become a house number match"""
    text = normalize_whitespace(value)
    if not text:
        return AddressParts()
    text = re.sub(r",?\s+United States(?: of America)?$", "", text, flags=re.I)
    zip_match = re.search(r"(?:\s|,)(\d{5}(?:-\d{4})?)$", text)
    zip_code = normalize_zip(zip_match.group(1)) if zip_match else None
    if zip_match:
        text = text[:zip_match.start()].rstrip(" ,")
    pieces = [v.strip() for v in text.split(",") if v.strip()]
    street = pieces[0] if pieces else ""
    city = state = None
    if len(pieces) > 1:
        locality = " ".join(pieces[1:])
        state_match = re.search(r"(?:^|\s)([A-Z]{2})$", locality)
        if state_match:
            state = state_match.group(1)
            locality = locality[:state_match.start()].strip()
        city = normalize_person(locality) or None
    unit_match = _UNIT_RE.search(street)
    unit = normalize_person(unit_match.group(1)) if unit_match else None
    if unit_match:
        street = street[:unit_match.start()] + street[unit_match.end():]
    house = _HOUSE_RE.match(street.strip())
    return AddressParts(
        street_number=normalize_whitespace(house.group(1)).lower() if house else None,
        street_name=normalize_street_name(house.group(2) if house else street) or None,
        unit=unit, city=city, state=state, zip_code=zip_code,
    )


def strip_unit_suffix(value: str | None) -> str:
    """compatibility helper for form values only; never discard semantic units"""
    return address_parts(value).street_name or ""


def normalize_request(request: PermitLookupRequest) -> PermitLookupRequest:
    parts = address_parts(request.street_name)
    unit = normalize_person(request.unit) or parts.unit
    if request.unit and parts.unit and normalize_person(request.unit) != parts.unit:
        raise ValueError("invalid_lookup_input: conflicting units")
    return PermitLookupRequest(
        record_number=normalize_record_number(request.record_number) or None,
        street_number=normalize_whitespace(request.street_number).lower() or None,
        street_name=normalize_street_name(_UNIT_RE.sub("", request.street_name or "")) or None,
        zip_code=normalize_zip(request.zip_code) or None,
        parcel_number=normalize_parcel(request.parcel_number) or None,
        applicant_name=normalize_person(request.applicant_name) or None,
        permit_type=normalize_permit_type(request.permit_type) or None,
        unit=unit, city=normalize_person(request.city) or None,
        state=normalize_whitespace(request.state).upper() or None,
        status=normalize_person(request.status) or None,
        raw_text=request.raw_text, source_spans=request.source_spans,
    )


# where an extracted address ends
_ADDRESS_END = (r"(?=\s+(?:zip|parcel|permit|record|for|applicant|contact|"
                r"and\s+(?:at|on|address))\b|\s*[—–:;]|\s*\(|$)")

_RECORD_RE = re.compile(r"\b[A-Z]{2,8}-?\d+[A-Z0-9]*(?:-[A-Z0-9]+)*\b", re.I)
_PARCEL_RE = re.compile(r"\bparcel(?:\s+(?:number|no\.?))?\s*[:#]?\s*([A-Z0-9]+(?:\s*[-/.]\s*[A-Z0-9]+)*)", re.I)
_TYPE_RE = re.compile(r"\b(commercial alteration|commercial electrical|residential addition|new single family residence|solar permit|right of way(?: use)? permit|sign\s*[- ]\s*temporary)\b", re.I)
_APPLICANT_RE = re.compile(r"\b(?:applicant|contact)\s*[:=]?\s+(.+?)(?=\s+(?:at|on|parcel|permit|record|zip)\b|$)", re.I)


def parse_lookup_request(text: str) -> PermitLookupRequest:
    """extract non overlapping typed spans, rejecting unsupported composition"""
    raw = text
    if re.search(r"\b(?:not|except|excluding|instead|or|rather than)\b", raw, re.I):
        raise ValueError("invalid_lookup_input: clarify exclusions or alternatives")
    if re.search(r"\b(?:this|that|same)\s+(?:address|property|record|permit)\b", raw, re.I):
        raise ValueError("invalid_lookup_input: resolve the referenced property or record")
    if re.search(r"\b(?:latest|newest|oldest|recent|open)\b", raw, re.I):
        raise ValueError("invalid_lookup_input: temporal/open filters require an explicit definition")
    values: dict[str, Any] = {}
    spans: dict[str, list[tuple[int, int]]] = {}
    occupied: list[tuple[int, int]] = []

    def free(start, end):
        return not any(start < b and end > a for a, b in occupied)

    def assign(field, value, start, end):
        if field in values and normalize_person(str(values[field])) != normalize_person(value):
            raise ValueError(f"invalid_lookup_input: multiple {field} values require clarification")
        values[field] = value
        spans.setdefault(field, []).append((start, end))
        occupied.append((start, end))

    for match in _PARCEL_RE.finditer(raw):
        if not any(c.isdigit() for c in match.group(1)):
            raise ValueError("invalid_lookup_input: incomplete parcel identifier")
        assign("parcel_number", re.sub(r"\s+", "", match.group(1)), *match.span(1))
    for match in re.finditer(r"\bzip(?:\s+code)?\s*[:#]?\s*([\d -]+)", raw, re.I):
        assign("zip_code", normalize_zip(match.group(1).strip()), *match.span(1))
    labeled = re.compile(r"\b(?:permit|record)(?:\s+(?:number|no\.?))?\s*[:#]?\s+([A-Z]{1,8}-?\d+[A-Z0-9-]*|\d+)\b", re.I)
    for pattern in (labeled, _RECORD_RE):
        for match in pattern.finditer(raw):
            group = 1 if pattern is labeled else 0
            if free(*match.span(group)):
                assign("record_number", normalize_record_number(match.group(group)), *match.span(group))
    if re.fullmatch(r"\s*\d{6,}\s*", raw) and not values:
        match = re.search(r"\d+", raw)
        assign("record_number", match.group(), *match.span())
    for match in _TYPE_RE.finditer(raw):
        assign("permit_type", normalize_permit_type(match.group(1)), *match.span(1))
    for match in _APPLICANT_RE.finditer(raw):
        assign("applicant_name", normalize_person(match.group(1)), *match.span(1))
    for match in re.finditer(r"\b(issued|expired|submitted|approved|cancelled|canceled|closed|in review)\b", raw, re.I):
        if free(*match.span()):
            assign("status", normalize_person(match.group()), *match.span())

    address_pattern = re.compile(
        r"\b(?:at|on|for|address(?:ed)?(?:\s+at)?)\s+(\d+[A-Za-z]?(?:-\d+)?(?:\s+\d+/\d+)?\s+.+?)"
        + _ADDRESS_END, re.I)
    addresses = list(address_pattern.finditer(raw))
    if not addresses:
        # "look up 123 main street", "find 123 main avenue" (discovery 002/004 wording family): no
        # at/on/for/address keyword introduces the address, so a bare lookup verb followed by a
        # house number span is the address
        addresses = list(re.finditer(r"\b(?:look\s*up|find|locate|search(?:\s+for)?)\s+(\d+[A-Za-z]?(?:-\d+)?(?:\s+\d+/\d+)?\s+[A-Za-z][A-Za-z0-9 .,'#/-]+?)" + _ADDRESS_END, raw, re.I))
    if not addresses:
        addresses = list(re.finditer(r"\b(?:on|at)\s+([A-Za-z][A-Za-z0-9 .,'#/-]+?)" + _ADDRESS_END, raw, re.I))
    if len(addresses) > 1:
        raise ValueError("invalid_lookup_input: multiple addresses require clarification")
    if addresses:
        match = addresses[0]
        if free(*match.span(1)):
            # sentence punctuation after a terminal address must not enter the street field: "123 main
            # street?" normalized to "main street?" and then matched nothing (prompt discovery 002 p029)
            parts = address_parts(match.group(1).rstrip(" .?!"))
            for field in ("street_number", "street_name", "unit", "city", "state", "zip_code"):
                value = getattr(parts, field)
                if value:
                    assign(field, value, *match.span(1))
    if not any(values.values()):
        raise ValueError("invalid_lookup_input: no supported lookup fields found")
    request = normalize_request(PermitLookupRequest(**values, raw_text=raw, source_spans=spans))
    choose_search_strategy(request)  # zip/type alone is not executable in this adapter
    return request


def choose_search_strategy(request: PermitLookupRequest) -> LookupMethod:
    request = normalize_request(request)
    if request.record_number:
        return LookupMethod.RECORD_NUMBER
    if request.parcel_number:
        return LookupMethod.PARCEL
    if request.street_number and request.street_name:
        return LookupMethod.FULL_ADDRESS
    if request.street_name:
        return LookupMethod.PARTIAL_ADDRESS
    if request.applicant_name:
        return LookupMethod.APPLICANT
    raise ValueError(LookupErrorCode.INVALID_LOOKUP_INPUT.value)


def build_search_plan(request: PermitLookupRequest, max_attempts: int = 3) -> list[SearchAttempt]:
    """build a narrow to broad bounded plan; never broaden indefinitely"""
    request = normalize_request(request)
    method = choose_search_strategy(request)
    attempts: list[SearchAttempt] = []
    if method is LookupMethod.RECORD_NUMBER:
        attempts.append(SearchAttempt(method, {"record_number": request.record_number or ""}, "exact identity"))
    elif method is LookupMethod.PARCEL:
        attempts.append(SearchAttempt(method, {"parcel_number": request.parcel_number or ""}, "exact parcel"))
    elif method is LookupMethod.FULL_ADDRESS:
        base = {"street_number": request.street_number or "", "street_name": request.street_name or ""}
        full = dict(base)
        if request.zip_code:
            full["zip_code"] = request.zip_code
        if request.permit_type:
            full["permit_type"] = request.permit_type
        attempts.append(SearchAttempt(method, full, "full address"))
        # fewer fields before fewer words
        if full != base:
            attempts.append(SearchAttempt(method, dict(base), "drop optional constraints"))
        attempts.append(SearchAttempt(LookupMethod.PARTIAL_ADDRESS, {"street_name": request.street_name or ""}, "bounded street fallback"))
    elif method is LookupMethod.PARTIAL_ADDRESS:
        attempts.append(SearchAttempt(method, {"street_name": request.street_name or ""}, "street name"))
    else:
        attempts.append(SearchAttempt(method, {"applicant_name": request.applicant_name or ""}, "applicant/contact"))
    return attempts[: max(1, max_attempts)]


def _request_summary(request: PermitLookupRequest) -> str:
    values = request.model_dump(exclude_none=True)
    return ", ".join(f"{key}: {value}" for key, value in values.items()) or "(empty)"


def _contains_token_sequence(haystack: str, needle: str) -> bool:
    """contiguous whole token containment"""
    hay = normalize_street_name(haystack).split()
    need = normalize_street_name(needle).split()
    if not need:
        return False
    return any(hay[i : i + len(need)] == need for i in range(len(hay) - len(need) + 1))


def _token_subset(small: str, large: str) -> bool:
    """every token of `small` appears in `large` (order independent)"""
    small_tokens = normalize_person(small).replace(",", " ").split()
    large_tokens = set(normalize_person(large).replace(",", " ").split())
    return bool(small_tokens) and set(small_tokens) <= large_tokens


def _type_matches(expected: str | None, actual: str | None) -> bool:
    """permit type equality tolerant of the portal's longer labels"""
    if not expected or not actual:
        return False
    # a trailing generic word is an explicit harmless label variant; arbitrary token subsets ("commercial"
    # vs "commercial alteration") are not equality
    canonical = lambda value: re.sub(r"\s+permits?$", "", normalize_person(value))
    return canonical(expected) == canonical(actual)


def _applicant_matches(expected: str | None, actual: str | None) -> bool:
    if not expected or not actual:
        return False
    expected_text, actual_text = normalize_person(expected), normalize_person(actual)
    if expected_text == actual_text:
        return True
    return sorted(expected_text.replace(",", " ").split()) == sorted(
        actual_text.replace(",", " ").split()
    )


def _applicant_only_request(request: PermitLookupRequest) -> bool:
    """true when the request has no identity/address key to fall back on"""
    return bool(request.applicant_name) and not any(
        (
            request.record_number,
            request.parcel_number,
            request.street_number,
            request.street_name,
            request.zip_code,
        )
    )


def _zip_codes_in(address: str | None) -> set[str]:
    """zip codes rendered as digit boundary tokens in an address"""
    return set(re.findall(r"(?<!\d)(\d{5})(?:-\d{4})?(?!\d)", address or ""))


def _dedupe(results: Iterable[SearchResult]) -> list[SearchResult]:
    rows: dict[tuple[Any, ...], SearchResult] = {}
    for original in results:
        key = (original.identity_key, normalize_record_number(original.record_number),
               normalize_person(original.record_type), normalize_street_name(original.address),
               normalize_parcel(original.parcel_number), normalize_person(original.applicant),
               normalize_person(original.status))
        rows.setdefault(key, original.model_copy(deep=True))
    return list(rows.values())


def record_numbers_match(expected: str, actual: str) -> bool:
    expected, actual = normalize_record_number(expected), normalize_record_number(actual)
    # preserve boundaries when both sides provide them
    if "-" in expected and "-" in actual:
        return expected == actual
    return bool(expected and actual) and compact(expected) == compact(actual)


def _street_matches(expected: str, actual: str) -> bool:
    need, got = normalize_street_name(expected).split(), normalize_street_name(actual).split()
    if need == got:
        return True
    return bool(need and got) and not any(w in _STREET_SUFFIXES for w in need) and got[:len(need)] == need


def match_constraints(request: PermitLookupRequest, row: SearchResult) -> dict[str, MatchState]:
    request = normalize_request(request)
    address = address_parts(row.address)
    actual = {"record_number": row.record_number, "permit_type": row.record_type,
              "parcel_number": row.parcel_number, "applicant_name": row.applicant,
              "status": row.status, **vars(address)}
    comparison = {
        "record_number": record_numbers_match, "permit_type": _type_matches,
        "parcel_number": lambda a, b: normalize_parcel(a) == normalize_parcel(b),
        "applicant_name": _applicant_matches, "street_name": _street_matches,
        "zip_code": lambda a, b: normalize_zip(a) == normalize_zip(b) if len(normalize_zip(a)) > 5 else normalize_zip(a) == normalize_zip(b)[:5],
    }
    result = {}
    for key in LOOKUP_FIELDS:
        wanted = getattr(request, key)
        if not wanted:
            continue
        observed = actual.get(key)
        if not observed:
            result[key] = MatchState.UNKNOWN
        else:
            matches = comparison.get(key, lambda a, b: normalize_person(a) == normalize_person(b))(wanted, observed)
            result[key] = MatchState.MATCH if matches else MatchState.CONTRADICTION
    return result


def rank_results(request: PermitLookupRequest, results: Iterable[SearchResult]) -> list[SearchResult]:
    """score candidates using identity first, then contextual evidence"""
    request = normalize_request(request)
    ranked: list[SearchResult] = []
    for original in _dedupe(results):
        result = original.model_copy(deep=True)
        score = 0.0
        reasons: list[str] = []
        if request.record_number and record_numbers_match(request.record_number, result.record_number):
            score += WEIGHT_RECORD_NUMBER; reasons.append("exact record number")
        if (
            request.parcel_number
            and result.parcel_number
            and normalize_parcel(request.parcel_number) == normalize_parcel(result.parcel_number)
        ):
            score += WEIGHT_PARCEL; reasons.append("exact parcel")
        components = address_parts(result.address)
        result.constraints = match_constraints(request, result)
        if request.street_number and result.address and request.street_number.lower() == components.street_number:
            score += WEIGHT_STREET_NUMBER; reasons.append("street number")
        if request.street_name and result.address and bool(components.street_name) and _street_matches(request.street_name, components.street_name):
            score += WEIGHT_STREET_NAME; reasons.append("street name")
        if request.zip_code and result.address and request.zip_code[:5] in _zip_codes_in(result.address):
            score += WEIGHT_ZIP; reasons.append("ZIP")
        if _type_matches(request.permit_type, result.record_type):
            score += WEIGHT_PERMIT_TYPE; reasons.append("permit type")
        if _applicant_matches(request.applicant_name, result.applicant):
            score += WEIGHT_APPLICANT; reasons.append("applicant")
        if _applicant_only_request(request) and "applicant" in reasons:
            score = max(score, APPLICANT_UNIQUE_CONFIDENCE)
        result.score = min(score, 1.0)
        result.match_reasons = reasons
        ranked.append(result)
    # deterministic: ties order by the sort key, so the same query yields the same ranking on every run
    # (phase 2 repeated run requirement)
    return sorted(
        ranked,
        key=lambda item: (-item.score, item.record_number, item.record_type or "", item.address or ""),
    )


def resolve_lookup(
    request: PermitLookupRequest, results: Iterable[SearchResult], *,
    min_confidence: float = DEFAULT_MIN_CONFIDENCE,
    ambiguity_margin: float = DEFAULT_AMBIGUITY_MARGIN,
    max_candidates: int = DEFAULT_MAX_CANDIDATES,
    method: LookupMethod | None = None, attempts: int = 1,
    complete: bool = True,
) -> LookupResult:
    """return a provisional candidate only after all constraints are supported"""
    if not 0 <= min_confidence <= 1 or ambiguity_margin < 0 or max_candidates < 1:
        raise ValueError("invalid lookup thresholds")
    request = normalize_request(request)
    ranked = rank_results(request, results)
    common = dict(matches=ranked, method=method or choose_search_strategy(request), attempts=attempts,
                  results_complete=complete)
    if not complete:
        return LookupResult(status=LookupStatus.INCOMPLETE, error_code=LookupErrorCode.INCOMPLETE_RESULTS,
                            message="candidate coverage is incomplete", **common)
    if not ranked:
        return LookupResult(status=LookupStatus.NOT_FOUND, error_code=LookupErrorCode.RECORD_NOT_FOUND,
                            message="search executed and returned no records", **common)
    eligible = [r for r in ranked if MatchState.CONTRADICTION not in r.constraints.values()]
    confidence = eligible[0].score if eligible else 0.0
    if len(ranked) > max_candidates:
        common["results_complete"] = False
        return LookupResult(status=LookupStatus.INCOMPLETE, confidence=confidence,
                            error_code=LookupErrorCode.TOO_MANY_RESULTS, message="candidate limit exceeded", **common)
    if len(eligible) != 1 or MatchState.UNKNOWN in eligible[0].constraints.values() or eligible[0].score < min_confidence:
        return LookupResult(status=LookupStatus.AMBIGUOUS, confidence=confidence,
                            error_code=LookupErrorCode.AMBIGUOUS_RECORD,
                            message="explicit constraints conflict, remain unknown, or do not identify one sufficiently supported record", **common)
    return LookupResult(status=LookupStatus.CANDIDATE, confidence=eligible[0].score,
                        selected=eligible[0], **common)


def verify_record_identity(expected: str, observed: str, *, expected_address: str | None = None, observed_address: str | None = None, expected_type: str | None = None, observed_type: str | None = None) -> tuple[bool, LookupErrorCode | None, str]:
    if not record_numbers_match(expected, observed):
        return False, LookupErrorCode.RECORD_MISMATCH, f"expected record {expected}, observed {observed}"
    if expected_address and (not observed_address or address_parts(expected_address) != address_parts(observed_address)):
        return False, LookupErrorCode.RECORD_MISMATCH, "record number matched but address differed"
    if expected_type and (not observed_type or not _type_matches(expected_type, observed_type)):
        return False, LookupErrorCode.RECORD_MISMATCH, "record number matched but permit type differed"
    return True, None, "record identity verified"


# lightweight captured html table parsing
_TAG_RE = re.compile(r"<[^>]+>")
_ROW_RE = re.compile(r"<tr\b([^>]*)>(.*?)</tr>", re.I | re.S)
_CELL_RE = re.compile(r"<t[dh]\b([^>]*)>(.*?)</t[dh]>", re.I | re.S)
_HREF_RE = re.compile(r"\bhref\s*=\s*(['\"])(.*?)\1", re.I | re.S)
_ONCLICK_TARGET_RE = re.compile(r"(?:capID1=|__doPostBack\([^,]+,\s*['\"])([^'\"<>]+)", re.I)


def _clean(fragment: str) -> str:
    return normalize_whitespace(html.unescape(_TAG_RE.sub(" ", fragment or "")))


class _ResultTables(HTMLParser):
    """keep row/cell boundaries and links scoped to their owning table"""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.tables = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "table":
            self.stack.append({"rows": [], "row": None, "cell": None})
        if not self.stack:
            return
        table = self.stack[-1]
        if tag == "tr":
            table["row"] = {"id": attrs.get("id"), "cells": []}
        elif tag in {"td", "th"} and table["row"] is not None:
            table["cell"] = {"text": [], "href": None}
        elif tag == "a" and table["cell"] is not None:
            table["cell"]["href"] = attrs.get("href")
        elif tag == "br" and table["cell"] is not None:
            table["cell"]["text"].append(" ")

    def handle_data(self, text):
        if self.stack and self.stack[-1]["cell"] is not None:
            self.stack[-1]["cell"]["text"].append(text)

    def handle_endtag(self, tag):
        if not self.stack:
            return
        table = self.stack[-1]
        if tag in {"td", "th"} and table["cell"] is not None:
            table["row"]["cells"].append(table["cell"])
            table["cell"] = None
        elif tag == "tr" and table["row"] is not None:
            table["rows"].append(table["row"])
            table["row"] = None
        elif tag == "table":
            self.tables.append(self.stack.pop())


def parse_search_results(source: str, *, max_results: int = 200) -> tuple[list[SearchResult], dict[str, Any]]:
    """read only tables with record headers; missing coverage stays unknown"""
    parser = _ResultTables()
    parser.feed(source or "")
    parsed = []
    malformed = bool(parser.stack)
    found_table = False
    for table in parser.tables:
        headers = None
        for row in table["rows"]:
            cells = row["cells"]
            values = [normalize_whitespace("".join(c["text"])) for c in cells]
            lower = [v.casefold() for v in values]
            if any(v in {"record number", "record id", "record #"} for v in lower):
                headers, found_table = lower, True
                continue
            if headers is None:
                continue
            if len(values) != len(headers):
                if any(c["href"] and ("capdetail" in c["href"].lower()) for c in cells):
                    malformed = True
                continue
            def cell(*names):
                return next((values[headers.index(n)] or None for n in names if n in headers), None)
            number = cell("record number", "record id", "record #")
            if not number:
                malformed = True
                continue
            index = next(headers.index(n) for n in ("record number", "record id", "record #") if n in headers)
            parsed.append(SearchResult(record_number=number,
                record_type=cell("record type", "type"), address=cell("address", "location", "project address"),
                status=cell("status", "record status"), applicant=cell("applicant", "contact", "applicant/contact"),
                parcel_number=cell("parcel", "parcel number", "parcel #"),
                href_or_target=cells[index]["href"], row_id=row["id"]))
    lowered = _clean(source).casefold()
    showing = re.search(r"showing\s+(\d+)\s*[-–]\s*(\d+)\s+of\s+(\d+)", lowered)
    has_next = bool(re.search(r"\b(?:next|more results)\b", lowered)) and not bool(re.search(r"\bnext\s+disabled\b", lowered))
    truncated = len(parsed) > max_results
    metadata = {"pages_seen": 1, "has_next": has_next, "rows_seen": min(len(parsed), max_results),
                "parse_error": malformed or not found_table, "truncated": truncated, "complete": False}
    if showing:
        first, last, total = map(int, showing.groups())
        metadata.update(first=first, last=last, total=total)
        metadata["has_next"] = has_next and last < total
        metadata["parse_error"] |= last - first + 1 != len(parsed)
        metadata["complete"] = first == 1 and last == total and not (metadata["parse_error"] or truncated)
    return parsed[:max_results], metadata


def _attrs(raw: str) -> dict[str, str]:
    pairs = re.findall(r"([\w:-]+)\s*=\s*(['\"])(.*?)\2", raw or "")
    return {name.lower(): value for name, _, value in pairs}


def lookup_not_found(result: LookupResult) -> bool:
    return result.status is LookupStatus.NOT_FOUND


def classify_results_page(text: str) -> str:
    """what a post search page is showing, from its visible text alone"""
    if accela.looks_like_zero_results(text or ""):
        return "zero_results"
    if accela.has_results_table(text or ""):
        return "results"
    return "parse_failed"


def pagination_actions() -> list[dict[str, Any]]:
    """dispatcher calls that advance the result grid one page"""
    return [{"name": "click", "args": {"target": accela.PAGINATION_NEXT_TEXT, "by": "text", "intent": "search_records"}}]


def should_scan_next_page(metadata: dict[str, Any], pages_scanned: int, max_pages: int = 5) -> bool:
    """bounded pagination: scan on only when the grid declares more pages and the scan budget allows"""
    if pages_scanned >= max_pages:
        return False
    return bool(metadata.get("has_next"))
