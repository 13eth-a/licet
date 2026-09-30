# Phase 7 portal-state review — portal integration

> Implementation follow-up: see [Phase 7 completion](completion.md) for the runtime fixes and current acceptance evidence. The findings below describe the earlier review snapshot.


Reviewed 2026-09-24 against the working tree after the implementation’s recovery controller
and the adversarial review’s adversarial review. Scope, per the Phase 7 assignment: make
recovery **portal-aware** — the "what the hell did Accela just do?" category.
Unexplained redirects, session expiry (redirect *and* modal), page-load timing,
partial AJAX rendering, stale result tables, modals, unexpected new tabs,
iframe state, alternate navigation paths, Accela error pages, recovery after
browser back. Plus the item the adversarial review’s review handed over explicitly:

> **portal integration**: supply a *settled* page identity for `loop_observed` — the Accela
> sections settle asynchronously (empty table, then rows), so the caller must
> pass the loaded state, not the in-flight one. Audit which Accela states
> produce a transient `active_section`/URL.

Method: new plain-data vocabulary over the real observation shape
(`licet/browser/accela.py`), a portal-state layer
(`licet/phase7/portal.py`), wiring into `read_page`, the Phase 3 retrieval
runner and the Phase 5 planner, then drive the **real** classifier, router,
retrieval runner and `GoalPlanner` with captured-portal-shaped inputs. Every
behaviour below is locked by `tests/test_phase7_portal.py` (29 cases) and
replayable with `scripts/phase7_portal_replay.py`
(`docs/phase7/portal_evidence.json`). No browser, model, credential or live
portal was contacted.

The Phase 7 principle this lane is measured against:

> **Recovery must never be more dangerous than the original failure.**

In portal terms: a half-rendered Inspections grid is annoying; a "recovery"
that reads it as *"no inspections on this record"* and reports the goal
complete is a fabricated answer. Both failure classes fixed below are of that
second kind.

## Headline

**The planner's loop key was built from a field nothing ever wrote.**
`planner.py` asked `browser_state.get("active_section") or browser_state.get("url")`,
but no code path ever set `active_section` — so every loop key collapsed to
the raw URL. On ACA that is close to the worst possible key: postback wizards
share one URL across every step (`CapDetail.aspx` for all five scheduling
steps), and record-section navigation never leaves the record detail at all.
The key could only distinguish records, never pages, which is exactly the
shape of the false-negative loop residual adversarial review recorded. And the fallback
contract — "pass the settled state, not a live render token" — was honoured by
nobody because there was no settled identity to pass.

The other half was structural: even a correct caller had no way to say "this
observation is not evidence yet". ACA's async sections render *"Loading..."*,
then an empty grid ("No data available in table"), then rows — and nothing in
the pipeline distinguished that sequence from a settled fact. A read that
failed while a session-expiry **modal** (not a redirect) sat on the page was
classified from the error text alone and retried through a dead session.

Both halves are fixed, and the fix is data, not new machinery: findings,
identity and routes are plain values the existing recovery controller keeps
bounding.

## What was built

| Piece | File | What it is |
|---|---|---|
| Portal-weirdness vocabulary | `licet/browser/accela.py` | `EMPTY_TABLE_MARKERS`, `MODAL_TEXT_MARKERS`, `CONSEQUENTIAL_MODAL_MARKERS`, `SESSION_MODAL_MARKERS`, `PORTAL_HOME_URL_MARKERS`, `NEW_TAB_URL_MARKERS` + `detect_empty_table`, `detect_modal`, `detect_session_modal`, `is_portal_home`, `looks_like_new_tab`, `detect_weirdness`. Same charter as the rest of the module: data + small parsers, offline-testable, provenance stated. |
| Portal state layer | `licet/phase7/portal.py` | `PortalFinding` (typed values mirroring `detect_weirdness`), `PageIdentity` (settled identity), `PortalState` (findings + identity + `unsettled`), `RecoveryRoute`, `route_recovery` (worst-first routing), `settled_browser_state` (the `browser_state` slice the planner persists), `identity_from_world` (the loop-key string), `audit_transient_identity_sources` (the audit adversarial review asked for). |
| `read_page` integration | `licet/browser/solari_client.py` | Every observation now carries `portal_findings` and `page_identity` (URL path + capID record identity). |
| Retrieval integration | `licet/phase3/runner.py` | After each settled read, `_record_browser_state` folds the settled identity into the caller's `browser_state` dict (opt-in via the new `browser_state=` constructor argument; default dict keeps script callers unchanged). |
| Planner integration | `licet/phase5/planner.py` | Loop key via `identity_from_world`; failed reads consult `route_recovery` — reads only, downgrade-only (see P3). |

## Findings

### P1 — the loop key was the URL, and ACA's URLs do not move

| | |
|---|---|
| Inherited behaviour (measured) | `run.world.browser_state.get("active_section") or run.world.browser_state.get("url") or "unknown"` — `active_section` had no producer anywhere in the repo (`code_search` confirms: the only writes to `browser_state` are `capabilities.py`'s `{"url": ...}` after FIND_PERMIT). |
| Why it harms recovery | Inside a scheduling wizard every step shares `CapDetail.aspx`, so `READ_INSPECTIONS` on step 1 and step 4 produce identical loop keys; conversely, section navigation (summary ↔ Inspections ↔ Fees) *also* produces identical keys. The `LoopDetector` was reduced to a per-record counter. Worse, a caller that did pass page text to be helpful would defeat detection entirely: a render timestamp or spinner label makes every occurrence unique. |
| Resolution | `PageIdentity.from_observation` derives the settled identity: URL *path* + record identity resolved to `capID1/2/3` from the URL (the identity that actually addresses the record — `000000014` and `BLD26-00472` are spellings, not identities) + flow/step from `accela.locate`, which the visible text refines because the URL does not move. The Phase 3 retrieval runner records it into `browser_state` after every settled read; the planner reads it through `identity_from_world`, whose fallback order is flow step → record identity → URL path → unknown. Locked by `test_loop_detection_survives_a_live_render_token`, `test_wizard_step_change_changes_the_key_even_though_the_url_does_not`, `test_display_label_churn_does_not_change_record_identity` and the replay's loop-key rows. |

The negative direction is pinned too: a *changed* wizard step is a different
key, so fixing the false negative does not manufacture false-positive loops
(`test_a_wizard_step_change_is_not_a_loop` in the replay;
`test_identity_from_world_prefers_the_settled_section`).

### P2 — a mid-render observation was indistinguishable from evidence

| | |
|---|---|
| Inherited behaviour (measured) | `read_page` flagged `loading` markers (Phase 3 correctly re-settles on them), but nothing downstream treated *"Loading..."* or an in-flight grid ("No data available in table") as **not evidence**. An empty grid wording was also absent from the vocabulary entirely — only the declared-empty markers existed. |
| Why it harms recovery | The checklist's exact scenario: *rows are empty for 2 seconds, then populate asynchronously*. A read landing in the window between "grid rendered" and "rows arrived" produced a payload that every downstream consumer was entitled to read as *"no inspections on this record"*. That is the fabricated-answer shape Phase 5's eval already fails runs for — arriving through a timing window instead of a parser bug. |
| Resolution | `EMPTY_TABLE_MARKERS` (grid idioms incl. the DataTables wording ACA ships) join the vocabulary; `detect_weirdness` returns the finding list for one observation; `PortalState.unsettled` marks observations carrying in-flight findings; `read_page` reports `portal_findings` on every payload. The declared-empty vocabulary (`declares_no_inspections`) is deliberately **not** extended — "You have not added any inspections" and "No data available in table" are different claims, and both `test_stale_result_table_is_never_a_fact` and the Phase 3 loading contract keep them distinct. |

### P3 — failed reads now route through what the portal actually did

| | |
|---|---|
| Inherited behaviour (measured) | The planner's failure branch classified the *error text* (`classify_failure`) and set `retryable` from the classification. The state of the *page* — session modal on top, still rendering, redirected home — played no role, because nothing could express it. |
| Why it harms recovery | A read that throws `TimeoutError` while a session-expiry modal sits on the page classifies as a browser timeout: recoverable, retry — into a dead session, repeatedly clicking through, precisely what the checklist forbids. A redirect home mid-workflow looked like any other navigation failure. A consequential modal ("are you sure…?") was invisible to the failure path entirely. |
| Resolution | After any failed **read** (raised or returned-failed; mutations are never routed — they reconcile upstream), `route_recovery` maps the worst portal finding to a route, and the planner acts only by *downgrading*: `terminal` routes (session expired, session modal, consequential modal) clear `retryable` and surface the portal's reason as the observation message, so the run stops inside its existing bounded loop; `unsettled` observations additionally get the `WAIT_FOR_SETTLE` reason appended, marking the attempt evidence-free. Every route event is traced (`PORTAL_RECOVERY_ROUTE`) with findings + page identity for the recovery traces the checklist requires. Locked by the three planner-integration tests and the replay's planner rows. |

Routing is deliberately conservative, in the fail-safe direction: `route_recovery`
can only downgrade an observation the failure branch already produced, never
upgrade a failure into a success, and never touches a mutation. The controller's
budgets remain the only thing that can spend retries.

### P4 — the session modal is a first-class finding

| | |
|---|---|
| Inherited behaviour (measured) | Session handling keyed on notices and URL (`detect_notices`, `NOTICE_PATTERNS`); ACA deployments that render expiry as a **modal over the current page** produced no URL change and no notice hit. |
| Why it harms recovery | The page underneath is unchanged, so every position signal said "nothing happened" while the session was gone. A planner could keep reading a dead session indefinitely. |
| Resolution | `SESSION_MODAL_MARKERS` + `detect_session_modal`; `session_expired_modal` is terminal in `route_recovery` (STOP, AUTH_REQUIRED shape), and `detect_modal` excludes the session wordings so the same page does not double-report as a generic consequential modal. `test_session_expiry_as_modal_is_terminal_without_redirect` and `test_finding_worst_first_ordering_session_beats_modal` pin it, including the ordering: a dead session with a dialog on top stops, it does not close the dialog. |

## End-to-end rows (replay, `--json docs/phase7/portal_evidence.json`)

```text
scenarios
  async grid: empty then rows          pending=empty_table_pending_rows unsettled=True
                                       -> settled findings=0              unsafe=False
  session expiry rendered as a modal   STOP terminal=True                 unsafe=False
  redirected to portal home            RECOVER_FROM_HOME (navigation)     unsafe=False
  popup / new tab opened               DISMISS_OR_RETURN / RETURN_TO_ORIGIN_TAB  unsafe=False
  postback wizard shares one URL       keys differ=True (same path=True)  unsafe=False

settled loop key (legacy = raw page text as the page-state string)
  render timestamp in the key          legacy: loop detected=False (each occurrence unique)
                                       current: detected on the third identical key
  wizard step change                   no false loop across steps         unsafe=False

planner integration
  read fails + session modal           status=BLOCKED strategy=STOP       unsafe=False
  read fails on a mid-render page      status=BLOCKED strategy=WAIT_FOR_SETTLE  unsafe=False
  mutation path with findings present  status=SUCCESS routes=0            unsafe=False

portal metrics (unsafe targets zero)
  unsafe_recoveries                    0
  session_click_throughs               0
  consequential_modals_dismissed       0
  facts_recorded_from_inflight_grids   0
  false_loops_from_step_changes        0
```

## Transient identity audit (the adversarial review handoff item)

`audit_transient_identity_sources()` returns this as data; prose here for the
review trail.

1. **AJAX sections still rendering** (`Loading...`, in-flight grid text) —
   transient signal is the in-flight wording itself. Mitigation:
   `UNSETTLED_FINDINGS` marks the observation not-evidence and
   `settled_browser_state` excludes text from identity; the Phase 3 runner
   already re-settles once before accepting.
2. **Postback wizards** — one URL across every step; no transient URL signal,
   which is the trap. Mitigation: identity carries flow+step (from
   `accela.locate`, text-refined), so a step change moves the loop key.
3. **Record-section navigation** — URL never changes, section list churns in
   the text. Mitigation: `active_section` is the settled flow step; text is
   excluded from identity, so churn cannot read as progress either.
4. **Display-label reformatting** (`000000014` vs `BLD26-00472`) — the
   *displayed* record number changes spelling across pages. Mitigation:
   identity resolves `capID1/2/3` from the URL, which addresses the record.
5. **Session expiry as modal** — page underneath unchanged, so URL/step keys
   see nothing. Mitigation: terminal finding; no loop key is consulted
   against a dead session at all.

## Verified-safe paths (attacked, no change needed)

- **Mutations are untouchable by the router.** The routing guard is
  `action not in MUTATIONS and not observation.success`; the replay's
  mutation row asserts `routes=0` with findings present, and the existing
  Phase 5/6 no-replay tests still pass untouched.
- **`World.fingerprint()` already excluded `browser_state`**, so writing the
  settled identity into it cannot fake progress: the "label churn is not
  progress" property survives unchanged (LE04 still passes).
- **The declared-empty vs in-flight distinction**: `declares_no_inspections`
  is untouched, so Phase 3's "a still-loading section is not 'no inspections'"
  contract holds (`test_stale_result_table_is_never_a_fact` pins both sides).
- **The legacy `browser_state` shape still works**: `identity_from_world`
  falls back through section → record → path → url → unknown, so callers that
  recorded nothing (script fixtures) behave exactly as before.

## Deliberately not changed (residual risk, named)

- **No recovery action is executed by the router (owner: implementation).** The planner
  consumes routes only as downgrades; strategies like `RECOVER_FROM_HOME`
  ("re-search the known permit, verify, resume") name the recovery shape but
  the planner's existing replanning semantics still drive it. Wiring the
  named strategies to bounded `recover()` calls is the natural implementation
  follow-up and needs no new portal data — the routes are already in the
  trace.
- **Modals/popups are text-detected, not DOM-confirmed.** ACA renders real
  dialog chrome (`[role="dialog"]`, the popup iframe), and `read_page` already
  reports `popup_open` from the frame inventory; the *wording* classifiers are
  the conservative fallback for payloads that lack frame data. When in doubt
  the router treats a modal as consequential (route to policy), never
  dismisses — the fail-safe direction.
- **`NEW_TAB_URL_MARKERS` is partly other-agency provenance** (stated per the
  module's convention): NI renders printable views/attachments, but the
  marker list is phrase-level, not a control map like Phase 6's mutation
  boundaries.
- **The empty-table vocabulary is wording-based.** A grid that renders zero
  rows *and* zero markers looks settled; the declared-empty distinction then
  rests on the section's own wording, as before. A DOM-level row count from
  `read_page` would close this and belongs with the implementation’s browser-lane work.

## Files touched

| File | Change |
|---|---|
| `licet/browser/accela.py` | Portal-weirdness vocabulary + `detect_empty_table` / `detect_modal` / `detect_session_modal` / `is_portal_home` / `looks_like_new_tab` / `detect_weirdness`. |
| `licet/phase7/portal.py` | New: `PortalFinding`, `PageIdentity`, `PortalState`, `RecoveryRoute`, `route_recovery`, `route_from_result`, `settled_browser_state`, `identity_from_world`, `audit_transient_identity_sources`. |
| `licet/phase7/__init__.py` | Re-export the portal layer. |
| `licet/browser/solari_client.py` | `read_page` reports `portal_findings` + `page_identity`. |
| `licet/phase3/runner.py` | Settled-identity recording (`browser_state=` arg, `_record_browser_state`). |
| `licet/phase5/planner.py` | Settled loop key (`identity_from_world`) + downgrade-only portal routing on failed reads, traced. |
| `licet/phase5/capabilities.py` | Pass `world.browser_state` into the retrieval runner for READS. |
| `tests/test_phase7_portal.py` | 29 regressions. |
| `scripts/phase7_portal_replay.py` | Scenario + loop-key + planner replay, optional JSON evidence. |
| `docs/phase7/portal_evidence.json` | Replay output. |

## Handoff

- **implementation**: bind the named strategies (`RECOVER_FROM_HOME`, `RETURN_TO_RECORD`,
  `RETURN_TO_ORIGIN_TAB`, `CLOSE_INFORMATIONAL_MODAL`, `WAIT_FOR_SETTLE`) to
  bounded `RecoveryController.recover()` calls if/when executor-level recovery
  lands — the routes and evidence are already on every trace event. A DOM row
  count in `read_page` would close the wording-only empty-grid residual.
- **adversarial review**: attack the router the way the controller was attacked. The
  interesting surfaces: `detect_modal`'s wording lists (an unforeseen
  consequential wording currently classifies informational), the worst-first
  ordering when several findings co-occur, and whether the downgrade-only
  contract can be violated by a caller that mutates `browser_state` between
  classification and routing.
- **architecture review**: whether `WAIT_FOR_SETTLE` downgrades should feed the replanner
  (currently they surface in the message only) and whether a portal-home
  redirect during a *mutation* (routed nowhere, by design) needs a semantic
  decision rather than reconciliation.
