#!/usr/bin/env python
"""Standalone Phase 3 golden + adversarial eval.

Runs offline: it scores the deterministic golden set and the adversarial
counterexamples through the same scorer the unit tests use, with no Solari key,
no browser, and no planner. This is the regression path for the model-level
reasoning stage once it is wired: today it exercises the deterministic baseline
and the validation/publication gates around the model coordinator.

Outputs a machine-readable report to docs/phase3/golden_report.json and prints a
one-line summary suitable for CI.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from licet.eval import phase3 as phase3_eval  # noqa: E402
from licet.eval import phase3_fixtures  # noqa: E402
from licet.phase3 import model_reasoning  # noqa: E402


def _report_path() -> Path:
    return REPO_ROOT / "docs" / "phase3" / "golden_report.json"


def _fmt(v) -> str:
    if isinstance(v, float):
        return f"{v:.4f}"
    return str(v)


def _score_model_path(cases):
    from licet.phase3.model_reasoning import understand_one

    results = []
    for case in cases:
        model_result = understand_one(case.state, case.question, snapshot_id=case.case_id, use_model=True)
        results.append(
            _score_case_model_result(case, model_result, model_result.question)
        )
    return {
        "passed": sum(1 for r in results if r["passed"]),
        "total": len(results),
        "unsupported_blocker_count": sum(len(r["unsupported_blockers"]) for r in results),
        "strength_mismatches": sum(len(r["strength_mismatches"]) for r in results),
        "forbidden_strength_hits": sum(len(r["forbidden_strength_hits"]) for r in results),
        "results": results,
    }


def _score_case_model_result(case, result, question):
    answer = phase3_eval.render_answer(result)
    actual = {blocker.type for blocker in result.blockers}
    expected = set(case.expected_blocker_types)
    forbidden = set(case.forbidden_blocker_types)
    unsupported_blockers = sorted(forbidden & actual)
    missing_blockers = sorted(expected - actual)
    classifications = {blocker.type: blocker.classification for blocker in result.blockers}
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
    strength_mismatches = sorted(
        f"{kind} strength={classifications.get(kind)!r} (wanted {wanted!r})"
        for kind, wanted in getattr(case, "expected_strength", ())
        if classifications.get(kind) != wanted
    )
    forbidden_strength_hits = sorted(
        f"{kind} strength={classifications.get(kind)!r} (forbidden)"
        for kind, unwanted in getattr(case, "forbidden_strength", ())
        if classifications.get(kind) == unwanted
    )
    answer_lower = answer.lower()
    missing_mentions = sorted(
        part for part in case.must_mention if part.lower() not in answer_lower
    )
    fabricated = sorted(claim for claim in case.must_not_claim if phase3_eval._asserted(answer, claim))
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
        "strength_mismatches": strength_mismatches,
        "forbidden_strength_hits": forbidden_strength_hits,
    }




def main() -> int:
    os.environ.setdefault("LICET_PHASE3_MODEL_ENABLED", "0")

    golden = phase3_eval.score_cases(phase3_fixtures.build_cases())
    # Extraction is scored separately from reasoning: these run real read_page
    # payloads through the ACA adapter first, so a per-municipality wording gap
    # cannot hide behind a reasoning pass (reasoning contract, evaluation rules).
    extraction = phase3_fixtures.run_extraction_fixtures()

    # Also exercise the model path deterministically when the env allows it.
    # This is the Phase 3 closure check: the model coordinator, payload, coercion,
    # validation, and publication gates must all pass the same golden set.
    model_enabled = os.environ.get("LICET_PHASE3_MODEL_ENABLED", "0").strip().lower() in {
        "1", "true", "yes",
    }
    if model_enabled:
        model_results = _score_model_path(phase3_fixtures.build_cases())
        print(
            "MODEL-PATH: "
            + str(model_results["passed"])
            + "/"
            + str(model_results["total"])
            + " passed | unsupported_blockers="
            + _fmt(model_results["unsupported_blocker_count"])
            + " | strength_mismatches="
            + _fmt(model_results["strength_mismatches"])
            + " | forbidden_strength_hits="
            + _fmt(model_results["forbidden_strength_hits"])
        )

    rows: list[dict[str, object]] = []
    for item in golden["results"]:  # type: ignore[union-attr]
        result = item["result"]  # type: ignore[union-attr]
        rows.append(
            {
                "case_id": item["case_id"],  # type: ignore[union-attr]
                "passed": item["passed"],  # type: ignore[union-attr]
                "answerability": item["answerability"],  # type: ignore[union-attr]
                "execution_allowed": result.execution_allowed,
                "missing_blockers": item["missing_blockers"],  # type: ignore[union-attr]
                "unsupported_blockers": item["unsupported_blockers"],  # type: ignore[union-attr]
                "fabricated_claims": item["fabricated_claims"],  # type: ignore[union-attr]
                "answer": item["answer"],  # type: ignore[union-attr]
                "flag_problems": item.get("flag_problems"),  # type: ignore[union-attr]
                "blockers": [
                    {
                        "type": b.type,
                        "classification": b.classification,
                        "description": b.description,
                        "confidence": b.confidence,
                        "source": b.source,
                    }
                    for b in result.blockers
                ],
                "next_actions": [
                    {
                        "action": a.action,
                        "requirement_strength": a.requirement_strength,
                        "confidence": a.confidence,
                    }
                    for a in result.next_actions
                ],
            }
        )

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "deterministic_baseline": True,
        "model_reasoning_enabled": model_reasoning._model_enabled(),  # type: ignore[attr-defined]
        "golden": {
            "passed": golden["passed"],  # type: ignore[union-attr]
            "total": golden["total"],  # type: ignore[union-attr]
            "blockers_emitted": golden["blockers_emitted"],  # type: ignore[union-attr]
            "unsupported_blocker_count": golden["unsupported_blocker_count"],  # type: ignore[union-attr]
            "unsupported_blocker_rate": golden["unsupported_blocker_rate"],  # type: ignore[union-attr]
            "false_ready_count": golden["false_ready_count"],  # type: ignore[union-attr]
            "confirmed_gates_emitted": golden["confirmed_gates_emitted"],  # type: ignore[union-attr]
            "gate_false_positives": golden["gate_false_positives"],  # type: ignore[union-attr]
            "misclassified_blockers": golden["misclassified_blockers"],  # type: ignore[union-attr]
            "contradiction_cases": golden["contradiction_cases"],  # type: ignore[union-attr]
            "contradictions_surfaced": golden["contradictions_surfaced"],  # type: ignore[union-attr]
            "contradiction_recall": golden["contradiction_recall"],  # type: ignore[union-attr]
            "abstentions": golden["abstentions"],  # type: ignore[union-attr]
        },
        "case_details": rows,
        "extraction": {
            "passed": sum(1 for item in extraction if item["passed"]),
            "total": len(extraction),
            "fixtures": extraction,
        },
        "model_path": model_results if model_enabled else {"enabled": False},
    }

    _report_path().parent.mkdir(parents=True, exist_ok=True)
    _report_path().write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    print(
        "GOLDEN: "
        + str(golden["passed"])  # type: ignore[union-attr]
        + "/"
        + str(golden["total"])  # type: ignore[union-attr]
        + " passed | unsupported_blockers="
        + _fmt(golden["unsupported_blocker_count"])  # type: ignore[union-attr]
        + " (target: 0) | gate_false_positives="
        + _fmt(golden["gate_false_positives"])  # type: ignore[union-attr]
        + " | false_ready="
        + _fmt(golden["false_ready_count"])  # type: ignore[union-attr]
        + " | contradiction_recall="
        + _fmt(golden["contradiction_recall"])  # type: ignore[union-attr]
        + " | abstentions="
        + _fmt(golden["abstentions"])  # type: ignore[union-attr]
        + " | model_path="
        + (
            str(model_results["passed"])
            + "/"
            + str(model_results["total"])
            + " strength_mismatches="
            + _fmt(model_results["strength_mismatches"])
            + " forbidden_strength_hits="
            + _fmt(model_results["forbidden_strength_hits"])
            if model_enabled
            else "disabled"
        )
        + " | extraction="
        + str(sum(1 for item in extraction if item["passed"]))
        + "/"
        + str(len(extraction))
        + " | report="
        + str(_report_path().relative_to(REPO_ROOT))
    )

    if any(not item["passed"] for item in extraction):
        return 7
    if golden["unsupported_blocker_count"]:  # type: ignore[union-attr]
        return 2
    if golden["gate_false_positives"]:  # type: ignore[union-attr]
        return 3
    if golden["false_ready_count"]:  # type: ignore[union-attr]
        return 4
    if int(golden["strength_mismatches"]):  # type: ignore[union-attr]
        return 5
    if int(golden["forbidden_strength_hits"]):  # type: ignore[union-attr]
        return 6
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
