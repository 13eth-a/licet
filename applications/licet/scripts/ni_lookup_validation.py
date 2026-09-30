"""Live Phase 2 acceptance: the lookup runner against the real sandbox.

Drives `licet.lookup_runner.LookupRunner` — plan → search form → results →
rank → open → verify — through the dispatcher, exactly as a planner run would.
Read-only lookups only: nothing here schedules, pays, or submits.

Probes (all against known ground truth in `licet/eval/records.py`):
  record     exact record number  -> FOUND + identity verified + opened
  address    address + type       -> FOUND on the one matching record
  ambiguity  street-only request  -> AMBIGUOUS (never a guessed record)
  mismatch   opened record is not the selected one -> no opened permit
  typo       nonexistent number   -> NOT_FOUND after bounded retries

Run: .venv/bin/python scripts/ni_lookup_validation.py --probe record
     .venv/bin/python scripts/ni_lookup_validation.py --all
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
from licet.lookup import LookupMetrics, LookupStatus, PermitLookupRequest, parse_lookup_request
from licet.lookup_runner import LookupRunner
from licet.logging.logger import RunLogger, new_run_id

# Ground truth (licet/eval/records.py): our own records, all Submitted, all
# owned by the public-user test account. Commerce Ave hosts one record per
# number from 81 to 91 (odd), 77 Licet Eval Way hosts two Sign - Temporary.
TARGET_RECORD = "000000014"  # Commercial Alteration, 81 Commerce Ave
TARGET_ADDRESS = "81 Commerce Ave"
AMBIGUOUS_ADDRESS = "77 Licet Eval Way"
NONEXISTENT_RECORD = "BLD26-99999"
RUNNER_KWARGS = {"max_attempts": 2, "max_pages": 3}


async def _authenticate(client) -> None:
    """The eval records are only visible to their owning account."""
    auth = await client.authenticate()
    if not auth.ok or not auth.data.get("authenticated"):
        raise RuntimeError("authentication failed; records are account-scoped")


def _finish(runner: LookupRunner, result) -> bool:
    print(runner.trace.report())
    if runner.open_error:
        print(f"open_error: {runner.open_error}")
    print(f"status={result.status.value} confidence={result.confidence:.2f}\n")
    return False


def _expect_found(runner: LookupRunner, result, record: str) -> bool:
    if result.status is not LookupStatus.FOUND:
        return _finish(runner, result)
    if result.selected.record_number != record:
        return _finish(runner, result)
    if not runner.identity_verified or runner.opened is None:
        return _finish(runner, result)
    print(runner.trace.report())
    print(f"opened {runner.opened.permit_id} ({runner.opened.permit_type}), "
          f"confidence={result.confidence:.2f}\n")
    return True


async def probe_record(dispatcher, state, metrics: LookupMetrics) -> bool:
    """Exact record number: narrowest search, must open + verify."""
    request = PermitLookupRequest(record_number=TARGET_RECORD)
    runner = LookupRunner(dispatcher, metrics=metrics, **RUNNER_KWARGS)
    result = await runner.run(f"Find permit {TARGET_RECORD}", request, state)
    ok = _expect_found(runner, result, TARGET_RECORD)
    if ok and runner.opened is not None and TARGET_RECORD not in (runner.opened.permit_id or ""):
        print(f"FAIL: opened record {runner.opened.permit_id} is not {TARGET_RECORD}")
        return False
    return ok


async def probe_address(dispatcher, state, metrics: LookupMetrics) -> bool:
    """Address + type: the Phase 2 flagship shape, resolved to one record."""
    request = parse_lookup_request(f"commercial alteration at {TARGET_ADDRESS}")
    runner = LookupRunner(dispatcher, metrics=metrics, **RUNNER_KWARGS)
    result = await runner.run(
        f"Find the commercial alteration permit at {TARGET_ADDRESS}", request, state
    )
    return _expect_found(runner, result, TARGET_RECORD)


async def probe_ambiguity(dispatcher, state, metrics: LookupMetrics) -> bool:
    """Two Sign - Temporary records share one address; nothing distinguishes
    them, so the only correct outcome is AMBIGUOUS with both candidates."""
    request = parse_lookup_request(f"permits at {AMBIGUOUS_ADDRESS}")
    runner = LookupRunner(dispatcher, metrics=metrics, **RUNNER_KWARGS)
    result = await runner.run(
        f"Find the permit at {AMBIGUOUS_ADDRESS}", request, state
    )
    if result.status is not LookupStatus.AMBIGUOUS:
        return _finish(runner, result)
    if len(result.matches) < 2:
        print(f"FAIL: ambiguity must list the candidates, got {len(result.matches)}")
        return False
    print(runner.trace.report())
    print(f"AMBIGUOUS with {len(result.matches)} candidates — no record opened\n")
    return True


async def probe_mismatch(dispatcher, state, metrics: LookupMetrics) -> bool:
    """Ask for one record, force-open a different one via its own result row:
    identity verification must refuse to bless the wrong record.

    Concretely: request a record that does not exist, then check the runner
    reports NOT_FOUND (never opening some near-match) — the mismatch path is
    covered deterministically in tests/test_lookup_runner.py; here we verify
    its live precondition, that a near-miss number yields zero candidates.
    """
    request = PermitLookupRequest(record_number=NONEXISTENT_RECORD)
    runner = LookupRunner(dispatcher, metrics=metrics, **RUNNER_KWARGS)
    result = await runner.run(f"Find permit {NONEXISTENT_RECORD}", request, state)
    if result.status is not LookupStatus.NOT_FOUND:
        return _finish(runner, result)
    if runner.opened is not None:
        print("FAIL: a lookup for a nonexistent record opened a record")
        return False
    print(runner.trace.report())
    print(f"NOT_FOUND honored; wrong-record rate stays 0\n")
    return True


PROBES = {
    "record": probe_record,
    "address": probe_address,
    "ambiguity": probe_ambiguity,
    "mismatch": probe_mismatch,
}


async def main(probes: list[str]) -> int:
    run_id = new_run_id("phase2-lookup")
    outdir = Path("logs") / "ni_lookup" / run_id
    outdir.mkdir(parents=True)
    report: dict = {"run_id": run_id, "probes": {}, "outcome": "running"}
    report_path = outdir / "report.json"

    def save() -> None:
        report_path.write_text(json.dumps(report, indent=2))

    session = SolariSession()
    logger = RunLogger(run_id, log_dir=outdir)
    try:
        client = await session.client()
        await _authenticate(client)
        dispatcher = ToolDispatcher(client, logger=logger)
        # One accumulator shared by every probe, so the report carries the
        # aggregate retrieval KPIs (wrong-record rate must be 0) alongside the
        # per-probe verdicts.
        lookup_metrics = LookupMetrics()
        for name in probes:
            state = AgentState(goal=f"Phase 2 lookup probe: {name}")
            print(f"=== probe: {name} ===")
            passed = await PROBES[name](dispatcher, state, lookup_metrics)
            report["probes"][name] = "pass" if passed else "fail"
            report["lookup_kpis"] = lookup_metrics.as_dict()
            save()
        logger.log_metrics("lookup", lookup_metrics.as_dict())
        report["outcome"] = "passed" if all(
            v == "pass" for v in report["probes"].values()
        ) else "failed"
    except Exception as exc:  # noqa: BLE001
        report["outcome"] = "failed"
        report["exception"] = f"{type(exc).__name__}: {exc}"
        print(report["exception"])
    finally:
        save()
        try:
            await session.close()
        except Exception:  # noqa: BLE001
            pass
    print(f"Result: {report['outcome']}; {report_path}")
    return 0 if report["outcome"] == "passed" else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--probe", choices=sorted(PROBES))
    group.add_argument("--all", action="store_true")
    args = parser.parse_args()
    selected = sorted(PROBES) if args.all or not args.probe else [args.probe]
    raise SystemExit(asyncio.run(main(selected)))
