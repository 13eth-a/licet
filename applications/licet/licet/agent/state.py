"""Agent state representation.

Tracks everything the planner needs to decide its next action: where it is
in the portal, what it knows about the permit so far, what it has already
tried, and what (if anything) is blocked on user approval.

Design note (see `docs/phase0_review.md`): every stop condition in
`licet/agent/stop_conditions.py` must read a field that something actually
sets. Four conditions — missing information, no valid action, portal
unavailable, ambiguous record — previously had no producer anywhere in the
repo, so they could never fire. Each one now has a `record_*` method here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from licet.lookup import CurrentPermitState


@dataclass
class FailedAction:
    """A browser action that failed, keyed by page + action + args.

    The key matters. The same selector failing once on two different wizard
    pages is two unrelated failures, not a pattern, and ACA's postback races
    ("element was detached from the DOM, retrying") mean a retry that later
    succeeds must clear the count — see `AgentState.record_success`.
    """

    action: str
    error: str
    page: str | None = None
    args: dict[str, str] = field(default_factory=dict)
    attempt_count: int = 1

    @property
    def key(self) -> tuple[str | None, str, tuple[tuple[str, str], ...]]:
        return (self.page, self.action, tuple(sorted(self.args.items())))


@dataclass
class PendingApproval:
    action: str
    reason: str
    details: dict[str, Any] = field(default_factory=dict)
    approved: bool = False


@dataclass
class AgentState:
    goal: str

    current_url: str | None = None
    current_page: str | None = None  # human label: "search_results", "record_details", etc.

    current_permit: str | None = None
    active_permit: CurrentPermitState | None = None
    known_facts: dict[str, Any] = field(default_factory=dict)

    # Flow position. ACA postback wizards barely change the URL, so the only
    # reliable signal is the stepNumber/pageNumber pair captured here — and the
    # safety guard needs it to recognise a flow's commit point.
    flow_name: str | None = None
    flow_step: str | None = None
    flow_page: int | None = None

    completed_steps: list[str] = field(default_factory=list)
    planned_next_step: str | None = None

    failed_actions: list[FailedAction] = field(default_factory=list)
    user_constraints: list[str] = field(default_factory=list)

    pending_approval: PendingApproval | None = None
    step_count: int = 0

    # Signals behind the non-obvious stop conditions.
    # A plain multi-result search is NOT ambiguity: `ambiguous_candidates` is
    # set only once the agent has concluded it cannot disambiguate.
    missing_information: list[str] = field(default_factory=list)
    no_valid_action_reason: str | None = None
    portal_issue: str | None = None
    ambiguous_candidates: list[str] = field(default_factory=list)

    # Stall detection: consecutive observations that are identical in every way
    # we can see — same URL, same flow step, same page content. Each part was
    # added because leaving it out produced a false stop on a real run:
    # URL alone died inside a postback wizard, where every step shares one URL
    # (`CapDetail.aspx`, live P10), and URL+step alone died on record-section
    # navigation, where the URL and the flow step both stay put while the section
    # content changes (`record_detail/summary`, live P14).
    last_observed_url: str | None = None
    last_observed_step: str | None = None
    last_observed_signature: str | None = None
    stalled_steps: int = 0

    # Bounded action history prevents an executor/model from blindly repeating
    # the same action on the same page (e.g. clicking Search forever). Keep the
    # state small so it is safe to serialize into a run report.
    recent_actions: list[tuple[str, str, str]] = field(default_factory=list)
    max_recent_actions: int = 12
    repeated_action_limit: int = 3

    # Progress in *facts*, which is a different question from progress in bytes.
    # Live P08/P20 ran out of steps cycling record sections: every read returned
    # different text (so nothing looked stalled) while the record's actual state —
    # number, type, status — never changed. `fact_signature` is a fingerprint of
    # the durable facts we hold, and `steps_without_new_facts` counts observations
    # that taught us nothing new. The planner uses it to tell the model to
    # converge; the stall counter above could never see this case.
    fact_signature: str | None = None
    steps_without_new_facts: int = 0

    def note_facts(self, signature: str | None) -> int:
        """Record a fact fingerprint; returns consecutive observations with no new fact.

        `None` means the observation produced no record facts at all (a list page,
        a click, a dialog): it is not evidence either way, so the count is left
        alone — the same rule the stall counter learned the hard way.
        """
        if signature is None:
            return self.steps_without_new_facts
        if signature == self.fact_signature:
            self.steps_without_new_facts += 1
        else:
            self.fact_signature = signature
            self.steps_without_new_facts = 0
        return self.steps_without_new_facts

    def set_active_permit(self, permit_state: CurrentPermitState) -> None:
        """Persist the selected permit so later phases do not re-search blindly."""
        self.active_permit = permit_state
        permit_id = getattr(permit_state.permit, "permit_id", None)
        if permit_id:
            self.current_permit = str(permit_id)

    def clear_active_permit(self) -> None:
        self.active_permit = None
        self.current_permit = None

    def record_step(self, description: str) -> None:
        self.completed_steps.append(description)
        self.step_count += 1

    def record_action(self, action: str, target: str = "", *, state: str = "") -> bool:
        """Remember an action and return whether its recent repetition is bounded.

        The returned value is true once the exact action/target/page-state pair
        has occurred ``repeated_action_limit`` times in the recent window.
        Callers can stop or re-plan before sending another identical browser
        request. Older entries are discarded deterministically.
        """
        key = (str(action), str(target), str(state))
        self.recent_actions.append(key)
        if len(self.recent_actions) > self.max_recent_actions:
            del self.recent_actions[: -self.max_recent_actions]
        return self.recent_actions.count(key) >= self.repeated_action_limit

    def repeated_action(self, action: str, target: str = "", *, state: str = "") -> int:
        """Return the count for an action/state pair in the recent window."""
        return self.recent_actions.count((str(action), str(target), str(state)))

    def observe_page(
        self,
        url: str | None = None,
        page: str | None = None,
        signature: str | None = None,
    ) -> int:
        """Record where we are; returns consecutive pages with nothing new.

        "No progress" means the same URL, the same flow step *and* the same page
        content as the last time we actually looked at a page. Anything that
        changes counts as progress, because every part of that triple is
        observable and every part has produced a false stop when ignored.

        `signature` is optional so URL-only callers still work; without it (or
        without a URL) this only tracks position, and the stall counter is left
        alone.
        """
        if page is not None:
            self.current_page = page
        if url is not None:
            self.current_url = url
        if url is None or signature is None:
            # An action that returned no page (a click, a screenshot, a wait) is
            # not evidence of progress *or* of its absence. Counting it as "no
            # change" let three non-observations trip the stall threshold while a
            # model was still working (live P04: read, screenshot, wait).
            return self.stalled_steps
        same_url = url == self.last_observed_url
        same_step = page is None or page == self.last_observed_step
        same_content = signature == self.last_observed_signature
        self.stalled_steps = (
            self.stalled_steps + 1 if (same_url and same_step and same_content) else 0
        )
        self.last_observed_url = url
        self.last_observed_step = page
        self.last_observed_signature = signature
        return self.stalled_steps

    def record_failure(
        self,
        action: str,
        error: str,
        *,
        page: str | None = None,
        args: Mapping[str, str] | None = None,
    ) -> FailedAction:
        """Count a failure against its (page, action, args) key."""
        target_page = page if page is not None else self.current_page
        key = (target_page, action, tuple(sorted((args or {}).items())))
        for failure in self.failed_actions:
            if failure.key == key:
                failure.attempt_count += 1
                failure.error = error
                return failure
        failure = FailedAction(action=action, error=error, page=target_page, args=dict(args or {}))
        self.failed_actions.append(failure)
        return failure

    def record_success(
        self,
        action: str,
        *,
        page: str | None = None,
        args: Mapping[str, str] | None = None,
    ) -> None:
        """Clear the failure counter for one (page, action, args) key.

        A retry that eventually succeeds must not keep counting toward
        REPEATED_ACTION_FAILED — counting it made long, legitimate wizard runs
        stop early (Phase 0 bug, `docs/phase0_review.md` §3).
        """
        target_page = page if page is not None else self.current_page
        key = (target_page, action, tuple(sorted((args or {}).items())))
        self.failed_actions = [f for f in self.failed_actions if f.key != key]

    def request_approval(self, action: str, reason: str, **details: Any) -> None:
        self.pending_approval = PendingApproval(action=action, reason=reason, details=details)

    def is_approved(self, action: str) -> bool:
        """True when a human has granted this exact action *here and now*.

        An approval is scoped to what was on screen when it was requested: if
        the page, the record or the flow step moved in between, the grant no
        longer describes anything the user agreed to and must not be reused.
        A request that recorded no page context (older callers) keeps the
        previous action-name-only behaviour rather than failing open or closed
        by accident.
        """
        pending = self.pending_approval
        if pending is None or pending.action != action or not pending.approved:
            return False
        for key, current in (("url", self.current_url), ("permit", self.current_permit),
                             ("flow_step", self.flow_step)):
            recorded = pending.details.get(key)
            if recorded is not None and recorded != current:
                return False
        return True

    def grant_approval(self) -> None:
        """Mark the pending approval granted; does not execute anything."""
        if self.pending_approval is not None:
            self.pending_approval.approved = True

    def clear_approval(self) -> None:
        """Consume/withdraw the pending approval."""
        self.pending_approval = None

    def enter_flow(self, flow: str, step: str, page: int | None = None) -> None:
        self.flow_name = flow
        self.flow_step = step
        self.flow_page = page

    def record_missing_information(self, *fields: str) -> None:
        """Record required values Licet cannot obtain on its own."""
        for name in fields:
            if name and name not in self.missing_information:
                self.missing_information.append(name)

    def resolve_missing_information(self, *fields: str) -> None:
        """Drop named fields once supplied; with no names, clear the list."""
        if not fields:
            self.missing_information.clear()
            return
        self.missing_information = [f for f in self.missing_information if f not in fields]

    def record_no_valid_action(self, reason: str) -> None:
        """The action space is exhausted (e.g. no slots in any window tried).

        Terminal by design — the loop stops on it, so only set this once every
        alternative has genuinely been tried.
        """
        self.no_valid_action_reason = reason

    def record_portal_issue(self, reason: str) -> None:
        """Portal-side blocker: Cloudflare 1015, session timeout, 5xx, offline."""
        self.portal_issue = reason

    def clear_portal_issue(self) -> None:
        self.portal_issue = None

    def record_ambiguous_candidates(self, *records: str) -> None:
        """Record candidates the agent could not disambiguate between."""
        for record in records:
            if record and record not in self.ambiguous_candidates:
                self.ambiguous_candidates.append(record)

    def resolve_ambiguity(self) -> None:
        self.ambiguous_candidates.clear()
