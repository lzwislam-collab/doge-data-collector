"""Read-only bridge to frozen real forecast, feature and book evidence.

No input JSON flags can authorize inference: the CLI replays existing frozen
guards, server-witnessed Git content, raw quote bindings and captured books.
"""
import base64
import hashlib
import json
import math
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.request import Request, urlopen

import factor_evaluation_v1 as review

ROOT=Path(__file__).resolve().parent
POLICY='config/factor_evaluation_v1.json'
REGISTRY='system/factor_evaluation_v1_registry.json'
OUT='data/factor_evaluation_v1'
REPO='lzwislam-collab/doge-data-collector'


def sha(raw):return hashlib.sha256(raw).hexdigest()


def check_freeze(root):
    registry=json.loads((root/REGISTRY).read_bytes());raw=(root/POLICY).read_bytes()
    policy=json.loads(raw)
    if registry['version_id']!=policy['protocol_id'] or sha(raw)!=registry['protocol_sha256']:
        raise ValueError('evaluation protocol changed after freeze')
    for path,expected in registry['code_sha256_lf'].items():
        if sha((root/path).read_bytes().replace(b'\r\n',b'\n'))!=expected:
            raise ValueError('evaluation implementation changed after freeze: '+path)
    return policy


def public_freeze(files,runs,read_at,effective):
    """Server-run clocks, never commit author dates, witness exact frozen bytes."""
    effective=review.utc(effective)
    for run in sorted(runs,key=lambda r:r.get('created_at','')):
        try:
            if (run['path'].split('@')[0]!='.github/workflows/factor_evaluation_v1.yml'
                    or run['status']!='completed' or run['conclusion']!='success'
                    or not review.utc(run['created_at'])<=review.utc(run['updated_at'])<effective):continue
            head=run['head_sha']
            if len(head)!=40 or any(c not in '0123456789abcdef' for c in head):continue
            if all(read_at(head,path)==raw for path,raw in files.items()):
                return {'verified':True,'run_id':run['id'],'run_attempt':run.get('run_attempt',1),
                        'head_sha':head,'server_created_at_utc':run['created_at'],
                        'server_completed_by_utc':run['updated_at'],
                        'trust_limit':'GitHub server witness; repository administrators can rewrite/delete history; no independent WORM'}
        except (KeyError,ValueError,OSError):continue
    return {'verified':False,'reason':'no_successful_pre_activation_server_run_with_exact_frozen_files'}


def get_json(path):
    headers={'Accept':'application/vnd.github+json','User-Agent':'DOGE-Factor-Review/1'}
    token=os.environ.get('GITHUB_TOKEN')
    if token:headers['Authorization']='Bearer '+token
    with urlopen(Request('https://api.github.com/repos/'+REPO+'/'+path,headers=headers),timeout=30) as response:
        raw=response.read(8_000_001)
    if len(raw)>8_000_000:raise ValueError('oversized GitHub witness response')
    return json.loads(raw)


def registration(root,policy):
    registry=json.loads((root/REGISTRY).read_bytes())
    files={POLICY:(root/POLICY).read_bytes(),REGISTRY:(root/REGISTRY).read_bytes()}
    files.update({path:(root/path).read_bytes().replace(b'\r\n',b'\n') for path in registry['code_sha256_lf']})
    files['.github/workflows/factor_evaluation_v1.yml']=(root/'.github/workflows/factor_evaluation_v1.yml').read_bytes().replace(b'\r\n',b'\n')
    # At most 1000 runs, failing closed if the witness cannot be found. Do not
    # infer publication from local Git commits or backdated author timestamps.
    runs=[]
    for page in range(1,11):
        boundary=review.utc(policy['effective_from_utc']).strftime('%Y-%m-%dT%H:%M:%SZ')
        part=get_json('actions/workflows/factor_evaluation_v1.yml/runs?created=%3C'+boundary+'&per_page=100&page='+str(page))['workflow_runs']
        runs.extend(part)
        if len(part)<100:break
    def read_at(head,path):
        data=get_json('contents/'+path+'?ref='+head)
        if data.get('encoding')!='base64':raise ValueError('unexpected public contents encoding')
        return base64.b64decode(data['content']).replace(b'\r\n',b'\n')
    return public_freeze(files,runs,read_at,policy['effective_from_utc'])


def book_eligible(book,item):
    older=book.get('eligible_sources',{}).get(item['forecast_id'],{})
    return (older.get('source_verified') is True
            and older.get('pair_sha256')==item['pair_sha256']
            and older.get('observed_public_utc')==item['observed_public_utc']
            and older.get('source_witness_head_sha')==item['source_witness_head_sha'])


def validate_change(root,proposal,now):
    """Validate a reviewable manifest, never apply it or grant production rights.

    A separately server-witnessed preregistration, independent sign-off and new
    model registration are still required. This function cannot mint those.
    """
    if review.utc(proposal['effective_from_utc'])<=review.utc(now):raise ValueError('future activation required')
    if not proposal['old_version'] or proposal['old_version']==proposal['new_version']:
        raise ValueError('distinct retained version required')
    maps=[proposal['rollback_files_sha256'],proposal['new_files_sha256']]
    if not all(maps) or set(maps[0])&set(maps[1]):raise ValueError('separate intact rollback artifacts required')
    def read(path):
        file=(root/path).resolve()
        if not file.is_relative_to(root.resolve()):raise ValueError('manifest path outside workspace')
        return file.read_bytes()
    for mapping in maps:
        for path,expected in mapping.items():
            if sha(read(path))!=expected:raise ValueError('change/rollback content hash mismatch')
    raw=read(proposal['review_report_path'])
    if sha(raw)!=proposal['review_report_sha256']:raise ValueError('review report changed')
    report=json.loads(raw)
    if any(report[k]!=proposal[k] for k in ('protocol_id','group','horizon_hours')):
        raise ValueError('proposal does not match reviewed factor/horizon')
    expected={'PROMOTE':'REVIEW_PROMOTION','RETIRE':'REVIEW_RETIREMENT'}.get(proposal['change'])
    if expected is None or report['status']!=expected:raise ValueError('no qualifying review recommendation')
    return {'status':'MANIFEST_VALIDATED_PENDING_INDEPENDENT_PUBLIC_PREREGISTRATION_AND_APPROVAL',
            'rollback_integrity_checked':True,'production_allowed':False,
            'model_mutation_performed':False,'real_orders_authorized':False}


def economic_deltas(root,books,pair,variant,public,now):
    import p2_paired_execution_v3_1 as paper
    original=pair['production'];h=int(original['horizon_hours']);fid=original['forecast_id']
    item={'forecast_id':fid,'horizon_hours':h,'issued_at_utc':original['issued_at_utc'],
          'target_utc':original['target_utc'],'paper_action':variant['paper_action'],
          'observed_public_utc':public['observed_public_utc'],'source_witness_head_sha':public['head_sha'],
          'source_verified':True,'pair_sha256':pair['record_sha256']}
    start=review.utc(public['observed_public_utc'])
    end=review.utc(original['target_utc'])+timedelta(minutes=25)
    if end>now:return None
    stream=[]
    for book in books:
        if not start<=review.utc(book['captured_at_utc'])<=end:continue
        copied=dict(book);copied['eligible_sources']={fid:item} if book_eligible(book,item) else {}
        stream.append(copied)
    candidate=paper.replay([item],stream,end)[str(h)+'h']
    control=dict(item,paper_action=pair['decision']['paper_action'])
    control_stream=[dict(b,eligible_sources={fid:control} if fid in b['eligible_sources'] else {}) for b in stream]
    baseline=paper.replay([control],control_stream,end)[str(h)+'h']
    for result in (candidate,baseline):
        if (result['completed_due_intervals']!=1 or result['blocked_forecast_id'] is not None
                or result['path_gap_count'] or result['missing_mark_count']):return None
    get=lambda r,key:r[key]['prefix_net_return_pct']/100
    net,stress=get(candidate,'strategy'),get(candidate,'double_cost')
    return {'paired_net':net-get(baseline,'strategy'),'cash_net':net,
            'hold_net':net-get(candidate,'hold'),
            'paired_stress_net':stress-get(baseline,'double_cost'),
            'cash_stress_net':stress,'hold_stress_net':stress-get(candidate,'double_cost_hold')}


def replay_feature(root,group,data,prepared_at,source_rows,public_head=None):
    """Reparse source packets / reconstruct four child candles, then formula."""
    import feature_ablation as features
    import csv
    at=review.utc(prepared_at);observations=data['evidence']
    asof=list(features.asof_rows(source_rows,at).values())
    for source in observations:
        if review.utc(source['usable_from_utc'])>at:raise ValueError('source unavailable at preparation')
        if group!='FOUR_HOUR':
            if source not in source_rows:raise ValueError('prepared source absent from PIT ledger')
            seen=review.utc(source['first_seen_utc'])
            if not review.utc(source['requested_at_utc'])<=seen<=review.utc(source['usable_from_utc']):
                raise ValueError('invalid source receipt chronology')
            raw=(root/source['raw_path']).read_bytes()
            if sha(raw)!=source['raw_sha256']:raise ValueError('source raw digest mismatch')
            parsed=features.sources.parse(source['source_id'],raw,seen)
            matching=[r for r in parsed if r['observation_key']==source['observation_key']]
            if len(matching)!=1 or any(source.get(k)!=v for k,v in matching[0].items()):
                raise ValueError('source observation does not reproduce from raw response')
    if group=='FOUR_HOUR':
        import derived_four_hour as derived
        if len(observations)!=2:raise ValueError('two four-hour bars required')
        with (root/'data/derived_4h_v1/ohlc.csv').open(encoding='utf-8',newline='') as stream:
            bars=[r for r in csv.DictReader(stream) if r['symbol']=='DOGE-USDT'
                  and review.utc(r['usable_from_utc'])<=at and int(r['close_utc_ms'])<=int(at.timestamp()*1000)]
        bars.sort(key=lambda r:int(r['open_utc_ms']))
        if observations!=bars[-2:] or at.timestamp()*1000-int(bars[-1]['close_utc_ms'])>14400000:
            raise ValueError('four-hour feature not latest qualified as-of bars')
        with (root/'data/hourly_research/ohlc.csv').open(encoding='utf-8',newline='') as stream:
            hours=list(csv.DictReader(stream))
        for bar in observations:
            start=int(bar['open_utc_ms'])
            children=[r for r in hours if r['symbol']==bar['symbol'] and start<=int(r['open_utc_ms'])<start+14400000]
            rebuilt,missing=derived.aggregate(children,at,{bar['symbol']+'|'+bar['open_utc_ms']:bar})
            if missing or rebuilt!=[bar]:raise ValueError('four-hour bar not reproducible from confirmed children')
        if int(observations[1]['open_utc_ms'])-int(observations[0]['open_utc_ms'])!=14400000:
            raise ValueError('noncontiguous four-hour bars')
        value=features.clip(math.log(float(observations[-1]['close'])/float(observations[-2]['close']))*20)
    elif group=='DXY':
        if len(observations)!=2 or any(r['source_id']!='DXY_YAHOO_UNOFFICIAL' for r in observations):raise ValueError('DXY source mismatch')
        expected=sorted([r for r in asof if r['source_id']=='DXY_YAHOO_UNOFFICIAL'],key=lambda r:r['observation_key'])[-2:]
        if observations!=expected or (at-review.utc(observations[-1]['usable_from_utc'])).total_seconds()>172800 or (at.date()-datetime.fromisoformat(observations[-1]['observation_key']).date()).days>10:
            raise ValueError('DXY not latest fresh as-of pair')
        value=-features.clip(math.log(observations[-1]['value']/observations[-2]['value'])*20)
    elif group=='BTC_SENTIMENT':
        if len(observations)!=1 or observations[0]['source_id']!='BTC_FEAR_GREED_PROXY':raise ValueError('sentiment source mismatch')
        expected=sorted([r for r in asof if r['source_id']=='BTC_FEAR_GREED_PROXY'],key=lambda r:r['observation_key'])[-1:]
        if observations!=expected or (at-review.utc(observations[0]['usable_from_utc'])).total_seconds()>172800 or (at-review.utc(observations[0]['published_at_utc'])).total_seconds()>259200:
            raise ValueError('sentiment not latest fresh as-of observation')
        value=features.clip((observations[0]['value']-50)/25)
    elif group=='NEWS_TONE':
        covered=data.get('covered_sources',[])
        if not covered or any(not sid.endswith('NEWS') for sid in covered):raise ValueError('unverified news source coverage')
        if public_head is None:raise ValueError('pre-target source report witness required')
        raw_report=features.cohort.publication.command(root,'show',public_head+':'+features.sources.BASE+'/latest.json')
        reports=json.loads(raw_report)['sources']
        active={sid for sid,r in reports.items() if sid.endswith('NEWS') and r['status']=='ok'
                and 0<=(at-review.utc(r['captured_at_utc'])).total_seconds()<=86400}
        if covered!=sorted(active):raise ValueError('news source subset not original as-of coverage')
        for sid in active:
            report=reports[sid];raw=(root/report['raw_path']).read_bytes()
            if sha(raw)!=report['raw_sha256']:raise ValueError('news source report raw digest mismatch')
            features.sources.parse(sid,raw,review.utc(report['captured_at_utc']))
        expected=[r for r in asof if r['source_id'] in active and r.get('published_at_utc')
                  and 0<=(at-review.utc(r['published_at_utc'])).total_seconds()<=86400]
        if observations!=expected:raise ValueError('news items differ from full as-of source ledger')
        if any(r['source_id'] not in covered or not r.get('published_at_utc') or not 0<=(at-review.utc(r['published_at_utc'])).total_seconds()<=86400 for r in observations):
            raise ValueError('news source/time mismatch')
        value=features.clip(2*sum(r['value'] for r in observations)/max(1,len(observations)))
    else:raise ValueError('unregistered feature group')
    if abs(value-review.number(data['value']))>1e-12:raise ValueError('feature value not mechanically reproducible')
    age=max((at-review.utc(r['usable_from_utc'])).total_seconds() for r in observations) if observations else None
    return {'source_count':len(observations),'oldest_observation_age_seconds':age,
            'raw_and_formula_replayed':True,'four_hour_is_derived_not_independent':group=='FOUR_HOUR'}


def load_events(root,policy,now):
    import feature_ablation as features
    import paired_cohort_audit as audit
    import paired_forward_cohort as paired
    import p0_operational_audit as p0
    import p2_paired_execution_v3_1 as paper
    features.guard(root);paired.guard(root);paper.guard(root)
    feature_rows=features.chain(root)
    originals=paired.frozen.read_rows(root/'data/direction_v42/forward_registry.csv',paired.frozen.FCOLS)
    scores=paired.frozen.read_rows(root/'data/direction_v42/forward_scores.csv',paired.frozen.SCOLS)
    bindings=paired.frozen.read_rows(root/paired.quotes.BINDINGS,paired.quotes.BCOLS)
    pairs={r['production']['forecast_id']:r for r in paired.history_guard(root)}
    contexts={r['forecast_id']:r for r in feature_rows}
    # Requalify remote publication/raw bindings instead of trusting latest.json.
    operations=p0.evaluate(root,now=now)
    books=list(paper.event_books(root,paper.history_guard(root)))
    result={(g,h):[] for g in policy['groups'] for h in policy['horizons_hours']};rejected={}
    start=review.utc(policy['effective_from_utc'])
    for fid,original in originals.items():
        h=int(original['horizon_hours']);issued=review.utc(original['issued_at_utc'])
        if h not in policy['horizons_hours'] or issued<start:continue
        # All issuances remain visible even when qualification fails. The fixed
        # planner also counts wholly absent issuer hours independently.
        for group in policy['groups']:
            row={'event_id':fid,'horizon_hours':h,'group':group,'issued_at_utc':original['issued_at_utc'],
                 'target_utc':original['target_utc'],'candidate_p':None,'paired_p':None,
                 'candidate_direction':None,'paired_direction':None,'frozen_direction':original['doge_reversal_direction'],
                 'regime':None,'y':None,'paper_action':None,'economic_deltas':None,'rolling_validation_verified':False}
            try:
                pair=pairs[fid];context=contexts[fid];proof=operations['forecast_proofs'][fid]
                if context['base_pair_sha256']!=pair['record_sha256'] or context['baseline_p_up']!=pair['challenger']['p_up']:
                    raise ValueError('feature/control not the same immutable event')
                audit.verify_pair(root,pair,original,scores.get(fid),proof,bindings)
                public=features.verify_record(root,context,original,proof)
                variant=context['variants'][group]
                if variant['status']!='ISSUED':raise ValueError('missing feature is not zero')
                prepared=json.loads((root/features.BASE/'prepared'/(context['prepared_sha256']+'.json')).read_bytes())
                source_rows=features.sources.read(root)
                provenance=replay_feature(root,group,prepared['features'][group],prepared['prepared_at_utc'],source_rows,public['head_sha'])
                score=scores.get(fid)
                y=None
                if score and score['status']=='scored':
                    if review.utc(original['target_utc'])+timedelta(minutes=25)>now:raise ValueError('premature target label')
                    y=int(score['realized_up']=='UP')
                row.update(candidate_p=variant['p_up_raw_proxy'],candidate_direction=variant['direction'],
                           paired_p=pair['challenger']['p_up'],paired_direction=pair['challenger']['direction'],
                           regime=original['btc_momentum_direction'],y=y,paper_action=variant['paper_action'],
                           source_provenance=provenance,
                           rolling_validation_verified=True,
                           economic_deltas=economic_deltas(root,books,pair,variant,public,now))
            except (KeyError,ValueError,OSError) as exc:rejected[group+'|'+fid]=type(exc).__name__+': '+str(exc)[:250]
            result[group,h].append(row)
    return result,rejected


def run(root=ROOT,now=None):
    now=review.utc(now or datetime.now(timezone.utc));policy=check_freeze(root)
    expected_family=len(policy['groups'])*len(policy['horizons_hours'])*15*len(policy['review_dates_utc'])*2
    if policy['family_size']!=expected_family or policy['horizons_hours']!=[1,24] or 'slot_step_hours' in policy:
        raise ValueError('evaluation comparison family or horizon schedule changed')
    errors=[]
    try:witness=registration(root,policy)
    except Exception as exc:witness={'verified':False,'reason':type(exc).__name__+': '+str(exc)[:250]}
    rows={(g,h):[] for g in policy['groups'] for h in policy['horizons_hours']};rejected={}
    if now>=review.utc(policy['effective_from_utc']):
        try:rows,rejected=load_events(root,policy,now)
        except Exception as exc:errors.append(type(exc).__name__+': '+str(exc)[:250])
    tracks={g+'_'+str(h)+'h':review.evaluate(policy,rows[g,h],now,
                registered=witness['verified'] and not errors,horizon=h,group=g)
            for g in policy['groups'] for h in policy['horizons_hours']}
    # Existing research scores are historical descriptions only; never inputs
    # to new inference or proof that a factor works.
    legacy_path=root/'data/feature_ablation_v1/audit_latest.json';legacy={}
    if legacy_path.exists():
        raw=legacy_path.read_bytes();old=json.loads(raw)
        legacy={'source_sha256':sha(raw),'cutoff_utc':old.get('audited_at_utc'),
                'independently_requalified_in_this_run':False,
                'tracks':{k:{f:v.get(f) for f in ('due_reserved','qualified','missing','accuracy_pct','brier')}
                          for k,v in old.get('tracks',{}).items()}}
    out={'schema_version':1,'protocol_id':policy['protocol_id'],'audited_at_utc':now.isoformat(),
         'status':'BLOCKED_EVIDENCE' if errors else 'WAIT_FUTURE_ACTIVATION' if now<review.utc(policy['effective_from_utc']) else 'ACTIVE_RESEARCH_REVIEW',
         'public_freeze':witness,'tracks':tracks,'rejected':rejected,'errors':errors,'legacy_descriptive_only':legacy,
         'inputs_sha256':{POLICY:sha((root/POLICY).read_bytes()),REGISTRY:sha((root/REGISTRY).read_bytes())},
         'production_model_changed':False,'new_production_features_adopted':0,
         'real_orders_authorized':False,'dca_rules_modified':False,'profitability_validated':False,
         'limits':['MBB assumes adequate stationarity/mixing and fixed block length; independent review required.',
                   'Interval paper hold/cash comparisons are not continuous compounded portfolios or live fills.',
                   'Existing frozen P2 acceptance, live fees/fills and continuous drawdown require separate review.',
                   'No recommendation authorizes model mutation; freeze new version prospectively and keep old rollback version.']}
    body=(json.dumps(out,ensure_ascii=False,sort_keys=True,indent=2,allow_nan=False)+'\n').encode()
    folder=root/OUT;folder.mkdir(parents=True,exist_ok=True)
    path=folder/(sha(body)+'.json')
    if path.exists() and path.read_bytes()!=body:raise ValueError('evaluation snapshot changed')
    if not path.exists():path.write_bytes(body)
    (folder/'latest.json').write_bytes(body)
    return out


if __name__=='__main__':
    try:
        result=run();print(json.dumps({'status':result['status'],'public_freeze':result['public_freeze']['verified'],
                                      'tracks':{k:v['status'] for k,v in result['tracks'].items()},
                                      'production_model_changed':False}))
        if result['errors']:raise SystemExit(1)
    except Exception as exc:
        print(json.dumps({'status':'BLOCKED','error':type(exc).__name__+': '+str(exc),'production_model_changed':False}))
        raise SystemExit(1)
