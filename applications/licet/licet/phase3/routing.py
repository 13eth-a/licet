"""Question routing for targeted, read-only section retrieval."""
from __future__ import annotations

import re
from dataclasses import dataclass
from licet.phase3.state import PermitState, Section


@dataclass(frozen=True)
class Route:
    sections: tuple[str, ...]
    reason: str
    stop_when: str


_ROUTES = (
    (r"\b(status|permit number|type|address|applicant|parcel|expiration|issued)\b", Route((Section.OVERVIEW.value,), "the answer is stated in the permit overview", "the requested overview fact is sourced")),
    (r"\b(inspection|inspections|inspector|failed|passed|reinspection)\b", Route((Section.INSPECTIONS.value,), "inspection lifecycle, result, or comment evidence is needed", "matching inspection history and linked comments are complete")),
    (r"\b(fee|fees|balance|paid|payment|payments)\b", Route((Section.FEES.value,), "fee state is section-specific", "current fee coverage is complete")),
    (r"\b(document|documents|plan|plans|attachment|attachments|upload|uploads)\b", Route((Section.DOCUMENTS.value,), "document requirements and document status are section-specific", "required-document evidence is complete")),
    (r"\b(condition|conditions|hold|holds|warning|requirement|requirements|blocking|approval|ready|stalled|moving forward)\b", Route((Section.CONDITIONS.value, Section.HISTORY.value), "blocker or readiness questions need explicit conditions and relevant history", "explicit prerequisites and relevant history are complete")),
)

# Questions whose answers are already fully supported by observed evidence —
# readiness/no-blocker claims are suppressed separately by the publication
# rules, so a readiness question never forces a conditions/history tab tour on
# its own (the contract's stop rule: retrieve only sections that could change
# the answer).
_NO_TOUR_PATTERNS = ("what did the inspector say", "did the inspector", "what failed", "what passed")

# "What should happen next?" is a blocker question with next-step framing: it
# needs the same explicit-prerequisite evidence as "what is blocking?".
_NEXT_QUESTION_PATTERN = r"\b(what|which)\s+(should|needs? to|has to|must)\b|\bwhat's next\b|\bwhat is next\b"


def route_question(question: str, state: PermitState | None = None) -> Route:
    """Return the smallest evidence route; current status never triggers a tab tour."""
    text = question.lower()
    if re.search(_NEXT_QUESTION_PATTERN, text):
        # A next-step question routes like a blocker question: explicit
        # prerequisites and relevant history are what could change the answer.
        return Route(
            (Section.CONDITIONS.value, Section.HISTORY.value),
            "a next-step recommendation needs explicit prerequisites and relevant history",
            "explicit prerequisites and relevant history are complete",
        )
    for pattern, route in _ROUTES:
        if re.search(pattern, text):
            if state is not None and route.sections[0] == Section.OVERVIEW.value and state.status:
                return Route(route.sections, "the requested status is already available", route.stop_when)
            return route
    # An evidence-in-hand question never forces a tour: if its own route
    # already matched, this line is unreachable; it only fires for questions
    # whose phrasing mentions "inspector"-type evidence in hand.
    if any(pattern in text for pattern in _NO_TOUR_PATTERNS):
        return Route((Section.INSPECTIONS.value,), "the requested evidence is already in hand", "the cited inspection evidence is quoted")
    return Route((Section.OVERVIEW.value,), "record identity and overview are the minimum available evidence", "the overview has been read")


def needed_sections(question: str, state: PermitState) -> list[dict[str, str]]:
    route = route_question(question, state)
    missing = []
    for section in route.sections:
        coverage = state.coverage.get(section)
        if coverage is None or not coverage.complete:
            missing.append({"section": section, "entity_id": state.record_key or "record", "reason": route.reason, "needed_fact": question, "stop_when": route.stop_when})
    return missing
