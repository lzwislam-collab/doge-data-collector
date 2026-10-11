#!/usr/bin/env python3
"""Independent Stage 5 DOGE direction accuracy audit. No trading rules or signal promises.

Historical fixed-phase research is never a prospective accuracy claim.
All registration/quote/score times must be timezone-aware UTC.
"""
import csv
import json
import math
import hashlib
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
    if name=='data/direction_v42/forward_latest.json' and data.get('status')=='stale_hour_no_new_registration':
        if (float(data.get('feature_lag_min',0))<=45
            or data.get('new_ids')!=[]
            or data.get('validated_70pct') is not False
            or data.get('claim_permission')!='NO_VALIDATED_EDGE'):
            raise ValueError('unexpected/misleading late forecast skip')
        utc(data['generated_utc'])
        utc(data['quote_at_utc'])
    elif data.get('status') not in ('ok','partial_backfill'):
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


def audit_locked_forward_trial(root):
    """Frozen pre-registered study; select fixed time bins before outcomes are inspected."""
    lock_path=root/'data/stage5/pre_registration.json'
    lock=json.loads(lock_path.read_text(encoding='utf-8'))
    if (lock.get('status')!='frozen_protocol'
        or lock.get('model_id')!='DOGE_DIRECTION_ISSUED_QUOTE_SHADOW_V42_1'
        or lock.get('primary_model')!='doge_reversal'
        or lock.get('comparison_model')!='majority'
        or lock.get('primary_horizons_hours')!=[1,24]
        or int(lock.get('eligible_target_quote_window_minutes',-1))!=25
        or int(lock.get('issue_quote_max_age_seconds',-1))!=120):
        raise ValueError('preregistered forward trial parameters changed')
    effective=utc(lock['effective_from_utc'])
    blob=(root/lock['locked_source_file']).read_bytes()
    actual=hashlib.sha1(b'blob '+str(len(blob)).encode('ascii')+b'\0'+blob).hexdigest()
    if actual!=lock['locked_source_git_blob_sha']:
        raise ValueError('frozen V4.2 source changed after preregistration')
    p=root/'data/direction_v42'
    forecast_rows=read_csv(p/'forward_registry.csv',
        ('forecast_id','model_id','issued_at_utc','horizon_hours','target_utc'))
    score_rows=read_csv(p/'forward_scores.csv',
        ('forecast_id','status','realized_up','majority_correct','doge_reversal_correct'))
    scores={row['forecast_id']:row for row in score_rows}
    result={'protocol_id':lock['protocol_id'],'protocol_frozen':True,
            'effective_from_utc':effective.isoformat(),'model_source_git_blob_sha':actual,
            'no_prefreeze_predictions_counted':True,'sample_selection':'first issued prediction per immutable UTC-aligned h-hour bin, before inspecting outcome',
            'overall_70pct_claim_allowed':False,'horizons':{}}
    for h in lock['primary_horizons_hours']:
        bins={}
        for row in forecast_rows:
            issued=utc(row['issued_at_utc'])
            if issued<effective or int(row['horizon_hours'])!=h:
                continue
            if row['model_id']!=lock['model_id']:
                raise ValueError('unexpected model version in post-freeze registry')
            index=int((issued-effective).total_seconds()//(3600*h))
            # Before outcomes: earliest prediction for this fixed phase. Do not select wins or scored-only rows.
            if index not in bins or issued<utc(bins[index]['issued_at_utc']):
                bins[index]=row
        selected=[bins[i] for i in sorted(bins)]
        resolved=[row for row in selected if row['forecast_id'] in scores]
        good=[row for row in resolved if scores[row['forecast_id']]['status']=='scored']
        missing=len(resolved)-len(good)
        coverage=len(good)/len(resolved) if resolved else None
        k=sum(scores[row['forecast_id']]['doge_reversal_correct']=='1' for row in good)
        baseline=sum(scores[row['forecast_id']]['majority_correct']=='1' for row in good)
        n=len(good)
        upper=wilson_lower(k,n) if n else None
        duration=(utc(selected[-1]['issued_at_utc'])-utc(selected[0]['issued_at_utc'])).total_seconds()/86400 if len(selected)>1 else 0
        up=[r for r in good if scores[r['forecast_id']]['realized_up']=='UP']
        down=[r for r in good if scores[r['forecast_id']]['realized_up']=='DOWN']
        up_recall=sum(scores[r['forecast_id']]['doge_reversal_correct']=='1' for r in up)/len(up) if up else None
        down_recall=sum(scores[r['forecast_id']]['doge_reversal_correct']=='1' for r in down)/len(down) if down else None
        result['horizons'][str(h)+'h']={
            'locked_bins_with_issued_predictions':len(selected),
            'resolved':len(resolved),'scored':n,'unscorable':missing,
            'pending':len(selected)-len(resolved),
            'coverage_pct':round(100*coverage,2) if coverage is not None else None,
            'observed_span_days':round(duration,2),
            'primary_correct':k,'primary_accuracy_pct':round(100*k/n,2) if n else None,
            'wilson95_lower_pct_descriptive_only':round(100*upper,2) if upper is not None else None,
            'up_recall_pct':round(100*up_recall,2) if up_recall is not None else None,
            'down_recall_pct':round(100*down_recall,2) if down_recall is not None else None,
            'baseline_majority_correct':baseline,
            'baseline_majority_accuracy_pct':round(100*baseline/n,2) if n else None,
            'minimum_n_met':n>=lock['minimum_nonoverlapping_valid_scores_per_horizon'],
            'coverage_target_met':bool(coverage is not None and coverage>=lock['minimum_resolved_quote_coverage_fraction']),
            'wilson_accuracy_target_met':bool(upper is not None and upper>=lock['minimum_wilson_95pct_lower_accuracy_fraction']),
            'time_span_met':duration>=lock['minimum_observation_span_days'],
            'both_directions_scored':bool(up and down),
            'beats_predeclared_majority_baseline':n>0 and k>baseline,
            'eligible_for_70pct_claim':False}
    return result



def audit_independent_minute_shadow(root):
    """Cross-check each minute score and freeze; never pool minute and quote accuracy."""
    protocol=json.loads((root/'data/stage5/minute_evaluation_protocol.json').read_text(encoding='utf-8'))
    latest=jsonfile(root,'data/stage5/minute_shadow/latest.json')
    if (protocol.get('status')!='frozen_protocol'
        or protocol.get('original_quote_scoring_untouched') is not True
        or latest.get('original_V42_scores_modified') is not False
        or latest.get('not_exact_issuance_to_target_quote') is not True
        or latest.get('claim_permission')!='NO_VALIDATED_EDGE'):
        raise ValueError('minute shadow must never be promoted as exact target ticker')
    source=(root/protocol['locked_evaluator_file']).read_bytes()
    digest=hashlib.sha1(b'blob '+str(len(source)).encode()+b'\0'+source).hexdigest()
    if digest!=protocol['locked_evaluator_git_blob_sha']:
        raise ValueError('minute evaluation source hash changed')
    registry=read_csv(root,'data/direction_v42/forward_registry.csv',
        ('forecast_id','issued_at_utc','target_utc','reference_price',
         'majority_direction','doge_reversal_direction','btc_momentum_direction'))
    registered={row['forecast_id']:row for row in registry}
    if len(registered)!=len(registry):raise ValueError('duplicate source predictions')
    scores=read_csv(root,'data/stage5/minute_shadow/minute_scores.csv',
        ('forecast_id','target_utc','bar_open_utc','bar_closed_at_utc',
         'retrieved_at_utc','evaluated_at_utc','minute_close_usdt','realized_up',
         'majority_correct','doge_reversal_correct','btc_momentum_correct',
         'protocol_id','evaluation_cohort','offset_seconds_after_target'))
    seen=set()
    freeze=utc(protocol['effective_from_utc'])
    counts={'pre_freeze_diagnostic_only':0,'preregistered_forward_shadow':0}
    for score in scores:
        fid=score['forecast_id']
        if fid in seen or fid not in registered:
            raise ValueError('duplicate/orphan minute score ID')
        seen.add(fid)
        forecast=registered[fid]
        issued=utc(forecast['issued_at_utc'])
        target=utc(forecast['target_utc'])
        bar_open=utc(score['bar_open_utc'])
        bar_close=utc(score['bar_closed_at_utc'])
        seen_at=utc(score['retrieved_at_utc'])
        audited_at=utc(score['evaluated_at_utc'])
        expect_open_ms=((int(target.timestamp()*1000)+59999)//60000)*60000
        if (score['target_utc']!=forecast['target_utc']
            or int(bar_open.timestamp()*1000)!=expect_open_ms
            or bar_close!=bar_open+timedelta(minutes=1)
            or seen_at<bar_close or audited_at<bar_close
            or not 0<(bar_close-target).total_seconds()<=120):
            raise ValueError('minute score has fabricated target/availability time')
        if abs(float(score['offset_seconds_after_target'])-(bar_close-target).total_seconds())>0.0011:
            raise ValueError('minute offset seconds inconsistent')
        price=float(score['minute_close_usdt'])
        ref=float(forecast['reference_price'])
        if not(math.isfinite(price) and price>0 and math.isfinite(ref) and ref>0):
            raise ValueError('invalid minute shadow or reference price')
        truth='UP' if price>ref else 'DOWN'
        if score['realized_up']!=truth:
            raise ValueError('minute shadow label wrong')
        for model in ('majority','doge_reversal','btc_momentum'):
            if score[model+'_correct']!=('1' if forecast[model+'_direction']==truth else '0'):
                raise ValueError('minute shadow outcome flag wrong')
        expected='pre_freeze_diagnostic_only' if issued<freeze else 'preregistered_forward_shadow'
        if score['evaluation_cohort']!=expected or score['protocol_id']!=protocol['protocol_id']:
            raise ValueError('historical shadow included in prospective experiment')
        counts[expected]+=1
    if (latest['minute_rows_total']!=len(scores)
        or latest['preregistered_forward_rows']!=counts['preregistered_forward_shadow']
        or latest['prefreeze_diagnostic_rows']!=counts['pre_freeze_diagnostic_only']):
        raise ValueError('minute score CSV and report row counts disagree')
    return {'status':'ok','method':'confirmed 1m CLOSE after target, never target ticker quote',
        'code_blob_sha':digest,
        'preregistered_future_evaluations':counts['preregistered_forward_shadow'],
        'retrospective_diagnostics_excluded':counts['pre_freeze_diagnostic_only'],
        'rows':len(scores),'duplicate_or_orphan_ids':0,
        'timestamp_and_direction_score_integrity':'verified',
        'can_merge_into_original_quote_accuracy':False,
        '70pct_accuracy_validated':False}

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
    preregistered_trial=audit_locked_forward_trial(root)
    minute_shadow=audit_independent_minute_shadow(root)
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
    daily_aligned=len(set(dates.values()))==1 and len({
        validation['doge_days'],stat['aligned_days'],probability['aligned_days'],
        history['quality']['symbols']['DOGE-USDT']['days']})==1
    v42_hourly_rows=int(direction['sources']['hourly_rows'])
    latest_hourly_rows=int(hourly['quality']['symbols']['DOGE-USDT']['hours'])
    v42_hourly_at=utc(direction['sources']['hourly_last_closed_start_utc'])
    latest_hourly_at=utc(hourly['quality']['symbols']['DOGE-USDT']['last_confirmed_start_utc'])
    historical_hourly_lag_h=(latest_hourly_at-v42_hourly_at).total_seconds()/3600
    historical_hourly_extra_rows=latest_hourly_rows-v42_hourly_rows
    # V4.2 research benchmark runs once per UTC day; hourly archive updates independently.
    # Permit normal <=26h scheduled refresh lag, never a bulk-backfill change.
    historical_direction_snapshot_aligned=(0<=historical_hourly_lag_h<=26 and 0<=historical_hourly_extra_rows<=26)
    aligned=daily_aligned and historical_direction_snapshot_aligned
    # Never promote historically selected candidate results to verified 70% accuracy.
    return {'schema_version':1,'status':'ok',
     'generated_at_utc':datetime.now(timezone.utc).isoformat(),
     'target_accuracy_pct':70,'historical':historical,
     'forward_live':forward,
     'preregistered_forward_trial':preregistered_trial,
     'independent_minute_shadow':minute_shadow,
     'daily_probability_forward':{'registered_total':len(ids),
       'scored_total':daily_score['scored'],'pending_total':daily_score['pending_count'],
       'quarantined_total':daily_score['quarantined_count'],
       'quarantined_reasons':daily_bad,
       'not_an_accurate_directional_model_claim':True},
     'comparability':{'dates':dates,'data_end_and_row_counts_aligned':daily_aligned,
        'historical_directional_hourly_source_aligned':historical_direction_snapshot_aligned,
        'historical_directional_hourly_rows':v42_hourly_rows,
        'latest_hourly_archive_rows':latest_hourly_rows,
        'historical_directional_hourly_lag_hours':round(historical_hourly_lag_h,2),
        'historical_directional_hourly_unmodeled_new_rows':historical_hourly_extra_rows,
        'hourly_daily_refresh_tolerance_hours':26,
        'historical_directional_hourly_end_utc':v42_hourly_at.isoformat(),
        'latest_hourly_archive_end_utc':latest_hourly_at.isoformat(),
        'status':('aligned_with_daily_hourly_refresh_lag' if aligned else 'BLOCKED_mismatched_history_or_model_source'),
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
