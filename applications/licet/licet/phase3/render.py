"""Narrative rendering from a validated Phase 3 ``ReasoningResult``.

The reasoning contract's last gate: the user-facing answer is generated *from
the structured result only* — never from raw page text — and it must preserve
the FACT / INFERENCE / UNCERTAIN distinctions, every simultaneous material
blocker, and the classification (confirmed gate / observed problem / potential
impediment). This renderer is deterministic: it introduces no new claims, no
requirements, and no confidence upgrades while formatting.
"""
from __future__ import annotations

from licet.phase3.state import (
    Blocker,
    FactKind,
    NextActionCandidate,
    ReasoningResult,
)

_CLASSIFICATION_LABEL = {
    "confirmed_gate": "confirmed gate",
    "observed_problem": "observed problem",
    "potential_impediment": "potential impediment",
}


def _bullet(text: str) -> str:
    return f"- {text}"


def render_blockers(result: ReasoningResult) -> list[str]:
    lines: list[str] = []
    for blocker in result.blockers:
        label = _CLASSIFICATION_LABEL.get(blocker.classification, blocker.classification)
        line = f"{blocker.description} ({label}"
        if blocker.affects_stage:
            line += f", affects: {blocker.affects_stage}"
        line += ")"
        lines.append(_bullet(line))
    return lines


def render_next_actions(result: ReasoningResult) -> list[str]:
    """Candidates with their requirement strength, never stated as obligations."""
    lines: list[str] = []
    for candidate in result.next_actions:
        strength = candidate.requirement_strength
        prefix = {
            "required": "Required:",
            "likely": "Likely next:",
            "possible": "Possible:",
        }.get(strength, f"{strength.capitalize()}:")
        lines.append(_bullet(f"{prefix} {candidate.action} — {candidate.reason}"))
    return lines


def render_claims(result: ReasoningResult) -> list[str]:
    lines: list[str] = []
    facts = [claim for claim in result.claims if claim.kind is FactKind.FACT]
    for claim in facts:
        lines.append(_bullet(claim.statement))
    return lines


def render_uncertainties(result: ReasoningResult) -> list[str]:
    lines = [_bullet(u.description) for u in result.uncertainties]
    if result.contradictions:
        lines.append(_bullet("Conflicting record evidence: " + "; ".join(result.contradictions)))
    return lines


def render_answer(result: ReasoningResult) -> str:
    """The full structured answer, question-shaped.

    Sections appear only when they carry content, and unsupported conclusions
    are structurally impossible here: every line comes from a validated claim,
    blocker or candidate that already carries its evidence and classification.
    """
    sections: list[str] = []

    if result.answerability == "conflicting":
        sections.append(
            "The record contains conflicting evidence, so a single confident answer is "
            "not possible:"
        )
    elif result.answerability in {"needs_data", "partial"} and not result.claims:
        needed = ", ".join(item["section"] for item in result.needed_sections) or "additional sections"
        sections.append(
            f"I cannot answer this from the evidence gathered so far; the {needed} "
            "section(s) still need to be read."
        )
        return "\n\n".join(sections)

    facts = render_claims(result)
    if facts:
        # A disputed value must not be presented as settled portal fact
        # (adversarial review A14): the heading carries the qualification, the
        # named conflicts stay in the uncertainties block below.
        heading = (
            "Facts reported by the portal (some are disputed; see Uncertainties):"
            if result.contradictions
            else "Facts reported by the portal:"
        )
        sections.append(heading + "\n" + "\n".join(facts))

    blockers = render_blockers(result)
    if blockers:
        sections.append("What is in the way:\n" + "\n".join(blockers))

    actions = render_next_actions(result)
    if actions:
        sections.append("Possible next steps (not yet executed):\n" + "\n".join(actions))

    uncertainties = render_uncertainties(result)
    if uncertainties:
        sections.append("Uncertainties:\n" + "\n".join(uncertainties))

    if result.answerability == "partial" and result.claims:
        needed = ", ".join(item["section"] for item in result.needed_sections)
        if needed:
            sections.append(
                f"This answer is partial: the {needed} section(s) have not been read yet."
            )

    return "\n\n".join(sections)
