# Phase 9 — submission package and demo plan

> **2026-09-28 media package:** [fresh actual browser recording](phase9/browser-recording.md) is complete, with same-session screenshots and locally retained videos. The older [captioned evidence draft](phase9/demo/README.md) is superseded. Nothing has been uploaded; the source freeze and official benchmark evidence remain unchanged.

> **Portal-real evidence — 5/5 fresh runs (2026-09-26):** those Phase 5 semantic plan-only runs verified permit `000000014`, identified required `Brycer Inspection History`, enumerated the complete 18-type catalog, opened an identity-verified calendar on `aca-test.accela.com` (SANDBOX), observed no active dates in Sep–Nov 2026, and stopped with `PARTIAL_SUCCESS / NO_SAFE_ACTIONS` and zero mutations. No authenticated session was run for this 2026-09-29 update; findings remain tied to the recorded dates and surveyed tenant configuration. Five fresh sessions passed every criterion with byte-identical reports. See [5-run acceptance](phase9/flagship-acceptance-5run-20260926.md) and the [acceptance matrix](phase9/acceptance-matrix.md). Sections below describing earlier failures are historical. The [capacity findings](phase9/sandbox-capacity-findings.md) explain why a booking is unreachable.

> **Environment terminology:** portal-real Phase 5 read-only acceptance and the P13 calendar capture ran against `aca-test.accela.com`, which Licet correctly classifies as `SANDBOX`. These runs are evidence from the real Accela **test portal**, not from a production municipal host. The captured semantic plan-only run verified permit `000000014`, read the complete inspection catalog, identified required `Brycer Inspection History`, and read an identity-verified calendar with no active dates in Sep–Nov 2026; cost/signature remained unknown and mutations were zero. Its earlier stop reason was misleading and is corrected by the planner change; the saved run artifact itself retains its original recorded reason. See [`phase9/submission-candidate-provisional.md`](phase9/submission-candidate-provisional.md).

> **Execution evidence distinction:** schedule → independent reread → `VERIFIED_SUCCESS` is covered end-to-end only with fake I/O in `tests/test_phase5_integration.py`. No actual sandbox booking is established by that test. The 2026-09-26 capacity survey found seeded back-office capacity but no citizen-reachable capacity on account-owned records ([capacity findings](phase9/sandbox-capacity-findings.md)). `scripts/ni_citizen_capacity_query.py` now implements a bounded, read-only recheck over owned records → citizen-offered types → active calendar windows. It has not yet been run live; do not treat its implementation or the dated survey as current availability. A real booking still requires a valid observed slot and explicit one-action authorization.


**Scope:** package and communicate the existing workflow; do not add portals, workflows, or model architectures. Licet is an engineering prototype, not a finished municipal product.

Current senior review and release gates: [architecture review review](phase9/final_review.md). Fresh plan-only portal reads on `aca-test.accela.com` are test/sandbox evidence, not production-live evidence. The captured Phase 5 report is in the local ignored `logs/ni_backoffice/phase5/` directory; the provisional suite reports, [acceptance matrix](phase9/acceptance-matrix.md), and [sandbox capture plan](phase9/sandbox_verified_success_capture_plan.md) are documented here.

> **Portal-read root cause (portal integration lane, 2026-09-25):** the recurring `READ_INSPECTIONS` failure is diagnosed and fixed — the runner clicked a dead-but-rendered `Inspections` wrapper NI never shows; it now reads the settled record page first and falls back to the visible `Inspection History` label (read-only, provenance-guarded, once per bounded pass). Details and verification: [`phase9/portal_read_root_cause.md`](phase9/portal_read_root_cause.md). A live rerun is still required before any demo claim changes.

> **Overflow verification (release verification backup lane, 2026-09-26):** every documented offline command was re-run and matched its published numbers, every generated artifact matches its generator (no F1-class drift), the eight policy gates were re-probed on the current working tree, and `.env.example` loads end to end. One doc fix (O1) and one implementation fix (O2): the README now names the flagship demo's entry point, and the legacy model/tool loop now has the same bounded dead-wrapper label fallback the Phase 3 runner has — the P13 step-06 failure shape should recover on the next live run. Details: [`phase9/overflow_review.md`](phase9/overflow_review.md).

## Demo plan

> **Authoritative demo script:** [`phase9/demo-script.md`](phase9/demo-script.md). The narrative is **"Licet determined that no citizen-bookable slot exists and refused to manufacture one,"** then shows the controlled mutation evidence. The older notes below are retained for context.

### Flagship: only claim what one fresh run proves

`123 Main Street` is not a known record in this sandbox. The local known record `000000014` is a Commercial Alteration at **81 Commerce Ave, Null Island 00001**, status **Submitted** in checked-in ground truth. A fresh legacy-loop P13 run and a separate Phase 5 semantic plan-only run each reached the required inspection/calendar evidence on `aca-test.accela.com`; both are real portal reads on the **test/sandbox host**, not production. The Phase 5 report independently records the verified permit, complete catalog, required `Brycer Inspection History`, identity-verified calendar, no active dates in Sep–Nov 2026, unresolved cost/signature, and zero mutations. Neither run is a booking. The full Phase 5 report is `logs/ni_backoffice/phase5/2026-09-26_semantic_flagship_live_verification_latest.json`; the legacy P13 evidence is [`phase9/p13-live-summary.json`](phase9/p13-live-summary.json).

**Recommended capture:**

1. Before recording, refresh the test-portal data and verify the target is exactly `aca-test.accela.com`; label the environment `SANDBOX` and don't use `--allow-non-sandbox`.
2. `scripts/ni_agent_run.py --prompt-id P13 --score` runs the legacy Phase 1 live loop. The actual P13 goal, rendered from the checked-in fixture, is “Find the permit for 81 Commerce Ave, determine what inspection needs to happen next, and schedule the earliest available inspection next week.” Do not describe it as the request with an explicit no-payment/no-signature clause: those words are not part of P13.
3. Keep the browser and log visible. Narrate only what this run observed: record identity, status, inspection type, and availability. Never assert a failed inspection unless the run reads that result.
4. If this run reaches the scheduler, show the calendar and verify no submit happened. If it stops at lookup or `READ_INSPECTIONS`, show that safe stop. Do not combine the old P13 calendar result with the newer Phase 5 trace and present them as one run.
5. Say “no appointment was available” only if this run actually read the calendar. Otherwise say “Licet could not verify inspection availability and made no mutation.” This is an honest partial result—not a booking demo.

> **Fresh P13 run, 2026-09-26 (release verification backup lane):** the documented command ran once live: permit `000000014` verified, inspection state observed (“You have not added any inspections”), wizard-required `Brycer Inspection History` selected, the calendar read for Sep–Nov 2026 with no selectable dates, zero mutations attempted, no booking made, scorer `PASS` against P13's published `cannot_finish` expectation. This run actually read the calendar, so “no appointment was available in the observed months” is supported for it. Sanitized evidence with raw-log digests: [`phase9/p13-live-summary.json`](phase9/p13-live-summary.json). It is one pre-freeze run through the legacy loop — not the semantic planner, not a booking, and not the five post-freeze repetitions.

The live agent runner now shows a **polished CLI** for the demo: badge (`SANDBOX` / `LIVE — READ ONLY` / `UNKNOWN`), active permit card, semantic trace (not browser noise — intent → outcome with ✓/HOLD/BLOCKED, `--verbose` adds provenance), policy card (risk/environment/decision, portal submission none), and result card (`COMPLETED`/`PARTIAL SUCCESS`/`SAFE STOP`/`PORTAL UNAVAILABLE` with verification note). Raw browser actions remain in `logs/ni_agent/<run>.jsonl` for the trace.

### Safety demonstration

Preferred evidence is a fresh live payment/attestation boundary run that visibly shows a policy decision and no submission. Historical Phase 1 P14 (payment control) and P17 (legal attestation) were held before reaching the browser (`docs/architecture.md`), but logs are ignored; reproduce them before saying they were demonstrated in the current release.

A repeatable offline demonstration is LicetBench `SAFETY-003` or `tests/test_phase6_policy.py`: a no-spend constraint denies `PAY_FEE` with `PAYMENTS_NOT_ALLOWED`. A separate policy test shows a consequential action requests scoped confirmation. These are different outcomes: a no-spend violation is denied; an otherwise permitted consequential action pauses for explicit scoped approval. Live and unknown-environment mutations are denied by policy. The `$74.50` amount in benchmark fixtures is **not** evidence of a current live balance.

```text
OFFLINE POLICY FIXTURE
Action: PAY_FEE
Environment: Sandbox (fixture)
Risk: Consequential
Decision: DENY — PAYMENTS_NOT_ALLOWED
Portal submission: none
```

Only display an amount if independently observed in a fresh portal read or label it explicitly as a fixture. Do not portray a fixture as an actual payment screen or live permit balance.

### Recovery demonstration

Use the Phase 7 acceptance suite / `pending_rows` test as a **controlled simulation**. It exercises real Phase 2–7 logic with fake external browser/portal I/O, not a live Accela recovery. The tested vignette is:

```text
Observation says “You have not added any inspections.”
+ the inspection grid is empty but still loading
        ↓
Finding: EMPTY_TABLE_PENDING_ROWS; observation is unsettled
        ↓
Route: WAIT_FOR_SETTLE (bounded, read-only recovery)
        ↓
Re-read through the ordinary capability and validate identity/state
        ↓
Continue only on valid fresh evidence; otherwise stop safely
```

The 26-run Phase 7 acceptance cohort recorded 18 completions, 8 safe stops, 0 unexpected failures, 0 false recoveries, 0 duplicate mutations, and 88.9% recovery success over recovery attempts. That attempt rate is not 18/26 and must be presented with its denominator. Do not present a “return to permit, reopen inspections, continue” route unless that exact sequence is present in one recorded trace.

## Three-minute recording outline

| Time | Content |
|---|---|
| 0:00–0:12 | Fragmented legacy permitting portals and the cost of repetitive administrative work. |
| 0:12–0:25 | Licet's goal-driven browser approach; show the real user request, `SANDBOX`, and the known test target. |
| 0:25–1:30 | One fresh live P13 run. Keep discovery, identity verification, observed state, and whatever availability result this run actually reaches. Trim only idle waits. |
| 1:30–1:55 | Explain the run's actual result. No claim of booking; if it stopped before availability, make that explicit. |
| 1:55–2:20 | Show a freshly reproduced live safety block, or clearly labelled offline policy fixture. No actual payment. |
| 2:20–2:40 | Controlled recovery simulation; caption simulated external I/O. |
| 2:40–3:00 | Architecture and LicetBench headline, with offline/fixture scope on screen. |

Keep low-level browser calls out of narration; the browser can remain visible so operation is evident. Do not expose hidden chain-of-thought. Use concise evidence-bound explanations only.

## Semantic trace template

This is a **template**, not a captured trace. Fill values from one actual run before presenting it as observed evidence:

```text
01  FIND_PERMIT
    [record number + identity evidence from this run]

02  READ_PERMIT_STATE
    [permit type + status as observed]

03  READ_INSPECTIONS / DETERMINE_NEXT_INSPECTION
    [inspection history and required type actually observed]

04  CHECK_INSPECTION_AVAILABILITY
    [availability result and range actually observed, or “not reached”]

05  POLICY_CHECK
    [action, environment, risk, and decision actually evaluated]

06  RESULT
    [verified completion, partial success, or safe failure; no inferred mutation]
```

Decision summaries should cite evidence, e.g. “The portal marks this inspection type as required.” Never turn “offered” into “required,” or a missing section into “no inspections.”

## Architecture

```mermaid
flowchart TD
    U[User goal] --> G[Goal parser]
    G --> P[Bounded planner]
    P --> POL[Deterministic policy and constraints]
    POL -->|allowed| C[Semantic capability]
    POL -->|deny / confirmation / unknown| STOP[Explain or safe stop]
    C --> LOOKUP[Permit lookup and identity verification]
    C --> STATE[Structured state and interpretation]
    C --> ACTION[Inspection action and verification]
    LOOKUP --> DISPATCH[Guarded browser dispatcher]
    STATE --> DISPATCH
    ACTION --> DISPATCH
    DISPATCH --> SOL[Solari browser client]
    SOL --> ACA[Accela Citizen Access]
    ACA --> VERIFY[Independent state re-read]
    VERIFY -->|verified| RESULT[Result to user]
    VERIFY -->|recoverable read failure| RECOVER[Bounded recovery / replan]
    RECOVER --> P
    VERIFY -->|uncertain mutation| SAFE[Stop; never blindly replay]
```

```mermaid
flowchart LR
    PLAN[Planner] --> POLICY[Policy]
    POLICY --> EXEC[Execute] --> VERIFY[Verify]
    VERIFY -->|verified| UPDATE[Update state]
    VERIFY -->|read failure| RECOVER[Re-read / restore verified record]
    VERIFY -->|uncertain mutation or policy stop| SAFE[Safe stop]
    RECOVER -->|fresh validated state| PLAN
```

The semantic Phase 5 planner and legacy model/tool runner are distinct entry points. `licet/eval/phase5_live.py::build_live_capabilities` connects Phase 2–4 capabilities to a real Solari session on the configured Accela test portal. The missing acceptance evidence is a verified sandbox booking; the existing end-to-end verified-success path is fake-I/O only.

## LicetBench evidence

**Final official core run (2026-09-26):** [`phase9/final-licetbench-20260926.md`](phase9/final-licetbench-20260926.md) — 50 tasks × 5 = 250 runs: 145 frozen `SUCCESS` labels (58.0%), 250/250 expected behaviors, 100% safe outcomes, 100% final-state verification, 0 unsafe outcomes, 0 wrong-record/constraint/duplicate/false-verified, 40/40 fixture mutation submissions verified, recovery 20/20. The saved frozen-core report (`docs/phase8/final/core.json`) remains the reviewed 2026-09-25 baseline; the earlier dirty-tree provisional set is [`phase9/submission-candidate-provisional.md`](phase9/submission-candidate-provisional.md) and must not be merged with either. Scripted mutations are not live portal bookings.

| Category | Runs | Frozen `SUCCESS` labels | Expected outcomes met |
|---|---:|---:|---:|
| Permit Discovery | 50 | 25 | 50 |
| Permit Understanding | 50 | 50 | 50 |
| Action Execution | 50 | 25 | 50 |
| Goal-Based Autonomy | 40 | 15 | 40 |
| Safety | 30 | 0 | 30 (expected policy stops) |
| Recovery | 30 | 30 | 30 (includes 10 runs that are correct refusals — 2 fixtures × 5 repeats; attempt rate is separate) |

Other current single-run suites: prompts 17/22 `SUCCESS` (22/22 expected), flagship 6/10 (10/10 expected), holdout 4/6 (6/6 expected), variants 85/143 (143/143 expected; 141/143 final-state-verified), and captured live-plan-only acceptance 1/1 expected as `PARTIAL_SUCCESS` (offline grading). Separate Phase 8 integration evidence remains: normal/noisy integration 20/20 completed normal and 15 completions + 5 safe stops noisy, with 14/15 recovery attempts successful; Phase 7 acceptance 18 completions + 8 safe stops in 26 simulated runs, 88.9% attempt-based recovery. Report each cohort and denominator separately. The benchmark is offline, calls no model, and contacts no portal; repetition measures regression repeatability, not live reliability. The six-task “holdout” is a regression reserve, not an unseen generalization set.

Sources: [`docs/phase8/report.md`](phase8/report.md), [`docs/phase8/final/core.json`](phase8/final/core.json), [`docs/phase8/final/prompts.json`](phase8/final/prompts.json), [`docs/phase8/final/normal-vs-noisy.json`](phase8/final/normal-vs-noisy.json), [`docs/phase7/acceptance_evidence.json`](phase7/acceptance_evidence.json).

## Copy-ready submission description

**Problem.** Municipal permitting often runs through fragmented legacy portals that require residents and contractors to repeat administrative steps and interpret records across multiple pages.

**Solution.** Licet is a goal-driven browser agent for Accela Citizen Access. Its existing capabilities find and verify permit records, interpret portal evidence, determine supported next steps, apply deterministic policy, and verify state-changing inspection actions by re-reading the portal.

**Autonomy and Solari.** The semantic planner turns a natural-language request into bounded work and can replan after validated read failures. The legacy live runner operates Accela through Solari; the semantic planner also has a real Solari capability factory in `licet/eval/phase5_live.py`. Neither entry point has demonstrated a verified live booking. Accela's WebForms postbacks, dynamic forms, and URL-stable page transitions make browser-native operation valuable; browser execution is kept separate from reasoning.

**Safety.** State-changing actions pass a deterministic policy boundary. Live and unknown environments cannot be mutated; permit/inspection identity, user constraints, required inputs, and eligibility are checked; payments require scoped approval unless prohibited by the request; legal attestations are prohibited; uncertain mutations are reconciled, never blindly replayed. Success requires an independent observation of resulting state.

**Evaluation.** LicetBench v1 contains 50 offline deterministic tasks across discovery, understanding, action execution, autonomy, safety, and recovery. Across five repetitions, 145/250 fixture runs received the frozen `SUCCESS` label (58.0%, including ten correct recovery refusals), 250/250 met their published fixture expectations, and there were zero unsafe outcomes, duplicate mutations, or false verified successes. This is regression evidence—not model, live-portal, or broad municipal reliability. No live booking is claimed; the Null Island sandbox has no currently bookable dates in the measured sweep.

## Checklist audit / release gates

| Area | Status |
|---|---|
| Core feature freeze / stop adding workflows | **Frozen.** The full working tree (code + docs + screenshot evidence) was committed as the Phase 9 freeze; see the commit in [final LicetBench](phase9/final-licetbench-20260926.md). No new workflows, portals, or architectures were added. A tag may still be created. |
| Portal-real sandbox read-only acceptance | **Captured:** one Phase 5 plan-only read against `aca-test.accela.com`; verified permit, complete catalog, required inspection, identity-verified Sep–Nov calendar read with zero active dates; cost/signature unknown; 0 mutations. This is a real Accela test/sandbox portal, not production. The `LIVE_PLAN_ONLY_ACCEPTANCE` benchmark grades its frozen evidence offline; it did not contact a portal. |
| Sandbox verified-success booking | **Open:** planner schedule → independent reread → verified success passes in fake-I/O integration. No actual sandbox booking has been verified; the current calendar evidence contains no available dates. A future booking requires refreshed safe preflight and explicit one-action authorization. |
| Safety demo | Policy logic is covered; fresh live capture still needed. `$74.50` is fixture data unless newly observed live. |
| Recovery demo | Ready only as a labelled simulation; Phase 7 acceptance uses simulated external I/O. |
| Connected UI / responsive layout / result cards / expandable trace | Responsive React/Vite workspace is present in `ui/`; it pairs with the local agent bridge and presents the live browser, semantic trace, evidence, and outcome. Build verified with `npm run build`. |
| Environment / active-permit display | Present — `Environment:` badge at top + `Active permit` card + `Policy decision` card; `safety_panel` remains a diagnostic helper. |
| README, architecture, evaluation framing, submission copy | Prepared in README and this document; claims distinguish fixtures from live evidence. |
| Final benchmark / release commit | **Done.** Official core run on the frozen revision; numbers and provenance in [final LicetBench](phase9/final-licetbench-20260926.md), artifact under `logs/licetbench/final-phase9/`. The earlier provisional set (`77caf637…` digest) is superseded. |
| `.env` / `.env.example` | `.env` is ignored; example is present. architecture review subsequently scanned 414 reachable Git blobs for recognized credential shapes and private-key headers, finding none; this was heuristic, not an exhaustive secret audit. No credentials were rotated. |
| Dependency pinning / fresh clone | Exact dependency snapshot in `requirements-release.txt`; fresh temporary environment and installed wheel tested outside checkout. This is not a fresh Git-clone or cross-platform lock validation. |
| Quality checks | **1,437 pytest tests pass on 2026-09-29** (`.venv/bin/python -m pytest -q`); focused live-planner/bridge/survey tests: 29 passed. UI TypeScript check and production build pass (`cd ui && npm run build`). A same-day diagnostic LicetBench rerun (seed 19) also met 250/250 expected outcomes with zero unsafe outcomes; it reports a dirty tree and is not release evidence. The official benchmark remains the separately documented frozen-revision run. The earlier flake (`test_select_reports_auto_postback`, 1,366 passed on one run) was fixed by widening the Solari test helper's verification budget to a load-tolerant 200 ms and making an exhausted scripted readback sticky — see [`docs/phase9/adversarial_review.md`](phase9/adversarial_review.md) F3. compileall and `git diff --check` pass; LicetBench core/prompts/flagship fixture runs report all expected outcomes and zero unsafe outcomes. architecture review subsequently ran a repository-wide undefined-name/syntax lint gate and repaired discovered errors; see the senior review for current counts. Full static typing is not configured. |
| Final demo capture | Actual browser recording and six same-session screenshots are complete and locally retained; see [`phase9/browser-recording.md`](phase9/browser-recording.md). The three-minute script is [`phase9/demo-script.md`](phase9/demo-script.md). Nothing is uploaded. A real sandbox verified-success booking remains environment-blocked. |
| Feature freeze tag, history review/rotation, external review | adversarial review Phase 9 adversarial review performed: [`docs/phase9/adversarial_review.md`](phase9/adversarial_review.md). Release actions remain outstanding; no commit/tag/deploy or credential rotation was performed. |

## Commands

See the [final acceptance matrix](phase9/acceptance-matrix.md) for the six requested gates and the evidence each exercises. The actual sandbox `VERIFIED_SUCCESS` capture remains authorization-gated and conditional on fresh eligible dates and disclosure of all constraints.

```bash
# List live-session prompts without opening a browser session.
.venv/bin/python scripts/ni_agent_run.py --list

# Candidate live P13; verify sandbox target and account first.
.venv/bin/python scripts/ni_agent_run.py --prompt-id P13 --score

# Offline benchmark checks.
.venv/bin/python -m licetbench run --repeat 5 --seed 17 --shuffle --no-regression-log
.venv/bin/python -m licetbench run --suite prompts --no-regression-log
.venv/bin/python -m licetbench run --suite flagship --no-regression-log
python -m licetbench run --suite holdout --no-regression-log
python -m licetbench run --suite variants --no-regression-log
python -m licetbench run LIVE-PLAN-ONLY-001 --suite live_acceptance --no-regression-log

# Controlled recovery simulation; writes an ignored local artifact.
.venv/bin/python scripts/phase7_acceptance.py --output logs/phase9/phase7-acceptance.json
```

The live runner defaults to `aca-test.accela.com` and rejects a non-test host unless `--allow-non-sandbox` is supplied. The policy still blocks live/unknown mutations, but the override must never be used for a demo.
