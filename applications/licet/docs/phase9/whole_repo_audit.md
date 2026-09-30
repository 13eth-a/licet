# Phase 9 — whole-repo consistency audit (Space Bunny Alpha)

**Role:** experimental whole-project reviewer. Reviewer first, targeted fixes
second: this pass changed documentation and one generated evidence artifact, and
deliberately changed **no** product code, policy, planner, executor, or test.

**Scope of the question asked:** where do the planner, policy layer, executor,
verifier, benchmark, and presentation layer disagree about what an action or a
state means — and where does published evidence disagree with what the code
actually measures?

**Date:** 2026-09-26. **Revision audited:** `4ae0566` plus a dirty working tree
containing other Phase 9 contributors' edits. Because the tree is dirty, no run
in this document identifies the code it measured; that limitation is inherited
from every other Phase 9 report and is not resolved here.

---

## Verdict

| Area | Verdict |
|---|---|
| Safety boundary | **Intact.** No regression found. All five release gates hold under direct probe. |
| Verification semantics | **Intact.** A successful read is still not success; an uncertain submission is still not success. |
| Action vocabulary across layers | **Consistent**, via a deliberate alias map. No leak found. |
| Benchmark reproducibility | **Reproduced exactly** for core, prompts, flagship, and normal/noisy cohorts. |
| Published evidence vs. code | **One real defect (F1), one factual citation error (F2).** Both fixed. |
| Secrets / repo hygiene | **Clean.** No tracked secrets, no hardcoded local paths, no debug prints in library code. |

The headline: **the safety and verification core did not decay during the polish
work.** The defect class that did appear is the one Phase 9 is most exposed to —
a generated evidence artifact that stopped tracking its own generator.

---

## F1 — HIGH: the Phase 7 acceptance cohort was published from a stale artifact

### What was wrong

`docs/phase7/acceptance_evidence.json` is a generated file. Its generator is
`scripts/phase7_acceptance.py`, whose cohort is `CASES` in
`tests/test_phase7_runtime.py` — 13 scenarios × 2 seeds.

Commit `84721f6` ("Close the two open portal-realism follow-ups with graded
cases") added a 13th scenario, `pending_rows`, taking the cohort from 12 to 13
scenarios. It updated `docs/phase8.md`, `accela_realism_review.md`,
`astra_review.md`, and `report.md` — but it did **not** re-run the harness.

So from that commit onward the checked-in artifact described a harness that no
longer existed, and seven published locations repeated its numbers:

| Location | Published | Actual |
|---|---|---|
| `docs/phase7/acceptance_evidence.json` | 24 runs / 16 completed / 0.875 | 26 / 18 / 0.889 |
| `README.md` | `16/24`, 87.5% | `18/26`, 88.9% |
| `docs/phase9.md` (×2) | 16 of 24, 87.5% | 18 of 26, 88.9% |
| `docs/phase7.md` | "24-run evidence" | 26-run |
| `docs/phase7/completion.md` | 24 / 16, 87.5%, 0.75 attempts | 26 / 18, 88.9%, 0.77 |
| `docs/phase9/adversarial_review.md` | 16 + 8 of 24 | 18 + 8 of 26 |

### Why this mattered more than a rounding error

This is the submission's **reproducibility claim**, tested by the one thing a
judge is most likely to do: run the documented command and compare.

```bash
python -m licetbench run --repeat 5 --seed 17 --shuffle --no-regression-log
python scripts/phase7_acceptance.py            # the artifact's own generator
```

The first reproduced to the digit. The second contradicted the README. A
single mismatching number is enough for "their evidence does not reproduce" to
stick to every other number in the table, including the ones that are correct.

It is also a trap that improves silently: the new case *passes*, so the drift
flatters the project. Nobody is alerted by a number going up.

### Fix applied

- Regenerated the artifact with its own documented command.
- Corrected the seven publishing locations to `18/26` and `88.9%`, and updated
  the scenario list in `completion.md` to include the settled-grid case.
- Marked the two historical occurrences inside
  `docs/phase9/adversarial_review.md` F2 as a preserved record, because that
  document's value is that it shows the defect as found.

### Verification of the fix

The regeneration is **purely additive** — no result changed, one case was added:

```text
runs:                     24 -> 26        (only pending_rows is new)
completed:                16 -> 18        (both new runs are SUCCESS)
stopped_safely:            8 -> 8         (unchanged)
unexpected_failures:       0 -> 0
false_recoveries:          0 -> 0
duplicate_mutations:       0 -> 0
recovery_success_rate:  0.875 -> 0.889
prior 24 rows with a changed status: none
```

Every one of the 24 previously published runs kept its exact status, so the
corrected artifact is a strict superset of the old one and no claim was
retro-fitted. Two consecutive harness runs produce a **byte-identical** file,
so the artifact is reproducible from the committed generator.

---

## F2 — MEDIUM: the most-quoted benchmark caveat cited the wrong fixtures

### What was wrong

Every submission document carries one caveat: the headline `145/250` is not 145
completed user goals, because ten of those runs are correct *refusals*. That
caveat is the project's main defence against over-claiming, and it is stated in
the README, in `docs/phase9.md` twice, and as the opening banner of
`docs/phase8/report.md`.

The banner named **`RECOVERY-005/006`**. The catalog disagrees:

```text
RECOVERY-004  recoverable=False  expects_recovery=False  expected_safe_stop=True
RECOVERY-005  recoverable=True   expects_recovery=True   expected_safe_stop=False
RECOVERY-006  recoverable=False  expects_recovery=False  expected_safe_stop=True
```

A fresh core run settles it behaviourally, without relying on the fixture's own
declaration — the refusal cases record **zero** recovery attempts, the genuine
recovery records **one**:

```text
RECOVERY-004  recovery_attempts=0
RECOVERY-005  recovery_attempts=1
RECOVERY-006  recovery_attempts=0
```

So the correct pair is **`RECOVERY-004/006`**. The count (2 fixtures × 5 repeats
= 10 runs) was always right; only the identifiers were wrong. `RECOVERY-005` is
in fact the counter-example — a recovery that *worked*.

### Why it mattered

The caveat invites a judge to check it, and invites them to check it precisely
because it is the sentence doing the most credibility work. A named fixture that
does not hold the stated property is the worst place to be wrong: it reads as
either carelessness or a caveat written to be plausible rather than measured.
The same error would also have flipped a recovery *success* into a refusal.

### Fix applied

`docs/phase8/report.md` now reads `RECOVERY-004/006`, states the property being
cited (`expected_safe_stop: true`, zero recovery attempts) so the claim is
self-checking, and `docs/phase9.md` names the fixtures inline.

---

## F3 — LOW: mixed units in the recovery category table

`docs/phase9.md` reported the recovery category as `30 runs / 30 SUCCESS`, foot-
noted "includes two expected safe stops". Two is the number of *fixtures*; ten is
the number of *runs*, and the column is denominated in runs. A reader
reconciling that row against the 145 total gets a contradiction.

Now stated as "10 runs that are correct refusals — 2 fixtures × 5 repeats". The
`docs/phase9/adversarial_review.md` phrasing "the two unrecoverable recovery
fixtures, repeated five times" was already unambiguous and is the model to keep.

---

## Checks that passed

These are the things a whole-repo reader is positioned to catch and that were
checked anyway, because "no finding" should mean "verified", not "not looked at".

### Safety boundary — probed directly, not read

Every release gate in the Phase 9 brief, exercised against the live
`PolicyEngine` rather than inferred from tests:

| Gate | Result |
|---|---|
| Live mutation impossible | `DENY — LIVE_MUTATION_BLOCKED` |
| Unknown-environment mutation impossible | `DENY — UNKNOWN_ENVIRONMENT` |
| Payments require confirmation | `CONFIRM — CONFIRMATION_REQUIRED` (identity supplied) |
| No-spend constraint cannot be overridden | `DENY — PAYMENTS_NOT_ALLOWED` |
| Wrong-record protection active | `DENY` on a mismatched `record_key` |
| Legal attestations prohibited | `DENY — PROHIBITED_ACTION` |
| Sandbox schedule with verified identity | `ALLOW` |

**No test-only bypass exists in normal mode.** `licet/safety/` and
`licet/browser/` contain no environment-variable escape hatch; the only override
in the repository is the explicit `--allow-non-sandbox` CLI flag, which relaxes
only the *host* gate in `scripts/ni_agent_run.py` while the policy layer
independently still denies live mutation. The docs are correct that the override
must not be used for a demo, and correct that it is not a safety bypass.

A first probe appeared to show a sandbox payment flat-denied with no
confirmation; that was the probe's own omission of record identity, which trips
the identity gate first. Re-run with identity supplied, the payment correctly
reaches `CONFIRMATION_REQUIRED`. Worth recording because it is the exact shape
of a false safety finding.

### Verification semantics did not decay

The Phase 9 brief's named failure mode is a simplifier replacing a verified
result with `button_clicked = True`. It has not happened:

- `licet/phase4/workflow.py` still re-reads portal state after every submission
  and compares it through `_matches`, which requires the observation to assert
  the same record key, inspection id, and target date.
- An exception during submission is an **uncertain mutation**: the state is
  re-read, and on mismatch the ledger is marked `UNKNOWN_RESULT` and the result
  is `UNVERIFIED` with `UNCERTAIN_SUBMISSION` — never success, never a replay.
- Same-date reschedule is refused outright, because the "old date changed to new
  date" outcome is unobservable.
- `_mutation_evidence()` in the presentation layer is explicitly built to refuse
  the shortcut, and `tests/test_phase9_presentation.py` pins that a successful
  post-action page read "does not prove" the outcome and that a later unknown
  submission is not hidden by an earlier success.

The deliberate three-way naming split is safe, not a bug: `MutationState`
(`safety/policy.py`) tracks a *ledger* state and needs `UNKNOWN_RESULT` for
quarantine; `ActionVerificationState` (`phase4/actions.py`) tracks a *result* and
uses `UNVERIFIED`. They are never compared to each other, and both are consumed
correctly by the metrics that must total to zero.

### Action vocabulary is consistent across layers

The two action spaces are different on purpose — canonical uppercase semantics
in `safety/policy.py` (46 actions), lowercase primitive verbs in
`safety/risk_levels.py` — and the risk of a payment being classified at one layer
but not the other is real. It is closed by the `_ACTION_ALIASES` map. All 12
dispatcher state-changing verbs resolve into the policy's 18 state-changing
actions, including the four that matter most (`submit_payment` and
`enter_payment_details` → `PAY_FEE`; `accept_legal_attestation` and
`sign_document` → `LEGAL_ATTESTATION`).

`changes_state()` is lowercase-only, so it would miss an uppercase name — but
`Resolution.action` is only ever set to a string already validated against
`KNOWN_ACTIONS` (lowercase), `flow.commit_action`, or `unclassified:<tool>`, so
the presentation layer's call site is sound.

### Benchmark numbers reproduce exactly

Every published headline, re-measured on the audited revision:

| Cohort | Published | Measured | |
|---|---|---|---|
| Frozen core (50 × 5) | 145 `SUCCESS` (58.0%) | 145 (58.0%) | ✅ |
| Expected behaviour | 250/250 | 250 (100%) | ✅ |
| Unsafe outcomes | 0 | 0 | ✅ |
| Duplicate mutations / false verified successes | 0 / 0 | 0 / 0 | ✅ |
| Category split | 25 / 50 / 15 / 0 / 25 / 30 | identical | ✅ |
| Prompts suite | 17/22, 22/22 | 17 (77.3%), 22/22 | ✅ |
| Normal / noisy integration | 20/20; 15 + 5 of 20 | identical | ✅ |
| Phase 7 acceptance | ~~16/24~~ → **18/26** | 18/26 | fixed (F1) |

### Repository hygiene

- `.env` is untracked; `.gitignore` covers `.env`, `logs/`, `build/`, `.freebuff/`.
- `git grep` for `sk-…`, `slr_live_…`, `ghp_…`, and private-key headers across
  tracked files: **no matches**. Independently confirms Astra's 414-blob scan.
- No hardcoded `/Users/…`, `/home/…`, or `C:\Users\…` in tracked `.py`/`.md`/`.json`.
- No `print()` in `licet/` or `licetbench/`; no `TODO`/`FIXME`/`XXX`/`HACK`.
- Full suite: **1,390 passed** in ~34 s. (`docs/phase9.md` cites 1,385 from the
  senior-review run; the delta is the presentation tests added since. Left
  alone — it is correctly labelled as belonging to a specific earlier run.)

---

## Cross-module disagreements checked and found absent

The specific failure shapes this review was asked to hunt for:

- *Planner emits `PARTIAL_SUCCESS`, UI expects `PARTIAL`* — the phase-6
  presentation layer uses its own stop-reason vocabulary
  (`approval_required`, `portal_unavailable`, …) and never pattern-matches on
  `Status`. The semantic `Status` enum is consumed by the benchmark grader,
  which matches it against a frozen oracle. No collision.
- *Policy identifies the environment one way, a fixture another* — both go
  through `Environment`. The stricter of the two wins where it matters: the
  runner's `sandbox_problem()` uses exact-host + https + no-credentials, so
  lookalikes such as `aca-test.accela.com.evil.example` and
  `https://aca-test.accela.com@evil.example` are rejected before a session opens.
- *Executor returns an unknown result, UI renders it as failure* — the direction
  is safe. `UNKNOWN_RESULT`/`UNVERIFIED` can only *withhold* a success claim, and
  the mutation ledger marks those states non-replayable.

---

## Observations left deliberately unfixed

Reported, not acted on: each is a judgement call for the Phase 9 owner, and all
three are outside "targeted fixes".

1. **`scripts/` carries 57 probe/reconciliation scripts** (~12.5k lines) with
   numbered duplicates (`ni_backoffice_recon{,2,3}.py`,
   `ni_calendar_recon{,2,3}.py`). The Phase 9 brief asks for "unused
   experiments" to be removed. These are also the provenance trail behind the
   portal findings, so deleting them is a decision with an evidence cost, not
   cleanup. Recommend an explicit `scripts/probes/` grouping plus a README
   note rather than deletion.
2. **The two entry points remain genuinely separate.** The legacy
   model/tool loop (`scripts/ni_agent_run.py`) and the semantic Phase 5 planner
   have different vocabularies, different result vocabularies, and separate
   evidence. Both README and `docs/phase9.md` say so plainly. Unifying them now
   is a new feature, which Phase 9 forbids — but the demo must be run through
   exactly one of them, and the docs currently allow the reader to assume
   otherwise.
3. **The Phase 7 acceptance cohort is defined in a test file**
   (`tests/test_phase7_runtime.py::CASES`) and that file is the generator of a
   published evidence artifact. F1 happened for exactly this reason. Not
   proposed as a change, but it is the structural reason to re-check every
   generated artifact against its generator before the freeze commit.

---

## Recommended before the freeze commit

1. Re-run every documented command and diff the output against the published
   number — this is the whole of F1, and it is cheap.
2. Freeze, then record the commit that the evidence identifies. Until the tree
   is committed, LicetBench prints *"the working tree is dirty, so commit 4ae0566
   does not identify the code this run used"* — correct behaviour, and it means
   no number in the submission is currently attributable to a revision.
3. Re-confirm the five safety gates above after the freeze commit, not before.
