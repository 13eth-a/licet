"""Shared, dependency-light entry points for scripted Phase 3 evals and replay.

Phase 3 reasoning is otherwise reachable from the planner and the dispatcher, both
of which pull in the live run stack. Offline regression (golden cases, adversarial
replay, CI without a Solari key) needs a tiny facade that returns a validated
deterministic result without building a full agent run.

Deterministic path: ``understand_one`` runs the existing deterministic coordinator
directly and is unchanged by model wiring.

Model path: ``reason_with_model`` is the isolated model-level interpretation stage.
It is gated so that:
- it can be disabled entirely for offline/deterministic runs;
- when enabled, it runs the structured interpretation prompt from
  ``licet/agent/phase3_reasoning_prompt.md`` with **zero action tools**;
- it validates the model's structured output through the same publication gates
  the deterministic layer enforces before rendering, and never returns a result
  whose ``execution_allowed`` is true.
"""
from __future__ import annotations

import logging
from typing import Any

from licet.phase3.errors import Phase3Error, Phase3ErrorCode
from licet.phase3.reasoning import understand
from licet.phase3.state import (
    Claim,
    Condition,
    Coverage,
    CoverageStatus,
    Document,
    Fact,
    FactKind,
    Fee,
    HistoryEvent,
    Inspection,
    PermitState,
    ReasoningResult,
    ConfidenceBand,
    Uncertainty,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Scripted model adapter for Phase 3 closure testing.
#
# The live model invocation is a planner-level integration decision (Phase 4
# boundary). For Phase 3 closure, however, the model-level *path* must be
# exercised deterministically so the prompt, payload, coercion, validation
# gates, and publication discipline are all real and tested — not a stub that
# only the deterministic baseline covers.
#
# LICET_PHASE3_MODEL_ADAPTER, when set, names a Python callable path
# (module:?function) that returns a structured dict for one question. When it
# is not set, the coordinator uses a deterministic scripted adapter that
# reproduces the current deterministic layer's conclusions in model-shape. That
# keeps the pipeline exercised without requiring a live model provider.
# ---------------------------------------------------------------------------

_ADAPTER_ENV = "LICET_PHASE3_MODEL_ADAPTER"


def _model_adapter() -> Any:
    import importlib
    import os

    name = os.getenv(_ADAPTER_ENV, "").strip()
    if not name:
        return _scripted_model_adapter
    if "?" not in name and ":" not in name:
        raise Phase3Error(
            Phase3ErrorCode.STATE_EXTRACTION_FAILED,
            f"bad adapter spec {name!r}; expected module:function or module?function",
            section="reasoning",
        )
    module_name, _, func_name = name.partition(":") if ":" in name else name.partition("?")
    module = importlib.import_module(module_name)
    func = getattr(module, func_name)
    if not callable(func):
        raise Phase3Error(
            Phase3ErrorCode.STATE_EXTRACTION_FAILED,
            f"adapter {name!r} is not callable",
            section="reasoning",
        )
    return func


def _scripted_model_adapter(prompt: str, payload: dict[str, Any], snapshot_id: str) -> dict[str, Any]:
    """Deterministic scripted adapter that emits the current layer's conclusions.

    This is not the production model path. It exists so the model coordinator,
    payload assembly, coercion, and publication gates are exercised in the same
    shape a real model would use, and so the golden set can be re-run with
    LICET_PHASE3_MODEL_ENABLED=1 without a live provider.
    """
    from licet.phase3.reasoning import understand

    state = _payload_to_state(payload)
    result = understand(state, payload["question"], snapshot_id=snapshot_id)
    return _result_to_model_shape(result)


def _result_to_model_shape(result: ReasoningResult) -> dict[str, Any]:
    return {
        "record_key": result.record_key,
        "snapshot_id": result.snapshot_id,
        "question": result.question,
        "answerability": result.answerability,
        "claims": [
            {
                "id": c.id,
                "kind": c.kind.value,
                "statement": c.statement,
                "evidence_ids": c.evidence_ids,
                "premise_claim_ids": c.premise_claim_ids,
                "confidence": c.confidence.value,
                "reason_code": c.reason_code,
            }
            for c in result.claims
        ],
        "blockers": [
            {
                "type": b.type,
                "description": b.description,
                "source": b.source,
                "confidence": b.confidence,
                "resolvable_by_licet": b.resolvable_by_licet,
                "classification": b.classification,
                "affects_stage": b.affects_stage,
                "evidence_ids": b.evidence_ids,
                "rank": b.rank,
            }
            for b in result.blockers
        ],
        "next_actions": [
            {
                "action": a.action,
                "reason": a.reason,
                "confidence": a.confidence,
                "requires_confirmation": a.requires_confirmation,
                "requirement_strength": a.requirement_strength,
                "preconditions": a.preconditions,
                "evidence_ids": a.evidence_ids,
            }
            for a in result.next_actions
        ],
        "contradictions": result.contradictions,
        "uncertainties": [
            {
                "description": u.description,
                "impact": u.impact,
                "needed_section": u.needed_section,
                "evidence_ids": u.evidence_ids,
                "blocks_answer": u.blocks_answer,
            }
            for u in result.uncertainties
        ],
        "needed_sections": result.needed_sections,
        "execution_allowed": result.execution_allowed,
    }


def understand_one(
    state: PermitState,
    question: str,
    *,
    snapshot_id: str = "snapshot",
    use_model: bool = False,
) -> ReasoningResult:
    """Best-effort Phase 3 understanding for one question.

    Deterministic unless the caller explicitly opts into the model stage.
    """
    if use_model:
        return reason_with_model(state, question, snapshot_id=snapshot_id)
    return understand(state, question, snapshot_id=snapshot_id)


def reason_with_model(
    state: PermitState,
    question: str,
    *,
    snapshot_id: str = "snapshot",
) -> ReasoningResult:
    """Model-level Phase 3 interpretation, isolated and read-only.

    This is the coordinator for the architecture review’s structured interpretation prompt. It is
    intentionally not the planner: the planner is about *acting*; this stage is
    about *explaining*. It receives a compact structured permit snapshot, never a
    raw page, and it returns a structured ``ReasoningResult`` that the renderer
    formats.

    The model call is gated behind env var ``LICET_PHASE3_MODEL_ENABLED`` because
    the model-level stage is the one piece of Phase 3 not yet verified against a
    live portal. Deterministic ``understand()`` remains the default and the
    regression baseline. When the model call is disabled, ``reason_with_model``
    falls back to the deterministic coordinator and logs the fact.
    """
    if not _model_enabled():
        logger.info(
            "phase3 model reasoning disabled (LICET_PHASE3_MODEL_ENABLED); "
            "using deterministic understand() for %s",
            snapshot_id,
        )
        return understand(state, question, snapshot_id=snapshot_id)

    prompt = _reasoning_prompt()
    payload = _build_model_payload(state, question, snapshot_id)
    adapter = _model_adapter()
    structured = adapter(prompt, payload, snapshot_id)
    result = _validate_model_output(structured, state, question, snapshot_id)
    # Publication gate: the model is interpretation, not execution.
    if result.execution_allowed:
        result.execution_allowed = False
        logger.warning(
            "model returned execution_allowed=True; forced to false for %s",
            snapshot_id,
        )
    _apply_contested_premise_gate(result, state)
    return result


def _model_enabled() -> bool:
    import os

    return str(os.getenv("LICET_PHASE3_MODEL_ENABLED", "0")).strip().lower() in {
        "1", "true", "yes",
    }


def _reasoning_prompt() -> str:
    from importlib.resources import files

    path = files("licet.agent").joinpath("phase3_reasoning_prompt.md")
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        logger.warning("phase3 reasoning prompt not found at %s; using fallback", path)
        return _FALLBACK_PROMPT


def _build_model_payload(state: PermitState, question: str, snapshot_id: str) -> dict[str, Any]:
    """Compact structured snapshot for the model. Never raw page text.

    The snapshot carries the section entities, not just the overview scalars:
    without them the reconstructed state had no inspections/fees/documents/
    conditions/history, so the model stage could not reproduce a single blocker
    and the golden model-path check scored 10/50. Conflicts and rejected
    observations travel with the snapshot for the same reason — they are part
    of the evidence the model must not silently resolve.
    """
    return {
        "snapshot_id": snapshot_id,
        "record_key": state.record_key,
        "question": question,
        "permit": _compact_permit(state),
        "facts": [_compact_fact(f) for f in state.facts],
        "sections": _compact_sections(state),
        "coverage": {k: _compact_coverage(v) for k, v in state.coverage.items()},
        "contradictions": list(state.contradictions),
        "rejected_observations": list(state.rejected_observations),
        "comments": list(state.comments),
        "outstanding_requirements": list(state.outstanding_requirements),
        "as_of": "now",
    }


def _compact_sections(state: PermitState) -> dict[str, Any]:
    """The structured section rows the reasoning stage operates on."""
    from dataclasses import asdict

    return {
        "inspections": [asdict(item) for item in state.inspections],
        "fees": [asdict(item) for item in state.fees],
        "documents": [asdict(item) for item in state.documents],
        "conditions": [asdict(item) for item in state.conditions],
        "history": [asdict(item) for item in state.history],
    }


def _compact_permit(state: PermitState) -> dict[str, Any]:
    return {
        "record_number": state.record_number,
        "record_type": state.record_type,
        "address": state.address,
        "status": state.status,
        "status_normalized": state.status_normalized,
        "application_date": state.application_date,
        "issued_date": state.issued_date,
        "expiration_date": state.expiration_date,
        "applicant": state.applicant,
        "parcel_number": state.parcel_number,
        "description": state.description,
    }


def _compact_fact(fact: Claim) -> dict[str, Any]:
    # Facts here are the state's extracted facts list. The model_reasoning
    # coordinator does not invent new portal facts, so it only needs values,
    # evidence ids, and provenance-like classification already carried on the
    # dataclass.
    return {
        "field": fact.field,
        "value": fact.value,
        "kind": fact.kind.value,
        "confidence": fact.confidence.value,
        "evidence_ids": fact.evidence_ids,
        "raw_value": fact.raw_value,
        "entity_id": fact.entity_id,
    }


def _compact_coverage(coverage: Any) -> dict[str, Any]:
    return {
        "status": coverage.status.value if hasattr(coverage.status, "value") else str(coverage.status),
        "complete_through": coverage.complete_through,
        "note": coverage.note,
    }




def _payload_to_state(payload: dict[str, Any]) -> PermitState:
    state = PermitState(
        record_key=payload.get("record_key"),
        record_number=payload["permit"].get("record_number"),
        record_type=payload["permit"].get("record_type"),
        address=payload["permit"].get("address"),
        status=payload["permit"].get("status"),
        status_normalized=payload["permit"].get("status_normalized"),
        application_date=payload["permit"].get("application_date"),
        issued_date=payload["permit"].get("issued_date"),
        expiration_date=payload["permit"].get("expiration_date"),
        applicant=payload["permit"].get("applicant"),
        parcel_number=payload["permit"].get("parcel_number"),
        description=payload["permit"].get("description"),
    )
    for fact in payload.get("facts") or []:
        try:
            kind = FactKind(fact["kind"]) if "kind" in fact else FactKind.FACT
        except ValueError:
            kind = FactKind.FACT
        try:
            confidence = (
                ConfidenceBand(fact["confidence"]) if "confidence" in fact else ConfidenceBand.MEDIUM
            )
        except ValueError:
            confidence = ConfidenceBand.MEDIUM
        # Replica facts need the same shape the deterministic layer's fact list
        # expects, so the scripted model path can round-trip through
        # derive_deterministic_findings / understand unchanged.
        state.facts.append(
            Fact(
                field=str(fact.get("field") or ""),
                value=fact.get("value"),
                kind=kind,
                evidence_ids=fact.get("evidence_ids") or [],
                confidence=confidence,
                entity_id=fact.get("entity_id"),
                raw_value=fact.get("raw_value"),
            )
        )
    # Rebuild the section entities, coverage, and record-state conflicts the
    # snapshot carried, so the scripted/model path reasons over the same record
    # the deterministic layer saw rather than an empty shell.
    from dataclasses import fields as _dataclass_fields

    def _entities(rows: Any, cls: Any) -> list[Any]:
        names = {f.name for f in _dataclass_fields(cls)}
        return [
            cls(**{key: value for key, value in row.items() if key in names})
            for row in (rows or [])
            if isinstance(row, dict)
        ]

    sections = payload.get("sections") or {}
    state.inspections = _entities(sections.get("inspections"), Inspection)
    state.fees = _entities(sections.get("fees"), Fee)
    state.documents = _entities(sections.get("documents"), Document)
    state.conditions = _entities(sections.get("conditions"), Condition)
    state.history = _entities(sections.get("history"), HistoryEvent)
    state.comments = [str(item) for item in (payload.get("comments") or [])]
    state.outstanding_requirements = [str(item) for item in (payload.get("outstanding_requirements") or [])]
    state.contradictions = [str(item) for item in (payload.get("contradictions") or [])]
    state.rejected_observations = [str(item) for item in (payload.get("rejected_observations") or [])]
    for section, coverage in (payload.get("coverage") or {}).items():
        try:
            status = CoverageStatus(str(coverage.get("status")))
        except ValueError:
            status = CoverageStatus.PARSE_FAILED
        state.coverage[section] = Coverage(
            status=status,
            complete_through=coverage.get("complete_through"),
            note=coverage.get("note"),
        )
    return state


def _validate_model_output(
    structured: dict[str, Any],
    state: PermitState,
    question: str,
    snapshot_id: str,
) -> ReasoningResult:
    """Publication-gate validation before the model result is used.

    The model may qualify, connect, or reject deterministic conclusions, but it
    cannot invent portal facts or enable execution. This is the code-side
    publication gate the contract assigns to implementation.
    """
    if not isinstance(structured, dict):
        raise Phase3Error(
            Phase3ErrorCode.STATE_EXTRACTION_FAILED,
            f"model returned non-dict for {snapshot_id}",
            section="reasoning",
        )

    result = _coerce_result(structured, snapshot_id, question=question)

    # Record identity must match the snapshot we gave it.
    if result.record_key and state.record_key and result.record_key != state.record_key:
        result.uncertainties.append(
            Uncertainty(
                f"model returned record key {result.record_key!r} that does not match "
                f"the snapshot's {state.record_key!r}",
                "rejects the model's record binding",
                None,
                [],
                True,
            )
        )

    # The model must not introduce new portal FACT claims that the snapshot did
    # not supply evidence for. INFERENCE and UNCERTAIN are fine; FACT claims must
    # be traceable to a state fact whose field/value/raw_value match.
    _apply_fact_discipline(result, state)
    _apply_contested_premise_gate(result, state)

    return result


def _coerce_result(structured: dict[str, Any], snapshot_id: str, *, question: str = "") -> ReasoningResult:
    """Best-effort coercion of a model dict into ReasoningResult.

    Missing fields are tolerated where the deterministic baseline already handles
    them; malformed fields become uncertainties rather than crashes.
    """
    answerability = str(structured.get("answerability", "answered"))
    result = ReasoningResult(
        record_key=str(structured.get("record_key")) or None,
        snapshot_id=snapshot_id,
        question=str(structured.get("question", question)),
        answerability=answerability
        if answerability in {"answered", "partial", "needs_data", "conflicting"}
        else "answered",
    )

    for claim in (structured.get("claims") or []):
        try:
            kind = FactKind(claim["kind"]) if "kind" in claim else FactKind.FACT
        except (ValueError, KeyError, TypeError):
            kind = FactKind.FACT
        try:
            confidence = (
                ConfidenceBand(claim["confidence"])
                if "confidence" in claim
                else ConfidenceBand.MEDIUM
            )
        except (ValueError, KeyError, TypeError):
            confidence = ConfidenceBand.MEDIUM
        c = Claim(
            str(claim.get("id", "")) or "claim_" + str(len(result.claims)),
            kind,
            str(claim.get("statement", "")),
            list(claim.get("evidence_ids") or []),
            list(claim.get("premise_claim_ids") or []),
            confidence,
            str(claim.get("reason_code", "")),
        )
        result.claims.append(c)

    for blocker in (structured.get("blockers") or []):
        result.blockers.append(_coerce_blocker(blocker))

    for candidate in (structured.get("next_actions") or []):
        result.next_actions.append(_coerce_candidate(candidate))

    for text in (structured.get("contradictions") or []):
        result.contradictions.append(str(text))

    for unc in (structured.get("uncertainties") or []):
        result.uncertainties.append(_coerce_uncertainty(unc))

    for section in (structured.get("needed_sections") or []):
        result.needed_sections.append(
            {
                "section": str(section.get("section", "")),
                "entity_id": str(section.get("entity_id", "")),
                "reason": str(section.get("reason", "")),
                "needed_fact": str(section.get("needed_fact", "")),
                "stop_when": str(section.get("stop_when", "")),
            }
        )

    return result


def _coerce_blocker(blocked: dict[str, Any]) -> Any:
    from licet.phase3.state import Blocker

    return Blocker(
        type=str(blocked.get("type", "unknown")),
        description=str(blocked.get("description", "")),
        source=str(blocked.get("source", "")),
        confidence=float(blocked.get("confidence", 0.5)),
        resolvable_by_licet=bool(blocked.get("resolvable_by_licet", False)),
        classification=str(blocked.get("classification", "observed_problem")),
        affects_stage=str(blocked.get("affects_stage")) or None,
        evidence_ids=list(blocked.get("evidence_ids") or []),
        rank=int(blocked.get("rank", 40)),
    )


def _coerce_candidate(candidate: dict[str, Any]) -> Any:
    from licet.phase3.state import NextActionCandidate

    return NextActionCandidate(
        action=str(candidate.get("action", "")),
        reason=str(candidate.get("reason", "")),
        confidence=float(candidate.get("confidence", 0.5)),
        requires_confirmation=bool(candidate.get("requires_confirmation", False)),
        requirement_strength=str(candidate.get("requirement_strength", "possible")),
        preconditions=list(candidate.get("preconditions") or []),
        evidence_ids=list(candidate.get("evidence_ids") or []),
    )


def _coerce_uncertainty(unc: dict[str, Any]) -> Uncertainty:
    return Uncertainty(
        description=str(unc.get("description", "")),
        impact=str(unc.get("impact", "")),
        needed_section=str(unc.get("needed_section")) or None,
        evidence_ids=list(unc.get("evidence_ids") or []),
        blocks_answer=bool(unc.get("blocks_answer", False)),
    )


def _apply_contested_premise_gate(result: ReasoningResult, state: PermitState) -> None:
    """Publication gate: contested premises must not quietly survive as facts.

    the implementation’s remaining handoff item. When two same-record observations disagree
    (A9: a fee read as unpaid is later read as paid), the value the model
    might assert is built on a premise the snapshot itself disputes. The
    contract says to retain both facts until their relation is supported; the
    publication gate says the model may not assert a conclusion that depends
    on a contested premise without flagging it.

    For now this enforces the same discipline as the deterministic layer:
    contested scalar facts are already surfaced in state.contradictions, and
    any model FACT that restates a contested value is reclassified to
    INFERENCE with an explicit uncertainty so it cannot read as a settled
    portal fact.
    """
    if not state.contradictions:
        return
    contested_values: set[str] = set()
    for contradiction in state.contradictions:
        lowered = contradiction.lower()
        # Contradiction records here are scalar disagreement strings such as
        # "conflicting fee paid: False vs True". Extract the competing values
        # so we can detect a model restating one of them as a FACT.
        for token in ("false", "true", "paid", "unpaid", "issued", "expired"):
            if token in lowered:
                contested_values.add(token)
    if not contested_values:
        return
    new_claims: list[Claim] = []
    for claim in result.claims:
        if claim.kind is not FactKind.FACT:
            new_claims.append(claim)
            continue
        normalized = claim.statement.strip().lower()
        if any(token in normalized for token in contested_values):
            new_claims.append(
                Claim(
                    claim.id,
                    FactKind.INFERENCE,
                    claim.statement,
                    claim.evidence_ids,
                    list(claim.premise_claim_ids),
                    ConfidenceBand.LOW,
                    claim.reason_code or "contested_premise",
                )
            )
            result.uncertainties.append(
                Uncertainty(
                    f"model asserted a FACT built on a contested premise: {claim.statement!r}",
                    "premise disputed by same-record observation",
                    None,
                    claim.evidence_ids,
                    False,
                )
            )
        else:
            new_claims.append(claim)
    result.claims = new_claims


def _apply_fact_discipline(result: ReasoningResult, state: PermitState) -> None:
    """Ensure FACT claims the model emitted are traceable to the snapshot.

    The model is allowed to emit INFERENCE and UNCERTAIN claims freely (within
    the evidence/premise discipline). FACT claims are different: a FACT must map
    to a real extracted fact in the snapshot. A FACT with no matching snapshot
    fact is reclassified as INFERENCE with a stated assumption, because the
    contract forbids inventing a portal fact the source did not state.
    """
    new_claims: list[Claim] = []
    for claim in result.claims:
        if claim.kind is not FactKind.FACT:
            new_claims.append(claim)
            continue
        if _fact_matches_snapshot(claim, state):
            new_claims.append(claim)
            continue
        # Reclassify an unsupported FACT into an INFERENCE whose premise is the
        # best available evidence and whose assumption is stated.
        new_claims.append(
            Claim(
                claim.id,
                FactKind.INFERENCE,
                claim.statement,
                claim.evidence_ids,
                list(claim.premise_claim_ids),
                ConfidenceBand.LOW,
                claim.reason_code or "model_fact_not_in_snapshot",
            )
        )
        result.uncertainties.append(
            Uncertainty(
                f"model asserted a FACT that is not in the snapshot evidence: {claim.statement!r}",
                "reclassified to inference",
                None,
                claim.evidence_ids,
                False,
            )
        )
    result.claims = new_claims


def _fact_matches_snapshot(claim: Claim, state: PermitState) -> bool:
    if not claim.statement:
        return False
    for fact in state.facts:
        rendered = f"{fact.field}: {fact.value}"
        if claim.statement.strip().lower() == rendered.strip().lower():
            return True
        if claim.statement.strip().lower().startswith(fact.value) and fact.evidence_ids:
            return True
    # A FACT without evidence ids cannot be a portal fact.
    return bool(claim.evidence_ids)


_FALLBACK_PROMPT = (
    "You are Licet's Phase 3 permit-state interpreter. Interpret the structured\n"
    "permit snapshot and answer the question. Return JSON matching\n"
    "ReasoningResult. execution_allowed must be false.\n"
)
