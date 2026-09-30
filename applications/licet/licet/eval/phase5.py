"""Measured planner outcomes; unknown accuracy metrics remain unmeasured."""
from collections import Counter
from licet.phase5.state import Action, Error, MUTATIONS, READS, Status


def planner_metrics(runs, *, expected_actions=None, necessary_reads=None):
    """Optional golden paths support next-step and unnecessary-read scoring.

    A zero constraint-violation count only covers restrictions inspectable from
    these traces. It is not a production guarantee or a browser-mutation audit.
    """
    total = len(runs)
    if any(labels is not None and len(labels) != total for labels in (expected_actions, necessary_reads)):
        raise ValueError("golden labels must cover every evaluated run")
    traces = [entry for run in runs for entry in run.trace if "step" in entry]
    actions = [entry["action"] for entry in traces]
    mutations = [entry for entry in traces if entry["action"] in {a.value for a in MUTATIONS}]
    duplicate = sum(sum(max(0, n - 1) for n in Counter(
        e.get("mutation_key") for e in run.trace if e.get("mutation_key")).values()) for run in runs)
    successful = sum(run.status == Status.SUCCESS for run in runs)
    efficiency = sum(run.useful_steps for run in runs) / len(traces) if traces else 0
    replanned = [run for run in runs if run.failed_steps and len(run.plans) > 1]
    measured_next = []
    if expected_actions is not None:
        for run, expected in zip(runs, expected_actions):
            actual = [e["action"] for e in run.trace if "step" in e]
            measured_next.extend(a == b for a, b in zip(actual, expected))
            measured_next.extend(False for _ in range(abs(len(actual) - len(expected))))
    unnecessary = None
    if necessary_reads is not None:
        unnecessary = sum(e["action"] in {a.value for a in READS} and e["action"] not in required
                          for run, required in zip(runs, necessary_reads) for e in run.trace)
    violations = sum(not e.get("policy_checked", False) for e in mutations)
    return {"runs": total, "goal_completion_rate": successful / total if total else 0,
        "partial_completion_rate": sum(r.status == Status.PARTIAL_SUCCESS for r in runs) / total if total else 0,
        "correct_next_step_rate": sum(measured_next) / len(measured_next) if measured_next else None,
        "replanning_success_rate": sum(r.status == Status.SUCCESS for r in replanned) / len(replanned) if replanned else None,
        "constraint_violation_rate": violations / len(mutations) if mutations else None,
        "duplicate_action_rate": duplicate / len(mutations) if mutations else 0,
        "planner_loop_rate": sum(r.error == Error.PLAN_LOOP_DETECTED for r in runs) / total if total else 0,
        "average_semantic_steps": len(traces) / total if total else 0,
        "efficiency": efficiency, "unnecessary_reads": unnecessary,
        "semantic_actions": dict(Counter(actions)), "mutations_attempted": len(mutations)}
