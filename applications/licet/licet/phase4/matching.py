from __future__ import annotations

import re


def _tokens(value: str) -> tuple[str, ...]:
    tokens = tuple(sorted(re.findall(r"[a-z0-9]+", (value or "").casefold())))
    # Checklist-supported whole-label alias, not a general synonym/fuzzy rule.
    if tokens == ("electric", "rough"):
        return ("electrical", "rough")
    return tokens


def match_inspection_type(requested: str, available: list[str] | tuple[str, ...]) -> str | None:
    """Return the portal's spelling only for one unambiguous token-exact match.

    Punctuation and word order vary across Accela agencies, but substring or
    fuzzy matching is unsafe: ``Electrical`` must not select ``Electrical Final``.
    """
    wanted = _tokens(requested)
    if not wanted:
        return None
    matches = [name for name in available if _tokens(name) == wanted]
    return matches[0] if len(matches) == 1 else None
