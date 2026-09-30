# Phase 4 checklist audit

Audited 2026-09-22 against the Phase 4 checklist, after the coordinator, metrics,
retry and mutation-safety work. Method: for each checklist item, name the code
that implements it and the test that locks it; anything that cannot be exercised
on this environment is marked **environment-limited** with the measurement that
proves it, not left implied.

Status key: **Done** (implemented + regression-locked) · **Scripted**
(implemented and locked against fakes; the live leg needs availability) ·
**Env-limited** (blocked on portal evidence this sandbox cannot produce) ·
**Open** (not started).

## Headline

The deterministic boundary — schema, matching, dates, policy, verification,
idempotency, audit, metrics — is complete and regression-locked (770 tests). The
**live mutation path has now been exercised end to end** and correctly refused at
the date gate: `BLD26-00469` was accepted and issued (two audited back-office
writes, citizen portal read-back `Submitted` → `Ready to Issue` → `Issued`), the
scheduling wizard then opens live and offers its six inspection types, and the real
executor returns `VERIFIED_FAILURE` with every zero-target counter at 0. What still
cannot happen on this sandbox is a **live booking**: the calendar renders **0 active
days**, and the horizon sweep below shows that is not a narrow gap — every offered
type is empty out to Apr/May 2027 (and `Rough` to Aug 2027). Reschedule and
cancellation controls have never rendered, so those two flows are unmapped by
construction.

**Refined 2026-09-26** (see [`docs/phase9/sandbox-capacity-findings.md`](../phase9/sandbox-capacity-findings.md)):
the precise statement is that **inspection capacity exists on seeded back-office
records, but no capacity-bearing inspection type is reachable through the citizen
scheduling workflow for records associated with the test account.** The tenant holds
7 scheduled inspections on 2 seeded records (`Fire`, and Building "Blitzz Remote"),
none account-owned; `Fire` exposes 0 citizen-configurable record types, and "Blitzz
Remote" is absent from the citizen wizard's complete type grids for the two record
types that carry it.

**Decision (2026-09-22): Phase 4 is signed off as code-complete with that one
accepted environment limitation.** The measured gap is appointment capacity — not
permit status, code, records, types or months. Scripted mutation evidence plus the
live run to the date gate is the acceptance for the mutation path; see the status
section in `docs/phase4.md`.

## The unblock, executed and verified (2026-09-22)

The recon below concluded that the only route to a bookable record was a write to
the sandbox back office. That write was authorized and performed:

| Step | Control | Result |
|---|---|---|
| 1. Accept | `workflowEdit.do` task *Application Submittal*, `value(taskItem*disposition)` = `Accepted - Plan Review Not Req`, `date(taskItem*statusDate)` = 09/21/2026, `SUBMIT` | task closed; citizen portal reads **Ready to Issue** |
| 2. Issue | next task *Permit Issuance*, same form, disposition = `Issued` | task closed; citizen portal reads **Issued** |

Both writes came from `scripts/ni_backoffice_accept.py`, which is **inspect-only
unless `--apply` is passed**, only touches those named controls, and presses the
task menu's `SUBMIT`. Verification is independent of the postback: each write was
followed by `scripts/ni_my_records.py`, which re-reads the citizen portal's
authoritative My Records grid.

**Consequence:** the permit-status blocker is gone — `BLD26-00469` is an issued,
account-owned record and the wizard is fully reachable for it. What remains is
capacity (`0` active days) for every citizen-offerable type; no write into this
environment has produced a citizen-bookable slot, and the 2026-09-26 survey shows
the seeded capacity is not reachable through the citizen scheduling flow.

## The environment finding (re-measured live 2026-09-22, before the unblock)

A read-only sweep (`scripts/ni_availability_sweep.py`) run this session:

| Record | Result |
|---|---|
| `BLD26-00469` (owned, then `Submitted`) | reached the wizard calendar: **Sep 30, Oct 31, Nov 30 cells — 0 active, 0 selectable times** |
| `PLB-10-00951` (public probe) | `not_found` in the account's scheduling context |
| `22CAP-00000-006RZ` (public probe) | `not_found` in the account's scheduling context |

`licet/eval/records.py` already names the cause: **all 8 owned records are
`Submitted`, not `Issued`**, and ACA only lets a citizen book inspections on
issued permits. The public probe records are not associated with the test
account, so they cannot be scheduled on at all.

Two consequences follow directly:

1. **No live booking can be accepted here.** The honest live result is
   `DATE_CONSTRAINT_UNSATISFIED` / `NO_AVAILABLE_DATES`, which is the gate
   working, not a failure of the harness.
2. **No per-row Reschedule/Cancel control has ever rendered** (`accela_ui_map.md`
   §6 lists them as per-inspection actions). Without a scheduled inspection there
   is no control to capture or map.

## Back-office recon (read-only, 2026-09-22)

`nullisland-test-av.accela.com` (developer/accela), via
`scripts/ni_backoffice_recon2.py`, `scripts/ni_backoffice_recon3.py` and
`scripts/ni_backoffice_issue_recon.py`. Nothing was created, edited or issued.

| Question | Finding |
|---|---|
| Are our records issued? | **No.** The work queue lists them as tasks: `BLD26-00466/67/69/72/73` under **Application Submittal**, `BLD26-00468/70` under **Application Acceptance**. Application stage, not issuance. |
| Does the issue workflow exist? | **Yes.** The back office exposes an **`Ready to Issue`** status and the `Application Acceptance` → issuance path, so a Submitted record can be driven to Issued by back-office staff actions. |
| Is any issued record tied to our account? | **None found.** The citizen account's `My Records` holds exactly the eight records above, all `Submitted`. Issued records elsewhere in the sandbox are not associated with the account, and ACA requires an account tied to the record to schedule. |
| Does `BLD26-00465` exist? | No — an exact search returns nothing, so the earlier "Permit Issuance" note on that id was stale. |

### What the acceptance task requires (read-only, `scripts/ni_backoffice_task_recon.py`)

Opened `Application Acceptance BLD26-00468` at
`.../nullisland.applicationacceptancebld2600468` — read only; nothing filled or
submitted. The **Task Details** form carries four required fields:

| Field | Required | Values seen |
|---|---|---|
| Status | yes | `--Select--`, `Accepted - Plan Review Not Req`, `Accepted - Plan Review Req` |
| Status Date | yes | date |
| Department | yes | current department (prefilled) |
| Staff | yes | current user (prefilled) |
| Note | no | free text |
| Time spent on task | no | "Add entry" sub-task |

So acceptance is a **write with four required fields**, and it only moves the
record to the next workflow step — the "`Ready to Issue`" status the back office
also exposes is a *later* step, reached after acceptance. Making one of our
records bookable therefore needs **at least two back-office writes** (accept,
then issue)**. Both were subsequently authorized and performed — see "The unblock,
executed and verified" above.

**Consequence, as of the recon:** the only route to a bookable record was to drive
one of our own records through the back-office **Application Acceptance → issue**
workflow — a multi-step *write* to the sandbox back office, which was thereafter
authorized and performed.

## Calendar horizon sweep (read-only, 2026-09-22)

`scripts/ni_calendar_horizon.py` tests the two axes the earlier sweep left open —
the other offered inspection types, and the months past the rendered strip (the
calendar carries a `Next »` postback, `ctl00_phPopup_calendar_AccelaLinkButton2`).
It never clicks a day, a time or the commit.

| Inspection type | Months read | Active days |
|---|---|---|
| `Rough` | Sep 2026 → Aug 2027 (12 months) | **0** |
| `Service` | Sep 2026 → Apr 2027 | **0** |
| `Temp Service Pole` | Sep 2026 → Apr 2027 | **0** |
| `Ground Work` | Sep 2026 → Apr 2027 | **0** |
| `Electrical Final` | Sep 2026 → Apr 2027 | **0** |
| `Progress Check` | Sep 2026 → Apr 2027 | **0** |

**Conclusion:** the sandbox has no appointment capacity configured — not for one
type, and not in any reachable month. Picking a different record, type or date
cannot unblock the live booking; the calendar itself is empty. Reports:
`logs/ni_backoffice/schedule/*_calendar_horizon.json`.

## Checklist

| Item | Status | Evidence / reason |
|---|---|---|
| View existing inspections | Done | `AccelaInspectionPortal.read_inspection_state*`; `tests/test_phase4_accela_portal.py` |
| Identify eligible inspection types | Done | wizard grid parsing (`accela.parse_inspection_types`), `offered_types` |
| Request/schedule an inspection | Scripted | executor + adapter wizard walk; `test_phase4_cases.py` `S01`–`S10`. **Live:** wizard opens on the issued record, reads 6 offered types, reaches the calendar, then refused for 0 capacity |
| Reschedule an existing inspection | Env-limited | **no control ever captured**; adapter refuses rather than driving the new-request wizard (which would duplicate the appointment). `M7` |
| Cancel an inspection | Env-limited | same; adapter refuses, guard also holds it at the dispatcher |
| Verify resulting state | Done | post-action re-read; `_matches` + `ACTION_VERIFICATION_FAILED` |
| Payments / application submission excluded | Done | absent from Phase 4; guard classifies and holds them |
| `InspectionAction` schema | Done | `licet/phase4/actions.py` |
| `InspectionActionResult` schema | Done | same, incl. before/after + `alternatives` |
| Inspection-section navigation from a verified permit | Done | adapter read path; adapts to mid-load with `Unknown (...)`, never a guessed state |
| Distinguish completed vs requestable; disabled types; prerequisites | Done | `InspectionSnapshot.is_scheduled/is_pending/is_cancelled/is_completed`, `eligible`, selection eligibility gates |
| Phase 3 → Phase 4 bridge | Done | `select_inspection_action` + `coordinator.run_inspection_workflow` |
| Require sufficient confidence; stop on ambiguity/conflict/missing prerequisites | Done | `test_phase4_selection.py` (55 cases) |
| Never choose an inspection from name similarity | Done | `matching.match_inspection_type` (token-exact, ambiguity rejected) |
| Exact inspection-type matching, portal terminology preserved | Done | same; label variants pinned |
| Scheduling workflow steps | Scripted | adapter; every non-commit step (entry, record open, wizard, type grid, calendar) now exercised **live**; the commit step is still fake-only because the calendar has no active day |
| Date-window instructions ("next week", "after Wednesday", "Friday", "before Oct 1", "earliest") | Done | `dates.normalize_date_constraints` |
| Deterministic date selection (never a later date) | Done | `select_date`; `S03`, `S05` |
| Unavailable dates: no silent expansion, closest alternatives if allowed | Done | `select_date` returns `None`; `closest_alternatives` is advisory and opt-in |
| Required scheduling fields | **Env-limited (filling)** | the executor's `MISSING_REQUIRED_INPUT` gate is fail-closed, but the adapter fills nothing: every captured step of this portal's flow renders no text input |
| Missing-information stop (never fabricate) | Done | `MISSING_REQUIRED_INPUT`; omitted mapping treated as no inputs (`M4`) |
| Rescheduling flow | Env-limited | see above; executor logic is locked against fakes |
| Cancellation flow | Env-limited | see above |
| Cancellation safety level (confirmation) | Done | `decide_action_policy` requires explicit confirmation |
| Action policy layer | Done | `ActionPolicyDecision`, `decide_action_policy` |
| Browser action separated from permission | Done | `InspectionPortal` protocol; executor owns permission, adapter owns UI |
| Pre-action verification | Done | permit/record-key/inspection/eligibility/idempotency/inputs/date, all before the first click |
| Post-action verification | Done | independent re-read; success requires a match |
| Detect partial success | Done | `UNVERIFIED` / `STATE_MISMATCH` without a scheduled row |
| Verification states | Done | `VERIFIED_SUCCESS`, `VERIFIED_FAILURE`, `UNVERIFIED`, `STATE_MISMATCH` |
| Capture before/after state | Done | on `InspectionActionResult` and `MutationAudit` |
| Mutation audit trail | Done | `MutationAudit`, now **persisted** to the run log via `RunLogger.log_event` |
| Idempotency | Done | blocks on scheduled **and** in-flight (`requested`/`pending`) — `M1` |
| Prevent duplicate execution after retries | Done | uncertain submission reconciles by re-reading; no replay |
| Mutation-specific errors | Done | full `ActionErrorCode` set |
| Controlled retries | Done | bounded read/settle retries; **no commit is ever retried**; `test_phase4_adapter_resilience.py` |
| Execution previews | Done | `runner.preview`; surfaced by the coordinator and the acceptance runner |
| Scripted action tests first | Done | 25-case checklist table in `licet/eval/phase4_fixtures.py` |
| Scheduling tests (10) | Done | `S01`–`S10` |
| Rescheduling tests (5) | Done | `R01`–`R05` |
| Cancellation tests (5) | Done | `C01`–`C05` |
| ≥25 Phase 4 tests | Done | 770 in the suite; 33 in the case table alone |
| Track Phase 4 metrics | Done | `Phase4Metrics` (throughput + selection accuracy + zero-target invariants) |
| Strict target metrics (0/0/0/0) | Done | `zero_targets()` / `within_zero_targets`; each probe proven to fire on a synthetic regression |
| First end-to-end autonomous workflow | Scripted | `coordinator.run_inspection_workflow` + `scripts/ni_phase4_acceptance.py`, which now drives the **real** executor against the live portal. Live completion still needs an open date |
| The "more impressive" combined command | Open | needs Phase 2/3 lookup + the same live booking capability |
| Keep open-ended planning disabled | Done | no planner loop; Phase 5 not started |

## Test-case traceability

The checklist's suggested split maps 1:1 onto the data table in
`licet/eval/phase4_fixtures.py`, so a reviewer can read the checklist against
executable cases:

- scheduling → `S01`–`S10` (one eligible, multiple eligible, exact date available,
  exact date unavailable, earliest, no availability, already scheduled, missing
  required field, invalid type, portal error after submit)
- rescheduling → `R01`–`R05` (later, earlier, date unavailable, exact appointment
  addressed, wrong inspection protected)
- cancellation → `C01`–`C05` (valid, already cancelled, completed, confirmation
  boundary, verified result)
- failure/safety/idempotency → `F01`–`F05` (in-flight duplicate, uncertain
  reconcile without replay, post-submit mismatch, wrong record key, no-op
  reschedule)

## What is required to close Phase 4

1. ~~**Obtain a bookable record.**~~ **Done 2026-09-22** — `BLD26-00469` was
   accepted and issued (two audited writes), and the wizard is now reachable for
   it.
2. **Supply citizen-bookable appointment capacity.** The one remaining blocker, now
   measured to the end of its useful horizon: the calendar renders **0 active days**
   for that issued record — every one of the six offered types, and every month from
   Sep 2026 through Apr/May 2027 (`Rough` was walked through Aug 2027). See "Calendar
   horizon sweep" below. This needs environment configuration; no code change, record,
   type or month selection can produce a bookable date. (The 2026-09-26 survey adds
   that the seeded back-office capacity is not reachable via the citizen workflow, so
   configuring citizen-bookable capacity is the specific requirement.)
3. **Run the acceptance runner with `--execute`** against the first open date and
   capture the first live `VERIFIED_SUCCESS`.
4. **Capture the Reschedule and Cancel controls** from the now-scheduled
   inspection, then map those two flows (currently refused).
5. **Re-check required fields** — only if the portal renders them; this flow
   renders none.

## Sign-off (2026-09-22)

Item 1 is done; item 2 is recorded as an **accepted environment limitation** rather
than an open engineering task. Phase 4 is therefore closed as code-complete — the
exit condition is met *by construction, by scripted evidence, and by a live run
that reaches the calendar and is refused there* — not by a live verified booking.
Items 3–5 remain the work that an environment with citizen-bookable capacity
re-opens; they are not blockers to closing Phase 4 here.

**Superseding note (2026-09-26):** "no appointment capacity anywhere" is
technically no longer accurate. Inspection capacity exists on seeded back-office
records, but **no capacity-bearing inspection type is currently reachable through
the citizen scheduling workflow for records associated with the test account.**
See [`docs/phase9/sandbox-capacity-findings.md`](../phase9/sandbox-capacity-findings.md).
