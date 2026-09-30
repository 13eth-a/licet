# Phase 8 — Astra final review

Astra's secondary semantic grading and final benchmark review are complete. Additional framework gaps found during review were repaired without changing the frozen core answers. The curated artifact set (headline, evaluation map, flagship traces, failure-priority ranking, regression curve) is in [`report.md`](report.md).

## Changes

- Freeze all 50 core definitions in `licetbench/core-v1.json` and bind complete underlying fixtures with `core-contracts-v1.json`. Fixture drift cannot silently change a passing benchmark.
- Independently re-read action state in the grader. An executor's claimed success cannot substitute for the observed record, inspection, status and date. Correctly refusing a foreign record before submission remains a safe stop.
- Reject ungraded oracle fields, unsafe success, false verification, nonfinite measurements, duplicate task IDs and misleading partial credit. Honor per-task repetitions and actual planner step budgets; exclude recovery annotations from executed-action traces.
- Record complete task manifests and source-file digests, including dirty working-tree contents, in reports. Reports identify the measured source snapshot; they do not establish a clean release commit.
- Add a separate 22-task `prompts` suite that actually sends natural language through production parsing and resolution/planning. Keep the historical 143 component variants separate.
- Fix the five production parsing/planning failures the prompt suite surfaced, without touching a single golden: "Book" now authorizes scheduling, a leading "Read only:" is an information grant rather than a conflict, and a parsed address no longer absorbs a terminal "?", an em-dash clause or a parenthetical. Each fix is pinned by a regression test, not by a weakened expected outcome.
- Make the reported run identity independent of git: `source_digest` is computed from the bytes of `licet/`, `licetbench/` and the dependency pins, so a dirty working tree stays replayable even though `HEAD` names a revision the run did not use (audit A9).
- Add paired configuration comparison and evidence-bound secondary semantic review. Semantic acceptance cannot promote a deterministic failure; rejection or uncertainty can demote a success. Model-comparison mode rejects fixture reports with zero model calls.

## Measured results

Artifacts are in `final/`; reproduce with `.venv/bin/python scripts/phase8_review.py`.

| Evaluation | Result |
|---|---|
| Frozen core, five repetitions | 250/250 expected outcomes; 145 actual completions |
| Component variants | 143/143 expected outcomes |
| Holdout reserve (deliberate run, not a CI gate) | 6/6 expected outcomes; 4 completions |
| Real prompt path | 22/22 expected outcomes; 17 completions, 5 published safe stops |
| Normal integrated runtime | 20/20 completions |
| Noisy integrated runtime | 15 completions, five safe stops; 14/15 recovery attempts succeeded |
| Prompt step budget 4 → 20 | Five completion wins, zero losses, 17 ties; expected behaviour 16/22 → 22/22 |

The integrated cohorts observed zero duplicate submissions and zero false successes. External I/O is simulated. Repetitions measure deterministic repeatability, not independent samples of live reliability. Policy-denial counters and wrong-record selection counters must not be interpreted as measured physical mutations on a live portal. Task action metadata describes contracts; source-specific graders enforce the supported fixture contracts, not arbitrary new action languages.

The configuration experiment changes the actual planner budget and improves completion by 22.73 percentage points. It makes no model calls, measures no model cost, and is not an Astra-versus-Luna comparison.

## Secondary semantic review

Reviewed all ten core understanding answers against structured permit evidence and explicit golden constraints: all ten accepted. `final/semantic-reviews.json` records individual rationales, evidence keys and hashes binding each review to the exact answer and ground truth in `final/semantic-packet.json`. This was an assistant review, not an automated model API experiment. Acceptance supplies secondary evidence and does not increase hard-grader scores.

The review checks unsupported claims, chronology and inspection scope, unresolved contradictions, distinction between completion and passing, and conditional versus definite next steps. In particular, fees must not become a confirmed scheduling gate without supporting evidence; a pass for another unit must not clear a failure; cancellation must not imply resolution. H04 remains a readability opportunity: include unit labels explicitly when discussing competing inspection rows.

## Failed-run analysis (closed)

The review's first run exposed five language-handling failures. All five were production parsing/planning defects, not grader crashes, and all five were fixed in the graded path; not one golden was weakened to hide them:

| Task | Failure | Resolution |
|---|---|---|
| PROMPT-003 | “Book” was interpreted as read-only rather than a scheduling request | “Book” is an authorized scheduling verb, and an explicit no-changes restriction still outranks it (`tests/test_phase5.py`). |
| PROMPT-006 | The read-only blocker question was stopped as a conflict | A leading “Read only:” is an information grant, so the question is answered instead of clarified (`tests/test_phase5.py`). |
| PROMPT-DISCOVERY-002-P029 | Natural-language address request returned ambiguity | Terminal sentence punctuation no longer enters the street field (`tests/test_lookup_adversarial.py`). |
| PROMPT-DISCOVERY-004-P032 | Commercial Alteration type qualifier was lost, yielding ambiguity | The record-type qualifier survives a question-style wording (`tests/test_lookup_adversarial.py`). |
| PROMPT-DISCOVERY-005-P036 | Address plus “multiple records there” was rejected as invalid | An address stops at trailing commentary — an em dash, a colon or an opening parenthesis — while ambiguity detection stays in the resolver (`tests/test_lookup_adversarial.py`). |

The whole suite is now locked at 22/22 expected outcomes (`tests/test_phase8_astra.py::test_prompt_suite_keeps_every_reviewed_golden_intact`); the five remaining non-completions are the cases whose *published* goldens require a safe stop. The generated component prompt variants remain insufficient evidence of language coverage because those paths use prebuilt structured goals. Only reviewed variants retaining enough context were admitted to the new prompt suite; omitted ZIP/type/target context must not inherit an inapplicable golden.

## Remaining evaluation boundaries

The six public “holdout” fixtures are regression cases now; they are not an unseen holdout. A future generalization claim needs separately retained, previously unexamined cases. Live portal reliability, model inference comparisons and adaptive routing require their own measured runs. Phase 8's offline review is complete; those claims are not implied by its passing component scores.
