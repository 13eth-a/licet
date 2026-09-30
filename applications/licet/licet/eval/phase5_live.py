"""live phase 5 capability wiring (solari + accela + phase 2/3/4)"""
from __future__ import annotations

import os
from pathlib import Path
from licet.phase7.journal import MutationJournal
from dataclasses import dataclass
from typing import Any

from dotenv import load_dotenv

load_dotenv()

from licet.agent.state import AgentState  # noqa: E402
from licet.browser import accela  # noqa: E402
from licet.browser.dispatcher import ToolCall, ToolDispatcher  # noqa: E402
from licet.browser.solari_client import SolariSession  # noqa: E402
from licet.lookup_runner import LookupRunner  # noqa: E402
from licet.phase3.runner import Phase3RetrievalRunner  # noqa: E402
from licet.phase3.state import Evidence, PermitState  # noqa: E402
from licet.phase4.accela_portal import AccelaInspectionPortal  # noqa: E402
from licet.phase4.actions import InspectionSnapshot  # noqa: E402
from licet.phase4.dates import available_dates_from_calendar, DateConstraints  # noqa: E402
from licet.phase4.selection import InspectionOption, SelectionContext  # noqa: E402
from licet.phase5.capabilities import LicetCapabilities, Preflight  # noqa: E402
from licet.phase5.state import World, operation_key  # noqa: E402

# lifecycle words that make a row a settled past attempt rather than one that could duplicate a new request
_SETTLED_RESULTS = {"PASSED", "FAILED"}
_SETTLED_LIFECYCLES = {"COMPLETED", "CLOSED", "CANCELLED", "CANCELED"}

# read-only forward-calendar search ceiling, shared with the citizen capacity query so the two layers
# cannot disagree
MAX_CALENDAR_WINDOWS = 36


def ref_from_record_key(record_key: str | None) -> dict[str, str] | None:
    """capid1/2/3 + module + agency from a ``recordref.as_key`` string"""
    parts = (record_key or "").split("/")
    if len(parts) != 5 or not all(parts):
        return None
    agency, module, cap_id1, cap_id2, cap_id3 = parts
    return {"capID1": cap_id1, "capID2": cap_id2, "capID3": cap_id3,
            "module": module, "agency_code": agency}


def _record_key_from_ref(ref: dict[str, str] | None) -> str | None:
    if not ref or not all(ref.get(key) for key in ("capID1", "capID2", "capID3")):
        return None
    return (f"{ref.get('agency_code') or accela.AGENCY_CODE}/"
            f"{ref.get('module') or accela.DEFAULT_MODULE}/"
            f"{ref['capID1']}/{ref['capID2']}/{ref['capID3']}")


def snapshot_status(inspection: Any) -> str:
    """a live inspection row as the phase 4 snapshot status vocabulary"""
    lifecycle = str(getattr(inspection, "lifecycle_normalized", "") or "").upper()
    result = str(getattr(inspection, "result_normalized", "") or "").upper()
    raw = str(getattr(inspection, "status", "") or "").strip()
    if lifecycle == "SCHEDULED" or raw.lower() in {"scheduled", "confirmed"}:
        return "Scheduled"
    if result in _SETTLED_RESULTS:
        return result.capitalize()
    if lifecycle in _SETTLED_LIFECYCLES:
        return "Cancelled" if lifecycle in {"CANCELLED", "CANCELED"} else "Completed"
    return raw or str(getattr(inspection, "raw_status", "") or "").strip() or "Unknown"


def options_from_types(names: tuple[str, ...]) -> tuple[InspectionOption, ...]:
    """offered wizard types as eligible selection options with evidence ids"""
    return tuple(
        InspectionOption(name, True, True, (f"portal-option:{name}",))
        for name in names if name
    )


def evidence_for_types(names: tuple[str, ...], record_key: str) -> dict[str, Evidence]:
    """compatibility helper for contexts built from a known offered-type list"""
    return {
        f"portal-option:{name}": Evidence(
            id=f"portal-option:{name}", section="inspections",
            raw_text=f"the scheduling wizard offers {name}", record_key=record_key,
        )
        for name in names if name
    }


def options_from_catalog(types: tuple[dict[str, Any], ...], record_key: str) -> tuple[tuple[InspectionOption, ...], dict[str, Evidence]]:
    """preserve offer and explicit `(required)` markers as separate evidence"""
    options = []
    evidence: dict[str, Evidence] = {}
    for item in types:
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        offer_id = f"portal-option:{name}"
        evidence[offer_id] = Evidence(
            id=offer_id, section="inspections",
            raw_text=f"the scheduling wizard offers {name}", record_key=record_key,
        )
        required = item.get("required") if isinstance(item.get("required"), bool) else None
        requirement_ids: tuple[str, ...] = ()
        if required is True:
            requirement_id = f"portal-required:{name}"
            evidence[requirement_id] = Evidence(
                id=requirement_id, section="inspections",
                raw_text=f"the scheduling wizard marks {name} (required)", record_key=record_key,
            )
            requirement_ids = (requirement_id,)
        options.append(InspectionOption(
            name, True, True, (offer_id,), required=required,
            requirement_evidence_ids=requirement_ids,
        ))
    return tuple(options), evidence


def history_from_permit(permit: PermitState | None, permit_id: str | None,
                        record_key: str | None) -> tuple[InspectionSnapshot, ...]:
    """the record's observed inspection rows as phase 4 snapshots"""
    if permit is None:
        return ()
    return tuple(
        InspectionSnapshot(
            permit_id=permit_id or "", inspection_id=row.inspection_id,
            inspection_type=row.type, status=snapshot_status(row),
            scheduled_date=row.scheduled_date or row.completed_date, record_key=record_key,
        )
        for row in permit.inspections
    )


def history_complete(permit: PermitState | None) -> bool:
    """whether the inspections section was read to completion (or is empty)"""
    if permit is None:
        return False
    coverage = permit.coverage.get("inspections")
    return bool(coverage and coverage.complete)


class LiveInspectionPortal(AccelaInspectionPortal):
    """the phase 4 adapter plus the two live reads phase 5's providers need"""

    async def inspection_catalog(self, permit_id: str, record_key: str, *,
                                 wizard=None) -> tuple[tuple[dict[str, Any], ...], bool]:
        """read every declared wizard type page without advancing to the calendar"""
        self.last_catalog = {
            "complete": False, "declared_count": None, "observed_count": 0,
            "pages_read": 0, "required_types": [], "offered_types": [],
            "failure": None,
        }
        wizard = wizard or await self._open_wizard()
        expected_key = _record_key_from_ref(self.record_ref)
        if (wizard.record_key != record_key or expected_key != record_key
                or str(wizard.record_header.get("permit_id") or "").casefold() != permit_id.casefold()):
            self.last_catalog["failure"] = "record_identity_mismatch"
            return (), False

        total = wizard.inspection_type_total
        self.last_catalog["declared_count"] = total
        if total is None or total < 0 or total > 200:
            # keep "the wizard never opened" separate from "it opened and declared nothing": the generic
            # message sent the 2026-09-30 investigation three layers off target, to the page the wizard
            # was supposed to have replaced
            self.last_catalog["failure"] = (
                "wizard_not_open"
                if "available inspection types" not in (wizard.text or "").casefold()
                else "invalid_or_missing_declared_count")
            return (), False
        gathered: dict[str, dict[str, Any]] = {}
        pages = 0
        max_pages = max(1, min((total + 9) // 10, 20))
        while pages < max_pages:
            page_types = accela.parse_inspection_types(wizard.fields)
            before = len(gathered)
            for option in page_types:
                key = option.name.casefold()
                value = {"name": option.name, "required": option.required}
                if key in gathered and gathered[key] != value:
                    return tuple(gathered.values()), False
                gathered[key] = value
            pages += 1
            if len(gathered) >= total:
                break
            if len(gathered) == before or "next >" not in " ".join(wizard.text.casefold().split()):
                break
            moved = await self._do(ToolCall("click", {
                "target": "Next >", "by": "text", "intent": "navigate",
            }))
            if not moved.get("success"):
                break
            await self._wait(until_present="Available Inspection Types", until_absent="Loading...", seconds=8)
            wizard = await self._read()
            if (wizard.record_key != record_key
                    or str(wizard.record_header.get("permit_id") or "").casefold() != permit_id.casefold()
                    or wizard.inspection_type_total != total):
                self.last_catalog.update({
                    "observed_count": len(gathered), "pages_read": pages,
                    "required_types": [item["name"] for item in gathered.values() if item.get("required") is True],
                    "offered_types": [item["name"] for item in gathered.values()],
                    "failure": "record_identity_or_catalog_changed",
                })
                return tuple(gathered.values()), False

        complete = len(gathered) == total
        self.last_catalog.update({
            "observed_count": len(gathered), "pages_read": pages,
            "required_types": [item["name"] for item in gathered.values() if item.get("required") is True],
            "offered_types": [item["name"] for item in gathered.values()],
            "complete": complete,
            "failure": None if complete else "catalog_pages_incomplete",
        })
        for _ in range(max(0, pages - 1)):
            moved = await self._do(ToolCall("click", {
                "target": "< Prev", "by": "text", "intent": "navigate",
            }))
            if not moved.get("success"):
                complete = False
                break
            await self._wait(until_present="Available Inspection Types", until_absent="Loading...", seconds=8)
            wizard = await self._read()
            if (wizard.record_key != record_key
                    or str(wizard.record_header.get("permit_id") or "").casefold() != permit_id.casefold()
                    or wizard.inspection_type_total != total):
                complete = False
                break

        if not complete:
            self.last_catalog["complete"] = False
            self.last_catalog["failure"] = "failed_to_restore_first_page"
        self.last_catalog["portal_type_page"] = wizard.inspection_type_total
        return tuple(gathered.values()), complete

    async def offered_types(self) -> tuple[str, ...]:
        wizard = await self._open_wizard()
        permit_id = str(wizard.record_header.get("permit_id") or "")
        if not permit_id or not wizard.record_key:
            return ()
        types, complete = await self.inspection_catalog(permit_id, wizard.record_key, wizard=wizard)
        return tuple(str(item["name"]) for item in types) if complete else ()

    async def available_dates(self, inspection_type: str | None, *,
                              constraints: DateConstraints | None = None,
                              max_windows: int = 12) -> tuple[str, ...]:
        self.last_availability = {
            "inspection_type": inspection_type,
            "calendar_read": False,
            "available_dates": [],
            "failure": None,
        }
        wizard = await self._open_wizard()
        if not inspection_type:
            self.last_availability["failure"] = "inspection_type_not_selected"
            return ()
        target = inspection_type.casefold()
        total = wizard.inspection_type_total
        if total is None or total <= 0 or total > 200:
            self.last_availability["failure"] = "invalid_or_missing_declared_count"
            return ()
        max_pages = max(1, min((total + 9) // 10, 20))
        for page in range(max_pages):
            page_types = accela.parse_inspection_types(wizard.fields)
            if any(option.name.casefold() == target for option in page_types):
                break
            if page + 1 >= max_pages or "next >" not in " ".join(wizard.text.casefold().split()):
                self.last_availability["failure"] = "target_type_not_found_in_catalog"
                return ()
            moved = await self._do(ToolCall("click", {
                "target": "Next >", "by": "text", "intent": "navigate",
            }))
            if not moved.get("success"):
                self.last_availability["failure"] = "catalog_page_navigation_failed"
                return ()
            await self._wait(until_present="Available Inspection Types", until_absent="Loading...", seconds=8)
            wizard = await self._read()
            if (wizard.record_key != _record_key_from_ref(self.record_ref)
                    or not wizard.record_header.get("permit_id")
                    or wizard.inspection_type_total != total):
                self.last_availability["failure"] = "record_identity_or_catalog_changed"
                return ()
        else:
            return ()
        await self._select_type(inspection_type, wizard)
        await self._continue()
        page = await self._read()
        return await self._scan_calendar(
            page, wizard, inspection_type, constraints=constraints, max_windows=max_windows,
        )

    async def _scan_calendar(self, page, wizard, inspection_type, *,
                             constraints: DateConstraints | None = None,
                             max_windows: int = 12) -> tuple[str, ...]:
        """observe forward windows, bounded and identity checked; never select a day"""
        from datetime import date
        constraints = constraints or DateConstraints()
        max_windows = max(1, min(max_windows, MAX_CALENDAR_WINDOWS))
        seen = set()
        observed = {}
        dates = ()
        previous = None
        failure = None
        stop = "search_limit"
        identity_verified = False
        windows = 0
        for index in range(max_windows):
            identity_verified = (
                page.record_key == _record_key_from_ref(self.record_ref)
                and bool(wizard.record_header.get("permit_id"))
                and str(page.record_header.get("permit_id") or "").casefold()
                == str(wizard.record_header.get("permit_id") or "").casefold()
            )
            if not identity_verified or not page.calendar or page.is_loading:
                failure = "calendar_record_identity_mismatch" if not identity_verified else "calendar_not_observed"
                stop = failure
                break
            # never infer future month identities from table position after paging
            if any(not str(m.get("month") or "").strip() for m in page.calendar):
                failure = stop = "calendar_month_identity_missing"
                break
            months = accela.resolve_calendar_months(list(page.calendar), reference=self._today())
            signature = tuple((y, m) for y, m, _ in months)
            if not signature or any(not y or not m for y, m in signature):
                failure = stop = "calendar_month_identity_missing"
                break
            if signature in seen or (previous is not None and signature[0] <= previous):
                failure = stop = "calendar_did_not_advance"
                break
            seen.add(signature)
            previous = signature[0]
            windows += 1
            for month in page.calendar:
                observed[str(month["month"])] = {
                    "month": str(month["month"]),
                    "active_day_count": len(month.get("active_days") or ()),
                    "inactive_day_count": len(month.get("inactive_days") or ()),
                    "any_available": bool(month.get("active_days")),
                }
            dates = tuple(day for day in available_dates_from_calendar(months)
                          if date.fromisoformat(day) >= self._today()
                          and constraints.allows(date.fromisoformat(day))
                          and (constraints.preferred is None or date.fromisoformat(day) == constraints.preferred))
            if dates:
                stop = "matching_dates_found"
                break
            end = constraints.preferred or constraints.end
            if end and signature[-1] >= (end.year, end.month):
                stop = "requested_window_exhausted"
                break
            if index + 1 >= max_windows:
                break
            moved = await self._do(ToolCall("click", {
                "target": "#ctl00_phPopup_calendar_AccelaLinkButton2",
                "by": "selector", "intent": "navigate",
            }))
            if not moved.get("success"):
                failure = stop = "calendar_navigation_failed"
                break
            await self._wait(until_absent="Please wait...", seconds=8)
            page = await self._read()
        self.last_availability = {
            "record_key": page.record_key,
            "permit_id": page.record_header.get("permit_id"),
            "inspection_type": inspection_type,
            "identity_verified": identity_verified,
            "calendar_months": list(observed.values()),
            "windows_read": windows,
            "search_limit_windows": max_windows,
            "search_stop": stop,
            "available_date_count": len(dates),
            "available_dates": list(dates),
            "calendar_read": bool(observed) and identity_verified,
            "availability_status": ("unknown" if failure else "available" if dates else
                                    "none_matching_constraints" if any(m["any_available"] for m in observed.values())
                                    else "none_in_observed_calendar"),
            "failure": failure,
        }
        return dates

    async def _select_date(self, selected_date, date_page):
        """re-find a future date after execution reopens the wizard at its first month"""
        from datetime import date
        if not selected_date:
            raise RuntimeError("executor selected no date")
        wanted = date.fromisoformat(selected_date)
        dates = await self._scan_calendar(
            date_page, date_page, None,
            constraints=DateConstraints(preferred=wanted),
        )
        if selected_date not in dates:
            raise RuntimeError("authorized date is no longer available within the verified calendar search")
        current = await self._read()
        if (current.record_key != date_page.record_key
                or current.record_header.get("permit_id") != date_page.record_header.get("permit_id")):
            raise RuntimeError("calendar record identity changed before date selection")
        await super()._select_date(selected_date, current)

    async def _open_wizard(self):
        """land on the record's inspection view, then open the wizard's type step"""
        obs = await self._read()
        if (obs.inspection_type_total is not None
                and "available inspection types" in obs.text.casefold()):
            return obs
        # land on the record's *inspection* view, not just the record detail
        ref = self.record_ref if isinstance(self.record_ref, dict) else None
        target = None
        if ref and all(ref.get(key) for key in ("capID1", "capID2", "capID3")):
            target = accela.inspection_detail_url(ref)
        elif "capdetail.aspx" not in (obs.url or "").lower():
            target = self._detail_url(obs)
        if target is not None:
            outcome = await self._do(ToolCall("navigate", {"url": target}))
            if not outcome.get("success"):
                return obs
            await self._wait(until_absent="Loading...")
            obs = await self._read()
        opener_present = any(label.casefold() in obs.text.casefold()
                             for label in accela.SCHEDULE_LINK_LABELS)
        if opener_present:
            # null island's schedule affordance is a clickable div with an inline
            # showinspectionpopupdialog handler; its nested label is a span and the similarly named anchor
            # is a dead placeholder
            await self._do(ToolCall("click", {
                "target": accela.SCHEDULE_LINK_CONTROL_ID,
                "by": "selector", "intent": "navigate",
            }))
        await self._wait(until_present="Available Inspection Types",
                         until_absent="Loading...", seconds=10)
        return await self._read()


@dataclass
class LiveCapabilities:
    """an open live session and the planner capabilities that ride it"""

    session: SolariSession
    client: Any
    dispatcher: ToolDispatcher
    capabilities: LicetCapabilities
    portal: LiveInspectionPortal

    async def close(self) -> None:
        try:
            await self.session.close()
        except Exception:  # noqa: BLE001 - teardown must never mask a result
            pass


async def build_live_capabilities(*, username: str | None = None,
                                  password: str | None = None,
                                  logger: Any | None = None) -> LiveCapabilities:
    """open one authenticated session and wire phase 2/3/4 into phase 5"""
    user = (username if username is not None else os.environ.get("ACCELA_TEST_USERNAME", "")).strip()
    secret = (password if password is not None else os.environ.get("ACCELA_TEST_PASSWORD", "")).strip()
    if not user or not secret:
        raise ValueError("live credentials are not configured (ACCELA_TEST_USERNAME/PASSWORD)")

    session = SolariSession()
    client = await session.client()
    dispatcher = ToolDispatcher(client, logger=logger)
    login = await client.login(user, secret)
    if not login.ok or not (login.data or {}).get("authenticated"):
        await session.close()
        raise RuntimeError("live login did not authenticate")

    lookup = LookupRunner(dispatcher)
    retrieval = Phase3RetrievalRunner(dispatcher)
    portal = LiveInspectionPortal(dispatcher)
    state = AgentState(goal="Phase 5 live")

    def _bind(world: World) -> None:
        """point the portal at the verified record before any provider read"""
        ref = ref_from_record_key(world.record_key)
        if ref:
            portal.record_ref = ref

    async def selection_context(world: World) -> SelectionContext:
        _bind(world)
        types, catalog_complete = await portal.inspection_catalog(world.permit_id or "", world.record_key or "")
        options, evidence = options_from_catalog(types, world.record_key or "")
        return SelectionContext(
            permit_id=world.permit_id or "",
            record_key=world.record_key or "",
            snapshot_id=world.snapshot_id,
            identity_verified=True,
            options=options,
            evidence=evidence,
            inspections=history_from_permit(world.permit, world.permit_id, world.record_key),
            history_complete=history_complete(world.permit),
            catalog_complete=catalog_complete,
        )

    async def preflight(world: World) -> Preflight:
        _bind(world)
        wanted = world.proposal.inspection_type if world.proposal else None
        names = await portal.offered_types()
        eligible = bool(names) and (wanted is None or wanted.casefold() in {n.casefold() for n in names})
        dates = await portal.available_dates(
            wanted, constraints=world.proposal.date_constraints if world.proposal else None
        ) if eligible else ()
        # preserve preflight evidence in the planner report
        details = {
            "catalog": dict(getattr(portal, "last_catalog", {})),
            "availability": dict(getattr(portal, "last_availability", {})),
            "eligibility_source": "complete inspection wizard catalog",
            "cost": {"value": None, "status": "unknown",
                     "reason": "portal does not render scheduling cost"},
            "signature": {"required": None, "status": "unknown",
                          "reason": "portal does not render signature requirement"},
        }
        return Preflight(
            record_key=world.record_key or "", snapshot_id=world.snapshot_id,
            operation_fingerprint=operation_key(world), eligible=eligible,
            available_dates=tuple(dates), cost=None, signature_required=None,
            details=details,
        )

    capabilities = LicetCapabilities(
        lookup=lookup, retrieval=retrieval, selection_context=selection_context,
        preflight=preflight, portal=portal, agent_state=state,
        mutation_journal=MutationJournal(Path(os.environ.get("LICET_MUTATION_JOURNAL", str(Path.home() / ".licet" / "mutation-journal.sqlite3")))),
    )
    return LiveCapabilities(session=session, client=client, dispatcher=dispatcher,
                            capabilities=capabilities, portal=portal)


__all__ = [
    "LiveCapabilities", "LiveInspectionPortal", "build_live_capabilities",
    "ref_from_record_key", "snapshot_status", "options_from_types",
    "evidence_for_types", "options_from_catalog", "history_from_permit", "history_complete",
]
