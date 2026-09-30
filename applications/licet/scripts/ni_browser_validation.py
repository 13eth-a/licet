"""Read-only Phase 1 acceptance: search -> known record -> inspections.

Uses runtime primitives exclusively for mutations; DOM reads provide independent
acceptance evidence. Stops on the first unverified action; never retries a run.
Run: .venv/bin/python scripts/ni_browser_validation.py --runs 10
"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from licet.agent.state import AgentState
from licet.browser import accela
from licet.browser.dispatcher import ToolDispatcher
from licet.browser.solari_client import SolariSession
from licet.logging.logger import RunLogger, new_run_id

SEARCH_URL = f"{accela.PORTAL_ROOT}/Cap/CapHome.aspx?TabName=Home&module=Building"
RECORD = "BLD26-00472"
PERMIT_INPUT = "#ctl00_PlaceHolderMain_generalSearchForm_txtGSPermitNumber"
SEARCH_TYPE = "#ctl00_PlaceHolderMain_ddlSearchType"


async def main(runs: int, inject_click_timeout: bool = False, verification_timeout_ms: int = 10000) -> int:
    run_id = new_run_id("phase1-live")
    outdir = Path("logs/browser_validation") / run_id
    outdir.mkdir(parents=True)
    report = {"run_id": run_id, "requested_runs": runs, "completed_runs": 0,
              "record": RECORD, "verification_timeout_ms": verification_timeout_ms, "checks": [], "steps": [], "outcome": "running"}
    report_path = outdir / "report.json"
    session = SolariSession()
    client = None
    logger = RunLogger(run_id, log_dir=outdir)

    def save():
        report["metrics"] = logger.summary()
        report_path.write_text(json.dumps(report, indent=2))

    def check(name, passed, detail=None):
        report["checks"].append({"name": name, "passed": bool(passed), "detail": detail})
        print(f"[{'PASS' if passed else 'FAIL'}] {name}", flush=True)
        save()
        if not passed:
            raise RuntimeError(name)

    try:
        print(f"Report: {report_path}", flush=True)
        client = await asyncio.wait_for(session.client(verification_timeout_ms=verification_timeout_ms), 150)
        auth = await asyncio.wait_for(client.authenticate(), 120)
        check("authenticated", auth.ok and auth.data.get("authenticated"))
        if inject_click_timeout:
            # Controlled adapter fault after a real, read-only search click.
            # This tests reconciliation without manufacturing a second click.
            resolve = client._resolve
            report["injected_timeout"] = {"kind": "after_search_click", "dispatches": 0}

            class TimeoutAfterClick:
                def __init__(self, locator):
                    self.locator = locator

                def __getattr__(self, name):
                    return getattr(self.locator, name)

                async def click(self, **kwargs):
                    report["injected_timeout"]["dispatches"] += 1
                    await self.locator.click(**kwargs)
                    raise TimeoutError("Injected timeout after real search click completed")

            async def resolve_with_fault(target):
                frame, locator, matches, diagnostics = await resolve(target)
                if target.text == "Search" and locator is not None:
                    locator = TimeoutAfterClick(locator)
                return frame, locator, matches, diagnostics

            client._resolve = resolve_with_fault
        dispatcher = ToolDispatcher(client, logger=logger)
        for index in range(1, runs + 1):
            state = AgentState(goal=f"Read-only validation {index}: search {RECORD} and read inspections")

            async def step(name, **args):
                result = await asyncio.wait_for(dispatcher.execute({"name": name, "args": args}, state), 90)
                report["steps"].append({"iteration": index, "name": name, "args": args, "result": result})
                save()
                check(f"run {index}: {name} {args.get('target', '')}", result["success"], result.get("error"))
                return result

            await step("navigate", url=SEARCH_URL)
            search_controls = await client.page.evaluate("""() => [...document.querySelectorAll('a,button,input[type=submit]')]
                .filter(e => /search/i.test(e.textContent || e.value || e.id))
                .map(e => ({id:e.id, text:e.textContent.trim(), title:e.title,
                    visible:!!e.getClientRects().length, href:e.getAttribute('href')}))""")
            report["search_controls"] = search_controls
            # Preserve the live selector evidence in the report.
            save()
            # Exercise both directions of a real native auto-postback dropdown.
            options = await client.page.locator(SEARCH_TYPE).evaluate(
                "e => [...e.options].map(o => ({label:o.label,value:o.value,selected:o.selected}))")
            report["search_options"] = options
            original = next(o for o in options if o["selected"])
            address = next(o for o in options if "address" in o["label"].lower())
            await step("select", target=SEARCH_TYPE, value=address["label"], intent="search_records")
            await step("select", target=SEARCH_TYPE, value=original["label"], intent="search_records")
            await step("type", target=PERMIT_INPUT, text=RECORD, intent="search_records")
            search_result = await step("click", target="Search", by="text", intent="search_records")
            if inject_click_timeout:
                check(f"run {index}: injected timeout recovered without replay",
                      search_result["data"]["recovered_after_error"]
                      and report["injected_timeout"]["dispatches"] == index)
            result = await step("read_page")
            check(f"run {index}: known record returned", RECORD in result["data"]["text"])
            # Some ACA configurations navigate directly for a unique match.
            if "capdetail.aspx" not in (result.get("url") or "").lower():
                record_links = await client.page.evaluate(r"""() => [...document.querySelectorAll('a')]
                    .filter(e => !!e.getClientRects().length && /^(BLD\d{2}-\d+|\d{9})$/.test(e.textContent.trim()))
                    .map(e => e.textContent.trim())""")
                check(f"run {index}: search filtered to exactly the requested record",
                      record_links == [RECORD], record_links)
                await step("click", target=RECORD, by="text", intent="open_record")
                result = await step("read_page")
            check(f"run {index}: record identity and status", RECORD in result["data"]["text"]
                  and "Record Status" in result["data"]["text"])
            await step("click", target="Record Info", by="text")
            await step("click", target="Inspections", by="text", intent="read_record")
            await step("wait", until_absent="Loading...")
            result = await step("read_page")
            text = result["data"]["text"]
            check(f"run {index}: inspections loaded", not result["data"]["loading"] and any(
                marker in text.lower() for marker in ("schedule an inspection", "no inspections", "upcoming", "schedule or request")))
            report["completed_runs"] = index
            save()
            print(f"Completed {index}/{runs} consecutive workflows", flush=True)
        report["outcome"] = "passed"
    except Exception as exc:
        report["outcome"] = "failed"
        report["exception"] = f"{type(exc).__name__}: {exc}"
        print(report["exception"], flush=True)
        if client is not None:
            try:
                snapshot = await asyncio.wait_for(client.read_page(), 25)
                report["failure_observation"] = snapshot.as_dict()
                observed_state = await asyncio.wait_for(client._action_state(), 10)
                report["failure_readiness"] = [{k: frame.get(k) for k in
                    ("url", "busy", "document_id", "postbacks_completed")} for frame in observed_state["frames"]]
                # Diagnostics only: no mask neutralization or action replay.
                report["failure_dom"] = await asyncio.wait_for(client.page.evaluate('''() => ({
                    buttons: [...document.querySelectorAll('a,button')].filter(e => e.id === 'btnSearch').map(e =>
                        ({id:e.id, cls:e.className, disabled:e.disabled, href:e.getAttribute('href'),
                          onclick:e.getAttribute('onclick')})),
                    busy: window.Sys?.WebForms?.PageRequestManager?.getInstance()?.get_isInAsyncPostBack()
                })'''), 10)
                await asyncio.wait_for(client.screenshot(str(outdir / "failure.png")), 20)
            except Exception as capture_error:
                report["capture_error"] = type(capture_error).__name__
    finally:
        save()
        try:
            await asyncio.wait_for(session.close(), 30)
        except Exception:
            pass
    print(f"Result: {report['outcome']} ({report['completed_runs']}/{runs}); {report_path}", flush=True)
    return 0 if report["outcome"] == "passed" else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=10)
    parser.add_argument("--inject-click-timeout", action="store_true")
    parser.add_argument("--verification-timeout-ms", type=int, default=10000)
    args = parser.parse_args()
    if args.runs < 1:
        parser.error("--runs must be positive")
    raise SystemExit(asyncio.run(main(args.runs, args.inject_click_timeout, args.verification_timeout_ms)))
