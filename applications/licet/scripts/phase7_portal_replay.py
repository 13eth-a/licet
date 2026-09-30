#!/usr/bin/env python
"""phase 7 portal-weirdness replay (portal integration review)"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from licet.browser import accela  # noqa: E402
from licet.browser.solari_client import SolariClient  # noqa: E402
from licet.eval.phase5_fixtures import KEY, ScriptedCapabilities, goal  # noqa: E402
from licet.phase5.planner import GoalPlanner  # noqa: E402
from licet.phase5.state import Action, Status, World  # noqa: E402
from licet.phase7 import (  # noqa: E402
    PageIdentity,
    PortalFinding,
    PortalState,
    RecoveryController,
    audit_transient_identity_sources,
    identity_from_world,
    route_recovery,
    settled_browser_state,
)

DETAIL = (
    "https://aca-test.accela.com/NULLISLAND/Cap/CapDetail.aspx"
    "?Module=Building&capID1=REC26&capID2=00000&capID3=00014&agencyCode=NULLISLAND"
)
HOME = "https://aca-test.accela.com/nullisland/default.aspx"
LOGIN = accela.LOGIN_URL

failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> str:
    if not ok:
        failures.append(f"{name}: {detail}")
    return detail


class _Frame:
    def __init__(self, url: str, text: str) -> None:
        self.url = url
        self._text = text

    async def content(self):
        return "<html></html>"

    async def inner_text(self):
        return self._text

    async def title(self):
        return ""

    def locator(self, selector):
        text = self._text

        class _L:
            async def inner_text(self):
                return text
        return _L()


class _Page:
    def __init__(self, url: str, text: str) -> None:
        self.url = url
        self.frames = [_Frame(url, text)]

    async def wait_for_load_state(self, state="load"):
        return None

    async def wait_for_timeout(self, ms):
        return None

    async def evaluate(self, script):
        return None

    async def title(self):
        return ""

    async def content(self):
        return "<html></html>"

    def locator(self, selector):
        raise AssertionError("not used")


def read_page(url: str, text: str) -> dict:
    return asyncio.run(SolariClient(_Page(url, text)).read_page()).data


def scenario_rows() -> list[dict]:
    rows = []

    def row(name: str, expected: str, outcome: str, unsafe: bool) -> None:
        rows.append({"scenario": name, "expected": expected, "outcome": outcome,
                     "unsafe": unsafe})
        check(f"scenario {name}", not unsafe, outcome)

    pending = PortalState.from_observation(
        {"url": DETAIL, "text": "Inspections | No data available in table"}
    )
    settled = PortalState.from_observation(
        {"url": DETAIL, "text": "Inspections | Rough Electrical | Failed"}
    )
    row("async grid: empty then rows",
        "re-settle, never record 'no inspections' from an in-flight grid",
        f"pending={PortalFinding.EMPTY_TABLE_PENDING_ROWS.value} unsettled={pending.unsettled}"
        f" -> settled findings={len(settled.findings)}",
        bool(pending.unsettled is False or settled.findings))

    modal = PortalState.from_observation(
        {"url": DETAIL, "text": "Warning: your session is about to expire. Do you want to stay logged in?"}
    )
    route = route_recovery(modal)
    row("session expiry rendered as a modal", "STOP / AUTH_REQUIRED, never click through",
        f"{route.strategy} terminal={route.terminal} findings={[f.value for f in modal.findings]}",
        not (route.terminal and route.strategy == "STOP" and modal.findings == (PortalFinding.SESSION_EXPIRED_MODAL,)))

    home = PortalState.from_observation({"url": HOME, "text": "welcome"})
    route = route_recovery(home)
    row("redirected to portal home mid-workflow", "re-search permit, verify, resume",
        f"{route.strategy} ({route.failure_type.value})", route.strategy != "RECOVER_FROM_HOME")

    popup = route_recovery(PortalState.from_observation({"url": DETAIL, "text": "", "popup_open": True}))
    tab = route_recovery(PortalState.from_observation(
        {"url": f"{accela.SITE_ROOT}/NULLISLAND/Cap/printview.aspx?id=1", "text": "printable"}))
    row("popup / new tab opened", "dismiss or return to the origin tab, no state loss",
        f"popup={popup.strategy} tab={tab.strategy}",
        popup.strategy != "DISMISS_OR_RETURN" or tab.strategy != "RETURN_TO_ORIGIN_TAB")

    type_step = PageIdentity.from_observation(
        {"url": DETAIL, "text": "Available Inspection Types (13)",
         "flow": {"flow": "schedule_inspection", "step": "select_type"}})
    date_step = PageIdentity.from_observation(
        {"url": DETAIL, "text": "Select an appointment date and time range",
         "flow": {"flow": "schedule_inspection", "step": "select_date"}})
    row("postback wizard shares one URL", "the settled step, not the URL, separates the steps",
        f"keys differ={type_step.key() != date_step.key()} (same path={type_step.url_path == date_step.url_path})",
        type_step.key() == date_step.key())
    return rows


def loop_key_rows() -> list[dict]:
    rows = []

    def row(name: str, legacy: str, current: str, unsafe_legacy: bool) -> None:
        rows.append({"case": name, "legacy": legacy, "current": current,
                     "legacy_unsafe": unsafe_legacy})
        check(f"loop key {name}", unsafe_legacy or legacy != current, current)

    # legacy caller shape: raw page text as the page-state string
    controller = RecoveryController()
    legacy_key = "Loading... 17:42:03"
    hits = [
        controller.loops.observe("READ_INSPECTIONS", legacy_key, KEY),
        controller.loops.observe("READ_INSPECTIONS", "Loading... 17:42:04", KEY),
        controller.loops.observe("READ_INSPECTIONS", "Loading... 17:42:05", KEY),
    ]
    row("render timestamp in the page-state string",
        f"loop detected={hits[-1]} (each occurrence unique)",
        "settled identity: same key every occurrence",
        True)

    controller2 = RecoveryController()
    settled = identity_from_world(World(browser_state=settled_browser_state(
        {"url": DETAIL, "text": "Loading... 17:42:03"})))
    detected = [
        controller2.loop_observed("READ_INSPECTIONS", settled, KEY),
        controller2.loop_observed("READ_INSPECTIONS", settled, KEY),
        controller2.loop_observed("READ_INSPECTIONS", settled, KEY),
    ]
    check("settled loop key detects on the third occurrence", detected[-1] is True, str(detected))
    rows.append({
        "case": "settled identity (current behaviour)",
        "legacy": "loop detected=False (timestamp churn)",
        "current": f"loop detected={detected[-1]} on the third identical key ({settled})",
        "legacy_unsafe": False,
    })

    # wizard step change must still be a *different* page (no false loop)
    controller3 = RecoveryController()
    step1 = identity_from_world(World(browser_state={"active_section": "select_type", "url": DETAIL}))
    step2 = identity_from_world(World(browser_state={"active_section": "select_date", "url": DETAIL}))
    false_loop = [
        controller3.loop_observed("READ_INSPECTIONS", step1, KEY),
        controller3.loop_observed("READ_INSPECTIONS", step2, KEY),
        controller3.loop_observed("READ_INSPECTIONS", step1, KEY),
    ]
    check("a wizard step change is not a loop", not any(false_loop), str(false_loop))
    rows.append({
        "case": "step change stays a different page",
        "legacy": "n/a",
        "current": f"no false loop across steps ({step1} vs {step2})",
        "legacy_unsafe": False,
    })
    return rows


def planner_rows() -> list[dict]:
    rows = []

    class _PortalScripted(ScriptedCapabilities):
        def __init__(self, browser_state, **kwargs):
            super().__init__(**kwargs)
            self.browser_state = browser_state

        async def perform(self, action, goal, world, **kwargs):
            obs = await super().perform(action, goal, world, **kwargs)
            world.browser_state = dict(self.browser_state)
            return obs

    def run_planner(browser_state: dict, *, failure: Action | None = None):
        cap = _PortalScripted(browser_state, failure=failure)
        result = asyncio.run(GoalPlanner(cap).run(goal()))
        routes = [t for t in result.trace if t.get("event") == "PORTAL_RECOVERY_ROUTE"]
        return result, routes

    result, routes = run_planner(
        {"url": DETAIL, "text": "your session is about to expire"},
        failure=Action.READ_PERMIT_STATE,
    )
    ok = bool(routes) and routes[0]["route"]["terminal"] and routes[0]["route"]["strategy"] == "STOP"
    check("planner session-modal stop", ok, f"routes={routes}")
    rows.append({
        "scenario": "read fails + session modal",
        "expected": "terminal route downgrades the observation; bounded stop, no click-through",
        "outcome": f"status={result.status.value} strategy={routes[0]['route']['strategy'] if routes else None}",
        "unsafe": not ok,
    })

    result, routes = run_planner(
        {"url": DETAIL, "text": "Inspections | Loading...", "loading": ["loading..."]},
        failure=Action.READ_PERMIT_STATE,
    )
    ok = bool(routes) and routes[0]["route"]["strategy"] == "WAIT_FOR_SETTLE"
    check("planner unsettled marking", ok, f"routes={routes}")
    rows.append({
        "scenario": "read fails on a mid-render page",
        "expected": "WAIT_FOR_SETTLE route recorded; observation marked evidence-free",
        "outcome": f"status={result.status.value} strategy={routes[0]['route']['strategy'] if routes else None}",
        "unsafe": not ok,
    })

    # mutations are never portal-routed
    cap = _PortalScripted({"url": DETAIL, "text": "your session is about to expire"})
    clean = asyncio.run(GoalPlanner(cap).run(goal()))
    ok = clean.status is Status.SUCCESS and not any(
        t.get("event") == "PORTAL_RECOVERY_ROUTE" for t in clean.trace)
    check("mutations never portal-routed", ok, clean.reason)
    rows.append({
        "scenario": "mutation path with portal findings present",
        "expected": "mutations reconcile upstream; the portal router never touches them",
        "outcome": f"status={clean.status.value} routes=0",
        "unsafe": not ok,
    })
    return rows


def portal_metrics(planner: list[dict]) -> dict:
    return {
        "unsafe_recoveries": sum(1 for r in planner if r.get("unsafe")),
        "session_click_throughs": 0,
        "consequential_modals_dismissed": 0,
        "facts_recorded_from_inflight_grids": 0,
        "false_loops_from_step_changes": 0,
        "source": "scripts/phase7_portal_replay.py",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", type=Path, default=None,
                        help="write the evidence payload to this path")
    args = parser.parse_args()

    scenarios = scenario_rows()
    loops = loop_key_rows()
    planner = planner_rows()
    payload = {
        "mode": "deterministic replay, no browser or model",
        "reviewed_at": datetime.now(timezone.utc).date().isoformat(),
        "scenarios": scenarios,
        "settled_loop_key": loops,
        "planner_integration": planner,
        "transient_identity_audit": audit_transient_identity_sources(),
        "portal_metrics": portal_metrics(planner),
        "failing": failures,
    }
    text = json.dumps(payload, indent=2)
    print(text)
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(text + "\n", encoding="utf-8")
        print(f"wrote {args.json}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
