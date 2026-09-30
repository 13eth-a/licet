# Phase 9 — architecture review senior review

Reviewed architecture, flagship demonstration and technical narrative on 2026-09-25. Completed targeted fixes found in the other Phase 9 lanes. **The review and local packaging work are complete; the full submission exit condition is not yet met.** A verified live booking, final recorded demo and reviewed release freeze remain absent.

## Architecture verdict

The strongest engineering ideas are the separation of portal observations from inferred requirements, deterministic policy below planning, record/inspection identity checks at mutation boundaries, bounded recovery, and explicit unknown-result reconciliation. The benchmark's source/task identities and independent fixture-state checks make regressions inspectable.

The largest remaining weakness is the live demonstration: fixture-backed orchestration exercises the complete scheduling/verification sequence, but the fresh semantic-planner run stops before inspection availability. More UI polish cannot close that evidence gap. Fix or select a usable sandbox inspection path before describing the flagship as an end-to-end booking demonstration; do not bypass identity, uncertainty or no-spend checks to manufacture a success.

Two entry points exist. `scripts/ni_agent_run.py` runs the legacy model/tool loop. `scripts/ni_phase5_acceptance.py` runs the semantic planner using the real Solari capability factory in `licet/eval/phase5_live.py`. Earlier Phase 9 copy incorrectly suggested that factory did not exist; corrected. The remaining gap is demonstrated live completion, not absent integration code.

## Fresh live evidence

Request: “Get permit 000000014 ready for its next inspection without paying anything or signing anything.” One attempt, ten-step ceiling, plan-only mode, all mutations held for approval. This exercised real Solari/Accela I/O; the semantic planner used its deterministic path, not a model API.

1. `FIND_PERMIT`: Phase 2 independently verified the permit.
2. `READ_PERMIT_STATE`: read overview.
3. `DETERMINE_BLOCKERS`: interpreted structured state; this does not establish unseen inspection history.
4. `READ_INSPECTIONS`: stopped after recovery validation could not establish known-good state.

Final: `BLOCKED / PLAN_LOOP_DETECTED`, four semantic steps, zero attempted or verified mutations. The internal error code is recorded verbatim; the useful explanation is “inspection history could not be verified after bounded recovery.” This run did not reach the calendar and proves neither availability nor a booking. Root cause of the portal read failure is not resolved by this review. *(portal integration lane follow-up, 2026-09-25: root cause since identified — the runner clicked a dead-but-rendered `Inspections` wrapper NI never shows; fixed with a read-only, provenance-guarded label fallback. See [`portal_read_root_cause.md`](portal_read_root_cause.md).)*

[Sanitized live summary](review-live-summary.json) binds the ignored raw report by SHA-256. Raw logs and portal captures remain under `logs/phase9-architecture review/` rather than in public submission assets.

## Repairs

- Result cards no longer infer verified completion from a model stopping tool calls. They display **COMPLETION DECLARED** until evidence/scoring supports more.
- A successful generic post-action page read no longer masquerades as proof of the requested inspection/date/status. An earlier successful action cannot hide a later uncertain action.
- Stop reasons and policy cards no longer assert globally that no inspection/payment/mutation occurred; a hold applies to that action, while prior actions remain in the trace. Incomplete work is no longer automatically called partial success.
- The demo URL check now requires the exact HTTPS sandbox hostname, ordinary HTTPS port and no user-info. Lookalike domains previously passed its substring test. This supplements the independent policy boundary.
- Wheels now contain frozen benchmark JSON and reasoning prompt resources. The reasoning prompt loads relative to its package, not the working directory.
- Repaired missing domain-error imports and undefined type/test diagnostic names discovered by lint.
- Added exact dependency versions in `requirements-release.txt` and included the file in benchmark source provenance. This is a tested Python 3.11/macOS snapshot, not a universal hash lock.
- Corrected README/demo descriptions of live wiring, historical calendar evidence and frozen benchmark outcome semantics.

## Verification

- Full suite: **1,385 passed**. Eighteen presentation/resource/error-path regressions added. After the final provenance change, 32 focused regressions passed; after the final held-action wording edit, the 18 presentation regressions passed again.
- Repository-wide syntax/undefined-name lint passes: `python -m ruff check --isolated --select F821,F822,F823,E9 licet licetbench scripts tests`. This is a focused correctness gate, not a claim that every style rule or full static typing passes.
- `compileall` and `git diff --check` pass.
- Final wheel built, installed with pinned dependencies into a fresh temporary virtual environment, then tested from `/tmp`: 50/50 core expectations and packaged prompt loading pass. `pip check` reports no broken requirements. This verifies a distributable install, not a fresh Git clone of an uncommitted release.
- All benchmark suites rerun: core five times **250/250** expectations, flagship subset five times **50/50**, public reserve **6/6**, variants **143/143**, prompts **22/22**. Zero unsafe outcomes or grader errors. [Summary and measured source digest](review-benchmark-summary.json). These are offline fixtures; the five flagship repetitions are not five live flagship runs.
- History scan: 414 reachable Git blobs, no recognized OpenAI/Solari key shapes or private-key headers; no tracked `.env` history. [Scope and findings](review-history-scan.json). This does not rule out every secret representation; no credential rotation was indicated by this scan.

## Benchmark narrative correction

Frozen v1 reports 145 `SUCCESS` outcomes among 250 runs. Ten of those are correct refusals in the two unrecoverable recovery fixtures, repeated five times. Therefore “145 completed user goals” is too strong. Keep the versioned goldens unchanged and say **145 frozen benchmark SUCCESS labels; 250/250 expected outcomes**, then show category outcomes and this caveat. A successful component recovery test is not equivalent to scheduling a user's inspection.

The prompt suite now has 22/22 expected outcomes and 17 completions; this supersedes the earlier architecture review Phase 8 snapshot with five parser failures. Normal/noisy evidence remains a separate simulated cohort and must not be merged with live or Phase 7 acceptance results.

## Recommended three-minute narrative

- **0:00–0:15:** show the exact user goal and sandbox environment. Explain the repetitive legacy-portal work.
- **0:15–1:20:** show one contiguous fresh live trace: verified record, observed overview, supported reasoning, and the actual inspection-history stop. Caption “plan-only live sandbox; no mutation attempted.” Do not splice in a calendar from another run.
- **1:20–1:55:** clearly switch to **SIMULATED PORTAL I/O** for the fixture that exercises allowed scheduling and independently checked final state. This demonstrates implementation coverage, not a live booking.
- **1:55–2:20:** show the no-payment refusal and controlled recovery, with fixture labels visible.
- **2:20–2:40:** show the planner → policy → capability → browser → verify architecture and uncertainty/recovery branches.
- **2:40–3:00:** show the frozen benchmark numbers with the recovery-label caveat and the remaining live limitation.

This is an honest prototype demonstration. If judging requires a successful live mutation, that requirement remains unmet; replacing it with fixture footage does not satisfy it.

## Release gates still open

1. Resolve the live inspection read/availability barrier and record a usable flagship; do not weaken safety checks. Repeat the final live scenario five times after freeze.
2. Capture the final video and screenshots from the actual chosen runs. No recording or publish action was performed in this review.
3. Review the shared working tree, create the release commit/tag, then rerun the packaged benchmark and clean-clone instructions on that frozen revision. Current source digests identify validation, not a release freeze.
4. Full static type checking and cross-platform dependency resolution remain unconfigured/unverified. The targeted lint and tested platform are stated explicitly above.

No new portal or workflow was added, and no live mutation was performed.
