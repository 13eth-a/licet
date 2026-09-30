# Phase 2 fix handoff, 2026-09-21

The parsing/ambiguity review's 43 offline checks now pass (previously 10/43).
See phase2_fixed_evidence.json; phase2_review_evidence.json preserves the baseline.
The full suite passed 468 tests; a subsequent stable cap-ID identity refinement
passed all 181 lookup tests. Phase 2 edits are in lookup.py, lookup_runner.py,
the shared fake fixtures, migrated lookup tests, and test_lookup_contracts.py.

Changes: nonoverlapping parser spans; preserved unit/locality/status constraints;
rejected unsupported alternatives/negation; hard contradiction exclusion and
unknown-field ambiguity; provisional CANDIDATE separate from verified FOUND;
fresh detail evidence; bounded retry and pagination contracts; failed/partial
results explicitly surfaced; cleared stale active permit; verified-only success
metrics; scoped HTML tables and stable earlier-page record URLs.

Remaining live issue: the read-only record lookup reached CapDetail but its Search
click returned action_outcome_unknown after the browser's verification deadline.
The runner correctly stopped without replay. This is not a successful live lookup.
Evidence: logs/ni_lookup/phase2-lookup-20260921T042904359883Z-5714a4a0/report.json
and its adjacent step log. The earlier sandboxed attempt failed to create a Solari
session. The live run with network access authenticated and reached the record.

Next investigation: capture the browser action-verification snapshots/busy markers
or implement bounded read-only reconciliation after an uncertain Search. Do not
blindly replay Search or clear the stop flag without new verified evidence. Detail
redirect handling itself passes offline. Address fields not independently visible
on the fresh detail observation remain identity_unverified. Broader detail-section
enrichment and live acceptance are still needed before claiming full portal coverage.

The user moved this task to Astra's Phase 3 reasoning assignment after this live
finding. No new Phase 1 browser fix or live-success claim is included in this handoff.
