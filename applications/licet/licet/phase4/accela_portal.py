"""The Accela adapter: `InspectionPortal` implemented against the real UI.

Phase 4's executor (`licet.phase4.workflow.InspectionActionExecutor`) owns
*what* a mutation means and *whether* it is allowed and verified; this module
owns only *how the portal is driven* — the Accela-specific half of the
specialist split, matching `docs/phase4.md`'s adapter boundary:

    Planner -> Action Policy -> Executor -> InspectionPortal (this file) -> Accela

Everything below rides the same ToolDispatcher the planner uses, so the guard,
flow-position tracking and JSONL run log apply to every call made here — the
adapter adds no new route to the browser. Control-level safety (e.g. refusing
to click the popup's disabled Continue, whose real postback is parked in
`href_disabled`) lives in `SolariClient`; flow-level safety (record identity,
authorization) lives above this module.

Wizard facts encoded below come from the live captures
(`logs/ni_backoffice/schedule/*_types_grid_f10.html`, `*_calendar_f10.html`,
2026-09-20) and `docs/accela_ui_map.md`.
"""  # noqa: E501

from __future__ import annotations

import asyncio
import datetime as _dt
import re
from dataclasses import dataclass
from typing import Any, Callable, Iterable

from licet.agent.state import AgentState
from licet.browser import accela
from licet.browser.dispatcher import ToolCall, ToolDispatcher
from licet.phase3 import accela_extract
from licet.phase4.actions import InspectionAction, InspectionSnapshot
from licet.safety.policy import Environment, detect_environment, environment_from_url

# A citizen-portal inspection row shape ACA renders on detail pages:
# "Electrical Final | Scheduled | 09/24/2026". The Phase 3 line parser only
# keeps rows that validate against the lifecycle AND result vocabularies, so a
# scheduled row whose third cell is a date (not a result word) is dropped there.
# Retry policy. Only read/settle steps are ever retried, and only a bounded
# number of times; a commit or wizard-advancing click is never replayed here.
# The executor reconciles a commit by re-reading state, never by re-submitting
# (see `licet/phase4/workflow.py`), so the adapter must not introduce a retry
# that could double-advance the wizard or double-submit.
_SETTLE_ATTEMPTS = 3
_READ_ATTEMPTS = 2
_TYPE_SELECT_ATTEMPTS = 2

# Action kinds whose live UI is not mapped: driving them through the
# scheduling wizard would perform a different mutation than requested, so the
# adapter fails closed rather than improvising a flow.
_UNMAPPED_REASONS: dict[str, str] = {
    "cancel": "cancellation flow is not mapped on this portal; refusing to improvise",
    "cancel_inspection": "cancellation flow is not mapped on this portal; refusing to improvise",
    "reschedule": "reschedule flow is not mapped on this portal; refusing to improvise",
    "reschedule_inspection": "reschedule flow is not mapped on this portal; refusing to improvise",
}

# This literal shape is the adapter's fallback — same row, date preserved.
_SCHEDULED_ROW_RE = re.compile(
    r"^(?P<type>[^|]+?)\s*\|\s*(?P<status>Scheduled|Requested|Cancelled|Canceled|Completed|Passed|Failed|Pending)"
    r"(?:\s*\|\s*(?P<date>\d{1,2}/\d{1,2}/\d{4}))?\s*$",
    re.I,
)


def _adapter_rows(text: str) -> list[dict[str, str]]:
    """Type/Status/Date rows from the citizen detail's inspections text.

    Used only when the Phase 3 validated parser found nothing or dropped the
    date column; the vocabulary-validated rows stay authoritative.
    """
    rows: list[dict[str, str]] = []
    for line in str(text or "").splitlines():
        match = _SCHEDULED_ROW_RE.match(line.strip())
        if not match:
            continue
        data = match.groupdict()
        row = {"type": data["type"].strip(), "status": data["status"].capitalize()}
        if data.get("date"):
            row["scheduled_date"] = data["date"]
        rows.append(row)
    return rows


def _ref_key(ref: dict[str, str] | None) -> str | None:
    """Stable record key (``RecordRef.as_key`` form) from a parsed page ref.

    None when the page does not address a record; the adapter never invents a
    key from the displayed permit number.
    """
    ref = ref or {}
    if not all(ref.get(name) for name in ("capID1", "capID2", "capID3")):
        return None
    return (
        f"{ref.get('agency_code') or accela.AGENCY_CODE}/"
        f"{ref.get('module') or accela.DEFAULT_MODULE}/"
        f"{ref['capID1']}/{ref['capID2']}/{ref['capID3']}"
    )


def _normalize_date_token(value: str) -> str | None:
    """MM/DD/YYYY (ACA's rendering) or ISO input as the snapshot's ISO date."""
    text = (value or "").strip()
    for pattern, fmt in ((r"^\d{1,2}/\d{1,2}/\d{4}$", "%m/%d/%Y"), (r"^\d{4}-\d{2}-\d{2}$", "%Y-%m-%d")):
        if re.fullmatch(pattern, text):
            try:
                return _dt.datetime.strptime(text, fmt).date().isoformat()
            except ValueError:
                return None
    return None


@dataclass(frozen=True)
class PortalObservation:
    """Everything one read_page pass yielded, in portal terms.

    `loading` flags the AJAX hazard: the Inspections section renders
    "Loading..." first, and a mid-load read must never become a snapshot.
    `appointment_controls` are the per-row action controls parsed from the page
    HTML (cancel/reschedule/edit) — the only appointment identity the citizen
    portal exposes. Empty when the read carried no HTML: targeted mutations
    then fail closed rather than acting on an unidentified row.
    """

    url: str | None
    text: str
    record_header: dict[str, str]
    record_ref: dict[str, str]
    record_key: str | None
    fields: list[dict[str, Any]]
    inspection_rows: list[dict[str, str]]
    offered_types: tuple[str, ...]
    inspection_types: tuple[dict[str, Any], ...]
    inspection_type_total: int | None
    calendar: list[dict[str, Any]]
    selectable_times: str
    loading: list[str]
    validation_errors: list[dict[str, Any]]
    appointment_controls: tuple[dict[str, str], ...] = ()

    @property
    def is_loading(self) -> bool:
        return bool(self.loading)

    @staticmethod
    def from_payload(data: dict[str, Any]) -> "PortalObservation":
        text = str(data.get("text") or "")
        record_ref = accela.parse_ref_from_url(str(data.get("url") or ""))
        rows = accela_extract.parse_inspection_rows(text)
        if not any(row.get("scheduled_date") for row in rows):
            # vocabulary rows win; the literal shape only fills their gaps
            literal = {row["type"].casefold(): row for row in _adapter_rows(text)}
            rows = [
                literal.get(str(row.get("type") or "").casefold(), row) for row in rows
            ] if rows else _adapter_rows(text)
        # Appointment identity: the client already parsed the per-row controls
        # from the full page HTML (`inspection_row_controls`); fall back to
        # parsing an inline `html` payload for scripted doubles that supply one.
        html = str(data.get("html") or "")
        controls = list(data.get("inspection_row_controls") or []) or (
            accela.parse_inspection_row_controls(html) if html else []
        )
        appointment_controls = tuple(controls)
        return PortalObservation(
            url=data.get("url"),
            text=text,
            record_header=accela.parse_record_header(text),
            record_ref=record_ref,
            record_key=_ref_key(record_ref),
            fields=list(data.get("fields") or []),
            inspection_rows=rows,
            offered_types=tuple(str(option.get("name") or "") for option in data.get("inspection_types") or []),
            inspection_types=tuple(dict(option) for option in data.get("inspection_types") or [] if isinstance(option, dict)),
            inspection_type_total=data.get("inspection_type_total"),
            calendar=list(data.get("calendar") or []),
            selectable_times=str(data.get("selectable_times") or ""),
            loading=[str(item) for item in data.get("loading") or []],
            validation_errors=list(data.get("validation_errors") or []),
            appointment_controls=appointment_controls,
        )


class AccelaInspectionPortal:
    """Drives the real inspection UI through the ToolDispatcher.

    `record_ref` is the verified record identity (capID1/2/3 + module + agency)
    Phase 2/3 established; the adapter refuses to act when neither the current
    page nor that ref addresses the record, rather than searching for one.
    """

    def __init__(
        self,
        dispatcher: ToolDispatcher,
        *,
        record_ref: dict[str, str] | None = None,
        today: Callable[[], _dt.date] | None = None,
    ) -> None:
        self.dispatcher = dispatcher
        self.record_ref = dict(record_ref) if record_ref else {}
        # Expose the portal's current host to the Phase 6 executor. This is
        # host-derived only; record contents never classify the environment.
        self.environment = detect_environment(dispatcher.client)
        self._today = today or _dt.date.today
        self.steps: list[dict[str, Any]] = []

    # --- dispatcher plumbing ------------------------------------------------

    def _state(self) -> AgentState:
        """A private scratch state; the adapter runs outside planner loops.

        The dispatcher records outcomes (failures, flow position) onto it so
        stop-condition bookkeeping stays real without sharing a planner run.
        """
        if not hasattr(self, "_scratch"):
            self._scratch = AgentState(goal="phase4 inspection action")
        return self._scratch

    async def _do(self, call: ToolCall) -> dict[str, Any]:
        outcome = await self.dispatcher.execute(call, self._state())
        self.steps.append(
            {
                "call": {"name": call.name, "args": call.args},
                "success": bool(outcome.get("success")),
                "blocked": bool(outcome.get("blocked")),
                "url": outcome.get("url"),
                "error": outcome.get("error"),
            }
        )
        return outcome

    async def _read(self) -> PortalObservation:
        """Read the page, retrying a failed read before trusting an empty result.

        A dropped read is a page-load/wait hazard, not a mutation, so re-reading
        is safe; the loop is bounded and returns the last (possibly empty)
        observation, which the callers already treat as explicitly unknown.
        """
        observation = PortalObservation.from_payload({})
        for _ in range(_READ_ATTEMPTS):
            # Include rendered HTML so the ACA inspection pager state is
            # captured alongside text/fields. All navigation remains through
            # the dispatcher; pagination itself is an explicit benign UI read.
            outcome = await self._do(ToolCall("read_page", {"include": ["text", "form", "errors", "frames", "html"]}))
            data = outcome.get("data") or {}
            observation = PortalObservation.from_payload(data)
            if outcome.get("success") and data:
                return observation
        return observation

    async def _wait(self, *, until_present: str | None = None, until_absent: str | None = None, seconds: int = 6) -> None:
        """Settle the page, re-asking a bounded number of times on a timeout.

        A timed-out wait means the condition was not observed; re-asking is
        strictly safer than reading a half-rendered page. Safe to retry because
        it changes no state.
        """
        args: dict[str, Any] = {"seconds": seconds}
        if until_present:
            args["until_present"] = until_present
        if until_absent:
            args["until_absent"] = until_absent
        for _ in range(_SETTLE_ATTEMPTS):
            outcome = await self._do(ToolCall("wait", dict(args)))
            if outcome.get("success"):
                return
        return

    # --- record addressing ---------------------------------------------------

    def _detail_url(self, obs: PortalObservation) -> str | None:
        """The record deep link from the page's own ref, else the verified one."""
        ref = obs.record_ref or self.record_ref
        if not ref or not ref.get("capID1"):
            return None
        return accela.detail_url(
            ref["capID1"],
            ref["capID2"],
            ref["capID3"],
            module=ref.get("module", accela.DEFAULT_MODULE),
            agency_code=ref.get("agency_code", accela.AGENCY_CODE),
        )

    @staticmethod
    def _shown_permit(obs: PortalObservation) -> str:
        return str(obs.record_header.get("permit_id") or "")

    async def _open_schedule_wizard(self, obs: PortalObservation) -> bool:
        """Open ACA's schedule wizard through its handler-bearing control.

        On Null Island the user-facing wording is a nested span, while the
        clickable target is a div with id ``lnkInspectionSchedule`` and an
        inline ``showInspectionPopupDialog`` handler. The adjacent anchor is a
        dead placeholder. Never click either text node as if it were a link.
        """
        # The opener lives inside the record-tabs menu, which ACA renders as a
        # collapsed dropdown, so on the plain detail URL it lays out at 0x0 and
        # the click is refused as `not_actionable`. Land on the record's
        # inspection view first — ACA's own `IsToShowInspection=yes` flag — so
        # the panel is actually rendered and the opener is clickable.
        ref = self.record_ref if isinstance(self.record_ref, dict) else None
        if ref and all(ref.get(key) for key in ("capID1", "capID2", "capID3")):
            opened = await self._do(ToolCall(
                "navigate", {"url": accela.inspection_detail_url(ref)}))
            if opened.get("success"):
                await self._wait(until_absent="Loading...")
                obs = await self._read()
        if not any(label.casefold() in obs.text.casefold()
                   for label in accela.SCHEDULE_LINK_LABELS):
            return False
        outcome = await self._do(ToolCall("click", {
            "target": accela.SCHEDULE_LINK_CONTROL_ID,
            "by": "selector", "intent": "navigate",
        }))
        return bool(outcome.get("success"))

    # --- InspectionPortal: read ----------------------------------------------

    async def read_inspection_state_async(
        self,
        permit_id: str,
        inspection_type: str | None = None,
        inspection_id: str | None = None,
    ) -> InspectionSnapshot:
        """Read one inspection's state off the record detail page.

        Land on the record's deep link when needed, open the Inspections
        section by its benign label, settle the AJAX section, and parse rows.
        Unknown states are returned explicitly (status "Unknown (...)") —
        never guessed into "not scheduled".
        """
        obs = await self._read()
        if "capdetail.aspx" not in (obs.url or "").lower():
            url = self._detail_url(obs)
            if url is None:
                return self._unknown_snapshot(permit_id, inspection_type, "record deep link unavailable")
            outcome = await self._do(ToolCall("navigate", {"url": url}))
            if not outcome.get("success"):
                return self._unknown_snapshot(permit_id, inspection_type, "record detail did not open")
            await self._wait(until_absent="Loading...")
            obs = await self._read()
            if obs.is_loading:
                await self._wait(until_absent="Loading...", seconds=8)
                obs = await self._read()
        if self._shown_permit(obs).casefold() != permit_id.casefold():
            # Carry the OBSERVED id so the executor reads this as a mismatch.
            return self._unknown_snapshot(
                self._shown_permit(obs) or permit_id, inspection_type, "portal is showing a different record"
            )
        if obs.is_loading:
            return self._unknown_snapshot(permit_id, inspection_type, "inspections section still loading")
        if not obs.inspection_rows and not accela.declares_no_inspections(obs.text) and not obs.offered_types:
            # The summary page says nothing about inspections: the section is a
            # postback away. Open it by its benign label (dispatcher resolves
            # to read_record) before answering, then settle the AJAX render.
            # Null Island's detail page renders the "Inspections" anchor only
            # as a dead wrapper (present but never visible — the 2026-09-25
            # live run's repeated `not_actionable`), so a failed exact-label
            # click falls back to the label the portal actually renders. The
            # fallback runs only when the click was *reported* not actionable;
            # a blocked or guard-held click never triggers it.
            outcome = await self._do(ToolCall("click", {"target": "Inspections", "by": "text"}))
            error = outcome.get("error") or {}
            error_text = str(error.get("message") or "")
            not_actionable = not outcome.get("success") and not outcome.get("blocked") and (str(error.get("kind") or "") == "not_actionable" or (
                "present but not visible" in error_text
            ))
            if not_actionable:
                outcome = await self._do(ToolCall("click", {"target": "Inspection History", "by": "text"}))
            if outcome.get("success"):
                resolution = outcome.get("resolution") or {}
                if str(resolution.get("provenance") or "") == "benign_target":
                    await self._wait(until_absent="Loading...", seconds=6)
                    obs = await self._read()
            elif not outcome.get("blocked") and (outcome.get("error") or {}).get("kind") in {"not_found", "not_actionable"}:
                ref = self.record_ref or obs.record_ref
                if ref and all(ref.get(k) for k in ("capID1", "capID2", "capID3")):
                    opened = await self._do(ToolCall("navigate", {"url": accela.inspection_detail_url(ref)}))
                    if opened.get("success"):
                        await self._wait(until_absent="Loading...")
                        obs = await self._read()
        # Recheck after EVERY navigation/postback, not just the initial read.
        if self._shown_permit(obs).casefold() != permit_id.casefold() or obs.is_loading:
            return self._unknown_snapshot(self._shown_permit(obs) or permit_id, inspection_type,
                                          "inspection view identity unverified or still loading")
        if self.record_ref and obs.record_key != _ref_key(self.record_ref):
            return self._unknown_snapshot(permit_id, inspection_type, "inspection view record key mismatch")
        if accela.declares_no_inspections(obs.text) and not obs.inspection_rows:
            return InspectionSnapshot(
                permit_id=permit_id,
                inspection_id=inspection_id,
                inspection_type=inspection_type or "",
                status="Not Scheduled",
                eligible=self._eligibility(inspection_type, obs) is not False,
                record_key=obs.record_key,
            )
        row = self._find_row(obs.inspection_rows, inspection_type, inspection_id)
        if row is None:
            return self._unknown_snapshot(permit_id, inspection_type, "inspection row not found on the record")
        return self._snapshot_from_row(permit_id, row, obs, appointment_id=self._appointment_id(obs, row))

    def read_inspection_state(
        self, permit_id: str, inspection_type: str | None = None, inspection_id: str | None = None
    ) -> InspectionSnapshot:
        _require_no_running_loop()
        return asyncio.run(self.read_inspection_state_async(permit_id, inspection_type, inspection_id))

    # --- InspectionPortal: submit ----------------------------------------------

    async def submit_inspection_action_async(
        self, action: InspectionAction, *, portal_type: str, selected_date: str | None = None
    ) -> str | None:
        """Drive the scheduling wizard for one already-authorized action.

        The executor has already decided permission, eligibility, idempotency
        and the date; this method performs the UI steps and returns the portal's
        confirmation number when one is printed. Any failure raises, which the
        executor treats as an uncertain submission and reconciles by re-reading
        state — never by replaying.
        """
        kind = action.action_type.casefold().strip()

        # The deepest line of defence, and the only line that sees the real
        # browser. Whatever a caller did upstream, this adapter commits only
        # against a positively identified sandbox:
        #
        # - the environment is re-derived from the page being driven rather than
        #   trusted from construction time, because navigation or a redirect can
        #   move the session (an observed live origin outranks configuration);
        # - a live municipal record is never mutated through this class;
        # - an origin that cannot be established fails closed here as well —
        #   unknown is not a synonym for sandbox on the direct route either.
        observed = detect_environment(self.dispatcher.client)
        if observed is Environment.UNKNOWN:
            # Second, independent source: the URL the dispatcher recorded for the
            # page it last read on this portal's own state. The executor always
            # reads the target before submitting, so a real sandbox session is
            # identifiable even if the client's live URL is not exposable.
            observed = environment_from_url(self._state().current_url)
        if observed is not Environment.SANDBOX:
            detail = "live municipal record" if observed is Environment.LIVE_READ_ONLY else \
                f"unclassified portal ({observed.value})"
            raise RuntimeError(f"{detail}: refusing to submit an inspection action")

        # Neither the cancel nor the reschedule control has a citizen-portal
        # mapping on this sandbox: no owned record ever held a scheduled
        # inspection, so the per-row controls (`docs/accela_ui_map.md` §6) were
        # never rendered or captured. Driving a reschedule through the *new
        # request* wizard instead would create a second appointment for the same
        # type and then fail verification — a real wrong mutation. Fail closed
        # on both rather than improvise a flow; cancellation is also held by the
        # guard at the dispatcher.
        if kind in _UNMAPPED_REASONS:
            raise RuntimeError(_UNMAPPED_REASONS[kind])

        obs = await self._read()
        if "capdetail.aspx" not in (obs.url or "").lower():
            url = self._detail_url(obs)
            if url is None:
                raise RuntimeError("record deep link unavailable; cannot open the scheduling wizard")
            outcome = await self._do(ToolCall("navigate", {"url": url}))
            if not outcome.get("success"):
                raise RuntimeError("record detail did not open")
            await self._wait(until_absent="Loading...")
            obs = await self._read()
        if self._shown_permit(obs).casefold() != action.permit_id.casefold():
            raise RuntimeError("portal is showing a different record; refusing to act")
        if action.record_key and obs.record_key and obs.record_key != action.record_key:
            # Second identity gate, at the real UI boundary: the page we would
            # act on must be the record the action was authorized for.
            raise RuntimeError("page record identity does not match the authorized record key; refusing to act")

        # Step 0: open the wizard from the actual handler-bearing control.
        if not await self._open_schedule_wizard(obs):
            raise RuntimeError("no scheduling link actionable on the record detail")

        # Step 1: choose the requested type from the wizard's grid.
        await self._wait(until_present="Inspection Type", until_absent="Loading...", seconds=10)
        wizard = await self._read()
        await self._select_type(portal_type, wizard)
        await self._continue()

        # Step 2: pick the chosen date on the calendar, then a time range.
        date_page = await self._read()
        await self._select_date(selected_date, date_page)
        await self._continue()

        # Step 3: the confirm step. Its generic Continue IS the commit; the
        # dispatcher resolves it through the flow's commit-point rule, so the
        # intent must acknowledge scheduling.
        outcome = await self._do(ToolCall("click", {"target": "Continue", "by": "text", "intent": "schedule_inspection"}))
        if not outcome.get("success"):
            raise RuntimeError("confirm step did not complete")
        await self._wait(until_absent="Please wait...", seconds=8)
        result = await self._read()
        if result.is_loading:
            await self._wait(until_absent="Loading...", seconds=8)
            result = await self._read()
        confirmation = accela.parse_confirmation_number(result.text)
        if confirmation:
            return confirmation
        # No confirmation number printed: a scheduled row for the type is the
        # portal's own acknowledgement (verification is the executor's
        # independent re-read anyway).
        for row in result.inspection_rows:
            if portal_type.casefold() in str(row.get("type") or "").casefold() and "schedul" in str(row.get("status") or "").lower():
                return None
        raise RuntimeError("no confirmation number or scheduled row after submission")

    def submit_inspection_action(
        self, action: InspectionAction, *, portal_type: str, selected_date: str | None = None
    ) -> str | None:
        _require_no_running_loop()
        return asyncio.run(
            self.submit_inspection_action_async(action, portal_type=portal_type, selected_date=selected_date)
        )

    # --- wizard internals -------------------------------------------------------

    async def _select_type(self, portal_type: str, wizard: PortalObservation) -> None:
        """Select the inspection type from the wizard's radio grid.

        Grid evidence (verified capture): the radios render as
        `ctl00_phPopup_gvInspectionType_ctlNN_rdInspectionType` with the type
        name as the label. ACA renders the input hidden and the label
        clickable, so a failed id click retries by label. A type absent from
        the grid is refused — not clicked hopefully.
        """
        def control_for(name: str, fields: list[dict[str, Any]]) -> str | None:
            parsed = {option.name.casefold(): option.control_id
                      for option in accela.parse_inspection_types(fields)}
            control_id = parsed.get(name.casefold())
            return control_id if control_id and accela.INSPECTION_TYPE_ID_MARKER in control_id else None

        control_id = control_for(portal_type, wizard.fields)
        if control_id is None:
            raise RuntimeError(f"inspection type '{portal_type}' is not offered by the wizard's grid")
        outcome = await self._do(
            ToolCall("click", {"target": control_id, "by": "selector", "intent": "select_inspection_type"})
        )
        if outcome.get("success"):
            return
        outcome = await self._do(
            ToolCall("click", {"target": portal_type, "by": "label", "intent": "select_inspection_type"})
        )
        if outcome.get("success"):
            return
        # Bounded element-lookup retry: the grid paginates via postback, so the
        # control id can differ after a re-render. Selecting a type is
        # idempotent, so resolving once more from a fresh read is safe.
        for _ in range(_TYPE_SELECT_ATTEMPTS - 1):
            refreshed = await self._read()
            retry_id = control_for(portal_type, refreshed.fields)
            target = retry_id or portal_type
            by = "selector" if retry_id else "label"
            outcome = await self._do(
                ToolCall("click", {"target": target, "by": by, "intent": "select_inspection_type"})
            )
            if outcome.get("success"):
                return
        raise RuntimeError(f"inspection type '{portal_type}' could not be selected in the wizard")

    async def _continue(self) -> None:
        """Advance a non-commit wizard page without labeling it as a schedule.

        The confirmation-step Continue is handled separately and explicitly
        acknowledges the commit. The dispatcher independently resolves that
        step from the observed flow position, so this navigation intent cannot
        weaken the commit guard.
        """
        outcome = await self._do(ToolCall("click", {"target": "Continue", "by": "text", "intent": "navigate"}))
        if not outcome.get("success"):
            raise RuntimeError("Continue did not advance the wizard")
        await self._wait(until_absent="Please wait...", seconds=8)

    async def _select_date(self, selected_date: str | None, date_page: PortalObservation) -> None:
        """Click the chosen day in the implied 3-month strip, then a time.

        The month tables carry no captions on this portal, so table index i is
        (reference month + i); `accela.resolve_calendar_months` is the one place
        that assumption lives. After the day click, `lblAvaliableTimes` fills
        and the first listed time range is clicked (deterministic, portal
        spelling). Any gap raises — a silently skipped time leaves Continue
        disabled forever.
        """
        if not selected_date:
            raise RuntimeError("executor selected no date; refusing to pick one arbitrarily")
        day = _dt.date.fromisoformat(selected_date)
        months = accela.resolve_calendar_months(list(date_page.calendar), reference=self._today())
        table_index = None
        for index, (year, month, active_days) in enumerate(months):
            if year == day.year and month == day.month:
                if day.day not in active_days:
                    raise RuntimeError(f"{selected_date} renders inactive on the portal calendar")
                table_index = index
                break
        if table_index is None:
            raise RuntimeError(f"{selected_date} is outside the rendered calendar months")
        selector = accela.active_calendar_day_selector(table_index, day.day)
        outcome = await self._do(
            ToolCall("click", {"target": selector, "by": "selector", "intent": "schedule_inspection"})
        )
        if not outcome.get("success"):
            raise RuntimeError(f"calendar day {selected_date} could not be clicked")
        await self._wait(until_absent="Loading...", seconds=6)
        observed = await self._read()
        if not observed.selectable_times:
            raise RuntimeError("no selectable times rendered after choosing the date")
        slot = self._pick_time(observed.selectable_times)
        time_selector = f'table[id*="{accela.CALENDAR_TABLE_ID_MARKER}"] td >> text="{slot}"'
        outcome = await self._do(
            ToolCall("click", {"target": time_selector, "by": "selector", "intent": "schedule_inspection"})
        )
        if not outcome.get("success"):
            raise RuntimeError(f"time slot '{slot}' could not be selected")

    @staticmethod
    def _pick_time(selectable_times: str) -> str:
        """The first listed time-range label (deterministic, portal-spelled)."""
        for line in selectable_times.splitlines():
            line = line.strip()
            if line:
                return line
        raise RuntimeError("selectable times rendered no parseable slot")

    # --- snapshot helpers --------------------------------------------------------

    @staticmethod
    def _eligibility(inspection_type: str | None, obs: PortalObservation) -> bool | None:
        """Whether the portal offers this type; None when the page is silent.

        The wizard's type grid is the offer list. When it is not rendered,
        eligibility is unknown — the snapshot must not say False, which the
        executor would read as a portal-side ineligibility verdict.
        """
        if not obs.offered_types:
            return None
        if not inspection_type:
            return True
        return inspection_type.casefold() in {t.casefold() for t in obs.offered_types}

    @staticmethod
    def _find_row(
        rows: Iterable[dict[str, str]], inspection_type: str | None, inspection_id: str | None
    ) -> dict[str, str] | None:
        for row in rows:
            if inspection_id and str(row.get("id") or "") == inspection_id:
                return row
            if inspection_type and str(row.get("type") or "").casefold() == inspection_type.casefold():
                return row
            if inspection_type is None and inspection_id is None:
                return row
        return None

    @staticmethod
    def _appointment_id(obs: PortalObservation, row: dict[str, str]) -> str | None:
        """The appointment's own id for one row, from its per-row action control.

        The citizen portal renders no id column; the row's Edit/Cancel control
        target is the identity (see `accela.parse_inspection_row_controls`).
        Binding is deliberately conservative: only the unambiguous case (exactly
        one scheduled/requested row AND exactly one cancel/reschedule control)
        binds, because a text row and a control cannot be matched positionally
        without a verified grid capture. Anything else stays None, which the
        policy layer reads as TARGET_INSPECTION_UNIDENTIFIED for a targeted
        mutation — fail-closed rather than acting on the wrong appointment.
        """
        scheduled = [
            candidate for candidate in obs.inspection_rows
            if "schedul" in str(candidate.get("status") or "").lower()
            or str(candidate.get("status") or "").strip().lower() in {"requested", "pending"}
        ]
        actionable = [c for c in obs.appointment_controls if c.get("verb") in {"cancel", "reschedule"}]
        if len(scheduled) == 1 and len(actionable) == 1 and row is scheduled[0]:
            return actionable[0].get("key") or actionable[0].get("control_id")
        return None

    @staticmethod
    def _snapshot_from_row(
        permit_id: str, row: dict[str, str], obs: PortalObservation, appointment_id: str | None = None
    ) -> InspectionSnapshot:
        status = str(row.get("status") or "Unknown")
        date_text = str(row.get("scheduled_date") or row.get("requested_date") or row.get("date") or "")
        scheduled = _normalize_date_token(date_text) or (date_text or None)
        return InspectionSnapshot(
            permit_id=permit_id,
            # An explicitly established appointment id outranks whatever the row
            # text carried (the text parser never emits one today).
            inspection_id=appointment_id or row.get("id"),
            inspection_type=str(row.get("type") or ""),
            status=status,
            scheduled_date=scheduled,
            eligible=True,
            confirmation_number=row.get("confirmation_number"),
            record_key=_ref_key(obs.record_ref),
        )

    @staticmethod
    def _unknown_snapshot(permit_id: str, inspection_type: str | None, reason: str) -> InspectionSnapshot:
        """An explicitly unknown state, never a guessed 'not scheduled'.

        The executor fails closed on these: an unknown permit id reads as
        STATE_MISMATCH; unknown eligibility reads as not eligible.
        """
        return InspectionSnapshot(
            permit_id=permit_id,
            inspection_id=None,
            inspection_type=inspection_type or "",
            status=f"Unknown ({reason})",
            eligible=False,
        )


def _require_no_running_loop() -> None:
    """Guard for the sync bridge: checked *before* a coroutine is created, so a
    misuse raises instead of leaking an un-awaited coroutine warning."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return
    raise RuntimeError(
        "AccelaInspectionPortal sync methods cannot be called from a running event loop; "
        "use the *_async variants"
    )


__all__ = ["AccelaInspectionPortal", "PortalObservation"]
