from __future__ import annotations

import re


def _tokens(value: str) -> tuple[str, ...]:
    tokens = tuple(sorted(re.findall(r"[a-z0-9]+", (value or "").casefold())))
    # checklist supported whole label alias, not a general synonym/fuzzy rule
    if tokens == ("electric", "rough"):
        return ("electrical", "rough")
    return tokens


def match_inspection_type(requested: str, available: list[str] | tuple[str, ...]) -> str | None:
    """return the portal's spelling only for one unambiguous token exact match"""
    wanted = _tokens(requested)
    if not wanted:
        return None
    matches = [name for name in available if _tokens(name) == wanted]
    return matches[0] if len(matches) == 1 else None
