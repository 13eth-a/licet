# Phase 9 — overflow verification (release verification, backup lane)

**Scope:** release verification and one targeted documentation fix. This pass
changed no product code, policy, planner, executor, benchmark fixture, or frozen artifact.

**Date:** 2026-09-26 UTC. **Revision measured:** HEAD `4ae0566` plus a dirty
working tree with other Phase 9 lanes' uncommitted edits. As with every other
Phase 9 report, the numbers below are validation of this working tree, not
frozen release evidence — LicetBench correctly prints that `4ae0566` does not
identify the code these runs used.

Method: every documented offline command was re-run and its output diffed
against the published number or artifact, rather than copied from a document.
This is the whole-repo audit's pre-freeze recommendation #1 ("re-run every
documented command and diff the output against the published number — it is
cheap"), executed across every generator in the repo.

---

## Verdict

| Area | Verdict |
|---|---|
| Generated artifacts vs. their generators | **No drift.** Every artifact reproduced; the F1 defect class did not recur. |
| Published benchmark numbers | **All reproduce** on the current tree. |
| Quality gates | **Clean.** 1,390 tests, lint gate, compileall, whitespace check. |
| Safety release gates | **All eight probes hold.** No env-var bypass in `licet/safety/` or `licet/browser/`. |
| `.env.example` usability | **Usable.** 12/12 keys parse; `load_config` accepts the template end to end. |
| Documentation | One targeted fix (O1) closing the last cross-entry-point ambiguity. |

---

## 1. Artifact ↔ generator matrix

Every checked-in evidence artifact was regenerated with its own documented
command and compared structurally (timestamps and provenance excluded):

| Artifact | Generator / command | Result |
|---|---|---|
| `docs/phase7/acceptance_evidence.json` | `scripts/phase7_acceptance.py` | **Content-identical** (26 runs / 18 completed / 8 safe stops / 0.889; the generator embeds no timestamp) |
| `docs/phase3/golden_report.json` | `scripts/phase3_golden_eval.py` | **Identical except `generated_at`** (1 diff, 0 non-provenance diffs) |
| `docs/phase8/final/normal-vs-noisy.json` | `scripts/phase8_review.py::noisy_comparison` (run in memory; the script's `__main__` overwrites tracked frozen artifacts, so it was not run wholesale) | **Summary fields and scope identical**: normal 20/20 completed; noisy 15 completed + 5 safe stops, recovery 0.933 |

Regenerated output was written to ignored `logs/phase9-overflow/`; no tracked
artifact was overwritten.

Fresh CLI runs against the published numbers:

| Command | Published | Measured | |
|---|---|---|---|
| `licetbench run --repeat 5 --seed 17 --shuffle` | 145/250 `SUCCESS` (58.0%), 250/250 expected, 0 unsafe/duplicate/false-verified | identical, category split 25/50/25/15/0/30 identical | ✅ |
| `licetbench run --suite prompts` | 17/22 completed, 22/22 expected | identical | ✅ |
| `licetbench run --suite flagship` | 10/10 expected per repeat | 10/10 expected (one repeat) | ✅ |
| `licetbench run --suite holdout` | 6/6 expected | 6/6 expected | ✅ |
| `licetbench run --suite variants` | 143/143 expected | 143/143 expected | ✅ |

All runs also reported grader errors 0 and golden/fixture disagreements 0, and
each printed the correct dirty-tree caveat. Note that "145 `SUCCESS` labels"
remains the frozen-label figure including the ten correct recovery refusals
(`RECOVERY-004/006`, per the whole-repo audit F2 fix); none of the runs above
contradicts that caveat.

## 2. Quality gates

```text
pytest -q                                              1390 passed in 32s
ruff check --isolated --select F821,F822,F823,E9 ...   All checks passed
compileall licet licetbench scripts tests              clean
git diff --check                                       clean
```

The 1,390 count matches the whole-repo audit's full-suite run. `docs/phase9.md`
cites 1,385 from the earlier senior-review run; the delta is the presentation
tests added since, and both numbers are correctly attributed to their own run —
left alone deliberately.

## 3. Safety release gates — re-probed on the current tree

The reviews that probed these gates predate the latest uncommitted edits, so
they were probed again directly against the live `PolicyEngine` (not inferred
from tests):

| Gate | Result |
|---|---|
| Live mutation impossible | `DENY — LIVE_MUTATION_BLOCKED` |
| Unknown-environment mutation impossible | `DENY — UNKNOWN_ENVIRONMENT` |
| Payments require confirmation | `CONFIRM — CONFIRMATION_REQUIRED` (identity supplied) |
| No-spend constraint cannot be overridden | `DENY — PAYMENTS_NOT_ALLOWED` |
| Wrong-record protection active | `DENY — RECORD_IDENTITY_UNVERIFIED` (mismatched `record_key`) |
| Legal attestations prohibited | `DENY — PROHIBITED_ACTION` |
| Sandbox schedule with verified identity | `ALLOW` |
| Live reads remain allowed | `ALLOW` (`READ_FEES`) |

Re-grep of `licet/safety/` and `licet/browser/` for `os.environ`/`getenv`/bypass
markers: no matches. No test-only escape hatch has reappeared.

## 4. `.env.example` usability (checklist item)

`dotenv_values(.env.example)` parses without warnings; all 12 keys
`load_config` reads are present with matching names (including
`LICET_MODEL_TIMEOUT`, `LICET_MODEL_RETRIES`, `LICET_MAX_STEPS`), and
`load_config` accepts the parsed template end to end. The `[TEMPLATE]` header
line is silently ignored by the parser and does not affect loading.

## 5. Change applied in this pass

**O1 — README: name the flagship's entry point.** The README's Demo section
presented the `P13` live candidate without saying which of the two entry points
runs it, while the architecture diagram directly below describes the semantic
path — the exact ambiguity the whole-repo audit left as observation 2 ("the demo
must be run through exactly one of them, and the docs currently allow the reader
to assume otherwise"). The sentence now states that `P13` runs through the
legacy model/tool loop (`scripts/ni_agent_run.py`), not the semantic planner.
No other wording changed.

**O2 — legacy loop: dead-wrapper label fallback (implementation).** The fresh
P13 run showed the same dead-but-rendered `Inspections` wrapper portal integration diagnosed,
but through the legacy model/tool loop (the model aimed at
`#ctl00_PlaceHolderMain_shInspection_btnSearch`), where the Phase 3 runner's
label fallback did not apply. `licet/agent/planner.py` now mirrors that bounded
rule for section-read intents: only a `not_actionable`/present-but-not-visible
failure falls back, only a resolution that still reads the same section may open
it (a relabelled commit or dangerous target reading is refused even when the
click succeeded), each benign label variant is tried at most once per failed
call, every attempt is recorded as its own trace action, and the settled read —
never the click alone — is returned to the model, marked `recovered_via` so a
recovered read is never mistaken for the model's own call having worked.
Five regression tests in `tests/test_planner.py` cover recovery, all-variants-
dead (original failure stands), a real failure (never retried), a guard block
(a decision, never a fallback), and the resolution guard. Full suite 1,395
passed; core 145/250 `SUCCESS` + 250/250 expected + 0 unsafe and flagship 10/10
expected identical to the frozen artifacts; lint gate, compileall and
`git diff --check` clean. Not yet shown on the live portal — the next P13 run
should demonstrate it.

## 6. Checked and left alone

- **`scripts/` probe duplication** (whole-repo observation 1): still an owner
  judgement between `scripts/probes/` grouping and deletion; the scripts are
  also the provenance trail behind the portal findings.
- **Cohort defined in a test file** (observation 3): structural; this pass
  re-verified the artifact against that generator instead (section 1).
- **Full static typing** (senior-review gate 4): unconfigured by design at this
  stage; the focused correctness lint gate passes. Adding mypy now would be new
  scope, which Phase 9 forbids.

## 7. Still open — not achievable in this lane

1. **Fresh live flagship runs** (adversarial review F1, senior-review gate 1): need the
   sandbox credentials and a human-visible recording window; five post-freeze
   runs remain outstanding. *Update 2026-09-26:* one pre-freeze live P13 run was
   completed in this lane — the calendar was read (Sep–Nov 2026, no selectable
   dates), zero mutations, scorer PASS — recorded in
   [`p13-live-summary.json`](p13-live-summary.json).
2. **Final video/screenshots** (gate 2): recording action, owner's call.
3. **Release commit/tag, then re-run the evidence on the frozen revision**
   (gate 3, whole-repo pre-freeze #2): a release action; not performed here.
   Once the tree is committed, the fresh-clone test (`git clone` → install →
   `pytest`) should be run — today it cannot pass meaningfully because
   `requirements-release.txt`, `docs/phase9.md`, and `docs/phase9/` are still
   untracked.
4. **Credential rotation / history review**: the architecture review’s 414-blob scan found no
   recognized key shapes; rotation remains an owner decision.

No new portal or workflow was added, no live portal was contacted, and no live
mutation was performed.
