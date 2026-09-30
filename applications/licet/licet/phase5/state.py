"""run local semantic planner contracts"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
import hashlib
import json
import uuid
from typing import Any

from licet.phase3.state import PermitState, ReasoningResult
from licet.phase4.actions import InspectionAction, InspectionActionResult, InspectionSnapshot
from licet.phase4.selection import ActionSelection
from licet.safety.policy import ConfirmationRequest


class Action(str, Enum):
    FIND_PERMIT = "FIND_PERMIT"
    READ_PERMIT_STATE = "READ_PERMIT_STATE"
    READ_INSPECTIONS = "READ_INSPECTIONS"
    READ_FEES = "READ_FEES"
    READ_DOCUMENTS = "READ_DOCUMENTS"
    READ_CONDITIONS = "READ_CONDITIONS"
    READ_HISTORY = "READ_HISTORY"
    DETERMINE_BLOCKERS = "DETERMINE_BLOCKERS"
    DETERMINE_NEXT_INSPECTION = "DETERMINE_NEXT_INSPECTION"
    CHECK_INSPECTION_AVAILABILITY = "CHECK_INSPECTION_AVAILABILITY"
    SCHEDULE_INSPECTION = "SCHEDULE_INSPECTION"
    RESCHEDULE_INSPECTION = "RESCHEDULE_INSPECTION"
    CANCEL_INSPECTION = "CANCEL_INSPECTION"
    VERIFY_STATE = "VERIFY_STATE"
    REQUEST_USER_APPROVAL = "REQUEST_USER_APPROVAL"
    STOP = "STOP"


MUTATIONS = frozenset({Action.SCHEDULE_INSPECTION, Action.RESCHEDULE_INSPECTION, Action.CANCEL_INSPECTION})
READS = {Action.READ_PERMIT_STATE: "overview", Action.READ_INSPECTIONS: "inspections",
         Action.READ_FEES: "fees", Action.READ_DOCUMENTS: "documents",
         Action.READ_CONDITIONS: "conditions", Action.READ_HISTORY: "history"}


class Status(str, Enum):
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    PARTIAL_SUCCESS = "PARTIAL_SUCCESS"
    BLOCKED = "BLOCKED"
    NEEDS_APPROVAL = "NEEDS_APPROVAL"
    FAILED = "FAILED"


class Error(str, Enum):
    NO_VALID_PLAN = "NO_VALID_PLAN"
    PLAN_LOOP_DETECTED = "PLAN_LOOP_DETECTED"
    STEP_BUDGET_EXCEEDED = "STEP_BUDGET_EXCEEDED"
    PRECONDITION_NOT_MET = "PRECONDITION_NOT_MET"
    GOAL_UNSATISFIABLE = "GOAL_UNSATISFIABLE"
    EXTERNAL_DEPENDENCY = "EXTERNAL_DEPENDENCY"
    NO_SAFE_ACTIONS = "NO_SAFE_ACTIONS"
    REPLAN_FAILED = "REPLAN_FAILED"


@dataclass(frozen=True)
class Goal:
    objective: str
    permit_id: str | None = None
    lookup_text: str | None = None
    constraints: tuple[str, ...] = ()
    success_conditions: tuple[str, ...] = ("permit_verified", "blockers_identified")
    prohibited_actions: tuple[str, ...] = ("payment", "signature", "application_submission")
    autonomous: bool = False
    inspection_type: str | None = None
    operation: str = "schedule"
    existing_inspection_id: str | None = None
    date_instruction: str | None = None
    date_window_start: str | None = None
    date_window_end: str | None = None
    preferred_date: str | None = None
    clarification: str | None = None
    vague: bool = False

    def __post_init__(self):
        for name in ("constraints", "success_conditions", "prohibited_actions"):
            values = tuple(getattr(self, name))
            if not all(isinstance(value, str) for value in values):
                raise ValueError(f"{name} must contain immutable strings")
            object.__setattr__(self, name, values)
        known = {"permit_verified", "blockers_identified", "next_inspection_identified",
                 "availability_checked", "inspection_scheduled", "inspection_rescheduled",
                 "inspection_cancelled", "permit_approved"}
        if not self.success_conditions or not set(self.success_conditions) <= known:
            raise ValueError("goal needs supported, observable success conditions")
        if self.operation not in {"schedule", "reschedule", "cancel"}:
            raise ValueError("unsupported goal operation")


@dataclass(frozen=True)
class ExternalDependency:
    type: str
    description: str
    evidence_ids: tuple[str, ...] = ()
    can_licet_resolve: bool = False


@dataclass
class World:
    permit_id: str | None = None
    record_key: str | None = None
    snapshot_id: str = ""
    permit_verified: bool = False
    permit: PermitState | None = None
    reasoning: ReasoningResult | None = None
    selection: ActionSelection | None = None
    proposal: InspectionAction | None = None
    available_dates: tuple[str, ...] = ()
    availability_checked: bool = False
    eligibility_verified: bool = False
    preflight_fingerprint: str | None = None
    cost: float | None = None
    signature_required: bool | None = None
    preflight_details: dict[str, Any] = field(default_factory=dict)
    result: InspectionActionResult | None = None
    verified_inspection: InspectionSnapshot | None = None
    dependencies: tuple[ExternalDependency, ...] = ()
    missing_information: tuple[str, ...] = ()
    browser_state: dict[str, Any] = field(default_factory=dict)

    def fingerprint(self) -> str:
        # run metadata and timestamps never count as progress
        permit = self.permit.to_dict() if self.permit else None
        if permit:
            permit.pop("evidence", None)
        def semantic(value):
            if isinstance(value, dict):
                return {k: semantic(v) for k, v in value.items() if k not in {"snapshot_id", "evidence_ids", "observed_at"}}
            if isinstance(value, (list, tuple)):
                return [semantic(v) for v in value]
            return value
        return digest(semantic({"key": self.record_key, "verified": self.permit_verified, "permit": permit,
                       "reasoning": self.reasoning.to_dict() if self.reasoning else None,
                       "selection": asdict(self.selection) if self.selection else None,
                       "proposal": self.proposal.as_dict() if self.proposal else None,
                       "dates": self.available_dates, "checked": self.availability_checked,
                       "eligible": self.eligibility_verified, "cost": self.cost,
                       "signature": self.signature_required,
                       "preflight_details": self.preflight_details,
                       "inspection": asdict(self.verified_inspection) if self.verified_inspection else None,
                       "dependencies": [asdict(d) for d in self.dependencies],
                       "missing": self.missing_information}))


@dataclass
class PlanStep:
    id: str
    action: Action
    reason: str
    dependencies: tuple[str, ...] = ()
    status: str = "pending"
    requires_confirmation: bool = False


@dataclass
class Plan:
    goal: Goal
    steps: list[PlanStep]
    status: Status = Status.RUNNING
    revision: int = 0

    def validate(self):
        seen = set()
        for step in self.steps:
            if step.id in seen or not set(step.dependencies) <= seen:
                raise ValueError("duplicate, missing, cyclic, or forward plan dependency")
            seen.add(step.id)
        if len(self.steps) > 8:
            raise ValueError("plan exceeds eight semantic steps")


@dataclass(frozen=True)
class Observation:
    world: World
    success: bool = True
    message: str = ""
    uncertain: bool = False
    retryable: bool = False


@dataclass
class Run:
    goal: Goal
    world: World = field(default_factory=World)
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    status: Status = Status.RUNNING
    error: Error | None = None
    reason: str = ""
    trace: list[dict[str, Any]] = field(default_factory=list)
    plans: list[Plan] = field(default_factory=list)
    completed_steps: list[str] = field(default_factory=list)
    failed_steps: list[str] = field(default_factory=list)
    current_step: str | None = None
    approval_token: str | None = None
    approval_denied: bool = False
    pending_confirmation: ConfirmationRequest | None = None
    attempted_mutations: set[str] = field(default_factory=set)
    verified_mutations: set[str] = field(default_factory=set)
    seen: dict[str, int] = field(default_factory=dict)
    no_progress: int = 0
    semantic_steps: int = 0
    useful_steps: int = 0
    model_fallbacks: int = 0
    recovery_report: dict[str, Any] = field(default_factory=dict)

    def report(self):
        return {"goal": asdict(self.goal), "status": self.status.value,
                "error": self.error.value if self.error else None, "reason": self.reason,
                "remaining_goal": sorted(set(self.goal.success_conditions) - established(self.world, self.goal)),
                "completed_steps": self.completed_steps, "failed_steps": self.failed_steps,
                "approval_token": self.approval_token, "trace": self.trace, "recovery": self.recovery_report,
                "preflight": {
                    "checked": self.world.availability_checked,
                    "record_key": self.world.record_key,
                    "inspection_type": self.world.proposal.inspection_type if self.world.proposal else None,
                    "eligible": self.world.eligibility_verified if self.world.availability_checked else None,
                    "available_dates": list(self.world.available_dates),
                    "cost": self.world.cost,
                    "cost_status": "unknown" if self.world.cost is None else "observed",
                    "signature_required": self.world.signature_required,
                    "signature_status": "unknown" if self.world.signature_required is None else "observed",
                    "details": self.world.preflight_details,
                    "dependencies": [asdict(dependency) for dependency in self.world.dependencies],
                    "missing_information": list(self.world.missing_information),
                },
                "metrics": {"semantic_steps": self.semantic_steps, "useful_steps": self.useful_steps,
                    "plan_revisions": max(0, len(self.plans) - 1), "model_fallbacks": self.model_fallbacks,
                    "mutations_attempted": len(self.attempted_mutations),
                    "mutations_verified": len(self.verified_mutations),
                    "efficiency": self.useful_steps / self.semantic_steps if self.semantic_steps else 0}}


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def operation_key(world: World) -> str:
    return digest({"record_key": world.record_key, "proposal": world.proposal.as_dict() if world.proposal else None})


def reasoning_is_sound(world: World, goal: Goal | None = None) -> bool:
    """true when an interpretation may be treated as settled *for this record*"""
    reasoning = world.reasoning
    if reasoning is None or not world.record_key or reasoning.record_key != world.record_key:
        return False
    if reasoning.snapshot_id != world.snapshot_id or reasoning.answerability != "answered":
        return False
    if reasoning.contradictions or reasoning.needed_sections:
        return False
    if any(uncertainty.blocks_answer for uncertainty in reasoning.uncertainties):
        return False
    if goal is not None and " ".join(reasoning.question.split()).casefold() != " ".join(goal.objective.split()).casefold():
        return False
    return True


def established(world: World, goal: Goal | None = None) -> set[str]:
    """goal predicates that the observed record state independently supports"""
    facts = set()
    if not world.permit_verified or not world.record_key:
        return facts
    facts.add("permit_verified")
    if reasoning_is_sound(world, goal):
        facts.add("blockers_identified")
    proposal = world.proposal
    bound = proposal is not None and proposal.record_key == world.record_key and proposal.snapshot_id == world.snapshot_id
    if bound:
        facts.add("next_inspection_identified")
        availability = world.preflight_details.get("availability", {})
        if (world.availability_checked
                and world.preflight_fingerprint == operation_key(world)
                and availability.get("calendar_read") is True
                and availability.get("identity_verified") is True):
            facts.add("availability_checked")
    observed, action = world.verified_inspection, proposal
    if (bound and observed and observed.permit_id == world.permit_id and observed.record_key == world.record_key
            and observed.inspection_type == action.inspection_type
            and (not action.existing_inspection_id or observed.inspection_id == action.existing_inspection_id)):
        from datetime import date
        within = False
        try:
            day = date.fromisoformat(observed.scheduled_date or "")
            within = (not action.preferred_date or day == date.fromisoformat(action.preferred_date)) and (
                not action.date_window_start or day >= date.fromisoformat(action.date_window_start)) and (
                not action.date_window_end or day <= date.fromisoformat(action.date_window_end))
        except ValueError:
            pass
        if observed.is_scheduled and within:
            facts.add("inspection_scheduled")
            if world.result and world.result.success and world.result.verified and world.result.previous_date != observed.scheduled_date:
                facts.add("inspection_rescheduled")
        if observed.is_cancelled and action.existing_inspection_id and observed.inspection_id == action.existing_inspection_id:
            facts.add("inspection_cancelled")
    if (world.permit and world.permit.record_key == world.record_key
            and world.permit.status_normalized == "APPROVED"
            and not (world.reasoning and world.reasoning.contradictions)):
        facts.add("permit_approved")
    return facts
