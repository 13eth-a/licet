# Accela UI map

> **Phase 6 note (2026-09-23):** the mutation-side reading of this map — which
> controls commit, which Continue is navigation, and where payments/
> attestations/cancellations live — is codified in
> `docs/phase6/portal_boundary_map.md` and as data in
> `licet/browser/accela.py:MUTATION_BOUNDARIES`.

Recon started 2026-09-18 by probing **live public ACA portals** over HTTP:
Omaha (`OMAHA`), Salt Lake City (`SLCREF`), Meridian ID (`MERIDIAN`), and
Accela's own Null Island sandbox (`aca-test.accela.com/nullisland`). No
credentials were used; anonymous search is available on all three.

**Solari live verification: DONE (2026-09-18, 7/8 PASS)** — see the
compatibility table and live findings below; screenshots in
`logs/solari_verify/`. Still pending: result-selection + section navigation
(blocked on known Null Island test data), and login-survival (blocked on the
ACA public-user test account).

> **Caution:** ACA is classic ASP.NET WebForms. Pages are huge, postback-heavy,
> and the same page renders differently per agency (config-driven). Selectors
> below were stable across three agencies, but nothing here is contractual.

## URL patterns

| Page | URL | Notes |
|---|---|---|
| Portal home | `aca-prod.accela.com/<AGENCY>/Default.aspx` | Agency code is a path segment, usually uppercase |
| Record search | `aca-prod.accela.com/<AGENCY>/Cap/CapHome.aspx?TabName=Home&module=<Module>` | The central search page; **module param matters** — search and results are scoped per module (Permits, Building, Planning, Dev-Services…) |
| Record detail | `aca-prod.accela.com/<AGENCY>?Module=<M>&TabName=<M>&capID1=<T>&capID2=<YYYY>&capID3=<SEQ>&agencyCode=<AGENCY>` | Direct deep link; server-renders as `Welcome.aspx`. capID3 is usually base-36-ish alphanumeric (e.g. `22CAP/00000/006RZ`) |
| Login | `<AGENCY>/Login.aspx` | Anonymous search works **without** it; see Login section below — **SSO/iframe flow, not a plain form** |
| APO lookup | `<AGENCY>/APO/APOLookup.aspx?module=<M>` | Structured address search: `addressLookupForm_txtAPO_Search_by_Address_StreetNumber_ChildControl0/1` (range), `_StreetName`, `_UnitNo`, `_City`, `_Zip`, `_State`, `_Direction` dropdowns. Cleaner fit for "123 Main Street"-style goals than the general search |

There is **no separate `CapDetail.aspx` or `InspectionList.aspx`** reachable by
URL. Record sections and the scheduling flow are **postback-driven inside
`Welcome.aspx`** — the agent must click its way through; it cannot deep-link to
a section (except the record itself).

## 1. Homepage — `Default.aspx`

- Agency-branded landing page; nav to search and login.
- Announcements, links to modules. Occasionally a hidden `iframeExport`
  (0×0, used for file export; ignorable but present in DOM).

## 2. Record search — `CapHome.aspx?TabName=Home&module=<M>`

Key controls (IDs stable across Omaha + SLC):

| Control | ID (stable suffix) | Behavior |
|---|---|---|
| Search-mode dropdown | `ctl00_PlaceHolderMain_ddlSearchType` | **Auto-postback on change** — swaps the whole form via `__doPostBack`. Options include permit-number / address / parcel / owner-style searches |
| Permit number | `ctl00_PlaceHolderMain_generalSearchForm_txtGSPermitNumber` | Text, maxlength 30 |
| Street number range | `…txtGSNumber_ChildControl0` / `…ChildControl1` | From/To pair (required for address search in some agencies) |
| Street name | `…txtGSStreetName` | Text; **enter digits only for numbered streets** (e.g. `72nd` → `72`) |
| Street suffix / direction | `…ddlGSStreetSuffix`, `…ddlGSDirection` | Plain `<select>`s, no postback |
| Parcel | `…txtGSParcelNo` | Text |
| Applicant first/last/business | `…txtGSFirstName`, `…txtGSLastName`, `…txtGSBusiName` | Text |
| Permit type | `…ddlGSPermitType` | **Auto-postback dropdown** — cascades status options |
| Record status | `…ddlGSCapStatus` | Populated only after permit-type postback |
| Date range | `…txtGSStartDate`, `…txtGSEndDate` | **Pre-filled silently** — see gotchas |
| Cross-module checkbox | `ctl00_PlaceHolderMain_chkCrossModuleSearch` | "Search All Records" |
| Submit | `<a id="btnSearch">` (class `gs_go`) | **Anchor, not a button** — fires JS form submit |

For address goals there is a second entry point: the **APO lookup** above,
with properly labeled structured fields, instead of the general search's
`txtGSStreetName` (which wants digits only for numbered streets).

## 3. Search results — postback of `CapHome.aspx`

- Result rows are rendered client-side after a **full page postback** (URL
  stays `CapHome.aspx`; there is no separate results URL to bookmark).
- Grid rows carry `__doPostBack` targets and/or links into the record detail
  URL (`capID1/2/3` pattern above).
- **Pagination is postback-based** ("Next"/page-number links trigger
  `__doPostBack`, not new URLs).
- Result columns are agency-configured (typically: record number, address,
  type, status, date).

## 4. Record details — `Welcome.aspx` (via capID deep link or result click)

- Header: record number, type, address; `My Collection` radio/name controls
  appear in DOM even when logged out.
- **Record status** ("Status" field) is on this summary page.
- Sections/links are grouped under `TabDataList`/`LinksDataList` controls; each
  is a JS `__doPostBack` link (`…LinkItemUrl`), not a URL. Typical sections:
  Record Info, Processing Status, Inspection History, Fees, Attachments,
  Related Records, Payment.
- Deep-link side doors worth knowing:
  - `CapHome.aspx?IsToShowInspection=yes&module=<M>` → opens the **Schedule an
    Inspection** flow for the logged-in user's records.
  - `&IsToShowInspection=` (empty) on a record URL shows inspection context.
- Meridian quirk: a visible **"Schedule an Inspection" nav item existed but was
  marked `Active: False`** in page data — rendered yet non-functional. The
  agent must treat dead-but-rendered links as an expected failure mode.

## 5. Status / history section

- Processing-status history and application info live inside `Welcome.aspx`
  under the Record Info / Processing Status postback links.
- Inspector comments: **agency-configured**. Usually surfaced in the inspection
  history rows (result/comment columns) and/or Processing Status. Not a fixed
  location; the planner must scan, not assume.

## 6. Inspections section

- Reached via postback link on record detail, or the `IsToShowInspection=yes`
  side door.
- Lists past inspections (type, date, status/result) and, for eligible records
  (logged-in account + record association), a Schedule/Reschedule action.
- **Anonymous visitors can read history; scheduling requires a free public-user
  account tied to the record.**

## 7. Documents section

- "Attachments" postback link on record detail. Public attachments download
  directly; many agencies expose few or none publicly.

## 8. Fees section

- Fees postback link on record detail; shows line items and balance. Payment
  is explicitly **out of scope** for Licet (safety boundary).

## 9. Inspection scheduling — `CapHome.aspx?IsToShowInspection=yes…` flow

**Mapped to the end, live 2026-09-20** (`scripts/ni_schedule_probe.py`,
`scripts/ni_availability_sweep.py`). Entry: postback link on record detail (or
the side door above). The wizard runs in a `ctl00_phPopup_*` dialog overlay with
postback steps:

1. `select_record` — `CapHome.aspx?IsToShowInspection=yes` renders a
   scheduling-scoped record list (with `rdoStart`/`rdoResume`); clicking a row
   opens its `CapDetail.aspx` **in scheduling context** (Inspections section
   expanded).
2. `select_type` — `Schedule/Request an Inspection` opens the type grid:
   `Available Inspection Types (N)` + `Show optional inspections`
   (`ctl00_phPopup_chkShowOptional`) + one radio per type
   (`ctl00_phPopup_gvInspectionType_ctlNN_rdInspectionType`). The label text
   carries `(required)` / `(optional)` — the only signal for what may be
   skipped. **The grid paginates at 10 rows** (`< Prev 1 2 Next >`), so the
   `(N)` heading, not the row count, is the total (Commercial Alteration: 18
   declared, 10 on page 1).
3. `select_date` — Continue reaches the calendar: `Inspection type: <name>` +
   `select an appointment date and time range by clicking a link on the
   calendar below`. Day cells are `<td class="CalendarDayInactive
   ACA_LinkButton" title="Cannot schedule inspection on this date">` — cells,
   **not** anchors, and an unavailable day says so twice over. Selectable-time
   panel is `lblAvaliableTimes` (ACA's spelling) with
   `divMorningEventItems` / `divAfternoonEventItems` / `divDayEventItems`.
4. `confirm` — the popup's own Continue is `ctl00_phPopup_lnkContinue`,
   rendered `disabled="disabled"` with `class="ButtonDisabled"` and the real
   postback parked in **`href_disabled`**. It enables only once a date *and*
   time are chosen; force-clicking it would fire a postback the portal
   explicitly disabled. Back is `ctl00_phPopup_lnkBack`, cancel is
   `ctl00_phPopup_lnkCancel` (which the guard holds — cancellation is
   confirmation-required).

Requires public-user login. Step transitions are full-page postbacks and the
URL does not change at all inside the dialog, so the agent must track flow
position from **what the popup says**, not the URL (`accela.schedule_step`).

### Availability: none on this sandbox (measured 2026-09-20)

All 8 owned records were walked to the calendar. **Every day cell in all three
rendered months (Sep/Oct/Nov 2026) is inactive — 91 cells, 0 selectable — for
every record that offers a type at all** (`lblAvaliableTimes` empty; Continue
disabled). Two records (New SFR `BLD26-00470`, Right of Way `BLD26-00472`) offer
**no inspection types at all** (`Available Inspection Types (0)`).

Likely cause: every owned record is **Submitted, not Issued**, and ACA normally
only lets a citizen book inspections on issued permits. So on this environment
the flagship request's last leg is unachievable, and the correct outcome is an
accurate report saying so — see `licet/eval/prompts.py` (`expects="cannot_finish"`)
and `licet/eval/records.py` `SCHEDULING_GROUND_TRUTH`. Re-measure before
assuming that still holds.

## 10. Confirmation/result page

- Post-schedule confirmation is a postback-rendered success message in the
  same page shell (no distinct confirmation URL). **Verification must be done
  by re-reading the inspection list**, which matches our Verify-Outcome stage
  in `docs/architecture.md`.

## Applied test set — all 7 target types created (verified 2026-09-20)

The citizen portal apply flow was driven end-to-end by script for every
Licet eval category (`scripts/ni_apply_batch.py`), and the results were read
back from the account's own records (`scripts/ni_my_records.py --details`).
Records are owned by the public-user test account, so scheduling and cancel
evals are safe to run against them.

| Eval target | cap type | Record number | capID3 |
|---|---|---|---|
| Commercial Alteration | `Building/Commercial/Alteration/NA` | `000000014` | `000QB` |
| Residential Addition | `Building/Residential/Addition/NA` | `BLD26-00468` | `000QC` |
| Commercial Electrical | `Building/Commercial/Electrical/NA` | `BLD26-00469` | `000QD` |
| New Single Family Residence | `Building/Residential/New/SFR` | `BLD26-00470` | `000QE` |
| Solar Permit | `Building/Solar/NA/NA` | `BLD26-00471` | `000QF` |
| Right of Way Use Permit | `Building/Right of Way/NA/NA` | `BLD26-00472` | `000QG` |
| Sign - Temporary | `Building/Sign/Temporary/NA` | `BLD26-00467` | `000QA` |
| Sign - Temporary (spare) | `Building/Sign/Temporary/NA` | `BLD26-00466` | `000Q9` |

All rows: status **Submitted**, expiration 01/31/2026, module `Building`,
address `<n> Commerce Ave, 00001` (sign permit: `77 Licet Eval Way`).

**My Records read path** — `Cap/MyRecordsCap.aspx?TabName=Home` (logged in).
Grid columns: Date / Record Number / Record Type / Project Name / Address /
Status / Action / Description / Expiration Date / Short Notes; `Showing
1-8 of 8`. Each row links to
`/NULLISLAND/Cap/CapDetail.aspx?Module=Building&TabName=Building
&capID1=REC26&capID2=00000&capID3=<ID3>&agencyCode=NULLISLAND&IsToShowInspection=`.
Two traps worth remembering:

1. the grid includes an **empty `Project Name` column** — a parser that
   drops empty cells silently shifts every later column;
2. row hrefs are **site-absolute** (`/NULLISLAND/...`), so they resolve
   against the host, not the agency path — `{CITIZEN}/NULLISLAND/...` gives
   `/nullisland/NULLISLAND/Cap/CapDetail.aspx` → "The file … does not
   exist".

**Read path replayed through the licet dispatcher (2026-09-20, 16/16 checks).**
`scripts/ni_dispatcher_replay.py` drives `SolariSession` → `SolariClient` →
`ToolDispatcher` → `AgentState` over navigate → login → My Records → click the
record number → record detail, with no raw Playwright anywhere and nothing
submitted. Two things that only showed up in the live loop: opening a grid
result needs an explicit `intent="open_record"` (an unclassified click is
blocked by design), and a click whose target text contained "Submit
Application" was held as `submit_application` before the browser was touched —
the hold happens at resolution time, so it does not depend on the control
existing on the current page. Evidence:
`logs/ni_backoffice/*_dispatcher_replay.json`.

**Section click-through verified (2026-09-20, 14/14 checks).** Clicking
`Record Info`, `Payments` and `Attachments` on a record detail page through the
dispatcher opens each section and changes the visible content (657 → 730 → 662
→ 402 chars) with **the URL unchanged every time** — they are `__doPostBack`
anchors, so section state can never come from the URL and must be tracked in
state. Two portal-compat lessons worth keeping:

- **Resolve section links by exact text.** `a:has-text('Attachments')` matched
  an *invisible wrapper* containing that text, so the click looked impossible
  (it was reported as `not_actionable`); `a:text-is('Attachments')` matches the
  real anchor and works. The client now tries exact text before substring text.
- **The dead-but-rendered shape is real and needed its own error.**
  `BrowserError.NOT_ACTIONABLE` ("matched the DOM but is not actionable",
  naming the hidden selectors) is what surfaced the wrapper mismatch instead of
  suggesting the section did not exist.

The scheduling entry also clicked through: it lands on
`CapHome.aspx?IsToShowInspection=yes&module=Building` and `accela.locate`
reports `schedule_inspection/select_record` — the flow was probed and abandoned
without touching the wizard.

**Apply-flow additions since the Sign - Temporary run:** per-type AppSpec
sections add required fields with arbitrary ids (Commercial Electrical
`txt_0_0/_0_2`, Solar `txt_0_1.._0_6`, Right of Way a "PROJECT DATES"
section with `txt_3_0/_3_1`). The two Right of Way dates are MaskedEdit
MM/DD/YYYY (they ignore `fill()`) and stalled the wizard until the script
started resolving required fields generically from `aria-required` /
`title="Required"` + `placeholder`/`fieldname` and typing masked values with
real keystrokes. The ACA validation panel's `skipTo('ctlId')` links are the
reliable way to learn what a Continue click is missing. Note also that
**altID format is per record type**: Commercial Alteration reports
`000000014`, everything else `BLD26-004xx`.

## Login & scheduling auth (verified logged-out, 2026-09-18)

- **Two unrelated "Accela accounts" — don't confuse them:**
  1. **Accela developer account** (developer.accela.com, Construct/v4 API).
     Under review as of 2026-09-18; **not required** for Licet v1's browser
     path. If approved later, its value is eval ground truth (cross-checking
     portal state via API), not runtime automation.
  2. **ACA public-user account** — free, self-registered on the portal
     (Login.aspx → "Register for an Account"). This is the account that can
     schedule inspections; `ACCELA_TEST_USERNAME/PASSWORD` refers to it.
- `Login.aspx` ships **zero password fields in the raw HTML**. Omaha wires
  `validateSSOLogin`/`SSOLogin(superAgencyCode, cloudApiUrl, tenantName)` —
  the Accela **CivicId SSO** flow. The actual credential form is rendered
  client-side (and `iframeResizer.min.js` is loaded on the page, so expect the
  SSO form to live inside an **iframe or popup**). An agent cannot "fill the
  login form" from page HTML; it must click the SSO entry and operate inside
  whatever frame/popup appears.
- **NI login mapped live (2026-09-19):** the credential form is an Angular
  panel iframe at `…/AngularUI/CommunityView/login-panel?inLegacyUI=true`
  with `input[name='username']` / `input[name='password']` (name attrs, no
  ids) and a single `<button>`; success lands on `Dashboard.aspx`.
  Registration lives at `…/CommunityView/account/new` (fields
  `txbUserName/txbEmail/txbPassword1/txbPassword2`, PrimeNG labels overlay
  inputs so click/fill actionability fails — use focus+type) behind a
  **Google reCAPTCHA v2 checkbox that serves interactive image challenges**
  for automation — registration is the one step needing a human.
- Clicking scheduling while logged out (`CapHome.aspx?IsToShowInspection=yes`)
  does **not** redirect: it raises a JS **"Please Login" notice dialog**
  (`please login to continue.', 'Notice', true, 1`). A naive agent will think
  the click failed. Expected handling: detect the notice, navigate to Login,
  or stop with `user approval required` / `missing credentials`.
- Login page offers **"Register for an Account"** (public-user self-service)
  next to login — that's how the test account gets created.
- No captcha observed on login page HTML; SSO may still add one at the
  identity-provider step (unverified — needs a live session).
- `SessionTimeout.js` is loaded site-wide: expect idle-session timeouts and
  re-login prompts during long eval runs.

## Complications observed

- **ViewState + postbacks everywhere.** Navigation is `__doPostBack` JS
  (encrypted `__VIEWSTATE`, ~100–500 KB pages). Real browser semantics
  required; raw HTTP replay would be fragile — good news for the Solari
  Playwright approach.
- **Auto-postback dropdowns** (`ddlSearchType`, `ddlGSPermitType`): changing
  selection reloads the page and resets/refreshes dependent fields. `select`
  must wait for the reload to settle.
- **Default date-range filter is pre-filled and invisible** in the UI flow
  (Omaha: `05/04/1999→today`; SLC: `06/01/1977→today`). An address search can
  return zero rows because of it. The agent should consider widening dates
  when a search "fails."
- **Search results have no URL** — postback-rendered; can't bookmark or
  navigate directly to results.
- **Section nav via postback links** (`TabDataList`/`LinksDataList`), not URLs.
- **Dead-but-rendered links** exist (Meridian "Schedule an Inspection" with
  `Active: False`).
- **iframes**: only the hidden `iframeExport` helper; low risk.
- **Popup/new-tab risk:** Omaha had exactly one `target="_blank"` (report
  export). Reports open via `Report/ReportParameter.aspx` and may pop out.
- **Auth quirks:** anonymous search + record reading works; scheduling,
  applying, and payment need a free public account. Session is cookie-based;
  deep-linking into `Welcome.aspx` while logged out still renders public
  record data. Login itself is **CivicId SSO rendered client-side (likely in
  an iframe/popup)** — the tool contract's selector strings must be able to
  address frames (Playwright `frame_locator`), or the planner must treat
  "switch to iframe context" as part of the click/select step.
- **Gated actions fail softly.** Auth-required actions don't redirect; they
  raise JS notice dialogs ("Please Login") — success/failure detection must
  read page text, not just navigation events.
- **No captcha on anonymous search** (none found in either agency's HTML).
  Solari stealth mode is still recommended.
- **Same IDs, different behavior per agency:** module scoping, search-type
  options, date defaults, and section availability all differ by agency.
  Never assume portability.

## Solari compatibility — LIVE VERIFIED (2026-09-18, 7/8 PASS)

Solari exposes a Playwright-compatible cloud browser
(`solari_browser.Solari(api_key=…) → await launch()` → ordinary `page`
API; Python SDK confirmed via the official cookbook). Live runs against
Omaha (`aca-prod`) + Null Island (`aca-test`), 6 consecutive iterations;
final result **7/8 PASS**. Runner: `scripts/solari_verify.py`
(`.venv/bin/python scripts/solari_verify.py omaha|nullisland`).

| Checklist item | Result | Notes |
|---|---|---|
| Open portal through Solari | ✅ PASS | `goto` + title read clean; both prod and test portals |
| Read visible page content | ✅ PASS | `body.inner_text()` works; search form controls detected |
| Click nav elements | ✅ PASS | after loading-mask neutralization (see below) |
| Fill a search field | ✅ PASS | value sets fine; but see **disabled-button finding** |
| Submit a search | ✅ PASS | **force-click fires the postback** — results rendered |
| Select a search result | ✅ PASS | probe record `PLB-10-00951` visible in live results (Omaha, falsifiable check 2026-09-19). NI: anonymous search yields 0 public rows — data availability, not tooling — but the account's own records open via the My Records detail deep link (2026-09-20, 8/8) |
| Navigate between sections | ✅ PASS (NI) | record detail renders the same section set for all 8 owned records; clicking the postback section links verified live 2026-09-20 (`scripts/ni_section_clickthrough.py`, 14/14) — see the click-through note below |
| Handle dropdowns/forms | ✅ PASS | auto-postback select works; **but mode switch replaces the form** (see below) |
| Auth survives navigation | ✅ PASS (NI) | public-user account created 2026-09-19; login → Dashboard.aspx, markers (Logout/My Records/My Account) persist across CapHome navigation; 10 accela cookies (`scripts/ni_login_verify.py`) |
| Screenshots | ✅ PASS | PNGs in `logs/solari_verify/` (gitignored) |

### Critical live findings (would have cost days to hit in Phase 1+)

1. **ACA's global loading mask eats clicks.** `#divGlobalLoadingMask`
   (`.ACA_MaskDiv` — a Silverlight-era overlay **iframe**) stays in the DOM
   "hidden" but still **intercepts pointer events**, so normal Playwright
   clicks time out even when the target is visible/enabled. Fix: inject
   `display:none !important` for it after every postback (the runner does
   this on `load`; Licet's real `wait`/`click` tools must do the same).
2. **`#btnSearch`'s `ButtonDisabled` class is cosmetic.** Typing, blur, and
   Tab never enable it, but a **force-click fires the search and renders
   results**. The enabling trigger lives somewhere in ACA's minified bundles.
   → Tool design: `click` should fall back to force-click; treat the class
   as advisory only.
3. **Search-mode dropdown swaps the entire form.** After selecting "Search
   by Address," `txtGSStreetName` AND even `txtGSPermitNumber` vanish — NI's
   address mode uses a different (unresolved) control-ID family. Agents must
   **re-read the form after every mode switch**; never cache field IDs
   across a postback.
4. **Postback races detach elements mid-click** (`element was detached from
   the DOM, retrying`). Clicks need re-resolution, not one-shot locators.
5. **Meridian gates anonymous record views** behind an "approved
   Address/Parcel Verification" — a deep link that renders via curl can be
   **dynamically gated** in a real browser. Deep links are not guaranteed
   access.
6. **Null Island confirmed as a standard ACA portal** (`ddlSearchType`,
   `generalSearchForm`, same postback machinery) — but its default date
   window is `09/18/2024→09/18/2026`, and `"Main"` matched 0 rows (sandbox
   data inventory still unknown). Date widening is mandatory before judging
   "no results."

### Null Island sandbox (developer.accela.com/docs/construct-appSandbox.html)

- Official test environment: back office at `nullisland-test-av.accela.com`
  (`nullisland` / `developer` / `accela`), citizen portal at
  `aca-test.accela.com/nullisland/`.
- **Live verification 2026-09-19: 6/8 PASS** — every mechanical step
  (open/read/fill/dropdown/screenshot/cookies) behaves identically to Omaha;
  the two failures are both **data availability, not tooling** (no public
  records to find or open).
- **Record discovery attempt** (`scripts/ni_find_records.py`): 6 modules ×
  5 loosening strategies (street Main/Test/1, permit `%`/`A`, dates widened
  1990–2035) → **0 public rows**. NI's anonymous data inventory appears
  empty; records likely become reachable after public-user registration
  ("My Records") or via seed data. Re-run the script after account setup.
- **Test API tokens** (developer.accela.com/TestToken/Index) still require a
  registered app + portal login — so API ground truth remains blocked on the
  developer-account review; the sandbox UI itself is open now.
- Strategic note: NI is Accela's own sandbox — schedule test inspections
  there rather than on a real city's production portal.

**Known Solari gotchas that matter for ACA** (from the official cookbook):

- `launch(profile_id=…)` only sets `storageState` (snake_case — verified
  against the installed SDK on 2026-09-20; the docs said `profileId`) — pass it
  at launch or every run starts anonymous while *looking* logged in. Critical
  for our public-user scheduling account.
- Playwright wire protocol is version-gated (428 on mismatch); prefer the CDP
  endpoint if we ever connect raw. Using the bundled SDK avoids this.
- `browser.close()` releases the session; keep it in a `try/finally`.
- Session recording is per-session (`recording: true` at creation) — worth
  enabling for eval replays.

## Impact on tool/state design (updated by live run)

- `wait` = "wait for postback to settle" **+ re-inject the mask-neutralizing
  CSS**; ACA postbacks take real time and the mask returns after every one.
- `read_page` must include current URL **and** a flow-position hint, because
  URLs barely change across steps — and must happen **after** mode-switch
  postbacks (the form may have been replaced wholesale).
- `click` needs the force-fallback (disabled-class anchors, overlay races).
- `go_back` is risky in WebForms (postback history resubmits) — prefer
  re-navigation via search or record deep link (and deep links themselves
  can be gated — see finding 5).
- The agent can never rely on URL alone — or on cached field IDs — to know
  where it is; `AgentState` must carry flow position (matches
  `licet/agent/state.py`).

- `wait` should mean "wait for postback to settle" (ACA postbacks take real
  time; fixed short sleeps will race).
- `read_page` must include current URL **and** a flow-position hint, because
  URLs barely change across steps.
- `go_back` is risky in WebForms (postback history resubmits) — prefer
  re-navigation via search or record deep link.
- The agent can never rely on URL alone to know where it is; `AgentState`
  must carry flow position (which matches `licet/agent/state.py`).
