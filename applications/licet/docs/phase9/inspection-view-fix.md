# Live inspection reader repair — 2026-09-26

The live semantic planner now passes `READ_INSPECTIONS`. This repairs the earlier hidden-section failure; it does not establish a completed booking.

## Cause and repair

The successful P13 capture uses the same verified record URL with `IsToShowInspection=yes`. The normal URL ends with `IsToShowInspection=` and renders the overview without inspection history. Trying “Inspections” and “Inspection History” labels could not open that panel in the failed live runs.

Added `inspection_detail_url`, built from the verified record reference. After benign section labels are unavailable, both the Phase 3 reader and Phase 4 inspection adapter can navigate to that read-only view through the normal dispatcher. No scheduling submit is involved. Policy blocks never trigger an alternate route. Missing/loading evidence stays unknown; foreign identity is rejected. The executor adapter rechecks identity after navigation and no longer attempts a fallback after a successful primary section click.

`tests/fixtures/ni_inspection_views.json` contains sanitized P13 record-header and inspection-panel text. Account/contact text was removed. Eight regressions test the actual view transition, loading and foreign-record rejection, and refusal to navigate around a guard block. The old all-labels-unavailable test now also supplies an unavailable deep-link page, rather than supplying explicit empty-history evidence while demanding failure.

## Live result

[Saved summary](inspection-view-fix-live.json), with SHA-256 binding to the ignored raw report:

1. Permit found and independently verified.
2. Overview read.
3. State interpreted.
4. **Inspection history read successfully.**
5. State reinterpreted.
6. Next-inspection selection stopped: **required inspection state is incomplete**.

Final `BLOCKED / NO_SAFE_ACTIONS`; six useful semantic steps; zero attempted or verified mutations. The wizard was opened through “Schedule or Request an Inspection”, but the planner did not select a target or reach availability.

## Remaining flagship gate

The live providers currently turn offered wizard types into eligibility options, while no-target selection requires evidence that identifies the relevant next inspection. Offering a type does not prove it is required. Offline Phase 5 regression now allows an explicit user-named type with a complete portal offer/history context—or a complete catalog's unique required marker—to advance to read-only availability preflight; an optional offer alone still abstains. The captured BLD26-00469 goal had no explicit type and its artifact does not show a required marker, so this correction does not authorize selecting Rough or claim that it is required. A fresh authorized sandbox preflight is still needed to reach and read the calendar. Do not set answerability or confidence to force a selection.

The prior P13 calendar had no selectable dates. This new semantic run did not reach the calendar, so it supplies no fresh availability or booking claim.

## Validation

Final full suite: **1,403 passed**. Frozen core: **50/50 expected outcomes**; prompt suite: **22/22**, both with zero unsafe outcomes. Focused syntax/undefined-name lint and `git diff --check` pass. No freeze commit or live booking was created.
