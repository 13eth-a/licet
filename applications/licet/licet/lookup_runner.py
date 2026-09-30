"""Browser execution for Phase 2 permit lookups.

`licet/lookup.py` decides *what* to do; this module makes it *happen* through
`ToolDispatcher` — the only path to the browser — so the guard, the run log and
step accounting apply to a lookup exactly as they do to a planner step.

Two live-verified behaviors shape everything below (2026-09-18/20):

- Selecting a search mode is an auto-postback that swaps the whole form, and
  the option labels are agency-configured. `search_actions` therefore emits a
  *logical mode key*; the runner resolves the observed option label from the
  dropdown's own options (`accela.search_mode_option`) and re-reads the field
  inventory after the postback. Field ids are never cached across a mode switch.
- An agency pre-fills a narrow search date window (NI: 09/18/2024→09/18/2026)
  that can hide records entirely, so "widen the dates" is the standard
  zero-result retry before anything may conclude a record does not exist.

The runner is deliberately bounded: attempts come from the plan, pages from
`should_scan_next_page`, and there is no fallback beyond the plan — "never
broaden indefinitely" is enforced here, not left to model discipline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
import re
from urllib.parse import urljoin, urlparse

from licet.agent.state import AgentState
from licet.browser import accela
from licet.browser.dispatcher import ToolDispatcher
from licet.lookup import (
    DEFAULT_AMBIGUITY_MARGIN,
    DEFAULT_MIN_CONFIDENCE,
    STRONG_IDENTITY_REASONS,
    CurrentPermitState,
    LookupErrorCode,
    LookupMetrics,
    LookupMethod,
    LookupResult,
    LookupStatus,
    PermitLookupRequest,
    SearchAttempt,
    SearchResult,
    build_search_plan,
    classify_results_page,
    empty_result_retry,
    parse_search_results,
    pagination_actions,
    resolve_lookup,
    search_actions,
    should_scan_next_page,
    verify_record_identity,
    match_constraints, MatchState, record_numbers_match,
)
from licet.schema.extract import permit_from_page
from licet.schema.permit import Permit

MAX_SEARCH_ATTEMPTS = 3
MAX_RESULT_PAGES = 5


@dataclass
class LookupTrace:
    """The audit trail: what was parsed, what was tried, what was seen.

    Textual on purpose (GOAL → PARSED → SEARCH → RESULT → …) so a run log or a
    test can show the lookup's decision chain without a browser session.
    """

    goal: str
    parsed: PermitLookupRequest | None = None
    attempts: list[SearchAttempt] = field(default_factory=list)
    pages_scanned: int = 0
    result: LookupResult | None = None

    def lines(self) -> list[str]:
        lines = ["GOAL", self.goal]
        if self.parsed:
            summary = ", ".join(
                f"{key}: {value}"
                for key, value in self.parsed.model_dump(exclude_none=True).items()
            )
            lines += ["PARSED", summary]
        for attempt in self.attempts:
            suffix = " +widen dates" if attempt.widen_dates else ""
            lines += ["SEARCH", f"{attempt.method.value}: {attempt.fields}{suffix}"]
        if self.pages_scanned:
            lines += ["PAGES", str(self.pages_scanned)]
        if self.result:
            lines += [
                "RESULT",
                f"{self.result.status.value} confidence={self.result.confidence:.2f} "
                f"band={self.result.band.value}",
            ]
            for match in self.result.matches[:5]:
                reasons = ", ".join(match.match_reasons) or "-"
                lines.append(f"- {match.record_number} score={match.score:.2f} ({reasons})")
        return lines

    def report(self) -> str:
        return "\n".join(self.lines())


@dataclass(frozen=True)
class _Observed:
    """One postback-settled look at the results page."""

    read: dict[str, Any]
    rows: list[SearchResult]
    metadata: dict[str, Any]
    classification: str  # results | zero_results | parse_failed


class _LookupFailure(Exception):
    def __init__(self, step):
        self.step = step
        super().__init__(str(step.get("error") or "browser action failed"))


class LookupRunner:
    """Drive one `PermitLookupRequest` end to end through the dispatcher."""

    def __init__(
        self,
        dispatcher: ToolDispatcher,
        *,
        max_attempts: int = MAX_SEARCH_ATTEMPTS,
        max_pages: int = MAX_RESULT_PAGES,
        min_confidence: float = DEFAULT_MIN_CONFIDENCE,
        ambiguity_margin: float = DEFAULT_AMBIGUITY_MARGIN,
        html_source: Any | None = None,
        metrics: LookupMetrics | None = None,
    ) -> None:
        self.dispatcher = dispatcher
        if max_attempts < 1 or max_pages < 1 or not 0 <= min_confidence <= 1:
            raise ValueError("lookup budgets must be positive and confidence between zero and one")
        self.max_attempts = max_attempts
        self.max_pages = max_pages
        self.min_confidence = min_confidence
        self.ambiguity_margin = ambiguity_margin
        # Optional DOM source for table parsing (tests inject a fake). Live runs
        # use `client.page.content()` — a read, never an action.
        self._html_source = html_source
        self.trace = LookupTrace(goal="")
        self.opened: Permit | None = None
        self.identity_verified: bool = False
        self.open_error: str | None = None
        # Retrieval metrics accumulate across every `run` on this runner, so a
        # caller can hand one object to several runs (or several runners) and
        # read success/ambiguity/wrong-record rates off it directly.
        self.metrics = metrics if metrics is not None else LookupMetrics()
        self.actions_taken = 0

    # --- low-level helpers -------------------------------------------------

    async def _html(self) -> str:
        if self._html_source is not None:
            html = self._html_source()
            return html if isinstance(html, str) else ""
        page = getattr(self.dispatcher.client, "page", None)
        content = getattr(page, "content", None)
        if content is None:
            return ""
        try:
            return await content() or ""
        except Exception:  # noqa: BLE001 - a dead frame must not kill the lookup
            return ""

    async def _step(
        self, name: str, args: dict[str, Any], state: AgentState
    ) -> tuple[bool, dict[str, Any]]:
        # Every browser action goes through here, so this is the one place the
        # "browser actions per lookup" metric can be counted honestly.
        if state.no_valid_action_reason:
            raise _LookupFailure({"error": state.no_valid_action_reason})
        self.actions_taken += 1
        if name == "click" and args.get("target") == accela.SEARCH_BUTTON_TEXT:
            self.submissions += 1
        step = await self.dispatcher.execute({"name": name, "args": args}, state)
        if not step.get("success") or state.no_valid_action_reason:
            raise _LookupFailure(step)
        return True, step

    async def _field_inventory(self, state: AgentState) -> list[dict[str, Any]]:
        _ok, step = await self._step("read_page", {"include": ["text", "form", "errors"]}, state)
        return (step.get("data") or {}).get("fields", [])

    @staticmethod
    def _dropdown_options(fields: list[dict[str, Any]], id_suffix: str) -> list[str]:
        for field in fields:
            if str(field.get("id") or "").endswith(id_suffix):
                return [str(option) for option in (field.get("options") or [])]
        return []

    # --- form handling -----------------------------------------------------

    async def _resolve_actions(
        self, attempt: SearchAttempt, state: AgentState
    ) -> list[dict[str, Any]]:
        """Turn the plan's logical actions into concrete dispatcher calls.

        The search-mode dropdown's labels are agency-configured, so the plan's
        *mode key* is resolved against the dropdown's actual options before the
        auto-postback fires; the field inventory is then re-read fresh, because
        the postback replaced the whole form.
        """
        actions = search_actions(attempt)
        mode_key = next(
            (a["args"]["value"] for a in actions if a["name"] == "select"), None
        )
        inventory = await self._field_inventory(state)
        if mode_key is not None:
            label = accela.search_mode_option(
                self._dropdown_options(inventory, accela.SEARCH_MODE_DROPDOWN), mode_key
            )
            if label is None:
                # This agency exposes no such search mode: fall back, don't guess.
                return []
            ok, _ = await self._step("select", {
                "target": f"#{accela.SEARCH_MODE_DROPDOWN}",
                "value": label,
                "intent": "search_records",
            }, state)
            if not ok:
                return []
            inventory = await self._field_inventory(state)
        return self._bind_fields(
            [action for action in actions if action["name"] != "select"], inventory
        )

    @staticmethod
    def _bind_fields(
        actions: list[dict[str, Any]], fields: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Drop type actions whose field the current form does not render.

        Selectors are comma-grouped id-suffix lists (`txtAPO…, txtGS…`), so
        presence of *any* family keeps the action — the portal legitimately
        drops the other family on a mode switch. Filling a field the form no
        longer has would dispatch into nothing and the search would silently
        run unfiltered. If NONE of the attempt's fields are present, the attempt
        cannot run as planned: return empty so the caller records a form
        failure instead of submitting a search with no constraints at all.
        """

        def rendered(selector: str) -> bool:
            for option in selector.split(","):
                suffix = (
                    option.strip()
                    .replace('input[id$="', "")
                    .replace('"]', "")
                    .strip()
                )
                if suffix and any(
                    str(field.get("id") or "").endswith(suffix) for field in fields
                ):
                    return True
            return False

        bound = [
            action
            for action in actions
            if action["name"] != "type" or rendered(str(action["args"].get("target") or ""))
        ]
        wanted_types = [action for action in actions if action["name"] == "type"]
        kept_types = [action for action in bound if action["name"] == "type"]
        if wanted_types and not kept_types:
            return []
        return bound

    # --- results -----------------------------------------------------------

    async def _observe_results(self, state: AgentState) -> _Observed:
        _ok, step = await self._step("read_page", {"include": ["text", "form", "errors"]}, state)
        data = step.get("data") or {}
        text = str(data.get("text") or "")
        rows, metadata = parse_search_results(await self._html() or text)
        if accela.RECORD_DETAIL_URL_MARKER in str(data.get("url") or "").lower():
            classification = "detail"
        elif rows:
            classification = "results"
        else:
            classification = classify_results_page(text)  # zero_results | parse_failed
        return _Observed(read=step, rows=rows, metadata=metadata, classification=classification)

    async def _scan_all_pages(
        self, state: AgentState, first: _Observed
    ) -> tuple[list[SearchResult], _Observed]:
        """Collect rows across result pages, bounded by `max_pages`.

        ACA paginates with postback links and never changes its URL, so a
        pagination click must *prove* it turned the page: the grid footer's
        first/last/total must change. A click that reproduces the same footer is
        treated as a dead link and the scan stops rather than re-parsing the
        same rows (duplicate rows would skew ranking and ambiguity).
        """
        self._coverage_valid = not (first.metadata.get("parse_error") or first.metadata.get("truncated"))
        rows: list[SearchResult] = [r.model_copy(update={"source_page": 1}) for r in first.rows]
        observed = first
        pages: list[dict[str, Any]] = [first.metadata]
        while should_scan_next_page(observed.metadata, len(pages), self.max_pages):
            footer = (
                observed.metadata.get("first"),
                observed.metadata.get("last"),
                observed.metadata.get("total"),
            )
            clicked = True
            for action in pagination_actions():
                ok, _ = await self._step(action["name"], action["args"], state)
                if not ok:
                    clicked = False
                    break
            if not clicked:
                break
            observed = await self._observe_results(state)
            if (
                observed.metadata.get("first"),
                observed.metadata.get("last"),
                observed.metadata.get("total"),
            ) == footer:
                break
            self._coverage_valid &= (not observed.metadata.get("parse_error")
                and not observed.metadata.get("truncated")
                and observed.metadata.get("total") == footer[2]
                and observed.metadata.get("first") == (footer[1] or 0) + 1)
            pages.append(observed.metadata)
            rows.extend(r.model_copy(update={"source_page": len(pages)}) for r in observed.rows)
        self.trace.pages_scanned += len(pages)
        return rows, observed

    # --- the loop ----------------------------------------------------------

    async def run(self, goal: str, request: PermitLookupRequest, state: AgentState) -> LookupResult:
        """Run one lookup, then fold its outcome into `metrics`.

        Wrapping (rather than instrumenting every early return) is what keeps
        the counts exact: `_execute` has a return per outcome, and a metric
        incremented by hand at each one would eventually miss a branch.
        """
        self.actions_taken = 0
        self.submissions = 0
        state.clear_active_permit()
        try:
            result = await self._execute(goal, request, state)
        except _LookupFailure as exc:
            result = LookupResult(status=LookupStatus.FAILED,
                error_code=LookupErrorCode.BROWSER_ACTION_UNCERTAIN,
                message=str(exc), browser_error=exc.step,
                attempts=len(self.trace.attempts))
        self.trace.result = result
        self._record_metrics(result)
        return result

    def _record_metrics(self, result: LookupResult) -> None:
        """Update the Phase 2 retrieval KPIs for one completed lookup.

        Retries count search submissions beyond the first (plan attempts plus
        the runner's zero-result date-widening). A `RECORD_MISMATCH` is the
        wrong-record event; it never increments verified successes. The rate
        uses all lookup attempts as its denominator.
        """
        self.metrics.attempts += 1
        self.metrics.browser_actions += self.actions_taken
        self.metrics.retries += max(0, self.submissions - 1)
        if result.status is LookupStatus.FOUND:
            self.metrics.successful += 1
            selected = result.selected
            if selected and any(
                reason in STRONG_IDENTITY_REASONS for reason in selected.match_reasons
            ):
                self.metrics.exact_matches += 1
        elif result.status is LookupStatus.AMBIGUOUS:
            self.metrics.ambiguous += 1
        if (self.open_error or "").startswith(LookupErrorCode.RECORD_MISMATCH.value):
            self.metrics.wrong_records += 1

    async def _execute(self, goal: str, request: PermitLookupRequest, state: AgentState) -> LookupResult:
        """Plan → search → rank → (disambiguate) → open → verify.

        Never raises: every outcome — including "the form would not cooperate" —
        lands in the returned `LookupResult`, with `trace` carrying the evidence.
        """
        self.trace = LookupTrace(goal=goal)
        self.opened = None
        self.identity_verified = False
        self.open_error = None
        self.trace.parsed = request

        try:
            plan = build_search_plan(request, max_attempts=self.max_attempts)
        except ValueError:
            result = LookupResult(
                status=LookupStatus.INVALID,
                error_code=LookupErrorCode.INVALID_LOOKUP_INPUT,
                message="lookup request has no supported search key",
            )
            self.trace.result = result
            return result

        # Search results are a postback of CapHome.aspx with no URL of their
        # own, so a previous lookup's grid can still be on the page. A fresh
        # navigation is what guarantees the run never reads stale results.
        navigated, _ = await self._step("navigate", {"url": accela.search_url()}, state)
        if not navigated:
            result = LookupResult(
                status=LookupStatus.FAILED,
                error_code=LookupErrorCode.SEARCH_FORM_FAILED,
                message="could not open the search page",
                method=plan[0].method,
                attempts=0,
            )
            self.trace.result = result
            return result

        rows: list[SearchResult] = []
        zero_result_seen = False
        search_executed = False
        queue = list(plan)
        while queue and len(self.trace.attempts) < self.max_attempts:
            attempt = queue.pop(0)
            if self.trace.attempts:
                await self._step("navigate", {"url": accela.search_url()}, state)
            self.trace.attempts.append(attempt)
            observed = await self._run_attempt(attempt, state)
            if observed is None:
                continue
            search_executed = True
            if observed.classification == "detail":
                return await self._verify_detail(request, state, observed.read.get("data") or {},
                                                 method=attempt.method)
            if observed.classification == "parse_failed":
                return LookupResult(status=LookupStatus.FAILED,
                    error_code=LookupErrorCode.SEARCH_RESULTS_PARSE_FAILED,
                    message="Search did not produce a readable result set")
            if observed.classification == "zero_results":
                zero_result_seen = True
                if not attempt.widen_dates:
                    queue.insert(0, empty_result_retry(attempt))
                continue
            found, last = await self._scan_all_pages(state, observed)
            total = last.metadata.get("total")
            complete = (isinstance(total, int) and last.metadata.get("last") == total
                        and len(found) == total and self._coverage_valid
                        and observed.metadata.get("first") == 1)
            if found:
                rows = found
                break

        if rows:
            result = resolve_lookup(
                request,
                rows,
                complete=complete,
                min_confidence=self.min_confidence,
                ambiguity_margin=self.ambiguity_margin,
                method=plan[0].method,
                attempts=len(self.trace.attempts),
            )
        elif zero_result_seen:
            result = LookupResult(
                status=LookupStatus.NOT_FOUND,
                error_code=LookupErrorCode.RECORD_NOT_FOUND,
                message="search executed and the portal returned no records",
                method=plan[0].method,
                attempts=len(self.trace.attempts),
            )
        elif search_executed:
            result = LookupResult(
                status=LookupStatus.FAILED,
                error_code=LookupErrorCode.SEARCH_RESULTS_PARSE_FAILED,
                message="search ran but no result grid rendered; see trace",
                method=plan[0].method,
                attempts=len(self.trace.attempts),
            )
        else:
            result = LookupResult(
                status=LookupStatus.FAILED,
                error_code=LookupErrorCode.SEARCH_FORM_FAILED,
                message="search form could not be completed; see trace",
                method=plan[0].method,
                attempts=len(self.trace.attempts),
            )
        self.trace.result = result

        if result.status is LookupStatus.CANDIDATE and result.selected is not None:
            result = await self._open_and_verify(result, request, state)
        return result

    async def _run_attempt(
        self, attempt: SearchAttempt, state: AgentState
    ) -> _Observed | None:
        """Fill, submit, settle, observe. None means the form never submitted."""
        actions = await self._resolve_actions(attempt, state)
        if not actions:
            return None
        for action in actions:
            ok, _ = await self._step(action["name"], action["args"], state)
            if not ok:
                return None
        return await self._observe_results(state)

    # --- open + identity verification --------------------------------------

    async def _open_and_verify(self, result, request, state) -> LookupResult:
        selected = result.selected
        assert selected is not None
        if selected.source_page != self.trace.pages_scanned:
            target = urljoin(accela.search_url(), selected.href_or_target or "")
            if (urlparse(target).netloc != urlparse(accela.search_url()).netloc
                    or not accela.parse_ref_from_url(target)):
                return LookupResult(status=LookupStatus.FAILED,
                    error_code=LookupErrorCode.RECORD_OPEN_FAILED,
                    message="Selected row is on an earlier page without a stable detail URL",
                    matches=result.matches, method=result.method, attempts=len(self.trace.attempts))
            await self._step("navigate", {"url": target}, state)
        else:
            await self._step("click", {"target": selected.record_number, "by": "text",
                                      "intent": "open_record"}, state)
        _, step = await self._step("read_page", {"include": ["text", "form", "errors"]}, state)
        return await self._verify_detail(request, state, step.get("data") or {},
                                         selected=selected, method=result.method,
                                         matches=result.matches, confidence=result.confidence)

    async def _verify_detail(self, request, state, data, *, selected=None,
                             method=None, matches=None, confidence=1.0) -> LookupResult:
        # Detail facts come only from this fresh observation, never the grid.
        text = str(data.get("text") or "")
        def labelled(*labels):
            pattern = r"(?:^|\n)\s*(?:" + "|".join(labels) + r")\s*:?\s*\n?([^\n]+)"
            found = re.search(pattern, text, re.I)
            return found.group(1).strip() if found else None
        address = labelled("Work Location", "Site Address", "Project Address", "Address")
        applicant = labelled("Applicant", "Applicant Name")
        parcel = labelled("Parcel Number", "Parcel #", "Parcel")
        permit = permit_from_page(data, address=address or "", applicant=applicant)
        code = LookupErrorCode.IDENTITY_UNVERIFIED
        message = "Detail page does not independently establish every requested constraint"
        observed = None
        if permit and accela.RECORD_DETAIL_URL_MARKER in str(data.get("url") or "").lower():
            observed = SearchResult(record_number=permit.permit_id, record_type=permit.permit_type,
                address=address, applicant=applicant, parcel_number=parcel, status=permit.status,
                href_or_target=data.get("url"))
            constraints = match_constraints(request, observed)
            if selected:
                constraints["selected_record"] = (MatchState.MATCH if record_numbers_match(
                    selected.record_number, observed.record_number) else MatchState.CONTRADICTION)
                if selected.record_type:
                    constraints["selected_type"] = match_constraints(
                        PermitLookupRequest(permit_type=selected.record_type), observed)["permit_type"]
                if selected.identity_key:
                    constraints["stable_identity"] = (MatchState.MATCH if selected.identity_key ==
                        observed.identity_key else MatchState.CONTRADICTION)
            if MatchState.CONTRADICTION in constraints.values():
                code, message = LookupErrorCode.RECORD_MISMATCH, "Opened record contradicts the requested or selected identity"
            elif constraints and all(v is MatchState.MATCH for v in constraints.values()):
                self.opened, self.identity_verified = permit, True
                chosen = selected or observed
                result = LookupResult(status=LookupStatus.FOUND, selected=chosen, permit=permit,
                    identity_verified=True, confidence=confidence, method=method,
                    matches=matches or [observed], attempts=len(self.trace.attempts),
                    evidence={"url": data.get("url"), "constraints": constraints,
                              "detail_text": text})
                state.set_active_permit(CurrentPermitState(permit=permit,
                    lookup_method=method or LookupMethod.RECORD_NUMBER,
                    lookup_confidence=confidence, source_query=request,
                    search_results_seen=len(result.matches)))
                return result
        self.open_error = f"{code.value}: {message}"
        return LookupResult(status=LookupStatus.FAILED, error_code=code, message=message,
            matches=matches or ([observed] if observed else []), method=method,
            attempts=len(self.trace.attempts), evidence={"url": data.get("url"), "detail_text": text})
