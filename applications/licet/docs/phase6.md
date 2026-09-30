# Phase 6 — Safety, Permissions & Guardrails

Phase 6 adds a deterministic safety boundary between semantic proposals and
browser mutation. The model may propose a supported action, but it cannot grant
itself permission or call the browser around policy.

## Architecture

```text
Planner / model proposal
        ↓
licet.safety.policy.PolicyEngine
        ↓
ALLOW / CONFIRM / DENY
        ↓
Phase 4 executor + Accela adapter
        ↓
post-action read and independent verification
```

The central implementation is `licet/safety/policy.py`. The Phase 4
`InspectionActionExecutor` invokes it before submitting a supported mutation;
the Phase 5 planner can also receive a `PolicyEngine` and checks the same
proposal before the capability layer. The existing `ToolDispatcher` remains the
only planner-to-browser primitive path.

## Environment modes

`Environment.SANDBOX`, `LIVE_READ_ONLY`, and `UNKNOWN` are classified from an
explicit environment or known portal host, never from record contents or test-
looking data. `UNKNOWN` is fail-closed. Every state-changing action is denied
unless the environment is explicitly `SANDBOX`; reads remain allowed in
`LIVE_READ_ONLY`.

Fail-closed is structural, not advisory: **there is no executor without a policy
engine.** `InspectionActionExecutor` (and `LicetCapabilities`) always construct
one, and an unclassifiable portal yields an `UNKNOWN` engine that denies every
mutation. A caller that has genuinely verified a sandbox declares it explicitly
(`environment=Environment.SANDBOX`, as the real adapter derives from its session
host and the test doubles state outright). Defence in depth below that: the
Accela adapter refuses to commit against a positively-live host, and the
dispatcher's guard applies the same rule to **every** state-changing action —
live and unknown both refuse, an approval included. That is only possible because
the dispatcher records the page it actually read into `AgentState.current_url`;
before that, an adapter call looked like an unknown environment and the rule
could not be evaluated on the real route.

## Action policy

`ActionRisk` is a closed classification: read-only, reversible,
consequential, or prohibited. Unknown planner actions receive
`UNKNOWN_ACTION_RISK` and are denied. Sandbox scheduling/rescheduling is
reversible; cancellation, payments, submissions, uploads, edits, and renewals
are consequential and require confirmation. Legal attestations, fabrication,
identifier invention, authentication bypass, authorization override, and live
mutation are prohibited.

## Constraints and confirmation

`UserConstraints` is an immutable run-scoped value object. Common natural
language restrictions are parsed conservatively, including read-only, no
payments, no submissions, schedule-but-don't-cancel, broad approval with an
exception, and no existing inspection changes. Direct prohibitions override
broad language regardless of clause order. Constraints are not derived from or
replaceable by planner output.

The run's permissions are parsed at the primitive layer too, from trusted text
only (`guard.run_constraints`: the state's goal plus any explicit constraint
lines, never model output or portal content), and a click that contradicts them
is refused — "don't spend money" blocks a payment control in a sandbox, not just
in the semantic layer. `AgentState.user_constraints` is therefore read rather
than a dead field.

`ConfirmationRequest` is scoped by operation, permit, target, inspection ID,
record key, both date-window edges, and optional amount; nullable fields are
compared exactly rather than treated as wildcards. It expires after ten minutes
and is consumed at the policy boundary both on the object *and* in a trusted
registry keyed by confirmation id, so a copied or deserialized approval cannot
authorize a second execution. Silence is not approval, and neither is a bare boolean: a
consequential action requires the scoped object, so the executor cannot mint
permission for whatever it is pointed at. `GoalPlanner` issues that object from
its own verified proposal when it pauses and hands it down on resumption, so the
planner's token binding and the boundary's approval scope check the same action.

A targeted mutation (cancel, reschedule) must name the appointment it acts on
(`inspection_id`); without it the approval, the identity check and the
post-action verification have nothing to bind to, so it is refused with
`TARGET_INSPECTION_UNIDENTIFIED` rather than resolved against whichever row the
portal rendered.

The click route's approval is scoped to what was on screen when it was asked for:
`AgentState.is_approved` refuses a grant whose recorded URL, record or flow step
has since moved, so navigating to another record does not inherit the previous
record's authorisation.

## Identity, stale state, and inputs

Every mutation proposal can be checked against an observed permit, stable record
key, inspection type/ID, and existing date. Missing observations or mismatches
return `RECORD_IDENTITY_UNVERIFIED`. Required fields are checked before
submission; no phone, applicant, parcel, license, access instruction, legal
text, or signature is invented. The executor also retains its pre-existing
identity and date-window gates.

## Mutation safety

`MutationLedger` assigns a unique mutation ID, captures the before state, and
tracks `NOT_STARTED`, `SUBMITTED`, `VERIFIED_SUCCESS`, `VERIFIED_FAILURE`, and
`UNKNOWN_RESULT`. Duplicate completed actions and unknown results cannot be
replayed. A timeout must be reconciled by reading current state, not by blindly
resubmitting. The default run limit is two reserved mutations.

The executor re-reads the portal after each submit and only reports success
when the observed target state matches. Its existing workflow requires a fresh
read after a mutation and the Phase 5 planner clears the verified inspection,
forcing a verify/replan cycle before another mutation.

## Audit logging

`SafetyAuditLog` records allowed, confirmation, and denied policy decisions,
including run ID, environment, goal, immutable constraints, permit, action,
risk, reason, confirmation ID, and state fields. It can forward events to the
existing JSONL `RunLogger` as `safety_audit` events. The executor's existing
mutation audits continue to record before/after state and verification details.

## Verification

The Phase 6 deterministic/adversarial suite is in
`tests/test_phase6_policy.py`; it covers environment modes, constraints,
confirmation scope and expiry, identity mismatches, required inputs,
unknown actions, portal-instruction text, mutation idempotency, unknown
results, max mutation limits, and audit records.

DeepSeek's adversarial review (`docs/phase6/adversarial_review.md`) added
`tests/test_phase6_adversarial.py` (98 cases) and
`scripts/phase6_adversarial_replay.py`, and closed four classes of bypass:
an unclassified portal silently losing its policy layer, identity verification
passing when the observation omitted the targeted field, a bare `confirmed=True`
being treated as an approval, and an under-specified targeted mutation. Open
residuals and their owners are listed at the end of that review — notably that
the capability factory is still not handed the user's instruction (the
constraint enforcement that does exist runs in the guard and in Phase 5's
immutable `Goal`, and a regression test keeps those two readings of the six
checklist phrases in agreement), and that cross-process idempotency remains
run-local.

Astra's architecture review (`docs/phase6/astra_architecture_review.md`, with
DeepSeek's point-by-point response in `docs/phase6/astra_review_response.md`)
reproduced the four above plus primitive-layer environment and intent handling,
approval binding and copy-resistance, post-action verification identity, and
vocabulary parity between the guard and the policy engine. All of it is closed or
named as a residual; `scripts/phase6_astra_review.py` exits 0 with its positive
controls intact.

## Portal mutation-boundary map (GLM 5.3 Flash)

GLM's lane audited where Accela actually mutates state and mapped it as data:
`licet/browser/accela.py:MUTATION_BOUNDARIES` (per flow/step: control, semantic
action, risk tier, mutates/commit flags, evidence provenance), with the full
audit in `docs/phase6/portal_boundary_map.md`. Headline facts the policy layer
now rests on: the only scheduling commit is the popup Continue on the wizard's
confirm step (portal-disabled until date+time chosen; every earlier step —
type radio, day cell, time link, early Continue — is navigation, which is what
makes driving the wizard to the gate lawful); the apply wizard's CapConfirm
Continue issues a record with no payment gate; the apply disclaimer's agree
checkbox is the one attestation control and it is PROHIBITED; cancel,
reschedule, payment, upload and renewal controls have never rendered on this
sandbox and are recorded UNMAPPED (fail-closed) rather than inferred.

The lane also closed the portal-data half of the cancel/reschedule identity gap
(the upstream half from DeepSeek's D1 handoff): the citizen portal exposes no
inspection-id column, so the client now parses the per-row action controls from
the page HTML (`accela.parse_inspection_row_controls`, shipped as
`read_page` → `inspection_row_controls`) and the Phase 4 adapter binds an
appointment id to a snapshot when — and only when — the read is unambiguous
(exactly one scheduled/requested row, exactly one cancel/reschedule control).
Ambiguous reads bind nothing; the policy engine then refuses with
`TARGET_INSPECTION_UNIDENTIFIED` as before, but a clean read now names the
appointment the human approves. Locked by `tests/test_phase6_portal_boundary.py`
(23 cases): map/risk-catalogue parity, wizard-navigation-is-read-only,
unmapped-honesty, the real engine refusing an id-less cancel, and the parser's
read-only-link refusal ("Cancellation Policy" is not a cancel control).

## Close-out additions

The items the checklist names but the policy engine did not yet represent are
now executable, and mapped item-by-item in
`docs/phase6/checklist_audit.md`:

- **Contradictory instructions → `CONSTRAINT_CONFLICT`.**
  `licet.safety.policy.detect_constraint_conflict` reads the positive clause of
  an instruction and asks the same permission object the run enforces whether it
  is forbidden; `parse_goal` reports the conflict instead of interpreting it.
- **Safety stop conditions.** `licet.safety.stops.SafetyStopCondition` is the
  checklist's eleven stops, with `stop_for_decision`/`stop_for_result` mapping
  every deterministic decision and execution result onto one of them.
- **Observation provenance.** `licet.safety.sources` labels text `TRUSTED`
  (system policy, user instruction) or `UNTRUSTED` (portal text, inspector
  comments, documents, model output); `trusted_text` is the one supported way to
  assemble intent from sources.
- **Safety metrics.** `licet.safety.metrics.SafetyMetrics` accumulates the seven
  zero-targets, fed by `PolicyEngine(metrics=…)` for refusals and by
  `InspectionActionExecutor(metrics=…)` at the one place a submission reaches
  the portal.
- **Flagship integration.** `tests/test_phase6_integration.py` runs the
  checklist's own task (address lookup + no-spend) end to end: sandbox schedules
  and verifies while never paying; live prepares but submits nothing.

Current verification:

```text
python -m compileall -q licet tests scripts
pytest -q
1166 passed (228 Phase 6 cases)
python scripts/phase6_adversarial_replay.py --json docs/phase6/adversarial_evidence.json
exit 0, all seven safety metrics zero
python scripts/phase6_astra_review.py --json docs/phase6/astra_review_evidence.json
exit 0, 14/14 checks
```

Live mutation remains intentionally blocked outside an explicitly classified
sandbox. A live portal run can read and prepare a proposed action, but cannot
submit scheduling, cancellation, payment, submission, upload, or edit actions.
