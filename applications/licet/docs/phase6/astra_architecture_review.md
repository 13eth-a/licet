# Phase 6 Astra architecture and authorization review

Reviewed 2026-09-23. Assignment: selective architecture review, confirmation semantics, state-change review, and final safety audit. Luna remains implementation owner. This review adds a replay script and evidence, without rewriting shared implementation while other models work on it.

> **Status, added after the fact (see `docs/phase6/astra_review_response.md`).**
> Every finding below reproduced on the tree this review was written against, and
> every one of them has since been addressed or accepted as a named residual:
> `scripts/phase6_astra_review.py` now exits **0** with all 14 checks passing
> (positive controls intact) and `docs/phase6/astra_review_evidence.json` is
> regenerated. The narrative and verdict below are kept as written, as the record
> of what the architecture looked like at review time.

**Verdict: do not sign off Phase 6 yet.** The central engine denies live mutations, but it is not the sole effective authorization boundary. The UNKNOWN-at-the-primitive-layer exception is incompatible with the exit condition, not an acceptable fixture accommodation. The prior adversarial report's claim of no alternate route and complete single-use confirmation enforcement is too strong.

## Reproducible findings

Run `.venv/bin/python scripts/phase6_astra_review.py --json docs/phase6/astra_review_evidence.json`. Exit status 1 means unresolved safety invariants. **At review time** the evidence contained 12 failed checks and two passing controls; the regenerated evidence contains 14 passing checks and no failures. These are actual local calls into the current code, not simulated legacy behavior. They prove authorization/verification defects; they do not claim any live municipal mutation occurred.

### P1 — browser primitive authorization bypasses central policy

`licet/safety/guard.py:86` blocks only schedule/reschedule on positively live hosts. Unknown-host scheduling returns ALLOW. At line 105, an action-name approval allows live cancellation, payment, and legal attestation. Legal attestation must remain prohibited even in sandbox. The generic approval is also not bound to record, inspection, date, or navigation generation (`AgentState.is_approved`).

Separately, `licet/browser/dispatcher.py:152` trusts caller `intent` before dangerous target text unless the current flow is already recognized as a commit step. `click(target="Submit Payment", intent="read_record")` resolves to read_record and is allowed on a live URL when no commit flow has been established. A buggy model can mislabel a mutation. A closed intent vocabulary alone does not establish what a click does.

Required fix (Luna, with GLM's portal map): route browser commits through the same policy and one-use authorization as semantic execution. Bind trusted adapter operations to known controls and current page/record identity; do not let model-provided intent downgrade risk. Unknown controls and unknown environments must refuse mutations. Explicit sandbox fixtures should replace the URL-less-test exemption. Test actual dispatcher calls with a recording client and assert the click count stays zero for these cases.

### P1 — approvals can change meaning and be copied

`licet/safety/policy.py:263`: ConfirmationRequest.matches does not bind record_key, existing date, new date/window, snapshot/navigation version, run, or complete action fingerprint. The same appointment ID can move to another date after approval and still match. An absent approval amount or inspection ID is treated as a wildcard. A copied unused approval retains the same confirmation ID; both original and copy receive ALLOW because consumption is only a mutable object flag, not a trusted consumed-ID registry.

The executor constructs the proposed existing date from the latest observation (`licet/phase4/workflow.py:121`), so comparing it against that same observation cannot establish that the date is still what the user approved. The Phase 5 token provides stronger protection on its normal resume path, but callers of the independent execution boundary do not inherit that protection.

Required fix (Luna): split a displayed confirmation request from an authenticated grant. Own issuance and consumption in a run-scoped trusted registry; bind grants to immutable, complete operation parameters, stable record key, target ID, old/new dates, amount, and state generation. Match nullable fields exactly. Atomically consume the registry ID at execution reservation. Test copied/deserialized approvals, same display permit across agencies, and same appointment rescheduled externally while awaiting approval. A caller-constructed ConfirmationRequest is not itself evidence of human consent; keep grant creation out of model tool arguments.

Expiry decision: retain the ten-minute fail-closed behavior. After expiry, refresh identity/target/availability, present a new concrete request, and require a new affirmative answer. Do not silently renew an old grant. Silence and broad approval never authorize later operations.

### P1 — post-action verification ignores stable record and appointment identity

`licet/phase4/workflow.py:216`: `_matches` checks display permit number/type and date or cancellation status, but ignores record_key and inspection_id. A cancelled I-2 from agency B verifies cancellation of I-1 from agency A if display permit/type match. The replay directly demonstrates this predicate returning true. Both the normal submission and timeout reconciliation paths call it, so either can label foreign state VERIFIED_SUCCESS and update the ledger accordingly. Phase 5's later verification does not repair the false Phase 4 result/audit or protect direct Phase 4 callers.

Required fix (Luna): require observed stable record identity and, for cancel/reschedule, the exact existing appointment ID on every verification path. Scheduling may create a new appointment ID: bind it through the independently observed record/type/date and capture the resulting ID, rather than requiring a preexisting one. Missing asserted identity must fail closed. Add executor-level tests for switched-record/appointment observations after both successful responses and timeouts.

### P1 — independent constraints are neither reliably parsed nor wired from the goal

`licet/safety/policy.py:203`: broad "do everything possible" resets previously parsed prohibitions. The replay shows "Do everything possible, but don't submit anything" allowing SUBMIT_APPLICATION and "...but don't cancel" allowing CANCEL_INSPECTION. "Don't schedule inspections" does not disable scheduling at all.

The runtime capability construction does not construct UserConstraints from the original instruction (`licet/eval/phase5_live.py`, `scripts/phase5_run.py`); PolicyEngine otherwise defaults to permissive operation flags. Phase 5's own immutable Goal checks help its normal path, but the assignment explicitly requires independent policy to withstand planner failures.

Required fix (Luna): parse trusted user constraints once at run creation and retain that authoritative value across planner, capabilities, executor, retries, and resumption. Prohibitions must dominate broad permission independent of clause order. Unrecognized restrictions must request clarification or block affected mutation, not default to permission. Test direct executor calls against the retained restrictions as well as planner paths.

### P2 — duplicate reservations are allowed before submission

`licet/safety/policy.py:398`: a second begin of the same fingerprint in NOT_STARTED creates a second mutation ID and overwrites by_fingerprint. The replay reserves the same operation twice successfully. The synchronous executor normally marks submitted immediately, so this is a ledger API/concurrency defect rather than a demonstrated duplicate in the current serial path.

Required fix (Luna): make reservation an atomic compare-and-set; every existing fingerprint, including NOT_STARTED, prevents a second reservation. Enforce state transitions and single submission per reservation. Bound retries to an explicit reconciliation result, never to a timeout alone. Keep the mutation limit tied to the run across executor instances.

## State-change and wiring review

The adapter caches environment at construction (`licet/phase4/accela_portal.py:188`) and only blocks cached LIVE at submission (line 353). The central engine also holds a configured environment. Navigation/redirect can invalidate that evidence. Refresh the actual browser origin and record at the final commit boundary, and intersect configured permissions with observed environment; configuration must not override an observed live origin. Unknown must not pass the direct adapter route. GLM should supply the precise portal-specific commit controls and ensure wizard navigation/availability checks do not themselves mutate.

`GoalPlanner` only optionally receives central policy and only consults it for mutations. Supplying it on a normal scheduling path can stop early because the planner's observed identity is built solely from verified_inspection, which often does not exist before scheduling. That optional planner check is not a replacement for the mandatory authoritative gate near execution. Align the semantic vocabulary (Phase 5 includes READ_CONDITIONS, DETERMINE_BLOCKERS, DETERMINE_NEXT_INSPECTION and different availability/approval names) so every semantic action gets an explicit deterministic decision without rejecting legitimate reads.

The public `PolicyEngine.decide(..., verify_record=False)` also skips required-input checks, not merely identity checks. No current executor caller uses it, but it should not be available to an execution authorization path. Use a separate non-authorizing preview API if UI needs a preliminary decision.

After any mutation: independently verify, invalidate prior reasoning/eligibility/availability, refresh affected sections, then replan. A changed navigation generation, target, cost, date, required field, or environment invalidates pending grants. Keep this enforcement in the trusted execution context, not only in planner memory. Run-local ledgers still do not survive process crashes; durable recovery remains an explicit residual.

## Acceptance required before sign-off

1. Replay this file's counterexamples with every safety invariant passing and preserve live-read/sandbox positive controls.
2. Add recording-client dispatcher tests proving denied calls never reach browser I/O, including malicious intent labels and navigation after approval.
3. Verify copied/stale approvals, independent original constraints, and wrong-record/wrong-inspection post-submit observations through the real executor.
4. Run the full suite and Phase 2–6 integration in explicit sandbox and live-read-only modes; report simulated and live evidence separately.
5. Correct the architecture documentation and safety metrics to cover primitive routes and failed authorization invariants. Zero violations in the existing replay are coverage-limited evidence, not a global safety proof.

No browser, model API, credential, or live portal was used in this review. Implementation fixes belong to Luna; portal commit mapping belongs to GLM. This review's scope is complete, but Phase 6 safety sign-off remains blocked by the findings above.

## Validation results

**At review time**, on the reviewed working tree, `.venv/bin/python -m pytest -q` completed with **1023 passed in 23.29s**. `git diff --check` passed. The separate architecture replay exited **1**, with **12 failed safety invariants and two passing controls**. A green existing suite therefore does not resolve these findings. The replay should return zero after the owner fixes the defects; its checks are not marked xfail or counted as passing safety tests.

**After the response**, `python -m pytest -q` completes with **1070 passed** and the replay exits **0** with 14 passing checks and no failures; see `docs/phase6/astra_review_response.md` for the per-finding disposition and the residuals that remain.
