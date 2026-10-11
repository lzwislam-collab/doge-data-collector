#!/usr/bin/env python3
"""Independent Stage 5 DOGE direction accuracy audit. No trading rules or signal promises.

Historical fixed-phase research is never a prospective accuracy claim.
All registration/quote/score times must be timezone-aware UTC.
"""
import csv
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / 'data/stage5/latest.json'
MODELS = ('expanding_majority', 'doge_momentum', 'doge_reversal', 'btc_momentum')
SHADOW = ('majority', 'doge_reversal', 'btc_momentum')
HOURS = {1, 24, 168, 720}
MIN_INDEPENDENT_SCORES = 100
REQUIRED_RESOLVED_COVERAGE = 0.90
TARGET_ACCURACY = 0.70


def utc(value):
    dt = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    if dt.tzinfo is None or dt.utcoffset() != timedelta(0):
        raise ValueError('timestamp must have UTC offset')
    return dt.astimezone(timezone.utc)


def wilson_lower(correct, n):
    """Two-sided Wilson 95% lower confidence limit, not an IID guarantee."""
    if not isinstance(n, int) or not isinstance(correct, int) or n < 1 or not 0 <= correct <= n:
        raise ValueError('invalid binomial count')
    z = 1.95996398454
    p = correct / n
    z2 = z*z
    return max(0.0, (p + z2/(2*n) - z*math.sqrt(p*(1-p)/n + z2/(4*n*n))) / (1 + z2/n))


def read_csv(path, fields):
    if not path.exists():
        raise ValueError('missing required file: ' + path.as_posix())
    with path.open(newline='', encoding='utf-8-sig') as stream:
        reader = csv.DictReader(stream)
        if not set(fields).issubset(reader.fieldnames or []):
            raise ValueError('missing CSV columns: ' + path.as_posix())
        return list(reader)


def jsonfile(root, name):
    path = root / name
    data = json.loads(path.read_text(encoding='utf-8'))
    if data.get('status') not in ('ok', 'partial_backfill'):
        raise ValueError('nonhealthy source ' + name + ' status=' + str(data.get('status')))
    return data


def audited_fixed_phase(direction):
    if direction.get('validated_accuracy_70pct') is not False:
        raise ValueError('historical report inaccurately claims 70% validation')
    results = []
    for horizon in ('1h', '24h', '7d', '30d'):
        frame = direction['horizons'][horizon]['fixed_phase_nonoverlap']
        n = int(frame['n'])
        if n <= 0:
            raise ValueError('empty fixed-phase holdout: ' + horizon)
        for name in MODELS:
            entry = frame[name]
            correct = int(entry['correct'])
            if round(100*correct/n, 2) != float(entry['accuracy_pct']):
                raise ValueError('inconsistent historical accuracy: ' + horizon + '/' + name)
            lower = wilson_lower(correct, n)
            results.append({
                'horizon': horizon, 'model': name, 'n_nonoverlap': n,
                'correct': correct, 'accuracy_pct': round(100*correct/n, 2),
                'wilson95_lower_pct_descriptive_only': round(100*lower, 2),
                'historical_selection_bias': True,
                'may_claim_prospective_70pct': False
            })
    best = sorted(results, key=lambda q: (-q['accuracy_pct'], q['n_nonoverlap']))[0]
    return {'fixed_phase_candidates': results, 'highest_observed_accuracy': best,
            'not_pristine_holdout': True,
            'fixed_phase_nonoverlap_not_guaranteed_independent': True,
            'candidate_selection_does_not_establish_predictive_edge': True}


def inspect_forward(root):
    p = root / 'data/direction_v42'
    registered = read_csv(p/'forward_registry.csv',
        ('forecast_id','model_id','issued_at_utc','source_quote_utc','reference_price',
         'horizon_hours','target_utc','majority_direction','doge_reversal_direction','btc_momentum_direction'))
    scored = read_csv(p/'forward_scores.csv',
        ('forecast_id','status','observed_at_utc','target_quote_utc','target_price','realized_up',
         'majority_correct','doge_reversal_correct','btc_momentum_correct'))
    reg = {}
    for row in registered:
        fid = row['forecast_id']
        if not fid or fid in reg:
            raise ValueError('duplicate/empty forward forecast ID')
        issued = utc(row['issued_at_utc'])
        source_quote = utc(row['source_quote_utc'])
        target = utc(row['target_utc'])
        h = int(row['horizon_hours'])
        if h not in HOURS or target != issued + timedelta(hours=h):
            raise ValueError('target horizon mismatch: '+fid)
        if not source_quote <= issued <= source_quote + timedelta(seconds=120):
            raise ValueError('source quote age invalid: '+fid)
        reference = float(row['reference_price'])
        if not math.isfinite(reference) or reference <= 0:
            raise ValueError('invalid anchor quote')
        if not all(row[name] in ('UP','DOWN') for name in
                   ('majority_direction','doge_reversal_direction','btc_momentum_direction')):
            raise ValueError('nonbinary direction: '+fid)
        reg[fid] = row
    scores = {}
    for row in scored:
        fid = row['forecast_id']
        if fid in scores or fid not in reg:
            raise ValueError('duplicate or orphan scored forecast ID')
        scores[fid] = row
        status = row['status']
        if status == 'scored':
            issued_target = utc(reg[fid]['target_utc'])
            observed_quote = utc(row['target_quote_utc'])
            observed_at = utc(row['observed_at_utc'])
            if not issued_target <= observed_quote <= issued_target + timedelta(minutes=25):
                raise ValueError('target quote outside prospective scoring window')
            if observed_at < observed_quote:
                raise ValueError('scoring before source quote timestamp')
            price = float(row['target_price'])
            if not math.isfinite(price) or price <= 0:
                raise ValueError('invalid target quote price')
            expected = 'UP' if price > float(reg[fid]['reference_price']) else 'DOWN'
            if row['realized_up'] != expected:
                raise ValueError('truth label inconsistent with recorded quotes')
            for model in SHADOW:
                col = model + '_direction'
                result_col = model + '_correct'
                if row[result_col] != ('1' if reg[fid][col] == expected else '0'):
                    raise ValueError('score flag inconsistent with recorded direction')
        elif status == 'unscorable_missing_target_quote':
            utc(row['observed_at_utc'])
            if any(row.get(key) for key in
                   ('target_quote_utc','target_price','realized_up','majority_correct',
                    'doge_reversal_correct','btc_momentum_correct')):
                raise ValueError('unscorable forecast has fabricated outcome')
        else:
            raise ValueError('unknown forward scoring status ' + str(status))
    report = {}
    for h in sorted(HOURS):
        relevant = [fid for fid,row in reg.items() if int(row['horizon_hours']) == h]
        resolved = [fid for fid in relevant if fid in scores]
        good = [fid for fid in resolved if scores[fid]['status'] == 'scored']
        lost = len(resolved)-len(good)
        coverage = len(good)/len(resolved) if resolved else None
        subset = []
        last_issued = None
        for fid in sorted(good, key=lambda f: utc(reg[f]['issued_at_utc'])):
            instant = utc(reg[fid]['issued_at_utc'])
            if last_issued is None or instant-last_issued >= timedelta(hours=h):
                subset.append(fid)
                last_issued = instant
        model_scores = {}
        for model in SHADOW:
            field = model+'_correct'
            k = sum(scores[fid][field] == '1' for fid in subset)
            n = len(subset)
            model_scores[model] = {
                'nonoverlap_n': n, 'correct': k,
                'accuracy_pct': round(100*k/n,2) if n else None,
                'wilson95_lower_pct_descriptive_only': round(100*wilson_lower(k,n),2) if n else None,
                'independent_minimum_100_met': n >= MIN_INDEPENDENT_SCORES,
                '70pct_lower_bound_met': bool(n and wilson_lower(k,n) >= TARGET_ACCURACY),
                'model_precommitted_as_challenger': False,
                'eligible_70pct_claim': False
            }
        report[str(h)+'h'] = {
            'registered':len(relevant),'resolved':len(resolved),'scored':len(good),
            'unscorable':lost,'pending':len(relevant)-len(resolved),
            'resolved_quote_coverage_pct':round(100*coverage,2) if coverage is not None else None,
            'required_coverage_pct':round(REQUIRED_RESOLVED_COVERAGE*100),
            'model_results':model_scores
        }
    return {'registered_total':len(reg),'resolved_total':len(scores),
      'scored_total':sum(s['status']=='scored' for s in scores.values()),
      'unscorable_total':sum(s['status']!='scored' for s in scores.values()),
      'pending_total':len(reg)-len(scores),
      'models_precommitted':False,'horizons':report,
      'all_target_quote_timestamps_validated':True,
      'not_external_tamper_proof':True}


def evaluate(root=ROOT):
    direction=jsonfile(root,'data/direction_v42/latest.json')
    validation=jsonfile(root,'data/validation/latest.json')
    stat=jsonfile(root,'data/stat_audit/latest.json')
    probability=jsonfile(root,'data/prob_baseline/latest.json')
    history=jsonfile(root,'data/history/latest.json')
    hourly=jsonfile(root,'data/hourly_research/latest.json')
    daily_score=jsonfile(root,'data/prospective/scores.json')
    direction_forward=jsonfile(root,'data/direction_v42/forward_latest.json')
    if validation['missing_internal_days'] or validation['duplicate_keys']:
        raise ValueError('daily candles contain gaps or duplicates')
    if history['quality']['duplicate_keys'] or any(
        x['missing_days_between_endpoints'] for x in history['quality']['symbols'].values()):
        raise ValueError('historical price series has gaps/duplicates')
    if hourly['quality']['duplicate_keys'] or any(
        x['missing_hours_between_endpoints'] for x in hourly['quality']['symbols'].values()):
        raise ValueError('hourly price series has gaps/duplicates')
    if any(not direction_forward.get(key) is False for key in ('validated_70pct',)):
        raise ValueError('shadow report prematurely claims 70%')
    historical=audited_fixed_phase(direction)
    forward=inspect_forward(root)
    daily_reg=read_csv(root/'data/prospective/registry.csv',('forecast_id','registered_utc','anchor_date_utc','target_date_utc','horizon_days'))
    ids={r['forecast_id'] for r in daily_reg}
    if len(ids)!=len(daily_reg) or len(ids)!=daily_score['registered']:
        raise ValueError('daily prospective registry count or unique IDs invalid')
    daily_bad=daily_score.get('quarantined',[])
    if daily_score['scored']+daily_score['pending_count']+daily_score['quarantined_count'] != len(ids):
        raise ValueError('daily scoring categories do not reconcile')
    if any(row['forecast_id'] not in ids for row in daily_bad):
        raise ValueError('daily quarantined ID missing from registry')
    for row in daily_reg:
        anchor=datetime.fromisoformat(row['anchor_date_utc']).date()
        target=datetime.fromisoformat(row['target_date_utc']).date()
        if anchor+timedelta(days=int(row['horizon_days']))!=target:
            raise ValueError('daily registry target date mismatch')
        utc(row['registered_utc'])
    dates={'validation':validation['end_utc'],'statistical':stat['last_date_utc'],
           'probability_baseline':probability['data_end_utc'],
           'daily_history':history['quality']['symbols']['DOGE-USDT']['last_utc']}
    aligned=len(set(dates.values()))==1 and len({
        validation['doge_days'],stat['aligned_days'],probability['aligned_days'],
        history['quality']['symbols']['DOGE-USDT']['days']})==1
    # Never promote historically selected candidate results to verified 70% accuracy.
    return {'schema_version':1,'status':'ok',
     'generated_at_utc':datetime.now(timezone.utc).isoformat(),
     'target_accuracy_pct':70,'historical':historical,
     'forward_live':forward,
     'daily_probability_forward':{'registered_total':len(ids),
       'scored_total':daily_score['scored'],'pending_total':daily_score['pending_count'],
       'quarantined_total':daily_score['quarantined_count'],
       'quarantined_reasons':daily_bad,
       'not_an_accurate_directional_model_claim':True},
     'comparability':{'dates':dates,'data_end_and_row_counts_aligned':aligned,
        'status':'aligned' if aligned else 'BLOCKED_mismatched_source_end_dates',
        'matched_day_backtest_recomputed':False},
     'thresholds':{'min_nonoverlapping_prospective_scored_predictions_per_horizon':MIN_INDEPENDENT_SCORES,
       'minimum_resolved_quote_coverage_pct':90,
       'minimum_wilson95_lower_bound_pct':70,
       'requires_precommitted_fixed_challenger_and_locked_evaluation':True,
       'requires_baseline_comparison_and_multi_regime_replication':True},
     'prospective_70pct_validated':False,
     'historical_70pct_validated':False,
     'directional_prediction_edge_proven':False,
     'claim_permission':'NO_VALIDATED_EDGE',
     'no_DCA_rule_change':True,
     'limitations':['Historical holdouts already used to select model variants, so exploratory only',
      'Wilson interval is descriptive and may understate serial dependence and model selection',
      'A single strong 30-day phase from 20 observations is not a validated 70% model',
      'Quoted-price prospective checks are issuance-to-target rather than closed-bar historical returns',
      'No model meets prospective sample, baseline, preregistration or quote-coverage criteria']}


def main():
    OUTPUT.parent.mkdir(parents=True,exist_ok=True)
    try:
        result=evaluate()
    except Exception as exc:
        result={'schema_version':1,'status':'blocked',
          'generated_at_utc':datetime.now(timezone.utc).isoformat(),
          'claim_permission':'NO_VALIDATED_EDGE','prospective_70pct_validated':False,
          'error':type(exc).__name__+': '+str(exc)[:400]}
    tmp=OUTPUT.with_suffix('.json.tmp')
    tmp.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    tmp.replace(OUTPUT)
    print(json.dumps({'status':result['status'],'claim':result['claim_permission'],
                      'error':result.get('error')},ensure_ascii=False),flush=True)
    return 0 if result['status']=='ok' else 1


if __name__=='__main__':
    raise SystemExit(main())
