# Browser action verification

The client now distinguishes provider acceptance from an observed interaction.
`click`, `type_text`, and `select` observe before dispatch, issue the action once,
and poll live DOM state afterward. Verification requires two matching non-loading
observations, within a configurable deadline (10 seconds by default; live Solari frame reads can exceed 3 seconds). The deadline
also bounds a stalled observation or read-back call.

- Clicks require a change in URL, frame inventory, visible text, live form values,
  checked state, document replacement, or semantic section/dialog attributes. Focus alone does not count.
- Typing requires the requested value to survive read-back. Masked numeric fields
  compare digits to allow date/phone formatting. Mismatches never cause text to be
  appended a second time.
- Native dropdowns resolve a unique enabled option before dispatch. Read-back must
  match that option's actual value, including when the displayed label differs.
  Controls are resolved again during polling to account for postback replacement.
  For controls declaring a WebForms postback, matching the value is insufficient:
  verification also requires a new document or a completed ASP.NET AJAX request.
  Live validation caught a new value appearing before the dependent form changed.
- Timeouts and detached controls are reconciled through observation. They never
  trigger force-clicks, repeated clicks, or a second dropdown selection by value.

If verification fails, the result is `action_outcome_unknown`, with
`retryable=false`. This means the action may have reached the server. The dispatcher
marks the run as requiring reconciliation, and the planner stops even when its
response contained additional tool calls. Standalone client consumers must likewise
inspect the result and reconcile portal state before dispatching another mutation.
There is no automatic resume protocol in this patch.

Successful results carry `data.verification` with before/after state hashes and a
`recovered_after_error` flag. This verifies an observable interaction, **not** that
an application was submitted or an inspection booked; those outcomes still need
workflow-specific evidence. An intentional no-op click or a download/new-window
interaction without a tracked page change remains unverified. Popup lifecycle
tracking and configurable action-specific postconditions remain follow-up work.

This patch does not replace `settle()` in navigation/login or recon scripts, change
the navigation allowlist, or unify the browser result contracts. Click/type/select
verification itself uses readiness polling rather than `settle()`'s fixed sleep.
The observations check document readiness, ASP.NET async postback activity, ARIA busy state, and visible
loading text; agency-specific loading indicators may require additional predicates.

Regression tests cover changes before timeouts, unchanged and loading pages,
postback replacement, frame-local transitions, wrong read-back values, disabled
options, observation failures, and stopping a batch after an uncertain action.
Run `.venv/bin/python -m pytest -q` from the repository root.

Test-stability note (2026-09-20): the postback-select test that waits for a
*delayed* document replacement originally scheduled that replacement with a
5 ms sleep against a 20 ms verification deadline — a wall-clock race that failed
intermittently under full-suite load. It now flips `document_id` on the second
observation poll (event-driven), so the test verifies the same behavior —
select succeeds only when the postback completes — with no dependence on
machine speed.


## Live validation, 2026-09-20

The existing dispatcher replay passed 16/16 checks. The new
`scripts/ni_browser_validation.py` then completed 10/10 consecutive read-only
search → known record → inspections workflows in one authenticated session.
Report: `logs/browser_validation/phase1-live-20260920T231707000683Z-af5c1514/report.json`.

Live testing required additional fixes: exact-title targeting, considering visible
matches rather than hidden duplicates for ambiguity, concurrent frame reads,
Patchright page-world access to ASP.NET state, and requiring actual postback
completion for auto-postback dropdowns. The ten-run workflow used a bounded
10-second verification timeout. Prior failed attempts were preserved.

A separate controlled timeout after a real search click sent exactly one click
and safely stopped unverified at the deadline. It did not recover to success;
slow-transition recovery remains a limitation. Report:
`logs/browser_validation/phase1-live-20260920T231812880188Z-73c58a1e/report.json`.
