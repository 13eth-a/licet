"""Accela page-to-observation adapter (Phase 3 extraction specialist).

The bridge between the real portal and the Phase 3 runtime: one ``read_page``
payload (the dict ``SolariClient.read_page`` assembles) becomes the section
observations ``licet.phase3.extract`` consumes. This module is deliberately
dumb about meaning — it maps *where ACA renders things* onto the observation
shape, and never invents values it did not see:

- The record-detail header (``Record <id>: <type> / Record Status: <status>``)
  becomes the overview observation.
- The detail page's Inspections section renders text only, so inspection rows
  here carry raw lines plus the wizard's own type list (offered types, with
  ACA's ``(required)`` marker preserved as requirement evidence). Table-shaped
  sections are handled by the same row contract the eval fixtures use.
- ACA's AJAX hazard is honored end to end: a section read while
  ``Loading...`` is still in the text is flagged, so downstream coverage
  degrades to ``partial`` instead of licensing a "no entries" claim
  (live finding, 2026-09-20: the Inspections section says "You have not added
  any inspections" before it finishes loading).
- The calendar and scheduling wizard are surfaced as offered types +
  availability — availability is not a requirement.
"""
from __future__ import annotations

from datetime import datetime as _datetime
import re
from typing import Any, Mapping

from licet.browser import accela

# The overview observation is keyed off ACA's own section labels, which the
# detail page renders for all 8 owned Null Island records (2026-09-20).
_SECTION_BY_LABEL: tuple[tuple[str, str], ...] = (
    ("record info", "overview"),
    ("inspections", "inspections"),
    ("fees", "fees"),
    ("payments", "fees"),
    ("attachments", "documents"),
    ("conditions", "conditions"),
)

# Rendered section tables, mapped by *meaning* rather than one hard-coded
# header string. Agencies configure their own column labels (the Phase 3
# handoff named fees grids as the first fixture gap), so a header cell is
# resolved to the canonical field ``licet.phase3.extract`` reads. Without this,
# a grid whose wording differs from the Null Island capture produced an
# "Unnamed fee" with no amount and "Unknown inspection" rows — a silent
# mis-parse rather than the honest ``partial`` coverage the handoff promised.
_HEADER_FIELDS: dict[str, dict[str, str]] = {
    "inspections": {
        "inspection": "type", "inspection type": "type", "inspection name": "type",
        "type": "type", "status": "status", "inspection status": "status",
        "lifecycle": "status", "result": "result", "outcome": "result",
        "inspection result": "result", "requested": "requested_date",
        "requested date": "requested_date", "date requested": "requested_date",
        "scheduled": "scheduled_date", "scheduled date": "scheduled_date",
        "date scheduled": "scheduled_date", "completed": "completed_date",
        "completed date": "completed_date", "date completed": "completed_date",
        "date": "date", "inspection date": "date", "inspector": "inspector",
        "inspector name": "inspector", "comments": "comments",
        "comment": "comments", "notes": "comments", "scope": "scope",
        "unit": "scope", "location": "scope",
    },
    "fees": {
        "fee": "description", "fee type": "description",
        "fee description": "description", "description": "description",
        "item": "description", "name": "description", "type": "description",
        "amount": "amount", "fee amount": "amount", "amount due": "amount",
        "charge": "amount", "charges": "amount", "amount charged": "amount",
        "fee total": "amount", "balance": "balance", "balance due": "balance",
        "outstanding": "balance", "outstanding balance": "balance",
        "current balance": "balance", "paid": "paid", "payment": "paid",
        "payment status": "paid", "status": "paid", "due": "due",
        "due date": "due_date", "due on": "due_date",
        "date": "date", "date paid": "date", "payment date": "date",
        "gate": "gate_text", "requirement": "gate_text",
    },
    "documents": {
        "name": "name", "file name": "name", "filename": "name",
        "file": "name", "document": "name", "document name": "name",
        "attachment": "name", "attachment name": "name", "title": "name",
        "type": "type", "category": "type", "document type": "type",
        "file type": "type", "status": "status", "document status": "status",
        "date": "date", "date uploaded": "date", "uploaded": "date",
        "date received": "date", "received": "date", "required": "required",
        "download": "downloadable", "downloadable": "downloadable",
    },
    "conditions": {
        "condition": "description", "description": "description",
        "name": "description", "status": "status", "severity": "severity",
        "type": "severity", "source": "source", "affects": "affects_stage",
        "affects stage": "affects_stage", "stage": "affects_stage",
    },
    "history": {
        "date": "date", "event date": "date", "activity date": "date",
        "event": "event", "action": "event", "activity": "event",
        "description": "event", "status": "status", "details": "details",
        "comments": "details", "comments notes": "details", "by": "details",
        "user": "details", "processed by": "details",
    },
}

# Columns that carry no structured meaning for licet (links/controls). One such
# column is tolerated in a header row so real grids with an Actions cell still
# parse; anything else unmapped means the header shape is not understood.
_ACTION_COLUMNS = {
    "", "action", "actions", "edit", "select", "view", "open", "delete",
    "download", "attach", "attach file", "details", "link",
}

# ACA renders dates as MM/DD/YYYY (Phase 1 capture), but municipality wording
# varies. Attempt ordering (rules `_attempt_order`) and Phase 4 date math
# consume ISO strings: a portal date left as MM/DD/YYYY silently reads as
# "attempt order unknown" downstream, which a planner cannot distinguish from
# an honestly unordered history. Parse or keep the raw text — never drop it.
_DATE_FORMATS = ("%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d", "%m-%d-%Y", "%d-%b-%Y", "%b %d, %Y")
_DATE_TOKEN_RE = re.compile(
    r"^\d{1,2}[/-]\d{1,2}[/-]\d{2,4}$|\d{4}-\d{2}-\d{2}$|^\d{1,2}-[A-Za-z]{3}-\d{4}$"
)


def _normalize_date(value: Any) -> str | None:
    """A portal date token as ISO, or None when it is not a parseable date."""
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return _datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _is_legend_type(value: str | None) -> bool:
    """Whether a would-be inspection *type* is itself a lifecycle/outcome word.

    Legend or summary lines (``Scheduled | Completed | Failed``) validate cell
    by cell against the vocabularies, so without this guard a legend becomes a
    fabricated row whose inspection type is "Completed" (H01). Real ACA type
    names ("Rough Electrical", "Electrical Final") never normalize as either
    dimension.
    """
    from licet.phase3.extract import normalize_lifecycle, normalize_result

    text = (value or "").strip()
    return bool(text) and (normalize_lifecycle(text) is not None or normalize_result(text) is not None)


def _header_field(section: str, cell: str) -> str | None:
    """Canonical extractor field for a rendered header cell, or None."""
    key = re.sub(r"[^a-z0-9 ]+", " ", (cell or "").lower())
    key = " ".join(key.split())
    return _HEADER_FIELDS.get(section, {}).get(key)

_MONEY_RE = re.compile(r"^\$?\s*\(?\d{1,3}(?:,\d{3})*(?:\.\d{1,2})?\)?$")


def _is_money(text: str) -> bool:
    return bool(_MONEY_RE.match((text or "").strip()))


def _paid_from_cell(text: str) -> bool | None:
    lowered = (text or "").strip().lower()
    if not lowered:
        return None
    if "paid" in lowered and "unpaid" not in lowered and "not paid" not in lowered:
        return True
    if "unpaid" in lowered or "not paid" in lowered or "balance due" in lowered:
        return False
    # ACA agency wordings that state non-payment without the word "unpaid"
    # (observed fee-grid statuses). Anything still unrecognized stays None —
    # an unknown payment state must never silently become "paid".
    if lowered in {"in collection", "past due", "delinquent", "overdue"}:
        return False
    return None


def _due_from_cell(text: str) -> bool | None:
    lowered = (text or "").strip().lower()
    if not lowered:
        return None
    if "due" in lowered or "outstanding" in lowered or "unpaid" in lowered:
        return True
    if "paid" in lowered:
        return False
    return None


def _bool_from_cell(text: str) -> bool | None:
    lowered = (text or "").strip().lower()
    if not lowered:
        return None
    if lowered in {"y", "yes", "true", "required", "download", "available"}:
        return True
    if lowered in {"n", "no", "false", "optional", "n/a", "none", "not required"}:
        return False
    if "not required" in lowered or "optional" in lowered or lowered.startswith("non"):
        return False
    if "required" in lowered:
        return True
    return None


def _finalize_row(section: str, row: dict[str, Any]) -> dict[str, Any]:
    """Coerce header-keyed cells into the canonical extract row contract."""
    if section == "fees":
        if "paid" in row:
            paid = _paid_from_cell(str(row.pop("paid")))
            if paid is not None:
                row["paid"] = paid
        if "due" in row:
            due = _due_from_cell(str(row.pop("due")))
            if due is not None:
                row["due"] = due
        if row.get("paid") is False and "due" not in row:
            row["due"] = True
        # A due date is not a payment status: keep the money facts without
        # letting a date-shaped cell fabricate one (H04). The raw amount text
        # is already preserved by the extractor when `amount_text` is absent.
        row.pop("due_date", None)
    elif section == "documents":
        for field in ("required", "downloadable"):
            if field in row:
                value = _bool_from_cell(str(row.pop(field)))
                if value is not None:
                    row[field] = value
    elif section == "inspections":
        # Some agencies render the outcome in the status column (or "Failed"
        # as a bare result). The lifecycle vocabulary stays the authority for
        # status; an outcome word found there is carried as the result too, so
        # a live failure never silently disappears (H06).
        from licet.phase3.extract import normalize_result

        status = str(row.get("status") or "").strip()
        if status and normalize_result(status) is not None and not row.get("result"):
            row["result"] = status
        date = row.pop("date", None)
        if date:
            normalized = _normalize_date(date)
            status_lower = status.lower()
            key = ("scheduled_date" if "schedul" in status_lower else
                   "completed_date" if ("complet" in status_lower or "done" in status_lower)
                   else "requested_date")
            # Parseable -> ISO; anything else keeps the portal's raw token so
            # downstream ordering honestly reports unknown instead of losing
            # the evidence.
            row.setdefault(key, normalized or str(date))
        for key in ("requested_date", "scheduled_date", "completed_date"):
            if key in row:
                normalized = _normalize_date(row[key])
                if normalized is not None:
                    row[key] = normalized
    return row


def section_for_page(data: Mapping[str, Any]) -> str | None:
    """Which record section this read_page payload is showing, best-effort."""
    text = (str(data.get("text") or "")).lower()
    for marker, section in _SECTION_BY_LABEL:
        if marker in text:
            return section
    return None


def record_key_from_page(data: Mapping[str, Any]) -> str | None:
    """Stable record identity from the page URL, in ``PermitState.record_key`` form.

    Same key format as ``RecordRef.as_key`` — the string both merge and the
    foreign-record rejection compare. None when the page is not a record page.
    """
    from licet.schema.extract import ref_from_page

    ref = ref_from_page(dict(data))
    return ref.as_key() if ref is not None else None


def _header_fields(data: Mapping[str, Any]) -> dict[str, Any]:
    header = accela.parse_record_header(str(data.get("text") or ""))
    fields: dict[str, Any] = {}
    if header.get("permit_id"):
        fields["record_number"] = header["permit_id"]
    if header.get("permit_type"):
        fields["record_type"] = header["permit_type"]
    if header.get("status"):
        fields["status"] = header["status"]
    expiration = header.get("expiration_text")
    if expiration and expiration.lower() not in {"n/a", "none", ""}:
        fields["expiration_date"] = expiration
    return fields


def _address_fields(data: Mapping[str, Any]) -> dict[str, Any]:
    """Work-location block: labeled lines only, never positional guessing."""
    text = str(data.get("text") or "")
    fields: dict[str, Any] = {}
    for line in text.splitlines():
        label, _, value = line.partition(":")
        label_key = label.strip().lower()
        if label_key in {"work location", "address", "location"} and value.strip():
            fields["address"] = value.strip()
        elif label_key in {"parcel", "parcel number"} and value.strip():
            fields["parcel_number"] = value.strip()
        elif label_key in {"applicant", "contact", "contact information"} and value.strip():
            fields["applicant"] = value.strip()
        elif label_key in {"description", "work description"} and value.strip():
            fields["description"] = value.strip()
    return fields


def _section_table_rows(text: str, section: str) -> list[dict[str, Any]]:
    """Rows of a rendered section table, or [] when none is present.

    Line-shaped path for text-mode reads: a ``Label | Value | Value`` header row
    is recognized when its cells map onto the section's canonical fields (one
    unmapped action/link column tolerated), then subsequent rows are zipped by
    column position and coerced by ``_finalize_row``. Rows carry the same
    contract the eval fixtures use, so section wording differences change which
    labels are recognized, not whether the data survives extraction.
    """
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    rows: list[dict[str, Any]] = []
    fields: list[str | None] | None = None
    for line in lines:
        if "|" not in line:
            continue
        cells = [cell.strip() for cell in line.split("|")]
        mapped = [_header_field(section, cell) for cell in cells]
        recognized = [field for field in mapped if field]
        unmapped = [
            cell
            for cell, field in zip(cells, mapped)
            if field is None and cell.lower() not in _ACTION_COLUMNS
        ]
        if len(recognized) >= 2 and not unmapped:
            fields = mapped
            continue
        if fields is None:
            continue
        row: dict[str, Any] = {}
        for field, value in zip(fields, cells):
            if field and value and field not in row:
                row[field] = value
        # A row whose every cell was just used as the header's canonical fields
        # is a legend/summary line ("Scheduled | Completed | Failed"), not data:
        # zipping it produces a fabricated row whose "type" is a lifecycle word.
        # Accept it only when at least one value differs from its own column
        # header word.
        if row and all(
            cell.casefold() == (field or "").replace("_", " ").casefold()
            or cell.casefold() in {"n/a", "none", "-"}
            for cell, field in zip(cells, fields)
            if cell
        ):
            continue
        if section == "inspections" and _is_legend_type(row.get("type")):
            continue
        if len(row) >= 2:
            rows.append(_finalize_row(section, row))
    return rows


def overview_observation(data: Mapping[str, Any]) -> dict[str, Any]:
    """The overview observation: header identity + labeled overview fields."""
    fields = _header_fields(data)
    fields.update(_address_fields(data))
    fields = {key: value for key, value in fields.items() if value}
    return {
        "record_key": record_key_from_page(data),
        "section": "overview",
        "coverage": "complete" if fields else "parse_failed",
        "url": data.get("url"),
        "observed_at": data.get("observed_at"),
        "text": str(data.get("text") or ""),
        "loading": list(data.get("loading") or []),
        "truncated": bool(data.get("truncated")),
        "fields": fields,
    }


def _inspection_lines_as_rows(text: str) -> list[dict[str, str]]:
    """Validated row candidates from inspection-shaped text lines.

    ACA's citizen detail renders inspection rows as delimited text. A line
    becomes a row only when its cells actually validate against the known
    lifecycle/result vocabularies (from ``licet.phase3.extract``) — a line that
    does not validate is never force-parsed. A line that begins with an
    explicit ``Comment:`` marker attaches to the most recent row (the marker is
    the linkage evidence; without it a comment stays unassigned).
    """
    from licet.phase3.extract import normalize_lifecycle, normalize_result

    rows: list[dict[str, str]] = []
    for line in str(text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        lowered = line.lower()
        if lowered.startswith("comment:") and rows:
            rows[-1]["comments"] = line.split(":", 1)[1].strip()
            continue
        if "|" not in line:
            continue
        cells = [cell.strip() for cell in line.split("|") if cell.strip()]
        status = next((c for c in cells if normalize_lifecycle(c)), None)
        result = next((c for c in cells if normalize_result(c)), None)
        if status is None and result is None:
            continue  # neither dimension validates: not an inspection row
        type_cell = next((c for c in cells if c not in {status, result}), None)
        if not type_cell or _is_legend_type(type_cell):
            continue
        row: dict[str, str] = {"type": type_cell}
        if status:
            row["status"] = status
        if result:
            row["result"] = result
        # A trailing date token becomes the status-appropriate ISO date, so a
        # Scheduled row keeps its appointment date and a Completed row its
        # completion date — the fields attempt ordering downstream depends on.
        leftover = [c for c in cells if c not in {type_cell, status, result}]
        if leftover and _DATE_TOKEN_RE.match(leftover[-1]):
            key = "scheduled_date" if status and "schedul" in status.lower() else (
                "completed_date" if status and ("complet" in status.lower() or "done" in status.lower()) else "requested_date"
            )
            normalized = _normalize_date(leftover[-1])
            row[key] = normalized or leftover[-1]
        rows.append(row)
    return rows


def inspections_observation(data: Mapping[str, Any]) -> dict[str, Any]:
    """The Inspections section observation.

    Structured table rows win when present; otherwise validated text-line rows
    are used (see ``_inspection_lines_as_rows``). "No inspections" is only
    claimed when the page says so explicitly, the section is not loading, AND
    no rows were parsed — a declared-empty marker beside parsed rows means the
    page shape is not understood, and the read degrades to partial.
    """
    text = str(data.get("text") or "")
    offered = [
        {"name": option["name"], "required": option["required"]}
        for option in (data.get("inspection_types") or [])
    ]
    loading = list(data.get("loading") or [])
    rows = _section_table_rows(text, "inspections") or _inspection_lines_as_rows(text)
    declares_empty = accela.declares_no_inspections(text) and not loading
    if loading:
        coverage = "loading"
    elif rows and declares_empty:
        # "You have not added any inspections." beside parsed rows: the page
        # shape is not understood (or the marker is stale template text). The
        # rows are kept — they validated against the vocabularies — but the
        # coverage must not claim completeness while the page contradicts
        # itself, or a planner reads a confident complete history from a
        # self-disputing section (H05).
        coverage = "partial"
    elif rows:
        coverage = "complete"
    elif declares_empty:
        coverage = "explicitly_empty"
    else:
        coverage = "partial"
    return {
        "record_key": record_key_from_page(data),
        "section": "inspections",
        "coverage": coverage,
        "coverage_note": (
            "the citizen detail page renders inspections as text; rows were validated "
            "against the lifecycle/result vocabularies"
            if rows and not _section_table_rows(text, "inspections")
            else None
        ),
        "url": data.get("url"),
        "text": text,
        "loading": loading,
        "truncated": bool(data.get("truncated")),
        "rows": rows,
        "raw_lines": [line.strip() for line in text.splitlines() if line.strip()],
        "offered_types": offered,
        "inspection_type_total": data.get("inspection_type_total"),
    }


def fees_observation(data: Mapping[str, Any]) -> dict[str, Any]:
    """The Fees/Payments section observation."""
    text = str(data.get("text") or "")
    loading = list(data.get("loading") or [])
    rows = _section_table_rows(text, "fees")
    if not rows:
        rows = _money_lines_as_rows(text)
    coverage = "loading" if loading else ("complete" if rows else "partial")
    return {
        "record_key": record_key_from_page(data),
        "section": "fees",
        "coverage": coverage,
        "url": data.get("url"),
        "text": text,
        "loading": loading,
        "truncated": bool(data.get("truncated")),
        "rows": rows,
    }


def _money_lines_as_rows(text: str) -> list[dict[str, str]]:
    """Fee-shaped lines: ``<description> | <money>`` or ``<desc>: <money>``.

    ACA's Payments section often renders one line per fee; a money value makes
    the row a fee line without claiming which column is which beyond that.
    """
    rows: list[dict[str, str]] = []
    for line in str(text or "").splitlines():
        line = line.strip()
        if "|" in line:
            cells = [cell.strip() for cell in line.split("|") if cell.strip()]
        elif ":" in line and not line.lower().startswith(("record", "expiration", "logout")):
            cells = [part.strip() for part in line.split(":", 1)]
        else:
            continue
        money_cells = [cell for cell in cells if _is_money(cell)]
        if len(money_cells) != 1 or len(cells) < 2:
            continue
        description = " | ".join(cell for cell in cells if cell is not money_cells[0])
        if description.lower().strip("| ") in {"fee", "amount", "balance", "total"}:
            continue
        rows.append({"description": description, "amount": money_cells[0]})
    return rows


def documents_observation(data: Mapping[str, Any]) -> dict[str, Any]:
    """The Attachments section observation."""
    text = str(data.get("text") or "")
    loading = list(data.get("loading") or [])
    rows = _section_table_rows(text, "documents")
    coverage = "loading" if loading else ("complete" if rows else "partial")
    return {
        "record_key": record_key_from_page(data),
        "section": "documents",
        "coverage": coverage,
        "url": data.get("url"),
        "text": text,
        "loading": loading,
        "truncated": bool(data.get("truncated")),
        "rows": rows,
    }


def conditions_observation(data: Mapping[str, Any]) -> dict[str, Any]:
    """The Conditions observation, when the agency renders one at all.

    Null Island's citizen detail renders no Conditions section; callers should
    treat ``section_for_page`` saying so as CONDITIONS_NOT_FOUND coverage, not
    as "no conditions exist".
    """
    text = str(data.get("text") or "")
    loading = list(data.get("loading") or [])
    rows = _section_table_rows(text, "conditions")
    coverage = "loading" if loading else ("complete" if rows else "partial")
    return {
        "record_key": record_key_from_page(data),
        "section": "conditions",
        "coverage": coverage,
        "url": data.get("url"),
        "text": text,
        "loading": loading,
        "truncated": bool(data.get("truncated")),
        "rows": rows,
    }


def history_observation(data: Mapping[str, Any]) -> dict[str, Any]:
    """The workflow/history observation, when the agency renders one."""
    text = str(data.get("text") or "")
    loading = list(data.get("loading") or [])
    rows = _section_table_rows(text, "history")
    coverage = "loading" if loading else ("complete" if rows else "partial")
    return {
        "record_key": record_key_from_page(data),
        "section": "history",
        "coverage": coverage,
        "url": data.get("url"),
        "text": text,
        "loading": loading,
        "truncated": bool(data.get("truncated")),
        "rows": rows,
    }


OBSERVATION_BUILDERS = {
    "overview": overview_observation,
    "inspections": inspections_observation,
    "fees": fees_observation,
    "documents": documents_observation,
    "conditions": conditions_observation,
    "history": history_observation,
}


def observation_for(section: str, data: Mapping[str, Any]) -> dict[str, Any]:
    """Build the observation dict for one section from a read_page payload."""
    builder = OBSERVATION_BUILDERS.get(section)
    if builder is None:
        raise ValueError(f"no observation builder for section {section!r}")
    return builder(data)


# Public alias: Phase 4's portal adapter reuses the *validated* inspection-row
# parser (rows only exist when a line actually validates against the lifecycle
# and result vocabularies), so a mutation adapter never re-derives rows from
# raw text. It is the same function `inspections_observation` calls.
parse_inspection_rows = _inspection_lines_as_rows


def offered_type_facts(data: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The scheduling wizard's type list as offer facts (never requirements).

    ``required: true`` here is ACA's own ``(required)`` marker — the only
    requirement signal the wizard carries. It is preserved verbatim for the
    caller to weigh; this adapter asserts nothing beyond what ACA printed.
    """
    return list(data.get("inspection_types") or [])
