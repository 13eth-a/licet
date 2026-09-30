"""paired report comparison with explicit measurement and comparability checks"""
from licetbench.provenance import digest


def compare_reports(left,right,*,model_comparison=False):
    if left['benchmark'] != right['benchmark'] or left['scope'] != right['scope']:
        raise ValueError('incomparable benchmark version or measurement scope')
    def problems(report):
        return digest([{k:v for k,v in t.items() if k not in {'max_steps','repeat','seed'}}
                       for t in report['task_manifest']])
    if problems(left)!=problems(right):
        raise ValueError('different prompts, input states or golden answers')
    if left['seed']!=right['seed']:
        raise ValueError('different experiment seeds')
    def indexed(report):
        rows=report['results']
        index={(r['task_id'],r['repeat_index']):r for r in rows}
        if len(index)!=len(rows):
            raise ValueError('duplicate paired result keys')
        return index
    a,b=indexed(left),indexed(right)
    if not a or a.keys()!=b.keys():
        raise ValueError('incomplete or unpaired runs')
    if model_comparison and (not all(r['model_calls']>0 for r in a.values()) or not all(r['model_calls']>0 for r in b.values())):
        raise ValueError('model labels without measured model calls are not a model experiment')
    wins=sum(b[k]['success'] and not a[k]['success'] for k in a)
    losses=sum(a[k]['success'] and not b[k]['success'] for k in a)
    keys=('task_completion_rate','safe_outcome_rate','average_semantic_steps','average_latency_seconds',
          'average_model_calls','approximate_cost_per_run')
    delta={k:(right['metrics'][k]-left['metrics'][k]) if left['metrics'].get(k) is not None and right['metrics'].get(k) is not None else None for k in keys}
    return {'scope':'measured model experiment' if model_comparison else 'paired offline configuration comparison; no model inference',
            'pairs':len(a),'wins':wins,'losses':losses,'ties':len(a)-wins-losses,
            'delta_right_minus_left':delta,'left_source':left['source_digest'],'right_source':right['source_digest'],
            'unsafe_right':sum(not r['safe'] for r in b.values()),
            'caution':'Paired fixture repetitions are not independent samples of live reliability.'}
