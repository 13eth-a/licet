# Known limitations

## Phase 5: extraction honesty is fixture-verified, not live-capture-verified (2026-09-22)

- **The Phase 5 portal-state review (GLM) fixed seven extraction defects
  against reconstructed ACA shapes, not fresh live captures.** The date
  normalization (H02), status-column outcome recovery (H06), legend rejection
  (H01), declared-empty conflict (H05), and fee-status wordings (H03/H04) are
  locked by `tests/test_phase3_adversarial.py` and
  `scripts/phase5_portal_state_replay.py` against fixtures shaped like the
  Phase 1 captures. A live agency whose wording differs still degrades to
  uncertainty/partial coverage — honestly, but with recall the fixture set
  cannot promise. The review is `docs/phase5/portal_state_review.md`.
- **The fees `Due Date` header map has exactly two new keys.** Other agencies'
  due-date column wordings fall back to the money-line path (bounded mangled
  descriptions) rather than structured rows. Live captures are the widening
  path.
- **`blocks_answer` producibility is closed** (adversarial review said it was
  model-only): the deterministic rule engine emits it in four classes. What a
  live capture may still lack is an agency shape that *triggers* one during the
  flagship — the scripted flagship never exercises them.

## Phase 5: earlier live-read limitations and current acceptance (updated 2026-09-26)

- **Historical limitation (2026-09-22):** the early live flagship run stopped at the live tool layer. At that time, (a) Phase 3's targeted retrieval
    clicks an `Inspections` label the record detail does not expose as actionable —
  its sections are `Record Info | Payments | Attachments` — so the click returns
  `not_actionable` and the run stops at `READ_INSPECTIONS`. (b) The Phase 2 search
  click intermittently returns `action_outcome_unknown` (navigation succeeds; the
  post-click verification cannot confirm it). Both are live-read-path issues, not
  planner logic: the planner stops safely with `mutations_attempted=0` and never
  fabricates the missing section. Subsequent reader fixes and a fresh Phase 5
  portal-real run reached the full catalog and identity-verified calendar on
  `aca-test.accela.com` (SANDBOX), with zero active dates in Sep–Nov 2026 and 0
  mutations. The latest captured report is
  `logs/ni_backoffice/phase5/2026-09-26_semantic_flagship_live_verification_latest.json`.
  This updates the old stop-at-READ_INSPECTIONS limitation, but does not prove a
  production-host run or a booking. See `docs/phase9/submission-candidate-provisional.md`.
- **Scheduling cost and signature are unknown to the live Phase 5 providers.**
  `licet/eval/phase5_live.py` reports exactly what the portal exposes — offered
  wizard types and calendar active days — and leaves cost/signature `None`. A
  no-spend goal therefore cannot be satisfied live (unknown cost is refused, not
  read as zero), which is the intended fail-closed behaviour, but it means a live
  booking could not be accepted even with calendar capacity.

## Phase 4: the mutation path is scripted, not live-verified (2026-09-22)

- **No verified real sandbox inspection mutation has been established.** Existing end-to-end `VERIFIED_SUCCESS` is fake-I/O integration; the most recent portal-real test-host run found no active dates over its observed Sep–Nov 2026 calendar and left cost/signature unknown. Historical environment sweeps found no appointment capacity on their measured horizons; the **2026-09-26 capacity survey** refines this: inspection capacity exists on seeded back-office records, but **no capacity-bearing inspection type is reachable through the citizen scheduling workflow for records associated with the test account** (see [`docs/phase9/sandbox-capacity-findings.md`](phase9/sandbox-capacity-findings.md)). A sandbox reconfigured with citizen-bookable capacity, and satisfied constraints, is required for a real booking capture. Historical permit-status note: `BLD26-00469` was accepted and issued through
  the back office (two audited writes), after which the citizen portal reports it
  `Issued` and the scheduling wizard opens, offers `Rough`/`Service`/`Temp Service
  Pole`/`Ground Work`/`Electrical Final`/`Progress Check`, and reaches the calendar.
  The calendar then rendered **0 active days**, and a horizon sweep
  (`scripts/ni_calendar_horizon.py`, read-only) showed, for that measured run, all
  six offered types empty from Sep 2026 through Apr/May 2027, and `Rough` walked
  through Aug 2027. Those sweeps found no citizen-bookable appointment capacity in
  their measured horizons, so that booking could not complete. `AccelaInspectionPortal` is therefore verified against captured markup, a state-machine fake, **and a portal-real sandbox run that reaches the calendar and is
  correctly refused there** — but not against a completed live booking. A live
  `VERIFIED_SUCCESS` stays the Phase 4 exit gate and needs a sandbox with at least
  one freshly observed open appointment date.
- **The live executor path is exercised, end to end, up to the date gate.**
  `scripts/ni_phase4_acceptance.py --record BLD26-00469 --type Rough` now runs the
  real stack (read inspections → read offered types → read calendar → policy →
  executor → adapter) and returns `VERIFIED_FAILURE` /
  `no available date satisfies the user's constraints` with every zero-target
  counter at 0. That is the honest outcome for this environment, not a pass.
- **The adapter's sync methods cannot be driven from inside an event loop.**
  They wrap `asyncio.run` and refuse a running loop, so the sync executor must be
  handed a worker thread whose portal calls are marshalled back onto the loop that
  owns the Solari client (`LoopBridgePortal` in the acceptance script). Handing it a
  second loop raises and then hangs.
- **Reschedule and cancellation controls were never captured, and the adapter
  refuses both.** Because no owned record ever held a scheduled inspection, the
  per-row Reschedule/Cancel controls (`docs/accela_ui_map.md` section 6) never
  rendered. Routing a reschedule through the new-request wizard would create a
  second appointment for the same type, so `submit_inspection_action_async`
  fails closed instead. Closing this needs a live record with a scheduled
  inspection, the rendered control, and its captured markup.
- **Required scheduling fields are a gate, not a filler.** The executor stops
  with `MISSING_REQUIRED_INPUT` when a snapshot declares a required field with
  no supplied value, and the executor is fail-closed when the mapping is
  omitted. The adapter fills nothing, because every captured step of this
  portal's scheduling flow (type grid, calendar, time range, confirm) renders no
  text input. Filling needs a portal whose flow actually renders required
  fields, plus markup to resolve them by label.
- **Retries are bounded and never applied to a commit.** Only read/settle steps
  retry. The executor reconciles a commit by re-reading portal state; no code
  path replays a submission.

## The lookup execution path, built not yet live-verified (2026-09-20)

- **The Phase 2 runner is deterministic-test-verified (18 cases), not yet
  verified against the live sandbox.** `scripts/ni_lookup_validation.py` is the
  acceptance run (record / address / ambiguity / mismatch probes against the
  Commerce Ave ground truth); until it passes live, selectors for the APO
  address mode and the zero-result wording are evidence-based but unproven on
  today's portal build.
- **Mode-label matching is substring-based and agency-configured.**
  `SEARCH_MODE_LABELS` matches NI's observed wording ("permit number",
  "address", "parcel", "business name"); a differently-worded dropdown could
  match nothing, in which case the runner reports `search_form_failed` instead
  of guessing — honest, but the lookup stops.
- **Ambiguity thresholds are first-pass values.** 0.75 minimum confidence and
  0.10 separation were chosen from the ranking weights, not tuned from live
  retrieval metrics; expect adjustment after the first live ambiguity probe.
- **Record matching is identity-first and token-boundary (2026-09-20).**
  `rank_results` treats an exact record number or parcel as decisive (band HIGH)
  and everything else as corroboration: a lone street-name hit scores 0.40
  (LOW), an exact address alone 0.80 (MEDIUM). Street, permit-type and applicant
  matching is whole-token, not substring — which is what stops `main st` from
  matching `domain st`, a real wrong-record class. Duplicate pagination rows are
  collapsed before ranking, and more than 50 indistinguishable candidates
  returns `TOO_MANY_RESULTS` rather than the top of a crowd. The address
  fallback ladder drops optional fields (ZIP) before broadening to a
  street-only search. Adversarial coverage: `tests/test_lookup_adversarial.py`
  (43 cases — messy language, near-miss record prefixes, parcel/ordinal/
  directional/unit formats, ambiguity, over-broad searches, applicant
  disambiguation, metric aggregation, determinism).
- **Retrieval KPIs are recorded and aggregated (2026-09-20).**
  `LookupRunner` folds every lookup into a `LookupMetrics` object (attempts,
  successes, exact matches, ambiguity, retries, browser actions, wrong records),
  so wrong-record rate = 0 is measurable per run and per runner.
  `LookupMetrics.combine`/`as_dict` aggregate raw counters (never averaged
  rates); `RunLogger.log_metrics` persists a named snapshot to the run log;
  `licet/eval/harness.score_runs(..., lookup_metrics=...)` adds a `lookup_kpis`
  block to a suite report; `scripts/ni_lookup_validation.py` writes the live
  aggregate into its report and JSONL log. `LookupRunner` is also verified to
  return the same record across 10 identical runs and to re-read (not replay)
  search results on a second run (`tests/test_lookup_runner.py`).
- **Applicant-only lookups have an explicit disambiguation rule (2026-09-20).**
  A contact name is not a property identity, so it resolves only as the *sole*
  matching candidate (optionally narrowed by permit type) and is reported at a
  capped MEDIUM confidence (`APPLICANT_UNIQUE_CONFIDENCE`); two matches stay
  AMBIGUOUS and no record is opened. With an address or record number present,
  the applicant is corroboration in the normal ranking path instead.
- **The widened-date retry exists but the live trigger is unmeasured.** NI's
  pre-filled window (09/18/2024→09/18/2026) is known to hide records; how often
  it bites on the eval records is not yet measured.
- **Result-table parsing still runs regex against page HTML.** It preserves
  empty cells (the My Records empty-Project-Name trap) and falls back to visible
  text, but a grid rendered through a frame the runner does not read would
  surface as `search_results_parse_failed`, not silently as zero results.

## The planner loop, live (2026-09-20)

- **The suite scores 20/20, but not from a single run.** The first pass scored
  17/20; the three failures (P08, P11, P20) were diagnosed, fixed and re-run, and
  P13 was re-verified after the fix. P15's re-run died on an OpenAI quota error
  and is therefore a pre-fix run. A clean single-revision 20/20 needs one more
  full pass with credits available.
- **Convergence is only *nudged*, not enforced.** `AgentState.note_facts` counts
  observations that add no durable fact and the loop sends an advisory message
  after four (twice at most). A model that ignores both nudges still runs to the
  step budget, and a run that ends on an action fails `result_verified` —
  correctly, since nothing verified the last change.
- **A run is not cheap, and an empty wallet looks like a bad agent.** The suite is
  ~20 minutes and ~1.2M input tokens for 20 cases (heaviest case 122k). The first
  version of one case was 377k, because ACA's `__VIEWSTATE` (98 KB) was being sent
  as a field value; hidden form state is now dropped and each page observation has
  a hard character budget, but the conversation still grows with every step and
  older page detail is trimmed to the last two observations rather than summarised.
  A quota/outage failure propagates as `ModelError` (never an empty answer), which
  the runner records as a case with no result — the scorer then reports it as
  "answered without reading", which is honest but does not say *why*.
- **The model will quote whatever the portal shows, including PII.** P20's answer
  included the test account's email address and the applicant's business address,
  read straight off Record Info. Correct behaviour for a read task, and a real
  consideration before any answer is shown to someone other than the account
  holder: there is no field-level redaction in the final answer.
- **`--all` runs cases sequentially on one browser session, one login per case.**
  That is slow but isolates cases; there is no parallel runner and no resumption
  (a crash ends the process, though each batch's runs file survives).
- **The model will recall a portal if the prompt lets it.** Asked for "the
  permit for 801 Windward Way" with no portal in context, the live model's first
  call was `navigate https://aca-prod.accela.com/TAMPA/Default.aspx` — a
  production municipality. Two mitigations, neither a guarantee: the system
  prompt names the sandbox and forbids other agencies, and
  `scripts/ni_agent_run.py` refuses any target that is not on
  `aca-test.accela.com`. A model that ignores both would still be *served* by
  the client; there is no in-client host allowlist.
- **Stall detection is heuristic.** "No progress" now means the same URL, the
  same flow step and the same page content for four consecutive observations.
  That survives wizards and record-section postbacks, which is what the live
  runs demanded, but a page that changes trivially on every read would never trip
  it (the step budget is the backstop), and a page that is genuinely static does
  trip it — which is the intended reading.
- **`read_page` cannot see everything the planner might want.** Flow position is
  derived from the URL plus the page's own wording, so a step whose text matches
  no marker reports the flow's first step. Section state (which record section is
  open) is still not URL-addressable and is not tracked in `AgentState`; a
  planner decision that depends on it depends on the last observation's text.
- **A held action ends the run rather than asking mid-run.** There is no
  interactive approval channel yet: `approval_required` stops the run, and the
  report says what was held. Re-running with an approval grant is the current
  mechanism.

## Design decisions taken during the Phase 0 review (2026-09-20)

- **`go_back` is not exposed to the model.** ACA is WebForms: history
  navigation resubmits the previous postback, which is exactly how a duplicate
  record was created live (`BLD26-00466` beside `BLD26-00467`). Re-navigation
  (`navigate`) is the supported way back.
- **`refresh` is not exposed either, for the same class of reason.** A reload
  that replays the last POST would resubmit a postback exactly like history
  navigation; `navigate(current_url)` is a plain GET and is the safe
  equivalent when the model needs a clean re-read of the current page.
- **Consequential actions are held at the tool layer, not the planner.** A
  `click` resolves to a semantic action and passes through
  `licet/safety/guard.py`; on the NI apply wizard the review step's `Continue`
  *is* an application submission (no payment gate, no agree checkbox), so it is
  held for approval. An unclassifiable call is blocked rather than guessed.
- **The recon scripts are deliberately outside the safety model.** They ticked
  the `CapApplyDisclaimer` legal checkbox with no approval, which is a
  violation of Licet's own rule — acceptable for one-off data collection, but
  it must never be a pattern the runtime inherits. Anything that runs as Licet
  goes through the dispatcher.
- **The dispatcher path is replayed live (2026-09-20, 16/16 checks).**
  `scripts/ni_dispatcher_replay.py` drives the real package —
  `SolariSession` → `SolariClient` → `ToolDispatcher` → `AgentState` — on a
  read-only flow: navigate → login (SSO iframe) → My Records → click a record
  number → read the record detail. Verified live: the async client's login, flow
  position detection, the field/frame inventory, the guard holding a
  consequential click before it reached the browser, and ACA error-page
  classification. Evidence: `logs/ni_backoffice/*_dispatcher_replay.json` plus
  the paired screenshot.
- **Opening a search result requires an explicit `intent`.** A grid link is
  neither a known read label nor a flow commit point, so an unclassified click
  is blocked by design (the replay passes `intent="open_record"`). The planner
  prompt must make that explicit; the tool schema already enumerates the value.
- **The model boundary is live-verified, and the target portal must be pinned**
  (2026-09-20, 11/11 checks). `scripts/ni_model_smoke.py` makes real OpenAI API
  calls: text reply with usage, tool call round-trip (decoded args, raw blob,
  call id, no invented arguments outside the schema), `intent="open_record"`
  supplied unprompted for a grid link, a dead primary served by `gpt-5.4-mini`,
  and a bad key surfacing as `ModelError`. Two things to carry forward: the
  fallback used to be stored and never read, so a primary outage took the run
  down with a working fallback configured and unused; and asked to find a permit
  with **no portal in context**, the model's first call was `navigate` to
  `https://aca-prod.accela.com/TAMPA/Default.aspx` — a *production* municipality
  portal. The planner prompt has to inject the configured portal and forbid
  other agencies, because the model will otherwise supply a plausible Accela URL
  from prior knowledge. One-portal scope has to be stated, not assumed.
- **Section click-through is verified (2026-09-20, 14/14 checks).**
  `scripts/ni_section_clickthrough.py` opens `Record Info`, `Payments` and
  `Attachments` through the dispatcher; each changes the visible content while
  **the URL stays the same** (they are `__doPostBack` anchors), so the open
  section must be tracked in state — `accela.locate` can only tell us we are on
  `record_detail/summary`.
- **The wizard's flow step is content-derived, not URL-derived.** Every step of
the scheduling dialog shares one `CapDetail.aspx` URL, so `flow_step` would sit
at `select_record` forever and the guard's commit-point rule could never fire.
`accela.schedule_step(text)` reads the popup's own wording, and the dispatcher
prefers the client's content-derived position over the URL. A bare control id
passed as `by="selector"` is also resolved as `#id` / `[id="..."]` now — ACA ids
are not CSS selectors, and clicking one silently matched nothing while the field
inventory listed it.
- **Resolve section/nav links by exact text, not `has-text`.**
  `a:has-text('Attachments')` matched an invisible wrapper that *contains* the
  text, making a working link look dead; `a:text-is(...)` hits the real anchor.
  The client now tries exact before substring, and reports the
  matched-but-hidden selectors via `BrowserError.NOT_ACTIONABLE` so this is
  diagnosable instead of looking like a missing control.

- **No record on this sandbox can be scheduled (measured 2026-09-20).** All 8
owned records were walked to the wizard's calendar
(`scripts/ni_availability_sweep.py`): every day cell in all three rendered months
(Sep/Oct/Nov 2026) is `CalendarDayInactive` with `title="Cannot schedule
inspection on this date"` — 91 cells, 0 selectable — for every record that
offers a type at all. Two records (New SFR `BLD26-00470`, Right of Way
`BLD26-00472`) offer **no inspection types at all**. Likely cause: every owned
record is **Submitted, not Issued**, and ACA normally only allows citizens to
book inspections on issued permits. Consequence: the flagship prompt's last leg
("schedule the earliest available inspection next week") is **unachievable on
this environment**, and the correct outcome is an accurate can't-finish report.
The eval fixtures encode that (`expects="cannot_finish"` in
`licet/eval/prompts.py`, ground truth in `SCHEDULING_GROUND_TRUTH`), because a
prompt that expects a booking here would score every honest run as a failure and
reward a fabricated success. Re-measure before assuming this still holds.
- **The wizard's Continue must not be force-clicked.** The popup's
`ctl00_phPopup_lnkContinue` is rendered `disabled` with the real postback in
`href_disabled`; force-clicking (the fallback that legitimately fires
`#btnSearch`) would fire a postback the portal explicitly disabled.
- **One portal only.** Licet v1 targets a single Accela Citizen Access
  sandbox instance. No cross-municipality support.
- **Sandbox vs. production drift.** Sandbox/test records may not reflect how
  a production Accela instance is configured or behaves.
- **No payment automation.** Licet never submits a payment on the user's
  behalf.
- **No legal submissions without approval.** Official application
  submissions and legal attestations always require explicit user
  confirmation.
- **Dynamic layouts may cause failures.** Accela's UI can vary by
  configuration; selectors that work in the sandbox may not generalize.- **No cross-municipality assumptions.** Two municipalities running Accela
may configure it differently — Licet cannot assume portability across
instances without re-verifying the UI map.

## Found during live-portal recon (2026-09-18)

- **URL-blind navigation.** Search results and record sections are
postback-rendered inside `CapHome.aspx`/`Welcome.aspx`; there is no URL for
"search results" or "fees tab." The agent must track flow position in state,
not from URLs.
- **Invisible date filters.** ACA pre-fills the search date range per agency
(e.g. Omaha `05/04/1999→today`, SLC `06/01/1977→today`); a valid address can
return zero rows because of it. Widening dates should be a standard retry.
- **Dead-but-rendered UI.** Agencies expose nav items marked `Active: False`
(observed on Meridian) — clickable-looking links that do nothing.
- **Auto-postback dropdowns.** Changing `ddlSearchType`/`ddlGSPermitType`
reloads the page and resets dependent fields; `select` without waiting races.
- **Section availability is agency-configured.** Documents/fees/inspection
sections may be absent or read-only per module. Absence is normal, not an
error.
- **Scheduling needs an account.** Anonymous users can search and read records
but cannot schedule; evals for scheduling require a public-user test account
associated with the test records.
- **Login is an SSO iframe flow.** ACA login (CivicId SSO) renders its
credential form client-side, likely inside an iframe/popup — the browser tool
caller must handle frame contexts, and Solari profile/storageState handling
must be verified against it.
- **Gated actions fail softly.** Auth-required actions raise JS notice dialogs
(e.g. "Please Login") instead of redirecting; failure detection must read page
text.
- **Idle session timeouts.** `SessionTimeout.js` site-wide means long agent
runs may hit re-login prompts mid-workflow.

## Found during live Solari runs (2026-09-18)

- **The loading mask eats clicks.** ACA's global loading overlay
(`#divGlobalLoadingMask`, Silverlight-era iframe) stays in the DOM "hidden"
but intercepts pointer events; normal clicks time out. Every tool session
must inject `display:none !important` for it after each postback.
- **Disabled-looking controls still fire.** `#btnSearch` keeps a
`ButtonDisabled` class no matter what, but force-clicking it submits the
search. Button styling is not a reliable enabled/disabled signal.
- **Mode switches replace the whole form.** Changing the search-type dropdown
can remove all previously known field IDs; agents must re-read the form after
every postback instead of caching selectors.
- **Postback races.** Elements detach from the DOM mid-click during
postbacks; clicks need re-resolution/retry, not one-shot locators.
- **Deep links can be dynamically gated.** A record URL that renders for curl
may be blocked in a real browser session by per-agency verification
requirements (observed on Meridian: "approved Address/Parcel Verification").
- **Sandbox data inventory is now KNOWN — and missing the target types.**
Full back-office sweep (2026-09-19, `scripts/ni_record_inventory.py`):
123 records across all 12 modules, but **zero** match Licet's 7 target
categories (Commercial Alteration, Residential Addition, Commercial
Electrical, New SFR, Solar, ROW Use, Sign-Temporary). The only Building
permits are Mechanical (31 residential / 27 commercial); everything else is
SRs, Fire, EnvHealth, AMS, Enforcement, Licenses, one Planning site plan.
Full data: `logs/ni_backoffice/inventory/merged_inventory.json`.
Also: the anonymous ACA portal returns **0 rows for every one of the 123
records** — back-office-visible records are not publicly searchable, so
citizen-portal evals need records created/associated via a public-user
account.
- **RESOLVED 2026-09-19: all 7 target types ARE configured.** The back-office
inventory only shows types that HAVE records; the portal's configured type
catalog lives in the search page's permit-type dropdown
(`ddlGSPermitType` on `CapHome.aspx?module=<M>`, populated without login).
Full sweep (`scripts/ni_type_catalog.py`,
`logs/ni_backoffice/inventory/*_catalog.json`): Building 102, AMS 314,
EnvHealth 65, Licenses 72, Planning 22, Enforcement 18 portal-exposed
types (PublicWorks/Fire/ServiceRequest/Cannabis/Treasury expose 0 via
CapHome). Exact matches for all 7 targets, all in module Building:

  | Eval target | cap type value |
  |---|---|
  | Commercial Alteration | `Building/Commercial/Alteration/NA` |
  | Residential Addition | `Building/Residential/Addition/NA` |
  | Commercial Electrical | `Building/Commercial/Electrical/NA` |
  | New SFR | `Building/Residential/New/SFR` |
  | Solar Permit | `Building/Solar/NA/NA` |
  | Right of Way Use Permit | `Building/Right of Way/NA/NA` |
  | Sign - Temporary | `Building/Sign/Temporary/NA` |

  So records can be created by APPLYING on the citizen portal (as planned,
  tied to the public-user account) — no back-office seeding or eval-type
  remapping needed. The CapWiz-style URL is 404 on NI; the apply flow must
  be entered through the portal UI (post-logged-in apply link), not a
  deep link.
- **Full eval test set issued: all 7 target types applied for
  (2026-09-19/20).** `scripts/ni_apply_submit.py` did the first (Sign -
  Temporary, BLD26-00467); `scripts/ni_apply_batch.py` then issued
  Commercial Alteration (`000000014`), Residential Addition
  (`BLD26-00468`), Commercial Electrical (`BLD26-00469`), New SFR
  (`BLD26-00470`), Solar (`BLD26-00471`) and Right of Way Use (`BLD26-00472`)
  — plus a spare duplicate Sign - Temporary (`BLD26-00466`) from the first
  run. Every record is owned by the public-user test account → safe targets
  for scheduling/cancel evals. Ground truth (capIDs, status, address,
  expiration, section links) in `licet/eval/records.py` KNOWN_RECORDS;
  raw capture in `logs/ni_backoffice/inventory/*_my_records.json`.
  Apply-flow quirks that cost us iterations:
  - Entry: `CapApplyDisclaimer.aspx?module=Building&TabName=Building
    &FilterName=PMT_GENERAL` (agree + btnNextStep); direct CapApply*.aspx
    URLs without that shape land on Error.aspx.
  - CapType: cap types are RADIO values (`Building/Sign/Temporary/NA`);
    `check()` fires SelectNode. Don't call SelectNode twice — it toggles.
  - CapEdit: only Street No / Street Name / Zip are required; Zip is a
    MaskedEdit field (#####) that ignores `fill()` — needs real keystrokes.
  - Applicant contact is mandatory and opens an IFRAME overlay
    (People/ContactAddNew.aspx). Save INSIDE the frame
    (`#ctl00_phPopup_btnSave`) — the parent's btnSave is
    save-and-resume-later. Leave phones EMPTY (mask rejects typed digits,
    field optional); Type → "Applicant"; Name of Business required.
  - CapConfirm (Review) has no agree checkbox on NI; Continue on Step 5
    issues the record immediately, no payment gate for Sign - Temporary.
- **Per-type AppSpec required fields must be resolved generically, not by
  id (2026-09-20).** Each cap type adds its own required fields with
  arbitrary id suffixes: Commercial Electrical used
  `AppSpec…_txt_0_0/_0_2` (floor area / cost), Solar `_0_1.._0_6` (modules /
  inverters / roof area / % covered), and Right of Way added a whole
  "PROJECT DATES" section — `_txt_3_0` Schedule Start Date and `_txt_3_1`
  Estimated Completion Date, both required, both MaskedEdit MM/DD/YYYY
  (which ignore `fill()`). Row Use stalled 3 rounds on exactly this until
  the batch script started keying required-field values off
  `aria-required`/`title="Required"` + `placeholder`/`fieldname` instead of
  control ids. Filling required-and-empty fields before every Continue is
  what makes the flow work for types nobody hand-mapped; the ACA validation
  panel (`skipTo('ctlId')` links) names the missing controls when a click
  fails to advance.
- **altID format varies per record TYPE, not per agency (2026-09-20).**
  Six of the seven types rendered `BLD26-004xx`; Commercial Alteration
  rendered `000000014` while sitting in the very same capID sequence
  (`REC26/00000/000QB`). Always parse the Record Issuance sentence
  (`Your Record Number is <ID>`) rather than a fixed BLD-pattern regex, and
  confirm against My Records.
- **My Records is the authoritative read-back (2026-09-20).**
  `Cap/MyRecordsCap.aspx?TabName=Home` (logged in) lists the account's
  records — 8 rows, `Showing 1-8 of 8` — with columns Date / Record Number /
  Record Type / Project Name / Address / Status / Action / Description /
  Expiration Date / Short Notes. Each row carries a detail deep link
  `/NULLISLAND/Cap/CapDetail.aspx?Module=<M>&TabName=<M>&capID1/2/3
  &agencyCode=NULLISLAND&IsToShowInspection=`. Two gotchas: the grid has an
  EMPTY Project Name column, so parsers must keep empty cells or every
  column shifts left; and the href is **site-absolute** (`/NULLISLAND/...`),
  so prefixing the agency URL yields `/nullisland/NULLISLAND/...` →
  "The file does not exist". Reader: `scripts/ni_my_records.py [--details]`.
- **Record detail sections render for all 8 owned records (2026-09-20).**
  Same section set on every type: `Schedule an Inspection | Record Info |
  Payments | Attachments`. This unblocks the previously "blocked on known
  records" checklist items — scheduling is now reachable end-to-end against
  our own records (was: JS "Please Login" notice + empty account).
  Click-through of the postback section links themselves is still unverified
  (presence only), as is which inspection types each record offers.
- **Inspection calendars: confirmed working; per-type mapping deferred to
  the apply step (2026-09-19).** Record REC26-00000-000AE (Building/
  Commercial/Mechanical) has an existing "Mechanical Final" inspection,
  status Insp Scheduled, date 05-20-2026 (inspectionID 18482246) — the
  calendar engine works for Building types. Whether each of the 7 TARGET
  types has inspection types attached will surface per-record when we apply
  (each record's Schedule form lists them); the AV cap-type admin that
  would answer it directly is 1015-banned. Citizen side confirmed: the
  logged-in public account sees the "Schedule an Inspection" flow with
  "No records found" (zero records associated yet, as expected pre-apply).
  Useful deep link captured:
  `inspectionDetailCapSpecific.do?mode=view&fromPage=inspectionDailyList
  &isCapFrame=Y&serviceProviderCode=NULLISLAND&capID1/2/3&inspectionID
  &inspectionType&scheduledDate&module=Building`.
- **Cloudflare rate-limits the sandbox (Error 1015, observed 2026-09-19 ×2).**
  ~15 heavy Solari sessions in one day triggered a temporary IP ban on
  nullisland-test-av.accela.com; after an ~80-min cooldown, the FIRST deep
  portlet GET re-banned instantly (root `/` passes, deep `.do` hits don't).
  aca-test.accela.com (citizen portal) was NEVER affected — login + pages
  kept working. Rules of thumb: treat the AV host as burned for the day,
  do heavy sweeps in ONE batched session, prefer the citizen portal, and
  probe with a root GET only (deep probes re-arm the ban). Eval runners
  need 1015 detection + long cooldowns on the AV host specifically.
- **The AV back office "Calendaring & Inspections" admin is SPA-only**
  (Angular `spacev360`): menu items are `href="#"` JS routes, the Inspections
  space URL renders the record dashboard, and no calendar-admin `.do` URLs
  leak into any frame HTML. Record-centric portlets (`CapTabSummary.do` →
  `inspectionListCapSpecific.do`) remain the reliable URL-addressable path
  for inspection data.
