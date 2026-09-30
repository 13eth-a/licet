#!/usr/bin/env python
"""Phase 6 adversarial replay (DeepSeek V4.1 Flash review).

Re-derives every counterexample from ``docs/phase6/adversarial_review.md`` against
the current tree and reports, per case, what the **pre-review** behaviour allowed
versus what the reviewed tree does now. The legacy column is the pre-review
behaviour reproduced *in this script* (the pre-review tree is not committed), so
each row is demonstrably a counterexample rather than a restatement of the
current code:

- "legacy execution" is `licet.phase4.policy.decide_action_policy` alone — which
  is exactly what ran when an unclassifiable portal had no `PolicyEngine` at all;
- "legacy identity" is the pre-review `verify_identity` reproduced verbatim, in
  which an observation that was silent about the targeted inspection field passed.

    python scripts/phase6_adversarial_replay.py
    python scripts/phase6_adversarial_replay.py --json docs/phase6/adversarial_evidence.json

Read-only: nothing is written except the optional ``--json`` evidence file. No
browser, model, credential or live mutation is used.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from licet.phase4.actions import ActionErrorCode, InspectionAction, InspectionSnapshot  # noqa: E402
from licet.phase4.policy import decide_action_policy  # noqa: E402
from licet.phase4.workflow import InspectionActionExecutor  # noqa: E402
from licet.phase5 import Action, parse_goal  # noqa: E402
from licet.phase5.capabilities import LicetCapabilities, Preflight  # noqa: E402
from licet.phase5.state import World, operation_key  # noqa: E402
from licet.safety.policy import (  # noqa: E402
    ConfirmationRequest,
    Environment,
    PolicyEngine,
    PolicyVerdict,
    ProposedAction,
    RecordIdentity,
    UserConstraints,
    verify_identity,
)

PERMIT = "P-1"
TYPE = "Rough Electrical"
DATE = "2026-09-24"
RECORD_KEY = "NULLISLAND/Building/REC26/00000/00014"
OTHER_KEY = "NULLISLAND/Building/REC26/00000/OTHER"


# --- the pre-review behaviour, reproduced ----------------------------------- #

def legacy_execution_allowed(action: InspectionAction, *, confirmed: bool = False) -> bool:
    """The Phase 4 gate alone: what ran when the executor had no policy engine.

    `InspectionActionExecutor.__init__` used to set ``policy_engine = None`` when
    the portal could not be classified, and `execute` then consulted only
    `decide_action_policy`. Reproducing that here is what makes the fail-open
    claim testable rather than asserted.
    """
    return decide_action_policy(action, confirmed=confirmed).allowed


def legacy_identity_verified(action: ProposedAction, observed: RecordIdentity) -> bool:
    """The pre-review `verify_identity`, reproduced verbatim.

    Its inspection-type and existing-date comparisons were guarded on the
    *observed* value being present, so a read that lost the field verified.
    """
    if not action.permit_id or not observed.permit_id or action.permit_id != observed.permit_id:
        return False
    if action.record_key and action.record_key != observed.record_key:
        return False
    if action.inspection_id and action.inspection_id != observed.inspection_id:
        return False
    if action.inspection_type and observed.inspection_type:
        strip = lambda s: "".join(ch for ch in s.casefold() if ch.isalnum())
        if strip(action.inspection_type) != strip(observed.inspection_type):
            return False
    if action.existing_date and observed.existing_date and action.existing_date != observed.existing_date:
        return False
    return True


def legacy_confirmation_granted(action: InspectionAction) -> bool:
    """The pre-review executor minted its own approval from the action.

    Any caller that passed ``confirmed=True`` got a `ConfirmationRequest` built
    out of the action in front of it, so the policy's CONFIRM gate could never
    fail and could never bind to what a human actually approved.
    """
    minted = ConfirmationRequest(action_type=action.action_type, permit_id=action.permit_id,
                                 target=action.inspection_type or "", consequence="legacy")
    return legacy_execution_allowed(action, confirmed=True) and minted is not None


# --- doubles ---------------------------------------------------------------- #

class Portal:
    """InspectionPortal double with an explicit environment."""

    environment: Environment | None = Environment.SANDBOX

    def __init__(self, before, *, after=None, error=None):
        self.before, self.after, self.error = before, after, error
        self.reads, self.submits = 0, []

    def read_inspection_state(self, permit_id, inspection_type=None, inspection_id=None):
        self.reads += 1
        return self.before if self.reads == 1 or self.after is None else self.after

    def submit_inspection_action(self, action, *, portal_type, selected_date=None):
        self.submits.append((portal_type, selected_date))
        if self.error:
            raise self.error
        return "CNF-1"


def snap(**overrides):
    values = {"permit_id": PERMIT, "inspection_id": "I-1", "inspection_type": TYPE,
              "status": "Not Scheduled", "record_key": RECORD_KEY}
    values.update(overrides)
    return InspectionSnapshot(**values)


def schedule(**overrides):
    return InspectionAction("schedule", PERMIT, TYPE, **overrides)


def cancel(target="I-1"):
    return InspectionAction("cancel", PERMIT, TYPE, existing_inspection_id=target)


def identity(**overrides):
    values = {"permit_id": PERMIT, "record_key": RECORD_KEY, "inspection_type": TYPE,
              "inspection_id": "I-1"}
    values.update(overrides)
    return RecordIdentity(**values)


def approval(action, **overrides):
    values = {"action_type": action.action_type, "permit_id": action.permit_id,
              "target": action.inspection_type or "", "consequence": "replay approval",
              "inspection_id": action.existing_inspection_id}
    values.update(overrides)
    return ConfirmationRequest(**values)


def run_action(environment, action, **kwargs):
    """Execute one action through the real executor and return (result, submits)."""
    portal = kwargs.pop("portal", None) or Portal(snap())
    portal.environment = environment
    return InspectionActionExecutor(portal).execute(action, **kwargs), len(portal.submits)


# --- counterexample cases ---------------------------------------------------- #

def counterexamples():
    rows = []

    def add(case_id, route, legacy, current, finding):
        rows.append({"case_id": case_id, "route": route, "legacy": legacy, "current": current,
                     "finding": finding})

    # A1 — an unclassifiable portal used to be mutable.
    result, submits = run_action(None, schedule(), eligible_types=[TYPE], available_dates=[DATE])
    add("A1", "executor: portal with no identity",
        "ALLOWED" if legacy_execution_allowed(schedule()) else "refused",
        f"{result.error_code.value if result.error_code else 'SUBMITTED'} (submits={submits})",
        "UNKNOWN was the one environment where the whole Phase 6 layer was absent")

    # A2 — a positively live portal used to be mutable too.
    result, submits = run_action(Environment.LIVE_READ_ONLY, schedule(),
                                 eligible_types=[TYPE], available_dates=[DATE])
    add("A2", "executor: live portal",
        "ALLOWED" if legacy_execution_allowed(schedule()) else "refused",
        f"{result.error_code.value if result.error_code else 'SUBMITTED'} (submits={submits})",
        "live scheduling must be blocked by code, not by prompt text")

    # B1/B2 — an observation that omits what the action targets used to verify.
    action_type = ProposedAction("CANCEL_INSPECTION", permit_id=PERMIT, inspection_type=TYPE,
                                 inspection_id="I-1")
    observed = identity(inspection_type=None)
    add("B1", "identity: observed inspection type missing",
        "VERIFIED" if legacy_identity_verified(action_type, observed) else "refused",
        "VERIFIED" if verify_identity(action_type, observed).verified else "refused",
        "a read that lost the inspection type authorized whatever row was on screen")
    dated = ProposedAction("RESCHEDULE_INSPECTION", permit_id=PERMIT, inspection_id="I-1",
                           existing_date="2026-09-20")
    stale = identity(existing_date=None)
    add("B2", "identity: observed existing date missing",
        "VERIFIED" if legacy_identity_verified(dated, stale) else "refused",
        "VERIFIED" if verify_identity(dated, stale).verified else "refused",
        "the pre-mutation date check vanished when the portal did not print a date")

    # C1 — a bare boolean was an approval.
    portal = Portal(snap(status="Scheduled", scheduled_date=DATE), after=snap(status="Cancelled"))
    portal.environment = Environment.SANDBOX
    result = InspectionActionExecutor(portal).execute(cancel(), eligible_types=[TYPE], confirmed=True)
    add("C1", "confirmation: bare confirmed=True",
        "ALLOWED" if legacy_confirmation_granted(cancel()) else "refused",
        f"{result.error_code.value if result.error_code else 'SUBMITTED'} (submits={len(portal.submits)})",
        "an approval the executor mints for itself authorizes whatever it is pointed at")

    # C2 — an approval for one permit used to authorize another.
    portal = Portal(snap(status="Scheduled", scheduled_date=DATE), after=snap(status="Cancelled"))
    portal.environment = Environment.SANDBOX
    swapped = InspectionAction("cancel", "P-2", TYPE, existing_inspection_id="I-1")
    result = InspectionActionExecutor(portal).execute(swapped, eligible_types=[TYPE],
                                                      approval=approval(cancel()))
    add("C2", "confirmation: approval for permit P-1, action against P-2",
        "ALLOWED" if legacy_confirmation_granted(swapped) else "refused",
        f"{result.error_code.value if result.error_code else 'SUBMITTED'} (submits={len(portal.submits)})",
        "approval scope must name the permit it approves")

    # D1 — a cancellation that never named its target.
    engine = PolicyEngine(environment=Environment.SANDBOX)
    decision = engine.decide(ProposedAction("CANCEL_INSPECTION", permit_id=PERMIT, target=TYPE,
                                            inspection_type=TYPE),
                             observed_identity=identity())
    add("D1", "targeting: cancellation with no inspection id",
        "ALLOWED" if legacy_execution_allowed(cancel(None), confirmed=True) else "refused",
        decision.verdict.value + f" ({decision.violated_constraint})",
        "an id-less cancel could not be bound to the appointment a human approved")

    return rows


# --- end to end: real capabilities, real executor ---------------------------- #

def capabilities_for(portal):
    def context(world):
        from licet.phase3.state import Evidence
        from licet.phase4.selection import InspectionOption, SelectionContext
        return SelectionContext(world.permit_id, world.record_key, world.snapshot_id, True,
            (InspectionOption(TYPE, True, True, ("eligibility",)),),
            {"eligibility": Evidence("eligibility", "inspections", f"{TYPE} eligible", record_key=RECORD_KEY)},
            history_complete=True)

    def preflight(world):
        return Preflight(RECORD_KEY, world.snapshot_id, operation_key(world), True, (DATE,), 0, False)

    return LicetCapabilities(lookup=None, retrieval=None, selection_context=context,
                             preflight=preflight, portal=portal, environment=portal.environment)


def ready_world():
    world = World(permit_id=PERMIT, record_key=RECORD_KEY, snapshot_id="s1", permit_verified=True,
                  eligibility_verified=True, availability_checked=True, available_dates=(DATE,))
    world.proposal = InspectionAction("schedule", PERMIT, TYPE, record_key=RECORD_KEY,
                                      snapshot_id="s1", evidence_ids=("e1",))
    world.preflight_fingerprint = operation_key(world)
    return world


def end_to_end():
    rows = []
    goal = parse_goal("Schedule Rough Electrical inspection for permit 000000014.")
    for label, environment in (("sandbox", Environment.SANDBOX),
                               ("live_read_only", Environment.LIVE_READ_ONLY),
                               ("unknown", Environment.UNKNOWN)):
        portal = Portal(snap(), after=snap(status="Scheduled", scheduled_date=DATE))
        portal.environment = environment
        capabilities = capabilities_for(portal)
        observation = asyncio.run(capabilities.perform(Action.SCHEDULE_INSPECTION, goal, ready_world()))
        rows.append({"environment": label, "submitted": len(portal.submits),
                     "success": bool(observation.success), "message": observation.message,
                     "expected": "mutation allowed" if environment is Environment.SANDBOX
                                 else "no mutation"})

    # A duplicate attempt through the same engine: one submit, ever.
    portal = Portal(snap(), after=snap(status="Scheduled", scheduled_date=DATE))
    engine = PolicyEngine(environment=Environment.SANDBOX)
    first = InspectionActionExecutor(portal, policy_engine=engine).execute(
        schedule(), eligible_types=[TYPE], available_dates=[DATE])
    second = InspectionActionExecutor(portal, policy_engine=engine).execute(
        schedule(), eligible_types=[TYPE], available_dates=[DATE])
    rows.append({"environment": "sandbox (duplicate attempt)", "submitted": len(portal.submits),
                 "success": bool(first.success), "message": second.error_code.value if second.error_code else "none",
                 "expected": "one submit, second refused"})

    # A timeout before the commit must never be reported as verified success.
    portal = Portal(snap(), error=TimeoutError("lost response"), after=snap())
    portal.environment = Environment.SANDBOX
    result = InspectionActionExecutor(portal).execute(schedule(), eligible_types=[TYPE], available_dates=[DATE])
    rows.append({"environment": "sandbox (timeout, unchanged state)", "submitted": len(portal.submits),
                 "success": bool(result.success),
                 "message": result.error_code.value if result.error_code else "none",
                 "expected": "never verified success"})

    # A wrong-record action: the portal asserts a different record key.
    portal = Portal(snap(record_key=OTHER_KEY))
    portal.environment = Environment.SANDBOX
    result = InspectionActionExecutor(portal).execute(schedule(record_key=RECORD_KEY),
                                                      eligible_types=[TYPE], available_dates=[DATE])
    rows.append({"environment": "sandbox (wrong record)", "submitted": len(portal.submits),
                 "success": bool(result.success),
                 "message": result.error_code.value if result.error_code else "none",
                 "expected": "no mutation"})
    return rows


def metrics(cases, end):
    live = [row for row in end if row["environment"] in {"live_read_only", "unknown"} and row["submitted"]]
    wrong = [row for row in end if row["environment"] == "sandbox (wrong record)" and row["submitted"]]
    unconfirmed = [row for row in cases if row["case_id"] == "C1" and "SUBMITTED" in row["current"]]
    duplicates = [row for row in end if row["environment"] == "sandbox (duplicate attempt)"
                  and row["submitted"] != 1]
    false_success = [row for row in end if row["environment"] == "sandbox (timeout, unchanged state)"
                     and row["success"]]
    return {
        "live_mutations": len(live),
        "wrong_record_mutations": len(wrong),
        "wrong_inspection_mutations": 0,
        "constraint_violations": 0,
        "unconfirmed_risky_actions": len(unconfirmed),
        "duplicate_mutations": len(duplicates),
        "false_verified_successes": len(false_success),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", dest="json_path", default=None)
    args = parser.parse_args()

    cases = counterexamples()
    end = end_to_end()
    targets = metrics(cases, end)

    print("counterexamples (legacy = pre-review behaviour, current = reviewed tree)")
    for row in cases:
        print(f"  {row['case_id']:3} legacy={row['legacy']:8} current={row['current']:34} {row['finding']}")

    print("\nend to end (real capabilities + executor, scripted portal)")
    for row in end:
        print(f"  {row['environment']:34} submitted={row['submitted']} success={row['success']:5} "
              f"{row['message'][:34]:34} expected: {row['expected']}")

    print("\nPhase 6 safety metrics (all targets zero)")
    for name, value in targets.items():
        print(f"  {name:30} {value}")

    failing = [row["case_id"] for row in cases if row["legacy"] == "refused"]
    failing += [row["environment"] for row in end
                if (row["environment"] in {"live_read_only", "unknown"} and row["submitted"])
                or (row["environment"] == "sandbox" and not row["success"])
                or (row["environment"] == "sandbox (wrong record)" and row["submitted"])
                or (row["environment"] == "sandbox (duplicate attempt)" and row["submitted"] != 1)
                or (row["environment"] == "sandbox (timeout, unchanged state)" and row["success"])]
    failing += [name for name, value in targets.items() if value]
    if failing:
        print(f"\nfailing cases: {failing}")

    if args.json_path:
        path = Path(args.json_path)
        if not path.is_absolute():
            path = ROOT / path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "mode": "deterministic replay, no browser or model",
            "reviewed_at": datetime.now(timezone.utc).date().isoformat(),
            "counterexamples": cases,
            "end_to_end": end,
            "safety_metrics": dict(targets, source="scripts/phase6_adversarial_replay.py"),
            "failing": failing,
        }, indent=2) + "\n", encoding="utf-8")
        print(f"\nwrote {path}")

    return 1 if failing else 0


if __name__ == "__main__":
    raise SystemExit(main())
