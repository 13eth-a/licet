"""the accela adapter: `inspectionportal` implemented against the real ui"""  # noqa: E501

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

# a citizen-portal inspection row shape aca renders on detail pages: "electrical final | scheduled |
# 09/24/2026"
_SETTLE_ATTEMPTS = 3
_READ_ATTEMPTS = 2
_TYPE_SELECT_ATTEMPTS = 2

# action kinds whose live ui is not mapped: driving them through the scheduling wizard would perform a
# different mutation than requested, so the adapter fails closed rather than improvising a flow
_UNMAPPED_REASONS: dict[str, str] = {
    "cancel": "cancellation flow is not mapped on this portal; refusing to improvise",
    "cancel_inspection": "cancellation flow is not mapped on this portal; refusing to improvise",
    "reschedule": "reschedule flow is not mapped on this portal; refusing to improvise",
    "reschedule_inspection": "reschedule flow is not mapped on this portal; refusing to improvise",
}

# this literal shape is the adapter's fallback — same row, date preserved
_SCHEDULED_ROW_RE = re.compile(
    r"^(?P<type>[^|]+?)\s*\|\s*(?P<status>Scheduled|Requested|Cancelled|Canceled|Completed|Passed|Failed|Pending)"
    r"(?:\s*\|\s*(?P<date>\d{1,2}/\d{1,2}/\d{4}))?\s*$",
    re.I,
)


def _adapter_rows(text: str) -> list[dict[str, str]]:
    """type/status/date rows from the citizen detail's inspections text"""
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
    """stable record key (``recordref.as_key`` form) from a parsed page ref"""
    ref = ref or {}
    if not all(ref.get(name) for name in ("capID1", "capID2", "capID3")):
        return None
    return (
        f"{ref.get('agency_code') or accela.AGENCY_CODE}/"
        f"{ref.get('module') or accela.DEFAULT_MODULE}/"
        f"{ref['capID1']}/{ref['capID2']}/{ref['capID3']}"
    )


def _normalize_date_token(value: str) -> str | None:
    """mm/dd/yyyy (aca's rendering) or iso input as the snapshot's iso date"""
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
    """everything one read_page pass yielded, in portal terms"""

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
    """drives the real inspection ui through the tooldispatcher"""

    def __init__(
        self,
        dispatcher: ToolDispatcher,
        *,
        record_ref: dict[str, str] | None = None,
        today: Callable[[], _dt.date] | None = None,
    ) -> None:
        self.dispatcher = dispatcher
        self.record_ref = dict(record_ref) if record_ref else {}
        # expose the portal's current host to the phase 6 executor
        self.environment = detect_environment(dispatcher.client)
        self._today = today or _dt.date.today
        self.steps: list[dict[str, Any]] = []


    def _state(self) -> AgentState:
        """a private scratch state; the adapter runs outside planner loops"""
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
        """read the page, retrying a failed read before trusting an empty result"""
        observation = PortalObservation.from_payload({})
        for _ in range(_READ_ATTEMPTS):
            outcome = await self._do(ToolCall("read_page", {"include": ["text", "form", "errors", "frames", "html"]}))
            data = outcome.get("data") or {}
            observation = PortalObservation.from_payload(data)
            if outcome.get("success") and data:
                return observation
        return observation

    async def _wait(self, *, until_present: str | None = None, until_absent: str | None = None, seconds: int = 6) -> None:
        """settle the page, re-asking a bounded number of times on a timeout"""
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


    def _detail_url(self, obs: PortalObservation) -> str | None:
        """the record deep link from the page's own ref, else the verified one"""
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
        """open aca's schedule wizard through its handler-bearing control"""
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


    async def read_inspection_state_async(
        self,
        permit_id: str,
        inspection_type: str | None = None,
        inspection_id: str | None = None,
    ) -> InspectionSnapshot:
        """read one inspection's state off the record detail page"""
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
            return self._unknown_snapshot(
                self._shown_permit(obs) or permit_id, inspection_type, "portal is showing a different record"
            )
        if obs.is_loading:
            return self._unknown_snapshot(permit_id, inspection_type, "inspections section still loading")
        if not obs.inspection_rows and not accela.declares_no_inspections(obs.text) and not obs.offered_types:
            # the summary page says nothing about inspections: the section is a postback away
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
        # recheck after every navigation/postback, not just the initial read
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


    async def submit_inspection_action_async(
        self, action: InspectionAction, *, portal_type: str, selected_date: str | None = None
    ) -> str | None:
        """drive the scheduling wizard for one already-authorized action"""
        kind = action.action_type.casefold().strip()

        # the deepest line of defence, and the only line that sees the real browser
        observed = detect_environment(self.dispatcher.client)
        if observed is Environment.UNKNOWN:
            # second, independent source: the url the dispatcher recorded for the page it last read on
            # this portal's own state
            observed = environment_from_url(self._state().current_url)
        if observed is not Environment.SANDBOX:
            detail = "live municipal record" if observed is Environment.LIVE_READ_ONLY else \
                f"unclassified portal ({observed.value})"
            raise RuntimeError(f"{detail}: refusing to submit an inspection action")

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
            # second identity gate, at the real ui boundary: the page we would act on must be the record
            # the action was authorized for
            raise RuntimeError("page record identity does not match the authorized record key; refusing to act")

        if not await self._open_schedule_wizard(obs):
            raise RuntimeError("no scheduling link actionable on the record detail")

        await self._wait(until_present="Inspection Type", until_absent="Loading...", seconds=10)
        wizard = await self._read()
        await self._select_type(portal_type, wizard)
        await self._continue()

        date_page = await self._read()
        await self._select_date(selected_date, date_page)
        await self._continue()

        # step 3: the confirm step
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
        # no confirmation number printed: a scheduled row for the type is the portal's own acknowledgement
        # (verification is the executor's independent re-read anyway)
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


    async def _select_type(self, portal_type: str, wizard: PortalObservation) -> None:
        """select the inspection type from the wizard's radio grid"""
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
        # bounded element-lookup retry: the grid paginates via postback, so the control id can differ
        # after a re-render
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
        """advance a non-commit wizard page without labeling it as a schedule"""
        outcome = await self._do(ToolCall("click", {"target": "Continue", "by": "text", "intent": "navigate"}))
        if not outcome.get("success"):
            raise RuntimeError("Continue did not advance the wizard")
        await self._wait(until_absent="Please wait...", seconds=8)

    async def _select_date(self, selected_date: str | None, date_page: PortalObservation) -> None:
        """click the chosen day in the implied 3-month strip, then a time"""
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
        """the first listed time-range label (deterministic, portal-spelled)"""
        for line in selectable_times.splitlines():
            line = line.strip()
            if line:
                return line
        raise RuntimeError("selectable times rendered no parseable slot")


    @staticmethod
    def _eligibility(inspection_type: str | None, obs: PortalObservation) -> bool | None:
        """whether the portal offers this type; none when the page is silent"""
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
        """the appointment's own id for one row, from its per-row action control"""
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
            # an explicitly established appointment id outranks whatever the row text carried (the text
            # parser never emits one today)
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
        """an explicitly unknown state, never a guessed 'not scheduled'"""
        return InspectionSnapshot(
            permit_id=permit_id,
            inspection_id=None,
            inspection_type=inspection_type or "",
            status=f"Unknown ({reason})",
            eligible=False,
        )


def _require_no_running_loop() -> None:
    """guard for the sync bridge: checked *before* a coroutine is created, so a misuse raises instead of leaking an un-awaited coroutine warning"""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return
    raise RuntimeError(
        "AccelaInspectionPortal sync methods cannot be called from a running event loop; "
        "use the *_async variants"
    )


__all__ = ["AccelaInspectionPortal", "PortalObservation"]
