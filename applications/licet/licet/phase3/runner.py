"""read-only targeted retrieval for phase 3"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from licet.browser.accela import detail_url, inspection_detail_url, parse_ref_from_url
from licet.browser.dispatcher import ToolCall, ToolDispatcher
from licet.phase3 import accela_extract
from licet.phase3.extract import extract_partial_state, merge_partial_states
from licet.phase3.state import PermitState, Section

# clicking these labels is how a record-detail section postback is opened
_SECTION_CLICK_LABELS: dict[str, str] = {
    "inspections": "Inspections",
    "fees": "Payments",
    "documents": "Attachments",
    "conditions": "Record Info",
    "history": "Record Info",
    "overview": "",
}

_DEFAULT_PAGE_SECTIONS = {"overview"}

# label variants to try, in order, when a section opens by postback
_SECTION_LABEL_VARIANTS: dict[str, tuple[str, ...]] = {
    "inspections": ("Inspections", "Inspection History"),
    "fees": ("Payments", "Fees"),
    "documents": ("Attachments", "Documents"),
    "conditions": ("Record Info",),
    "history": ("Record Info", "Processing Status"),
}


@dataclass
class RetrievalOutcome:
    """what one targeted-retrieval pass did, for the run log and tests"""

    sections_requested: list[str] = field(default_factory=list)
    sections_retrieved: list[str] = field(default_factory=list)
    sections_failed: list[str] = field(default_factory=list)
    actions: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "sections_requested": self.sections_requested,
            "sections_retrieved": self.sections_retrieved,
            "sections_failed": self.sections_failed,
            "actions": self.actions,
        }


class Phase3RetrievalRunner:
    """bounded, read-only section retrieval through the tooldispatcher"""

    def __init__(
        self,
        dispatcher: ToolDispatcher,
        *,
        record_ref: dict[str, str] | None = None,
        max_reads_per_pass: int = 6,
        browser_state: dict[str, Any] | None = None,
    ) -> None:
        self.dispatcher = dispatcher
        self.record_ref = dict(record_ref) if record_ref else {}
        self.max_reads_per_pass = max_reads_per_pass
        self.browser_state = browser_state if browser_state is not None else {}
        self._current_url: str | None = None

    @property
    def current_url(self) -> str | None:
        return self._current_url

    def _sync_url(self, result: dict[str, Any]) -> None:
        """track position from each dispatcher outcome (no planner involved)"""
        url = result.get("url") or (result.get("data") or {}).get("url")
        if url:
            self._current_url = str(url)

    async def retrieve_missing_sections(
        self, state: PermitState, needed: list[dict[str, str]]
    ) -> RetrievalOutcome:
        """fetch the sections a question still needs; merge into ``state``"""
        outcome = RetrievalOutcome(sections_requested=[item["section"] for item in needed])
        for item in needed:
            if len(outcome.sections_retrieved) >= self.max_reads_per_pass:
                outcome.sections_failed.extend(
                    section
                    for section in outcome.sections_requested
                    if section not in outcome.sections_retrieved
                        and section not in outcome.sections_failed
                )
                break
            section = item["section"]
            data = await self._read_section(section, outcome)
            if data is None:
                outcome.sections_failed.append(section)
                continue
            observation = accela_extract.observation_for(section, data)
            if observation.get("coverage") == "loading":
                # aca renders "loading..." first; one best-effort settle and reread, then accept what is there
                await self._settle(outcome)
                data = await self._read_current(outcome)
                if data is not None:
                    observation = accela_extract.observation_for(section, data)
            if data:
                self._record_browser_state(data)
            from licet.phase7.portal import PortalState, route_recovery
            route = route_recovery(PortalState.from_observation(data)) if data else None
            if data is None or observation.get("coverage") == "loading" or route is not None:
                outcome.sections_failed.append(section)
                continue
            merge_partial_states(state, extract_partial_state(observation))
            outcome.sections_retrieved.append(section)
            if data:
                self._record_browser_state(data)
        return outcome

    def _record_browser_state(self, data: dict[str, Any]) -> None:
        """fold one settled read into the caller's browser-state dict"""
        from licet.phase7.portal import settled_browser_state

        merged = settled_browser_state(data)
        merged.setdefault("url", data.get("url"))
        self.browser_state.update(merged)

    async def _settle(self, outcome: RetrievalOutcome) -> None:
        try:
            await self.dispatcher.execute(
                ToolCall("wait", {"until_absent": "Loading...", "seconds": 3}),
                self._state_shim(),
            )
            outcome.actions.append({"wait": "until_absent=Loading...", "ok": True})
        except Exception:  # noqa: BLE001 - settle is best-effort by design
            outcome.actions.append({"wait": "until_absent=Loading...", "ok": False})

    async def _read_section(self, section: str, outcome: RetrievalOutcome) -> dict[str, Any] | None:
        """land on the record detail (and the section's postback, if any), then read"""
        if not self._on_record_detail():
            url = self._detail_url()
            if url is None:
                return None
            result = await self.dispatcher.execute(
                ToolCall("navigate", {"url": url}), self._state_shim()
            )
            self._sync_url(result)
            outcome.actions.append({"navigate": url, "ok": bool(result.get("success"))})
            if not result.get("success"):
                return None
            # the record url already carries `istoshowinspection=` (the inspection-context deep link,
            # verified in the ui map), so the first settled read may already show the section
            data = await self._read_current(outcome)
            if data is not None:
                observation = accela_extract.observation_for(section, data)
                if observation.get("coverage") in {"complete", "explicitly_empty"}:
                    from licet.phase7.portal import PortalState, route_recovery
                    if route_recovery(PortalState.from_observation(data)) is None:
                        return data
        label = _SECTION_CLICK_LABELS.get(section, "")
        if label and section not in _DEFAULT_PAGE_SECTIONS:
            data = await self._click_section(section, label, outcome)
            if data is None:
                if (section != "inspections" or not all(self.record_ref.get(k) for k in ("capID1", "capID2", "capID3"))
                        or not outcome.actions[-1].get("section_unavailable")):
                    return None
                # the captured p13 route shows inspections only with this view flag; the ordinary
                # summary's section anchors can be hidden
                url = inspection_detail_url(self.record_ref)
                result = await self.dispatcher.execute(
                    ToolCall("navigate", {"url": url}), self._state_shim()
                )
                self._sync_url(result)
                outcome.actions.append({"navigate": url, "ok": bool(result.get("success"))})
                if not result.get("success"):
                    return None
                data = await self._read_current(outcome)
                if data is None:
                    return None
                observation = accela_extract.observation_for(section, data)
                if observation.get("coverage") not in {"complete", "explicitly_empty"}:
                    return None
                return data
            return data
        return await self._read_current(outcome)

    async def _click_section(
        self, section: str, label: str, outcome: RetrievalOutcome
    ) -> dict[str, Any] | None:
        """open a section by its postback label, trying the known label variants"""
        for index, candidate in enumerate(_SECTION_LABEL_VARIANTS.get(section, (label,))):
            result = await self.dispatcher.execute(
                ToolCall("click", {"target": candidate, "by": "text"}), self._state_shim()
            )
            outcome.actions.append({"click": candidate, "ok": bool(result.get("success")),
                                    "section_unavailable": not result.get("blocked") and
                                    (result.get("error") or {}).get("kind") in {"not_found", "not_actionable"}})
            if result.get("success"):
                provenance = str((result.get("resolution") or {}).get("provenance") or "")
                if provenance != "benign_target":
                    # the guard resolved this label to something other than a read-record target
                    outcome.actions.append(
                        {"click": candidate, "ok": False, "refused": "not a benign section target"}
                    )
                    return None
                return await self._read_current(outcome)
            if result.get("blocked"):
                return None
            error = result.get("error") or {}
            message = str(error.get("message") or "")
            not_actionable = str(error.get("kind") or "") == "not_actionable" or (
                "present but not visible" in message
            )
            last_variant = index + 1 >= len(_SECTION_LABEL_VARIANTS.get(section, (label,)))
            if not not_actionable or last_variant:
                return None
            # present-but-not-visible on a non-final variant: the portal is rendering a dead wrapper for
            # this label
        return None

    async def _read_current(self, outcome: RetrievalOutcome) -> dict[str, Any] | None:
        result = await self.dispatcher.execute(
            ToolCall("read_page", {}), self._state_shim()
        )
        self._sync_url(result)
        outcome.actions.append({"read_page": True, "ok": bool(result.get("success"))})
        if not result.get("success"):
            return None
        data = result.get("data") or {}
        if isinstance(data, dict):
            self._record_browser_state(data)
        return data if isinstance(data, dict) else None

    def _on_record_detail(self) -> bool:
        return "capdetail.aspx" in (self._current_url or "").lower()

    def _detail_url(self) -> str | None:
        if not self.record_ref:
            return None
        try:
            return detail_url(
                self.record_ref["capID1"],
                self.record_ref["capID2"],
                self.record_ref["capID3"],
                module=self.record_ref.get("module", "Building"),
                agency_code=self.record_ref.get("agency_code", "NULLISLAND"),
            )
        except KeyError:
            return None

    def _state_shim(self) -> Any:
        """minimal agentstate the dispatcher records outcomes onto"""
        if not hasattr(self, "_scratch"):
            from licet.agent.state import AgentState

            self._scratch = AgentState(goal="phase3 targeted retrieval")
            if self._current_url:
                self._scratch.current_url = self._current_url
        return self._scratch


def record_ref_from_url(url: str) -> dict[str, str] | None:
    """capid1/2/3 + module + agency out of a capdetail url (runner re-entry)"""
    parsed = parse_ref_from_url(url or "")
    return parsed or None


def run_retrieval(
    state: PermitState,
    needed: list[dict[str, str]],
    dispatcher: ToolDispatcher,
    *,
    record_ref: dict[str, str] | None = None,
) -> RetrievalOutcome:
    """sync wrapper for script callers (asyncio.run under the hood)"""
    runner = Phase3RetrievalRunner(dispatcher, record_ref=record_ref)
    return asyncio.run(runner.retrieve_missing_sections(state, needed))


__all__ = [
    "Phase3RetrievalRunner",
    "RetrievalOutcome",
    "record_ref_from_url",
    "run_retrieval",
    "Section",
]
