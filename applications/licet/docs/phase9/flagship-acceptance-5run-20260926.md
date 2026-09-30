# Real-portal flagship acceptance — 5 fresh runs (2026-09-26)

The redefined real-portal acceptance criterion (see
[acceptance matrix](acceptance-matrix.md)) run five times in fresh sessions.
**Result: 5/5 PASS.**

## Setup

- **Entry point:** `scripts/ni_phase5_acceptance.py` (real semantic planner;
  Solari browser + Accela sandbox; no shortcut around the product path).
- **Goal:** *"Get permit 000000014 ready for its next inspection without paying
  anything or signing anything."*
- **Mode:** plan-only (no `--execute`). Mutations are held behind an approval
  pause the harness never resumes, so zero mutation is structural.
- **Environment:** `aca-test.accela.com` (Licet classifies it `SANDBOX`).
- **Attempts per invocation:** 1 (each invocation is one fresh session; no retry).
- One characterization run preceded the five official runs; it was not counted.

## Pass criterion and result

| Check | Target | Result |
|---|---|---|
| Correct permit | `000000014` verified | **5/5** |
| Inspection reasoning | `Brycer Inspection History` identified | **5/5** |
| Supported types read | complete catalog (18/18 declared) | **5/5** |
| Calendar reached | opened + identity verified | **5/5** |
| Availability conclusion | no active dates, observed window Sep–Nov 2026 | **5/5** |
| Zero mutations | 0 attempted / 0 verified | **5/5** |
| Final status | `PARTIAL_SUCCESS / NO_SAFE_ACTIONS` | **5/5** |

## Per-run evidence

| Run | permit | reasoning | types | calendar | availability | mutations | final status | runtime | recovery |
|---|---|---|---|---|---|---|---|---|---|
| 1 | ok | ok | 18/18 | ok | ok | 0 | PARTIAL_SUCCESS / NO_SAFE_ACTIONS | 177.1 s | used |
| 2 | ok | ok | 18/18 | ok | ok | 0 | PARTIAL_SUCCESS / NO_SAFE_ACTIONS | 159.4 s | used |
| 3 | ok | ok | 18/18 | ok | ok | 0 | PARTIAL_SUCCESS / NO_SAFE_ACTIONS | 172.8 s | used |
| 4 | ok | ok | 18/18 | ok | ok | 0 | PARTIAL_SUCCESS / NO_SAFE_ACTIONS | 167.8 s | used |
| 5 | ok | ok | 18/18 | ok | ok | 0 | PARTIAL_SUCCESS / NO_SAFE_ACTIONS | 177.3 s | used |

Semantic trace in every run (7 steps, 7 useful, `model_fallbacks=0`):

```text
FIND_PERMIT → READ_PERMIT_STATE → DETERMINE_BLOCKERS → READ_INSPECTIONS
→ DETERMINE_BLOCKERS → DETERMINE_NEXT_INSPECTION → CHECK_INSPECTION_AVAILABILITY
```

`DETERMINE_NEXT_INSPECTION` selected the single evidence-supported target
(`Brycer Inspection History`, marked required in the wizard catalog);
`CHECK_INSPECTION_AVAILABILITY` then opened the calendar, verified identity, and
observed no active days.

## Determinism

All five report JSONs are **byte-identical**
(`30a8303e080519aadafb773eb996a78c7a5879d818f92113ddaf90a6c0ac8b9c`): the
semantic planner is on its deterministic path (no model fallback), so the runs
reproduce. `recovery` was exercised (the re-observe / label-fallback path) without
changing the outcome.

## Why this is a pass, not a failed booking

The 2026-09-26 capacity survey found no citizen-bookable capacity for any
record type reachable to the account in the surveyed configuration
([capacity findings](sandbox-capacity-findings.md)). These findings are dated and
should not be read as a guarantee against later tenant changes. For this recorded
run, verifying the permit, reasoning to the correct required inspection,
enumerating the full type catalog, reading the calendar, and stopping without
mutations was the **correct expected outcome**. The booking-mutation machinery is covered
separately by the controlled/fake-I/O evidence, which must never be labeled
"live".

## Evidence binding

Ignored local artifacts under `logs/phase9-acceptance/`:

- `official-run-1.json` … `official-run-5.json` — SHA-256
  `30a8303e080519aadafb773eb996a78c7a5879d818f92113ddaf90a6c0ac8b9c` (identical).
- `acceptance-5run-summary.json` — SHA-256
  `e6cf5e62599b6f25d6037c39097d0932f6788b00022a9b3c97a92f95f1ddf60b`.
- One `phase5-live-*/` run log per invocation.
