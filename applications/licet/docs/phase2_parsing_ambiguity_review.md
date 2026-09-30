# Phase 2 review: parsing, ambiguity, and architecture

Update 2026-09-21: this is the historical review. The 43 offline checks now pass.
See [fix handoff](phase2_fix_handoff.md) for changes and the remaining live
verification issue; [fixed evidence](phase2_fixed_evidence.json) records results.

Reviewed 2026-09-20. Scope: hard natural-language inputs, candidate disambiguation,
and the lookup/verification boundary. **Phase 2 should not be considered reliable
for automatic record selection yet.** The current implementation can select and
verify a record that contradicts the user's explicit record number.

This review adds an isolated probe script and evidence file. It does not rewrite
`lookup.py` or `lookup_runner.py`, which are developed separately.
Source SHA-256 hashes are recorded in the evidence; the sources did not change
during the probe run.

## Evidence and reproduction

- Existing Phase 2 tests: **129 passed**.
- Review pack: **43 checks; 10 passed, 33 failed**.
- These are deliberately adversarial checks, not an estimate of production failure rate.
- Runner cases use the project's fake browser client; no portal actions are performed.
- Case inputs, expected behavior, actual outputs, and source hashes are in
  [phase2_review_evidence.json](phase2_review_evidence.json).

Run from the repository root:

```sh
.venv/bin/python scripts/phase2_review_probe.py
```

Exit code 1 means a review invariant failed. This script intentionally sits outside
the passing pytest suite so other implementation work can consume the findings
without silently treating known defects as accepted behavior.

## Findings, in fix order

### 1. P1 — Contradictions can be outweighed, then certified as correct

`rank_results` adds points for matching fields but does not exclude contradictory
candidates. Two address components reach 0.80 even when an explicit record number,
parcel, applicant, ZIP, or permit type disagrees. Street number + ZIP + type can
reach 0.80 despite a different street.

**End-to-end reproduction A08:** request record `BLD-1` at `123 Main St`; return
candidate `BLD-2` at that address; open detail `BLD-2`. The runner returns `FOUND`,
sets `identity_verified=True`, increments success, and records `wrong_records=0`.
It verifies the selected candidate against the opened page, not against the
original requested record number.

Relevant code: [ranking](../licet/lookup.py), `rank_results` lines 693–728;
`resolve_lookup` lines 737–832; [runner](../licet/lookup_runner.py),
`_open_and_verify` lines 500–512. Evidence: R02–R09, A08.

**Required change:** evaluate each explicit constraint as MATCH, CONTRADICTION, or
UNKNOWN before ranking. A known contradiction is not compensable by unrelated
points. An exact ID plus conflicting address should produce an input/evidence
conflict for resolution. Preserve the original request through every fallback and
verify the final page against both that request and the selected stable identity.

### 2. P1 — `FOUND` is returned even when opening/verification fails

A01 returns `FOUND` with `selected=BLD-1` while `identity_verified=False` and
`open_error=record_mismatch: expected record BLD-1, observed BLD-2`. The public result
and success metric disagree with the actual outcome. Callers must know to inspect
mutable side-channel fields on the runner.

Relevant code: `lookup_runner.py` lines 338–348, 448–450, 467–517.

**Required change:** make candidate selection provisional. Emit final `FOUND` only
after identity verification succeeds, with the verified permit in that result.
Return typed mismatch/open/parse failures in the same result contract. Count
verified retrieval success separately from candidate selection and wrong-record
opens; do not count a mismatch as a successful retrieval.

### 3. P1 — Parsing loses field ownership, exclusions, and multiple alternatives

| Case | Observed behavior |
|---|---|
| `Find permit BLD26-00472` | Only `zip_code=00472`; no usable strategy. This is an actual live sandbox record format. |
| `permit 12345` | Becomes a ZIP, even though the user labels it a permit. |
| `parcel 123456789` | Also becomes a record number, and record search wins. |
| `BLD-2026-00123 in ZIP 57104` | ZIP becomes `00123`, taken from inside the record ID. |
| `Do not use BLD-2026-00123; find BLD-2026-00124` | Selects the negated first ID. |
| Two IDs joined by `or` | Silently selects the first. |
| `parcel 42 - 18 - 33` | Parcel is truncated to `42`. |

Independent unscoped regex searches explain the collisions. `re.search` also loses
cardinality: a scalar request schema cannot reveal that two entities were present.
Relevant code: `lookup.py` lines 475–537. Evidence: P01–P05, P10, P14–P16.

**Required change:** extract typed spans with source offsets before assigning
fields. Explicit labels take precedence; already-consumed spans cannot become ZIPs
or a second identifier type. Treat identifiers as opaque strings, preserving leading
zeros. Recognize the actual agency's formats. Negation and alternatives must be
represented or return a clarification result before browser access. A schema-valid
parse alone is not evidence that the user's intent was preserved.

### 4. P1 — Address matching can identify the wrong property or unit

R05 accepts `999 Main St Apt 123` for `123 Main St` because the house number is
matched anywhere in the address token list. R06 accepts Apt 5 for a request for Apt 4
because query normalization deletes the unit. P11 folds city/state into the street;
P12 treats a fractional house number as part of the street. P20 silently drops an
explicit issued-status constraint.

Relevant code: `lookup.py` lines 444–471, 496–499, 715–724.

**Required change:** represent street number, fractional/range portion, prefix,
street name, suffix, unit, locality, and ZIP separately. Dropping a unit from the
portal search form may be necessary, but it must remain a post-search identity
constraint. Preserve unsupported filters or reject them explicitly. Do not silently
broaden semantic intent when broadening the browser query.

Existing tests explicitly endorse dropping units and treating `permit 12345` as
invalid; revise those expectations, rather than merely adding more tests around
the existing behavior (`test_lookup_adversarial.py` lines 59 and 190;
`test_lookup_recovery.py` line 104).

### 5. P1 — Incomplete pagination can create false uniqueness

A03 supplies one matching row with a footer declaring two results, then limits
scanning to one page. The runner still returns `FOUND` and verifies that candidate.
Failed Next clicks and repeated footer detection similarly stop scanning without
propagating incomplete coverage. A missing applicant on a competing row is also
incorrectly treated as absence of a matching applicant (R14).

Relevant code: `lookup_runner.py` lines 272–310 and 408–420;
`lookup.py` lines 773–791 and 900–905.

**Required change:** return a candidate-set envelope containing scope, pages scanned,
total/parsed counts, completeness, and truncation/failure reason. A unique observed
row is not a proven unique match in an incomplete result set. Return INCOMPLETE or
AMBIGUOUS unless a scoped authoritative identity proves uniqueness. Missing row
metadata is UNKNOWN, not contradictory evidence.

### 6. P1 — The lookup runner can replay an uncertain browser mutation

A07 makes a select return `action_outcome_unknown`. The dispatcher sets the stop
reason, but the runner issues the same select again through the next fallback
attempt. This reintroduces the timeout/replay hazard fixed in the browser client
and planner.

Relevant code: `lookup_runner.py` lines 206–213, 391–395, 456–462.

**Required change:** propagate typed browser failures through the runner. An uncertain
mutation is terminal pending reconciliation, not an absent form or a zero-result
search. Test the entire client → dispatcher → lookup runner path. The runner's
`verification_timeout_ms=15000` argument is also currently ignored by the
dispatcher's select adapter; choose a single supported configuration boundary.

### 7. P1 — Address verification is based on copied search evidence

`_open_and_verify` supplies `selected.address` to `permit_from_page`, then treats the
resulting `permit.address` as the observed detail-page address. A05 verifies a
request for `123 Main St` even when the detail fixture says `999 Other St`.

Relevant code: `lookup_runner.py` lines 492–510;
`schema/extract.py` lines 55–79.

**Required change:** separate candidate evidence from freshly observed detail
facts. Preserve source page, field label, raw value, and observation ID. Missing
independent detail evidence must remain unverified, rather than being filled from
what the verifier expected to find.

### 8. P2 — Parcel “identity” and clipped scores break disambiguation

An exact parcel is scored 1.0 and treated as identifying a record, although one
parcel can have many permits. In R12, the correct commercial alteration and an
electrical permit both clamp to 1.0, so the requested type cannot distinguish them.
The applicant special case also bypasses `min_confidence`: R13 selects at 0.80 when
the caller's minimum is 0.95. Type matching accepts a generic `Commercial` label as
matching `Commercial Alteration` (R15).

**Required change:** distinguish property identity from permit identity. Apply
constraint compatibility before scores, preserve discriminating evidence without
saturation, and honor thresholds on every branch. Use explicit agency type aliases;
token-subset similarity alone is not type equivalence. These weights are heuristic
scores, not calibrated probabilities; do not present 0.98 as measured 98% certainty.

### 9. P2 — Runner lifecycle and budget contracts need tightening

- **A02:** a successful search that redirects directly to record detail returns
  `NOT_FOUND`/parse failure. This redirect was observed in all ten Phase 1 live runs.
- **A04:** `max_attempts=1` issues two searches because date widening sits outside
  the global submission budget.
- **A06:** after a successful lookup, a failed new lookup leaves the previous
  `active_permit` in state, without an explicit stale/query-scope marker.
- Row targets from earlier pagination pages are not used when opening: the runner
  clicks record text on the current final page. A selected earlier-page row may be
  absent. This last item is a source-review finding, not an executed probe.

**Required change:** support grid, explicit zero-results, direct-detail, and error
outcomes as distinct transitions. Enforce one global search budget. Scope or
invalidate active state at lookup start and only publish it after verification.
Retain stable row identity and its page/target so selection can be opened reliably.

## Proposed contract for the implementation owners

1. **ParseResult:** raw request, typed entity spans, normalized values, exclusions,
   unresolved references, and clarification/unsupported-field reasons.
2. **LookupRequest:** immutable semantic constraints, including units and explicit
   filters. A separate SearchAttempt records which constraints the portal can apply.
3. **CandidateSet:** structured rows, authoritative scope/identity when available,
   evidence provenance, pagination completeness, and search-attempt identity.
4. **Resolution:** per-constraint MATCH/CONTRADICTION/UNKNOWN plus a provisional
   candidate or ambiguity. Rank compatible candidates only; never silently choose
   an alternative from an unresolved request.
5. **VerifiedLookupResult:** final status, independently observed permit facts,
   identity checks, residual uncertainty, and structured error. `FOUND` means verified.

An optional model may propose a structured parse for difficult language, but it
should not receive browser tools. Validate source spans, competing entities, and
preservation of explicit constraints deterministically before any search. This
keeps Phase 2's planner limited to lookup and disambiguation.

Suggested division: parsing owner handles P01–P20; ranking owner handles R01–R15;
runner/state owner handles A01–A08 and the pagination/opening contract. Fix findings
1, 2, and 6 first because they permit a wrong record or replay despite existing
safety signals. Promote the review cases into ordinary regression tests as each
contract is corrected.

## Phase 1 validation handoff

The already-running read-only validation completed **10/10 consecutive** search →
record → inspections workflows in one authenticated Solari session. The earlier
existing dispatcher replay passed **16/16**. Evidence is in
`logs/browser_validation/phase1-live-20260920T231707000683Z-af5c1514/report.json`.

A separate injected timeout after a real search click dispatched exactly once and
stopped unverified after the 10-second deadline; it did **not** demonstrate successful
recovery. Its report is
`logs/browser_validation/phase1-live-20260920T231812880188Z-73c58a1e/report.json`.
This validates safe non-replay, while leaving slow-transition recovery as a remaining
limitation. These Phase 1 results do not establish Phase 2 retrieval correctness.
