from __future__ import annotations

import calendar
import datetime as dt
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class DateConstraints:
    start: dt.date | None = None
    end: dt.date | None = None
    preferred: dt.date | None = None
    earliest: bool = False

    def allows(self, value: dt.date) -> bool:
        return (self.start is None or value >= self.start) and (self.end is None or value <= self.end)


def _parse_date(value: str) -> dt.date:
    for fmt in ("%Y-%m-%d", "%B %d, %Y", "%b %d, %Y", "%m/%d/%Y"):
        try:
            return dt.datetime.strptime(value.strip(), fmt).date()
        except ValueError:
            pass
    raise ValueError(f"unsupported date: {value!r}")


def _next_week(reference: dt.date) -> tuple[dt.date, dt.date]:
    monday = reference + dt.timedelta(days=(7 - reference.weekday()))
    return monday, monday + dt.timedelta(days=6)


def normalize_date_constraints(text: str | None, *, reference: dt.date | None = None) -> DateConstraints:
    """convert bounded supported instructions to concrete inclusive dates"""
    reference = reference or dt.date.today()
    lowered = (text or "").casefold().strip()
    start = end = preferred = None
    if not lowered:
        return DateConstraints(earliest=True)
    if "next week" in lowered:
        start, end = _next_week(reference)
    explicit = re.search(r"\b(?:before|by|through)\s+([A-Za-z]+\s+\d{1,2}(?:,\s*\d{4})?|\d{4}-\d{2}-\d{2})", text or "", re.I)
    if explicit:
        end = _parse_date(explicit.group(1) if "," in explicit.group(1) or "-" in explicit.group(1) else f"{explicit.group(1)}, {reference.year}")
    after = re.search(r"\bafter\s+([A-Za-z]+)(?:\s+(\d{1,2}))?", text or "", re.I)
    if after:
        try:
            weekday = list(calendar.day_name).index(after.group(1).capitalize())
        except ValueError:
            weekday = -1
        if weekday < 0:
            raise ValueError(f"unsupported weekday: {after.group(1)!r}")
        delta = (weekday - reference.weekday()) % 7 or 7
        start = reference + dt.timedelta(days=delta + 1)
    weekday_match = re.search(r"\b(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\b", text or "", re.I)
    if weekday_match and "next week" not in lowered and not after:
        target = list(calendar.day_name).index(weekday_match.group(1).capitalize())
        delta = (target - reference.weekday()) % 7
        preferred = reference + dt.timedelta(days=delta)
    if "earliest" not in lowered and preferred is None and start is None and end is None:
        raise ValueError(f"unsupported date constraint: {text!r}")
    return DateConstraints(start=start, end=end, preferred=preferred, earliest=True)


def _parsed_dates(available: list[str] | tuple[str, ...]) -> list[tuple[dt.date, str]]:
    """parse, drop unparseable values, and collapse duplicate days to the first spelling seen (a day is one day regardless of how the portal renders it)"""
    parsed: list[tuple[dt.date, str]] = []
    seen: set[dt.date] = set()
    for raw in available:
        try:
            value = _parse_date(raw)
        except ValueError:
            continue
        if value in seen:
            continue
        seen.add(value)
        parsed.append((value, raw))
    return parsed


def _is_selectable(value: dt.date, constraints: DateConstraints) -> bool:
    """whether selection may pick this day: inside the window, and when the user named an exact day exactly that day"""
    if not constraints.allows(value):
        return False
    return constraints.preferred is None or value == constraints.preferred


def _distance_from_window(value: dt.date, constraints: DateConstraints) -> int:
    """days between an unselectable day and the nearest edge of the request"""
    if constraints.start and value < constraints.start:
        return (constraints.start - value).days
    if constraints.end and value > constraints.end:
        return (value - constraints.end).days
    if constraints.preferred and value != constraints.preferred:
        return abs((value - constraints.preferred).days)
    return 0


def select_date(available: list[str] | tuple[str, ...], constraints: DateConstraints) -> str | None:
    """select the chronologically earliest allowed portal date; never expand bounds"""
    selectable = [(value, raw) for value, raw in _parsed_dates(available) if _is_selectable(value, constraints)]
    if constraints.preferred:
        # a requested exact date (e.g. "friday") is never silently swapped for another allowed day: the
        # caller must report the constraint failure
        return selectable[0][1] if selectable else None
    return min(selectable, default=(None, None))[1]


def available_dates_from_calendar(months: object, *, reference: dt.date | None = None) -> list[str]:
    """iso dates a portal calendar offers, from `accela.resolve_calendar_months` output (`(year, month, active_days)` tuples, in render order)"""
    dates: set[str] = set()
    for entry in months or ():
        try:
            year, month, active_days = entry
        except (TypeError, ValueError):
            continue
        if not year or not month:
            continue
        for day in active_days or ():
            try:
                dates.add(dt.date(int(year), int(month), int(day)).isoformat())
            except (TypeError, ValueError):
                continue
    return sorted(dates)


def within_constraints(value: dt.date, constraints: DateConstraints) -> bool:
    """public form of the selection predicate, for callers checking a date they did not choose (e.g. verifying a portal reported appointment date)"""
    return _is_selectable(value, constraints)


def closest_alternatives(
    available: list[str] | tuple[str, ...], constraints: DateConstraints, *, limit: int = 3
) -> list[str]:
    """portal dates nearest the request, for *reporting* only"""
    alternatives = [
        (value, raw) for value, raw in _parsed_dates(available) if not _is_selectable(value, constraints)
    ]
    alternatives.sort(key=lambda item: (_distance_from_window(item[0], constraints), item[0]))
    return [raw for _, raw in alternatives[:limit]]
