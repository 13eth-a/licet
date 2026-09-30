# Phase 3 reasoning contract — architecture review handoff

Version 1, 2026-09-21. Owner: reasoning/state interpretation. This completes the
architecture review design and interpretation portion of the supplied assignment. implementation owns
runtime schemas, deterministic rules, routing, validators, and evaluation; portal integration
owns extraction. This document defines semantic requirements, not competing
Python schemas. The accompanying prompt is `licet/agent/phase3_reasoning_prompt.md`.

## Boundary and inputs

Reason over a compact, structured, identity-verified permit snapshot. Never feed
the Phase 1 execution prompt or browser tools to this reasoning stage. No raw-page
reasoning and no action execution. Deterministic rules run before interpretation;
architecture review can qualify, connect, or reject their conclusions, but cannot invent facts.

The snapshot must carry:

- Stable record key (agency/module/cap IDs), verified lookup evidence ID, snapshot
  ID/version, question, and explicit `as_of` time and timezone.
- Facts with unique IDs, typed field/value, entity and scope IDs, raw value and
  exact supporting excerpt, section/URL, observation time, effective/event time
  when known, extraction confidence, and source kind. A model-generated fact
  cannot become a portal fact on round-trip.
- Section coverage: `not_requested`, `loading`, `partial`, `complete`,
  `explicitly_empty`, `unavailable`, or `parse_failed`. Completeness includes
  pagination, relevant filters and date range. An empty list is not coverage.
- Inspection lifecycle separately from inspection outcome. `COMPLETED` does not
  mean `PASSED`. Comments have inspection attempt IDs, their own dates, and
  linkage evidence. Unknown linkage remains unknown.
- Explicit requirements and prerequisite edges, if present, distinguished from
  offered inspection types, uploaded files, and navigable section labels.
- Conflicts and deterministic candidates with premise fact IDs and rule IDs.
  Include raw source evidence for these premises so bad normalization is visible.

Input reduction must retain competing evidence, relevant earlier/later attempts,
coverage gaps, required/optional qualifiers, and linkage IDs. Record any omitted
entities or truncated text; truncation prevents completeness-dependent claims.

## Result semantics

Use a structured `ReasoningResult`, conceptually containing:

| Field | Required meaning |
|---|---|
| `record_key`, `snapshot_id`, `question` | Bind every answer to its verified input |
| `answerability` | `answered`, `partial`, `needs_data`, or `conflicting` for this question |
| `claims` | Atomic FACT / INFERENCE / UNCERTAIN claims with stable IDs |
| `blockers` | Supported current impediments, with scope and classification |
| `next_actions` | Ranked recommendations, including preconditions and dependency IDs |
| `contradictions` | Competing facts, their scope, and what would resolve the conflict |
| `uncertainties` | Missing or conflicting premises and the claims they affect |
| `needed_sections` | Targeted read requests, their precise evidence question and stop condition |
| `execution_allowed` | Always false in Phase 3 |

Each claim carries `kind`, `statement`, `evidence_ids`, `confidence_band`, and
`reason_code`. Inferences additionally carry `premise_claim_ids` and a short
assumption/rationale. Uncertainty carries the missing premise and impact. These
are concise justifications, not a request for hidden chain-of-thought.

A FACT states what the source establishes. “The portal reports Issued” remains
true even if another section disagrees; “the permit is currently valid” does not
follow. A high-confidence extraction does not confer high confidence on an
inference. Confidence bands are policy labels, not calibrated probabilities.

## What counts as a blocker

Separate `confirmed_gate`, `observed_problem`, and `potential_impediment`.
The unqualified answer “X blocks Y” requires an explicit current gate or a
verified prerequisite edge to Y. Every blocker names its affected stage; unknown
scope stays unknown. Do not infer a global approval hold from a local issue.

| Evidence | Safe conclusion | Forbidden promotion |
|---|---|---|
| Active condition explicitly prevents issuance | Confirmed gate for issuance | Blocks all inspections too |
| Latest relevant inspection failed | Observed problem; failure is a FACT | Must immediately rebook; failure necessarily blocks issuance |
| $74.50 unpaid balance, no gate shown | Unpaid amount FACT; potential payment impediment | Payment required before reinspection |
| Fee due and unpaid, explicit “pay before issuance” | Confirmed issuance gate | Payment before every activity |
| Required document explicitly missing | Observed unmet requirement; gate only for supported stage | Any absent upload is a missing required plan |
| Pending review | Review pending FACT | Review is late or blocks unrelated work |
| Portal explicitly reports Expired | Reported expiration FACT; administrative problem | Date alone establishes expiration or legal ineligibility |
| Catalog offers Electrical Final | Possible type FACT | Required or next inspection |
| Section empty, loading, hidden, or unavailable | Coverage result | Permit defect or no outstanding work |

`resolvable_by_licet` must come from an implementation capability registry and
required authorization, never from the reasoning model's estimate. Corrections to
physical work are not a browser capability. `requires_confirmation` cannot grant
permission; a recommendation cannot override `execution_allowed=false`.

## Inspection history and next-action analysis

1. Group by verified requirement/scope (trade, phase, location, unit, inspection
   type) and attempt identity. Same display name alone does not prove same scope.
2. Order by effective completion/event time, not screen order or scrape time.
   Unknown timestamps, ties, mixed timezones, or missing pages prevent “latest.”
3. A later pass supersedes an earlier failed outcome only for the same verified
   requirement/scope. Preserve the earlier failure as history. It does not
   automatically resolve separate active conditions or fees.
4. A later scheduled attempt means reinspection is already arranged. Do not
   recommend duplicate scheduling. A cancelled attempt satisfies nothing and
   does not erase a previous failure. Cancellation alone is not failure.
5. If a correction is explicitly described, quote it and associate it only with
   that attempt. “Okay after correction” does not identify the correction.
6. A failed latest attempt supports a CONDITIONAL suggestion to address the
   cited correction and then consider/request reinspection. It does not prove
   corrections have been made, reinspection is mandatory, or scheduling is allowed.
7. Prefer known prerequisite edges. If no sequence is recorded, return multiple
   unordered options with that uncertainty; never choose the first catalog item.

Rank recommendations by explicit dependencies first, then known administrative
holds, failed prerequisites, explicit missing required documents, explicit
outstanding required inspections, payment gates, and informational issues.
The ordering is an explanation policy, not evidence that the highest-ranked item
is legally or operationally required first. Independent blockers can be parallel.
Ties remain ties. Every candidate states its affected stage, preconditions,
requirement strength (`required`, `likely`, or `possible`), and supporting IDs.

## Contradictions and time

Retain both facts until their relation is supported. A later observation timestamp
alone does not supersede an older effective event. “Issued” in overview and an
explicit expiration event yesterday requires a conflict/possible stale overview
flag unless the history also establishes a subsequent renewal. A configured past
expiration date on a Submitted record is not an expiration event.

Passed result plus “Correction Required” comment can mean a conditional pass, a
stale comment, or a parsing/linkage error. Preserve both, suppress readiness, and
request the matching attempt detail. Do not rewrite the result or invent a
conditional-pass definition. A later pass in a different unit does not resolve a
failure in this unit. A partial pass does not establish the scope that passed.

## Targeted retrieval and stopping

| Question | Minimum relevant evidence | Stop / ask for more |
|---|---|---|
| Current status | Current overview status and identity | Answer source-attributed status; fetch history only for a relevant conflict |
| Last failure / inspector comment | Relevant ordered attempts and linked comments | Unknown ordering/linkage -> targeted attempt detail/history |
| Unpaid fees | Current balance/payment observations and fee coverage | Do not read inspections to answer an amount |
| Outstanding inspections | Explicit requirements plus covered matching history | Missing requirements -> unknown, not catalog minus history |
| Missing plans | Explicit requirement plus matching document state | Missing catalog/requirement evidence -> unknown |
| Ready for next inspection | Named target, explicit prerequisites, current relevant holds, covered outcomes | Without target or prerequisite evidence, readiness is unknown |
| Why stalled / what next | Existing problem/gate evidence and relevant unresolved dependencies | Report known blockers now; retrieve only sections that could change the answer |

A full empty section supports “no entries shown in that section at this time,”
not a global “no blockers.” Availability is not completeness. Distinguish
`INSPECTIONS_NOT_FOUND`/`FEES_NOT_FOUND` (section unavailable) from a successfully
observed empty section. Parse failure -> `STATE_EXTRACTION_FAILED` or
`HISTORY_PARSE_FAILED`; contradictory relevant facts -> `CONFLICTING_RECORD_STATE`;
unmapped substantive status -> `UNSUPPORTED_STATUS`; missing premise ->
`INSUFFICIENT_EVIDENCE`.

Read requests are declarative (`section`, `entity_id`, `reason`, `needed_fact`,
`stop_when`), not selectors, clicks, or URLs invented by architecture review. the implementation’s read-only
adapter resolves them. One bounded retrieval per unchanged missing premise;
a repeated unavailable/unchanged observation returns a partial answer. A shared
budget applies across all sections. Do not force every permit through every tab.

## Publication gates for implementation

Before publishing model output, enforce in code:

1. Same verified record key and snapshot; reject foreign-record evidence.
2. Every cited ID exists; inference premises form an acyclic graph ending at facts.
3. Do not accept a FACT derived only from another inference. Raw snippets and
   values belong to the cited entity and section. Citation existence alone is
   not entailment; evaluate semantic support as well.
4. Required actions and confirmed gates need explicit requirement/gate evidence.
   A confidence score cannot substitute for it. Reject unsupported blocker claims
   and preserve a partial answer with an uncertainty, rather than silently dropping it.
5. Readiness/no-blocker claims need complete relevant coverage and no unresolved
   contradictory prerequisites. `answerability=answered` is question-specific.
6. Recommendations cannot produce browser tool calls. Runtime allows only
   observation/navigation actions vetted for retrieval; no scheduling, payment,
   resubmission, application submission, cancellation, or attestations.
7. A stale snapshot during retrieval invalidates dependent conclusions; merge only
   same-record observations with provenance, retaining competing values.

The narrative renderer uses only this validated result. It must preserve FACT /
INFERENCE / UNCERTAIN distinctions, evidence links and all simultaneous material
blockers. Do not regenerate new requirements while formatting.

## Evaluation contract

Use the worked decisions in `reasoning_cases.md` as interpretation oracles for
implementation/fixture generation's golden tests and the adversarial review’s adversarial review. Match atomic supported
claims, not prose. Score supported gate detection separately from observed problems
and potential impediments, so cautious language cannot conceal false positives.

- Unsupported blocker count must be zero on the golden set. Report numerator,
  denominator, and abstentions; zero emitted blockers is not successful recall.
- False-ready count must be zero. Abstention on incomplete/conflicting inputs is correct.
- Require evidence precision, gate precision/recall, latest-attempt correctness,
  contradiction recall, and requirement-strength correctness.
- Separate extraction errors from reasoning errors by running reasoning both on
  verified golden structured inputs and extracted inputs.
- Report test-set size and coverage; passing synthetic cases does not establish
  a production hallucination rate of zero.

No Phase 3 runtime existed at review time. This contract and prompt are complete;
runtime integration and its final review remain dependent on implementation/the portal integration’s work.
