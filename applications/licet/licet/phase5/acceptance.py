"""evidence bounded presentation helpers for plan only acceptance reports"""
from __future__ import annotations

from typing import Any


def live_plan_only_summary(report: dict[str, Any]) -> dict[str, Any]:
    """summarize independently recorded read only progress without overclaiming"""
    trace = report.get("trace", [])
    preflight = report.get("preflight", {})
    details = preflight.get("details", {})
    availability = details.get("availability", {})
    months = [str(item.get("month")) for item in availability.get("calendar_months", [])
              if item.get("month")]
    cost = details.get("cost", {})
    signature = details.get("signature", {})
    metrics = report.get("metrics", {})

    no_dates = (
        availability.get("calendar_read") is True
        and availability.get("identity_verified") is True
        and availability.get("availability_status") == "none_in_observed_calendar"
        and not preflight.get("available_dates")
    )
    blocker = None
    if no_dates:
        window = f" in the observed calendar window ({', '.join(months)})" if months else " in the observed calendar window"
        blocker = f"No active inspection dates were available{window}."

    status, error = report.get("status"), report.get("error")
    outcome = f"{status} / {error}" if status and error else status
    permit_verified = any(
        item.get("action") == "FIND_PERMIT" and item.get("success") is True
        for item in trace
    )
    return {
        "acceptance_case": "LIVE_PLAN_ONLY_ACCEPTANCE",
        "goal": "Prepare permit for its required inspection",
        "permit_verified": permit_verified,
        "permit_id": report.get("goal", {}).get("permit_id"),
        "inspection_type": preflight.get("inspection_type"),
        "calendar_read": availability.get("calendar_read") is True,
        "availability_checked": preflight.get("checked") is True,
        "calendar_identity_verified": availability.get("identity_verified") is True,
        "observed_calendar_months": months,
        "blocker": blocker,
        "unresolved": {
            "cost": "not disclosed" if cost.get("status") == "unknown" else cost.get("value"),
            "signature_requirement": "not disclosed" if signature.get("status") == "unknown" else signature.get("required"),
        },
        "result": outcome,
        "mutations_attempted": metrics.get("mutations_attempted", 0),
        "mutations_verified": metrics.get("mutations_verified", 0),
    }


def format_live_plan_only_summary(summary: dict[str, Any]) -> str:
    """render the concise terminal acceptance trace"""
    lines = [f"Goal: {summary['goal']}"]
    if summary["permit_verified"]:
        lines.append(f"✓ Permit verified: {summary['permit_id']}")
    if summary["inspection_type"]:
        lines.append(f"✓ Required inspection identified: {summary['inspection_type']}")
    if summary["calendar_read"]:
        lines.append("✓ Inspection calendar opened")
    if summary["calendar_identity_verified"]:
        lines.append("✓ Calendar identity verified")
    if summary["availability_checked"]:
        lines.append("✓ Availability checked")
    if summary["blocker"]:
        lines.extend(("", "BLOCKER", summary["blocker"]))
    lines.extend(("", "UNRESOLVED", f"Cost: {summary['unresolved']['cost']}",
                  f"Signature requirement: {summary['unresolved']['signature_requirement']}",
                  "", "RESULT", str(summary["result"]),
                  f"Mutations: {summary['mutations_attempted']}"))
    return "\n".join(lines)
