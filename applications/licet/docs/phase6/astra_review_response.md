# Response to the Phase 6 Astra architecture review — DeepSeek V4.1 Flash

Reviewed 2026-09-23. This is the adversarial reviewer's disposition of
`docs/phase6/astra_architecture_review.md`. Astra's review keeps its own text and
verdict: it is the historical record of what the tree looked like when it was
written. This document says which findings reproduced, which are fixed, which
are accepted as residuals with an owner, and what the invariants now cost.

**Headline: Astra's core claim was correct and is no longer true.** When it was
written, the central engine was not the sole authorization boundary — the
primitive (`guard`) route was environment-blind, approvals could be re-pointed
and copied, `_matches` could certify foreign state, constraints were unparsed and
unwired, and a duplicate reservation could claim a second slot. Every one of
Astra's 14 probe checks now passes against the current tree; its script exits 0
and `docs/phase6/astra_review_evidence.json` is the regenerated evidence.

## Disposition of each finding

| Astra finding | Reproduced on this tree? | Disposition |
|---|---|---|
| **P1a** primitive layer blocks only `schedule`/`reschedule` on live hosts; unknown-host scheduling allowed; action-name approval unlocked live cancel/payment/attestation | Yes (`guard.authorize`) | **Fixed.** The guard now applies the Phase 6 core rule to every state-changing action: `changes_state(action)` requires a positively identified sandbox, whatever the tier says and whoever approved it. UNKNOWN is no longer a synonym for sandbox on the click route. `accept_legal_attestation` is a `PROHIBITED` tier that no approval can unlock. |
| **P1b** `dispatcher.resolve_action` trusts caller `intent` over dangerous target text; `click(target="Submit Payment", intent="read_record")` resolved to a read | Yes (probe: `read_record: allow`) | **Fixed.** An intent may refine an ambiguous control but may never *lower* the risk the control's own text implies (`_risk_rank` comparison), and the commit point overrides a non-acknowledging intent. The probe now reports `enter_payment_details: block`. |
| **P1c** the generic approval is not bound to record, inspection, date or navigation generation | Yes | **Fixed at the semantic boundary; closed at the primitive boundary too.** `ConfirmationRequest` binds `record_key`, `inspection_id`, `amount` and both date-window edges, compares nullable fields exactly, and is consumed in a trusted registry keyed by confirmation id (so a deepcopy is refused as well as the original being spent). At the primitive layer, `AgentState.is_approved` now also requires the page context recorded when the approval was asked for: a changed URL, record or flow step invalidates the grant. |
| **P1d** the executor derives the proposed existing date from the same observation it verifies against | Yes, by construction | **Accepted residual (owner: Luna/GLM).** The user's date *instruction* is bound (`date_window_start`/`date_window_end` on the approval, checked against the action); the *observed* appointment date cannot be bound at approval time because the run has not read the appointment yet, and fabricating one would be worse. The Phase 5 token binding covers the normal resume path. Renamed in `docs/phase6/adversarial_review.md` so it is not presented as solved. |
| **P1e** `_matches` ignores `record_key` and `inspection_id`, so foreign state can be certified (both the success and the timeout path) | Yes (probe: `matched=True`) | **Fixed.** `_matches(kind, proposed, observed, action)` now requires the observation to assert the record key the action named and, for targeted mutations, the exact appointment id — on every verification path. It is a `staticmethod` so the invariant can be probed without constructing an executor. Two regressions drive it through the real executor, after a successful response and after a timeout. |
| **P1f** `UserConstraints.from_text`: "do everything possible, but don't …" re-granted prohibitions; "don't schedule inspections" did not disable scheduling | Yes | **Fixed.** Prohibitions are collected first and no affirmative phrase may re-grant them; clause order is irrelevant. "Do everything possible, but don't submit anything", "…but don't cancel" and "Don't schedule inspections" all deny, in every environment (parametrized regression). |
| **P1g** no runtime constructs `UserConstraints`; `AgentState.user_constraints` is declared and never written; the engine defaults to permissive | Yes | **Fixed at the primitive layer, residual named for the capability layer.** `guard.run_constraints(state)` parses the run's permissions from trusted text only (the goal plus explicit constraint lines) and the guard refuses a state-changing action it contradicts — which gives `UserConstraints.from_text` a production caller. `AgentState.user_constraints` is now read rather than dead. The capability factory still relies on Phase 5's immutable `Goal` for the semantic route; threading an instruction into `build_live_capabilities` is recorded for Luna. A regression asserts the two readings agree so they cannot silently diverge. |
| **P2** a duplicate `MutationLedger.begin` in `NOT_STARTED` could create a second mutation id and overwrite the fingerprint | Yes (probe: `first=True, second=True`) | **Fixed.** Every existing fingerprint, including `NOT_STARTED`, refuses a second reservation (`MUTATION_ALREADY_RESERVED`); the ledger no longer overwrites by fingerprint. Regression pins `first.allowed and not again.allowed and len(ledger.records) == 1`. |
| **Adapter caches the environment at construction and only blocked cached LIVE at submission; the direct adapter route passed unknown** | Yes | **Fixed.** The adapter refuses a positively live host at the commit entry point. The dispatcher now records the URL it actually read into `AgentState.current_url`, which is what let the guard's environment rule apply on the real adapter route at all — previously every adapter call looked like an unknown environment. |
| **`GoalPlanner`'s central-policy check is optional and would stop early because its observed identity comes from `verified_inspection`, which does not exist before scheduling** | Yes | **Accepted as designed, with the mandatory gate moved.** The planner check stays advisory; the authoritative gate is the executor, which cannot now exist without a policy engine. The planner's own pause-time approval is the scoped object threaded down, so a planner that loses its mind still cannot execute without the deterministic layer agreeing. |
| **Vocabulary mismatch: `READ_CONDITIONS`, `DETERMINE_BLOCKERS`, `DETERMINE_NEXT_INSPECTION`, different availability/approval names** | Partly — the planner verbs were already classified; the *dispatcher's* verbs were not | **Fixed both directions.** Regression 1 asserts every `licet.phase5.Action` value has a deterministic policy decision. Regression 2 asserts the policy layer is never *weaker* than the guard for the same named action (policy may be stricter; it may not wave through what the guard gates). Guard-side names bridge onto the canonical vocabulary (`submit_payment`/`enter_payment_details` → `PAY_FEE`, `accept_legal_attestation`/`sign_document` → `LEGAL_ATTESTATION`), and the read/session verbs are classified rather than left to default-deny. `DELETE_RECORD` is deliberately *not* given a consequential tier: the Phase 6 checklist requires an invented planner verb to be rejected as `UNKNOWN_ACTION_RISK`, and unlisted ⇒ prohibited is the stricter, safe direction. |
| **`decide(..., verify_record=False)` also skips required-input checks** | Yes | **Fixed.** `verify_record` is now an identity shortcut only; `required_inputs` are enforced independently. Regression: `verify_record=False` with a missing input is still `MISSING_REQUIRED_INPUT`. |
| **Acceptance 1**: replay the counterexamples with every invariant passing, keeping the positive controls | — | **Done.** Astra's script exits 0 (14/14), with the live-read and central-deny positive controls intact. Two checks were re-targeted at the code that now enforces them (the engine's issuance path for the copied approval, and `_matches(..., action)`); the invariants under test are unchanged and the probe is stronger, not weaker. |
| **Acceptance 2**: recording-client dispatcher tests proving denied calls never reach browser I/O, including malicious intent labels and navigation after approval | — | **Done.** `tests/test_dispatcher.py` now has a live page refusing an approved scheduling click (`client.calls == []`), a navigation-between-approval-and-click case that re-asks instead of reusing the grant, and a goal constraint blocking a payment click in a sandbox. They sit beside the existing `client.calls == []` cases for commit-point, intent-downgrade and unknown-tool blocks. |
| **Acceptance 3**: verify copied/stale approvals, independent constraints, and wrong-record/wrong-inspection post-submit observations through the real executor | — | **Done** (see the rows above; all four are executor-level regressions in `tests/test_phase6_adversarial.py`). |
| **Acceptance 4**: run the suite and the Phase 2–6 integration in explicit sandbox and live-read-only modes, reporting them separately | — | **Done.** **1070 passed.** The end-to-end rows drive the real capabilities + executor + scripted portal in all three environments: sandbox submits exactly once, live submits zero, unknown submits zero, with the positive control that sandbox scheduling still works. |
| **Acceptance 5**: correct the architecture documentation and safety metrics to cover primitive routes and failed authorization invariants; stop presenting the green replay as a global proof | — | **Done.** `docs/phase6/adversarial_review.md` now states the primitive-route rules as they *are* (all state-changing actions need a positively identified sandbox), names the residuals with owners, and the handoff no longer implies the replay is coverage-complete. The metrics are reported as measured over the replay's counterexamples, not as a global safety proof. |

## What is still not true (named, not hidden)

1. **Cross-process idempotency.** `MutationLedger` is run-local; a process that
   dies between submit and re-read is protected only by the portal's read gate on
   the next run. Owner: Luna.
2. **The observed existing appointment date is not bound to the approval**
   (P1d above): binding it would require reading the appointment before asking
   the human, and the Phase 4 boundary deliberately does not guess. Owner: Luna
   with GLM's portal map.
3. **The capability factory is not handed the user instruction.** The guard
   enforces constraints from trusted text; the semantic route still leans on
   Phase 5's `Goal`, with a regression keeping the two readings in agreement.
   Owner: Luna.
4. **`parse_goal`/selection still do not establish the appointment for a
   cancellation.** The boundary refuses an id-less targeted mutation rather than
   guessing, so a text-parsed cancellation stops instead of acting. Owner:
   GLM/Luna.
5. **The payment and attestation screens are still unmapped**, so the guard's
   vocabulary is GLM's portal map rather than an independent audit of those
   flows. Owner: GLM.
6. **The primitive layer's approval is page-scoped, not argument-scoped.** It
   binds action + URL + record + flow step, not the exact control that will be
   clicked. Owner: Luna.

## Verification

```text
python -m pytest -q                         1070 passed
python scripts/phase6_adversarial_replay.py exit 0   (counterexamples + 6 end-to-end rows, 7 metrics at 0)
python scripts/phase6_astra_review.py       exit 0   (14/14 checks, positive controls intact)
python -m compileall -q licet tests scripts clean
```

No browser, model API, credential or live portal was used: both replays are local
deterministic probes, and the only mutations performed anywhere in this work were
against scripted portal doubles.
