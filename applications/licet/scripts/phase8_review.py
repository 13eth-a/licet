"""reproducible offline benchmark audit artifacts; no model or portal calls"""
import asyncio
import json
from pathlib import Path
from dataclasses import replace, asdict
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from licetbench.runner import select_tasks, run_tasks, build_report, write_json, write_csv
from licetbench.comparison import compare_reports
from licetbench.provenance import digest
from licet.eval.phase3_fixtures import build_cases
from licet.eval.phase3 import score_case
from tests.test_phase7_runtime import noisy_run, CASES, KEY

OUT=Path('docs/phase8/final')
_IDENTITY=None


def identity():
    """the git identity of the code being measured, captured exactly once"""
    global _IDENTITY
    if _IDENTITY is None:
        from licetbench.__main__ import _commit, _commit_dirty
        _IDENTITY=(_commit(), _commit_dirty())
    return _IDENTITY


def report(suite,name,repeats=1,max_steps=None):
    tasks=select_tasks(suite=suite)
    if max_steps:
        tasks=[replace(t,max_steps=min(t.max_steps,max_steps)) for t in tasks]
    rows=run_tasks(tasks,repeats=repeats,seed=17,shuffle=True)
    commit,commit_dirty=identity()
    r=build_report(tasks,rows,seed=17,repeats=repeats,model='fixture',config=f'step-budget-{max_steps or 20}',commit=commit,commit_dirty=commit_dirty)
    write_json(r,OUT/(name+'.json'))
    write_csv(rows,OUT/(name+'.csv'))
    return r


async def noisy_comparison():
    cohorts={}
    for cohort in ('normal','noisy'):
        rows=[]
        for i in range(20):
            row=await noisy_run('normal' if cohort=='normal' else CASES[i%len(CASES)],seed=i,validate_outcome=False)
            final=row['portal_final']
            independently_verified=bool(final and final['record_key']==KEY and final['inspection_type']=='Rough Electrical' and final['status']=='Scheduled' and final['scheduled_date']=='2026-09-24')
            row['false_success']=row['status']=='SUCCESS' and not independently_verified
            row['unexpected_outcome']=(row['status']=='SUCCESS')!=row['expected_success']
            rows.append(row)
        attempts=sum(r['recovery']['stats']['recovery_attempts'] for r in rows)
        cohorts[cohort]={'runs':20,'completed':sum(r['status']=='SUCCESS' for r in rows),
            'safe_stops':sum(r['status']!='SUCCESS' and not r['false_success'] and r['submits']<=1 for r in rows),
            'unexpected_outcomes':sum(r['unexpected_outcome'] for r in rows),
            'false_successes':sum(r['false_success'] for r in rows),
            'duplicates':sum(max(0,r['submits']-1) for r in rows),
            'recovery_success_rate':sum(r['recovery']['stats']['recovery_successes'] for r in rows)/attempts if attempts else None,
            'rows':rows}
    return {'scope':'real Phase 2–7 runtime; simulated external I/O; not live reliability','cohorts':cohorts}


if __name__=='__main__':
    OUT.mkdir(parents=True,exist_ok=True)
    core=report('core','core',5)
    variants=report('variants','variants')
    prompts=report('prompts','prompts')
    limited=report('prompts','prompts-step4',max_steps=4)
    # the holdout is deliberately not gated in ci (see docs/phase8.md); this deliberate run is where its
    # verdicts are recorded
    holdout=report('holdout','holdout')
    write_json(compare_reports(limited,prompts),OUT/'configuration-comparison.json')
    noise=asyncio.run(noisy_comparison())
    write_json(noise,OUT/'normal-vs-noisy.json')
    packet=[]
    for case in build_cases()[:10]:
        scored=score_case(case)
        ground={'evidence':case.state.to_dict(), 'expected':{'blocker_types':case.expected_blocker_types,
            'forbidden_blocker_types':case.forbidden_blocker_types,'answerability':case.expected_answerability,
            'classifications':case.expected_classifications,'forbidden_classifications':case.forbidden_classifications}}
        packet.append({'case_id':case.case_id,'question':case.question,'answer':scored['answer'],
            'answer_digest':digest(scored['answer']),'ground_truth_digest':digest(ground),'ground_truth':ground,
            'deterministic_pass':scored['passed']})
    write_json({'scope':'structured ground truth and generated answers for secondary semantic review','cases':packet},OUT/'semantic-packet.json')
    print(json.dumps({'core':{k:core['metrics'][k] for k in ('runs','completed','expected_behavior','unsafe_failures','grader_errors')},
        'prompts':{k:prompts['metrics'][k] for k in ('runs','completed','expected_behavior','grader_errors')},
        'holdout':{k:holdout['metrics'][k] for k in ('runs','completed','expected_behavior','grader_errors')},
        'variants_failures':len(variants['failed_runs']),
        'noise':{k:{a:b for a,b in v.items() if a!='rows'} for k,v in noise['cohorts'].items()}},indent=2))
