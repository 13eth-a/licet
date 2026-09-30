# Cookbook transfer

Imported September 30, 2026 into `applications/licet` on `codex/licet-application`, based on the cookbook fork's existing main branch.

## Included

- All 362 Git-tracked or non-ignored project files from the current Licet working tree, including uncommitted updates.
- Python agent, CLI tools, inspection policies, adapters, tests, fixtures, LicetBench data, and evaluation reports.
- Connected React UI, private local account bridge, offline booking replay, package lockfile, and recording scripts.
- Design and architecture docs, phase reports, demo script, narration text, prepared evidence video, and screenshots.
- Finished September 29 and September 30 walkthroughs, both full and shortened versions, with selected screenshots under `docs/walkthroughs/`.

Only the imported README and ignore rules differ from the source project: the README links the latest UI and recordings; ignore rules also protect additional local environment files and dependency directories. Transfer documentation is new.

## Kept local

- `.env`, account credentials, API keys, and local agent/tool configuration.
- Raw `logs/` (portal captures, authenticated session artifacts, connection keys and temporary run outputs); selected reviewed walkthrough media was copied out explicitly.
- `.git/` and Licet's separate commit history; the import preserves the cookbook history without merging unrelated histories.
- Virtual environments, node_modules, caches, build output, and package metadata generated during installation.

No source files or original local materials were deleted.

## Validation

- All copied source files match the current local working tree except the two documented integration changes.
- Configured-credential and credential-pattern scan: no matches in transferred source files.
- Imported application, using the existing validated Python environment: `python -m pytest -q` → **1518 passed**.
- UI: `npm ci --offline` and `npm run build` passed from the imported directory.

The live recordings show safe stops with no appointment submission. The offline replay uses one injected slot and simulated browser I/O; its verified result is not a live booking.
