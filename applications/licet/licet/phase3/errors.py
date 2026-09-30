"""phase 3 error taxonomy"""
from __future__ import annotations

from enum import Enum


class Phase3ErrorCode(str, Enum):
    """why a section or a whole understanding pass fell short"""

    # a section the route asked for does not render on this record/portal (e.g
    INSPECTIONS_NOT_FOUND = "INSPECTIONS_NOT_FOUND"
    FEES_NOT_FOUND = "FEES_NOT_FOUND"
    HISTORY_NOT_FOUND = "HISTORY_NOT_FOUND"
    DOCUMENTS_NOT_FOUND = "DOCUMENTS_NOT_FOUND"
    CONDITIONS_NOT_FOUND = "CONDITIONS_NOT_FOUND"

    # content existed but could not be parsed into structured rows
    HISTORY_PARSE_FAILED = "HISTORY_PARSE_FAILED"
    STATE_EXTRACTION_FAILED = "STATE_EXTRACTION_FAILED"

    # two sources on the same record disagree and nothing resolves them
    CONFLICTING_RECORD_STATE = "CONFLICTING_RECORD_STATE"

    # a conclusion's premise is missing; abstain instead of guessing
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"

    # a substantive status word maps to no agency supported label; the raw value is preserved, never
    # substring guessed (architecture review review p1 #2)
    UNSUPPORTED_STATUS = "UNSUPPORTED_STATUS"

    FOREIGN_RECORD_EVIDENCE = "FOREIGN_RECORD_EVIDENCE"


SECTION_ERROR_CODES: dict[str, Phase3ErrorCode] = {
    "inspections": Phase3ErrorCode.INSPECTIONS_NOT_FOUND,
    "fees": Phase3ErrorCode.FEES_NOT_FOUND,
    "history": Phase3ErrorCode.HISTORY_NOT_FOUND,
    "documents": Phase3ErrorCode.DOCUMENTS_NOT_FOUND,
    "conditions": Phase3ErrorCode.CONDITIONS_NOT_FOUND,
}


class Phase3Error(Exception):
    """a phase 3 failure carrying its machine readable code and provenance"""

    def __init__(self, code: Phase3ErrorCode | str, message: str, *, section: str | None = None) -> None:
        self.code = Phase3ErrorCode(code) if not isinstance(code, Phase3ErrorCode) else code
        self.section = section
        super().__init__(message)

    def as_dict(self) -> dict[str, str | None]:
        return {"code": self.code.value, "section": self.section, "message": str(self)}
