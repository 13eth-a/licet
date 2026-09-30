"""turn a portal page into the `permit` schema"""

from __future__ import annotations

import datetime as dt
from typing import Any

from licet.browser import accela
from licet.schema.permit import Fact, Permit, Provenance, RecordRef

# permit fields arrive as portal text (e.g. "01/31/2026"); parse, never assume
_DATE_FORMATS = ("%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d", "%d-%b-%Y", "%b %d, %Y")


def parse_portal_date(value: str | None) -> dt.date | None:
    """parse a portal date string, or none"""
    text = (value or "").strip()
    if not text:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return dt.datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def ref_from_page(data: dict[str, Any]) -> RecordRef | None:
    """stable identity for the page being read, from its url"""
    parsed = accela.parse_ref_from_url(str(data.get("url") or ""))
    if not parsed:
        return None
    header = accela.parse_record_header(data.get("text") or "")
    return RecordRef(
        cap_id1=parsed["capID1"],
        cap_id2=parsed["capID2"],
        cap_id3=parsed["capID3"],
        module=parsed["module"],
        agency_code=parsed["agency_code"],
        display_id=header.get("permit_id"),
    )


def permit_from_page(
    data: dict[str, Any],
    *,
    address: str = "",
    applicant: str | None = None,
    description: str | None = None,
) -> Permit | None:
    """build a `permit` from one `read_page` result"""
    text = data.get("text") or ""
    header = accela.parse_record_header(text)
    ref = ref_from_page(data)
    if not header and ref is None:
        return None

    permit = Permit(
        permit_id=header.get("permit_id") or (ref.display_id if ref else "") or "",
        address=address,
        ref=ref,
        permit_type=header.get("permit_type"),
        status=header.get("status"),
        applicant=applicant,
        description=description,
        expiration_date=parse_portal_date(header.get("expiration_text")),
        sections=accela.parse_sections(text),
    )

    # coverage notes (phase 3): loading markers, empty observations, calendar scope and truncation are
    # *coverage*, not outstanding requirements
    if data.get("loading"):
        permit.coverage_notes.append(
            Fact(
                value=(
                    "inspections section was still loading when read; "
                    f"loading markers: {data.get('loading')}"
                ),
                provenance=Provenance.DERIVED,
                evidence_url=str(data.get("url") or ""),
            )
        )
    elif accela.declares_no_inspections(text):
        # a successful observation of an empty section: "none shown", not "no work required" and not an
        # extraction failure
        permit.coverage_notes.append(
            Fact(
                value="no inspection history on this record",
                provenance=Provenance.PORTAL,
                evidence_url=str(data.get("url") or ""),
            )
        )

    if data.get("truncated"):
        permit.coverage_notes.append(
            Fact(
                value="page text was truncated; absence of content beyond the cut is not evidence",
                provenance=Provenance.DERIVED,
                evidence_url=str(data.get("url") or ""),
            )
        )

    # the scheduling wizard's own type list, when this page has it
    types = accela.parse_inspection_types(data.get("fields") or [])
    if types:
        permit.schedulable_inspection_types = [option.name for option in types]
        permit.required_inspection_types = [
            option.name for option in types if option.required
        ]
        total = data.get("inspection_type_total")
        if isinstance(total, int) and total > len(types):
            permit.coverage_notes.append(
                Fact(
                    value=(
                        f"{total} inspection types offered but only {len(types)} on this "
                        "page of the wizard's grid (it paginates)"
                    ),
                    provenance=Provenance.DERIVED,
                    evidence_url=str(data.get("url") or ""),
                )
            )

    if data.get("calendar"):
        months = [month.get("month") or "" for month in data["calendar"]]
        active = sum(len(month.get("active_days") or []) for month in data["calendar"])
        if active == 0:
            permit.coverage_notes.append(
                Fact(
                    value=(
                        "no bookable appointment dates in the "
                        f"{len(months)} observed month(s)"
                        + (f" ({', '.join(m for m in months if m)})" if any(months) else "")
                    ),
                    provenance=Provenance.PORTAL,
                    evidence_url=str(data.get("url") or ""),
                )
            )
    return permit


def apply_next_action(permit: Permit) -> Permit:
    """record what the record is actually waiting on, as a derived fact"""
    missing = permit.missing_inspections()
    if missing:
        permit.next_action = Fact(
            value=(
                "required inspection not yet completed: "
                + ", ".join(missing)
                + (" (no bookable dates offered)" if _has_no_availability(permit) else "")
            ),
            provenance=Provenance.DERIVED,
        )
    else:
        permit.next_action = None
    return permit


def _has_no_availability(permit: Permit) -> bool:
    return any(
        "no bookable appointment dates" in fact.value for fact in permit.coverage_notes
    )
