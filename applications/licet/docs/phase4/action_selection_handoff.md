# Phase 4 — architecture review action-selection handoff

Completed 2026-09-21. Assigned scope: which inspection and operation the verified
Phase 3 evidence supports. Implementation: `licet/phase4/selection.py`; exact
label alias support: `licet/phase4/matching.py`. This component makes no browser,
model-provider, authorization, date-choice, or mutation calls.

## Checklist coverage

- [x] Connect supported Phase 3 next-action candidates to InspectionAction.
- [x] Require sufficient confidence without treating confidence as permission.
- [x] Stop on unresolved candidates, conflicting evidence, or missing prerequisites.
- [x] Bind selection to verified permit ID, stable record key, and snapshot ID.
- [x] Require same-record candidate and eligibility evidence.
- [x] Match one exact portal type and preserve its terminology.
- [x] Support the checklist's Rough Electrical / Electrical Rough /
      Electrical - Rough / Rough Electric variants without fuzzy matching.
- [x] Distinguish a new request from rescheduling or cancelling a specific attempt.
- [x] Preserve original dates, windows, inspection ID, and constraints at selection.
- [x] Return structured stop reasons and required additional state.
- [x] Test hard action-selection cases without invoking a browser.

Policy, calendars, forms, submission, retries, audit persistence and final
verification belong to implementation/portal integration/the adversarial review’s assigned portions. No live scheduling
was performed. These checks establish selection behavior, not the Phase 4 exit
condition or a production wrong-mutation rate of zero.

## Before and after

Previously the bridge filtered high-confidence prose containing `inspection`,
then defaulted every unrecognized operation to schedule. It could select
`Review inspection: Rough Electrical`, ignore a competing low-confidence target,
and bind reasoning for one stable record to an unrelated caller permit ID.
It also had no current-history, target eligibility, or prerequisite evidence gate.

Now `select_inspection_action` takes a `SelectionContext` supplied by the
coordinator from one verified observation snapshot. Missing context safely
returns NEEDS_DATA. Existing callers that supplied only a display permit ID must
migrate; silent legacy acceptance would defeat the identity gate.

```python
selection = select_inspection_action(
    reasoning,
    permit_id=verified_display_id,
    context=SelectionContext(
        permit_id=verified_display_id,
        record_key=verified_record_ref.as_key(),
        snapshot_id=reasoning_snapshot_id,
        identity_verified=True,
        options=observed_options,
        evidence=same_record_evidence_registry,
        inspections=current_inspection_snapshots,
        history_complete=inspection_coverage.complete,
    ),
    requested_action=normalized_user_action,  # optional; preserve user constraints
)
```

Each InspectionOption contains the observed name, tri-state `eligible`, tri-state
`prerequisites_satisfied`, and evidence IDs. Both booleans must be explicitly true
for selection. A dropdown entry alone proves neither. These are trusted adapter
assertions; do not fill them from the model's confidence or free-text assertions.
The evidence registry must be scoped to the named snapshot, retain provenance,
and bind every cited item to the same stable record key. Citation existence is
necessary, not proof of semantic entailment: the adapter/rules must establish what
the eligibility evidence actually says.

The result distinguishes SELECTED, NEEDS_DATA, AMBIGUOUS, CONFLICTING,
UNSUPPORTED and ALREADY_SCHEDULED. It returns record/snapshot/evidence IDs and
needed-state descriptions for the coordinator. A selected action remains a
proposal: it does not change Phase 3's execution_allowed flag or grant permission.

## Selection rules

1. Only `answerability=answered`, no contradictions, no outstanding retrieval,
   and no blocking uncertainty can proceed.
2. Identity must already be verified; display ID, stable key, and snapshot must
   agree. A high-confidence foreign/stale candidate still stops.
3. The migration adapter accepts only complete commands of the form
   `Request inspection: TYPE`, `Request reinspection: TYPE`,
   `Schedule inspection: TYPE`, `Reschedule inspection: TYPE`, or
   `Cancel inspection: TYPE`. General reasoning prose is not executable.
4. All recognized competing operations count, including lower-confidence and
   conditional competitors. Two plausible operations do not become one merely
   because one passes a numeric threshold. No arbitrary tie-break.
5. The unique candidate needs finite confidence within the configured range,
   `likely` or `required` strength, no unresolved preconditions, and evidence.
   A `possible` action at 1.0 confidence still stops. Confidence is not calibrated
   probability. Required inspection language does not itself prove scheduling
   is the correct operation; `Complete required inspection` remains non-executable.
6. A unique exact option, supported eligibility/prerequisites, and complete current
   history must be independently provided. Unknown/disabled options stop.
7. Scheduling stops if a matching attempt is scheduled, pending/requested, or
   unknown. A past failure can coexist with a new request only after correction
   and prerequisite evidence establishes eligibility; the failure alone does not.
8. Rescheduling/cancelling requires the user-bound existing inspection ID and
   exactly one matching scheduled attempt. Same-type rows are not interchangeable.
9. An explicit requested action cannot change operation, record, or inspection
   target. All date fields and free-text constraints are copied intact. Selection
   never widens Friday to Monday or chooses a calendar date.
10. Cancellation and candidate confirmation metadata remain flagged. Policy owns
    the approval decision and must still run immediately before mutation.

## Worked ambiguous-state decision

Input: Rough Electrical has Corrections Required; Final Electrical is unrequested;
General Final is pending; Rough Plumbing passed. Those facts alone establish no
immediately executable action.

- Rough Electrical is a plausible reinspection target, conditional on correction
  completion, current eligibility, and no existing follow-up appointment.
- Final Electrical being unrequested does not establish prerequisites or priority.
- General Final pending must not receive a duplicate request.
- Rough Plumbing passed does not establish another trade's prerequisites.

Return NEEDS_DATA with the relevant correction/eligibility/current-attempt evidence
request. Once Rough Electrical alone is supported by verified prerequisites,
current complete history, one portal option and an explicit scheduling candidate,
select that portal option. Do not transform the physical correction recommendation
itself into a browser action.

## Integration notes for implementation

Use `action_from_selection(selection)` to bind the selected action to its
record/snapshot/evidence metadata. Validate that context is still current, check
user intent/policy, and obtain required inputs before execution. InspectionAction
and MutationAudit now retain those bindings. Fresh pre-action record/inspection
verification is still mandatory. Selection-time completeness is not a substitute for executor-time
idempotency or post-submit verification.

The targeted `runner.action_from_selection` integration is now implemented:

- Explicit window bounds and preferred dates survive selection, preview, and
  executor handoff. Date language intersects explicit bounds; contradictory
  dates fail before browser access. Exact dates cannot silently fall back.
- Arbitrary constraints are retained as constraints, never treated as date text.
  This handoff does not itself interpret or enforce free-text access/time notes.
- Stable record key, snapshot ID, and evidence IDs reach InspectionAction and the
  mutation audit. Conflicting selection/action identity metadata is rejected.
  Retaining provenance does not replace fresh executor/adapter identity checks.
- Candidate confirmation requirements reach the existing policy layer, which
  blocks unconfirmed actions before portal access. Cancellation remains held.
- Runner accepts observed availability for the existing executor's date selector.

Do not lower the threshold merely because current Phase 3 rules emit
.72/.75 conditional recommendations: retrieve the missing evidence or obtain an
explicit user target instead.

## Validation

`tests/test_phase4_selection.py` adds 55 selection regressions, including the four
specified label variants, unsupported/negated prose, non-finite confidence,
possible-action promotion, conditional correction, lower-confidence competitors,
foreign/stale records, coverage gaps, missing/foreign evidence, disabled/unknown
eligibility, duplicate options, existing/pending appointments, exact appointment
IDs, preserved constraints, and confirmation metadata.

Together with the existing executor and Accela adapter tests: 119 passed.
The old confidence-only positive test now correctly expects a context-required
stop; supported positive selections are exercised with complete verified context.

The targeted adapter regressions are in `tests/test_phase4_runner_handoff.py`: window preservation, constraint separation, exact-date refusal, confirmation enforcement, provenance in audits, and conflicting metadata/date rejection.
