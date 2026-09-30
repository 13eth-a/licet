# Phase 7 adversarial recovery review — DeepSeek V4.1 Flash

> Implementation follow-up: see [Phase 7 completion](completion.md) for the runtime fixes and current acceptance evidence. The findings below describe the earlier review snapshot.


Reviewed 2026-09-23 against the working tree after Luna's recovery controller.
Scope, per the Phase 7 assignment: assume every underlying component — the
browser, the model, the planner, and any future caller — can fail intermittently
or be buggy, and find every route by which the recovery system could

- **falsely claim success** it did not achieve,
- **repeat a mutation** after an uncertain outcome,
- **corrupt state** by trusting a stale checkpoint or a wrong page,
- **loop**, including by hopping between different recovery paths, or
- **spend past its stated budgets**.

Method: read `licet/phase7/recovery.py` and its wiring into
`licet/phase5/planner.py`, then drive the **real** `RecoveryController`,
`classify_failure`, `LoopDetector`, `ProgressTracker` and `PageFingerprint` with
adversarial inputs. Every finding below was reproduced against the pre-review
tree before it was fixed; each fix is locked by regressions in
`tests/test_phase7_adversarial.py` (22 cases) and every counterexample is
replayable with `scripts/phase7_adversarial_replay.py`
(`docs/phase7/adversarial_evidence.json`).

**Read the metrics as measured over these counterexamples, not as a global
proof.** The replay is finite, local and deterministic; a new failure shape, a
new caller or a new portal state can still be uncovered, which is why the
invariants are pinned as executable tests rather than as a claim in a document.

The Phase 7 principle this review is measured against:

> **Recovery must never be more dangerous than the original failure.**

A failed click is annoying. A "recovery" that schedules the same real inspection
twice is much worse.

## Headline

**The controller's single most important guarantee — "never retry a mutation" —
was not structural.** It rested entirely on a caller-supplied `mutation: bool`.
String classification ran *first*, and a navigation- or search-shaped message
from a submission (e.g. `"navigation timeout while submitting inspection"`)
classified as `NAVIGATION` and came back `recoverable=True`. Any caller that
forgot the flag — and the flag is a default argument, so forgetting is a silent
no-op — was handed a recoverable failure for a mutation, and `recover()` would
run whatever recovery action it was given. Four smaller instances of the same
shape (success without work, a budget that could be renamed away, a checkpoint
with no page identity, and a `True`-on-empty comparison) compounded it.

Two of the fixes are *houses built on sand* checks: they make a forgotten
argument or absent evidence **fail safe** rather than pass.

## R1 — a recovery with no action reported success (false recovery)

| ID | Route (measured on the pre-review tree) | Why it is unsafe | Resolution |
|---|---|---|---|
| R1 | `recover(failure, "re-observe and retry")` with `action=None` → `value = action() if action else True` → `RecoveryResult(recovered=True, ...)` and `recovery_successes += 1`. | The controller is the single place that decides recovery happened. Its default path reported a healthy recovery having performed nothing, which is precisely the "false recovery" the checklist sets to zero. A caller that forgot to pass an action got a green light and moved on against unchanged state. | A recoverable failure with no action returns `NO_RECOVERY_ACTION`, `recovered=False`, and increments `false_recovery_blocked` (a new metric). Terminal failures still return `STOP` (they are unrecoverable, not un-attempted). |

## R2 — the global action budget was not a ceiling

| ID | Route (measured on the pre-review tree) | Why it is unsafe | Resolution |
|---|---|---|---|
| R2 | `MAX_RECOVERY_ACTIONS = 8` was checked once at the top of `recover()`. The inner loop then incremented `recovery_actions` up to `max_attempts` more times without re-checking. | The checklist's explicit budget was an *advisory* bound: a sequence entered while `recovery_actions = 7` could run its full `max_attempts` (up to 8) and leave the controller at 15. Cascading recovery ("recovery for recovery") was bounded only by luck of alignment. | The loop's condition includes `self.stats.recovery_actions < max_recovery_actions`, and each sequence is capped by `min(max_attempts, remaining)`. The counter can never exceed the ceiling; `budget_overshoots` is recomputed as zero by the replay. |

## R3 — renaming the strategy minted a fresh retry budget

| ID | Route (measured on the pre-review tree) | Why it is unsafe | Resolution |
|---|---|---|---|
| R3 | The per-sequence allowance was keyed on `f"{type}:{operation}:{strategy}"`. Two attempts of `"re-observe"` exhausted the budget, but `"go back"` and `"reload section"` each started from zero. | The checklist warns specifically that "a retry limit existing doesn't automatically mean your system can't loop through **different** recovery paths forever". Keying on the label the caller chooses is exactly the evasion it names. | The allowance is keyed on `(failure_type, operation)` only, so all recovery paths for one failing operation share one budget. Locked by `test_renaming_the_strategy_cannot_mint_a_fresh_retry_budget` (4 strategies → 2 sequences) and R3 in the replay (legacy 4 vs current 2). |

## R4 — the search-reformulation budget was per-query

| ID | Route (measured on the pre-review tree) | Why it is unsafe | Resolution |
|---|---|---|---|
| R4 | `allow_search_reformulation` counted `search:{query}`. Reformulation *is* changing the query, so every reformulation got its own `MAX_SEARCH_REFORMULATIONS` allowance. | "Don't broaden indefinitely" was unenforceable: three attempts per query is unlimited attempts overall, and the checklist's search-recovery path (`123 Main St + ZIP` → drop ZIP → normalize St) is exactly a sequence of distinct queries. | The budget is spent per run (`stats.search_reformulations`, reset in `begin_run`); the query is recorded for the trace only. Locked by a varying-query test and R4 in the replay (legacy 5 vs current 2). |

## R5 — the never-retry-a-mutation rule depended on a boolean

| ID | Route (measured on the pre-review tree) | Why it is unsafe | Resolution |
|---|---|---|---|
| R5 | `classify_failure(TimeoutError("navigation timeout while submitting inspection"), operation="SCHEDULE_INSPECTION")` (flag omitted, the default) → `FailureType.NAVIGATION`, `recoverable=True`. | `recover()` runs its `action` for any recoverable failure. A caller that did not pass `mutation=True` — including one classifying a submission with a navigation- or search-shaped message — could be given a resubmit as the recovery action. The module's docstring promised mutations are never retried; the code made that a convention. | Two changes, both fail-safe. (1) When `mutation=True`, the taxonomy **must** say `MUTATION` even if the message looks like navigation. (2) A conservative mutation signal — an explicit flag, a mutation verb in the operation name, or the word "mutation" in the error — marks the failure `recoverable=False`, so a forgotten flag stops (`STOP`) instead of retrying. Locked by `test_a_forgotten_mutation_flag_still_fails_safe` and R5 in the replay (legacy recoverable=True). |
| R5b | `Failure(FailureType.MUTATION, ...)` constructed directly, or any failure whose `mutation` flag is set, was already reconciled — that path held. | — | Unchanged and re-pinned: `recover()` reconciles rather than retries, and the action is never called (`test_a_declared_mutation_timeout_is_reconciled_never_retried`). |

Verb detection is deliberately narrow (it reads the *operation* names — the
planner's `Action` values, none of which contain a mutation verb for a read —
plus the word "mutation" in the message). It cannot make a legitimate read
unrecoverable, and if it ever did, the direction is safe (stop, not retry).

## R6 — a checkpoint was reusable with no evidence

| ID | Route (measured on the pre-review tree) | Why it is unsafe | Resolution |
|---|---|---|---|
| R6a | `validate_checkpoint(name)` with no fingerprint → `valid=True` (`fingerprint is None` was a pass). | The checklist requires "validate checkpoint state before reuse… re-read relevant state first". A caller that did not re-read got a pass, so a checkpoint could be reused after arbitrary portal drift. | A fingerprint is required; absence is a failure, and an invalid validation marks the checkpoint unusable. Locked by `test_checkpoint_validation_requires_the_current_page_to_be_supplied`. |
| R6b | `checkpoint(name, state)` stored with no fingerprint, then validated with *any* fresh fingerprint → `point.fingerprint is None` was a pass. | A checkpoint that recorded no page identity could never be checked against the page it would be reused on. | A checkpoint whose stored fingerprint carries no identity (`url` and `record_number` both absent) is never reusable. Locked by `test_a_checkpoint_without_a_fingerprint_is_never_reusable` and `test_a_checkpoint_with_anonymous_identity_is_never_reusable`. |
| R6c | Two fingerprints that both failed to establish identity (`url=None`, `record_number=None`) compared equal field-for-field → `matches` `True`. | "I thought I was on Inspections, but I'm actually back on Search" is undetectable if neither observation could say which page it was; the vacuous match reads missing evidence as sameness. | `matches(..., require_identity=True)` refuses a positive match when either side has no URL *and* no record number, and `validate_checkpoint` always uses it. Plain `matches()` keeps its field-equality semantics for callers that want them; the identity-bearing comparison is what guards a checkpoint. Locked by `test_two_anonymous_fingerprints_are_not_the_same_page` and R7 in the replay. |

## R7 — a mutation reservation could never be released (and a metric gap)

| ID | Route (measured on the pre-review tree) | Why it is unsafe | Resolution |
|---|---|---|---|
| R7a | `mutation_started(key)` added the key to a set that `begin_run` cleared and nothing else removed. After a submit→timeout→re-read that proved the mutation **absent** (checklist scenario 10, "safe retry if allowed"), the same action could never be re-reserved. | The module could not express the checklist's own scenario 10. It failed *safe* (over-blocking rather than duplicating), but it made a legitimate, reconciled retry impossible and left "safe retry if allowed" unimplementable. | `mutation_reconciled(key, occurred=…)`: `occurred=True` keeps the key reserved and forbids replay (scenario 9); `occurred=False` releases it so a **bounded retry with a fresh reservation** is possible (scenario 10). An unknown key is never a retry licence. Locked by the two scenario tests and both replay end-to-end rows. |
| R7b | `stats.recovery_attempts` was incremented for **every** `recover()` call, including terminal `STOP`s and mutation reconciliations that attempted nothing. | `recovery_success_rate = successes / attempts` was silently deflated by failures the system correctly refused to attempt, so a healthy controller could look bad and a bad one could look fine. | `recovery_attempts` counts only sequences that actually run; terminal stops are counted in `unrecoverable_failures`. Locked by `test_terminal_stops_do_not_inflate_the_recovery_attempt_rate`. |

## Verified-safe paths (attacked, no change needed)

- **Mutation reconciliation.** A declared `MUTATION` failure, or one whose
  `mutation` flag is set, returns `RECONCILE_MUTATION_STATE` and the supplied
  action is provably never called (asserted with a recording action).
- **Terminal classification wins over recovery.** `POLICY`, `MUTATION`, session
  and `auth_required` failures, and every `_TERMINAL_PHRASES` message, return
  `STOP` regardless of how many times `recover()` is called.
- **Session death does not click through.** `SESSION_TIMEOUT` classifies
  `PORTAL`/unrecoverable; the end-to-end row shows `STOP` with the action never
  invoked — the checklist's "don't repeatedly click through" and "return
  `AUTH_REQUIRED`".
- **Loop detection.** A repeated `(action, page, permit)` tuple is detected on
  its third occurrence; a changed page or permit correctly resets the tuple.
- **Progress scoring.** New information, a changed state and goal progress rank
  above `NO_PROGRESS`; two consecutive `NO_PROGRESS` now emits a
  `REPLAN_REQUIRED` signal and exposes `replan_required()`.
- **Page identity.** Wrong record and wrong section are both detected by
  `PageFingerprint.matches`.

## Deliberately not changed (residual risk, named)

- **The controller is not wired into the running planner for retries (owner:
  Luna).** `licet/phase5/planner.py` calls `classify`, `loop_observed`,
  `progress_update` and `begin_run`, but it never calls `recover()`,
  `allow_replan()` or `mutation_reconciled()`. So the budgets and strategies this
  review hardened are exercised by tests and by the replay, not yet by the live
  loop: an action that fails today is classified and either stops or is skipped,
  it is not retried through this controller. Threading `recover()` into the
  planner's failure branch — as safe reads only — is the Phase 7 integration
  step. Until then, the strongest guarantees here are latent.
- **`replan_required()` / `max_no_progress` is not consumed (owner: Luna).** The
  trigger now exists and is traced, but the planner's no-progress stop uses its
  own `max_failures` counter. The checklist's "2 consecutive NO_PROGRESS → force
  replan" becomes real only when the planner calls `replan_required()` and
  `allow_replan()`.
- **An oscillating state evades the no-progress counter (named, mitigated).**
  `A→B→A→B` is always `STATE_CHANGED`, so `consecutive_no_progress` never
  reaches the budget and the forced-replan trigger never fires for a two-state
  cycle. The loop detector is the mitigation, but it fires only on the *third*
  exact repeat, so a cycle is caught late
  (`test_an_oscillating_state_evades_the_no_progress_counter`).
- **A transient page-state string defeats loop detection (owner: Luna/GLM).**
  The loop key is only as stable as the page state the caller supplies; a render
  timestamp or spinner label makes every occurrence unique
  (`test_a_transient_page_state_string_defeats_loop_detection`). The controller
  cannot normalise what it is not told. This is a caller contract: pass the
  settled page identity, not a live render token.
- **Mutation reservations remain run-local (owner: Luna).** `_mutation_keys` is
  cleared by `begin_run`; a process that dies mid-mutation is protected only by
  the portal's own read gate on the next run (the same cross-process residual
  Phase 6 recorded).
- **`classify_failure` remains heuristic.** Broad substrings (`"loading"`,
  `"section"`, `"not found"`) can misroute a specific error. Every misroute this
  review found that mattered was in the *safe-to-recover* direction, which is
  why the mutation signals were made fail-closed rather than the whole classifier
  rewritten. A structured error code from the tool layer would remove the guess.

## Evidence

```text
counterexamples (legacy = pre-review behaviour reproduced in the script)
  R1  legacy=recovered=True successes=1            current=NO_RECOVERY_ACTION recovered=False
  R2  legacy=actions spent=4 against ceiling 3     current=actions spent=3 against ceiling 3
  R3  legacy=sequences allowed=4                   current=sequences allowed=2
  R4  legacy=reformulations allowed=5              current=reformulations allowed=2
  R5  legacy=navigation recoverable=True           current=navigation recoverable=False
  R6  legacy=valid=True                            current=valid=False
  R7  legacy=matches=True (vacuous)                current=matches(require_identity)=False

end to end (checklist recovery scenarios through the real controller)
  scenario 9  (timeout, state changed)    retry_allowed=False duplicate_blocked=True   unsafe=False
  scenario 10 (timeout, state unchanged)  retry_allowed=True  retry_reserved=True     unsafe=False
  scenario 1  (failed click)              recovered=True attempts=1                    unsafe=False
  scenario 3  (session expired)           STOP action_ran=False                        unsafe=False
  mutation timeout                        RECONCILE_MUTATION_STATE action_ran=False    unsafe=False
  scenario 8  (loop)                      detected_on=3                                unsafe=False
  false recovery                          NO_RECOVERY_ACTION recovered=False           unsafe=False

Phase 7 recovery metrics (unsafe targets zero)
  unsafe_recoveries                  0
  duplicate_mutations                0
  mutation_retries_after_timeout     0
  budget_overshoots                  0
  strategy_hopping_escapes           0
  stale_checkpoint_accepts           0
  false_recoveries_claimed           0
```

- `tests/test_phase7_adversarial.py` — **22 regressions**: false recovery
  without an action, validation-gated success, declared and forgotten-flag
  mutation timeouts, checklist scenarios 9 and 10, reconciliation of an
  unreserved key, the hard global action ceiling, strategy-hopping,
  per-run search reformulation, cascading recovery, anonymous fingerprints,
  fingerprint-less and identity-less checkpoints, checkpoint evidence on both
  sides, forced-replan signalling, the oscillation and transient-state
  residuals, the full timeout taxonomy, and recovery-attempt metric semantics.
- `scripts/phase7_adversarial_replay.py` re-derives every counterexample, the
  seven end-to-end rows and the recovery metrics; `--json
  docs/phase7/adversarial_evidence.json` records the result. Nothing here touches
  a browser, a model or a credential, and no live portal was contacted.

## Handoff

- **Luna**: wire `recover()` (safe reads only) into the planner's failure branch,
  have the planner consult `replan_required()` before `allow_replan()`, and call
  `mutation_reconciled()` from the Phase 4/5 reconciliation so scenario 10's
  bounded retry is reachable end to end. Decide the cross-process mutation
  reservation.
- **GLM**: supply a *settled* page identity for `loop_observed` — the Accela
  sections settle asynchronously (empty table, then rows), so the caller must
  pass the loaded state, not the in-flight one. Audit which Accela states
  produce a transient `active_section`/URL.
- **Astra**: decide whether the oscillation evasion (A→B cycles) should count as
  progress at all for the semantic loop, and confirm the residual heuristic in
  `classify_failure` is acceptable given the fail-closed mutation signals.

## Files touched

| File | Change |
|---|---|
| `licet/phase7/recovery.py` | `recover()`: mutation reconciliation first, hard global action ceiling, per-`(type, operation)` sequence budget, `NO_RECOVERY_ACTION` instead of a no-op success. `classify_failure`: `mutation=True` forces `MUTATION`; conservative mutation signals make a forgotten flag unrecoverable; `ELEMENT_TIMEOUT` reachable. `validate_checkpoint`: requires a fingerprint and a stored identity. `PageFingerprint.matches(require_identity=…)`. `allow_search_reformulation`: per-run budget. `progress_update`/`replan_required`: forced-replan signal. `mutation_reconciled`: scenario 10 release. `recovery_attempts` counts sequences only; `false_recovery_blocked` and `mutation_reconciliations` stats added. |
| `tests/test_phase7_adversarial.py` | 22 regressions. |
| `scripts/phase7_adversarial_replay.py` | Counterexample + end-to-end + metrics replay, optional JSON evidence. |
| `docs/phase7/adversarial_evidence.json` | Replay output. |
