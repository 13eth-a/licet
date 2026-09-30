"""conservative extraction from normalized accela section observations"""
from __future__ import annotations

import hashlib
import re
from typing import Any, Iterable, Mapping

from licet.phase3.state import (
    Blocker, Condition, ConfidenceBand, Coverage, CoverageStatus, Document,
    Evidence, Fact, Fee, HistoryEvent, Inspection, PermitState, Section,
)


def _norm(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _evidence(data: Mapping[str, Any], section: str, raw: str = "") -> Evidence:
    source = str(data.get("url") or data.get("source_url") or "") or None
    identity = f"{section}|{source or ''}|{raw}"
    eid = "ev_" + hashlib.sha1(identity.encode()).hexdigest()[:12]
    return Evidence(eid, section, raw, source, _norm(data.get("observed_at")),
                    _norm(data.get("effective_at")), ConfidenceBand(data.get("confidence", "medium"))
                    if data.get("confidence") in {x.value for x in ConfidenceBand} else ConfidenceBand.MEDIUM,
                    _norm(data.get("record_key")))


def _coverage(data: Mapping[str, Any], section: str, has_rows: bool) -> Coverage:
    value = str(data.get("coverage") or ("complete" if has_rows else "not_requested")).lower()
    try:
        status = CoverageStatus(value)
    except ValueError:
        status = CoverageStatus.PARSE_FAILED
    note = _norm(data.get("coverage_note"))
    # coverage honesty (architecture review review p1 #5): a declared-complete section that is still
    # rendering, or whose source text was cut off, is only a partial view
    if data.get("loading") and status == CoverageStatus.COMPLETE:
        status = CoverageStatus.PARTIAL
        note = "; ".join(filter(None, [note, "section was still loading when read"]))
    if data.get("truncated") and status == CoverageStatus.COMPLETE:
        status = CoverageStatus.PARTIAL
        note = "; ".join(filter(None, [note, "page text was truncated"]))
    return Coverage(status=status, complete_through=_norm(data.get("complete_through")),
                    pages_seen=int(data.get("pages_seen") or 0),
                    total_pages=data.get("total_pages"), note=note)


def normalize_permit_status(raw: str | None) -> str | None:
    text = (raw or "").strip().lower()
    if not text or text in {"not issued", "unissued", "inactive", "unexpired"}:
        return None
    if text in {"issued", "permit issued", "active"}:
        return "ISSUED"
    if text in {"submitted", "application received"}:
        return "SUBMITTED"
    if text in {"approved"}:
        return "APPROVED"
    if text in {"pending review", "under review", "in review", "processing"}:
        return "IN_REVIEW"
    if text in {"expired", "permit expired"}:
        return "EXPIRED"
    if text in {"closed", "complete", "completed", "finaled"}:
        return "CLOSED"
    if text in {"cancelled", "canceled", "void"}:
        return "CANCELLED"
    return None


def normalize_lifecycle(raw: str | None) -> str | None:
    text = (raw or "").strip().lower()
    if not text:
        # no status shown at all is unknown lifecycle — never fabricated into "pending" (pending is a
        # claim the portal must make)
        return None
    if text in {"not scheduled", "awaiting", "pending"}:
        return "PENDING"
    if text in {"scheduled", "insp scheduled", "appointment scheduled"}:
        return "SCHEDULED"
    if text in {"completed", "complete", "done"}:
        return "COMPLETED"
    if text in {"cancelled", "canceled", "void"}:
        return "CANCELLED"
    return None


def normalize_result(raw: str | None) -> str | None:
    text = (raw or "").strip().lower()
    if not text:
        return None
    if text in {"failed", "corrections required", "not approved", "denied", "fail"}:
        return "FAILED"
    if text in {"passed", "pass", "approved"}:
        return "PASSED"
    if text in {"partial", "partial pass", "conditionally passed"}:
        return "PARTIAL"
    # completed is lifecycle only; it is intentionally not a result
    return None


def _rows(data: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    rows = data.get("rows", data.get("records", data.get("items", [])))
    return [row for row in rows if isinstance(row, Mapping)] if isinstance(rows, list) else []


def extract_partial_state(observation: Mapping[str, Any], *, section: str | Section | None = None) -> PermitState:
    """extract one observation into a partial state; no fields are guessed"""
    name = str(section or observation.get("section") or "overview").lower()
    state = PermitState(record_key=_norm(observation.get("record_key")))
    raw_text = str(observation.get("text") or "")
    ev = _evidence(observation, name, raw_text)
    state.evidence[ev.id] = ev
    rows = _rows(observation)
    state.coverage[name] = _coverage(observation, name, bool(rows or raw_text))

    fields = observation.get("fields") if isinstance(observation.get("fields"), Mapping) else {}
    if name == Section.OVERVIEW.value:
        values = {**fields, **{k: v for k, v in observation.items() if k not in {"fields", "text"}}}
        state.record_number = _norm(values.get("record_number") or values.get("permit_id"))
        state.record_type = _norm(values.get("record_type") or values.get("permit_type"))
        state.address = _norm(values.get("address"))
        state.status = _norm(values.get("status"))
        state.status_normalized = normalize_permit_status(state.status)
        state.application_date = _norm(values.get("application_date") or values.get("submitted_date"))
        state.issued_date = _norm(values.get("issued_date"))
        state.expiration_date = _norm(values.get("expiration_date"))
        state.applicant = _norm(values.get("applicant"))
        state.contact = _norm(values.get("contact"))
        state.parcel_number = _norm(values.get("parcel_number") or values.get("parcel"))
        state.description = _norm(values.get("description"))
        if state.record_key is None:
            state.record_key = _norm(values.get("record_key"))
        for key in ("record_number", "record_type", "status", "address", "application_date", "issued_date", "expiration_date", "applicant", "parcel_number"):
            value = getattr(state, key)
            if value is not None:
                state.facts.append(Fact(key, value, evidence_ids=[ev.id], confidence=ev.confidence, raw_value=str(value)))
    elif name == Section.INSPECTIONS.value:
        for index, row in enumerate(rows):
            raw_status = _norm(row.get("status"))
            raw_result = _norm(row.get("result"))
            comment = _norm(row.get("comments") or row.get("comment"))
            inspection = Inspection(
                type=_norm(row.get("type") or row.get("inspection_type")) or "Unknown inspection",
                status=raw_status, result=raw_result,
                requested_date=_norm(row.get("requested_date")), scheduled_date=_norm(row.get("scheduled_date") or row.get("date") if "scheduled" in (raw_status or "").lower() else row.get("scheduled_date")),
                completed_date=_norm(row.get("completed_date")), inspector=_norm(row.get("inspector")), comments=comment,
                inspection_id=_norm(row.get("inspection_id") or row.get("id")), scope=_norm(row.get("scope")),
                evidence_ids=[ev.id], comment_evidence_ids=[ev.id] if comment else [], raw_status=raw_status, raw_result=raw_result,
                lifecycle_normalized=normalize_lifecycle(raw_status), result_normalized=normalize_result(raw_result),
            )
            state.inspections.append(inspection)
        # offered types are catalog facts; aca's own `(required)` marker is the only requirement signal
        # and is kept as its own fact (never merged)
        for offer in observation.get("offered_types") or []:
            if isinstance(offer, Mapping) and _norm(offer.get("name")):
                state.facts.append(Fact("offered_type", offer["name"], evidence_ids=[ev.id], confidence=ev.confidence, raw_value=str(offer)))
                if offer.get("required") is True:
                    state.facts.append(Fact("required_type", offer["name"], evidence_ids=[ev.id], confidence=ev.confidence, raw_value=str(offer)))
        if not rows and state.coverage[name].status == CoverageStatus.COMPLETE:
            state.coverage[name].status = CoverageStatus.EXPLICITLY_EMPTY
    elif name == Section.FEES.value:
        for row in rows:
            amount = row.get("amount", row.get("balance"))
            try: parsed = float(str(amount).replace("$", "").replace(",", "")) if amount is not None else None
            except ValueError: parsed = None
            paid = row.get("paid") if isinstance(row.get("paid"), bool) else None
            due = row.get("due") if isinstance(row.get("due"), bool) else None
            # `balance` is a money fact only
            balance = row.get("balance")
            try: balance_amount = float(str(balance).replace("$", "").replace(",", "")) if balance is not None else None
            except ValueError: balance_amount = None
            state.fees.append(Fee(_norm(row.get("description") or row.get("name")) or "Unnamed fee", parsed, _norm(row.get("amount_text") or amount), paid, due, balance_amount, _norm(row.get("gate_text")), [ev.id]))
    elif name == Section.DOCUMENTS.value:
        for row in rows:
            state.documents.append(Document(_norm(row.get("name")) or "Unnamed document", _norm(row.get("type")), _norm(row.get("status")), _norm(row.get("date")), bool(row.get("downloadable")), row.get("required") if isinstance(row.get("required"), bool) else None, [ev.id]))
    elif name == Section.CONDITIONS.value:
        for row in rows:
            state.conditions.append(Condition(_norm(row.get("description") or row.get("condition")) or "Unnamed condition", _norm(row.get("status")), _norm(row.get("severity")), _norm(row.get("source")), _norm(row.get("affects_stage")), [ev.id]))
    elif name == Section.HISTORY.value:
        for row in rows:
            state.history.append(HistoryEvent(_norm(row.get("event") or row.get("description")) or "Unnamed event", _norm(row.get("date")), _norm(row.get("status")), _norm(row.get("details")), [ev.id]))
    elif name == Section.COMMENTS.value:
        comments = observation.get("comments") if isinstance(observation.get("comments"), list) else []
        state.comments = [str(c) for c in comments if str(c).strip()]
    return state


def merge_partial_states(base: PermitState, *partials: PermitState) -> PermitState:
    """merge same-record observations without replacing known data with blanks"""
    for part in partials:
        if base.record_key and part.record_key and base.record_key != part.record_key:
            base.rejected_observations.append(
                f"rejected observation for record {part.record_key!r} "
                f"(expected {base.record_key!r})"
            )
            continue
        if base.record_key is None: base.record_key = part.record_key
        for key in ("record_number", "record_type", "address", "status", "status_normalized", "application_date", "issued_date", "expiration_date", "applicant", "contact", "parcel_number", "description"):
            current, incoming = getattr(base, key), getattr(part, key)
            if current is None and incoming is not None: setattr(base, key, incoming)
            elif current is not None and incoming is not None and current != incoming and key in {"status", "status_normalized"}:
                base.contradictions.append(f"conflicting {key}: {current!r} vs {incoming!r}")
        base.evidence.update(part.evidence)
        base.coverage.update(part.coverage)
        # facts are shared observations when their evidence agrees; when the evidence differs, keep both
        # so competing values stay visible (the contract forbids last-write-wins on same-field facts)
        for fact in part.facts:
            if not any(
                x.field == fact.field and x.value == fact.value
                and set(x.evidence_ids) == set(fact.evidence_ids)
                for x in base.facts
            ):
                base.facts.append(fact)
        _merge_entities(base.inspections, part.inspections, lambda x: (x.inspection_id or "", x.type.lower(), x.scope or ""), base.contradictions, "inspection")
        _merge_entities(base.fees, part.fees, lambda x: (x.description.lower(), x.amount_text or ""), base.contradictions, "fee")
        _merge_entities(base.documents, part.documents, lambda x: x.name.lower(), base.contradictions, "document")
        _merge_entities(base.conditions, part.conditions, lambda x: x.description.lower(), base.contradictions, "condition")
        _merge_entities(base.history, part.history, lambda x: (x.event.lower(), x.date or ""), base.contradictions, "history")
        base.comments.extend(c for c in part.comments if c not in base.comments)
        base.outstanding_requirements.extend(r for r in part.outstanding_requirements if r not in base.outstanding_requirements)
    return base


# scalar fields whose disagreement between two same-record reads is a real conflict the answer must surface
_CONFLICT_FIELDS = {
    "paid", "due", "status", "result", "amount", "balance", "required",
    "downloadable", "completed_date", "scheduled_date", "lifecycle_normalized",
    "result_normalized", "type", "name", "description",
}


def _merge_entities(
    target: list[Any],
    incoming: Iterable[Any],
    key_fn: Any,
    conflicts: list[str] | None = None,
    label: str = "entity",
) -> None:
    keys = {key_fn(item) for item in target}
    for item in incoming:
        key = key_fn(item)
        if key not in keys:
            target.append(item); keys.add(key)
        else:
            existing = next(x for x in target if key_fn(x) == key)
            for name, value in vars(item).items():
                current = getattr(existing, name, None)
                if current in (None, [], "") and value not in (None, [], ""):
                    setattr(existing, name, value)
                elif (
                    conflicts is not None
                    and name in _CONFLICT_FIELDS
                    and current not in (None, [], "")
                    and value not in (None, [], "")
                    and current != value
                ):
                    conflicts.append(
                        f"conflicting {label} {name}: {current!r} vs {value!r}"
                    )
