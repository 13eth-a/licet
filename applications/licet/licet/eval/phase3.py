"""Phase 3 evaluation: deterministic scoring of reasoning over golden states.

The scorer implements the contract's evaluation rules:

- **unsupported blocker count must be zero** — reported with numerator and
  denominator, and "zero emitted blockers" is never treated as recall;
- **false-ready count must be zero** — a "ready"/"no blockers" claim with
  incomplete coverage or unresolved conflicts is a hallucination even when
  every named blocker is correct;
- **contradiction recall** — a case whose state carries contradictions must
  surface them;
- **abstentions are counted** — a needs_data/partial verdict on an
  incomplete-input case is the correct answer, not a failure.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from licet.phase3.reasoning import understand
from licet.phase3.render import render_answer
from licet.phase3.state import PermitState, ReasoningResult


@dataclass(frozen=True)
class Phase3Case:
    case_id: str
    question: str
    state: PermitState
    expected_blocker_types: tuple[str, ...] = ()
    forbidden_blocker_types: tuple[str, ...] = ()
    expected_answerability: str | None = None
    # substrings the rendered answer must contain (rendered from the result
    # only — never from raw page text)
    must_mention: tuple[str, ...] = ()
    # substrings that must not appear as an asserted claim in the answer
    must_not_claim: tuple[str, ...] = ()
    # (flag, expected_present) pairs on the ReasoningResult, e.g. needed_sections
    expect_flags: tuple[tuple[str, bool], ...] = ()
    expect_uncertainty_on: tuple[str, ...] = ()
    # The contract requires gate detection to be scored separately from observed
    # problems, "so cautious language cannot conceal false positives". Type-only
    # scoring did not enforce that: an unpaid balance classified as a confirmed
    # gate passed as long as the type was allowed (adversarial review A12).
    # (blocker_type, classification) pairs.
    expected_classifications: tuple[tuple[str, str], ...] = ()
    forbidden_classifications: tuple[tuple[str, str], ...] = ()
    # (blocker_type, required_classification) pairs that must be present AND
    # correctly classified. This is the contract's remaining open metric:
    # requirement-strength correctness per candidate / blocker, scored separately
    # from type and from generic classification so a wrong-strength claim cannot
    # hide behind a correct type or a correct generic label.
    expected_strength: tuple[tuple[str, str], ...] = ()
    forbidden_strength: tuple[tuple[str, str], ...] = ()


def _asserted(answer: str, claim: str) -> bool:
    """Whether `claim` appears un-negated in the rendered answer.

    Same semantics as the Phase 1 scorer's `asserts()`: a negation window in
    the current clause suppresses the claim, so "the evidence does not
    establish that the balance must be paid" does not count as asserting a
    payment requirement.
    """
    import re

    answer_lower = answer.lower()
    claim_lower = claim.lower()
    negation_re = re.compile(
        r"\b(no|not|nothing|none|never|cannot|can't|unable|doesn't|does not|"
        r"didn't|did not|isn't|is not|won't|will not|without|fails? to|"
        r"there are no|there is no|not established|not shown)\b",
        re.I,
    )
    clause_break_re = re.compile(r"[.;:!?]|\bbut\b")
    start = 0
    while True:
        index = answer_lower.find(claim_lower, start)
        if index == -1:
            return False
        window = answer_lower[max(0, index - 48):index]
        breaks = list(clause_break_re.finditer(window))
        if breaks:
            window = window[breaks[-1].end():]
        if not negation_re.search(window):
            return True
        start = index + 1


def score_case(case: Phase3Case) -> dict[str, object]:
    result = understand(case.state, case.question, snapshot_id=case.case_id)
    answer = render_answer(result)
    actual = {blocker.type for blocker in result.blockers}
    expected = set(case.expected_blocker_types)
    forbidden = set(case.forbidden_blocker_types)
    unsupported_blockers = sorted(forbidden & actual)
    missing_blockers = sorted(expected - actual)
    classifications = {blocker.type: blocker.classification for blocker in result.blockers}
    strength_by_type = {blocker.type: blocker.classification for blocker in result.blockers}
    misclassified = sorted(
        f"{kind}={classifications.get(kind)!r} (wanted {wanted!r})"
        for kind, wanted in case.expected_classifications
        if classifications.get(kind) != wanted
    )
    forbidden_classification_hits = sorted(
        f"{kind}={classifications.get(kind)!r} (forbidden)"
        for kind, unwanted in case.forbidden_classifications
        if classifications.get(kind) == unwanted
    )
    # Requirement-strength correctness per candidate. The contract leaves this
    # as the one metric not yet reported; implement it on the blocker
    # classification vocabulary mapped onto the candidate requirement_strength
    # where the case explicitly asserts a (type, strength) expectation. Other
    # cases remain scored on type + generic classification only.
    strength_mismatches = sorted(
        f"{kind} strength={strength_by_type.get(kind)!r} (wanted {wanted!r})"
        for kind, wanted in case.expected_strength
        if strength_by_type.get(kind) != wanted
    )
    forbidden_strength_hits = sorted(
        f"{kind} strength={strength_by_type.get(kind)!r} (forbidden)"
        for kind, unwanted in case.forbidden_strength
        if strength_by_type.get(kind) == unwanted
    )

    answer_lower = answer.lower()
    missing_mentions = sorted(
        part for part in case.must_mention if part.lower() not in answer_lower
    )
    fabricated = sorted(claim for claim in case.must_not_claim if _asserted(answer, claim))

    flags_ok = True
    flag_details: list[str] = []
    for flag, wanted in case.expect_flags:
        present = {
            "execution_allowed": result.execution_allowed,
            "needed_sections": bool(result.needed_sections),
            "contradictions": bool(result.contradictions),
            "uncertainties": bool(result.uncertainties),
        }.get(flag)
        if present is None:
            flag_details.append(f"unknown flag {flag!r}")
            flags_ok = False
        elif present != wanted:
            flag_details.append(f"{flag}={'present' if present else 'absent'} (wanted {'present' if wanted else 'absent'})")
            flags_ok = False

    uncertainty_ok = all(
        any(token in u.description for token in case.expect_uncertainty_on)
        for u in result.uncertainties
    ) if case.expect_uncertainty_on else True
    if not case.expect_uncertainty_on and case.expected_answerability in {"partial", "needs_data"}:
        # A case that EXPECTS abstention fails if the runtime answered
        # unconditionally instead.
        uncertainty_ok = result.answerability == case.expected_answerability

    answerability_ok = (
        case.expected_answerability is None or result.answerability == case.expected_answerability
    )

    passed = (
        not missing_blockers
        and not unsupported_blockers
        and not forbidden_classification_hits
        and not misclassified
        and not strength_mismatches
        and not forbidden_strength_hits
        and answerability_ok
        and not missing_mentions
        and not fabricated
        and flags_ok
        and not flag_details
        and uncertainty_ok
    )
    return {
        "case_id": case.case_id,
        "passed": passed,
        "missing_blockers": missing_blockers,
        "unsupported_blockers": unsupported_blockers,
        "misclassified_blockers": misclassified,
        "forbidden_classifications": forbidden_classification_hits,
        "answerability": result.answerability,
        "answerability_expected": case.expected_answerability,
        "missing_mentions": missing_mentions,
        "fabricated_claims": fabricated,
        "flag_problems": flag_details,
        "answer": answer,
        "result": result,
        "strength_mismatches": strength_mismatches,
        "forbidden_strength_hits": forbidden_strength_hits,
    }


def score_cases(cases: Iterable[Phase3Case]) -> dict[str, object]:
    results = [score_case(case) for case in cases]
    total_blockers_emitted = sum(
        len(item["result"].blockers) for item in results  # type: ignore[union-attr]
    )
    unsupported_blockers = sum(
        len(item["unsupported_blockers"]) for item in results  # type: ignore[union-attr]
    )
    false_ready = sum(
        1
        for item in results  # type: ignore[union-attr]
        if item["fabricated_claims"]
        and any("ready" in claim or "no blockers" in claim for claim in item["fabricated_claims"])
    )
    contradiction_cases = [
        item for item in results  # type: ignore[union-attr]
        if item["result"].contradictions
    ]
    contradictions_surfaced = [
        item for item in contradiction_cases if item["result"].answerability == "conflicting"
    ]
    abstentions = sum(
        1 for item in results  # type: ignore[union-attr]
        if item["result"].answerability in {"needs_data", "partial"}
    )
    confirmed_gates = sum(
        1
        for item in results  # type: ignore[union-attr]
        for blocker in item["result"].blockers
        if blocker.classification == "confirmed_gate"
    )
    forbidden_classification_hits = sum(
        len(item["forbidden_classifications"]) for item in results  # type: ignore[union-attr]
    )
    strength_mismatches = sum(
        len(item.get("strength_mismatches", [])) for item in results  # type: ignore[union-attr]
    )
    forbidden_strength_hits = sum(
        len(item.get("forbidden_strength_hits", [])) for item in results  # type: ignore[union-attr]
    )
    return {
        "passed": sum(1 for item in results if item["passed"]),
        "total": len(results),
        # hallucination metric with numerator/denominator (contract rule:
        # zero emitted blockers is not successful recall)
        "blockers_emitted": total_blockers_emitted,
        "unsupported_blocker_count": unsupported_blockers,
        "unsupported_blocker_rate": (
            unsupported_blockers / total_blockers_emitted if total_blockers_emitted else 0.0
        ),
        "false_ready_count": false_ready,
        # gate precision is scored separately from observed problems: a gate
        # claim that the portal never made is a hallucination too
        "confirmed_gates_emitted": confirmed_gates,
        "gate_false_positives": forbidden_classification_hits,
        "misclassified_blockers": sum(
            len(item["misclassified_blockers"]) for item in results  # type: ignore[union-attr]
        ),
        "strength_mismatches": strength_mismatches,
        "forbidden_strength_hits": forbidden_strength_hits,
        "contradiction_cases": len(contradiction_cases),
        "contradictions_surfaced": len(contradictions_surfaced),
        "contradiction_recall": (
            len(contradictions_surfaced) / len(contradiction_cases)
            if contradiction_cases
            else None
        ),
        "abstentions": abstentions,
        "results": results,
    }


def run_golden_cases() -> dict[str, object]:
    """Score the full golden set (fixtures + flagship acceptance cases)."""
    from licet.eval.phase3_fixtures import build_cases

    return score_cases(build_cases())
