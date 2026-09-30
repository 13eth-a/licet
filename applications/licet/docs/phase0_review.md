# Phase 0 review — assumptions, failures, schemas, browser abstractions

Reviewed 2026-09-20 against the Phase 0 checklist. Method: read every module
in `licet/`, all 11 tests, `docs/*`, and all 25 scripts in `scripts/`;
cross-checked every claim either with a grep or with live evidence captured
in `docs/accela_ui_map.md`, `docs/known_limitations.md`, and
`logs/ni_backoffice/inventory/`.

Severity tags: **BLOCKER** (fix before writing the planner), **HIGH**,
**MEDIUM**, **LOW**.

## Verdict

The environment half of Phase 0 is genuinely done — Solari drives the live
portal, 8 owned test records exist with captured ground truth, 18 prompts
and 8 criteria are written. But three structural assumptions in the
implementation are wrong, and they are wrong in ways that Phase 1 will
inherit silently. The most important one: **the safety boundary and the stop
conditions are not reachable from the code path that acts.**

The meta-problem behind all of it: `scripts/` — the only code that has ever
touched the real portal — imports `licet/` **zero times** (verified with
grep). `licet/` encodes a pre-recon guess about how ACA behaves; the
behaviors we actually proved live in 25 ad-hoc scripts. Until those two are
reconciled, every abstraction in `licet/` is untested theory.

**Status 2026-09-20:** all four headline findings were acted on the same day —
see the FIXED notes on §1, §2, §3 and §4. The proven portal knowledge now lives
in `licet/browser/accela.py`, the guard and dispatcher are the only path to the
browser, and 103 tests cover the pure logic. The dispatcher path has since been
replayed **live** (16/16 checks, `scripts/ni_dispatcher_replay.py`): login
through the SSO iframe, My Records, opening a record, reading its detail, the
guard holding a consequential click, and live error classification. The honest
remaining gap is the planner loop itself, and `scripts/` still being mostly
standalone.

---

## 1. BLOCKER — the safety boundary cannot fire

> **FIXED 2026-09-20.** The semantic layer now exists: `licet/browser/dispatcher.py`
> maps every tool call onto a classified action (`resolve_action`), and
> `licet/safety/guard.py` decides allow / hold / block *before* the browser is
> touched. `go_back` is removed from the model-facing tool set (WebForms history
> resubmits postbacks), the flow commit point that issues a record resolves to
> `submit_application` and is held, and unclassified calls are blocked rather
> than guessed. `intent` in the tool schema is enumerated from the same
> catalogue the guard classifies, so the model cannot invent an action the
> guard has never seen. Covered by `tests/test_guard.py` and
> `tests/test_dispatcher.py`.

`licet/safety/risk_levels.py` documents a two-bucket boundary and
`tests/test_safety.py` proves `classify()` works. Verified by grep:

```
classify(                 -> 0 callers outside its own definition + tests
requires_confirmation(    -> 0 callers outside its own definition + tests
check_stop_condition(     -> 0 callers at all
request_approval(         -> 0 callers at all
record_failure(           -> 0 callers at all
```

Nothing in the repo calls any of them. The boundary is a documented
intention, not a control.

The design error is *where* the risk key lives. `risk_levels` classifies
**semantic** actions (`submit_application`, `submit_payment`), but the layer
the planner actually invokes is 8 **generic** verbs (`click(selector)`,
`type(selector, text)`). There is no mapping from "click `#…btnContinue`" to
"submit_application", so an agent can submit an application, accept a legal
attestation, or pay a fee without a single confirmation check, because the
only string in flight is `"click"`.

Live evidence that this is not hypothetical:

- The NI apply wizard's commit point is **one `Continue` click**
  (`CapConfirm`, `stepNumber=5`) that issues the record immediately — no
  payment gate, no agree checkbox, no distinct confirmation URL. So
  `submit_application` (confirmation-required) and `click` (unclassified)
  are literally the same action.
- **Our own recon scripts auto-checked a legal disclaimer checkbox** on
  `CapApplyDisclaimer.aspx` (and an "agree" box on CapConfirm) with no
  approval — a live violation of Licet's own rule ("never auto-accept terms
  or signatures"), committed by Licet's own tooling.
- A duplicate record exists (`BLD26-00466` beside `BLD26-00467`, both
  Sign - Temporary at the same address) because a run continued past the
  Record Issuance page. That is the same "commit point not recognized"
  failure, and there is no idempotency-by-intent or verify-before-report
  rule that would have caught it.
- `go_back` is offered to the model even though WebForms history resubmits
  postbacks — a duplicate-submission primitive with no guard.

**Recommended fix.** Introduce the semantic action layer the classifier
already assumes: tools emit their intent (`search_permit`, `open_record`,
`read_section`, `schedule_inspection`, `submit_application`, …), the guard
runs in the tool *dispatcher* (not in the planner's good intentions), and
each flow has a declared **commit point** so the issuing step is recognized
before it is clicked. Approval must actually populate
`AgentState.pending_approval` and then trip `APPROVAL_REQUIRED`.

## 2. BLOCKER — the browser abstraction contradicts every live finding

> **FIXED 2026-09-20.** `SolariClient` is now async, frame-aware, and
> ACA-aware (postback settle + mask neutralization, force-click fallback,
> masked keystrokes, frame/popup-aware resolution, structured errors, and a
> `read_page` that returns flow position + field inventory + validation panel +
> frames), and it is written against a `PageLike` protocol so all of it is
> tested against a fake page (`tests/test_solari_client.py`). `accela.py` holds
> the verified portal knowledge as data. Replayed live on 2026-09-20 — 16/16
> checks in `scripts/ni_dispatcher_replay.py`, including login through the SSO
> iframe and the guard holding a submission click before it reached the browser
> (see the status note in `docs/architecture.md`).

`licet/browser/tools.py` + `solari_client.py` define a synchronous,
selector-string, single-frame API. Everything we learned live says the API
must be different:

| Contract today | What the live portal requires |
|---|---|
| sync methods returning `SolariResult` | Solari's SDK is **async** (`await launch()`, `await page.goto()`); all 25 scripts are `asyncio`. The sync stub will force a rewrite of every caller, including `TOOL_DEFINITIONS` consumers and tests. |
| `click(selector)` — no frame parameter | Login lives in an iframe (`…/CommunityView/login-panel`), contact/LP/education dialogs are iframe overlays (`ctl00_phPopup_*`, `dvACADialogLayer`). Scripts needed `find_popup_frame()`/`run_popup_to_close()` heuristics because the API has no frame concept. |
| plain click | `#btnSearch` carries a cosmetic `ButtonDisabled` class and only a **force-click** submits; postbacks **detach** elements mid-click ("element was detached from the DOM, retrying"). Click needs force fallback + re-resolution, not one-shot locators. |
| `type(selector, text)` → `fill()` | MaskedEdit fields (`class="… maskedfields …"`, e.g. Zip `#####`, date `MM/DD/YYYY`) **ignore `fill()`** — they need real keystrokes. Every apply script needed a masked-field branch. |
| `wait(seconds)` | Must mean *settle the postback* **and re-inject** `#divGlobalLoadingMask { display:none !important }` — the hidden Silverlight-era overlay intercepts pointer events after every postback. A fixed sleep races. |
| `select(selector, value)` | `ddlSearchType` / `ddlGSPermitType` are **auto-postback** dropdowns that replace the whole form and invalidate previously cached field ids. Select must wait for the reload and the caller must re-read the form. |
| `read_page()` → `page_summary` + `url` | Needs: url **+ flow position** (urls barely change), a **field inventory** (ids/labels/required flags), and the **validation panel** — the scripts depended on `skipTo('ctlId')` links to know what a Continue click was missing. Also: bodies can be an empty shell mid-redirect (scripts poll `len(body) > 300`), and pages are 100–500 KB of encrypted `__VIEWSTATE`. |
| `go_back` exposed | Should be discouraged/removed for WebForms; prefer re-navigation. |
| `success: bool` + `error: str` | Needs an **error taxonomy**: detached-element retry, JS notice dialog ("Please login to continue" — no URL change), dynamically gated deep link, dead-but-rendered link (`Active: False`), Cloudflare 1015, session timeout. The planner's recovery differs per class; one bool cannot drive it. |
| LLM authors `selector` strings | ACA ids are ~60-char `ctl00_PlaceHolderMain_…` values and per-agency config-driven. Every script ended up resolving **semantically** (by `fieldname`, `aria-label`, text, `aria-required`). Selector-first is the wrong default. |

Also: `_default_tools = BrowserTools()` is constructed at import, so config
is bound at import time and there is no per-run session object (`TOOLS` is
never used by anything). Same pattern in `config.py` (`CONFIG =
load_config()`) and `stop_conditions.py` (`from licet.config import
MAX_STEPS`).

**Recommended fix.** Make `SolariClient` async and frame-aware; encode each
row of that table as tool *behavior* (with a test), not as prose the planner
must remember. Add the observation tools the failures actually demanded —
field/form inventory, validation errors, frame/popup inventory, HTML dump —
because those diagnoses came from parsing HTML by hand in every script.

## 3. HIGH — half the stop conditions can never fire

> **FIXED 2026-09-20.** `licet/agent/state.py` now carries a producer for each
> condition (`record_missing_information`, `record_ambiguous_candidates`,
> `record_no_valid_action`, `record_portal_issue` + clear/resolve
> counterparts), `record_failure`/`record_success` are keyed by
> (page, action, args) with successes clearing the count, a same-URL stall
> detector was added, thresholds became per-run parameters instead of
> import-time constants, `describe_stop` supplies the explanation the module
> docstring always promised, and `tests/test_stop_conditions.py` covers all of
> it (21 new tests). The analysis below is kept as the record of what was
> wrong.

`StopCondition` declares 8 members; `check_stop_condition` can only return 4.

- `MISSING_INFORMATION`, `NO_VALID_ACTION`, `PORTAL_UNAVAILABLE`,
  `AMBIGUOUS_RECORD` have **no producer anywhere**. Nothing in `AgentState`
  models "an unknown required value", "no slots available", "portal
  unhealthy", or "several candidate records".
- `AMBIGUOUS_RECORD` is exactly what the prompt *"Find the permit for an
  address that matches multiple records"* needs, and there is no
  representation for candidates.
- NI's invisible date window (`09/18/2024→09/18/2026`) makes a valid search
  return 0 rows, so "no results" must **not** become `NO_VALID_ACTION`
  before date-widening is retried. There is no `assumptions` /
  `retries_exhausted` in state to carry that.
- `SessionTimeout.js` is loaded site-wide and the AV host can return
  Cloudflare **1015** — both are `PORTAL_UNAVAILABLE`-class events with no
  producer and no cooldown/backoff concept.
- `REPEATED_ACTION_FAILED` is buggy as written:
  `record_failure` dedupes on the **action string only** (no args, no page),
  so the same selector failing once on two different wizard pages collapses
  into one counter; `attempt_count` is never reset on success, so a click
  that fails twice and then succeeds still counts; and the threshold is
  `attempt_count > 2` (third attempt halts). A long flow that legitimately
  clicks the same `Continue` selector many times (the row_use apply took 13
  wizard iterations) is one flaky postback away from a false stop.
  The key should be `(page, action, args)`, reset on success, with a
  per-page stall detector (scripts used "same URL 3 rounds → abort").

`stop_conditions.py` also has **zero tests**, despite being the one module
with a real logic bug.

## 4. HIGH — the schema cannot represent the records we created

> **FIXED 2026-09-20.** `licet/schema/permit.py` now has `RecordRef`
> (capID1/2/3 + module + agencyCode, with `detail_url()` matching the verified
> shape), a `submitted_date` distinct from issued/expiration, raw + normalized
> status, `Inspection.inspection_id` and split scheduled/completed dates,
> optional fee amounts alongside portal text, `Document`, provenance-marked
> `Fact`s, and `schedulable_inspection_types` with `missing_inspections()` as
> the answer to "what inspection needs to happen next". Tested against the real
> records (`tests/test_schema.py`).

Checked against the 8-record capture (capIDs, statuses, fields in
`licet/eval/records.py`):

- **Identity.** The displayed record number is per-type: Commercial
  Alteration reports `000000014` while every other type reports
  `BLD26-004xx`. The *stable* identity is
  `capID1=REC26 & capID2=00000 & capID3=000QB` + `Module=Building` +
  `agencyCode=NULLISLAND` — which is what a deep link needs. `permit_id: str`
  alone cannot address a record; add a `RecordRef`.
- **No created/submitted date.** My Records' first column is `Date`
  (09/19/2026) and the record detail has no issued date, yet `Permit` offers
  only `issued_date` / `expiration_date`. Our records read
  `Expiration Date: 01/31/2026` while being **Submitted, not issued** — that
  value is agency config, not an outcome. Reasoning about "is this permit
  expired?" from `expiration_date` would be wrong today.
- **No inspection-type universe.** The checklist's central question —
  "what inspection needs to happen next" — is `(required types from the
  scheduling form)` minus `(inspection history)`. The schema has no field
  for the scheduling form's type list, so that inference is unrepresentable.
- **`Inspection` is too thin.** Live: `inspectionID 18482246`,
  type `Mechanical Final`, status `Insp Scheduled`, date `05-20-2026`. The
  model drops the **inspection id** (needed for reschedule/cancel),
  conflates scheduled/completed/resulted dates into one `date`, and stores
  only the raw status string with no normalized counterpart (evals need to
  compare both).
- **`Fee.amount: float` + `paid: bool` are required.** The portal renders
  fees as text/balance, and payment is explicitly out of scope — required
  parsed numerics invite parse failures on `$1,234.56` and blanks. Keep the
  raw string, make the parsed value optional.
- **`documents: list[str]`.** "Attachments" is a postback **link**, and
  report export opens new tabs (`target="_blank"`). Filenames cannot express
  "unknown until clicked" or a download artifact.
- **No provenance.** `outstanding_requirements` and `next_action` mix
  portal-stated facts with agent inferences — precisely the distinction two
  eval criteria (`final_answer_accurate` vs `correct_next_action_identified`)
  depend on.
- **Nothing constructs a `Permit`.** Verified by grep: only
  `tests/test_schema.py` instantiates it. `AgentState.known_facts: dict[str,
  Any]` is the real (untyped) store, so the schema is decorative and there
  are two competing sources of truth for permit data.

## 5. MEDIUM — state model gaps

- No **flow position**. `current_page` is a human label, but the wizards'
  real position lives in `stepNumber` / `pageNumber` query params
  (`CapEdit stepNumber=2 pageNumber=2`, `CapConfirm stepNumber=5`), and the
  docs already say URLs must not be trusted for this. Add
  `flow: {name, step, page}`.
- No **frame/popup inventory**, so the planner cannot answer "is the contact
  dialog currently open?" — the single most common branch in the apply flow.
- `current_permit: str` is not linked to any structured record object.
- No **evidence/provenance per fact** (source url, screenshot path, page
  hash), which makes `result_verified` and `final_answer_accurate`
  unscorable and makes failures hard to debug after the fact.
- `pending_approval` holds a single item; confirm steps can block on several
  actions at once (fee + attestation).
- `MAX_STEPS` is resolved at import; stop conditions are not per-run
  configurable or injectable.

> **PARTIALLY FIXED 2026-09-20.** Flow position now lives in `AgentState`
> (`flow_name` / `flow_step` / `flow_page`, kept current by the dispatcher via
> `accela.locate`), approvals have a real lifecycle (`pending_approval`,
> `is_approved`, `grant_approval`, `clear_approval`), and stop-condition
> thresholds are per-run parameters. Still open: a frame/popup inventory in
> state (it is in `read_page` data, not yet in `AgentState`),
> `current_permit` is not linked to a structured `Permit`, and there is still
> no evidence/provenance recorded per fact.

## 6. MEDIUM — the eval layer is not runnable against our own fixtures

- Prompts hardcode **"123 Main Street"**, which is documented to return
  **0 rows** on NI, and `{permit_id}` / `{nonexistent_permit_id}` placeholders
  have no substitution mechanism anywhere. As written, several prompts fail
  for fixture reasons rather than capability reasons. Bind them to
  `KNOWN_RECORDS` (which now exists).
- Prompts and criteria have **no ids**, so eval reports cannot be tracked
  across runs.
- Prompts are single-shot, but "What is the status of **this** permit?" /
  "…on **this** permit" imply prior conversation state — no multi-turn or
  state-reuse design exists.
- **The flagship prompt's first step is unverified on NI.** Its entry point
  is address search, but the verified read path is
  `MyRecordsCap.aspx → CapDetail.aspx` deep links; anonymous search returns
  0 rows for every sandbox record. Either verify that the logged-in search
  (Search Applications / APO lookup) finds our 8 records, or redefine the
  flagship entry point around a path we have proven.
- No ground-truth binding, no scoring protocol (pass/fail vs rubric), and no
  expected-vs-actual diff harness.

## 7. LOW / cheap

- **Logger**: 10/10 checklist fields are present, but there is no run-level
  record (models, sandbox, git sha, prompt id) and nothing writes
  `final_outcome`; no **credential redaction** (an account email/password
  typed into a field would be logged verbatim); no artifact/screenshot path
  field despite screenshots being the primary diagnostic; ACA bodies are not
  truncated; `LOG_DIR` is CWD-relative.
- **Config**: no timeouts/retries, no rate-limit cooldown, no log dir, no
  per-run `max_steps`; `SOLARI_BASE_URL` is unused by every script; model
  slugs are unvalidated (nothing resolves the slug the config named, so a bad
  slug fails at runtime). *Close-out: model ids are verified OpenAI ids
  (`gpt-5.6-sol` / `gpt-5.4-mini`), timeout and retries are configured, and
  `build_model` refuses to start without a key.*
- **Tests**: 11 green, but they test stub shape. In particular
  `test_tool_contract_has_exactly_eight_tools` **actively blocks** the tools
  §2 requires, and there is no test for `stop_conditions.py`.
- **Docs staleness (a real bug, because it is what the next agent reads)**:
  `docs/architecture.md` says "Solari itself and the Accela portal are
  stubbed/untested pending the manual sandbox walkthrough", and
  `solari_client.py`'s docstring calls itself a stub. Both are false as of
  2026-09-20 — Solari is live-verified 7/8 and has driven login, the full
  apply wizard, and My Records reads repeatedly.
- `logs/` is gitignored, so the captured HTML corpus is local-only. Curate a
  few non-sensitive pages into `tests/fixtures/` so the ACA parsing and
  summarization layers can be developed and tested **offline** instead of by
  burning live sessions.

## 8. Checklist items still genuinely open

- "Find records containing inspection history" / "Locate inspector comments"
  / "Determine how outstanding inspections are represented" — our 8 records
  have **no inspections**; the only NI record with one is
  `REC26-00000-000AE` ("Mechanical Final", `Insp Scheduled`, 05-20-2026).
  Inspector-comment location is still unmapped.
- "Schedule/reschedule an inspection manually if sandbox allows it" — **not
  done**. The scheduling section renders for all 8 owned records
  (unblocked as of 2026-09-20), but no inspection has been scheduled, and
  the per-record inspection-type list is unverified.
- "Identify confirmation screens" — partially: Record Issuance prints
  `Your Record Number is <ID>`; there is no distinct confirmation URL.
- Phase 0 sanity test "You can manually perform the flagship workflow" —
  **answered, and the answer was no.** The workflow was walked to the end
  (2026-09-20, read-only): record → scheduling context → type grid → calendar.
  Every day cell on every owned record is inactive, so **no appointment could
  be booked by hand either**. The blocker is the environment (all records are
  Submitted, not Issued), not the tooling. The flagship demo's success
  condition was redefined accordingly — see `docs/known_limitations.md` and
  `licet/eval/records.py:SCHEDULING_GROUND_TRUTH`.

## Close-out (2026-09-20)

The recommended order of work below was completed; the review's own findings
§1–§5 are all fixed, and §3 was marked fixed earlier.

1. `stop_conditions.py` rebuilt with producers for all eight conditions — done
   (`tests/test_stop_conditions.py`).
2. Client/dispatcher rewritten async, frame-aware, guard-gated — done, and
   replayed live (16/16, `scripts/ni_dispatcher_replay.py`).
3. Schema upgraded to `RecordRef` + dates + provenance + inspection types —
   done, and now actually constructed (`licet/schema/extract.py`).
4. Fixtures bound to `KNOWN_RECORDS` with ids and expectations — done, and made
   **runnable** (`licet/eval/harness.py`, `scripts/ni_eval_fixtures.py`). The
   harness rejects a fixture whose expectation this environment cannot satisfy,
   which is the failure mode that let the old suite rot unnoticed.
5. Import-time singletons for state/config injection — done; thresholds are
   per-run parameters and `RunLogger` is injectable into the dispatcher.

6. The model boundary: `licet/agent/model.py` — the prerequisite for the loop.
   Provider is configuration (`OPENAI_API_KEY`, with the Anthropic dependency
   swapped out on 2026-09-20), the client is injectable, and a missing key fails
   before the run instead of mid-run.

Still unbuilt, deliberately: the planner loop itself, and a live eval run (the
scorer is offline by design; no agent has been driven end-to-end yet).

## Recommended order of work

1. Fix `stop_conditions.py` (counter key/reset, stall detector, tests) and
   give the four dead conditions real producers. Small, pure, testable.
2. Rewrite `SolariClient`/`browser_tools` as async + frame-aware with the
   seven ACA behaviors baked in, the observation tools added, and the risk
   guard in the dispatcher with per-flow commit points.
3. Upgrade `schema/permit.py`: `RecordRef` (capIDs + module + agencyCode),
   submitted/created date, `inspection_id` + normalized status, provenance,
   `schedulable_inspection_types`.
4. Bind prompts to `KNOWN_RECORDS`, give prompts/criteria ids, and verify
   the flagship entry point (logged-in search) before building the planner.
5. Curate fixtures from the captured HTML; delete the import-time
   singletons so runs are injectable.
