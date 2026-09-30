# Phase 9 — portal read reliability: root cause of the `READ_INSPECTIONS` barrier

**Lane:** portal integration (portal/demo reliability), 2026-09-25. Scope: resolve release gate #1's
stated unknown — architecture review: *"Root cause of the portal read failure is not resolved by this review."*
No live portal, model API, or credential was used; the diagnosis is from the captured live trace
plus in-repo evidence, and the fix is verified offline.

## The failure every live run since Phase 5 shares

the architecture review’s fresh plan-only run (`logs/phase9-architecture review/live-plan-only.json`, sanitized in
[`review-live-summary.json`](review-live-summary.json)) stopped at `READ_INSPECTIONS`:

```text
FAILURE  type=extraction  message="section unavailable: inspections"
RECOVERY REOBSERVE_AND_READ  attempts=7  recovered=false
         error="recovery validation failed; state is not known-good"
```

The same failure closed all four Phase 5 live attempts (`docs/phase5/live_evidence.json`) and the
Phase 9 live run — always at the same step, always with the same message.

## Root cause (pinned to one dispatcher call)

The raw session log of the 2026-09-25 run
(`logs/phase9-architecture review/phase5-live-20260925T215455649786Z-d05b442b.jsonl`, lines 11–17) shows what
actually happened during those seven recovery attempts — the same call failing identically, ~8 s
apart:

```text
click "Inspections" (by=text)
error kind=not_actionable
  "no visible element for text=Inspections across 9 frame(s);
   present but not visible: ["a:text-is('Inspections')", "a[title='Inspections']",
   "a:has-text('Inspections')", ":text('Inspections')"]"
```

The `Inspections` anchor **exists in the DOM but is never visible** on Null Island's record detail.
This is the dead-but-rendered shape this project had already documented twice:

- `scripts/ni_section_clickthrough.py` header (live 2026-09-20): *"the record detail renders
  `Record Info | Payments | Attachments` (plus `Schedule an Inspection`)"* — **no visible
  "Inspections" link** — and *"`Attachments` is rendered but not actionable: the anchor exists in
  the DOM and is never visible"* — the identical signature.
- `docs/accela_ui_map.md` §4: generic ACA sections are *Record Info, Processing Status,
  Inspection History, Fees, Attachments*; NI renders its own subset.

The retrieval runner (`licet/phase3/runner.py`) sent exactly one label — `"Inspections"` — got
`not_actionable`, mapped it to `sections_failed`, and returned *"section unavailable: inspections"*.
Phase 7 recovery then did the only thing it knows: re-observe and re-click the same dead wrapper.
Seven identical failures later, the budget died and the planner reported `PLAN_LOOP_DETECTED`.
The safety/verification layers were never wrong: the click simply never had a visible target, and
no code path tried the label the portal actually renders.

Secondary observations from the same trace:

- The record deep link carries `IsToShowInspection=` — the UI map's "inspection context" variant —
  but the runner never read the settled page after landing before clicking.
- Phase 4's adapter (`licet/phase4/accela_portal.py`) had the same single-label click, so the
  mutation-time read would have hit the same wall.
- No route in `route_recovery` covers "the section label is dead": the finding never appears as a
  portal finding because the page itself is clean — the target is not.

## Fix (bounded, read-only, no safety change)

1. **Label variants with a dead-wrapper fallback** (`licet/phase3/runner.py`).
   `_SECTION_LABEL_VARIANTS` orders the label each section is known to open under. When the exact
   label click is reported `not_actionable` ("present but not visible" — the live message), the
   next variant is tried: `Inspections → Inspection History`, `Payments ↔ Fees`,
   `Attachments → Documents`. Everything else (`not_found`, timeout, blocked) fails exactly as
   before. A guard-blocked click is a decision, not a selector problem, and never triggers a
   fallback.
2. **Provenance guard on the click that changed the page.** A click resolved by the dispatcher to
   anything other than `benign_target` is refused even if it succeeded: the runner reports the
   click failed rather than reading whatever the click opened. (The refusal path was
   reviewer-suggested; it is defense-in-depth on top of the dispatcher's own benign resolution.)
3. **Read the settled page before clicking.** After landing on the record deep link, the first
   settled read is kept only as *decisive* evidence (`complete` or `explicitly_empty` coverage, no
   portal findings). A `partial` or loading summary read is not the section and never substitutes
   for it.
4. **Phase 4 adapter gets the same fallback** (`licet/phase4/accela_portal.py`), under the same
   rule: only a `not_actionable`-reported click may open `Inspection History`, and the following
   read is accepted only when the click resolved to `benign_target`.

Deliberately **not** changed: recovery budgets, the validation contract ("state is not
known-good"), `route_recovery`'s finding taxonomy, and every policy boundary. The fallback is one
extra benign click inside the existing bounded pass (`max_reads_per_pass` still caps each pass);
it cannot loop because the variant list is finite and tried once per retrieval pass.

## Verification

- New regression tests, all through the real dispatcher/guard:
  - `tests/test_phase3_integration.py`: live-failure-shape fallback succeeds and merges the
    section (`test_runner_falls_back_when_exact_label_is_dead_but_rendered`); all variants dead ⇒
    section still failed with exactly two clicks, no budget growth
    (`test_runner_reports_unavailable_after_all_label_variants_fail`); a non-benign resolution is
    refused even when the click succeeded (`test_runner_refuses_a_non_benign_section_click`).
  - `tests/test_phase4_accela_portal.py`: adapter fallback reaches a declared-empty snapshot
    (`test_read_falls_back_when_the_inspections_label_is_dead_but_rendered`); a guard-blocked
    click never opens the fallback and the snapshot stays explicitly Unknown
    (`test_read_blocked_click_still_never_triggers_the_fallback`).
- Focused suites: phase3 integration, phase4 adapter, phase7 portal/runtime/adversarial,
  phase5 integration, phase6 policy — **240 passed**.
- Portal replays: `scripts/phase7_portal_replay.py` (failing: none) and
  `scripts/phase5_portal_state_replay.py` (9/9). Phase 3 golden eval 57/57.
- Full suite: **1,390 passed** (1,385 prior + 5 new).
- Repo-wide `ruff --select F821,F822,F823,E9` and `compileall` pass.
- LicetBench offline rerun on this tree: core 50 tasks — 29/50 frozen `SUCCESS` labels, **50/50
  expected outcomes**, 0 unsafe / 0 duplicate / 0 false-verified / 0 grader errors; prompts 22/22
  expected, 17 completions; flagship 10/10 expected. Identical to the frozen artifacts
  (`docs/phase8/final/*.json`) — the change touches only the live click path, which fixtures
  stub.
- Benchmarks record `commit_dirty=true` by design: this is a validation run on the shared tree,
  not a release freeze.

## What this does and does not claim

The root cause is diagnosed from the captured live trace and the repo's own live-verified
documents; the fix is verified offline. **No live rerun happened in this lane.** The next live
plan-only run of `scripts/ni_phase5_acceptance.py` should now pass the point where every previous
run died: landing on the record reads the settled page first, and the section opens through the
label Null Island actually renders. Whether the run then reaches availability still depends on
the sandbox's calendar, which is a separate, documented limitation.

## Suggested next-lane handoffs

- **implementation (owner):** accept the fix, then run the live plan-only validation and, if it reaches the
  calendar, capture the flagship. The five post-freeze live runs remain the open gate.
- **adversarial review:** the new fallback is one more read-path click; worth one adversarial look at
  whether a hostile page could bait the label-variant order (both labels are pre-whitelisted
  benign targets, so the blast radius is a page read, never a submit).
- **architecture review:** this resolves the stated unknown in the senior review; the release-gate wording for
  gate #1 can now say "root cause identified and fixed; live rerun pending" instead of
  "not resolved".

## Live follow-up — 2026-09-26

The semantic flagship was rerun with execution enabled under normal policy. The fallback was exercised, but neither label opened a usable section: `Inspections` was present but hidden and `Inspection History` had no visible match. The fresh-session retry stopped at `READ_INSPECTIONS` after bounded recovery with zero mutations. Thus the offline-tested label fallback has **not resolved the live barrier**. See [both attempt summaries](semantic-flagship-20260926.json); the first attempt stopped earlier at unverifiable Search outcome.

## Superseding repair — explicit inspection view

The successful P13 route uses `IsToShowInspection=yes`, while the summary URL leaves it empty. The readers now have a guarded read-only fallback to that verified-record view. A fresh live semantic run passed `READ_INSPECTIONS` and stopped later at incomplete next-inspection selection evidence. See [repair and live evidence](inspection-view-fix.md).
