# Forward calendar search — 2026-09-28

Licet's live planner now follows the Accela calendar Next control when the displayed months have no suitable date. It checks record identity and explicit month labels on every page, rejects repeated/backward windows, excludes past dates, honors exact dates and date bounds, and stops after at most 36 windows. The execution adapter can relocate a selected future date after reopening the wizard, then rechecks that the day remains active before selecting its time slot.

## Horizon — 2026-09-30

The 12-window ceiling only ever saw about a year past today, which is why the citizen capacity query's first authenticated run reported Sep–Nov 2026 and said nothing about later months. The ceiling is now `MAX_CALENDAR_WINDOWS = 36` in `licet/eval/phase5_live.py`, shared by the query so the two layers cannot disagree, and the query takes an explicit `--horizon YYYY-MM` (default `2028-12`). Each window renders three month tables and `Next »` advances the strip by one month, so the query derives its per-type window count from the horizon. Stopping on the horizon month yields `requested_window_exhausted`, which is what lets a negative read mean "no active day through that month" instead of "the budget ran out". This raises scan breadth only: the loop still never selects a day, a time, or the confirm step.

The report preserves all observed months, number of windows read, search limit and stop reason. A search-limit stop does not establish anything about later availability. A navigation or identity failure is reported as unknown availability. No mutation guard, cost requirement or signature restriction was weakened.

## Live result

Run `phase5-live-20260928T064054270174Z-01dc22e8` searched permit 000000014, required type Brycer Inspection History, across 12 overlapping windows: September 2026 through October 2027. All 14 distinct months contained zero active days. It stopped at the configured search limit with PARTIAL_SUCCESS / NO_SAFE_ACTIONS and zero mutations. December and later months were actually read; the sandbox still offered no appointment in this horizon. A successful real booking remains unverified.

Raw run report and actual browser recording are in `logs/phase9-calendar-search-20260928/`. Capture was plan-only, used a 45-second browser-verification observation window and a 600-second semantic-step timeout to accommodate live portal latency. The recording begins after login and does not include request entry in Licet's UI; it is a separate recording from the 2026-09-28 demo package in [browser-recording.md](browser-recording.md).

Focused validation: 122 tests passed across live wiring, Phase 4 portal adapter, Phase 5 planner and integration, and capture observer. Tests include December discovery, year rollover, requested-date filtering, horizon/deadline stops, missing month captions, past dates, repeated windows, navigation failure, changed record identity, and future-date relocation before selection. Added 2026-09-30: paging past the old 12-window ceiling into 2028, stopping on a requested horizon month, horizon parsing, and the horizon-derived window budget.

For CLI live runs covering the full search horizon, pass `--timeout 600` to `scripts/ni_phase5_acceptance.py`. The legacy default per-step timeout may expire before a slow multi-window search completes; explicit timeout remains a hard stop. Later explanatory-report wording and future-date execution tests were completed after the recorded read-only run; that run exercised the forward availability scan, not appointment submission.
