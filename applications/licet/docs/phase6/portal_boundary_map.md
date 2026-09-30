# Accela portal mutation-boundary map — Phase 6 (portal integration)

*Portal safety specialist deliverable, 2026-09-23. Scope per the Phase 6 role
assignment: determine where Accela Citizen Access actually mutates state —
which buttons commit, which "Continue" is navigation, where payments,
attestations, cancellations and rescheduling live, and what the sandbox/live
distinction is — so the policy layer's boundaries rest on observed controls
rather than label guesses. This is the audit the adversarial review’s review named as missing
("the payment and attestation screens are unmapped (owner: portal integration)") and the
commit-control mapping architecture review asked for ("portal integration should supply the precise
portal-specific commit controls").*

**Method.** Everything below is read from the repository's live evidence —
`docs/accela_ui_map.md` (7/8 Solari verification, the apply wizard driven
end-to-end for all 7 target record types, the scheduling wizard walked to its
calendar), `licet/eval/records.py` (apply-flow map, ground truth), the captured
HTML in `licet/browser/accela.py`'s documented selectors, and the flow
definitions in `licet/browser/accela.py` / `licet/safety/risk_levels.py`. No
browser was driven for this audit; where a control has never rendered on the
Null Island sandbox it is recorded as **UNMAPPED**, not inferred. Nothing here
was verified by clicking it during this phase — it is the codification of
evidence Phase 0–4 already captured, plus the new machine-readable map in
`licet/browser/accela.py:MUTATION_BOUNDARIES`.

**One sentence for the policy layer:** on this portal the only wizard Continue
that commits scheduling is the popup's `ctl00_phPopup_lnkContinue` on the
confirm step (disabled until a date and time are chosen); every earlier
Continue is navigation; the apply wizard's `CapConfirm` Continue issues a
record with no payment gate; the apply disclaimer's agree checkbox is the one
attestation control and it is PROHIBITED; and no citizen-portal control for
cancel, reschedule, payment, upload, or renew has ever rendered on this
sandbox, so every one of those flows is fail-closed by construction.

## 1. The scheduling wizard, step by step (mapped live, 2026-09-20)

The wizard runs inside a `ctl00_phPopup_*` dialog; every step shares one URL,
so flow position comes from the popup's own wording (`accela.schedule_step`).
URL never distinguishes a commit from a read here.

| Step | Control | What it does | Mutates? | Classified as |
|---|---|---|---|---|
| select_record | row click | opens record in scheduling context | no | read (navigate) |
| select_type | `gvInspectionType` radios | chooses a type; nothing books | **no** | `select_inspection_type` (automatic) |
| select_type | `Continue` (early) | advances to calendar | **no** | navigation |
| select_date | calendar day cell | fills `lblAvaliableTimes`; nothing books | **no** | read |
| select_date | time-range link | selects a slot; nothing books | **no** | read |
| select_time | `Continue` (early) | advances to confirm | **no** | navigation |
| **confirm** | **`ctl00_phPopup_lnkContinue`** | **books the appointment** | **YES** | `schedule_inspection` commit |

The confirm Continue's failure mode is portal-enforced, not agent-enforced: it
renders `disabled="disabled"` with the real postback parked in `href_disabled`
until a date **and** time are chosen, and `SolariClient.click` refuses the
disabled variant outright (`accela.popup_continue_disabled` + the
`NOT_ACTIONABLE` error). A force-click here would fire a postback the portal
explicitly disabled; the client has never done it.

**A "Continue" means two different things on this portal** — exactly the case
the role assignment predicted. The scheduler's confirm Continue and the apply
wizard's review Continue both commit; the same label on every other wizard step
is pure navigation. That is why the dispatcher resolves the commit through the
flow's commit-*step* rule (`accela.FLOWS[...].commit_step`) and not through
button text: text says "Continue" everywhere, position says what it does.

## 2. Cancellation and rescheduling — UNMAPPED, fail-closed

No owned Null Island record has ever held a scheduled inspection (all 8 are
status *Submitted*; zero bookable calendar days — `SCHEDULING_GROUND_TRUTH`),
so the per-row **Cancel / Reschedule controls on the record's inspection list
have never rendered or been captured**. The citizen portal renders no
inspection-id column either: the row text is `Type | Status | Date`, and the
only appointment identity the page exposes is the per-row action control's
postback target (`__doPostBack('...lnkCancel...')`-style ids).

Consequences encoded in code, not in prose:

- `AccelaInspectionPortal._UNMAPPED_REASONS` refuses cancel/reschedule rather
  than improvising a flow through the new-request wizard (which would create a
  second appointment — a real wrong mutation).
- The policy engine refuses a targeted mutation that does not name its
  appointment (`TARGET_INSPECTION_UNIDENTIFIED`).
- **New this phase:** the client now parses per-row action controls from the
  page HTML (`accela.parse_inspection_row_controls`, shipped as
  `read_page` → `inspection_row_controls`), and the adapter binds an
  appointment id to a row when — and only when — the read is unambiguous
  (exactly one scheduled/requested row, exactly one cancel/reschedule control).
  The conservative binding avoids positional guessing; any ambiguity leaves the
  id unset and the mutation refused. The dispatcher's BENIGN_TARGETS list
  already maps a bare "Cancel" click against a *record-level* cancel flow to
  `cancel_inspection` and the guard holds it — a permission page cannot be
  driven around by clicking the row control either, because the same guard sees
  every click.

When the sandbox gains an issued record with bookable dates, the first live
walkthrough must capture: the row-control id shape, whether cancel renders a
separate confirm dialog (ACA convention: a JS confirm() or a dedicated
`lnkCancel` postback that re-renders a "Are you sure" panel), and whether the
reschedule flow reuses the scheduling wizard (ACA convention: it does, with the
existing appointment preselected). Until then the boundary map records the
shape as unknown and the code refuses.

## 3. Payments — UNMAPPED on NI, phrase-guarded at the primitive layer

- The record detail renders a **"Payments" section link — that link is a
  READ** (`BENIGN_TARGETS["payments"] → read_record`). Opening the section is
  lawful; nothing in it has been clicked by any Licet script.
- The apply wizard on NI has **no payment gate**: the `CapConfirm` Continue
  issues the record directly (verified live 2026-09-19, all 7 record types).
- No payment-screen control (Make a Payment / Pay Now / Continue to Payment /
  card-entry form) has ever rendered on an owned record, so there is no mapped
  control id. The dispatcher catches these labels by **phrase**
  (`DANGEROUS_PHRASES`) and resolves them to `enter_payment_details`, which the
  guard holds before the browser is touched.
- If a payment screen ever appears (agency-config-driven; another ACA
  deployment could render one), the correct behavior is already encoded:
  phrase-resolution holds the click, the semantic layer's `PAY_FEE` requires a
  scoped confirmation with an amount, and the constraint layer refuses a
  no-spend goal. The audit gap adversarial review named — nobody had verified what the
  section actually contains — remains open *by environment limit*, not by
  omission: the section on NI renders fee line items only (read path verified
  in Phase 3's fees observation), and no owned record shows a payable balance
  in any capture.

## 4. Legal attestation — mapped, PROHIBITED

- The apply wizard's **disclaimer step** (`CapApplyDisclaimer.aspx`) renders
  the agree checkbox + `btnNextStep`. Clicking through it is classified
  `accept_legal_attestation` → **PROHIBITED tier** (`risk_levels.py`): no user
  approval path, the guard blocks it outright, and the Phase 1 live suite
  proved the hold fires on the real page (P17 reached `CapApplyDisclaimer` and
  was held).
- The review step's Continue is separately resolved as `submit_application`
  (the flow's commit point) — the attestation cannot be smuggled in under a
  submission intent (`COMMIT_ACKNOWLEDGING_ACTIONS` requires the intent to
  acknowledge the commit).
- No other attestation surface exists on NI: no e-signature widget, no
  notary flow, no "I certify" text on the record pages in any capture.
- **Supported path when the attestation must actually be made:**
  `licet/safety/attestation_handoff.py` pauses the apply flow at the disclaimer
  and waits for the operator to tick the agree checkbox in their own browser,
  then resumes with the non-attestation steps. The module reads the checkbox's
  state and never operates it — `tests/test_attestation_handoff.py` asserts the
  agent registers zero interactions with that control across every branch
  (accepted, declined, timed out, page advanced, transient re-render). This
  keeps the PROHIBITED tier intact while giving the flow somewhere to go; the
  prohibition is a boundary on *who* attests, not a dead end for the wizard.
  `scripts/ni_apply_batch.py`, `scripts/ni_apply_submit.py` and
  `scripts/ni_apply_probe.py` now route their disclaimer step through it.
- **The handoff needs a browser the operator can reach.** A handoff into a
  browser nobody can see is not a handoff — it is a stall. Solari renders
  *remotely*, and its SDK exposes no live-view or takeover endpoint (only a
  replay URL, and only after the session is released), so a Solari session can
  never host this step. The three apply scripts therefore drive a **local
  headed Chrome** through `licet/browser/apply_browser.py`, whose
  `open_apply_browser()` is headed by default; read-only Accela work needs no
  human and stays on Solari. `LICET_APPLY_BROWSER=solari` remains available
  for non-interactive reproduction, and warns on startup that its disclaimer
  step will time out.
- **NI pre-ticks the attestation, so a satisfied control is not a human act.**
  Observed live 2026-09-30 (local Chrome, no clicks by anyone):
  `ctl00_PlaceHolderMain_termAccept` reads `checked=True` ~3 s after
  `CapApplyDisclaimer.aspx` loads, and the wizard then moves to `CapType.aspx`
  **on its own**. A handoff that treats "the box is checked" as "the human
  accepted" therefore reports an attestation nobody made — the first `res_alt`
  run did exactly that, printing `disclaimer accepted by the human` without
  ever asking anyone. The handoff now reports only what it observed, and names
  no outcome after the operator:
  `HandoffOutcome.SATISFIED_WHILE_WAITING` means the control was unsatisfied
  when the run first looked and satisfied by the time the wait ended — it does
  **not** claim the operator did it, because NI ticks its own box late as well
  as early (the second `res_alt` run observed exactly that flip in a browser
  nobody had touched), and `SATISFIED_WITHOUT_HUMAN_ACTION` covers a control
  already satisfied on first read. The report's only go/no-go is
  `permits_continuation(allow_portal_default=…)`: a timeout or a decline never
  proceeds, and a control the run never saw the operator asked about proceeds
  only under the explicit opt-in.
- **Operator decision, 2026-09-30.** Because NI's default is a pre-ticked
  attestation, this flow offers no genuine attestation act for the operator to
  perform — there is nothing for them to click. The operator authorised the
  apply scripts to continue over it (`DISCLAIMER_ALLOW_PORTAL_DEFAULT = True`
  in `ni_apply_batch.py`, `ni_apply_submit.py` and `ni_apply_probe.py`). That
  is a decision about a portal default, not a weakening of the PROHIBITED tier:
  the agent still never operates the control, and the report still says plainly
  that the run could not verify whose action satisfied it. The first authorised
  run issued `BLD26-00483` (Residential Alteration, 2026-09-30).
  The run also **does not block on a human**: `DISCLAIMER_SETTLE_SECONDS = 30.0`
  is a bounded settle window for the portal, not a wait for a click, because a
  gate on an act nobody has to perform only stalls the run. `surface_window()`
  raises the driven Chrome best-effort and posts a notification so whoever can
  see the screen can watch it; it is never a request to act, and a failure to
  raise is swallowed rather than failing the run. Note the driven browser is a
  *separate Chrome instance* from the operator's own (the driver gives it a
  throwaway profile), so its window opens underneath theirs — on 2026-09-30 the
  operator reported seeing nothing while macOS reported a healthy window at
  {left 22, top 55, 1282×800}. Do not treat "the operator watched it" as a
  property this flow can rely on.
  **Unmeasured:** the same page read *unchecked* through Solari on
  2026-09-29 (it waited the full 300 s), so the two drivers disagree about the
  initial state; whether that is render timing, session state, or an
  account-level flag is unknown.
- **Consequence for the apply flow:** whether a portal-default attestation may
  carry an application, which is the operator's call and not the agent's.

## 5. Submission / upload / applicant edits

| Flow | Mapped? | Boundary |
|---|---|---|
| Submit application (apply wizard review Continue) | **mapped, commit** | issues the record on NI — held as `submit_application`, confirmation required |
| Submit corrections / renewal | UNMAPPED | no citizen-portal control captured; unknown planner verbs deny as `UNKNOWN_ACTION_RISK`, dispatcher phrase "submit" catches relabels |
| Upload document | UNMAPPED | Attachments section renders; no upload control captured; phrase "upload" resolves `upload_document`, guard holds |
| Edit applicant/contact | UNMAPPED | contact edit happens in the apply wizard's contact step (popup `btnSave`); as a standalone record edit, no control captured |

The popup `btnSave` family (`ctl00_phPopup_btnSave` / `SaveAndClose`) is
recorded in `accela.py` as **mutation-capable controls** for every popup flow
that saves data (contact add/edit, parcel pick). They are used by the apply
batch script only; the runtime never clicks them outside an explicit apply
flow, and the dispatcher's commit-point rule governs the wizard while it runs.

## 6. Sandbox vs live: the URL distinction and its enforcement

- The sandbox host is `aca-test.accela.com` (`accela.SITE_ROOT`); live municipal
  portals run on `aca-prod.accela.com/<AGENCY>` or agency vanity domains.
- Classification is **host-derived only** (`policy.environment_from_url`):
  `aca-test.accela.com` → SANDBOX; other `accela` hosts → LIVE_READ_ONLY;
  anything else (vanity domains!) → UNKNOWN. Record contents never
  participate: test-looking data on a production host is a live portal, which
  is precisely the trap the checklist names ("never infer sandbox from test
  data").
- Enforcement is three independent layers, each refusing without asking
  another model: the policy engine (mutations need SANDBOX), the Accela
  adapter (re-derives the environment from the page being driven at submit
  time; a positively-live host refuses), and the primitive guard (every
  `changes_state` click needs a positively identified sandbox, approval
  included). the adversarial review’s replay locked all three (98 cases).

## 7. Wizard navigation and availability checks do not mutate (the architecture review’s ask)

The question was whether *reaching* the gate could itself mutate. Answer from
the mapped controls: **no.** Every pre-confirm step in the scheduling wizard
(select type radio, click a day cell, click a time link, early Continue) is
rendered or documented as non-committal — the portal keeps its own commit
disabled until the confirm step. The read-side equivalents
(`LiveInspectionPortal.offered_types` / `available_dates` in
`licet/eval/phase5_live.py`) drive the wizard up to the calendar **and stop
there**: they click radios and Continue on non-confirm steps only, and the
phase-4 acceptance runs confirmed 0 submissions on those paths. The one
exception to watch is in the UI map already: a dead-but-rendered control
(Meridian's `Active: False` schedule link) can look actionable; the client's
`NOT_ACTIONABLE` error surfaces it instead of retrying.

## 8. Machine-readable summary

`accela.MUTATION_BOUNDARIES` (new) is the map the audit doc describes, as data:
per flow/step, the control, the semantic action, the risk tier, whether it
mutates, whether it is the commit, and the evidence line. `tests/test_phase6_portal_boundary.py`
locks it to the policy engine's tiers and to the dispatcher's phrase
vocabulary, so the two cannot drift:

- every entry's action must classify to the same tier in `risk_levels` as the
  map's risk states (parity guard, same idea as the existing
  policy-vs-guard parity test),
- every mutation-capable entry must appear in `risk_levels.STATE_CHANGING_ACTIONS`
  (via the semantic-layer vocabulary) so the guard's environment rule applies,
- the scheduling confirm entry must be the only scheduling entry that mutates
  (the wizard-navigation-is-read-only invariant).

## 9. Residuals (updated owners)

- **Cancel/reschedule control shape (owner: portal integration, blocked on environment).**
  Unmapped because the sandbox offers no bookable appointment; the parser and
  the conservative binding are in place for the day it renders. Re-measure
  before the first live cancel eval.
- **Payment section controls (owner: portal integration, blocked on environment).** The
  section renders fee lines only on NI; no payable balance exists on any owned
  record. The phrase guard + constraint layer already cover the behavior; the
  control-id map stays empty until a payable record exists.
- **Cross-agency generalization (owner: portal integration, out of MVP scope).** All control
  ids here are Null Island's render; ACA is agency-configured, so the map is a
  per-deployment artifact. `MUTATION_BOUNDARIES` marks evidence provenance per
  entry so a new deployment re-audits rather than inherits.

*Everything in this map is enforceable without model judgment: the dispatcher
resolves, the guard and policy engine decide, the adapter refuses. The map's
job is to make those refusals rest on what the portal actually renders.*
