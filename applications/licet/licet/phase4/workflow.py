from __future__ import annotations

from dataclasses import replace
from typing import Protocol

from licet.phase4.actions import (
    ActionErrorCode,
    ActionVerificationState,
    InspectionAction,
    InspectionActionResult,
    InspectionSnapshot,
    MutationAudit,
)
from licet.phase4.dates import closest_alternatives, select_date
from licet.phase4.matching import match_inspection_type
from licet.phase4.policy import decide_action_policy
from licet.safety.policy import (
    ConfirmationRequest,
    Environment,
    PolicyEngine,
    ProposedAction,
    RecordIdentity,
    detect_environment,
)

_UNSET = object()


class InspectionPortal(Protocol):
    def read_inspection_state(self, permit_id: str, inspection_type: str | None = None, inspection_id: str | None = None) -> InspectionSnapshot: ...
    def submit_inspection_action(self, action: InspectionAction, *, portal_type: str, selected_date: str | None = None) -> str | None: ...


class InspectionActionExecutor:
    """browser-independent workflow; the adapter owns accela clicks/forms"""

    def __init__(self, portal: InspectionPortal, *, policy_engine: PolicyEngine | None = None,
                 environment: Environment | None = None, constraints=None,
                 run_id: str = "run", user_goal: str = "", audit_log=None,
                 metrics=None) -> None:
        self.portal = portal
        self.policy_engine = policy_engine or PolicyEngine(
            environment=(environment if environment is not None else detect_environment(portal)),
            constraints=constraints, run_id=run_id, user_goal=user_goal, audit_log=audit_log,
            metrics=metrics,
        )
        self.metrics = metrics
        self.audits: list[MutationAudit] = []

    def execute(
        self,
        action: InspectionAction,
        *,
        eligible_types: list[str] | tuple[str, ...] = (),
        available_dates: list[str] | tuple[str, ...] = (),
        required_inputs: dict[str, str] | None = None,
        confirmed: bool = False,
        approval: ConfirmationRequest | None = None,
        allow_alternatives: bool = False,
    ) -> InspectionActionResult:
        approved = bool(confirmed or approval is not None)
        policy = decide_action_policy(action, confirmed=approved)
        if not policy.allowed:
            code = ActionErrorCode.ACTION_REQUIRES_CONFIRMATION if policy.requires_confirmation else ActionErrorCode.ACTION_NOT_ALLOWED
            return self._failure(action, code, policy.reason)
        kind = action.action_type.casefold().strip()
        # an omitted mapping means "no inputs supplied", not "the portal requires nothing": required
        # fields stay unsatisfied and must stop the action
        inputs = required_inputs or {}
        portal_type = match_inspection_type(action.inspection_type or "", list(eligible_types))
        if not portal_type:
            return self._failure(action, ActionErrorCode.INSPECTION_NOT_ELIGIBLE, "inspection type is not an exact eligible portal option")
        before = self.portal.read_inspection_state(action.permit_id, portal_type, action.existing_inspection_id)
        if before.permit_id != action.permit_id:
            return self._failure(action, ActionErrorCode.STATE_MISMATCH, "verified permit does not match requested permit", before=before)
        if action.record_key and before.record_key and before.record_key != action.record_key:
            # the action is authorized for one stable record; the page being read is another
            return self._failure(action, ActionErrorCode.STATE_MISMATCH, "observed record key does not match the authorized record", before=before)
        if not before.eligible:
            return self._failure(action, ActionErrorCode.INSPECTION_NOT_ELIGIBLE, "portal reports this inspection is disabled or ineligible", before=before)
        if action.existing_inspection_id and before.inspection_id != action.existing_inspection_id:
            return self._failure(action, ActionErrorCode.STATE_MISMATCH, "verified inspection does not match requested inspection", before=before)
        if kind in {"schedule", "schedule_inspection"} and (before.is_scheduled or before.is_pending):
            # idempotency: an existing appointment or an accepted in-flight request already produces the
            # desired outcome; submitting again duplicates it. "not scheduled" is requestable and is not
            # blocked
            return self._failure(action, ActionErrorCode.INSPECTION_ALREADY_SCHEDULED, "inspection is already scheduled or has an in-flight request", before=before)
        if kind in {"reschedule", "reschedule_inspection"} and not before.is_scheduled:
            return self._failure(action, ActionErrorCode.RESCHEDULE_FAILED, "target inspection is not scheduled", before=before)
        if kind in {"cancel", "cancel_inspection"} and (before.is_cancelled or before.is_completed):
            return self._failure(action, ActionErrorCode.CANCELLATION_FAILED, "completed or already-cancelled inspection cannot be cancelled", before=before)
        missing = [name for name in before.required_fields if not inputs.get(name)]
        if missing:
            return self._failure(action, ActionErrorCode.MISSING_REQUIRED_INPUT, "missing required input: " + ", ".join(missing), before=before)

        phase6_confirmation = approval
        if self.policy_engine is not None:
            proposed = ProposedAction(
                action_type=action.action_type,
                permit_id=action.permit_id,
                target=action.inspection_type or action.existing_inspection_id,
                inspection_type=portal_type,
                inspection_id=action.existing_inspection_id,
                existing_date=before.scheduled_date,
                date_window_start=action.date_window_start,
                date_window_end=action.date_window_end,
                required_inputs=tuple(before.required_fields),
                record_key=action.record_key,
            )
            observed = RecordIdentity(
                permit_id=before.permit_id, record_key=before.record_key,
                inspection_type=before.inspection_type, inspection_id=before.inspection_id,
                existing_date=before.scheduled_date, status=before.status,
            )
            decision = self.policy_engine.decide(
                proposed, observed_identity=observed,
                confirmation=phase6_confirmation, required_inputs=inputs,
                pre_action_state=before,
            )
            if not decision.allowed:
                if decision.requires_confirmation:
                    code = ActionErrorCode.ACTION_REQUIRES_CONFIRMATION
                else:
                    code_map = {
                        "LIVE_MUTATION_BLOCKED": ActionErrorCode.LIVE_MUTATION_BLOCKED,
                        "UNKNOWN_ENVIRONMENT": ActionErrorCode.UNKNOWN_ENVIRONMENT,
                        "UNKNOWN_ACTION_RISK": ActionErrorCode.UNKNOWN_ACTION_RISK,
                        "RECORD_IDENTITY_UNVERIFIED": ActionErrorCode.RECORD_IDENTITY_UNVERIFIED,
                        "TARGET_INSPECTION_UNIDENTIFIED": ActionErrorCode.TARGET_INSPECTION_UNIDENTIFIED,
                        "MAX_MUTATIONS_PER_RUN": ActionErrorCode.MAX_MUTATIONS_PER_RUN,
                    }
                    code = code_map.get(decision.violated_constraint or "", ActionErrorCode.ACTION_NOT_ALLOWED)
                return self._failure(action, code, decision.reason, before=before)
        selected = None
        if kind in {"schedule", "schedule_inspection", "reschedule", "reschedule_inspection"}:
            constraints = action.date_constraints
            selected = select_date(list(available_dates), constraints)
            if selected is None:
                dated = bool(action.date_window_start or action.date_window_end or action.preferred_date)
                code = ActionErrorCode.DATE_CONSTRAINT_UNSATISFIED if dated else ActionErrorCode.NO_AVAILABLE_DATES
                # advisory alternatives are reported, never selected: the action still fails, and widening
                # needs a new user instruction
                alternatives = tuple(closest_alternatives(available_dates, constraints)) if allow_alternatives else ()
                return self._failure(action, code, "no available date satisfies the user's constraints",
                                     before=before, alternatives=alternatives)
            if kind in {"reschedule", "reschedule_inspection"} and selected == before.scheduled_date:
                # a same-date reschedule is indistinguishable from a no-op, so a verified "old date
                # changed to new date" result is impossible
                return self._failure(action, ActionErrorCode.RESCHEDULE_FAILED, "requested date equals the current scheduled date; no change to verify", before=before)
        proposed = replace(before, scheduled_date=selected or before.scheduled_date,
                           status="Cancelled" if kind in {"cancel", "cancel_inspection"} else "Scheduled")
        mutation_id = None
        if self.policy_engine is not None:
            ledger_action = ProposedAction(
                action_type=action.action_type, permit_id=action.permit_id,
                target=action.inspection_type or action.existing_inspection_id,
                inspection_type=portal_type, inspection_id=action.existing_inspection_id,
                existing_date=before.scheduled_date, date_window_start=action.date_window_start,
                date_window_end=action.date_window_end, record_key=action.record_key,
            )
            reservation = self.policy_engine.ledger.begin(ledger_action, before_state=before)
            if not reservation.allowed:
                code = (ActionErrorCode.MUTATION_ALREADY_COMPLETED
                        if reservation.state is not None and reservation.state.value == "VERIFIED_SUCCESS"
                        else ActionErrorCode.MAX_MUTATIONS_PER_RUN
                        if reservation.reason == "MAX_MUTATIONS_PER_RUN"
                        else ActionErrorCode.UNCERTAIN_SUBMISSION)
                return self._failure(action, code, reservation.reason, before=before)
            mutation_id = reservation.mutation_id
        submitted = False
        try:
            if self.policy_engine is not None and mutation_id:
                self.policy_engine.ledger.mark_submitted(mutation_id)
            confirmation = self.portal.submit_inspection_action(action, portal_type=portal_type, selected_date=selected)
            submitted = True
            self._record_submission()
        except Exception as exc:  # uncertain mutation: reconcile, never replay blindly
            submitted = True
            self._record_submission()
            observed = self.portal.read_inspection_state(action.permit_id, portal_type, action.existing_inspection_id)
            steps = ("submit", "re-read after uncertain response")
            if self._matches(kind, proposed, observed, action):
                if self.policy_engine is not None and mutation_id:
                    self.policy_engine.ledger.mark_verified_success(mutation_id, observed)
                return self._success(action, before, observed, confirmation=None, steps=steps, submitted=submitted)
            if self.policy_engine is not None and mutation_id:
                self.policy_engine.ledger.mark_unknown(mutation_id, str(exc))
            self._record_verification(success=False, verified=False)
            return self._failure(action, ActionErrorCode.UNCERTAIN_SUBMISSION, "submission response was uncertain; state did not verify",
                                 before=before, after=observed, verification=ActionVerificationState.UNVERIFIED,
                                 steps=steps, response=str(exc), proposed=proposed)
        after = self.portal.read_inspection_state(action.permit_id, portal_type, action.existing_inspection_id)
        if not self._matches(kind, proposed, after, action):
            if self.policy_engine is not None and mutation_id:
                self.policy_engine.ledger.mark_verified_failure(mutation_id, "portal state does not match requested action", after)
            return self._failure(action, ActionErrorCode.ACTION_VERIFICATION_FAILED, "portal state does not match requested action",
                                 before=before, after=after, verification=ActionVerificationState.STATE_MISMATCH,
                                 steps=("submit", "re-read inspection state"),
                                 response=str(confirmation) if confirmation else None, proposed=proposed)
        if self.policy_engine is not None and mutation_id:
            self.policy_engine.ledger.mark_verified_success(mutation_id, after)
        return self._success(action, before, after, confirmation, ("submit", "re-read inspection state"),
                             submitted=submitted)

    @staticmethod
    def _matches(kind: str, proposed: InspectionSnapshot, observed: InspectionSnapshot,
                 action: InspectionAction | None = None) -> bool:
        """whether the re-read state is the outcome the authorized action requested"""
        if observed.permit_id != proposed.permit_id or observed.inspection_type.casefold() != proposed.inspection_type.casefold():
            return False
        if action is not None:
            if action.record_key:
                if not observed.record_key or observed.record_key != action.record_key:
                    return False
            if action.existing_inspection_id and observed.inspection_id != action.existing_inspection_id:
                return False
        if kind in {"cancel", "cancel_inspection"}:
            return observed.is_cancelled
        return observed.is_scheduled and observed.scheduled_date == proposed.scheduled_date

    def _success(self, action, before, after, confirmation, steps, *, submitted=True):
        result = InspectionActionResult(True, action.action_type, after.inspection_type, after.scheduled_date,
                                        before.scheduled_date, confirmation or after.confirmation_number, True, None,
                                        ActionVerificationState.VERIFIED_SUCCESS, None, before, after)
        if submitted:
            self._record_verification(success=True, verified=True)
        self._audit(action, before, after, steps, "success", after)
        return result

    def _record_submission(self) -> None:
        """record that a mutation reached the portal (metrics targets, if attached)"""
        if self.metrics is None:
            return
        self.metrics.record_submission(environment=self.policy_engine.environment)

    def _record_verification(self, *, success: bool, verified: bool) -> None:
        if self.metrics is not None:
            self.metrics.record_verification(success=success, verified=verified)

    def _failure(self, action, code, message, *, before=None, after=None,
                 verification=ActionVerificationState.VERIFIED_FAILURE,
                 steps=(), response=_UNSET, proposed=None, alternatives=()):
        """one failed action records exactly one audit entry (never two)"""
        result = InspectionActionResult(False, action.action_type, action.inspection_type,
                                        after.scheduled_date if after else None,
                                        before.scheduled_date if before else None, verified=False,
                                        error=message, verification_state=verification, error_code=code,
                                        before=before, after=after, alternatives=tuple(alternatives))
        if before:
            self._audit(action, before, proposed, tuple(steps),
                        message if response is _UNSET else response, after)
        return result

    def _audit(self, action, before, proposed, steps, response, after):
        self.audits.append(MutationAudit(action.permit_id, action.inspection_type, action.action_type,
                                         before, proposed, tuple(steps), response, after,
                                         action.record_key, action.snapshot_id, action.evidence_ids,
                                         action.requires_confirmation))
