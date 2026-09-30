# Phase 7 completion — recovery runtime and acceptance

2026-09-24. This implementation follow-up supersedes the open runtime-wiring items in the earlier architecture review, adversarial review, and portal integration reviews. Phase 7's bounded recovery implementation and simulated integration acceptance are complete. These results are not live Accela validation.

## What changed

- **Runtime recovery:** GoalPlanner calls the semantic recovery decision layer and RecoveryController through `licet/phase7/runtime.py`. Failed safe reads receive bounded retries with a mandatory result validator. Invalid planner choices consume the replan budget and never reach execution. No-progress triggers bounded replanning; revisiting A/B states no longer creates new information. The existing overall step/loop limits remain in force.
- **Known portal routes:** `LicetCapabilities.recover_read` executes waiting, navigation to the verified record, and return to the original browser page, then performs the original read through the normal capability. Informational-modal/popup recovery abandons the obstructed page for the verified record instead of clicking an unknown dismissal/confirmation control. If navigation is blocked, recovery stops within its budget. Authentication and consequential modals stop immediately. Persisted portal findings now survive the retrieval-to-planner handoff, and terminal findings outrank loading findings regardless of input order.
- **Freshness and checkpoints:** failed read results do not overwrite verified world state. Recovery starts from a copy of the prior verified state and validates the new record and settled observation before accepting it. The runtime records semantic checkpoints, checks fresh fingerprints on recovery, and invalidates mismatches. Checkpoints are deep-copied and cleared between runs; they are not authority to mutate. Conflict recovery refreshes overview/history once, then re-interprets or stops. Changed terminal permit status stops preparation rather than inventing a renewal action.
- **Controller hardening:** callbacks without validators are refused before execution. Validators must return True. Each attempted recovery consumes the per-operation and global budgets, including unsuccessful attempts. Async attempts have a timeout. A→B→A is NO_PROGRESS once those states have already been observed. Per-run metrics/checkpoints no longer leak into another run.
- **Mutation reconciliation:** the planner reserves mutation keys and records independently verified outcomes. Unknown outcomes remain unknown and cannot release a reservation. A trusted, explicit absence result can release the controller reservation at most once; it never overrides Phase 6 policy or the executor ledger. The production planner deliberately does **not** infer absence from an unchanged page or automatically resubmit. Checklist scenario 10 therefore stops safely unless authoritative absence and execution-layer permission are available; scenario 9 verifies the committed operation without a second submit.
- **Crash/restart protection:** the real-session factory installs a SQLite MutationJournal. The bridge reserves immediately before the actual submission, after the executor's policy/precondition gates. An unresolved record is quarantined across processes even if a new proposal has a different fingerprint. Independent verification completes the matching entry; there is no timeout-based unlock. The default is `~/.licet/mutation-journal.sqlite3`, configurable with `LICET_MUTATION_JOURNAL`. Custom factories must inject a shared MutationJournal for equivalent cross-process protection. Direct Phase 4 integrations outside this factory retain their own execution contract.
- **DOM grid evidence:** known record-grid tables report row counts and explicit empty-state evidence. A header-only or unpopulated grid is marked unsettled, not interpreted as “no inspections/fees.” Unknown layout tables are excluded. This is conservative evidence over recognized grids, not a universal parser for every agency's markup.
- **Reports:** each Run report now includes recovery budgets, statistics, traces, and checkpoint validity. Metrics distinguish capability recovery attempts from additional dispatched browser actions. Mutation outcomes, recovery reasons, and remaining goals remain visible.

## Reproducible acceptance

Run:

```sh
.venv/bin/python scripts/phase7_acceptance.py
.venv/bin/python -m pytest -q
```

The acceptance harness reuses the existing Phase 2 lookup/browser fixtures and drives the actual lookup runner, Phase 3 retrieval/reasoning, Phase 4 selection/executor and policy, and Phase 5/7 planner/recovery. External browser/portal I/O is simulated. The 26 executions use seeded timing variation and shuffled scenario order. The model-choice recovery case has a separate real-planner regression; this I/O cohort does not claim a model-replanning success rate.

Scenarios: transient read timeout, homepage redirect, AJAX loading, informational modal, wrong page, new tab, session expiry, persistent outage, response lost after a committed submission, unknown submission outcome, wrong-record response, availability-read timeout, and a grid rendered without rows while its section settles. Each runs with two seeds. Assertions validate final status, independent target identity, at-most-one submission, no submission for terminal read failures, and recovery ceilings.

See `acceptance_evidence.json` for every run's trace and counters:

| Result | Count |
|---|---:|
| Noisy executions | 26 |
| Verified completion | 18 |
| Safe stop | 8 |
| Unexpected failures | 0 |
| Duplicate submissions | 0 |
| False recoveries | 0 |

The recovery sequence success rate is 88.9%; the mean is 0.77 recovery attempts per run. Safe stops include authentication loss, an unresolved submission, unresolved record identity, and persistent outage. They are expected outcomes, not completion failures disguised as success. Separate regressions prove restart quarantine through two complete runtime executions, bounded absence-release, validator enforcement, timeouts, terminal priority, checkpoint isolation, grid evidence, and invalid-plan recovery.

## Operational boundary

No live browser session, credentials, municipal mutation, or model API was used for this closure. Live portal qualification remains a separate deployment check. Unknown controls, unresolved conflicts, unknown submission outcomes, and unsupported recovery paths stop safely. Recovery does not bypass Phase 6 or promise completion at all costs.

An unresolved durable journal entry intentionally requires independent reconciliation; there is no automatic clearing command. Deleting/changing the journal path loses restart protection. Process-resumable browser/checkpoint restoration is not implemented: a new process starts discovery again, while the durable journal prevents replay of unresolved real-session mutations. This is the safe restart behavior for the MVP, rather than trusting cached browser state after a crash.

## Final verification

- Full suite: **1,280 passed in 25.69s**.
- Phase 7: **114 tests collected**, included in the full pass.
- Acceptance: **24/24 expected outcomes**, with 34 additional dispatched browser actions across recovery sequences.
- Existing adversarial and portal replay scripts also completed without failing cases; fresh outputs were inspected separately from their historical evidence files.
- `git diff --check`: passed.
