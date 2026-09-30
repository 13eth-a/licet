from dataclasses import replace
import hashlib
import json
from pathlib import Path
import pytest
from licetbench.catalog import build_tasks
from licetbench.grading import grade_task, _result
from licetbench.provenance import digest
from licetbench.runner import run_tasks, build_report
from licetbench.semantic_review import SemanticReview, apply_review
from licetbench.comparison import compare_reports


def test_frozen_core_does_not_follow_changed_production_fixture(monkeypatch):
    import licetbench.catalog as catalog
    before=[t.as_dict() for t in build_tasks()]
    monkeypatch.setattr(catalog,'phase3_cases',lambda: [], raising=False)
    assert [t.as_dict() for t in build_tasks()] == before
    assert digest(before) == digest(json.loads(Path('licetbench/core-v1.json').read_text()))


def test_extra_golden_field_cannot_be_silently_ungraded():
    t=next(t for t in build_tasks() if t.source=='understanding')
    result=grade_task(replace(t,expected_outcome={**t.expected_outcome,'invented_amount':999}))
    assert not result.expectation_met and result.details['benchmark_integrity']


def test_action_grader_uses_its_own_portal_read(monkeypatch):
    import licetbench.grading as grading
    original=grading.run_case
    def dishonest(case):
        result,portal=original(case)
        portal.after=case.before  # returned executor claims success, backend does not
        return result,portal
    monkeypatch.setattr(grading,'run_case',dishonest)
    t=next(t for t in build_tasks() if t.id=='ACTION-001')
    result=grade_task(t)
    assert not result.success and not result.expectation_met
    assert result.false_verified_successes == 1


def test_expected_partial_does_not_award_partial_credit_to_mismatch():
    t=build_tasks()[0]
    result=_result(t,passed=False,safe=True,verified=True,final_state={},partial_success=True)
    assert not result.partial_success


def test_review_cannot_promote_failure_and_requires_bound_evidence():
    t=build_tasks()[0]
    failed=_result(t,passed=False,safe=True,verified=True,final_state={})
    truth={'evidence':{'e1':'unpaid balance; no gate evidence'}}
    answer='An unpaid balance is present; its effect on scheduling is not established.'
    review=SemanticReview('ACCEPT',digest(answer),digest(truth),'architecture review','Separates fact from gate inference',('e1',))
    assert not apply_review(failed,review,answer=answer,ground_truth=truth).success
    with pytest.raises(ValueError,match='stale'):
        apply_review(failed,review,answer='Must pay before scheduling',ground_truth=truth)
    with pytest.raises(ValueError,match='unknown evidence'):
        apply_review(failed,replace(review,evidence_ids=('invented',)),answer=answer,ground_truth=truth)


def test_pending_or_rejected_semantic_review_cannot_leave_success():
    t=build_tasks()[0]
    result=_result(t,passed=True,safe=True,verified=True,final_state={})
    truth={'evidence':{'e1':'unknown'}}
    for verdict in ('REJECT','NEEDS_REVIEW'):
        review=SemanticReview(verdict,digest('claim'),digest(truth),'architecture review','Unsupported certainty',('e1',))
        assert not apply_review(result,review,answer='claim',ground_truth=truth).success


def test_prompt_is_executed_and_not_replaced_by_expected_intent():
    from licetbench.prompts import build_prompt_tasks
    task=build_prompt_tasks()[0]
    assert grade_task(task).expectation_met
    result=grade_task(replace(task,prompt='Read only: inspect permit P-1.'))
    assert not result.expectation_met
    assert result.details['parsed_goal']['autonomous'] is False


def test_prompt_suite_keeps_every_reviewed_golden_intact():
    from licetbench.runner import select_tasks
    from licetbench.schema import Outcome
    tasks = select_tasks(suite="prompts")
    assert len(tasks) == 22
    results = run_tasks(tasks)
    assert all(result.expectation_met for result in results)
    by_id = {result.task_id: result for result in results}
    # the four language handling failures fixed after the architecture review review, plus the ambiguity
    # case that must stay a safe stop (goldens were never weakened)
    for task_id in ("PROMPT-003", "PROMPT-006",
                    "PROMPT-DISCOVERY-002-P029", "PROMPT-DISCOVERY-004-P032"):
        assert by_id[task_id].success and by_id[task_id].safe
    assert by_id["PROMPT-DISCOVERY-005-P036"].outcome == Outcome.SAFE_FAILURE.value


def test_duplicate_ids_and_per_task_repeats():
    task=build_tasks()[0]
    with pytest.raises(ValueError,match='duplicate'):
        run_tasks([task,task])
    assert len(run_tasks([replace(task,repeat=2)],repeats=2)) == 4


def test_provenance_and_comparison_reject_fake_model_experiment():
    tasks=[build_tasks()[0]]
    report=build_report(tasks,run_tasks(tasks),seed=0,repeats=1,model='architecture review',config='labels-only')
    assert report['source_digest'] and report['task_digest'] and report['created_at']
    assert compare_reports(report,report)['pairs'] == 1
    with pytest.raises(ValueError,match='model labels'):
        compare_reports(report,report,model_comparison=True)
    changed={**report,'task_manifest':[{**report['task_manifest'][0],'prompt':'changed'}]}
    with pytest.raises(ValueError,match='golden'):
        compare_reports(report,changed)


def test_working_tree_digest_tracks_uncommitted_bytes(tmp_path):
    """the run identity is the tree on disk, not head (audit a9)"""
    from licetbench.provenance import provenance
    for directory in ("licet", "licetbench"):
        (tmp_path / directory).mkdir()
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\n')
    source = tmp_path / "licetbench" / "catalogue.py"
    source.write_text("tasks = []\n")
    before = provenance([], root=tmp_path)
    assert "licetbench/catalogue.py" in before["source_files"]
    assert "pyproject.toml" in before["source_files"]
    assert before["source_digest"] == digest(before["source_files"])
    source.write_text("tasks = [1]\n")  # uncommitted edit; head does not move
    after = provenance([], root=tmp_path)
    assert after["source_digest"] != before["source_digest"]
    assert after["source_files"]["licetbench/catalogue.py"] == hashlib.sha256(
        b"tasks = [1]\n").hexdigest()


def test_detected_wrong_record_before_submission_is_a_safe_stop():
    from licetbench.variants import build_action_variant_tasks
    task=next(t for t in build_action_variant_tasks() if t.id=='ACTION-V014')
    result=grade_task(task)
    assert result.expectation_met and result.safe
    assert not result.success and result.mutation_submissions == 0


def test_nan_cost_cannot_enter_metrics():
    with pytest.raises(ValueError):
        replace(grade_task(build_tasks()[0]), estimated_cost=float('nan'))


def test_complete_fixture_contract_is_frozen():
    from licetbench.contracts import frozen_contract_matches
    assert all(frozen_contract_matches(t) for t in build_tasks())
