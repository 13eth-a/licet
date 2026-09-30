# Phase 4 — Inspection actions

Phase 4's deterministic action boundary lives in `licet/phase4/`.

## Status: closed 2026-09-22 — code-complete, one accepted environment limitation

Phase 4 is signed off with a single, explicitly recorded limitation: the live
booking leg of its exit condition cannot be performed on this environment.

- **Implemented and regression-locked (770 tests):** schemas, exact type matching,
  date constraints and deterministic selection, the policy layer separated from
  execution, pre/post verification, idempotency, uncertain-submission
  reconciliation, mutation errors, controlled retries, persisted audit trail,
  metrics with the four zero-target invariants, the 25-case scripted harness, and
  the Phase 3 → Phase 4 bridge with the `run_inspection_workflow` entry point.
- **Exercised live:** issued record `BLD26-00469` reaches the wizard, which offers
  its six inspection types, and the *real* executor runs the real stack as far as
  the calendar — returning `VERIFIED_FAILURE` / `no available date satisfies the
  user's constraints` with every zero-target counter at 0.
- **Accepted environment limitation (live booking):** no live `VERIFIED_SUCCESS`
  booking has ever been performed, because the Null Island sandbox has **no
  appointment capacity configured**. All six offered types are empty from Sep 2026
  through Apr/May 2027, and `Rough` through Aug 2027
  (`scripts/ni_calendar_horizon.py`, read-only). No record, type, or date choice
  unblocks this.
- **Unmapped by construction (reschedule, cancel):** their per-row controls have
  never rendered, because no owned record has ever held a scheduled inspection —
  the same limitation one step later. The adapter fails closed on both rather than
  driving a flow that would duplicate the appointment.

Acceptance for the mutation path is therefore the scripted executor + adapter
harness (`tests/test_phase4_accela_portal.py`, `tests/test_phase4_cases.py`,
`tests/test_phase4_mutation_safety.py`, `tests/test_phase4_adapter_resilience.py`)
plus the live run to the date gate. In an environment with at least one open date,
`scripts/ni_phase4_acceptance.py --execute` is the single command that replaces this
limitation with a live `VERIFIED_SUCCESS`.

## Implemented

- `InspectionAction` and `InspectionActionResult` contracts, including before/after state.
- Exact inspection-type matching: case/punctuation/order variants are accepted, but substring and ambiguous matches are rejected; the matched portal spelling is preserved.
- Date constraint normalization for concrete dates, next week, weekdays, before/by, after, and earliest selection. Selection is chronological and never expands the requested window.
- Advisory alternatives: `dates.closest_alternatives` reports the nearest portal dates outside the request when selection fails. It is opt-in (`allow_alternatives`) and never selects; the action still fails and the caller must obtain a new instruction to use one.
- Separate action policy: scheduling and rescheduling are automatic; cancellation requires explicit confirmation.
- Pre-action checks: permit identity, inspection identity, eligibility, already-scheduled idempotency, completed/cancelled protection, required inputs, and date constraints.
- Post-action verification: every mutation re-reads inspection state and is only successful when the observed state matches the proposed state.
- Uncertain submission reconciliation: state is re-read before any possible retry; the executor never blindly re-submits.
- Structured verification states: `VERIFIED_SUCCESS`, `VERIFIED_FAILURE`, `UNVERIFIED`, and `STATE_MISMATCH`.
- Mutation audit records with permit, inspection, requested/proposed state, browser steps, portal response, and verified final state.
- Conservative Phase 3 bridge: conflicting, ambiguous, low-confidence, or prerequisite-blocked reasoning cannot become an action.
- End-to-end entry point: `licet.phase4.coordinator.run_inspection_workflow` assembles the whole path — verified Phase 3 reasoning → action selection → policy → executor → independently verified portal state — and names the stage that stopped it (`selection`, `validation`, `execution`) so a refusal that never touched a browser is never mistaken for a portal outcome.
- Phase 4 metrics + audit persistence: `licet.phase4.metrics.Phase4Metrics` counts throughput and the zero-target invariants (duplicate submissions, wrong-record/wrong-inspection mutations, constraint violations, unverified successes), which it re-checks against the executor's own audit rather than asserting. `Phase4ActionRunner` records them and writes each `MutationAudit` to the run log via `RunLogger.log_event`, so the mutation trail survives the process.
- Mutation safety (DeepSeek review, `docs/phase4/mutation_safety_review.md`): idempotency covers an in-flight `Requested`/`Pending` request, the observed stable record key is enforced before any mutation, an exact requested weekday is never substituted, omitted required inputs fail closed, a same-date reschedule is refused rather than reported verified, and one action writes exactly one audit record.

## Tests

`tests/test_phase4_actions.py` contains 36 scripted cases covering scheduling,
rescheduling, cancellation, exact matching, date constraints, unavailable
dates, missing inputs, duplicate prevention, wrong-record protection, uncertain
submissions, post-action mismatches, confirmation boundaries, audits, and the
Phase 3 action-selection bridge.

`tests/test_phase4_accela_portal.py` contains 28 further cases for the live
adapter: they run the *real* `ToolDispatcher` and safety guard over a
state-machine fake of the ACA scheduling surface (detail → type grid →
calendar → time range → confirm → result), pinning record-identity refusal,
grid-gated type selection, day-gated calendar clicks, intent-carrying Continue
steps, disabled-Continue semantics, and the UNVERIFIED outcome when the portal
prints no confirmation and the re-read shows nothing scheduled.

`tests/test_phase4_mutation_safety.py` adds 17 DeepSeek review cases for the
four zero-targets (wrong permit/inspection, duplicate submission, constraint
violation, unverified success) plus the single-audit guarantee and the
adapter's stable-record-key gate.

`licet/eval/phase4_fixtures.py` holds the shared Phase 4 fixtures: a
`ScriptedPortal` double (records every read/submit so a refusal can be proven
to have never reached the mutation) and the checklist's 25 cases as data — 10
scheduling, 5 rescheduling, 5 cancellation, 5 failure/safety/idempotency.
`tests/test_phase4_cases.py` runs all 25 through the real executor and covers
the advisory-alternatives utility. It is also the "scripted action tests
first" harness: fixtures in, expected `InspectionActionResult` out.

`tests/test_phase4_coordinator.py` adds 13 end-to-end cases: selection →
execution → verification over the scripted portal (including the "earliest in
next week" milestone), the selection-stop and validation-stop paths (no portal
read), the cancellation confirmation boundary end to end, the metrics counters
and rates, each zero-target probe firing on a synthetic regression, and the
persisted run-log audit record.

`tests/test_phase4_adapter_resilience.py` adds 8 cases: a transient read and a
timed-out settle are retried, a permanently failing read is bounded and degrades
to an explicit `Unknown`, a failed commit click is attempted exactly once, the
reschedule and cancel paths refuse before any wizard step, and a reschedule
commit intent is acknowledged while a benign intent still cannot bypass the
commit point.

`tests/test_phase4_acceptance_runner.py` adds 8 cases for the acceptance
runner's planning halves (plan-only default, request construction, preview) and
the calendar-to-ISO-dates helper.

The full suite passes with 770 tests.

## Acceptance harness and audit

`scripts/ni_phase4_acceptance.py` is the live exit-gate harness for one permit:
read the record's inspections and the wizard's offered types, read the calendar's
availability, then select -> policy -> execute -> independently verify. **A bare
run is a plan**: nothing is submitted without `--execute`, and even then the
mutation rides the same policy/executor/adapter path as the product.

Live 2026-09-22: `BLD26-00469` was accepted and issued through the back office
(`scripts/ni_backoffice_accept.py`, inspect-only unless `--apply`), verified
independently by re-reading the citizen portal's My Records grid
(`Submitted` → `Ready to Issue` → `Issued`, via `scripts/ni_my_records.py`). The
wizard now opens for it and offers `Rough`, `Service`, `Temp Service Pole`,
`Ground Work`, `Electrical Final`, `Progress Check` — but the calendar renders
**0 active days across Sep/Oct/Nov 2026**, so the run ends in
`VERIFIED_FAILURE` / `no available date satisfies the user's constraints` with
every zero-target counter at 0. The two public probe records remain `not_found`
in the account's scheduling context. **Appointment capacity is now the only
blocker** — permit status no longer is. See `docs/phase4/checklist_audit.md` for
item-by-item status (Done / Scripted / Env-limited / Open) and what is required
to sign the phase off.

One adapter constraint the live run exposed and the harness now handles: the
adapter's sync methods wrap `asyncio.run` and refuse to run inside a live event
loop, so the harness runs the sync executor on a worker thread and marshals its
portal calls back onto the loop that owns the Solari client (`LoopBridgePortal`).
Handing the executor a second loop both raises and then hangs.

## Live adapter boundary

The executor intentionally depends on the `InspectionPortal` protocol rather
than embedding Accela selectors or permission checks in button-click code.
That protocol now has a real implementation:
`licet/phase4/accela_portal.AccelaInspectionPortal`, built on the verified
Accela/Solari mapping (`licet/browser/accela.py`, `licet/browser/solari_client.py`)
and driven only through the `ToolDispatcher`, so the safety guard, flow-position
tracking, and JSONL run log cover every step it takes. It owns the
portal-specific half of the split:

- **Read path** — land on the record's deep link using the *verified*
  capID1/2/3 ref from the page (Phase 2's identity contract; never guessed from
  a displayed id alone), open the Inspections section by its benign label,
  settle the AJAX render, and parse inspection rows through the Phase 3
  vocabulary parser, with a literal `Type | Scheduled | MM/DD/YYYY` fallback so
  the date column survives. Mid-load sections degrade to an explicit
  `Unknown (...)` snapshot — never to a guessed "not scheduled".
- **Wizard walk** — select the type from the popup's radio grid
  (`gvInspectionType_ctlNN_rdInspectionType`, refusing a type the grid does not
  offer), click the requested day only inside a *rendered, active* calendar
  cell (month tables carry no captions on this portal; the implied 3-month
  strip is resolved in `accela.resolve_calendar_months`), pick the first listed
  time range deterministically, and pass through the confirm step with an
  intent that acknowledges the scheduling commit. The popup Continue is never
  clicked while the portal has rendered it disabled (`href_disabled`).
- **Result** — the portal's confirmation number is parsed when printed; a
  submission with neither a confirmation number nor a scheduled row raises, and
  the executor reconciles by re-reading state (never by replaying).- **Cancellation and reschedule** — no owned record ever held a scheduled inspection
  on this sandbox (zero bookable dates), so the per-row Reschedule/Cancel
  controls `docs/accela_ui_map.md` §6 describes were never rendered and never
  captured. The adapter therefore refuses **both** paths rather than
  improvising. Cancellation also remains guard-held at the dispatcher.
  Rescheduling is the more dangerous of the two: driving it through the
  *new-request* wizard would create a second appointment for the same type and
  then report a verification mismatch — a real wrong mutation. Mapping either
  flow stays open until a portal with a scheduled inspection shows the control.
- **Bounded retries** — only read/settle steps retry (`_READ_ATTEMPTS`,
  `_SETTLE_ATTEMPTS`), plus a re-read for element lookup when the type grid
  re-renders. A commit or wizard-advancing click is never replayed: the executor
  reconciles a commit by re-reading state, so an adapter-level retry there would
  be a double-submit risk. `reschedule_inspection` was added to
  `COMMIT_ACKNOWLEDGING_ACTIONS`, so a reschedule commit can no longer be
  silently relabelled as the schedule commit in the audit.
- **Scope boundary** — the adapter never decides permission, eligibility,
  idempotency, or the date; those belong to the policy layer and executor. It
  also never searches for a record: the verified ref or the current page must
  already address it.

The Null Island sandbox still has no bookable dates, so a live mutation cannot be
used as an acceptance fixture there; the scripted executor + adapter tests remain
the mutation acceptance harness until a sandbox date exists. The live path is
exercised as far as the date gate and refuses there. Before any live scheduling
run, re-measure availability (`scripts/ni_availability_sweep.py`) — the
zero-availability finding is data, not a permanent property.

Payments and application submission remain outside Phase 4.
