"""phase 6 observation provenance: which text may authorize, and which is data"""

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


# both kinds are trusted *sources of intent*; `model_output` is not, because a model proposing an action
# is exactly the component phase 6 assumes can be wrong
_TRUSTED_KINDS = frozenset({SourceKind.SYSTEM_POLICY, SourceKind.USER_INSTRUCTION})


def trust_of(kind: SourceKind | str) -> Trust:
    """classify a source kind"""
    try:
        source = SourceKind(kind)
    except ValueError:
        return Trust.UNTRUSTED
    return Trust.TRUSTED if source in _TRUSTED_KINDS else Trust.UNTRUSTED


# phrasings that look like an instruction to a model
_INSTRUCTION_MARKERS = (
    "ignore previous", "ignore all", "ignore the rules", "disregard",
    "you must", "system:", "assistant:", "immediately", "do not tell",
    "override", "click submit", "click the", "upload",
)


@dataclass(frozen=True)
class Observation:
    """one labelled piece of text read from a source"""

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
        """whether this observation may contribute to an intent or an action"""
        return self.trust is Trust.TRUSTED

    @property
    def instruction_like(self) -> bool:
        low = self.text.casefold()
        return any(marker in low for marker in _INSTRUCTION_MARKERS)


def trusted_text(*parts: str | Observation) -> str:
    """join the trusted parts only; untrusted observations are dropped"""
    kept: list[str] = []
    for part in parts:
        if isinstance(part, Observation):
            if part.actionable and part.text:
                kept.append(part.text)
        elif part:
            kept.append(str(part))
    return "\n".join(kept)


def as_untrusted(kind: SourceKind, text: str) -> Observation:
    """build an observation for content whose provenance is not trusted"""
    return Observation(kind, text)


def is_authoritative(observation: Observation) -> bool:
    """whether an observation may authorize an action (trusted, non-empty)"""
    return observation.actionable and bool(observation.text.strip())


__all__ = [
    "Observation", "SourceKind", "Trust", "as_untrusted", "is_authoritative",
    "trust_of", "trusted_text",
]
