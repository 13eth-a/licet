#!/usr/bin/env python
"""Phase 5 adversarial replay (DeepSeek V4.1 Flash review).

Re-derives each counterexample from ``docs/phase5/adversarial_review.md`` against
the current tree and reports, per case, what the **pre-review** completion rule
would have concluded versus what the current one does. The legacy column is the
old predicate reproduced verbatim in this script (the pre-review Phase 5 runtime
is not committed, so no revision contains it), so each case is demonstrably a
counterexample rather than a restatement of the current code.

    python scripts/phase5_adversarial_replay.py
    python scripts/phase5_adversarial_replay.py --json docs/phase5/adversarial_evidence.json

Read-only: nothing is written except the optional ``--json`` evidence file. No
browser, model, credential or live mutation is used.
"""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import replace
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from licet.phase3.state import Uncertainty  # noqa: E402
from licet.phase4.actions import InspectionSnapshot  # noqa: E402
from licet.phase5 import Action, GoalPlanner, Status, parse_goal  # noqa: E402
from licet.phase5.state import MUTATIONS, established, reasoning_is_sound  # noqa: E402
from licet.eval.phase5_fixtures import KEY, ScriptedCapabilities, goal, ready_world  # noqa: E402


def legacy_established(world) -> set[str]:
    """The pre-review completion predicate, reproduced exactly.

    ``blockers_identified`` came from ``answerability == "answered"`` and a record
    match; ``next_inspection_identified`` came from any proposal; ``permit_approved``
    did not require the permit to belong to the verified record.
    """
    facts = set()
    if not world.permit_verified or not world.record_key:
        return facts
    facts.add("permit_verified")
    if world.reasoning and world.reasoning.record_key == world.record_key and world.reasoning.answerability == "answered":
        facts.add("blockers_identified")
    if world.proposal:
        facts.add("next_inspection_identified")
    if world.permit and world.permit.status_normalized == "APPROVED" and not (
        world.reasoning and world.reasoning.contradictions
    ):
        facts.add("permit_approved")
    return facts


# --- counterexample fixtures ------------------------------------------------- #

def _stale(world):
    world.reasoning.snapshot_id = "previous-snapshot"


def _contradictory(world):
    world.reasoning.contradictions = ["fee row says paid; balance row says unpaid"]


def _needs_section(world):
    world.reasoning.needed_sections = [{"section": "fees"}]


def _blocking_uncertainty(world):
    world.reasoning.uncertainties = [Uncertainty("ordering unknown", "which attempt is current", blocks_answer=True)]


def _another_question(world):
    world.reasoning.question = "Is this permit approved yet?"


def _foreign_proposal(world):
    world.proposal = replace(world.proposal, record_key="another/record", snapshot_id="elsewhere")
    world.selection = replace(world.selection, record_key="another/record", snapshot_id="elsewhere")


def _stale_proposal(world):
    world.proposal = replace(world.proposal, snapshot_id="previous-snapshot")


def _foreign_approved_permit(world):
    world.permit.status_normalized = "APPROVED"
    world.permit.record_key = "another/record"


def _identity(world):
    return world


# Per case: (case id, fact, fixture, finding)
CASES = [
    ("F1", "blockers_identified", _stale, "an interpretation from a previous snapshot established blockers"),
    ("F2", "blockers_identified", _contradictory, "a self-contradicting interpretation established blockers"),
    ("F3", "blockers_identified", _needs_section, "an interpretation that still needs a section established blockers"),
    ("F4", "blockers_identified", _blocking_uncertainty, "an interpretation whose uncertainty blocks the answer established blockers"),
    ("F5", "blockers_identified", _another_question, "an answer to a different question established blockers"),
    ("F6", "next_inspection_identified", _foreign_proposal, "a proposal bound to another record established next-inspection"),
    ("F7", "next_inspection_identified", _stale_proposal, "a stale proposal established next-inspection"),
    ("F8", "permit_approved", _foreign_approved_permit, "approval on another record established permit approval"),
    ("C1", "blockers_identified", _identity, "positive control: sound reasoning still establishes blockers"),
]


# --- end-to-end scenarios ---------------------------------------------------- #

class _Inject:
    """Capability wrapper that perturbs one reasoning result end to end."""

    def __init__(self, mutate=None):
        self.mutate = mutate

    def build(self):
        mutate = self.mutate

        class Capabilities(ScriptedCapabilities):
            async def perform(self, action, goal, world, **kwargs):
                observation = await super().perform(action, goal, world, **kwargs)
                if action == Action.DETERMINE_BLOCKERS and observation.world.reasoning is not None:
                    mutate(observation.world.reasoning)
                return observation

        return Capabilities()


def _e2e(name, finding, mutate, goal_text="Fix my permit.", **options):
    capabilities = _Inject(mutate).build() if mutate else ScriptedCapabilities()
    run = asyncio.run(GoalPlanner(capabilities, **options).run(parse_goal(goal_text)))
    return {
        "name": name,
        "finding": finding,
        "goal": goal_text,
        "status": run.status.value,
        "remaining_goal": run.report()["remaining_goal"],
        "mutations": run.report()["metrics"]["mutations_attempted"],
        "passed": run.status != Status.SUCCESS and run.report()["metrics"]["mutations_attempted"] == 0,
    }


def _end_to_end() -> list[dict]:
    rows = [
        _e2e("E1", "stale reasoner cannot complete a vague goal", _stale),
        _e2e("E2", "contradicting reasoner cannot complete a vague goal", _contradictory),
        _e2e("E3", "reasoner demanding an unread section cannot complete a vague goal", _needs_section),
        _e2e("E4", "blocks-answer uncertainty cannot complete a vague goal", _blocking_uncertainty),
        _e2e("E5", "an answer to another question cannot complete a vague goal", _another_question),
    ]
    flagship = _e2e("E6", "flagship goal still succeeds once", None,
                    "Find the permit at 123 Main Street, get it ready for its next inspection now.")
    rows.append(flagship | {"passed": flagship["status"] == Status.SUCCESS})
    return rows


def _parse_rows() -> list[dict]:
    rows = []
    for text in ("Get this permit moving.", "Get me as close to approval as possible."):
        parsed = parse_goal(text)
        rows.append({"goal": text, "autonomous": parsed.autonomous, "vague": parsed.vague,
                     "success_conditions": list(parsed.success_conditions)})
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", dest="json_path", default=None)
    args = parser.parse_args()

    cases = []
    for case_id, fact, fixture, finding in CASES:
        world = ready_world()
        fixture(world)
        legacy = fact in legacy_established(world)
        current = fact in established(world, goal())
        cases.append({"case_id": case_id, "fact": fact, "finding": finding,
                      "legacy_establishes": legacy, "current_establishes": current})

    end_to_end = _end_to_end()
    parse_rows = _parse_rows()

    print("counterexamples (legacy = pre-review predicate, current = reviewed tree)")
    for case in cases:
        print(f"  {case['case_id']:3} legacy={'GRANTS ':7} current={'GRANTS' if case['current_establishes'] else 'REFUSES':7} "
              f"{case['finding']}")

    print("\nend to end")
    for row in end_to_end:
        print(f"  {row['name']:3} {row['status']:16} mutations={row['mutations']} "
              f"{'pass' if row['passed'] else 'FAIL'}  {row['finding']}")

    print("\nbroader checklist goals")
    for row in parse_rows:
        print(f"  {row['goal']!r}: autonomous={row['autonomous']} vague={row['vague']} success={row['success_conditions']}")

    # A counterexample passes when the legacy predicate granted the fact and the
    # current one refuses it. The positive control (C1) must still grant it.
    failures = [case["case_id"] for case in cases
                if (case["legacy_establishes"] and case["current_establishes"] and case["case_id"] != "C1")
                or (case["case_id"] == "C1" and not case["current_establishes"])]
    failures += [row["name"] for row in end_to_end if not row["passed"]]
    if failures:
        print(f"\nfailing cases: {failures}")

    if args.json_path:
        path = Path(args.json_path)
        if not path.is_absolute():
            path = ROOT / path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "mode": "deterministic predicate + end-to-end, no browser or model",
            "counterexamples": cases,
            "end_to_end": end_to_end,
            "broader_goals": parse_rows,
            "failing": failures,
        }, indent=2) + "\n", encoding="utf-8")
        print(f"\nwrote {path}")

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
