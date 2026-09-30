# Phase 3 runtime integration — portal integration handoff

Completed 2026-09-21. Owner: Accela extraction + runtime integration. This
closes the implementation/portal integration portion of the Phase 3 assignment on top of the architecture review’s
reasoning handoff (`reasoning_contract.md`, `reasoning_cases.md`,
`current_architecture_evidence.json`).

## What was built

| Module | Role |
|---|---|
| `licet/phase3/accela_extract.py` | **The missing bridge**: real `read_page` payloads → Phase 3 section observations. Overview from ACA's record header (identity from the URL capIDs, never invented); Inspections with validated text-line rows + the wizard's `(required)` marker preserved as requirement evidence; Fees/Documents/Conditions/History table and money-line shapes; loading/truncation surfaced. |
| `licet/phase3/errors.py` | `Phase3ErrorCode`: `INSPECTIONS_NOT_FOUND`, `FEES_NOT_FOUND`, `HISTORY_NOT_FOUND`, `DOCUMENTS_NOT_FOUND`, `CONDITIONS_NOT_FOUND`, `HISTORY_PARSE_FAILED`, `STATE_EXTRACTION_FAILED`, `CONFLICTING_RECORD_STATE`, `INSUFFICIENT_EVIDENCE`, `UNSUPPORTED_STATUS`, `FOREIGN_RECORD_EVIDENCE`. |
| `licet/phase3/runner.py` | Read-only targeted retrieval: executes `needed_sections` through the **ToolDispatcher** (navigate to the record's own deep link + benign section-label clicks resolved via `BENIGN_TARGETS`), one bounded pass, URL tracked from outcomes, loading sections get one settle-and-reread. |
| `licet/phase3/render.py` | Deterministic narrative renderer over a validated `ReasoningResult` only. Preserves FACT/INFERENCE/UNCERTAIN, blocker classification, requirement strength (`Required/Likely/Possible:`), uncertainties, and partial-answer provenance. Introduces no claims. |
| `licet/eval/phase3_fixtures.py` | Golden-state fixtures (5 synthetic records with recorded ground truth) + 57 reasoning cases encoding the architecture review’s 30-row oracle, the 11 adversarial counterexamples, the flagship acceptance cases, and the checklist's missing-section / multi-blocker cases. Also holds the per-municipality **extraction fixtures** (`ACA_PAGE_FIXTURES`) that run real `read_page` payloads through the ACA adapter. |
| `licet/eval/phase3.py` | Evaluator with the contract's metrics: unsupported-blocker count (numerator/denominator), false-ready count, contradiction recall, abstentions. |

Plus the P1 fixes below in `licet/phase3/extract.py`, `rules.py`,
`reasoning.py`, `routing.py`, and the Phase 1/2 schema
(`licet/schema/permit.py`, `licet/schema/extract.py`).

## The review's P1 items — status

| architecture review finding | Resolution |
|---|---|
| Catalog entries become requirements | `Permit.missing_inspections()` now subtracts history only from `required_inspection_types` (ACA's own `(required)` marker, captured by the adapter). New `offered_inspection_types()` carries the catalog under a name that implies no obligation. `apply_next_action` emits nothing without explicit requirement evidence. |
| Status substring matches reverse meaning | Already exact-vocabulary in phase3 (verified by tests); `"active hold"` now reads as active via bounded phrase matching, not positive-substring guessing. Negative phrases (`Not Issued`, `Unexpired`, `Inactive`, `Not Scheduled`, `Not Approved`) are covered by `tests/test_phase3.py` + golden case S05. |
| Unattributed strings promoted to portal facts | `Provenance` gained `UNATTRIBUTED`; the bare-string coercion default changed from `PORTAL` to `UNATTRIBUTED`. The old behavior was asserted by one test (`test_schema.py`) — the test asserted the defect and was updated. |
| Planner prompt authorizes actions | Phase 3 reasoning remains a separate read-only module; the retrieval runner builds **only** `read_page`/`navigate`/benign-label `click` calls and passes through the same guard as every other path (tested: `test_runner_never_emits_mutation_intents`). No reasoning output can become a tool call. |
| Partial absence stored as requirements | Loading markers, declared-empty observations, truncated grids, and calendar samples moved to `Permit.coverage_notes` (new field). Calendar claims are scoped to observed months ("no bookable appointment dates in the 2 observed month(s)"). In the Phase 3 layer, absence yields `explicitly_empty`/`partial` coverage — never a blocker, never a requirement. |
| Single normalized inspection status loses dimensions | Phase 3 `Inspection` already carries lifecycle + result + comment linkage + scope; `normalize_lifecycle` no longer fabricates `PENDING` from an absent status. Golden cases S04/H01–H05 lock the semantics. |
| Single next_action hides alternatives/conflicts | `ReasoningResult` carries multiple blockers (ranked, classified) and multiple candidates with requirement strength + preconditions; merge retains competing same-field facts and records rejected foreign observations in `rejected_observations` instead of last-write-wins. |

## Evaluation evidence (golden set, deterministic)

```
GOLDEN: 35/35 passed
unsupported blockers: 0 of 33 emitted   (target: 0)
false-ready count:     0                (target: 0)
contradiction recall:  1.0
abstentions:           8   (needs_data/partial verdicts on incomplete inputs — correct behavior, counted, not hidden)
```

Full unit suite: **522 passed** (was 503; +19 integration tests for the
adapter, runner, renderer, errors).

The flagship answer (rendered from the structured result, verbatim):

```
Facts reported by the portal:
- The portal reports permit status Issued.
- Fee Permit balance: $74.50, unpaid.
- Inspection Rough Electrical — completed — failed
- Rough Electrical inspector comment: "Enclose exposed junction box."

What is in the way:
- Rough Electrical inspection did not pass (result: Corrections Required)
  (observed problem, affects: inspection)
- Permit balance remains unpaid ($74.50) (potential impediment)

Possible next steps (not yet executed):
- Likely next: Address Rough Electrical correction — (Enclose exposed junction box.)
- Possible: Request reinspection: Rough Electrical — Consider a new attempt
  after the correction; eligibility and requirement are not established by the
  failure alone.

Uncertainties:
- The fee is unpaid, but no portal evidence establishes that it blocks a
  particular stage.
```

Note the fact/inference distinction the phase demands: the failure and the
balance are facts with classification; the reinspection is `Possible:` with
its unknown premises stated; the fee's blocking scope is explicitly
not established.

## Design decisions worth recording

- **Blocked-vs-conflicting separation.** Foreign-record observations are
  recorded in `PermitState.rejected_observations` and surfaced in the answer,
  but do not flip `answerability` to `conflicting` — they are hygiene events,
  not record-state conflicts (oracle U04). Contradictions between two sources
  *on the same record* do flip it (S03, F04).
- **Answerability ladder.** `understand` downgrades to `partial`/`needs_data`
  only when unread coverage could change *this* answer. A blocker question
  whose route sections are unread stays `partial` even with zero observed
  blockers — the runtime never implies "nothing blocks" from covered evidence
  alone (publication gate 5, and the anti-false-ready metric).
- **Ordering honesty.** A same-type fail+pass pair with unknown dates or a
  date tie suppresses all correction/reinspection candidates (H05); differing
  known dates establish order (H01); differing explicit scopes keep a failure
  alive (H04).
- **Outcome questions.** "What did the inspector say?" answers from linked
  comment evidence in hand and never tours tabs (routing stop rule); outcome
  unknowns qualify but do not block a quote answer (F03).
- **Stale-overview detection.** Overview-Issued + dated explicit expiration
  event with no renewal evidence is flagged as a conflict with what would
  resolve it (S03).

## Known limitations

- ~~Fees rendered as nested ACA grids with header cells not matching
  `_TABLE_PATTERNS` degrade to partial coverage.~~ **Resolved**: header cells
  now resolve to canonical extractor fields by *meaning* (`_HEADER_FIELDS`), one
  unmapped action/link column tolerated, so per-municipality grids parse as data
  instead of yielding a phantom "Unnamed fee" with no amount. A wording licet
  cannot resolve still degrades to `partial`, never to invented rows. The
  per-municipality variants are locked by `ACA_PAGE_FIXTURES` (8 fixtures across
  two municipal wordings) and `tests/test_phase3_fixtures.py`. A live capture of
  an agency whose labels exclude every canonical synonym is still the way to
  widen coverage further — an unknown header stays honestly partial.
- The retrieval runner performs one bounded pass per `understand` round; the
  "repeated unavailable observation returns a partial answer" loop is the
  caller's policy (Phase 5 planning territory), not implemented here.
- The model-level reasoning stage is a **facade, not a live provider call**:
  `reason_with_model` runs the structured prompt with zero action tools and
  validates output through the publication gates, but the default adapter
  (`_scripted_model_adapter`) reproduces the deterministic layer's conclusions
  in model shape so the coordinator, payload, coercion, and gates are exercised
  offline. The snapshot now carries the section entities, coverage, conflicts,
  and rejected observations — without them the model stage reasoned over an
  empty record and the model-path check scored 10/50; it now passes the full
  57/57 golden set. Pointing `LICET_PHASE3_MODEL_ADAPTER` at a real provider is
  still the one open integration decision (Phase 4 boundary); `understand`
  itself stays fully deterministic.
- Golden cases are synthetic; passing them does not establish a production
  hallucination rate of zero (contract's own caution). The eval split —
  golden reasoning on verified structured inputs vs. extraction-through-adapter
  fixtures — keeps the two error classes separable; the split is now both a
  reasoning set (`build_cases`) and an adapter set (`run_extraction_fixtures`)
  scored in the same report.

## Handoff

- **implementation**: publication-gate enforcement in code before any model output is
  rendered (gate list in `reasoning_contract.md` §Publication gates); the
  model-level reasoning invocation.
- **fixture generation**: ~~encode additional per-municipality ACA wording variants as
  fixtures (fees grid header variants are the first gap)~~. **Done**:
  `ACA_PAGE_FIXTURES` (fees/documents/inspections/history/conditions header
  variants across two municipal wordings) with the adapter canonical-field
  mapping they require, plus the checklist's missing-section and multi-blocker
  golden cases.
- **adversarial review**: challenge the golden set itself — especially H05 ordering
  semantics and the fee/condition wording matchers — and audit
  `render_answer` for classification-preserving phrasing. **Done**:
  `docs/phase3/adversarial_review.md` (counterexamples A01–A11, now 46 golden
  cases, plus `tests/test_phase3_adversarial.py` and
  `scripts/phase3_adversarial_replay.py`). Two P1 defects were real: a negated
  payment condition became a `confirmed_gate`, and an earlier pass silently
  resolved a later failure. Gate classification is now scored separately from
  blocker type, so a false gate fails the case instead of hiding behind a
  cautious type.
- **Phase 4**: `execution_allowed` is `False` everywhere and the retrieval
  runner is incapable of building mutation calls; action execution starts from
  the planner, not from Phase 3 results.
