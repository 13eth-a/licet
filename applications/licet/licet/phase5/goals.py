"""conservative goal parsing: preserve restrictions, abstain on unknown actions"""
import re
from datetime import date, timedelta
from licet.phase5.state import Goal
from licet.phase4.dates import normalize_date_constraints
from licet.safety.policy import detect_constraint_conflict


def parse_goal(text: str, *, reference: date | None = None) -> Goal:
    if not text.strip():
        raise ValueError("goal is empty")
    low = text.casefold().replace("’", "'")
    prohibited = {"payment", "signature", "application_submission"}
    constraints = tuple(m.group().strip() for m in re.finditer(
        r"(?:without|do not|don't|no|never)\b[^.;\n]+", text, re.I))
    no_changes = bool(re.search(r"(?:don't|do not|no|without)\s+(?:make\s+|making\s+)?(?:any\s+)?(?:changes|changing|modifications)", low))
    no_cancel = bool(re.search(r"(?:don't|do not|no|without)\s+cancell?", low))
    for verb, operation_name in (("schedul", "schedule"), ("reschedul", "reschedule"), ("cancell?", "cancel")):
        if re.search(r"(?:don't|do not|no|without|never)\s+" + verb, low):
            prohibited.add(operation_name)
    if no_changes:
        prohibited.update(("schedule", "reschedule", "cancel"))
    # remove restrictions before detecting the requested positive operation
    positive = re.split(r"\b(?:without|but|do not|don't|never)\b", low)[0]
    operation = "reschedule" if re.search(r"\breschedule\b", positive) else "cancel" if re.search(r"\bcancel\b", positive) else "schedule"
    explicit = bool(re.search(r"\b(?:book|schedule|reschedule|cancel)\b", positive))
    broad = any(x in positive for x in (
        "get ready", "ready for", "do everything possible", "fix whatever",
        "handle the outstanding inspection", "permit moving", "as close to approval",
        "as close as possible", "safe progress"))
    autonomous = explicit or broad
    # a request that contradicts its own prohibition has no single reading; it is reported as
    # constraint_conflict rather than silently interpreted either way (phase 6 checklist)
    clarification = detect_constraint_conflict(text)
    autonomous = autonomous and not no_changes
    record = re.search(r"\bpermit\s+([A-Z]+\d*[\w]*-\d[\w-]*|\d{5,})\b", text, re.I)
    address = re.search(r"\bat\s+(\d[^,;\n]+?)(?=,|\s+(?:figure|and|without|but|for permit)\b|$)", text, re.I)
    lookup = f"permit {record.group(1)}" if record else f"permit at {address.group(1)}" if address else None
    target = re.search(r"\b(?:book|schedule|reschedule|cancel)\s+(?:the\s+)?(?:earliest available\s+)?(.+?)\s+inspection\b", text, re.I)
    inspection = target.group(1).strip() if target else None
    if inspection and inspection.casefold() in {"next", "required", "outstanding", "existing", "an", "my"}:
        inspection = None
    temporal = re.search(r"\b(?:earliest available\s+)?next week\b|\bafter (?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\b|\b(?:before|by) [A-Za-z]+ \d{1,2}(?:, \d{4})?\b|\b(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\b", text, re.I)
    instruction = temporal.group() if temporal else "earliest available" if "earliest available" in low else None
    dates = normalize_date_constraints(instruction, reference=reference)
    end = dates.end - timedelta(days=1) if instruction and instruction.lower().startswith("before ") and dates.end else dates.end
    # a leading "read only:" is the *grant* (an inquiry permission), not an extra restriction on a
    # mutation (prompt-006)
    restricted = re.sub(r"^\s*read[- ]only\s*[:,]?\s*", "", low)
    if re.search(r"\b(?:except|unless|only|avoid|no later|no earlier)\b|\d{4}-\d{2}-\d{2}", restricted):
        clarification = "Resolve the additional restriction before mutation"
    if any(x in restricted for x in ("today", "tomorrow", "am only", "pm only", "morning", "afternoon", "at 9")):
        clarification = "Resolve the requested date/time constraint before mutation"
    desired = ("permit_verified", "blockers_identified")
    if autonomous:
        outcome = {"schedule": "inspection_scheduled", "reschedule": "inspection_rescheduled", "cancel": "inspection_cancelled"}[operation]
        desired = ("permit_verified", "next_inspection_identified", outcome)
    if re.search(r"\b(?:get|make)\b.*\bapproved\b", positive):
        desired = ("permit_verified", "permit_approved")
    return Goal(text, record.group(1) if record else None, lookup, constraints, desired,
        tuple(sorted(prohibited)), autonomous, inspection, operation,
        date_instruction=instruction, date_window_start=dates.start.isoformat() if dates.start else None,
        date_window_end=end.isoformat() if end else None,
        preferred_date=dates.preferred.isoformat() if dates.preferred else None,
        clarification=clarification, vague=not explicit and not broad)
