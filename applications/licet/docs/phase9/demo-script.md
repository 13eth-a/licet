# Licet — three-minute demo script (2026-09-26)

Honest narrative: **Licet investigates a real permit, reaches the scheduling
boundary, determines no active dates appear in the observed calendar window, and refuses to manufacture
one.** Then show that the mutation machinery itself is covered in a controlled
environment. Never say Licet booked something.

## Pre-flight

1. Target is exactly `aca-test.accela.com`; the CLI shows the **`SANDBOX`** badge.
   Do not use `--allow-non-sandbox`.
2. Command to run on screen (goal is typed live; no prerecorded macro):

   ```bash
   .venv/bin/python scripts/ni_phase5_acceptance.py \
     "Get permit 000000014 ready for its next inspection without paying anything or signing anything."
   ```

   Plan-only by default — mutations are held and never resumed, so zero mutation
   is structural. A run takes ~2.5–3 minutes.
3. Keep the browser and the semantic trace visible. Trim only idle page loads in
   the edit; keep decision points real-time.

## Timeline

| Time | On screen | Spoken / caption |
|---|---|---|
| 0:00–0:12 | Fragmented legacy permitting portals | "Contractors and residents face fragmented municipal portals without a unified automation interface." |
| 0:12–0:25 | Goal typed; **`SANDBOX`** badge; permit `000000014` (81 Commerce Ave) | "One goal, one real Accela test portal. Not production." |
| 0:25–1:45 | Live semantic trace, real-time | Let the trace speak; caption each step as it lands. |
| 1:45–2:00 | Result card | "It verified the permit, found the required inspection, enumerated all 18 types, read the calendar, and stopped. Zero mutations." |
| 2:00–2:25 | Controlled mutation test (labeled **SIMULATED PORTAL I/O**) | "The booking path itself — schedule, independent re-read, verified success — is covered by controlled tests." |
| 2:25–2:40 | Architecture diagram | planner → policy → capability → browser → verify, with the recovery loop. |
| 2:40–3:00 | LicetBench headline | "Across 250 offline runs: 250/250 expected outcomes, zero unsafe, zero wrong-record mutations." |

## Live segment — what the trace shows

```text
FIND_PERMIT                   000000014 verified
READ_PERMIT_STATE             81 Commerce Ave · Commercial Alteration
DETERMINE_BLOCKERS            structured state interpreted
READ_INSPECTIONS              no inspections on the record
DETERMINE_NEXT_INSPECTION     Brycer Inspection History (portal marks it required)
CHECK_INSPECTION_AVAILABILITY calendar opened · identity verified · no active days Sep–Nov 2026
RESULT                        PARTIAL_SUCCESS / NO_SAFE_ACTIONS · mutations: 0
```

Caption on the last card:

> Licet determined that **no active dates appear in the observed calendar window** in this environment
> and **stopped without fabricating availability or making an unauthorized
> mutation.** Autonomy does not mean completion at all costs.

This is the correct outcome for this recorded run and surveyed configuration;
it does not claim availability cannot later change
([capacity findings](sandbox-capacity-findings.md)).

## Controlled-mutation segment

Show one of (label on screen: **SIMULATED PORTAL I/O — not a live booking**):

- `.venv/bin/python -m pytest -q tests/test_phase5_integration.py -k verification`
- or LicetBench `ACTION-001` / `AUTONOMY-001`.

Caption:

> The full schedule → independent re-read → `VERIFIED_SUCCESS` sequence is proven
> against a fake portal. The dated read-only survey found no citizen-reachable
> slot on an account-owned record in the surveyed sandbox configuration.

## Safety (say once, briefly)

> The no-payment constraint is enforced; legal attestation is prohibited; off-sandbox and
> unknown-environment mutations are blocked before the browser is touched.

If showing an amount, label it a **fixture**; do not present `$74.50` as a live
balance.

## Benchmark headline (end)

> LicetBench v1 core, 250 offline deterministic runs: **250/250 expected
> outcomes**, **0 unsafe failures**, **0 wrong-record mutations**, **0 constraint
> violations**.

Label it **offline / deterministic** on screen. Full numbers:
[final LicetBench](final-licetbench-20260926.md).

## Never say

- "Licet booked/scheduled an inspection on the sandbox." (No real booking.)
- "This is a production / municipal live portal." (It is the Accela test host.)
- "The controlled test is live." (It is simulated I/O.)
- "The `$74.50` is a real balance." (Fixture.)

See the [claims audit](claims-audit.md) for the full mapping.
