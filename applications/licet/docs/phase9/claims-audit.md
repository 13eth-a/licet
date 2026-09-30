# Claims audit — every claim mapped to one evidence class (2026-09-26)

Three evidence classes. A claim may cite more than one, but the wording must
name the class it rests on.

- **REAL ACCELA** — a real browser session against `aca-test.accela.com`
  (Licet classifies it `SANDBOX`; it is **not** a production municipal host).
- **CONTROLLED TEST** — product code driven by fake/simulated portal I/O.
- **AUTOMATED TESTS** — deterministic offline suites (pytest, LicetBench).

## Claim → evidence

| # | Claim | Class | Source | Accepted wording |
|---|---|---|---|---|
| 1 | Finds and verifies the requested permit | REAL ACCELA + AUTOMATED | 5/5 runs `000000014`; lookup tests | "verified permit `000000014` on the Accela test host (5/5)" |
| 2 | Reads and interprets permit state | REAL ACCELA + AUTOMATED | 5/5 `READ_PERMIT_STATE`; phase3 tests | "read and interpreted the permit overview" |
| 3 | Identifies the required next inspection | REAL ACCELA + AUTOMATED | 5/5 `Brycer Inspection History` (wizard-marked required) | "identified `Brycer Inspection History` as required" |
| 4 | Enumerates the supported inspection types | REAL ACCELA | 5/5 complete catalog, 18/18 | "enumerated the full 18-type catalog" |
| 5 | Reaches and reads scheduling availability | REAL ACCELA | 5/5 identity-verified calendar | "opened the calendar, verified identity, read Sep–Nov 2026" |
| 6 | Concludes no eligible slot and stops safely | REAL ACCELA | 5/5 `PARTIAL_SUCCESS / NO_SAFE_ACTIONS`, 0 mutations | "no active dates; stopped with no mutation" |
| 7 | Schedules an inspection and independently verifies the result | CONTROLLED TEST (+ AUTOMATED) | `test_phase5_integration`; LicetBench ACTION/RECOVERY fixtures | "schedule → independent reread → `VERIFIED_SUCCESS`, demonstrated with simulated portal I/O" |
| 8 | Payments / attestations are denied; live & unknown mutations blocked | AUTOMATED | phase6 policy + adversarial tests; LicetBench `SAFETY-*` | "policy denies payments prohibited by the user, requires scoped approval for consequential actions, prohibits legal attestation, and blocks off-sandbox mutation" |
| 9 | Recovers from controlled failures | CONTROLLED TEST + AUTOMATED | phase7 acceptance (18/26, 8 safe stops); LicetBench Recovery 30/30 | "bounded recovery in simulated I/O" |
| 10 | Benchmark performance | AUTOMATED TESTS | [final LicetBench](final-licetbench-20260926.md): 250 runs, 58.0% `SUCCESS`, 100% expected, 0 unsafe | "offline deterministic fixtures; 250/250 expected outcomes" |

## Must never be said

- "Licet booked/scheduled an inspection on the real Accela sandbox." — **No real
  booking exists.** The 2026-09-26 survey found no citizen-reachable capacity on
  account-owned records ([findings](sandbox-capacity-findings.md)); 0/5 runs mutated.
- "Licet ran against a production / municipal live portal." — **No production
  session exists.** All portal-real evidence is `aca-test.accela.com`.
- "The fake-I/O path is live." — The `VERIFIED_SUCCESS` sequence is **simulated
  I/O**; label it as such on screen.
- "$74.50 is an observed live balance." — It is fixture data, not a live read.
- "Recovery for a live Accela failure." — Recovery evidence is controlled
  simulation, not live portal recovery.
- "LicetBench proves live/model/municipal reliability." — It is offline and
  deterministic; it makes no model calls and contacts no portal.

## The one-sentence claim the evidence supports

> Licet is an autonomous browser agent that, on a real Accela test portal,
> verifies a permit, determines its required inspection, enumerates the
> scheduling options, reads availability, and stops safely without mutation when
> no active dates appear in the observed calendar window—and whose scheduling, reverification,
> policy, and recovery machinery is covered in controlled tests and offline
> benchmarks.

## Standing caveats

- **Frozen-code:** the official core benchmark ran on clean revision `9a6a424810afcf887cd6507a7517b2b5378b5701`; see the final LicetBench report. Later media/documentation edits do not change that historical evidence.
- **Capacity:** the 2026-09-26 survey is dated environment evidence, not current availability. The bounded recheck is `scripts/ni_citizen_capacity_query.py`; it must be run read-only and must not be described as portal evidence until it has actually run.
