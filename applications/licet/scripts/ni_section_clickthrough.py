"""Live section click-through on a record detail page — READ-ONLY.

Closes the gap left by `scripts/ni_dispatcher_replay.py`: the record detail
renders `Record Info | Payments | Attachments` (plus `Schedule an Inspection`),
but nobody had clicked *into* those postback links through the dispatcher.

ACA section links are JS `__doPostBack` anchors, not URLs, so the checks are
content-based: after a click the visible text must change, the flow must stay
`record_detail`, and the URL is expected to stay put (recorded as an
observation — it is why section state cannot come from the URL).

Live observations that shaped this script (2026-09-20):

- **`Attachments` is rendered but not actionable** on these records: the anchor
  exists in the DOM and is never visible, so a click is impossible. The client
  reports that as `not_actionable` (naming the matched-but-hidden selector),
  which is a different problem from `not_found` — and the distinction was added
  because this run exposed it.
- Sections are **not** per-view: `Payments` was clickable from the `Record Info`
  view, so the summary round-trip is convenience, not a requirement. Each
  section is still opened from the summary so the checks stay comparable.

The section clicks deliberately carry **no** `intent`, so they exercise the
benign-label resolution path. Nothing is submitted, scheduled, paid or
attested; the scheduling entry is probed, then abandoned without interacting
with the wizard.

Run:  .venv/bin/python scripts/ni_section_clickthrough.py
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from licet.agent.state import AgentState  # noqa: E402
from licet.browser import accela  # noqa: E402
from licet.browser.dispatcher import ToolDispatcher  # noqa: E402
from licet.browser.solari_client import SolariSession  # noqa: E402
from licet.eval.records import KNOWN_RECORDS  # noqa: E402

OUTDIR = Path("logs/ni_backoffice")
TARGET_RECORD = "BLD26-00472"
SECOND_RECORD = "BLD26-00467"

# (link text, markers that suggest the right section opened, expected_to_open)
SECTIONS: tuple[tuple[str, tuple[str, ...], bool], ...] = (
    ("Record Info", ("Record Details", "Work Location", "Applicant"), True),
    ("Payments", ("Fee", "Payment", "Balance", "Total"), True),
    ("Attachments", ("Attachment", "Document", "File", "Upload"), False),
)


def out(message: str) -> None:
    print(message, flush=True)


def stamp_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _caps(permit_id: str) -> dict:
    for record in KNOWN_RECORDS:
        if record.permit_id == permit_id:
            return record.expected_state.get("capids") or {}
    return {}


def _detail_link(permit_id: str) -> str:
    caps = _caps(permit_id)
    return accela.detail_url(
        caps.get("capID1", "REC26"), caps.get("capID2", "00000"), caps.get("capID3", "000QG")
    )


class Findings:
    def __init__(self) -> None:
        self.checks: list[dict] = []
        self.observations: list[dict] = []

    def check(self, name: str, ok: bool, detail: str = "") -> bool:
        self.checks.append({"check": name, "ok": bool(ok), "detail": detail})
        out(f"  [{'PASS' if ok else 'FAIL'}] {name}{f' — {detail}' if detail else ''}")
        return bool(ok)

    def observe(self, name: str, detail: str) -> None:
        self.observations.append({"observation": name, "detail": detail})
        out(f"  [note] {name} — {detail}")

    @property
    def passed(self) -> int:
        return sum(1 for check in self.checks if check["ok"])


async def _text(client) -> str:
    result = await client.read_page(include=["text"])
    return (result.data or {}).get("text") or ""


async def main() -> int:
    found = Findings()
    session = SolariSession()
    state = AgentState(goal=f"Read every section of {TARGET_RECORD} (read-only)")
    stamp = stamp_now()
    detail_url = _detail_link(TARGET_RECORD)
    report: dict = {"generated": stamp, "record": TARGET_RECORD, "sections": {}}

    try:
        client = await session.client()
        dispatcher = ToolDispatcher(client)

        login = await client.login(
            os.environ.get("ACCELA_TEST_USERNAME", "").strip(),
            os.environ.get("ACCELA_TEST_PASSWORD", "").strip(),
        )
        found.check(
            "login via SSO iframe",
            login.ok and bool(login.data.get("authenticated")),
            f"url={login.url}",
        )

        outcome = await dispatcher.execute(
            {"name": "navigate", "args": {"url": detail_url}}, state
        )
        found.check("open record detail deep link", outcome["success"], detail_url)

        outcome = await dispatcher.execute({"name": "read_page", "args": {}}, state)
        summary = (outcome.get("data") or {}).get("text") or ""
        found.check(
            "summary shows the record",
            TARGET_RECORD in summary and "Record Status" in summary,
            f"{len(summary)} chars, flow={state.flow_step}",
        )
        before_url = outcome.get("url")

        # --- the actual click-through -------------------------------------
        for index, (label, markers, should_open) in enumerate(SECTIONS):
            if index:
                await dispatcher.execute(
                    {"name": "navigate", "args": {"url": detail_url}}, state
                )
            previous = await _text(client)
            outcome = await dispatcher.execute(
                {"name": "click", "args": {"target": label, "by": "text"}}, state
            )
            clicked = outcome["success"] and not outcome["blocked"]
            provenance = outcome["resolution"]["provenance"]
            error = outcome["error"] or {}

            found.check(
                f"'{label}' resolves as a read-only section label",
                provenance == "benign_target",
                f"provenance={provenance}",
            )
            if should_open:
                found.check(
                    f"click '{label}'",
                    clicked,
                    f"err={error.get('kind')}",
                )
            else:
                # The portal renders this link without making it actionable;
                # the contract is that we say so precisely, never NOT_FOUND and
                # never a silent no-op.
                found.check(
                    f"'{label}' opens or is reported dead-but-rendered",
                    clicked or error.get("kind") == "not_actionable",
                    f"opened={clicked} kind={error.get('kind')}",
                )
            if not clicked:
                found.observe(f"'{label}' not clickable", error.get("message", "")[:200])
                report["sections"][label] = {
                    "opened": False,
                    "kind": error.get("kind"),
                    "message": error.get("message"),
                }
                continue

            after_result = await dispatcher.execute({"name": "read_page", "args": {}}, state)
            after = (after_result.get("data") or {}).get("text") or ""
            after_url = after_result.get("url")

            opened = after.strip() != previous.strip()
            found.check(
                f"'{label}' changed the page content", opened, f"{len(previous)} -> {len(after)} chars"
            )
            hit = [marker for marker in markers if marker.lower() in after.lower()]
            found.observe(f"'{label}' markers", f"matched={hit or 'none'}")
            found.observe(
                f"'{label}' URL",
                "unchanged (postback link)" if after_url == before_url else f"changed -> {after_url}",
            )

            shot = OUTDIR / f"{stamp}_section_{label.replace(' ', '_').lower()}.png"
            await dispatcher.execute({"name": "screenshot", "args": {"path": str(shot)}}, state)
            report["sections"][label] = {
                "opened": True,
                "provenance": provenance,
                "url_changed": after_url != before_url,
                "markers": hit,
                "text_head": after[:400],
                "screenshot": str(shot),
            }

            # sections are not per-view: check that another section is still
            # reachable from inside this one
            if index + 1 < len(SECTIONS):
                other = SECTIONS[index + 1][0]
                probe = await dispatcher.execute(
                    {"name": "click", "args": {"target": other, "by": "text"}}, state
                )
                found.observe(
                    f"cross-view click: '{other}' from the '{label}' view",
                    f"success={probe['success']} "
                    f"kind={(probe['error'] or {}).get('kind')}",
                )

        found.check(
            "flow position still record_detail after section clicks",
            state.flow_name == "record_detail",
            f"flow_name={state.flow_name} step={state.flow_step}",
        )
        found.observe(
            "section state is not URL-addressable",
            f"flow_step stayed '{state.flow_step}' across sections — "
            "the planner must track the open section in state",
        )

        # --- is the dead Attachments link record-specific or universal? ---
        second_url = _detail_link(SECOND_RECORD)
        await dispatcher.execute({"name": "navigate", "args": {"url": second_url}}, state)
        await _text(client)
        probe = await dispatcher.execute(
            {"name": "click", "args": {"target": "Attachments", "by": "text"}}, state
        )
        found.observe(
            f"Attachments on {SECOND_RECORD}",
            f"success={probe['success']} kind={(probe['error'] or {}).get('kind')}",
        )

        # --- scheduling entry, probed and abandoned -----------------------
        await dispatcher.execute({"name": "navigate", "args": {"url": detail_url}}, state)
        outcome = await dispatcher.execute(
            {
                "name": "click",
                "args": {
                    "target": "Schedule an Inspection",
                    "by": "text",
                    "intent": "navigate",
                },
            },
            state,
        )
        found.observe(
            "schedule entry probe",
            f"clicked={outcome['success']} flow={state.flow_name}/{state.flow_step} "
            f"url={(outcome.get('url') or '')[:90]}",
        )
        await dispatcher.execute({"name": "navigate", "args": {"url": detail_url}}, state)

        found.check(
            "guard never released a consequential action",
            state.pending_approval is None,
            "no approval was granted during this run",
        )

    except Exception as exc:  # noqa: BLE001 - report, do not mask
        found.check("run completed without exception", False, repr(exc))
    finally:
        try:
            await session.close()
        except Exception:
            pass

    report["checks"] = found.checks
    report["observations"] = found.observations
    report["passed"] = found.passed
    report["total"] = len(found.checks)
    OUTDIR.mkdir(parents=True, exist_ok=True)
    (OUTDIR / f"{stamp}_section_clickthrough.json").write_text(json.dumps(report, indent=2))

    out(f"\n=== section click-through: {found.passed}/{len(found.checks)} checks passed ===")
    for check in found.checks:
        if not check["ok"]:
            out(f"  FAILED: {check['check']} — {check['detail']}")
    return 0 if found.passed == len(found.checks) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
