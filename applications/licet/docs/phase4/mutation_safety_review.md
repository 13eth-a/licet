# Phase 4 mutation-safety review — DeepSeek V4.1 Flash

Reviewed 2026-09-21 against the working tree after Luna's executor/policy work
and GLM's Accela adapter. Scope, per the Phase 4 assignment: every path through
this code that could **mutate the wrong record, violate a user constraint,
duplicate an action, or falsely report success**, run against the assumption
that the browser eventually drives a real municipal portal.

Method: read the deterministic executor (`licet/phase4/workflow.py`), the
policy layer, the date layer, the Accela adapter, and the runner handoff, then
construct the counterexample state for each hazard and run it. Every finding
below was reproduced against the inherited code first; each fix is locked by
regressions in `tests/test_phase4_mutation_safety.py` (17 cases). Full suite:
**707 passed** (was 690).

Targets this review is measured against: wrong permit mutations 0, wrong
inspection mutations 0, duplicate submissions 0, user-constraint violations 0,
unverified successes 0.

## Findings fixed

| ID | Hazard | Inherited behavior (measured) | Why it is unsafe | Resolution |
|---|---|---|---|---|
| M1 | **Duplicate action** | `is_scheduled` recognised only `scheduled`/`confirmed`/`appointment scheduled`. A row the portal reported as **`Requested`** or **`Pending`** fell through the idempotency gate and the executor submitted a *second* request for the same inspection. | The adapter's own row vocabulary (`_adapter_rows`) parses `Requested`/`Pending`, so this is reachable on the real portal, not hypothetical. An accepted in-flight request already produces the desired outcome; a duplicate submission does not. | Added `InspectionSnapshot.is_pending` (`requested`/`pending`) and gated `schedule` on `is_scheduled or is_pending`. `not scheduled` remains requestable, so the core workflow is not blocked. |
| M2 | **Wrong-record mutation** | The action carried a stable `record_key`, but the executor only compared the *displayed* permit id and the adapter only compared the *displayed* permit id. The key reached the audit and nothing else. | Two records can render the same displayed number; the stable capID ref is the identity that actually addresses a record (Phase 2's contract). A display-id match is not proof the page is the authorized record. | `InspectionSnapshot` now carries `record_key`; the executor refuses (`STATE_MISMATCH`) when the action's key and the observed key disagree, and the adapter refuses at the UI boundary (`submit_inspection_action_async`) when the page's capID key disagrees — two gates, both before the first mutation click. |
| M3 | **User-constraint violation** | `select_date` returned the earliest allowed day when an exact requested weekday was unavailable: `"Friday"` with Friday booked could schedule **Monday** and report `VERIFIED_SUCCESS`. | The checklist names this exact failure ("silently chooses Monday"). A constraint that is quietly widened is worse than a stop, because the result is verified and reported as the user's intent. | `select_date` returns `None` when a `preferred` date is set and absent; the executor surfaces `DATE_CONSTRAINT_UNSATISFIED` instead of substituting. |
| M4 | **Missing-input fail-open** | `if required_inputs is not None:` — the runner defaults `required_inputs` to `None`, so whenever the caller did not pass an input mapping the required-field gate was skipped entirely and the action proceeded. | "Never fabricate required information" was enforced only when the caller happened to pass an (empty) dict. The default path is the common path. | An omitted mapping is treated as no inputs supplied; every declared `required_fields` entry must have a truthy value or the action stops with `MISSING_REQUIRED_INPUT`. |
| M5 | **False success** | A reschedule whose selected date equalled the current scheduled date could be "verified": `_matches` compared only the date, so an unchanged re-read (including the uncertain-submission reconciliation path) produced `VERIFIED_SUCCESS` for a mutation that never happened. | The reschedule contract is "verify the **old date changed** to the new date". When the target equals the source, that verification is not possible, and a failed submit is reported as success. | The executor refuses a same-date reschedule (`RESCHEDULE_FAILED`) before submitting, because the change it is required to verify cannot exist. |
| M6 | **Audit-trail integrity** | The uncertain-submission and post-submit-mismatch branches each wrote an audit record *and then* called `_failure`, which wrote a second one. One action produced two audit entries. | A mutation audit trail is the reconciliation source of truth after a crash or a disagreement; double entries distort duplicate-action and verification metrics and make "one action, one record" untrustworthy. | `_failure` now accepts `steps`/`response`/`proposed` and writes exactly one audit entry per action; mutation-path failures keep their richer browser-step evidence. |
| M7 | **Wrong mutation (reschedule)** | The adapter routed a `reschedule` through the same *new-request* wizard as a schedule: open the record's `Schedule an Inspection` link, pick the type and a date, and commit. On a portal where the record already holds a scheduled inspection, that creates a **second appointment** for the same type and only then reports a state mismatch. | A reschedule must move the existing appointment, not add one. The per-row Reschedule control was never captured (no owned record ever held a scheduled inspection), so the new-request wizard was the only path encoded. | `submit_inspection_action_async` now refuses `reschedule` (and `cancel`) before any wizard step, the same fail-closed move already used for cancellation. The executor's reschedule logic stays tested against fakes for when a portal surface exists. |
| M8 | **Mislabeled commit** | `reschedule_inspection` was absent from `COMMIT_ACKNOWLEDGING_ACTIONS`, so a reschedule intent on the wizard's confirm step resolved to the flow's `schedule_inspection` commit action — the commit happened, but the audit recorded a *schedule*. | The commit point's whole purpose is that the recorded semantic action is the real one, so the risk classification and the audit both describe what is being issued. | Added `reschedule_inspection` to the acknowledging set. A regression test pins that a benign intent (`read_record`) still cannot bypass the commit point. |

## Verified-safe paths (no change needed)

- **Cancellation boundary.** `decide_action_policy` requires explicit
  confirmation for `cancel`, the executor blocks before portal access, and the
  adapter refuses the cancel path outright rather than improvising a flow. No
  bypass found.
- **Uncertain submission.** The `except` path re-reads state and never replays
  the submit; a matching re-read is the only route to success, and a
  non-matching one is `UNVERIFIED`, not success. A blind retry cannot originate
  inside `InspectionActionExecutor`.
- **Wrong-inspection mutation.** `existing_inspection_id` is checked against the
  observed snapshot before submit; a mismatch is `STATE_MISMATCH` with no
  browser action.
- **Pre-submit ordering.** Policy → eligibility → identity → idempotency →
  required inputs → date → submit. Every refusal above happens before the first
  mutation click, and each failure returns before `submit_inspection_action`.

## Deliberately not changed (residual risk, named)

These are real gaps that are **not** fixed here, and Phase 4's zero-targets
should be read with them in mind rather than around them.

- **Free-text time/access constraints are stored, not enforced (owner:
  Luna/GLM).** `InspectionAction.constraints` (e.g. `"AM only"`,
  `"leave gate code"`) reaches the audit but not the adapter's `_pick_time`,
  which deterministically takes the first rendered time range. A user who said
  "AM only" can therefore receive a PM slot. Enforcing this needs a portal
  AM/PM vocabulary and a form-field policy — Luna's/GLM's assigned surface — so
  it is recorded here rather than half-built. Until then, callers must not treat
  a stated time preference as honoured.
- **No cross-process idempotency key (owner: Luna).** If the process dies
  between the commit click and the verification re-read, a fresh run is
  protected only by the read gate (`ALREADY_SCHEDULED`) once the portal has
  materialised the request. On an eventually-consistent portal a re-run inside
  the propagation window could duplicate. Phase 4 is single-run, so this is a
  Phase 5 concern, but it is a duplicate-action path.
- **No concurrency guard on a record.** Two executors acting on the same permit
  are not mutually excluded. Single-run scope.
- **A portal implementation that declines to assert identity falls back to the
  display id.** If an `InspectionPortal` returns no `record_key`, M2's hard gate
  cannot fire and only the displayed-permit check remains. The real
  `AccelaInspectionPortal` always populates the key on any snapshot that can
  proceed (unknown snapshots are ineligible and never submit), so live runs get
  the gate; this is pinned as a contract, not a claim of key coverage for every
  conceivable portal.
- **Ambiguous `Awaiting` rows are treated as requestable.** `normalize_lifecycle`
  groups `awaiting` with `not scheduled`/`pending` as an appointment-less
  lifecycle, so the executor does not treat `Awaiting` as an in-flight request.
  If a real agency uses `Awaiting` to mean "request received", this is a
  duplicate path — it needs live vocabulary evidence, not a guess.
- **Advisory alternatives are surfaced, not enforced (owner: coordinator).**
  `closest_alternatives` / the opt-in `allow_alternatives` flag report dates
  outside the request when selection fails; the executor never selects or
  submits one. Nothing in this layer forces the coordinator to ask the user
  before issuing a new action for a reported alternative, so that gate lives
  above Phase 4.
- **Post-action verification re-reads through the same adapter.** It is an
  independent re-read of portal state (re-navigate, re-render), not an
  independent channel. Reconciliation with a second identity (e.g. the
  confirmation number) is not required to succeed.

## Retry policy (added 2026-09-21)

Only read and settle steps are retried, each a bounded number of times
(`_READ_ATTEMPTS`, `_SETTLE_ATTEMPTS`), plus one re-read for element lookup when
the type grid re-renders. **No commit or wizard-advancing click is ever
retried**, because the executor's reconciliation is a state re-read, never a
replay — an adapter-level retry of the commit would be exactly the duplicate
submission M1 exists to prevent. A permanently failing read degrades to an
explicit `Unknown` snapshot (ineligible, never a guessed "not scheduled").

## Evidence

- `tests/test_phase4_mutation_safety.py` — 17 regressions: in-flight duplicate
  blocking (requested/pending) and the `not scheduled` non-block, record-key
  refusal/matching/audit and the decline-to-assert contract, exact-weekday
  non-substitution and constraint-failure codes, omitted-input fail-closed and
  supplied-input success, same-date reschedule refusal, single-audit integrity
  on both failure paths, and the adapter's key population + UI-boundary refusal.
- Full suite: `707 passed`.
