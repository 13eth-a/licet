"""Phase 6 observation provenance: which text may authorize, and which is data.

The checklist's labelling rule, made executable: instruction-looking content
that Licet reads off a portal is *information*, never authorization. Inspector
comments ("Upload corrected documents immediately."), document names and any
other page text are `UNTRUSTED`; only the system policy and the user's own
instruction are `TRUSTED`. A caller that wants to act must build its intent from
trusted text only — `trusted_text` is the one supported way to concatenate
sources, and it silently drops untrusted ones rather than folding portal prose
into a decision.

This module is deliberately small and dependency-free so any layer (guard,
policy engine, planner) can label an observation without importing the browser.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Trust(StrEnum):
    TRUSTED = "trusted"
    UNTRUSTED = "untrusted"


class SourceKind(StrEnum):
    SYSTEM_POLICY = "system_policy"
    USER_INSTRUCTION = "user_instruction"
    MODEL_OUTPUT = "model_output"
    PORTAL_TEXT = "portal_text"
    INSPECTOR_COMMENT = "inspector_comment"
    DOCUMENT = "document"
    PORTAL_CONTROL = "portal_control"


# Both kinds are trusted *sources of intent*; `MODEL_OUTPUT` is not, because a
# model proposing an action is exactly the component Phase 6 assumes can be
# wrong. Portal text, inspector comments, document names and rendered control
# labels are observed state.
_TRUSTED_KINDS = frozenset({SourceKind.SYSTEM_POLICY, SourceKind.USER_INSTRUCTION})


def trust_of(kind: SourceKind | str) -> Trust:
    """Classify a source kind. Unknown kinds are never trusted."""
    try:
        source = SourceKind(kind)
    except ValueError:
        return Trust.UNTRUSTED
    return Trust.TRUSTED if source in _TRUSTED_KINDS else Trust.UNTRUSTED


# Phrasings that look like an instruction to a model. They are recorded as an
# observation property, not acted on: an untrusted instruction can never become
# an action regardless of how imperative it reads.
_INSTRUCTION_MARKERS = (
    "ignore previous", "ignore all", "ignore the rules", "disregard",
    "you must", "system:", "assistant:", "immediately", "do not tell",
    "override", "click submit", "click the", "upload",
)


@dataclass(frozen=True)
class Observation:
    """One labelled piece of text read from a source."""

    kind: SourceKind
    text: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.kind, SourceKind):
            object.__setattr__(self, "kind", SourceKind(str(self.kind)))

    @property
    def trust(self) -> Trust:
        return trust_of(self.kind)

    @property
    def actionable(self) -> bool:
        """Whether this observation may contribute to an intent or an action."""
        return self.trust is Trust.TRUSTED

    @property
    def instruction_like(self) -> bool:
        low = self.text.casefold()
        return any(marker in low for marker in _INSTRUCTION_MARKERS)


def trusted_text(*parts: str | Observation) -> str:
    """Join the trusted parts only; untrusted observations are dropped.

    Passing an `Observation` that is untrusted contributes nothing. This is the
    choke point that keeps portal text out of decisions even when a caller is
    sloppy about where its strings came from.
    """
    kept: list[str] = []
    for part in parts:
        if isinstance(part, Observation):
            if part.actionable and part.text:
                kept.append(part.text)
        elif part:
            kept.append(str(part))
    return "\n".join(kept)


def as_untrusted(kind: SourceKind, text: str) -> Observation:
    """Build an observation for content whose provenance is not trusted."""
    return Observation(kind, text)


def is_authoritative(observation: Observation) -> bool:
    """Whether an observation may authorize an action (trusted, non-empty)."""
    return observation.actionable and bool(observation.text.strip())


__all__ = [
    "Observation", "SourceKind", "Trust", "as_untrusted", "is_authoritative",
    "trust_of", "trusted_text",
]
