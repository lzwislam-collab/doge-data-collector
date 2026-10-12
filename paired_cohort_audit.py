"""Independent paired outcome audit; NO_TRADE never removes difficult samples."""
import hashlib
import json
from collections import OrderedDict
from datetime import datetime,timedelta,timezone
import paired_forward_cohort as cohort
import paired_selection_v1 as selection
import p1_model_competition as statistics
import quote_evidence_v2 as quotes
import direction_forward_v42 as frozen

COMPARATORS={'frozen_production':'doge_reversal_direction','historical_majority':'majority_direction',
             'btc_momentum':'btc_momentum_direction','always_up':None,'always_down':None}


def nonoverlap(entries):
    selected=[];reserved=None
    for item in sorted(entries,key=lambda x:(x['issued_at_utc'],x['forecast_id'])):
        when=cohort.utc(item['issued_at_utc'])
        if reserved is None or when>=reserved:
            selected.append(item);reserved=cohort.utc(item['target_utc'])+timedelta(minutes=25)
    return selected


def metrics(entries,days):
    valid=[x for x in entries if x['qualified'] and x['pair'] is not None and x['outcome'] is not None]
    n=len(valid);due=len(entries)
    correct=sum(x['pair']['challenger']['direction']==x['outcome'] for x in valid)
    actions=[x for x in valid if x['pair']['decision']['paper_action']=='PAPER_LONG']
    pairs={}
    for name,col in COMPARATORS.items():
        wins=losses=0
        for x in valid:
            older='UP' if name=='always_up' else 'DOWN' if name=='always_down' else x['pair']['production'][col]
            new=x['pair']['challenger']['direction'];outcome=x['outcome']
            wins+=int(new==outcome and older!=outcome);losses+=int(new!=outcome and older==outcome)
        p=statistics.exact_mcnemar(wins,losses)
        pairs[name]={'wins':wins,'losses':losses,'ties':n-wins-losses,'p_exact':p,
                    'passes_familywise_10_tests':wins>losses and wins+losses>=30 and p<=.005}
    brier=sum((x['pair']['challenger']['p_up']-int(x['outcome']=='UP'))**2 for x in valid)/n if n else None
    reliability=[]
    for i in range(10):
        part=[x for x in valid if min(9,int(x['pair']['challenger']['p_up']*10))==i]
        reliability.append({'lower':i/10,'upper':(i+1)/10,'n':len(part),
            'mean_p_up':sum(x['pair']['challenger']['p_up'] for x in part)/len(part) if part else None,
            'up_frequency':sum(x['outcome']=='UP' for x in part)/len(part) if part else None})
    regimes={direction:{'n':len(group),'correct':sum(x['pair']['challenger']['direction']==x['outcome'] for x in group)}
             for direction in ('UP','DOWN') for group in [[x for x in valid if x['pair']['production']['btc_momentum_direction']==direction]]}
    return {'due':due,'qualified':n,'missing':due-n,'quote_pair_coverage_pct':100*n/due if due else None,
        'challenger_accuracy_pct':100*correct/n if n else None,
        'accuracy_if_missing_wrong_pct':100*correct/due if due else None,
        'wilson95_lower_pct':100*statistics.wilson(correct,n) if n else None,
        'frozen_production_accuracy_pct':100*sum(x['pair']['production']['doge_reversal_direction']==x['outcome'] for x in valid)/n if n else None,
        'brier_mean':brier,'brier_half_probability':.25 if n else None,'reliability_fixed_10_bins':reliability,'paired':pairs,
        'paper_long_n':len(actions),'no_trade_n':n-len(actions),
        'paper_long_coverage_of_all_due_pct':100*len(actions)/due if due else None,
        'paper_long_accuracy_pct':100*sum(x['pair']['challenger']['direction']==x['outcome'] for x in actions)/len(actions) if actions else None,
        'no_trade_excluded_from_full_accuracy':False,'exante_btc_regimes':regimes,
        'observation_days':days,'new_model_promotion_authorized':False,'economic_edge_validated':False}


def bounded_cache(cache,key,load):
    if key in cache:
        cache.move_to_end(key);return cache[key]
    value=load();cache[key]=value
    if len(cache)>8:cache.popitem(last=False)
    return value


def verify_pair(root,pair,original,score,proof,bindings,cache=None):
    cache=cache if cache is not None else {'prepared':OrderedDict(),'public_prepared':OrderedDict()}
    if pair['production']!=original:raise ValueError('paired original differs from immutable production')
    issued=cohort.utc(original['issued_at_utc']);target=cohort.utc(original['target_utc'])
    recorded=cohort.utc(pair['decision_recorded_at_utc'])
    if not 0<=(recorded-issued).total_seconds()<=30:raise ValueError('late paired decision')
    if cohort.utc(pair['prepared_at_utc'])>issued:raise ValueError('late challenger preparation')
    snapshot=root/cohort.BASE/'prepared'/(pair['prepared_snapshot_sha256']+'.json')
    prepared,prepared_bytes=bounded_cache(cache['prepared'],pair['prepared_snapshot_sha256'],lambda:(json.loads(snapshot.read_bytes()),snapshot.read_bytes()))
    if cohort.digest(prepared)!=pair['prepared_snapshot_sha256']:raise ValueError('prepared snapshot hash mismatch')
    candidate=prepared['candidates'][original['horizon_hours']]
    if cohort.digest(candidate['fit_examples'])!=candidate['fit_examples_sha256']:raise ValueError('training snapshot hash mismatch')
    examples=candidate['fit_examples']
    if (max(x['label_close_ms'] for x in examples['train'])>=min(x['anchor_ms']+3600000 for x in examples['calibration'])
        or max(x['label_close_ms'] for x in examples['calibration'])>=min(x['anchor_ms']+3600000 for x in examples['validation'])):
        raise ValueError('training calibration validation intervals overlap')
    if any(x['label_close_ms']>int(cohort.utc(prepared['source_close_utc']).timestamp()*1000) for group in examples.values() for x in group):
        raise ValueError('future fitted label')
    raw_p=cohort.causal.probability(candidate['weights'],candidate['prediction_features'])
    p=cohort.causal.probability(candidate['platt_weights'],[1.,selection.logit(raw_p)])
    if abs(p-pair['challenger']['p_up'])>1e-12:raise ValueError('paired probability disagrees with frozen prepared weights')
    recreated=cohort.make_pair(original,prepared,pair['issue_quote_sha256'],recorded)
    check=dict(pair);check.pop('previous_sha256');check.pop('record_sha256')
    if recreated!=check:raise ValueError('paired probability/decision not reproducible from prepared snapshot')
    quotes.verify_bound(root,original,score,bindings)
    if bindings[original['forecast_id']+'|ISSUE']['quote_id']!=pair['issue_quote_sha256']:raise ValueError('different issue quote')
    public=proof.get('publication',{})
    observed=cohort.utc(public['observed_public_utc'])
    if not recorded<=observed<target or observed>issued+timedelta(minutes=45):raise ValueError('pair not publicly witnessed before outcome')
    head=public['head_sha']
    individual=cohort.record_path(root/cohort.REG,original['forecast_id']).relative_to(root).as_posix()
    raw_pair=cohort.publication.command(root,'show',head+':'+individual)
    try:public_pair=json.loads(raw_pair)
    except ValueError:raise ValueError('paired record absent at original public witness')
    if pair!=public_pair:raise ValueError('paired record absent/changed at original public witness')
    public_path=head+':'+snapshot.relative_to(root).as_posix()
    public_prepared=bounded_cache(cache['public_prepared'],public_path,lambda:cohort.publication.command(root,'show',public_path))
    if public_prepared!=prepared_bytes:raise ValueError('prepared model absent/changed at witness')
    return True


def evaluate(root=cohort.ROOT,now=None):
    now=now or datetime.now(timezone.utc);policy=cohort.guard(root);records=cohort.history_guard(root)
    effective=cohort.utc(policy['effective_from_utc'])
    registry=frozen.read_rows(root/'data/direction_v42/forward_registry.csv',frozen.FCOLS)
    scores=frozen.read_rows(root/'data/direction_v42/forward_scores.csv',frozen.SCOLS)
    bindings=frozen.read_rows(root/quotes.BINDINGS,quotes.BCOLS)
    operations=json.loads((root/'data/p0_operations/latest.json').read_text(encoding='utf-8'))
    if (operations.get('status')!='ok' or operations.get('frozen_implementation_verified') is not True
        or not 0<=(now-cohort.utc(operations['audited_at_utc'])).total_seconds()<=21600):
        raise ValueError('P0 original public evidence missing/stale')
    # Require current immutable contents, not a stale report authorizing modified rows.
    cohort.publication.validate_state(root)
    paired={r['production']['forecast_id']:r for r in records}
    entries=[];rejected={};pending=[];cache={'prepared':OrderedDict(),'public_prepared':OrderedDict()}
    for fid,original in registry.items():
        issued=cohort.utc(original['issued_at_utc']);h=int(original['horizon_hours'])
        if issued<effective or h not in (1,24):continue
        if issued>now:raise ValueError('future production issuance')
        pair=paired.get(fid);score=scores.get(fid);proof=operations.get('forecast_proofs',{}).get(fid)
        valid=False;outcome=None
        if pair and proof:
            try:
                verify_pair(root,pair,original,score,proof,bindings,cache)
                if score and score['status']=='scored':outcome=score['realized_up'];valid=True
            except Exception as exc:rejected[fid]=type(exc).__name__+': '+str(exc)[:200]
        elif not pair:rejected[fid]='missing_pair_no_retrospective_reconstruction'
        else:rejected[fid]='waiting_for_original_pre_target_server_witness'
        entry={'forecast_id':fid,'issued_at_utc':original['issued_at_utc'],
               'target_utc':original['target_utc'],'horizon_hours':original['horizon_hours'],
               'pair':pair,'qualified':valid,'outcome':outcome}
        entries.append(entry)
        if cohort.utc(original['target_utc'])+timedelta(minutes=25)>now:pending.append(fid)
    # Orphan paired records can never be removed from the audit silently.
    if any(fid not in registry for fid in paired):raise ValueError('paired original missing from production registry')
    age=max(0,(now-effective).total_seconds()/86400);tracks={}
    closed=max(0,int((now-effective-timedelta(minutes=45)).total_seconds()//3600)+1)
    for h in (1,24):
        group=[x for x in entries if int(x['horizon_hours'])==h]
        due=[x for x in group if cohort.utc(x['target_utc'])+timedelta(minutes=25)<=now]
        independent=nonoverlap(group)
        independent_due=[x for x in independent if cohort.utc(x['target_utc'])+timedelta(minutes=25)<=now]
        slots={int((cohort.utc(x['issued_at_utc'])-effective).total_seconds()//3600) for x in group}
        paired_slots={int((cohort.utc(x['issued_at_utc'])-effective).total_seconds()//3600) for x in group if x['pair']}
        issued_closed=sum(i in slots for i in range(closed));paired_closed=sum(i in paired_slots for i in range(closed))
        full=metrics(due,age);ind=metrics(independent_due,age)
        issuance=issued_closed/closed if closed else None;paired_cov=paired_closed/closed if closed else None
        meets=bool(ind['qualified']>=100 and age>=30 and issuance is not None and issuance>=.9
            and paired_cov is not None and paired_cov>=.9 and (ind['quote_pair_coverage_pct'] or 0)>=90
            and (ind['wilson95_lower_pct'] or 0)>=70 and all(x['passes_familywise_10_tests'] for x in ind['paired'].values())
            and all(x['n']>=30 for x in ind['exante_btc_regimes'].values()))
        tracks[str(h)+'h']={'all_predictions':full,'fixed_nonoverlap':ind,'pending':len(group)-len(due),
            'expected_closed_hourly_issuance_slots':closed,'issued_closed':issued_closed,
            'paired_closed':paired_closed,'missing_original_issue_slots':closed-issued_closed,
            'missing_pair_slots':closed-paired_closed,
            'original_issuance_coverage_pct':100*issuance if issuance is not None else None,
            'paired_issuance_coverage_pct':100*paired_cov if paired_cov is not None else None,
            'eligible_for_independent_manual_review_only':meets,'edge_validated':False}
    result={'schema_version':1,'status':'ok','protocol_id':policy['protocol_id'],
        'evaluated_at_utc':now.isoformat(),'source_p0_cutoff_utc':operations['audited_at_utc'],
        'effective_from_utc':effective.isoformat(),'activation_state':'ACTIVE_SHADOW' if now>=effective else 'WAIT_ACTIVATION',
        'registered_pairs':len(records),'tracks':tracks,'pending_ids':pending,'rejected_or_missing':rejected,
        'action':'NO_TRADE','real_orders_authorized':False,'auto_promotion':False,
        'validated_70pct':False,'economic_edge_validated':False,'frozen_production_changed':False,
        'selection_and_forecast_accuracy_are_separate':True,
        'input_registry_sha256':hashlib.sha256((root/'data/direction_v42/forward_registry.csv').read_bytes()).hexdigest(),
        'limits':['Full forecasts include all abstentions; selection subset never replaces headline accuracy',
            'Historical Platt calibration is not forward probability validation',
            'Fixed 10-test Bonferroni across five comparators and two horizons; no automatic promotion',
            'Sample count and days are review gates, not proof of profit; P2 remains independent']}
    return result


def main():
    try:
        result=evaluate()
        frozen.write(cohort.ROOT/cohort.BASE/'audit_latest.json',json.dumps(result,ensure_ascii=False,indent=2)+'\n')
        frozen.write(cohort.ROOT/'reports/DOGE_PAIRED_SELECTIVE_SHADOW_V1.md',
            '# DOGE 同场预测与选择性纸面决定 V1\n\n结论：'+result['activation_state']+'；不授权交易或晋级。\n\n'
            +'截止UTC：'+result['evaluated_at_utc']+'；原P0截止：'+result['source_p0_cutoff_utc']+'。\n\n'
            +'同一生产发行报价、特征截点和到期结果；NO_TRADE完整计入全样本准确率。\n\n'
            +'配对登记数：'+str(result['registered_pairs'])+'。不同周期和不同版本不合并凑样本。历史校准与收益边界只是纸面选择代理，不是前瞻有效性证明。\n')
        print(json.dumps({'status':'ok','activation_state':result['activation_state'],'registered_pairs':result['registered_pairs']}));return 0
    except Exception as exc:
        print(json.dumps({'status':'BLOCKED','error':str(exc)}));return 1


if __name__=='__main__':raise SystemExit(main())
