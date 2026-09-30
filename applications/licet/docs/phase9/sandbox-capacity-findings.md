# Sandbox capacity findings — 2026-09-26

Read-only survey of the Null Island tenant, prompted by the flagship question:
*is a live booking reachable at all outside the empty Building calendar we have
been using?*

**Scope:** read-only. No citizen-portal session was opened; no record was
created, issued, scheduled, or submitted. Back-office navigation and grid reads
only.

**Tools:** new `scripts/ni_capacity_finder.py` (back-office Inspections portlet,
paged); existing `scripts/ni_type_catalog.py` (citizen `ddlGSPermitType` sweep).

## Previous understanding

`docs/phase4/checklist_audit.md` concluded, from the issued record `BLD26-00469`:
*"the sandbox has no appointment capacity configured — not for one type, and not
in any reachable month."* All eight owned records are `module=Building`, and the
horizon sweep only ever walked **Building** inspection types.

## What the survey found

The back-office **Inspections** portlet holds **7 scheduled inspections**, on
**2 records** — neither of them owned by Licet's test account:

| Record | Record type | Inspection | Scheduled | Notes |
|---|---|---|---|---|
| `FIR26-00018` | `Fire/Inspection/NA/NA` | Inspection (Required) | **09/30/2026** | future |
| `FIR26-00018` | `Fire/Inspection/NA/NA` | Inspection (Optional) | 09/03/2026 | past |
| `FIR26-00018` | `Fire/Inspection/NA/NA` | Reinspection (Optional) | 09/03/2026 | past |
| `FIR26-00018` | `Fire/Inspection/NA/NA` | Water Flow Inspection | 09/03/2026 | past |
| `FIR26-00018` | `Fire/Inspection/NA/NA` | Fire Alarm Inspection | 09/03/2026 | past |
| `BLD26-00473` | `Building/Commercial/Demolition/NA` | Blitzz Remote | 09/22/2026 | past |
| `BLD26-00310` | `Building/Residential/Mechanical/NA` | Blitzz Remote | 09/22/2026 | past |

Record `FIR26-00018`'s inspector/address reads **"Developer Sandbox"** — this is
seeded demo data, not a booking Licet (or a citizen) made.

Separately, the citizen record-type catalog confirms:
- **Fire: 0 configured types** — citizens cannot apply for a Fire record, so the
  only *future*-dated capacity (`FIR26-00018`, 09/30/2026) is not reachable by
  creating an owned record.
- **Building: 102 configured types**, including `Commercial Demolition` and
  `Residential Mechanical` — the exact record types on the two records that hold
  the past **Blitzz Remote** inspections — and a configured inspection type
  `Building/Inspection/Blitzz Inspection/Remote Video Inspection`.

## Interpretation

1. The tenant is **not** uniformly capacity-free. Capacity is configured per
   agency / record-type / inspection-type, and Fire Inspection + Building
   "Blitzz Remote" have produced real scheduled rows.
2. What remains true: **no capacity has been observed for any inspection type
   offered on a record Licet owns.** Owned records offer Rough / Service / Temp
   Service Pole / Ground Work / Electrical Final / Progress Check / etc., all
   empty Sep 2026 → Aug 2027.
3. The one future date is a **Fire** inspection on a record we cannot own via the
   citizen apply flow. The Building "Blitzz Remote" rows are dated in the **past**
   (09/22/2026) yet still `Scheduled`, which is consistent with seeded data rather
   than live booking capacity. Existence of a scheduled row is therefore
   **necessary but not sufficient** evidence that its calendar is currently open.

## Testable hypothesis — executed 2026-09-26

`Commercial Demolition` and `Residential Mechanical` are creatable by citizens,
and their (seed) records drew **Blitzz Remote** inspections. With authorization,
both were created on the test account and probed **read-only**:

| New record | Record type | Result |
|---|---|---|
| `BLD26-00480` | `Building/Commercial/Demolition/NA` | 18 offered types; **no** remote-video type; calendar empty Sep–Nov 2026 |
| `BLD26-00481` | `Building/Residential/Mechanical/NA` | 10 offered types; **no** remote-video type; calendar empty Sep–Nov 2026 |

The Demolition record's type grid was paged to completion (`declared_total=18`,
all 18 enumerated): standard building inspections only — Brycer Inspection
History, Set Backs, Temp Power, Footings & Forms, Foundation, Rough Frame, Frame,
Floor Deck, Roof Deck, Partial/Temp Building Final, Building Final, Fire Final,
Health Final, Sanitary Sewer Final, Storm Sewer Final, Storm Water Quality Final,
Zoning Final, Progress Check. The Mechanical record offers its 10 mechanical
types. **Neither offers any "Blitzz" / Remote Video inspection type.**

### Verdict

`Blitzz Remote` is **not a citizen-offerable inspection type** on this tenant —
the seed rows that carry it were added through the back office, not booked via
the scheduling wizard. Therefore the calendar capacity visible in the back-office
representation is **not reachable through the citizen path Licet operates**, and
no record type the test account can own exposes a capacity-bearing inspection
type. The one future-dated appointment (`FIR26-00018`, Fire, 09/30/2026) is on an
agency (`Fire`) with **0 citizen-configurable record types**.

**Conclusion: the 2026-09-26 survey found no citizen-reachable live booking path on this sandbox configuration.** This is dated environment evidence, not a current-availability claim. The dedicated bounded read-only query below now provides the recheck; issuing more records or scanning unrelated types is not warranted.

A dedicated bounded query is now available at `scripts/ni_citizen_capacity_query.py`. It starts from the authenticated account's My Records grid, verifies complete record coverage and stable row identities, opens only those owned records, pages the full citizen-offered type catalog, and reads identity-verified calendar windows — clicking the calendar's own `Next »` control forward until it reaches `--horizon` (default the end of 2028). It stops at the first active date and never selects a date/time or submits. Hard caps are 20 account records, 200 type checks, 5 My Records pages and 36 calendar windows per type; an `--horizon` that would need more windows than that is rejected at argument parsing. An incomplete page, identity mismatch, or an exhausted window budget reports `unknown` rather than “none”; reaching the horizon month reports `requested_window_exhausted`.

Run only with explicit approval for an authenticated read-only portal query:

```bash
.venv/bin/python scripts/ni_citizen_capacity_query.py
.venv/bin/python scripts/ni_citizen_capacity_query.py --max-records 12 --max-types 60 --horizon 2028-12
```

Offline deterministic parser, identity, pagination and bounds tests are in `tests/test_ni_citizen_capacity_query.py`.

### Live run — 2026-09-30 (first authenticated execution)

The query was run against the authenticated portal with explicit approval, budgeted read-only. Report: `logs/ni_backoffice/schedule/20260930T061330Z_citizen_capacity_query.json`.

Record `BLD26-00483` (Residential Alteration, account-owned, created 2026-09-30) verified end to end: identity-verified detail page, **complete offered-type catalog (13 declared / 13 observed, 2 pages, no failure)**, then identity-verified calendars per type. Eight of the thirteen offered types were checked (`Set Backs`, `Temp Power`, `Footings & Forms`, `Foundation`, `Rough Frame`, `Frame`, `Floor Deck`, `Roof Deck`), each with `calendar_read=true`, `identity_verified=true`, and

| Month | Active days | Any available |
|---|---|---|
| Sep 2026 | 0 of 30 | no |
| Oct 2026 | 0 of 31 | no |
| Nov 2026 | 0 of 30 | no |

Every type returned `availability_status=none_in_observed_calendar`. No active date was found, so the query never selected a day.

The overall report is `unknown`, not “none”: the run was budgeted to 8 of 13 offered types and 2 of 14 account records, so coverage is explicitly partial and the reason string says so. Two incomplete draft applications in the grid (`26TMP-000071`, `26TMP-000072`) are classified `not_a_record` and skipped with a stated reason rather than reported as parser failures.

**This reproduces, from the citizen path, the 2026-09-26 back-office conclusion: no citizen-bookable inspection appointment is reachable on this sandbox configuration in the Sep–Nov 2026 horizon.** Both findings are dated environment evidence. The earlier runs of the same query on 2026-09-29/30 reported `unknown` for adapter reasons (grid pager parsed as a record; the scheduling dialog never opened because its opener sits in a collapsed dropdown, so no type or calendar was read at all); those defects are fixed and the query now reads the real catalog and calendar.

### Extended horizon — 2026-09-30

The first run read only the three months the wizard renders on open, so it said nothing about 2027 and beyond. The scan now clicks the calendar's own `Next »` control forward (identity-checked, never selecting a day) until it reaches the `--horizon` month, and the per-type ceiling is `MAX_CALENDAR_WINDOWS = 36` shared by the portal and the query. Two further read-only runs on `BLD26-00483`:

- `--horizon 2027-12` (`logs/ni_backoffice/schedule/20260930T064433Z_citizen_capacity_query.json`): `Set Backs` and `Temp Power` each read **16 distinct months, Sep 2026 through Dec 2027**, every month 0 active days, stopping with `search_stop=requested_window_exhausted`.
- `--horizon 2028-12` (`logs/ni_backoffice/schedule/20260930T065050Z_citizen_capacity_query.json`): `Set Backs` read **28 distinct months, Sep 2026 through Dec 2028**, every month 0 active days, again `requested_window_exhausted`.

So the defect was horizon depth, not the paging mechanism: the calendar does keep advancing, and the sandbox still offers no appointment anywhere in the Sep 2026 – Dec 2028 window for these types. `requested_window_exhausted` is the honest stop for a negative result; it means the horizon was actually read. Reports remain `unknown` overall only because the run is budgeted to a prefix of records and offered types, and the reason string says which.

### Full type sweep — 2026-09-30

Those earlier runs only covered a prefix of the offered types. The query now supports `--type-offset N` so the full catalog can be walked in bounded chunks, and all **13 offered types** on `BLD26-00483` were checked at `--horizon 2027-12`, one type per run (the 600 s foreground budget does not admit multi-type chunks at a multi-year horizon). Every type was identity-verified and paged forward **Sep 2026 through Dec 2027 (16 distinct months)**, every month with **0 active days**, stopping with `search_stop=requested_window_exhausted`:

| # | Offered inspection type | Status | Months read | Stop | Report (`logs/ni_backoffice/schedule/`) |
|---|---|---|---|---|---|
| 0 | Set Backs | `none_in_observed_calendar` | 16 | `requested_window_exhausted` | `20260930T064433Z_…json` |
| 1 | Temp Power | `none_in_observed_calendar` | 16 | `requested_window_exhausted` | `20260930T065843Z_…json` |
| 2 | Footings & Forms | `none_in_observed_calendar` | 16 | `requested_window_exhausted` | `20260930T175657Z_…json` |
| 3 | Foundation | `none_in_observed_calendar` | 16 | `requested_window_exhausted` | `20260930T073135Z_…json` |
| 4 | Rough Frame | `none_in_observed_calendar` | 16 | `requested_window_exhausted` | `20260930T171937Z_…json` |
| 5 | Frame | `none_in_observed_calendar` | 16 | `requested_window_exhausted` | `20260930T172421Z_…json` |
| 6 | Floor Deck | `none_in_observed_calendar` | 16 | `requested_window_exhausted` | `20260930T172837Z_…json` |
| 7 | Roof Deck | `none_in_observed_calendar` | 16 | `requested_window_exhausted` | `20260930T173256Z_…json` |
| 8 | Partial/Temp Building Final | `none_in_observed_calendar` | 16 | `requested_window_exhausted` | `20260930T173711Z_…json` |
| 9 | Building Final | `none_in_observed_calendar` | 16 | `requested_window_exhausted` | `20260930T174052Z_…json` |
| 10 | Fire Final | `none_in_observed_calendar` | 16 | `requested_window_exhausted` | `20260930T174446Z_…json` |
| 11 | Zoning Final | `none_in_observed_calendar` | 16 | `requested_window_exhausted` | `20260930T174904Z_…json` |
| 12 | Progress Check | `none_in_observed_calendar` | 16 | `requested_window_exhausted` | `20260930T175305Z_…json` |

The final chunk (offset 12, the last type) reached the end of the catalog, so the **record-level** status flipped to `no_active_date_observed — all verified offered types checked within the bounded calendar horizon` rather than the partial-coverage `unknown`. That is the complete negative result for this record: **13 of 13 citizen-offered inspection types, each read over 16 months, exposed no active appointment day.**

One intermittent failure was observed and is reported distinctly from a horizon stop: the first attempt at `Footings & Forms` (offset 2)
(`20260930T072746Z_…json`) stopped at `search_stop=calendar_navigation_failed` after 14 months — a refused `Next »` click, not a read horizon — and honestly reported `unknown`. A retry of the same offset the same day returned `none_in_observed_calendar` over the full 16 months (`20260930T175657Z_…json`), confirming the failure was transient rather than a property of that type. The distinction matters: `requested_window_exhausted` means the horizon was actually read; `calendar_navigation_failed` means it was not, and the type stays unknown.

The append-only sweep trace is `logs/ni_backoffice/schedule/sweep_2027.log`. As throughout, the scan only selected a type and clicked the calendar's own pager — it never selected a day or time and never reached confirm/submit.

### Can the back office seed a citizen-bookable slot? — 2026-09-30

If no slot exists, the alternative to a fixture-only claim is to *create* one. That was probed read-only with `scripts/ni_backoffice_sched_recon.py` (record-centric portlets, deliberately not the rate-limited SPA). Report: `logs/ni_backoffice/inventory/20260930T181322Z_backoffice_sched_recon.json`.

**What is addressable.** The back office (`nullisland-test-av.accela.com`, `developer`/`accela`) renders `BLD26-00483` (`REC26/00000/000QS`) and its inspection portlets directly. The record shows **`Inspections (0)`** — the back office agrees with the citizen side that no inspection exists. Its toolbar carries `Manage Inspection`, `Schedule Inspections`, `Assign Inspector`, and a `.do`-addressable inspection-calendar family (`calendarInspectionList.do` daily, `calendarInspectionWeekly.do` weekly, `calendarInspectionListMonthly.do`, `calendarInspectionsList.do`, `calendarInspectionAddress.do?mode=assignByDistrict`, `calendarInspectionStatistics.do`).

**Why seeding is not possible with this access.**

1. **The record-level scheduling form is a `showModalDialog`.** `Schedule Inspections` resolves to `selectManageInspection('0', …) → openScheduleInspectionsDialog()`, which is literally `window.showModalDialog("/portlets/inspection/workloadingInspectionList.do?value(mode)=doManage&doPending=true&RCAP=true&module=Building…", …)`. `window.showModalDialog` was **removed from Chrome 43+**, and Solari drives modern Chromium, so the dialog cannot open there — the concrete cause of the earlier `noform.png` dead end. Addressed directly that URL returns an empty document (`frames=1, controls=0`): it is a modal child that expects the opener's form.
2. **The addressable calendar surfaces expose no capacity.** Every calendar view read shows **0 active and 0 inactive day cells** — they list *scheduled* inspections (the daily view renders 09/30/2026, the weekly view the week of 09/26/2026), not appointment availability. The weekly view's inspection-type filter `<select name="showType">` renders **no options**.
3. **The capacity *configuration* admin is the SPA.** Inspection types' calendars/time windows live in the Angular `spacev360` "Calendaring & Inspections" module, which `docs/known_limitations.md` already records as `href="#"` JS routes with no `.do` URLs leaking into any frame, on a host that Cloudflare bans (Error 1015) after ~15 heavy sessions and re-arms on deep `.do` GETs.

So the two routes to writing capacity are both closed to this account: the record-level dialog needs a retired browser API, and the configuration surface is an unmapped, rate-limited SPA. **Seeding a citizen-bookable slot therefore requires a change on the environment side (an agency-admin/sandbox-owner action to configure inspection-type availability), not another run of the agent.**

### The reachable substitute: portal-realism replay — 2026-09-30

Since the environment withholds the slot, the booking leg is proven by supplying *only* the slot and keeping everything else real. `tests/test_scheduling_portal_replay.py` loads `tests/fixtures/ni_schedule_calendar.html` — a verbatim capture of this tenant's popup calendar in which **every** cell is `CalendarDayInactive` — injects exactly one active day, and then runs the shipping code paths over it: the real `accela.parse_calendar`, `resolve_calendar_months`, `active_calendar_day_selector`, the real `AccelaInspectionPortal` through the real dispatcher and guard, and the real `InspectionActionExecutor`. The result is a `VERIFIED_SUCCESS` on the injected day, plus a refusal for any day the calendar does not offer.

This is deliberately *not* described as a live booking: it proves the booking path is correct given a slot, against observed portal markup, while the sandbox still renders no bookable day. Closing the last gap — a live `VERIFIED_SUCCESS` — remains an environment-side change.

## Safety

- No mutation occurred in this survey: zero creates, zero issues, zero
  submissions. The inspector grid was read, the `Next` pager was clicked, the
  type catalog was read from unauthenticated search GETs.
- `scripts/ni_capacity_finder.py` refuses any write control by element type
  (`button`/`input`/`select`) when its label reads like a write verb, and it
  writes only under the ignored `logs/` tree.

## Owned records created during the experiment

The two records above (`BLD26-00480`, `BLD26-00481`) are real, owned sandbox
records created by the authorized apply run. They are harmless spare eval slots
and can be kept or left unused; no inspection was scheduled on either.

## Artifacts

- `logs/ni_backoffice/capacity/<stamp>_capacity_finder.json` (+ `.progress.jsonl`,
  `_Inspections_f*.html`) — ignored local run artifacts.
- `logs/ni_backoffice/inventory/<stamp>_catalog.json` — citizen type catalog.
- `logs/ni_backoffice/schedule/<stamp>_availability_sweep.json` — new-record
  calendar sweep (both empty).
- `logs/ni_backoffice/offered_types/<stamp>_offered_types.json` — full offered-type
  grids (Demolition 18, Mechanical 10) with paged HTML.

## Survey scripts

- `scripts/ni_capacity_finder.py` — back-office capacity survey (read-only).
- `scripts/ni_offered_types.py` — full scheduling-wizard type-grid enumerator (read-only, pages the grid).
- `scripts/ni_citizen_capacity_query.py` — bounded account-owned-record → citizen-offered-type → active-calendar query (read-only, no date/time/submit).
