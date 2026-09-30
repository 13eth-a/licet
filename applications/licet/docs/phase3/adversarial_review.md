# Phase 3 adversarial review — adversarial review

Reviewed 2026-09-21 against the working tree after the implementation’s implementation and
the portal integration’s extraction/runtime integration. Scope, per the Phase 3 assignment:
hallucination checks, contradiction cases, unsupported blocker detection, rule
review — specifically H05 ordering semantics, the fee/condition wording
matchers, and `render_answer`'s classification-preserving phrasing.

Method: construct a counterexample state, run `understand()` + `render_answer()`
and read the *whole* structured result (blockers, classifications, candidates,
uncertainties, contradictions) rather than the prose alone. Every finding below
was reproduced live against the inherited code before it was fixed, and is now
locked by a golden counterexample (`A01`–`A11`) and unit tests in
`tests/test_phase3_adversarial.py`.

Headline: the inherited runtime could tell a user that payment was required
when the portal said it was **not**, and could silently drop a failed
inspection when an earlier pass existed. Both are the failure mode this phase
exists to prevent — not a crash, but an unsupported confident claim.

## P1 — unsupported gate, lost blocker

| ID | Inherited behavior (measured) | Why it is unsupported | Resolution |
|---|---|---|---|
| A1 | `_is_active_condition` substring-matched `hold`/`warning`/`unsatisfied`: status **“Hold released”** produced an `active_condition` blocker classified **`confirmed_gate`**; **“No warning”** produced an observed problem. | A gate is the strongest claim Phase 3 can make. A released hold is history; a negated label is the opposite of an active one. | Condition activity is an exact-label vocabulary (`_condition_activity` → `active`/`inactive`/`unknown`); resolved labels are inert; unmapped labels become an uncertainty, never a blocker or gate. |
| A2 | `explicit_gate` concatenated all condition text and asked for `pay|payment` plus `before|prior to|required`. **“No payment is required before issuance”** satisfied it → `unpaid_fee` **`confirmed_gate`** (confidence 0.95) plus a `Required: Resolve outstanding fee` action. | The record explicitly says there is no gate. The matcher also ignored negation and cross-condition scope. | Phrase-bounded `_PAYMENT_GATE_RE` with a `_NEGATED_PAYMENT_RE` guard; wording that negates the requirement is not a gate. |
| A3 | `affects_stage` was hardcoded to `"issuance"` whenever any gate existed, so **“Payment required before final inspection”** was reported as gating *issuance*. | The portal named a different stage; the stage was invented. | `_stage_from_text` derives the stage from the wording; an unnamed stage stays `None` and is reported as an uncertainty. |
| A4 | The resolution step had no chronology: for a same-scope `Passed 9/18` + `Failed 9/20` record it deleted the `failed_inspection` blocker **and** its candidates. “Why is this permit not moving forward?” returned facts only — no blocker at all. | The contract says a *later* pass supersedes an earlier failure. The inverse (an earlier pass) must leave the failure current. | `_attempt_order` compares ISO dates; only `later` + verified scope resolves. `earlier` keeps the blocker and its candidates; unknown order/scope stays an uncertainty. |
| A5 | `"correction" in comments` on a passed attempt flagged **“No corrections required.”** as conflicting evidence, flipping `answerability` to `conflicting` and suppressing readiness. | A clean pass was reported as a contradiction. | `comment_requests_correction` requires a correction word followed by a required/outstanding word, with a negation guard. |
| A7 | `unpaid = paid is False or (due is True and paid is not True)`, so a fee whose payment state was absent was asserted as unpaid — the same rendered answer contained both “payment state not shown” and “remains unpaid ($74.50)”. | Non-payment is a portal statement. Two contradictory statements in one answer. | Only `paid is False` is unpaid; `due` with unknown payment becomes an uncertainty. |

## P2 — false conflict, wrong number, stale data, unenforced scoring

| ID | Inherited behavior (measured) | Why it is unsupported | Resolution |
|---|---|---|---|
| A6 | `" expired" in event` matched **“…permit not expired”** in history → stale-overview conflict → `conflicting` for a routine status question. | The statement says the opposite of what was concluded. | `explicit_expiration_event` is phrase-bounded with a negation guard. |
| A8 | `_format_money` returned `amount_text` first, so a row with `amount $74.50 / balance $100.00` reported **“remains unpaid ($74.50)”** and put “$74.50” in the facts list. | The reported balance was a different number from the portal's balance. | `format_fee_amount` is number-first (`balance`, else `amount`); the raw `amount_text` capture is preserved on the model, not used for the claim. |
| A9 | `_merge_entities` only filled blank fields, so a second read of the same fee with `paid=True` was silently discarded: no contradiction, and the answer kept asserting unpaid. | The contract forbids last-write-wins *and* requires competing values to be retained; this was first-write-wins with no signal. | Disagreeing scalar fields on the same entity are recorded in `state.contradictions` (identifier/evidence fields excluded). |
| A12 | The golden evaluator scored blockers by **type only**. “Score supported gate detection separately from observed problems, so cautious language cannot conceal false positives” was not enforceable: a confirmed gate passed as long as `unpaid_fee` was an allowed type. | The contract's own evaluation rule was unimplemented, so the metric could not catch the A2 defect. | `Phase3Case` gained `expected_classifications` / `forbidden_classifications`; the report now carries `confirmed_gates_emitted` and `gate_false_positives`. |
| A14 | The rendered “Facts reported by the portal:” block presented values that another source disputed as settled facts. | A disputed value is not a settled portal fact. | The facts heading is qualified when the result carries contradictions; the conflicts stay named below. |

## P3 — noise and over-reach

| ID | Inherited behavior | Resolution |
|---|---|---|
| A10 | A `$0.00` balance with `paid=False` was emitted as an outstanding amount (“Fee remains unpaid ($0.00)”). | A zero/negative outstanding amount is not an impediment; the fee fact is still reported. |
| A11 | A required document with status **“Pending”** was reported as “Required document not satisfied”. | `Pending`/`Under review` become an uncertainty; only explicitly-missing labels are blockers. |

## What I deliberately did not change

- **A contested blocker is still listed.** When two same-record reads disagree
  (A9), the fee blocker built from the first read remains in the list, but
  `answerability` is `conflicting` and both values are named. The contract says
  to retain both facts until their relation is supported; suppressing a
  contested blocker is a planner/publication-gate decision, not a rule fix.
- **Non-ISO dates stay unknown.** “Sept 18” does not order attempts — that is
  H05 applied consistently, not a parsing gap to paper over.
- **An `active_condition` whose text reads negative** (A2's fixture: a row whose
  status is `Active` but whose description says no payment is required) is still
  listed as an observed problem. The row's status is the portal's statement; only
  the unsupported *gate* was removed.
- **`execution_allowed` remains `False`**, and the retrieval runner is unchanged:
  no finding here creates a mutation path.
- **The model-level reasoning stage is still unwired** (the portal integration’s recorded
  limitation). Everything reviewed is deterministic.

## Evidence

```
GOLDEN: 46/46 passed
unsupported blockers:     0 of 40 emitted   (target: 0)
gate false positives:     0                (classification scored separately)
misclassified blockers:   0
false-ready count:        0                (target: 0)
contradiction recall:     1.0   (4 cases, 4 surfaced)
abstentions:              11    (needs_data/partial on incomplete input — counted, not hidden)
```

Full unit suite: **547 passed** (was 522; +25 in `tests/test_phase3_adversarial.py`).

`docs/phase3/adversarial_evidence.json` records the live result per
counterexample. Replay:

```bash
.venv/bin/python scripts/phase3_adversarial_replay.py
.venv/bin/python scripts/phase3_adversarial_replay.py --json docs/phase3/adversarial_evidence.json
.venv/bin/python scripts/phase3_adversarial_replay.py --baseline <dir with the pre-fix phase3 modules>
```

Provenance of the “inherited behavior” column: measured by this reviewer against
the pre-fix working tree (the Phase 3 runtime is not committed, so no revision
contains it); `--baseline` re-runs the comparison against a copy of any earlier
tree. One counterexample was reproduced per finding before the fix — the table
records the observed output, not a reconstruction.

## Handoff

- **implementation**: publication-gate enforcement for *contested premises* (a claim
  derived from a value that a same-record observation disputes should be dropped
  or explicitly abstained from, mirroring the foreign-record path); and the
  model-level interpreter must emit this classification vocabulary
  (`confirmed_gate` / `observed_problem` / `potential_impediment`,
  `required` / `likely` / `possible`) unchanged.
- **portal integration / fixture generation**: the exact-label condition vocabulary and the document-status
  sets are Null-Island shaped. Real ACA captures should extend
  `_ACTIVE_CONDITION_LABELS`, `_INACTIVE_CONDITION_LABELS`,
  `_MISSING_DOCUMENT_LABELS` and the fee-gate phrasings — an unknown label is
  reported as an uncertainty, so recall is preserved honestly until then.
  The next capture targets are condition status labels and fee grids whose
  header cells do not match `_TABLE_PATTERNS`.

  **Follow-up (fixtures landed).** Fee-grid header cells no longer need to match
  one hard-coded pattern: header cells resolve to canonical extractor fields by
  meaning (`_HEADER_FIELDS` in `licet/phase3/accela_extract.py`, one unmapped
  action/link column tolerated). The per-municipality wordings — including
  condition-status and document-status label variants — are encoded as
  `ACA_PAGE_FIXTURES` in `licet/eval/phase3_fixtures.py` and scored by
  `run_extraction_fixtures()`, with the golden set now at 57 reasoning cases.
  An unknown label still degrades to an uncertainty or `partial` coverage rather
  than a fabricated value.
- **architecture review**: the contract's gate precision/recall rule is now implemented in
  `licet/eval/phase3.py`; the remaining contract metric not yet reported is
  *requirement-strength correctness* per candidate.
- **Phase 4**: unaffected. Phase 3 still cannot schedule, pay, submit, or
  cancel; `execution_allowed` is `False` everywhere.

## Files touched

| File | Change |
|---|---|
| `licet/phase3/rules.py` | exact-label condition activity; negation-guarded payment gate with derived stage; money-first formatting; unknown-payment honesty; zero-balance guard; document-status split; chronology-aware resolution (`_attempt_order`); negation-guarded correction/expiry matchers. |
| `licet/phase3/extract.py` | same-entity scalar disagreement recorded as a contradiction instead of silently dropped. |
| `licet/phase3/reasoning.py` | uses the shared fee rendering and the negation-guarded expiration matcher. |
| `licet/phase3/render.py` | disputed facts are marked as disputed. |
| `licet/eval/phase3.py` | classification-level scoring + `gate_false_positives` / `confirmed_gates_emitted`. |
| `licet/eval/phase3_fixtures.py` | counterexample cases `A01`–`A11` (46 total). |
| `tests/test_phase3_adversarial.py` | 25 unit-level locks on the same predicates. |
| `scripts/phase3_adversarial_replay.py` | replay + optional baseline comparison + evidence dump. |
