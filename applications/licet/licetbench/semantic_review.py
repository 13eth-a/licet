"""evidence bound secondary semantic review; never overrides deterministic failure"""
from dataclasses import dataclass, replace
from licetbench.provenance import digest


@dataclass(frozen=True)
class SemanticReview:
    verdict: str
    answer_digest: str
    ground_truth_digest: str
    reviewer: str
    rationale: str
    evidence_ids: tuple[str,...]


def apply_review(result, review, *, answer, ground_truth):
    if review.verdict not in {'ACCEPT','REJECT','NEEDS_REVIEW'}:
        raise ValueError('unknown semantic verdict')
    if review.answer_digest != digest(answer) or review.ground_truth_digest != digest(ground_truth):
        raise ValueError('semantic review is stale or addresses different evidence')
    if not review.reviewer or not review.rationale or not review.evidence_ids:
        raise ValueError('semantic review requires attribution, rationale and evidence')
    if not set(review.evidence_ids) <= set(ground_truth['evidence']):
        raise ValueError('semantic review cites unknown evidence')
    details={**result.details,'semantic_review':review.__dict__,
             'semantic_review_secondary':True}
    if review.verdict=='ACCEPT' or not result.success:
        return replace(result,details=details)  # an accept cannot promote a failed hard grade
    return replace(result,success=False,expectation_met=False,outcome='FAILURE',
                   failure_type='reasoning failure' if review.verdict=='REJECT' else 'semantic review required',details=details)
