# Phase 3 architecture review — Astra

Reviewed 2026-09-21 against the current working tree. Phase 3 runtime classes,
extractors, and reasoning coordinator were not yet present. These are concrete
integration findings in the inherited implementation, not a review of nonexistent
Phase 3 code. Reproductions are in `current_architecture_evidence.json`: 10
focused checks, 9 unsafe or unsupported interpretations, 1 control passed.
This is not an estimate of production accuracy.

## P1: Catalog entries currently become requirements

`licet/schema/permit.py:243` computes missing inspections as schedulable types
minus every historical type. `licet/schema/extract.py:140` turns that list into
“schedule an inspection.” A catalog is not a required checklist. A failed or
cancelled historical entry also suppresses that type from this subtraction.

Luna: separate offered types, explicit requirements, and attempts. Deprecate this
method as a next-action source. Preserve an offered-but-unseen utility only under
a name that carries no obligation. Sequence and resolution require matching
scope, outcome, and chronology, as specified in the reasoning contract.

## P1: Status substring matches reverse meaning

`licet/schema/permit.py:82` and `:92` match substrings in ordered lists:

- “Not Approved” -> PASSED because “approved” is checked first.
- “Not Scheduled” -> SCHEDULED because “scheduled” is checked first.
- “Completed” -> PASSED although completion says nothing about outcome.
- “Corrections Required” -> UNKNOWN despite a failed/correction outcome.
- Permit “Not Issued” -> ISSUED; “Unexpired” -> EXPIRED; “Inactive” -> ISSUED.

Luna/GLM: use exact agency-supported labels after harmless normalization, separate
lifecycle/outcome vocabularies, and preserve unmapped values. Add explicit tests
for negation and positive controls. A new global fuzzy alias is not a safe fix.

## P1: Unattributed strings are promoted to portal facts

`Fact.provenance` defaults to PORTAL (`licet/schema/permit.py:108`); the requirement
coercer wraps bare strings as Fact. Thus “Unverified inferred claim” becomes a
portal fact without evidence. URLs alone also cannot link a conclusion to an
attempt or support its semantics.

Luna: unknown provenance must remain unknown. Require evidence IDs for important
portal claims; preserve section, entity, raw source, observation/event times,
coverage, and inference dependencies. Do not silently convert legacy strings into
verified portal facts. Version the compatibility adapter if needed.

## P1: The inherited planner prompt authorizes actions and guesses sequence

`licet/agent/prompts.py` requires exactly one tool call, tells the model to act
rather than describe, permits choosing the first offered inspection, and says
record questions can be answered from current page text within two or three
reads. These assumptions conflict with Phase 3's read-only structured reasoning
and evidence-dependent targeted retrieval.

Luna: keep the Phase 3 interpreter in a separate invocation with zero action
tools and the dedicated reasoning prompt. A system sentence saying “don't act”
is insufficient if the coordinator still accepts mutation tool calls. Route
needed_sections through a bounded, read-only retrieval adapter. Do not reuse the
Phase 1 prompt verbatim or put raw pages directly in the reasoning invocation.

## P1: Partial absence and diagnostics are stored as requirements

`licet/schema/extract.py:88` stores loading markers and truncated type lists in
outstanding_requirements. It also puts “no inspection history” there. These are
coverage/observation facts, not unmet permit obligations. Empty calendar samples
at `:127` become an agency-wide “no appointment dates” claim even though only
observed months were sampled.

Luna/GLM: move these to coverage/uncertainty. Scope calendar statements to observed
months and loaded coverage. Do not convert missing data into a blocker. Preserve
successful empty observations separately from unavailable or failed extraction.

## P1: A single normalized inspection status loses crucial dimensions

Current Inspection cannot distinguish lifecycle Completed from result Failed,
represent comment linkage provenance, or establish requirement/location scope.
Historical and current facts cannot be safely resolved merely by type/date.

Luna/GLM: add these fields before enabling the reasoning stage; use the contract's
unknown states when extraction cannot supply them. Date ties and partial history
must remain unresolved. Never infer scope solely from equal display names.

## P2: A single next_action and single-valued fields hide alternatives/conflicts

`apply_next_action` selects the first outstanding string if no missing-inspection
list exists. It cannot represent independent blockers, competing next steps,
conditions, dependency order, or confidence separately from provenance.

Luna: accumulate same-record observations without destructive overwrite. Preserve
contradictions and supersession evidence, then return structured candidates with
requirement strength, premises, preconditions, affected stage, and explainable
priority. Do not resolve conflicts with last-write-wins or maximum confidence.

## Handoff and readiness

Astra deliverables are complete:

- `reasoning_contract.md`: interface semantics, blocker taxonomy, temporal and
  contradiction policy, retrieval/stop rules, publication gates, evaluation rules.
- `reasoning_cases.md`: 30 worked interpretation decisions plus the flagship answer.
- `../../licet/agent/phase3_reasoning_prompt.md`: isolated structured interpretation prompt.
- `current_architecture_evidence.json`: current-code reproductions for the owners.

Luna owns schema/runtime integration and enforcement. GLM owns source-to-field
mappings and coverage/linkage. Solar can encode these decisions as fixtures;
DeepSeek should challenge claim entailment, not just citation existence.

Final Phase 3 integration approval is pending implementation, not implied by
completion of this reasoning handoff. Required acceptance evidence: golden-state
reasoning tests; extracted-state tests; foreign/stale record rejection; no mutation
capability; zero unsupported gate/required-action/ready claims on the evaluated
set; contradiction and blocker recall reported alongside abstentions. Review the
actual implemented coordinator after it exists; do not label this an end-to-end
Phase 3 pass.
