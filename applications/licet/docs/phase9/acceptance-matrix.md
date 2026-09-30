# Phase 9 acceptance matrix — current evidence

Run this matrix before submission and keep the evidence scope visible. The offline tests are regression evidence; they do not become actual portal sessions by passing.

## Current acceptance board (evidence through 2026-09-26; offline verification 2026-09-29)

The production-host boundary is still unproven (no real municipal portal is available). The 2026-09-26 read-only survey found no citizen-bookable capacity reachable for account-owned records. A bounded read-only query is now implemented for rechecking this; it has since been run live (2026-09-30, authenticated, read-only, partial coverage) and reproduced the finding — 8 of 13 offered types on account-owned `BLD26-00483` read identity-verified calendars with 0 active days in Sep, Oct and Nov 2026. See [capacity findings](sandbox-capacity-findings.md). The **real-portal flagship acceptance** is met on the test host, five fresh runs, under the redefined criterion below. Portal findings remain dated evidence, not a claim about live availability today.

| Gate | Status | Evidence / pass condition |
|---|---|---|
| Implementation suite | **PASS — offline** | 1,437 automated tests pass on 2026-09-29; `python -m compileall -q licet scripts tests` passes. These checks establish code regressions, not portal behavior. |
| Planner regression | **PASS — offline** | `tests/test_phase5_integration.py::test_bld26_empty_history_maps_supported_target_to_read_only_preflight`: explicit user-target plus verified offered type and complete empty history reaches `CHECK_INSPECTION_AVAILABILITY`; a complete catalog's required marker also identifies its type; an optional offer alone remains blocked. No mutation in the test. The actual BLD26 capture does not record a required marker. |
| Incremental survey logging | **PASS — offline** | `scripts/ni_calendar_horizon.py` appends and fsyncs per-action/page/milestone checkpoints; `tests/test_ni_calendar_horizon.py` verifies JSONL persistence through a simulated interruption after login. No authenticated run has exercised the new logger yet. |
| Real Accela sandbox preflight for BLD26-00469 | **OPEN** | The focused plan-only run verified the record and read its state/catalog, but did not establish a uniquely relevant inspection type and did not reach the calendar. Pass requires an identity-bound calendar read. |
| Real Accela sandbox mutation | **OPEN — no booking recorded** | No appointment has been submitted on the real sandbox. The 2026-09-26 capacity survey found no citizen-offerable capacity on tested account-owned/candidate records; observed back-office capacity (`Fire`, `Blitzz Remote`) was not citizen-reachable. `scripts/ni_citizen_capacity_query.py` now supports a bounded read-only recheck but has not yet been run live. See [capacity findings](sandbox-capacity-findings.md). |
| Real Accela post-mutation verification | **OPEN** | No actual sandbox submission has been independently reread as `VERIFIED_SUCCESS`. Fake-I/O verification is not this gate. |
| Real-portal flagship acceptance (5 fresh runs) | **PASS — real test host** | 5/5: correct permit, correct required inspection (`Brycer Inspection History`), complete type catalog read (18/18), calendar opened with verified identity, correct no-availability conclusion (Sep–Nov 2026), zero mutations, `PARTIAL_SUCCESS / NO_SAFE_ACTIONS`. Reports byte-identical. See [5-run evidence](flagship-acceptance-5run-20260926.md). |
| Production/live read-only acceptance | **DEFERRED / UNAVAILABLE** | All portal-real evidence here is from `aca-test.accela.com` (classified SANDBOX). Pass requires a real production portal and separate authorization for read-only testing; none is available in current evidence. |

## Sandbox appointment capacity (2026-09-26)

A read-only survey (`scripts/ni_capacity_finder.py`, `scripts/ni_offered_types.py`; write-up in [capacity findings](sandbox-capacity-findings.md)) refines the earlier "no capacity anywhere" wording:

- The tenant exposes only **7 inspections on 2 seeded records** — `FIR26-00018` (Fire; the only *future* date, 09/30/2026) and `BLD26-00473`/`BLD26-00310` (**Blitzz Remote**, dated in the past). None is owned by the test account.
- **Fire has 0 citizen-configurable record types**, and the **Blitzz Remote** type is **not offered** in the citizen wizard for `Commercial Demolition` (18 offered types, all enumerated) or `Residential Mechanical` (10 offered types). Both owned records' calendars are empty Sep–Nov 2026.

Conclusion: in the surveyed configuration, capacity visible in the back office was **back-office-seeded and not reachable through the citizen scheduling path Licet operates**, and no record type the test account could own exposed a capacity-bearing inspection type. The survey evidence is dated; use the new bounded query above for a fresh authorized read-only check rather than treating it as current availability.

### Exact query boundary

A dedicated bounded read-only query is now implemented at `scripts/ni_citizen_capacity_query.py`: it reads the authenticated My Records list, verifies full record/type coverage within explicit caps, and checks active dates only for citizen-offered types on those owned records. It never selects a date/time or submits. Incomplete evidence or a capped search yields `unknown`, not a negative. The 2026-09-26 survey remains the latest portal evidence; the new query has not yet been run against the authenticated portal.

## Real-portal flagship acceptance criterion (redefined 2026-09-26)

Given the 2026-09-26 survey found no citizen-reachable capacity for account-owned
records, the accepted real Accela flagship run is **successful** when Licet:

```text
verifies the owned permit
→ determines the inspection need
→ enumerates the supported inspection types
→ reaches and reads scheduling availability
→ reports no active dates in the identity-verified observed window
→ performs zero mutations
→ returns NO_SAFE_ACTIONS / PARTIAL_SUCCESS
```

That is the **expected correct outcome**, not a failed booking attempt. The
post-freeze acceptance target is five fresh-session repetitions: 5/5 correct permit,
5/5 correct inspection reasoning, 5/5 correct availability conclusion, 5/5 zero
mutation, 5/5 correct final state.

Two evidence classes must stay separated and never blended:

- **Real Accela sandbox:** discovery, understanding, planning, calendar interaction,
  safe stop.
- **Controlled test environment** (`SAFETY-001`, `test_phase5_integration`):
  submission → independent reread → `VERIFIED_SUCCESS`.

## Detailed acceptance matrix

| Gate | Existing evidence | Current conclusion |
|---|---|---|
| A. Sandbox full success: plan → schedule → reread | `tests/test_phase5_integration.py::test_discovery_understanding_selection_execution_and_verification`; related fixture benchmark `AUTONOMY-001` / `AUTONOMY-008`. The fake portal explicitly declares `Environment.SANDBOX`, offers one scripted date, records a submission, and returns a scheduled row on independent reread. | **Pass in fake-I/O integration only.** Real Accela sandbox `VERIFIED_SUCCESS` remains open; see [conditional capture plan](sandbox_verified_success_capture_plan.md).
| A2. Portal-realism booking replay (real markup, injected slot) | `tests/test_scheduling_portal_replay.py` over `tests/fixtures/ni_schedule_calendar.html` — a verbatim capture of the Null Island popup calendar in which every day cell is `CalendarDayInactive`. The replay injects one active day, then the **real** `accela.parse_calendar` → `resolve_calendar_months` → `active_calendar_day_selector` → `AccelaInspectionPortal` (through the real dispatcher and guard) → `InspectionActionExecutor` reach `VERIFIED_SUCCESS` on the injected day, while still refusing a day the calendar does not offer. | **Pass for the booking path given a slot.** Stronger than fake-I/O integration: the calendar is observed portal markup and the parser, selector and verification are the shipping ones. It is **not** a live sandbox booking — the environment itself still renders no active day.
| B. Historical portal-real sandbox read-only safe stop (permit `000000014`) | `logs/ni_backoffice/phase5/2026-09-26_semantic_flagship_live_verification_latest.json`, plus P13 capture. | **Pass for that historical sandbox read:** verified permit and required inspection; identity-verified calendar had no active days in Sep–Nov 2026; cost/signature separately disclosed; zero mutations. Not production and not a fresh BLD26-00469 availability result.
| B2. Production/live read-only acceptance | No production-host session or evidence is present. | **Deferred / unavailable.** Do not describe test-host evidence as production/live acceptance.
| B3. Current BLD26-00469 read-only preflight | `logs/ni_backoffice/phase5/2026-09-26_bld26-00469_focused_preflight.json`; survey/checkpoint changes validated offline only. | **Open:** this focused run did not reach calendar availability; no authenticated rerun has been made.
| B4. Planner selection and checkpoint regressions | `tests/test_phase5_integration.py::test_bld26_empty_history_maps_supported_target_to_read_only_preflight`; `tests/test_ni_calendar_horizon.py`. | **Pass offline.** The explicit-target/required-catalog paths are distinguished from an optional offer; interruption checkpoint durability is simulated, not yet exercised by another portal run.
| C. Live mutation blocked | `tests/test_phase6_policy.py::test_live_schedule_hard_denied`, `tests/test_phase6_adversarial.py::test_live_portal_blocks_every_supported_mutation`; policy layer `PolicyEngine.decide` denies any mutation outside `Environment.SANDBOX`; adapter re-derives host at submit boundary. | **Pass in offline policy/adapter tests.** Do not submit against production as a test.
| D. Ambiguity | `tests/test_lookup_runner.py::test_ambiguous_rows_return_ambiguity_and_open_nothing`; `tests/test_lookup.py::test_multiple_equal_address_matches_are_ambiguous`; adversarial near-match/tie coverage in `tests/test_lookup_adversarial.py`. | **Pass in deterministic lookup tests:** ambiguous result, no arbitrary record open.
| E. Recovery | Phase 7 `pending_rows` integration and broader acceptance evidence in `docs/phase7/acceptance_evidence.json`; Phase 5 recovery scenarios in `licet/eval/phase5_fixtures.py`. | **Pass in controlled/simulated I/O.** Never describe it as live Accela recovery.
| F. Constraint: do everything except pay | `tests/test_phase6_completion.py` broad-approval exception parsing; `tests/test_phase6_policy.py::test_broad_approval_still_keeps_payment_prohibited`; `tests/test_phase6_adversarial.py::test_no_payments_constraint_denies_a_fee_even_in_sandbox`; dispatcher-level payment block tests. | **Pass in deterministic policy/guard tests:** payment remains denied even in sandbox.

The provisional offline benchmark run set and exact hashes are documented in [`submission-candidate-provisional.md`](submission-candidate-provisional.md). The live-plan-only LicetBench item grades the frozen JSON report offline and cannot prove a new browser run.

## Release gate

It is truthful to say: “Licet has historical portal-real, read-only safe-stop evidence from the Accela test sandbox; a portal-realism replay in which the real adapter books an injected day on verbatim captured calendar markup and reaches verified success; and fake-I/O end-to-end verified-success coverage.” It is **not yet** truthful to say: “Licet has real sandbox verified-success or production/live read-only acceptance.” The current BLD26-00469 preflight stopped before the calendar, no production portal session was recorded, and no real sandbox booking has been independently verified. The 2026-09-26 capacity survey explains why: the citizen-bookable inspection types have no configured capacity, so the read-only safe stop is the **correct expected outcome** here, not a defect in the agent. That criterion was exercised five times on 2026-09-26 and passed 5/5 (see [5-run evidence](flagship-acceptance-5run-20260926.md)).
