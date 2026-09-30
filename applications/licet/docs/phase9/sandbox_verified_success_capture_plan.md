# Conditional sandbox `VERIFIED_SUCCESS` capture plan

**Status:** plan only. No portal mutation has been authorized or attempted by this plan.

## Evidence baseline

- `aca-test.accela.com` is classified as `SANDBOX` by Licet's host classifier.
- The recent read-only Phase 5 run used permit `000000014` / key `NULLISLAND/Building/REC26/00000/000QB`, selected the catalog-required `Brycer Inspection History`, and found 0 active dates across the observed Sep–Nov 2026 calendar.
- The provider did not disclose scheduling cost or signature requirement. Those values are `unknown`, not zero/false.
- Existing successful Phase 5 end-to-end scheduling coverage uses a fake portal and a scripted date. It is not an actual sandbox booking.

Therefore the existing target cannot presently produce a truthful constrained `VERIFIED_SUCCESS` using the captured evidence. A later availability change or a different sandbox-owned record would need a new read-only preflight.

## Gate 1 — read-only discovery and preflight

1. Confirm the browser host is exactly `aca-test.accela.com` and Licet reports `SANDBOX`; stop if it is `LIVE_READ_ONLY`, `UNKNOWN`, or any other host. Never pass `--allow-non-sandbox`.
2. Use a sandbox-owned test permit and independently verify its display ID and stable record key before proceeding.
3. Read the full inspection catalog and establish which type is required (do not infer required from offered).
4. Read the calendar for the selected type, verify record identity again, and capture the exact selectable date(s) and displayed calendar horizon.
5. Establish cost = 0 and signature requirement = false from authoritative portal evidence for the action. Unknown values do not satisfy a no-payment/no-signature goal.
6. Confirm eligibility, required inputs, constraints, and a fresh preflight fingerprint for the exact permit/type/date.
7. If any check fails or is unknown, stop with no mutation. In particular, if no dates are active, do not invoke `--execute` or continue into scheduling.

## Gate 2 — explicit one-action authorization

Only after Gate 1 succeeds, present the user the exact action to authorize: sandbox host, permit display ID and stable key, required inspection type, selected date, observed zero cost, observed no-signature status, and consequence (submitting one inspection request). Wait for explicit authorization of that exact action. A general request to “plan capture” is not mutation authorization.

If authorization is not supplied, expires, or the proposal/preflight changes, stop and repeat read-only validation. No wildcard approval and no production override.

## Gate 3 — one submit, then independent verification

After explicit authorization:

1. Revalidate the exact host, permit, proposal, date availability, and preflight immediately before submit.
2. Submit once through the ordinary planner → policy → executor → Accela adapter path. Do not call low-level browser controls directly.
3. Never retry a timed-out/uncertain submit. Re-read portal state to reconcile it.
4. Independently read the same permit and inspection row after submission. Require matching permit identity, inspection type, scheduled date/status, and the product's `VERIFIED_SUCCESS` state before reporting success.
5. If the reread is ambiguous, mismatched, unavailable, or does not show the expected state, report unknown/failed verification and stop; do not replay.
6. Save the sanitized report, trace, safety audit, and artifact hashes. Do not include credentials or session material.

## Expected report if the full gate passes

```text
Environment: SANDBOX (aca-test.accela.com)
Permit: independently verified [display ID + stable key]
Inspection: portal-required [exact type]
Date: [available date observed before submit]
Cost/signature: zero cost and no signature requirement independently observed
Policy: allowed for this exact sandbox action after explicit authorization
Mutation: one submission
Verification: independent reread = VERIFIED_SUCCESS
```

This is a future capture specification, not present evidence. If the sandbox remains without dates or cannot disclose the no-spend/no-signature facts, record the safe stop and leave sandbox verified-success acceptance open. The fake-I/O integration remains useful test evidence but must be labeled as simulated.
