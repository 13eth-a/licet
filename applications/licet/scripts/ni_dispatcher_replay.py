"""Replay the licet dispatcher against a live Solari session — READ-ONLY.

This is the reconciliation the Phase 0 review asked for: it drives the real
package (`SolariSession` → `SolariClient` → `ToolDispatcher` → `AgentState`),
not raw Playwright, and it imports `licet` rather than re-deriving portal
knowledge in a standalone script.

Flow, all read-only:
  navigate portal -> login (SSO iframe) -> My Records -> open a record by
  clicking its number -> read the record detail -> negative error-taxonomy
  check. Nothing is submitted, scheduled, paid, cancelled or attested; the one
  consequential click it attempts is expected to be *held by the guard* before
  it ever reaches the browser.

Run:  .venv/bin/python scripts/ni_dispatcher_replay.py
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
from licet.agent.stop_conditions import check_stop_condition  # noqa: E402
from licet.browser import accela  # noqa: E402
from licet.browser.dispatcher import ToolDispatcher  # noqa: E402
from licet.browser.solari_client import SolariSession  # noqa: E402
from licet.eval.records import KNOWN_RECORDS  # noqa: E402
from licet.safety.guard import deny_approval  # noqa: E402

OUTDIR = Path("logs/ni_backoffice")
TARGET_RECORD = "BLD26-00472"  # Right of Way Use Permit, created 2026-09-20


def out(message: str) -> None:
    print(message, flush=True)


def stamp_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _record_fixture(permit_id: str):
    for record in KNOWN_RECORDS:
        if record.permit_id == permit_id:
            return record
    return None


class Replay:
    """Collects check results so a partial failure is still informative."""

    def __init__(self) -> None:
        self.checks: list[dict] = []

    def check(self, name: str, ok: bool, detail: str = "") -> None:
        self.checks.append({"check": name, "ok": bool(ok), "detail": detail})
        out(f"  [{'PASS' if ok else 'FAIL'}] {name}{f' — {detail}' if detail else ''}")

    @property
    def passed(self) -> int:
        return sum(1 for check in self.checks if check["ok"])


async def main() -> int:
    replay = Replay()
    session = SolariSession()
    state = AgentState(
        goal=f"Open record {TARGET_RECORD} and read its status (read-only replay)"
    )
    stamp = stamp_now()
    report: dict = {"generated": stamp, "checks": replay.checks}

    try:
        out("launching Solari session…")
        client = await session.client()
        dispatcher = ToolDispatcher(client)
        out(f"live client ready: {client.page.url[:80]}")

        # 1. navigate to the portal
        home = f"{accela.PORTAL_ROOT}/Default.aspx"
        outcome = await dispatcher.execute({"name": "navigate", "args": {"url": home}}, state)
        replay.check(
            "navigate portal",
            outcome["success"],
            f"{outcome.get('url')} err={outcome['error'] and outcome['error']['kind']}",
        )

        # 2. read the logged-out page (fields/frames/notices)
        outcome = await dispatcher.execute({"name": "read_page", "args": {}}, state)
        data = outcome.get("data") or {}
        replay.check(
            "read_page on portal home",
            outcome["success"] and bool(data.get("frames")),
            f"frames={len(data.get('frames') or [])} fields={len(data.get('fields') or [])}",
        )

        # 3. login through the CivicId SSO iframe
        login = await client.login(
            os.environ.get("ACCELA_TEST_USERNAME", "").strip(),
            os.environ.get("ACCELA_TEST_PASSWORD", "").strip(),
        )
        replay.check(
            "login via SSO iframe",
            login.ok and bool(login.data.get("authenticated")),
            f"url={login.url}",
        )
        report["checks"] = replay.checks

        # 4. My Records: the verified read path
        outcome = await dispatcher.execute(
            {"name": "navigate", "args": {"url": accela.MY_RECORDS_URL}}, state
        )
        replay.check("navigate My Records", outcome["success"], f"{outcome.get('url')}")

        outcome = await dispatcher.execute({"name": "read_page", "args": {}}, state)
        data = outcome.get("data") or {}
        replay.check(
            "read_page reports flow position",
            outcome["success"] and (data.get("flow") or {}).get("flow") == "my_records",
            f"flow={data.get('flow')}",
        )
        replay.check(
            "no login notice after navigation (auth survived)",
            not data.get("notices"),
            f"notices={data.get('notices')}",
        )
        text = data.get("text") or ""
        replay.check(
            f"{TARGET_RECORD} listed in My Records",
            TARGET_RECORD in text,
            f"{len(text)} chars of visible text",
        )

        # 5. open the record by clicking its number. The contract requires an
        #    explicit intent for this: a grid link is neither a known read
        #    label nor a commit point.
        outcome = await dispatcher.execute(
            {
                "name": "click",
                "args": {"target": TARGET_RECORD, "by": "text", "intent": "open_record"},
            },
            state,
        )
        clicked = outcome["success"]
        replay.check(
            "click record result (intent=open_record)",
            clicked,
            f"resolution={outcome['resolution']['provenance']} "
            f"err={outcome['error'] and outcome['error']['kind']}",
        )

        if not clicked:
            fixture = _record_fixture(TARGET_RECORD)
            caps = (fixture.expected_state.get("capids") if fixture else None) or {}
            fallback = accela.detail_url(
                caps.get("capID1", "REC26"), caps.get("capID2", "00000"),
                caps.get("capID3", "000QG"),
            )
            out(f"  falling back to the deep link: {fallback}")
            await dispatcher.execute({"name": "navigate", "args": {"url": fallback}}, state)

        # 6. the record detail read path
        outcome = await dispatcher.execute({"name": "read_page", "args": {}}, state)
        data = outcome.get("data") or {}
        flow = (data.get("flow") or {}).get("flow")
        replay.check(
            "record detail flow detected",
            flow == "record_detail",
            f"flow={flow} step={(data.get('flow') or {}).get('step')}",
        )
        detail_text = data.get("text") or ""
        replay.check(
            "record detail shows the record number + status",
            TARGET_RECORD in detail_text and "Record Status" in detail_text,
            f"{len(detail_text)} chars",
        )
        replay.check(
            "state.flow_* synced by the dispatcher",
            state.flow_name == "record_detail",
            f"flow_name={state.flow_name} step={state.flow_step}",
        )
        sections = [link for link in ("Schedule an Inspection", "Record Info",
                                      "Payments", "Attachments")
                    if link in detail_text]
        replay.check(
            "record sections present",
            "Record Info" in detail_text,
            f"sections={sections}",
        )
        report["detail_text_head"] = detail_text[:600]

        shot = OUTDIR / f"{stamp}_dispatcher_replay_detail.png"
        outcome = await dispatcher.execute(
            {"name": "screenshot", "args": {"path": str(shot)}}, state
        )
        replay.check("screenshot captured", outcome["success"], str(shot))

        # 7. the guard in the live loop: a consequential click must be held
        #    before it reaches the browser (no approval here, so it stays held).
        blocked = await dispatcher.execute(
            {
                "name": "click",
                "args": {"target": "Submit Application", "by": "text"},
            },
            state,
        )
        replay.check(
            "consequential click held by the guard",
            blocked["blocked"] is True and blocked["semantic_action"] == "submit_application",
            f"decision={blocked['authorization']['decision']} "
            f"stop={check_stop_condition(state)}",
        )
        replay.check(
            "held action never reached the browser",
            blocked["success"] is False and blocked["error"]["kind"] == "auth_required",
            f"error={blocked['error']['kind']}",
        )
        deny_approval(state)

        # 8. the error taxonomy, live: the double-prefixed URL shape that bit us
        bad = f"{accela.PORTAL_ROOT}/NULLISLAND/Cap/CapDetail.aspx"
        outcome = await dispatcher.execute({"name": "navigate", "args": {"url": bad}}, state)
        replay.check(
            "ACA error page classified (not just an HTTP failure)",
            outcome["success"] is False
            and outcome["error"]["kind"] == "portal_error",
            f"kind={outcome['error'] and outcome['error']['kind']}",
        )

    except Exception as exc:  # noqa: BLE001 - report, do not mask
        replay.check("replay completed without exception", False, repr(exc))
    finally:
        try:
            await session.close()
        except Exception:
            pass

    report["checks"] = replay.checks
    report["passed"] = replay.passed
    report["total"] = len(replay.checks)
    OUTDIR.mkdir(parents=True, exist_ok=True)
    (OUTDIR / f"{stamp}_dispatcher_replay.json").write_text(
        json.dumps(report, indent=2)
    )

    out(f"\n=== dispatcher replay: {replay.passed}/{len(replay.checks)} checks passed ===")
    for check in replay.checks:
        if not check["ok"]:
            out(f"  FAILED: {check['check']} — {check['detail']}")
    return 0 if replay.passed == len(replay.checks) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
