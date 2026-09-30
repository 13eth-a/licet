"""Read-only targeted retrieval for Phase 3.

Executes ``ReasoningResult.needed_sections`` — the *declarative* read requests
the reasoning layer emits (reasoning contract: "Read requests are declarative
(``section``, ``entity_id``, ``reason``, ``needed_fact``, ``stop_when``), not
selectors, clicks, or URLs invented by the reasoning stage").

Safety shape:

- The only tool calls this runner may emit are ``read_page`` and, when the
  record detail is not the current page, a ``navigate`` to the record's own
  deep link plus a *benign* section-label click (``"Inspections"`` etc.), which
  the dispatcher resolves through ``BENIGN_TARGETS`` — the same guard, run log
  and verification every other path goes through. No scheduling, payment,
  resubmission or attestation call can be built by this module, and none is
  accepted from anywhere else: tool calls are constructed here, never passed in.
- One bounded attempt per missing section per retrieval pass (a shared budget
  across all sections). An unavailable or still-loading observation is not
  retried indefinitely — the answer degrades to ``partial`` instead.
- URL position is tracked from each dispatcher outcome (the planner is not
  involved, so nothing else maintains it).
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from licet.browser.accela import detail_url, inspection_detail_url, parse_ref_from_url
from licet.browser.dispatcher import ToolCall, ToolDispatcher
from licet.phase3 import accela_extract
from licet.phase3.extract import extract_partial_state, merge_partial_states
from licet.phase3.state import PermitState, Section

# Clicking these labels is how a record-detail section postback is opened. The
# dispatcher resolves each to `read_record` (benign target), so the guard still
# sees every one of them.
_SECTION_CLICK_LABELS: dict[str, str] = {
    "inspections": "Inspections",
    "fees": "Payments",
    "documents": "Attachments",
    "conditions": "Record Info",
    "history": "Record Info",
    "overview": "",
}

# Sections served by the record detail page itself (no click needed).
_DEFAULT_PAGE_SECTIONS = {"overview"}

# Label variants to try, in order, when a section opens by postback. Null
# Island's record detail does NOT render a visible "Inspections" link — the
# click-through evidence (`scripts/ni_section_clickthrough.py` observations,
# 2026-09-20) lists Record Info | Payments | Attachments plus "Schedule an
# Inspection", and the 2026-09-25 live run showed the "Inspections" anchor is
# present in the DOM but never visible (a dead-but-rendered wrapper: the
# client reports `not_actionable`, naming the hidden selectors). The right
# answer is not "the section is unavailable" but "try the label the portal
# actually renders". Every variant stays within the dispatcher's benign
# section-label resolution, so the guard still sees and logs each attempt.
_SECTION_LABEL_VARIANTS: dict[str, tuple[str, ...]] = {
    "inspections": ("Inspections", "Inspection History"),
    "fees": ("Payments", "Fees"),
    "documents": ("Attachments", "Documents"),
    "conditions": ("Record Info",),
    "history": ("Record Info", "Processing Status"),
}


@dataclass
class RetrievalOutcome:
    """What one targeted-retrieval pass did, for the run log and tests."""

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
    """Bounded, read-only section retrieval through the ToolDispatcher."""

    def __init__(
        self,
        dispatcher: ToolDispatcher,
        *,
        record_ref: dict[str, str] | None = None,
        max_reads_per_pass: int = 6,
        browser_state: dict[str, Any] | None = None,
    ) -> None:
        self.dispatcher = dispatcher
        # capID1/2/3 + module + agency — the identity needed to re-land on the
        # record detail when a targeted read is required from another page.
        self.record_ref = dict(record_ref) if record_ref else {}
        self.max_reads_per_pass = max_reads_per_pass
        # Phase 7: the settled page identity the planner's loop key consumes.
        # Callers pass the World's browser_state dict so retrieval writes
        # where the page actually settled, in place.
        self.browser_state = browser_state if browser_state is not None else {}
        self._current_url: str | None = None

    @property
    def current_url(self) -> str | None:
        return self._current_url

    def _sync_url(self, result: dict[str, Any]) -> None:
        """Track position from each dispatcher outcome (no planner involved)."""
        url = result.get("url") or (result.get("data") or {}).get("url")
        if url:
            self._current_url = str(url)

    async def retrieve_missing_sections(
        self, state: PermitState, needed: list[dict[str, str]]
    ) -> RetrievalOutcome:
        """Fetch the sections a question still needs; merge into ``state``.

        Returns the per-section outcome. Sections whose read failed stay listed
        in ``sections_failed`` — the caller reports a partial answer rather
        than looping.
        """
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
                # ACA renders "Loading..." first; one best-effort settle and
                # reread, then accept what is there. A mid-load read must never
                # become a fact, and a section that never finishes loading is
                # reported as such rather than blocked on.
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
                # Phase 7 (GLM): persist the *settled* page identity so the
                # planner's loop key is built from where the page actually
                # settled, never from a live render token (the DeepSeek handoff
                # residual). browser_state is excluded from the World
                # fingerprint, so this cannot fake progress either.
                self._record_browser_state(data)
        return outcome

    def _record_browser_state(self, data: dict[str, Any]) -> None:
        """Fold one settled read into the caller's browser-state dict.

        `settled_browser_state` writes url path, record identity (capID1/2/3),
        flow step and the portal findings; plain dict in, plain dict out, no
        imports from the planner layer.
        """
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
        """Land on the record detail (and the section's postback, if any), then read."""
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
            # The record URL already carries `IsToShowInspection=` (the
            # inspection-context deep link, verified in the UI map), so the
            # first settled read may already show the section. Read before
            # clicking and keep the read only when it is *decisive* evidence
            # for the section (complete or explicitly empty): a partial or
            # loading summary read is not the section, and the postback click
            # still has to be tried. If the read shows a portal finding, the
            # normal failure path below applies.
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
                # The captured P13 route shows inspections only with this view
                # flag; the ordinary summary's section anchors can be hidden.
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
        """Open a section by its postback label, trying the known label variants.

        The primary label is tried first; if the client reports it present but
        not actionable (the dead-but-rendered shape live-verified on Null
        Island), the next variant is tried before giving up. The settled read
        from a successful variant is returned so the caller's `loading` path
        sees the section the click actually opened, and the provenance of the
        call that changed the page is checked against the benign resolution —
        a click resolved to anything other than a read-record target is
        refused regardless of its outcome.
        """
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
                    # The guard resolved this label to something other than a
                    # read-record target. Fail closed: report the click failed
                    # rather than reading whatever the click opened.
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
            # Present-but-not-visible on a non-final variant: the portal is
            # rendering a dead wrapper for this label. Try the next one.
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
        """Minimal AgentState the dispatcher records outcomes onto.

        Phase 3 retrieval runs outside a planner run, so it gets a private
        scratch state (own counters, own flow position) instead of sharing the
        planner's. The current URL is tracked here from each outcome.
        """
        if not hasattr(self, "_scratch"):
            from licet.agent.state import AgentState

            self._scratch = AgentState(goal="phase3 targeted retrieval")
            if self._current_url:
                self._scratch.current_url = self._current_url
        return self._scratch


def record_ref_from_url(url: str) -> dict[str, str] | None:
    """capID1/2/3 + module + agency out of a CapDetail URL (runner re-entry)."""
    parsed = parse_ref_from_url(url or "")
    return parsed or None


def run_retrieval(
    state: PermitState,
    needed: list[dict[str, str]],
    dispatcher: ToolDispatcher,
    *,
    record_ref: dict[str, str] | None = None,
) -> RetrievalOutcome:
    """Sync wrapper for script callers (asyncio.run under the hood)."""
    runner = Phase3RetrievalRunner(dispatcher, record_ref=record_ref)
    return asyncio.run(runner.retrieve_missing_sections(state, needed))


__all__ = [
    "Phase3RetrievalRunner",
    "RetrievalOutcome",
    "record_ref_from_url",
    "run_retrieval",
    "Section",
]
