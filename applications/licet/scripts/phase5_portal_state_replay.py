#!/usr/bin/env python
"""phase 5 portal-state replay (portal integration review)"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from licet.phase3 import accela_extract  # noqa: E402
from licet.phase3.accela_extract import (  # noqa: E402
    _inspection_lines_as_rows,
    fees_observation,
    inspections_observation,
)
from licet.phase3.extract import extract_partial_state  # noqa: E402
from licet.phase3.reasoning import understand  # noqa: E402
from licet.phase5.state import World, reasoning_is_sound  # noqa: E402

KEY = "NULLISLAND/Building/REC26/00000/00014"
URL = "https://aca-test.accela.com/NULLISLAND/Cap/CapDetail.aspx?Module=Building&TabName=Building&capID1=REC26&capID2=00000&capID3=00014&agencyCode=NULLISLAND"


def payload(text: str) -> dict:
    return {"url": URL, "text": text, "loading": [], "truncated": False}


_LEGACY_DATE_FORMATS = ("%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d", "%d-%b-%Y", "%b %d, %Y")

from datetime import datetime as _legacy_datetime  # noqa: E402


def legacy_normalize_date(value):
    """the pre-review adapter kept dates raw; only the extractor's money parse knew about currency"""
    text = str(value or "").strip()
    for fmt in _LEGACY_DATE_FORMATS:
        try:
            return _legacy_datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def legacy_inspection_table_dates(text: str) -> list[dict]:
    """pre-review table path: dates stayed raw mm/dd/yyyy (h02), a 'due date' column defeated fees header recognition, and a result word in the status column produced no result (h06)"""
    rows = []
    fields = None
    for line in text.splitlines():
        line = line.strip()
        if "|" not in line:
            continue
        cells = [c.strip() for c in line.split("|")]
        if fields is None:
            if "Completed Date" in cells:
                fields = ["type" if c.lower().startswith("inspection") else c.lower().replace(" ", "_") for c in cells]
            continue
        row = {}
        for field, value in zip(fields, cells):
            if field and value and field not in row:
                row[field] = value
        if len(row) >= 2:
            rows.append(row)
    return rows


def legacy_text_rows(text: str) -> list[dict]:
    """pre-review text path: the trailing date token was dropped entirely (h02b)"""
    rows = []
    for line in str(text or "").splitlines():
        line = line.strip()
        if not line or "|" not in line:
            continue
        cells = [c.strip() for c in line.split("|") if c.strip()]
        status = next((c for c in cells if accela_extract and _legacy_lifecycle(c)), None)
        result = next((c for c in cells if _legacy_result(c)), None)
        if status is None and result is None:
            continue
        type_cell = next((c for c in cells if c not in {status, result}), None)
        if not type_cell:
            continue
        row = {"type": type_cell}
        if status:
            row["status"] = status
        if result:
            row["result"] = result
        rows.append(row)
    return rows


def _legacy_lifecycle(text: str):
    from licet.phase3.extract import normalize_lifecycle
    return normalize_lifecycle(text)


def _legacy_result(text: str):
    from licet.phase3.extract import normalize_result
    return normalize_result(text)


def legacy_inspection_coverage(text: str, rows: list) -> str:
    """pre-review: rows -> complete, regardless of a declared-empty marker (h05)"""
    from licet.browser import accela
    if rows:
        return "complete"
    if accela.declares_no_inspections(text):
        return "explicitly_empty"
    return "partial"


def case_h02_mmdd_dates():
    """real aca dates must reach the iso-consuming ordering logic"""
    from licet.phase3.rules import _attempt_order
    from licet.phase3.state import Inspection

    text = ("Inspection | Status | Result | Completed Date\n"
            "Rough Electrical | Completed | Failed | 09/18/2026\n"
            "Rough Electrical | Completed | Passed | 09/20/2026")
    legacy_rows = legacy_inspection_table_dates(text)
    # the pre-review tree kept mm/dd/yyyy in permitstate; _attempt_order only parses iso, so a real
    # later-pass was reported as order "unknown"
    legacy = [
        r.get("completed_date") for r in legacy_rows
    ] + [
        _attempt_order(
            Inspection("Rough Electrical", completed_date="09/18/2026", result_normalized="FAILED"),
            Inspection("Rough Electrical", completed_date="09/20/2026", result_normalized="PASSED"),
        )
    ]
    state = extract_partial_state(inspections_observation(payload(text)))
    current = [i.completed_date for i in state.inspections] + [
        _attempt_order(state.inspections[0], state.inspections[1])
    ]
    return legacy, current, current[-1] == "later" and "/" not in "".join(current[:-1])


def case_h02b_text_row_date():
    text = "Rough Electrical | Insp Scheduled | 05-20-2026"
    legacy = legacy_text_rows(text)
    current = _inspection_lines_as_rows(text)
    return [r.get("scheduled_date") for r in legacy], [r.get("scheduled_date") for r in current], bool(current and current[0].get("scheduled_date"))


def case_h06_status_column_failure():
    text = "Inspection | Status | Completed Date\nRough Electrical | Failed | 09/18/2026"
    legacy_rows = legacy_inspection_table_dates(text)
    legacy_state = extract_partial_state({"record_key": KEY, "section": "inspections", "coverage": "complete", "url": URL, "rows": legacy_rows})
    legacy_blockers = [b.type for b in understand(legacy_state, "What is blocking this permit?").blockers]
    state = extract_partial_state(inspections_observation(payload(text)))
    current_blockers = [b.type for b in understand(state, "What is blocking this permit?").blockers]
    return legacy_blockers, current_blockers, "failed_inspection" in current_blockers


def case_h01_legend_line():
    text = "Rough Electrical | Insp Scheduled | 05-20-2026\nScheduled | Completed | Failed"
    legacy = legacy_text_rows(text)
    current = _inspection_lines_as_rows(text)
    legacy_types = [r["type"] for r in legacy]
    current_types = [r["type"] for r in current]
    return legacy_types, current_types, "Completed" not in current_types


def case_h05_declared_empty_beside_rows():
    text = "Inspections\nYou have not added any inspections.\nRough Electrical | Completed | Failed"
    rows = _inspection_lines_as_rows(text)
    legacy = legacy_inspection_coverage(text, rows)
    current = inspections_observation(payload(text))["coverage"]
    return legacy, current, current == "partial"


def case_h03_in_collection():
    text = "Fee | Amount | Balance | Status\nPlan Check Fee | $200.00 | $200.00 | In Collection"
    legacy_state = extract_partial_state({"record_key": KEY, "section": "fees", "coverage": "complete", "url": URL,
                                          "rows": [{"description": "Plan Check Fee", "amount": "$200.00", "balance": "$200.00", "paid": None}]})
    legacy = (legacy_state.fees[0].paid, legacy_state.fees[0].due)
    fee = extract_partial_state(fees_observation(payload(text))).fees[0]
    return legacy, (fee.paid, fee.due), fee.paid is False and fee.due is True


def case_h04_due_date_header():
    """'due date' defeated fee header recognition; the grid degraded to money lines that mangled the description"""
    from licet.phase3.accela_extract import _money_lines_as_rows, _section_table_rows

    text = "Fee | Amount | Due Date\nPlan Check Fee | $74.50 | 09/30/2026"
    legacy = _money_lines_as_rows(text)
    current = _section_table_rows(text, "fees")
    return legacy, current, bool(current) and current[0].get("description") == "Plan Check Fee"


def case_h07_planner_consequence():
    """the failure mode the assignment names: bad state, good planner"""
    text = ("Inspection | Status | Result | Completed Date\n"
            "Rough Electrical | Completed | Failed | 09/18/2026\n"
            "Rough Electrical | Completed | Passed | 09/20/2026")

    def blocking_wording(rows):
        state = extract_partial_state({"record_key": KEY, "section": "inspections",
                                       "coverage": "complete", "url": URL, "rows": rows})
        result = understand(state, "get ready for next inspection")
        return [u.description for u in result.uncertainties if u.blocks_answer]

    legacy = blocking_wording(legacy_inspection_table_dates(text))
    current = blocking_wording(inspections_observation(payload(text))["rows"])
    return legacy, current, (
        any("attempt order is not established" in text for text in legacy)
        and any("scope" in text and "order is not established" not in text for text in current)
    )


def case_positive_control():
    """scope rendered + real dates: the pass resolves the failure"""
    text = ("Inspection | Status | Result | Completed Date | Scope\n"
            "Rough Electrical | Completed | Failed | 09/18/2026 | Unit A\n"
            "Rough Electrical | Completed | Passed | 09/20/2026 | Unit A")
    state = extract_partial_state(inspections_observation(payload(text)))
    result = understand(state, "get ready for next inspection")
    return "n/a (pre-review failed this too)", not result.blockers, not result.blockers


CASES = [
    ("H02", "MM/DD/YYYY dates reach the ISO-only ordering logic", case_h02_mmdd_dates),
    ("H02b", "text-path inspection row keeps its date", case_h02b_text_row_date),
    ("H06", "outcome in the status column is not lost", case_h06_status_column_failure),
    ("H01", "legend line does not fabricate an inspection row", case_h01_legend_line),
    ("H05", "declared-empty marker beside rows degrades coverage", case_h05_declared_empty_beside_rows),
    ("H03", "'In Collection' wording reads unpaid", case_h03_in_collection),
    ("H04", "'Due Date' header does not defeat the fees table", case_h04_due_date_header),
    ("H07", "planner completion gate on extracted (not injected) state", case_h07_planner_consequence),
    ("C1", "positive control: scope + dates resolve the failure", case_positive_control),
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", type=Path, default=None, help="write evidence JSON here")
    args = parser.parse_args()

    rows = []
    failures = 0
    for case_id, label, case in CASES:
        legacy, current, ok = case()
        failures += int(not ok)
        detail = ""
        if isinstance(current, tuple) and len(current) == 2 and isinstance(current[0], list):
            detail = f" blocking premise: {current[0]}" if current[0] else ""
        print(f"  {case_id:<5} legacy={legacy!r:<24} current={current!r}{detail}")
        print(f"        {'PASS' if ok else 'FAIL'} — {label}")
        rows.append({"case": case_id, "label": label, "legacy": _safe(legacy),
                     "current": _safe(current), "passed": ok})

    print(f"\ncases: {len(rows)}  passed: {len(rows) - failures}  failed: {failures}")
    if args.json:
        args.json.write_text(json.dumps({"cases": rows, "passed": failures == 0,
                                         "total": len(rows), "failures": failures}, indent=2) + "\n")
        print(f"evidence written to {args.json}")
    return 1 if failures else 0


def _safe(value):
    try:
        json.dumps(value)
        return value
    except TypeError:
        return repr(value)


if __name__ == "__main__":
    raise SystemExit(main())
