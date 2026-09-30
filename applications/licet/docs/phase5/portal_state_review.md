# Phase 5 portal-state review — GLM 5.3 Flash

Reviewed 2026-09-22 against the working tree after Luna's Phase 5 planner and
DeepSeek's planner attack. Scope, per the Phase 5 assignment: **cases where a
planner failure is actually state extraction** — confusing Accela rendering
producing a bad `PermitState` that a correct planner then faithfully blocks on,
loops on, or acts on. The distinction the phase depends on:
`bad PermitState → good planner makes bad decision` is not an Astra problem.

Method: drive the **real** adapter (`licet/phase3/accela_extract.py`), real
`understand()`, and the real Phase 5 completion gate (`reasoning_is_sound`)
with portal payloads shaped like ACA's actual renderings, then read what the
planner's gates would receive. Every finding was reproduced live before the
fix; each is locked by a regression in `tests/test_phase3_adversarial.py`
(H01–H07 section) and replayable with `scripts/phase5_portal_state_replay.py`.

## Headline

The deterministic rule engine **can** emit `blocking_answer=True` — DeepSeek's
Phase 5 handoff recorded it as "unproducible by the shipped rule engine," which
is now stale. Four live classes produce it: unknown attempt ordering (two
paths), a completed attempt with no recorded result, and unavailable/parse-failed
section coverage (`rules.py` emissions at the `unordered_conflict`,
completed-unknown-outcome, and coverage branches). The Phase 5 gate is
therefore reachable from real portal data, not only from injected reasoning
results. The handoff is closed; what remained wrong was **the data feeding
that gate**, which is this review's findings.

The worst consequence, end to end: ACA renders dates as `MM/DD/YYYY`; Phase 3
extraction stored them raw while the ordering logic (`_attempt_order`) only
parses ISO — so an honestly rendered fail→pass history was reported as
"attempt order is not established," flipping `answerability` to `partial` and
the Phase 5 completion gate to `False`. A correct planner cannot distinguish
that from an honestly unordered history: it loops targeted reads until
`PLAN_LOOP_DETECTED`. **Extraction turned a solved question into a planner
loop.** Phase 4 already normalized these dates (`_normalize_date_token`) —
Phase 3, which feeds every gate, did not.

## Findings

| ID | Inherited behavior (measured) | Why it harms the planner | Resolution |
|---|---|---|---|
| H02 | Table-path `completed_date` kept `MM/DD/YYYY` raw. `_attempt_order` returns `unknown` for any non-ISO date, so a fail 9/18 → pass 9/20 history read as unordered. | A real later pass failed to resolve a real earlier failure. The planner receives a `blocks_answer` uncertainty for data the portal states plainly; targeted re-reads return the same `partial` answer until `PLAN_LOOP_DETECTED`. | `_normalize_date` in the adapter: portal date tokens become ISO at extraction, across both the table and text-line paths. Unparseable tokens keep the raw text (honest `unknown`), never dropped. |
| H02b | The citizen-detail *text* path dropped a trailing date token entirely: `Rough Electrical | Insp Scheduled | 05-20-2026` yielded no date at all. | Scheduled appointments lost their date; Phase 4's re-read path consumed the same parser, so its snapshot lost the date too. | The text-row parser captures the trailing date token into the status-appropriate field (`scheduled`/`completed`/`requested`), normalized to ISO. |
| H06 | Some agencies render the outcome in the status column (`Status: Failed`). The lifecycle vocabulary matched nothing, so the row carried no result: `failed=False`, `blockers: []`. | A permit with a live failure extracted as if none existed — the planner reports "no blockers" or proceeds toward scheduling on a false-clean state. The reverse-direction twin of the false-ready gate. | In `_finalize_row`, an outcome word found in the status cell (`normalize_result(status) is not None`) is carried as the result too. The lifecycle vocabulary stays the sole authority for *status*; this only prevents the outcome from vanishing. |
| H01 | A legend-shaped line (`Scheduled | Completed | Failed`) validated cell-by-cell against the vocabularies and became a fabricated inspection row whose *type* is `Completed`. | A phantom inspection attempt enters `PermitState`; merge keys on `(id, type, scope)` keep it; any same-type matching downstream reads a third attempt the portal never rendered. | `_is_legend_type`: a would-be type that itself normalizes as lifecycle or outcome is rejected in both row paths. Real type names never normalize as either dimension. |
| H05 | A declared-empty marker (`You have not added any inspections.`) beside parsed rows kept coverage `complete` — the observation's own docstring promised the opposite. | The section is self-disputing; the planner is told coverage is complete while the page contradicts the rows, so the completeness gate licenses claims the evidence disputes. | `rows and declares_empty` degrades coverage to `partial` (rows are kept). One existing integration-test fixture combined the marker with an appended row; its assertion was updated to the honest expectation. |
| H03 | Fee status wording outside the known set was silently dropped: `Status: In Collection` produced `paid=None, due=None` — the money fact survived, its status did not. | Phase 3 already refuses to invent a payment state, but *discarding* a stated one hides the very fact a no-spend goal needs. | `_paid_from_cell` recognizes the observed non-payment wordings (`in collection`, `past due`, `delinquent`, `overdue`) as `False`; the existing guard makes `paid=False` imply `due=True`. Anything unrecognized still stays `None`. |
| H04 | A `Due Date` column is unmapped, so the whole fees header failed recognition; the money-line fallback then mangled the row: `description='Plan Check Fee | 09/30/2026'`. | A zero-coverage fee section and a mangled description are exactly the "weird portal response" that makes downstream money reasoning wrong. | The header map gains `due date`/`due on` → a `due_date` cell key; `_finalize_row` pops it (a due date is not a payment status — `paid` stays `None`). The table parses with clean money facts. |

## End-to-end consequence (H07)

The same page, before and after, through the real gates:

```
Page:  Rough Electrical | Completed | Failed | 09/18/2026
       Rough Electrical | Completed | Passed | 09/20/2026

pre-review:  completed_date raw MM/DD/YYYY
             → _attempt_order = unknown
             → uncertainty: "attempt order is not established (missing or
               tied dates)"      ← contradicts the page, which shows dates
             → answerability partial, reasoning_is_sound False
             → planner: blocked, re-reads, PLAN_LOOP_DETECTED at budget

reviewed:    completed_date ISO
             → _attempt_order = later
             → uncertainty: "both attempts' scopes are not shown, so whether
               the pass addresses the failed attempt is not established"
             → planner: the failure stays open for the reason actually true

Page + rendered Scope column (positive control):
             → pass resolves the failure; blockers == []; no blocking
               uncertainty; the planner proceeds on honest state
```

The second wording matters: with dates now reliably extracted, the remaining
unknown on a scope-less page is the scope — the uncertainty must name the
premise actually missing, not assert a fact-free "order unknown."

## blocks_answer handoff (closed)

`docs/phase5/adversarial_review.md` handed this to GLM as "enforced but
unproducible by the deterministic rule engine … the model-level interpreter is
the only emitter." Re-verified: the rule engine emits `blocks_answer=True` in
four classes today (unknown attempt ordering via the unresolved-conflict and
resolution branches, completed attempt with no recorded outcome, and
unavailable/parse-failed coverage), and `understand` converts them to
`partial`/`needs_data` answerability, which `reasoning_is_sound`,
`select_inspection_action`, and `mutation_denial` all gate on. The class is
producible from real portal data end to end. Locked by
`test_blocks_answer_uncertainty_is_producible_by_the_deterministic_stack`.

## Deliberately not changed

- **Fee grid "Due Date" wording still varies by agency.** The two new header
  keys cover the observed shape; a wording outside the map still degrades to
  the money-line fallback honestly (that fallback's mangling is now the
  pre-existing bounded behavior, and an unknown header stays partial rather
  than invented). Live captures remain the way to widen the map.
- **`_money_lines_as_rows` description joining.** `A | B | $1.00` joins `A | B`
  as the description; acceptable for a fallback whose alternative is no data.
  H04's fix keeps well-formed grids out of it.
- **Phase 4's `parse_inspection_rows` alias.** It consumes the same text-row
  parser, so scheduled snapshots now carry ISO dates too; `_snapshot_from_row`
  already accepted ISO (`_normalize_date_token` handles both shapes), so the
  seam is consistent in both directions. Its `_adapter_rows` literal-shape
  fallback is untouched.
- **Vague-goal `SUCCESS` capping, free-text constraint enforcement,
  cross-process idempotency.** Unchanged; they remain Luna/Astra residuals in
  the adversarial review.

## Evidence

```
counterexamples (legacy = pre-review extraction reproduced verbatim in the replay)
  H02   legacy dates raw + order 'unknown'   → current ISO + order 'later'
  H02b  legacy date dropped (None)           → current '2026-05-20'
  H06   legacy blockers []                   → current ['failed_inspection']
  H01   legacy fabricated type 'Completed'   → current rejected
  H05   legacy coverage 'complete'           → current 'partial'
  H03   legacy (paid=None, due=None)         → current (False, True)
  H04   legacy mangled money-line row        → current clean table row
  H07   legacy "order is not established"    → current names the missing scope
  C1    positive control: scope + dates      → failure resolves, blockers == []
```

- `tests/test_phase3_adversarial.py` — 10 new regressions (H01–H07 + the
  blocks_answer producibility lock; 35 total in the file).
- Full suite: **889 passed** (879 pre-existing + 10).
- `scripts/phase5_portal_state_replay.py` re-derives every counterexample
  against the current tree with the pre-review extraction reproduced inline;
  `--json docs/phase5/portal_state_evidence.json` records the result. The
  legacy column is reconstruction, not a committed revision — the pre-review
  Phase 3 extraction is uncommitted, as with the earlier phase reviews.

## Handoff

- **Astra**: the completion gate is fed by extraction; when a live run reports
  `PLAN_LOOP_DETECTED` on repeated reads with unchanged `partial` answers,
  check the trace's `remaining_goal` against what the page actually rendered
  before blaming plan selection — H02 was exactly that signature. The four
  `blocks_answer` producers are the deterministic classes a model reasoner
  must reproduce, not narrow.
- **Luna**: `PermitState.inspections` dates are ISO from extraction now; the
  `select_inspection_action` date checks and Phase 5 `established()` date math
  can rely on it. If a proposal's date window ever needs to accept raw portal
  text, widen it at the adapter, not in `established()`.
- **Solar**: the fixture set gains real-shape cases H02/H06/H01/H03/H04;
  per-municipality captures with different status-column conventions or fee
  headers remain the recall path — an unknown label degrades to
  uncertainty/partial, never a fabricated value.
- **DeepSeek**: the blocks_answer handoff is closed (section above); the
  attack surface that remains model-only is the *wording-level* matchers
  (`_NEGATED_*` regexes) against agency phrasings not yet captured.
