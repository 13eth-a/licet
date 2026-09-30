# Phase 9 — adversarial review (adversarial review)

Role: final red team. This pass inspects the Phase 9 submission as if it intends the
flagship demo to fail during judging. It reads the implementation, the checked-in
benchmark artifacts, the CLI runner, and the README/`docs/phase9.md` claims, and
reports every claim that is stronger — or different — than what the code and the
artifacts actually prove.

Method: every number below is re-derived from the checked-in artifact, not copied
from a document. Claims are marked **holds up**, **overstated**, or **unverifiable
here** (needs live credentials).

---

## 1. Claims that hold up

Re-derived from artifacts, not from prose:

- `docs/phase8/final/core.json`: `tasks=50`, `runs=250`, `completed=145`
  (`task_completion_rate=0.58`), `expected_behavior=250`, `unsafe_failures=0`,
  `duplicate_mutations=0`, `false_verified_successes=0`,
  `mutation_submissions=40` / `verified_mutations=40`, `wrong_record_actions=0`.
  The README table and the `docs/phase9.md` per-category table (50/50/50/40/30/30
  runs; 25/50/25/15/0/30 completions) reproduce the artifact's `by_category`
  exactly.
- `docs/phase8/report.md` headline scope ("no model called, no portal contacted",
  `--repeat` = repeatability not reliability) is consistent with
  `average_model_calls=0.0` and `approximate_cost_per_run=null` in the artifact.
- `docs/phase7/acceptance_evidence.json`: `runs=26`, `completed=18`,
  `stopped_safely=8`, `recovery_success_rate=0.889` — matches `docs/phase9.md`.
  (Superseded after this review: the artifact was regenerated on 2026-09-26
  because commit `84721f6` had added a 13th acceptance case without re-running
  the harness, so the cohort was 24 runs / 16 completions / 0.875 at the time of
  this review. See [`whole_repo_audit.md`](whole_repo_audit.md) F1.)
- `.env` is **not** tracked (`git ls-files` lists only `.env.example`); `.gitignore`
  excludes `.env` and `logs/`.
- `git grep` over `*.py`/`*.md`/`*.json` finds no hardcoded local paths
  (`/Users/…`, `C:\Users\…`) or credential-shaped literals (`sk-…`, `slr_live_…`).
- `pytest --collect-only` → **1367 tests collected**, matching `docs/phase9.md`.
- `core.json` records `commit_dirty=false` on commit `84721f6`, i.e. the frozen
  benchmark was produced from a clean revision.

## 2. Findings

### F1 — HIGH / demo-blocking: the flagship cannot demonstrate the specified outcome

`docs/phase9.md` and the README are honest that the current Phase 5 live path stops
at `READ_INSPECTIONS`/availability and that **no verified live booking exists**.
That honesty is correct, but it means the submission's central claim — *"safely
execute permitted actions … and prove whether it succeeded by verifying the
resulting portal state"* — is backed at runtime only up to discovery and state
reading. Every mutation/verification number in the repo (40/40) is a **fixture**
count, not a live one.

Fix: either produce one fresh live run through availability with credentials, or
keep the flagship as discovery + state + policy + availability gate and label the
unshown segment explicitly. Do not present a fixture trace as the live run.

### F2 — HIGH / documentation accuracy: README mislabels the benchmark cohorts

> **Historical record.** The README row and artifact figures quoted in this
> finding are the ones observed on 2026-09-25 and are preserved as found. The
> cohort split F2 identified is still correct; the Phase 7 acceptance numbers
> have since been regenerated to 26 runs / 18 completions / 0.889 — see
> [`whole_repo_audit.md`](whole_repo_audit.md) F1.

README row:

```text
| Seeded noisy integration | 16/24 completed; 8 safe stops | Simulated external I/O; no live reliability claim |
```

Artifacts say otherwise:

- `normal-vs-noisy.json`: normal `20/20` completed; noisy `15` completed + `5` safe
  stops over **20** runs (`recovery_success_rate = 0.933`, i.e. 14/15).
- `acceptance_evidence.json`: `16` completed + `8` safe stops over **24** runs
  (Phase 7 acceptance), `recovery_success_rate = 0.875`. *(These are the
  figures as of this review; the cohort has since been regenerated to 26 runs /
  18 completions / 0.889 — see [`whole_repo_audit.md`](whole_repo_audit.md) F1.)*

So the README attributes the Phase 7 acceptance cohort to the "noisy integration"
suite and omits the actual noisy-integration result. `docs/phase9.md` states the
correct figures; the README does not.

Fix: split into the real cohorts (normal 20/20; noisy 15 + 5 of 20; Phase 7
acceptance 16 + 8 of 24). Fixed in this pass.

### F3 — MEDIUM / test stability: the quality gate is flaky

`docs/phase9.md` states "1,367 pytest tests pass". Two full-suite runs from this
clean tree produced different results:

```text
run 1: 1 failed, 1366 passed  (tests/test_solari_client.py::test_select_reports_auto_postback)
run 2: 1367 passed
```

The failing test passes in isolation and within its own file; it fails only under
full-suite load. Cause: the helper builds `SolariClient(..., verification_timeout_ms=20,
verification_poll_ms=1)`, and `_verify_action` returns `verified` only after **two
consecutive agreeing samples** inside a 20 ms deadline. Under load the deadline
expires first and the action returns `ACTION_OUTCOME_UNKNOWN`, failing the test.

Fix: widen the test's verification budget (e.g. 200 ms) or make it load-tolerant;
state the residual flakiness in `docs/phase9.md` rather than claiming a clean pass.

### F4 — MEDIUM / UI overstates verification

`scripts/ni_agent_run.py` prints, for any `goal_completed` run with a permit:

```text
│ Verified by re-reading the portal at <record page>.
```

But the planner sets `GOAL_COMPLETED` purely from the model returning text and no
tool call (`licet/agent/planner.py`). Whether that declaration is true — and
whether any mutation was independently re-read — is the **scorer's** job, not the
loop's. A read-only run that merely stops talking therefore renders a
"Verified by re-reading the portal" line it did not earn. This is the same class of
regression the Phase 9 brief warns about (`verified_success` quietly becoming
`button_clicked`).

Fix: only claim verification when a mutation was submitted and its resulting state
was independently observed; otherwise label the card "model declared the goal
complete", and leave the verified/unverified distinction to the scorer.

### F5 — LOW / cross-module vocabulary: the policy card shows a different risk taxonomy

`docs/phase9.md` advertises a policy card of `Risk: Reversible / Consequential`.
`_print_policy_card` instead renders `licet.safety.risk_levels.classify(action).value`
→ `automatic` / `confirmation_required` / `prohibited`, while the deterministic
`PolicyEngine`/`ActionRisk` uses `READ_ONLY` / `REVERSIBLE` / `CONSEQUENTIAL` /
`PROHIBITED`. A held `PAY_FEE`/`submit_payment` therefore displays
"Risk: confirmation_required", not "Consequential", and the trace's own
`authorization["risk"]` is ignored.

Not a safety bug (both layer reads agree the action is held), but the UI's
vocabulary disagrees with the policy layer it is describing.

Fix: display the policy vocabulary, or read `authorization["risk"]` from the trace
entry already in hand.

### F6 — LOW / stale claim outside Phase 9

`docs/phase8/report.md` argues a per-commit curve is impossible because the repo
carries "the whole Phase 1–8 tree uncommitted (HEAD names a revision that does not
contain the code)". The tree is now committed (`2a9c301` … `4ae0566`) and
`core.json` records `commit=84721f6`, `commit_dirty=false`. The paragraph is stale.

## 3. Five most likely ways the flagship fails from a clean state

1. **Credentials/session.** Missing or expired `OPENAI_API_KEY` / `SOLARI_API_KEY` /
   `ACCELA_TEST_*`, or a dead Solari session → login fails → `PORTAL UNAVAILABLE`
   before any permit is read.
2. **Host guard.** `ACCELA_SANDBOX_URL` changed (or a non-test host configured) →
   the runner refuses; using `--allow-non-sandbox` on camera looks broken even
   though policy still blocks the mutation.
3. **Unavailable `Inspections` section.** The Phase 5 path can stop at
   `READ_INSPECTIONS`, so there is no blocker to narrate and no path to
   availability.
4. **Empty calendar.** Even if availability is reached, the sandbox has no
   selectable dates → no scheduling/verification to show; must be narrated as a
   safe stop (this is the currently documented reality).
5. **Model nondeterminism / step budget.** A live model can cycle record sections
   into the convergence nudge or `MAX_STEPS_EXCEEDED`, producing a weaker trace
   than the scripted demo expects. The 20 ms flake (F3) can also surface as a
   spurious `PORTAL_UNAVAILABLE`.

## 4. Safety / state-verification regression audit

Read the boundary code directly; no polish change weakened it:

- Unknown environment ⇒ mutation denied (`UNKNOWN_ENVIRONMENT`).
- Non-sandbox ⇒ mutation denied (`LIVE_MUTATION_BLOCKED`) **independently of any
  approval**; `PROHIBITED` tier cannot be confirmed.
- Targeted mutations (`CANCEL`/`RESCHEDULE`) require `inspection_id`.
- Permit/inspection identity is re-verified against a fresh observation before
  mutation; an observation silent about an asserted field is `unverified`, not a
  pass.
- Confirmations are consumed at the policy boundary and by id; the ledger quarantines
  `UNKNOWN_RESULT` and refuses replay/duplicate.
- The one state-verification caveat is F4 (a UI label, not a policy hole).

## 5. Resolution (same pass)

- **F2** — fixed: README now lists the normal (20/20), noisy (15 + 5 of 20) and
  Phase 7 acceptance cohorts separately. The split is correct; the Phase 7
  figures it quoted (16 + 8 of 24) were themselves stale and now read
  18 + 8 of 26.
- **F3** — fixed: the shared Solari test helper's verification budget is now
  200 ms (still far under the deadline tests' 0.5 s guards), and an exhausted
  scripted `value_sequence` is sticky instead of silently returning the written
  value (which had let a longer budget flip two mismatch fixtures to "verified").
  Two consecutive full-suite runs now pass 1367/1367.
- **F4** — fixed: `_verified_mutation` gates the result card's verification line
  on a successful state-changing action with a passing post-action `verification`;
  a model-declared completion with no mutation verified now reads "Licet declared
  the goal complete", not "Verified by re-reading the portal".
- **F5** — fixed: the policy card normalizes the action through the policy alias
  table and prints the policy vocabulary (`Reversible` / `Consequential`)
  instead of the dispatcher's `confirmation_required` tier.
- **F6** — fixed: the stale "tree is uncommitted" paragraph in
  `docs/phase8/report.md` now states that the tree is committed and the artifacts
  carry their revision and a clean tree, while keeping the honest point that a
  curve needs a measurement per clean revision.

Still open: **F1** (needs a live flagship run).

## 6. Unverifiable in this environment

- Any live claim (availability, booking, payment boundary, recovery route): needs
  the sandbox account. Treated as unverifiable, not as failed.
- Dependency pinning and fresh-clone install: no lockfile exists and no fresh clone
  was tested in this pass (matches the `docs/phase9.md` checklist row).
