"""Planner recovery bridge. Only closed read capabilities may be retried."""
from copy import deepcopy
from dataclasses import replace
from licet.phase5.state import Action, READS, Observation, established
from licet.phase7.portal import PortalState, route_recovery
from licet.phase7.recovery import Failure, FailureType, PageFingerprint
from licet.phase7.semantic import SemanticContext, RecoveryChoice, decide_semantic_recovery

SAFE_READS = frozenset(READS) | {Action.FIND_PERMIT, Action.CHECK_INSPECTION_AVAILABILITY,
    Action.VERIFY_STATE, Action.DETERMINE_BLOCKERS, Action.DETERMINE_NEXT_INSPECTION}


def context_for(run, controller, **overrides):
    fields = dict(identity_verified=run.world.permit_verified,
        verified_findings=tuple(established(run.world, run.goal) - {'permit_verified'}),
        permit_status=getattr(run.world.permit, 'status_normalized', None),
        replan_remaining=controller.budgets.max_replans-controller.stats.replans,
        read_retry_remaining=controller.budgets.max_recovery_actions-controller.stats.recovery_actions)
    fields.update(overrides)
    return SemanticContext(**fields)


def fingerprint(world):
    return PageFingerprint(url=world.browser_state.get('url'), record_number=world.record_key,
        active_section=world.browser_state.get('active_section'), important_visible_text=world.fingerprint())


async def recover_read(planner, run, action, observation):
    controller = planner.recovery
    if action not in SAFE_READS:
        return observation
    route = route_recovery(PortalState.from_observation(observation.world.browser_state))
    if route:
        planner._trace(run, {'event': 'PORTAL_RECOVERY_ROUTE', 'route': route.as_dict()})
    if route and route.terminal:
        return replace(observation, retryable=False, message=route.reason)
    failure = controller.classify(observation.message, operation=action.value)
    if route:
        failure = Failure(route.failure_type, route.reason, action.value, recoverable=True)
    if not failure.recoverable or not (observation.retryable or route):
        return observation
    # Discovery precedes a verified identity; its own result must establish it.
    decision = decide_semantic_recovery(context_for(run, controller,
        identity_verified=run.world.permit_verified or action == Action.FIND_PERMIT,
        transient_read_failure=True))
    planner._trace(run, {'event': 'SEMANTIC_RECOVERY', 'decision': decision.choice.value,
                         'reason': decision.reason, 'action': action.value})
    if decision.choice != RecoveryChoice.RETRY:
        return replace(observation, retryable=False, message=decision.reason)
    checkpoint = deepcopy(run.world)
    strategy = route.strategy if route else 'REOBSERVE_AND_READ'
    candidate = None
    async def attempt():
        nonlocal candidate
        world = deepcopy(checkpoint)
        # Route implementations must finish by re-reading through the ordinary
        # capability. They cannot grant permission, submit or invent identity.
        counter = getattr(planner.capabilities, 'browser_action_count', lambda: 0)
        before_actions = counter()
        try:
            if route:
                handler = getattr(planner.capabilities, 'recover_read', None)
                if handler is None:
                    raise RuntimeError('no validated portal recovery adapter for ' + strategy)
                candidate = await handler(strategy, action, run.goal, world)
            else:
                candidate = await planner.capabilities.perform(action, run.goal, world)
        finally:
            controller.stats.additional_browser_actions += max(0, counter() - before_actions)
        return candidate
    def validate(value):
        if not isinstance(value, Observation) or not value.success:
            return False
        w = value.world
        if not w.permit_verified or not w.record_key:
            return False
        if checkpoint.record_key and w.record_key != checkpoint.record_key:
            return False
        if action in READS and w.permit is None:
            return False
        if w.permit and w.permit.record_key != w.record_key:
            return False
        if route_recovery(PortalState.from_observation(w.browser_state)):
            return False
        if action == Action.VERIFY_STATE and not w.verified_inspection:
            return False
        return True
    result = await controller.recover(failure, strategy, attempt, validate=validate)
    planner._trace(run, {'event': 'RECOVERY', 'action': action.value, **result.as_dict()})
    if result.recovered:
        controller.validate_checkpoint('verified_state', fingerprint(candidate.world))
        return candidate
    return replace(observation, retryable=False, message="recovery budget exhausted: " + (result.error or observation.message))
