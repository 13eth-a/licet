# Clean freeze checklist (historical preparation)

> Superseded status: the Phase 9 source was frozen at `9a6a424810afcf887cd6507a7517b2b5378b5701`, and the official benchmark ran clean. See [final LicetBench](final-licetbench-20260926.md). The checklist below preserves the pre-freeze procedure; its original status is historical.

**Current status:** not clean and not frozen. The current benchmark artifacts are explicitly provisional: `commit_dirty=true`, HEAD `4ae0566aa04b886e16c97ab0bd0753c816781902`, source digest `77caf637335833f34b0b01a3bdbc9b24c17736db5398cdcf8fdfe9e6c6776388`.

The repository started this work with many dirty Phase 9 edits from other work. Do not use `git stash`, reset, checkout, clean, commit, or tag to create a superficially clean tree: that could discard or publish other contributors' changes. The final acceptance changes must first be reviewed and integrated into a deliberate release snapshot with the owner's knowledge.

## Freeze sequence

1. **Review scope and ownership**
   - Inspect `git status --short` and `git diff` (including untracked files) before choosing the release snapshot.
   - Separate this Phase 9 work from pre-existing modifications; include only reviewed, intended paths.
   - Confirm `.env` and credentials remain untracked/ignored; do not print or copy secret values.
2. **Validate the candidate**
   - Run full pytest, compileall/lint checks available in the project, and all six offline matrix gates in `acceptance-matrix.md`.
   - Run core (5 repeats with recorded seed/order), prompts, flagship, holdout, variants, and live-plan-only offline grading on the exact candidate snapshot.
   - Save JSON and CSV reports, source digest, task digests/manifests, Python/dependency versions, and dirty status. The live-plan-only task is a frozen evidence grader, not a portal test.
3. **Resolve portal acceptance**
   - Current portal-real safe-stop is on `aca-test.accela.com` (SANDBOX), not production.
   - Current real sandbox booking is not verified. A booking capture stays conditional on a refreshed safe preflight and explicit one-action authorization; no date/cost/signature may be guessed.
   - If no bookable date or authoritative no-cost/no-signature evidence exists, mark the gate open and do not call the candidate dual-acceptance complete.
4. **Freeze and tag**
   - After the owner approves the exact reviewed snapshot, run the full checks on the clean committed revision, verify `commit_dirty=false`, and rerun benchmarks so hashes correspond to that revision.
   - Create a release tag only with separate explicit authorization. Tagging is intentionally not performed by this checklist.
5. **Final package**
   - Update official numbers only from clean-revision reports. Preserve the Phase 8 and provisional dirty-tree reports as distinct historical artifacts.
   - Capture demo/video only after evidence and environment labels are reviewed; say `SANDBOX` for `aca-test.accela.com`.

## Clean-state requirement

A clean working tree cannot be safely manufactured by this agent from the present shared dirty state without deciding which existing user edits to include or isolate. This document prepares the release gate; it does not claim the tree is clean or the project frozen.
