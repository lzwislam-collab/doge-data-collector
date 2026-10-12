"""Precommitted isolated feature perturbations of paired challenger; no orders."""
import csv,hashlib,json,math,os,sys
from datetime import datetime,timedelta,timezone
from pathlib import Path
import enhancement_sources as sources
import evidence_append_guard
import paired_forward_cohort as cohort
import paired_cohort_audit as paired_audit
import paired_selection_v1 as selection
import p2_paired_execution_v3_1 as economics

ROOT=Path(__file__).resolve().parent;BASE='data/feature_ablation_v1'
POLICY='data/stage5/feature_ablation_v1_protocol.json'
SYSTEM='system/versions/DOGE_FEATURE_ABLATION_V1_20261013_0200.json'
GROUPS=('FOUR_HOUR','DXY','NEWS_TONE','BTC_SENTIMENT');ALPHA=.15
def utc(s):return sources.utc(s)
def digest(x):return sources.digest(x)
def clip(x):return max(-4.,min(4.,float(x)))
def asof_rows(rows,at):
    result={}
    for r in rows:
        if utc(r['usable_from_utc'])<=at:result[r['source_id'],r['observation_key']]=r
    return result
def variant(base_p,value):
    if value is None:return {'status':'WAIT_FEATURE','direction':None,'p_up_raw_proxy':None,'paper_action':'NO_TRADE','real_orders_authorized':False}
    score=selection.logit(base_p)+ALPHA*clip(value);p=1/(1+math.exp(-score))
    return {'status':'ISSUED','direction':'UP' if p>=.5 else 'DOWN','p_up_raw_proxy':p,
        'paper_action':'PAPER_LONG' if p>=.60 else 'NO_TRADE','feature_value':clip(value),
        'probability_calibration_validated':False,'research_model':'FIXED_FEATURE_LOGIT_NUDGE_V1',
        'real_orders_authorized':False,'does_not_remove_no_trade_from_direction_accuracy':True}
def code_files():
    import ast
    todo=['feature_ablation','feature_registration','enhancement_sources','derived_four_hour','evidence_append_guard'];seen=set()
    while todo:
        name=todo.pop();path=ROOT/(name+'.py')
        if name in seen or not path.exists():continue
        seen.add(name)
        for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
            if isinstance(node,ast.Import):todo.extend(x.name.split('.')[0] for x in node.names)
            elif isinstance(node,ast.ImportFrom) and node.module:todo.append(node.module.split('.')[0])
    return sorted({n+'.py' for n in seen}|set(economics.CODE_FILES))
def guard(root=ROOT):
    p=json.loads((root/POLICY).read_bytes());r=json.loads((root/SYSTEM).read_bytes())
    pointer=json.loads((root/'system/current_feature_research.json').read_bytes())
    if pointer['registry_path']!=SYSTEM or pointer['registry_sha256']!=hashlib.sha256((root/SYSTEM).read_bytes()).hexdigest() or pointer['version_id']!=r['version_id']:raise ValueError('feature pointer changed')
    hashes={f:hashlib.sha256((root/f).read_bytes().replace(b'\r\n',b'\n')).hexdigest() for f in code_files()}
    if r['code_sha256_lf']!=hashes or r['protocol_sha256']!=hashlib.sha256((root/POLICY).read_bytes()).hexdigest():raise ValueError('feature registry integrity mismatch')
    if p['protocol_id']!=r['version_id'] or p['fixed_logit_feature_weight']!=ALPHA or p['real_orders_authorized'] is not False:raise ValueError('feature protocol identity/rule mismatch')
    return p
def chain(root=ROOT):
    path=root/BASE/'contexts.jsonl';body=path.read_bytes() if path.exists() else b''
    evidence_append_guard.guard(root,path.relative_to(root).as_posix(),body)
    previous=None;rows=[];ids=set()
    for line in body.decode().splitlines():
        row=json.loads(line);check=dict(row);sha=check.pop('record_sha256')
        if sha!=digest(check) or row['previous_sha256']!=previous or row['forecast_id'] in ids:raise ValueError('feature context hash chain/duplicate changed')
        if json.loads((root/BASE/'records'/(row['forecast_id']+'.json')).read_bytes())!=row:raise ValueError('feature individual record changed')
        previous=sha;ids.add(row['forecast_id']);rows.append(row)
    return rows
def feature_values(root,at):
    values={g:{'value':None,'status':'WAIT_SOURCE','evidence':[]} for g in GROUPS}
    path=root/'data/derived_4h_v1/ohlc.csv'
    if path.exists():
        with path.open(encoding='utf-8',newline='') as f:bars=[r for r in csv.DictReader(f) if r['symbol']=='DOGE-USDT' and utc(r['usable_from_utc'])<=at and int(r['close_utc_ms'])<=int(at.timestamp()*1000)]
        bars.sort(key=lambda r:int(r['open_utc_ms']))
        if len(bars)>=2 and int(bars[-1]['open_utc_ms'])-int(bars[-2]['open_utc_ms'])==14400000 and at.timestamp()*1000-int(bars[-1]['close_utc_ms'])<=14400000:
            values['FOUR_HOUR']={'value':clip(math.log(float(bars[-1]['close'])/float(bars[-2]['close']))*20),'status':'qualified','evidence':bars[-2:]}
    rows=list(asof_rows(sources.read(root),at).values())
    dollar=sorted([r for r in rows if r['source_id']=='DXY_YAHOO_UNOFFICIAL'],key=lambda r:r['observation_key'])
    if len(dollar)>=2 and (at-utc(dollar[-1]['usable_from_utc'])).total_seconds()<=172800 and (at.date()-datetime.fromisoformat(dollar[-1]['observation_key']).date()).days<=10:
        values['DXY']={'value':-clip(math.log(dollar[-1]['value']/dollar[-2]['value'])*20),'status':'qualified_unofficial_DXY','evidence':dollar[-2:]}
    fg=sorted([r for r in rows if r['source_id']=='BTC_FEAR_GREED_PROXY'],key=lambda r:r['observation_key'])
    if fg and (at-utc(fg[-1]['usable_from_utc'])).total_seconds()<=172800 and (at-utc(fg[-1]['published_at_utc'])).total_seconds()<=259200:
        values['BTC_SENTIMENT']={'value':clip((fg[-1]['value']-50)/25),'status':'qualified_Bitcoin_proxy','evidence':fg[-1:]}
    report_path=root/sources.BASE/'latest.json'
    if report_path.exists():
        reports=json.loads(report_path.read_bytes())['sources']
        active={sid for sid,r in reports.items() if sid.endswith('NEWS') and r['status']=='ok' and 0<=(at-utc(r['captured_at_utc'])).total_seconds()<=86400}
        if active:
            news=[r for r in rows if r['source_id'] in active and r.get('published_at_utc') and 0<=(at-utc(r['published_at_utc'])).total_seconds()<=86400]
            values['NEWS_TONE']={'value':clip(2*sum(r['value'] for r in news)/max(1,len(news))),'status':'qualified_observed_RSS_proxy','evidence':news,'covered_sources':sorted(active),'observed_items':len(news)}
    return values
def prepare(root=ROOT):
    policy=guard(root);now=datetime.now(timezone.utc)
    original=cohort.frozen.read_rows(root/'data/direction_v42/forward_registry.csv',cohort.frozen.FCOLS)
    times,_=cohort.frozen.read_csv(root/'data/hourly_research/ohlc.csv',cohort.frozen.HOUR)
    close=datetime.fromtimestamp((times[-1]+cohort.frozen.HOUR)/1000,timezone.utc)
    result={'prepared_at_utc':now.isoformat(),'feature_cutoff_utc':close.isoformat(),'starting_ids':sorted(original),
        'features':feature_values(root,now),'protocol_id':policy['protocol_id'],'preparation_is_not_prediction':True}
    folder=root/BASE;folder.mkdir(parents=True,exist_ok=True);(folder/'pending_prepare.json').write_bytes((sources.canon(result)+'\n').encode())
    return result
def bind(root=ROOT):
    policy=guard(root);now=datetime.now(timezone.utc);old=chain(root)
    if now<utc(policy['effective_from_utc']):
        cohort.publication.command(root,'add','-f','--',BASE)
        return {'status':'WAIT_ACTIVATION','new_contexts':0}
    prepared=json.loads((root/BASE/'pending_prepare.json').read_bytes());prepared_sha=digest(prepared)
    original=cohort.frozen.read_rows(root/'data/direction_v42/forward_registry.csv',cohort.frozen.FCOLS)
    pairs={r['production']['forecast_id']:r for r in cohort.history_guard(root)}
    prior={r['forecast_id'] for r in old};new=[]
    for fid,row in original.items():
        if int(row['horizon_hours']) not in (1,24):continue
        if fid in prepared['starting_ids'] or fid in prior:continue
        issued=utc(row['issued_at_utc'])
        if issued<utc(policy['effective_from_utc']):continue
        if not utc(prepared['prepared_at_utc'])<=issued<=now or (now-issued).total_seconds()>30:raise ValueError('feature decision late or preparation after issue')
        pair=pairs.get(fid)
        if not pair or pair['production']!=row:raise ValueError('new original lacks genuine prepared pair')
        if pair['feature_cutoff_utc']!=prepared['feature_cutoff_utc']:raise ValueError('feature cutoff differs from original')
        variants={g:variant(pair['challenger']['p_up'],prepared['features'][g]['value']) for g in GROUPS}
        r={'schema_version':1,'forecast_id':fid,'original':row,'base_pair_sha256':pair['record_sha256'],
            'baseline_p_up':pair['challenger']['p_up'],'baseline_direction':pair['challenger']['direction'],
            'prepared_sha256':prepared_sha,'prepared_at_utc':prepared['prepared_at_utc'],
            'decision_recorded_at_utc':now.isoformat(),'variants':variants,
            'previous_sha256':new[-1]['record_sha256'] if new else old[-1]['record_sha256'] if old else None,'real_orders_authorized':False}
        r['record_sha256']=digest(r);new.append(r)
    if new:
        folder=root/BASE/'prepared';folder.mkdir(parents=True,exist_ok=True);(folder/(prepared_sha+'.json')).write_bytes((sources.canon(prepared)+'\n').encode())
        folder=root/BASE/'records';folder.mkdir(parents=True,exist_ok=True)
        with (root/BASE/'contexts.jsonl').open('ab') as stream:
            for r in new:
                stream.write((sources.canon(r)+'\n').encode());(folder/(r['forecast_id']+'.json')).write_bytes((sources.canon(r)+'\n').encode())
        cohort.publication.command(root,'add','-f','--',BASE)
    return {'status':'ok','new_contexts':len(new),'missing_features_retained':True}
def verify_record(root,row,original,proof):
    if row['original']!=original:raise ValueError('feature original changed')
    public=proof['publication'];head=public['head_sha'];at=utc(public['observed_public_utc']);issued=utc(original['issued_at_utc'])
    if not 0<=(utc(row['decision_recorded_at_utc'])-issued).total_seconds()<=30:raise ValueError('late feature decision')
    if not utc(row['decision_recorded_at_utc'])<=at<utc(original['target_utc']) or at>issued+timedelta(minutes=45):raise ValueError('feature not public before target/deadline')
    raw=(root/BASE/'prepared'/(row['prepared_sha256']+'.json')).read_bytes();prepared=json.loads(raw)
    if digest(prepared)!=row['prepared_sha256'] or utc(prepared['prepared_at_utc'])>issued:raise ValueError('prepared features changed/future')
    for g in GROUPS:
        data=prepared['features'][g]
        for evidence in data['evidence']:
            if utc(evidence['usable_from_utc'])>utc(prepared['prepared_at_utc']):raise ValueError('future feature availability')
        if row['variants'][g]!=variant(row['baseline_p_up'],data['value']):raise ValueError('variant not reproducible')
    for path,expected in [(BASE+'/records/'+row['forecast_id']+'.json',(root/BASE/'records'/(row['forecast_id']+'.json')).read_bytes()),(BASE+'/prepared/'+row['prepared_sha256']+'.json',raw)]:
        if cohort.publication.command(root,'show',head+':'+path)!=expected:raise ValueError('feature record/preparation absent at original server witness')
    return public
def publication(root=ROOT):
    guard(root);chain(root)
    paths=[p for p in (root/BASE).rglob('*') if p.is_file() and p.name!='failure_latest.json']
    expected={p.relative_to(root).as_posix():p.read_bytes() for p in paths}
    if not cohort.publication.verify_readback(root,'FETCH_HEAD',expected):raise ValueError('feature remote readback mismatch')
    return {'status':'verified_remote_feature_readback','verified_files':len(expected)}
def audit(root=ROOT):
    policy=guard(root);now=datetime.now(timezone.utc);records=chain(root)
    closed=max(0,int((now-utc(policy['effective_from_utc'])-timedelta(minutes=45)).total_seconds()//3600)+1)
    result={'schema_version':1,'version_id':policy['protocol_id'],'audited_at_utc':now.isoformat(),'status':'WAIT_ACTIVATION' if now<utc(policy['effective_from_utc']) else 'ACTIVE_RESEARCH_SHADOW',
        'registered_contexts':len(records),'tracks':{},'rejected':{},'real_orders_authorized':False,'validated_70pct':False,'profitability_validated':False,
        'expected_closed_hourly_source_slots_each_horizon':closed,'no_contexts_is_not_zero_missing':True,
        'missing_context_closed_slots_if_no_records_each_horizon':closed,
        'accuracy_denominator':'Issued original due nonoverlap events; absent issuer hours are separate coverage failures, never fabricated predictions or outcome labels',
        'missing_issuer_hours_can_never_be_hidden_by_feature_conditional_accuracy':True,
        'inference':'Descriptive only; fixed future review dates; no automatic sequential p-value stopping, no dependency-robust proof yet'}
    if not records:
        (root/BASE).mkdir(parents=True,exist_ok=True);(root/BASE/'audit_latest.json').write_bytes((json.dumps(result,indent=2)+'\n').encode());return result
    original=cohort.frozen.read_rows(root/'data/direction_v42/forward_registry.csv',cohort.frozen.FCOLS)
    scores=cohort.frozen.read_rows(root/'data/direction_v42/forward_scores.csv',cohort.frozen.SCOLS)
    operations=json.loads((root/'data/p0_operations/latest.json').read_bytes())
    if operations.get('status')!='ok' or not 0<=(now-utc(operations['audited_at_utc'])).total_seconds()<=900:raise ValueError('feature source proof stale')
    publics={};pairmap={r['production']['forecast_id']:r for r in cohort.history_guard(root)}
    bindings=cohort.frozen.read_rows(root/cohort.quotes.BINDINGS,cohort.quotes.BCOLS)
    for r in records:
        try:
            fid=r['forecast_id'];pair=pairmap[fid]
            if pair['record_sha256']!=r['base_pair_sha256'] or pair['challenger']['p_up']!=r['baseline_p_up'] or pair['challenger']['direction']!=r['baseline_direction']:raise ValueError('feature base paired forecast differs')
            paired_audit.verify_pair(root,pair,original[fid],scores.get(fid),operations['forecast_proofs'][fid],bindings)
            publics[fid]=verify_record(root,r,original[fid],operations['forecast_proofs'][fid])
        except Exception as e:result['rejected'][r['forecast_id']]=str(e)
    economics.guard(root)
    books=list(economics.event_books(root,economics.history_guard(root)))
    for group in GROUPS:
        for h in (1,24):
            byid={r['forecast_id']:r for r in records}
            source_rows=[r for r in original.values() if int(r['horizon_hours'])==h and utc(r['issued_at_utc'])>=utc(policy['effective_from_utc'])]
            rows=[byid.get(r['forecast_id'],{'forecast_id':r['forecast_id'],'original':r,'record_sha256':None,
                'variants':{g:variant(.5,None) for g in GROUPS}}) for r in source_rows]
            entries=[{'forecast_id':r['forecast_id'],'issued_at_utc':r['original']['issued_at_utc'],'target_utc':r['original']['target_utc'],'record':r} for r in rows]
            chosen=paired_audit.nonoverlap(entries);valid=[];due=[]
            for e in chosen:
                r=e['record'];fid=r['forecast_id'];s=scores.get(fid)
                if utc(e['target_utc'])+timedelta(minutes=25)>now:continue
                due.append(r)
                if fid in publics and r['variants'][group]['status']=='ISSUED' and s and s['status']=='scored':valid.append((r,s))
            correct=sum(r['variants'][group]['direction']==s['realized_up'] for r,s in valid)
            comps={}
            for name,col in list(paired_audit.COMPARATORS.items())+[('base_paired',None)]:
                wins=losses=0
                for r,s in valid:
                    older=r['baseline_direction'] if name=='base_paired' else 'UP' if name=='always_up' else 'DOWN' if name=='always_down' else r['original'][col]
                    new=r['variants'][group]['direction'];y=s['realized_up'];wins+=int(new==y and older!=y);losses+=int(new!=y and older==y)
                comps[name]={'wins':wins,'losses':losses,'p_descriptive':paired_audit.statistics.exact_mcnemar(wins,losses),'promotion_authorized':False}
            items=[]
            for e in entries:
                r=e['record'];public=publics.get(r['forecast_id']);v=r['variants'][group]
                items.append({'forecast_id':r['forecast_id'],'horizon_hours':h,'issued_at_utc':e['issued_at_utc'],'target_utc':e['target_utc'],
                    'paper_action':v['paper_action'],'observed_public_utc':public['observed_public_utc'] if public else None,
                    'source_verified':public is not None and v['status']=='ISSUED','pair_sha256':r['record_sha256']})
            eligible={i['forecast_id']:i for i in items if i['source_verified']}
            stream=[]
            for book in books:
                b=dict(book);at_capture=book.get('eligible_sources',{})
                b['eligible_sources']={fid:i for fid,i in eligible.items() if utc(i['issued_at_utc'])<=utc(b['captured_at_utc'])
                    and at_capture.get(fid,{}).get('source_verified') is True
                    and at_capture[fid]['observed_public_utc']==i['observed_public_utc']
                    and at_capture[fid]['source_witness_head_sha']==publics[fid]['head_sha']}
                stream.append(b)
            paper=economics.replay(items,stream,now)[str(h)+'h']
            controls=[dict(i,paper_action=pairmap[i['forecast_id']]['decision']['paper_action']) if i['forecast_id'] in pairmap else dict(i,source_verified=False,paper_action='NO_TRADE') for i in items]
            controlmap={i['forecast_id']:i for i in controls}
            controlstream=[dict(b,eligible_sources={fid:controlmap[fid] for fid in b['eligible_sources']}) for b in stream]
            controlpaper=economics.replay(controls,controlstream,now)[str(h)+'h']
            result['tracks'][group+'_'+str(h)+'h']={'due_reserved':len(due),'qualified':len(valid),'missing':len(due)-len(valid),
                'accuracy_pct':100*correct/len(valid) if valid else None,'accuracy_missing_as_wrong_pct':100*correct/len(due) if due else None,
                'brier':sum((r['variants'][group]['p_up_raw_proxy']-int(s['realized_up']=='UP'))**2 for r,s in valid)/len(valid) if valid else None,
                'no_trade_in_accuracy':sum(r['variants'][group]['paper_action']=='NO_TRADE' for r,s in valid),'comparators':comps,'paper':paper,'paired_control_same_qualified_sources_paper':controlpaper,
                'full_source_forecast_count':len(rows),'registered_context_count':sum(r['forecast_id'] in byid for r in rows),
                'original_closed_hour_slots':len({int((utc(r['issued_at_utc'])-utc(policy['effective_from_utc'])).total_seconds()//3600) for r in source_rows if utc(r['issued_at_utc'])<=now-timedelta(minutes=45)}),
                'context_closed_hour_slots':len({int((utc(r['original']['issued_at_utc'])-utc(policy['effective_from_utc'])).total_seconds()//3600) for r in rows if r['forecast_id'] in byid and utc(r['original']['issued_at_utc'])<=now-timedelta(minutes=45)}),
                'probability_calibration_validated':False,'economic_gain_verified':False}
    (root/BASE/'audit_latest.json').write_bytes((json.dumps(result,indent=2)+'\n').encode());return result
if __name__=='__main__':
    try:
        mode=sys.argv[1];result=prepare() if mode=='prepare' else bind() if mode=='bind' else audit() if mode=='audit' else publication() if mode=='publication' else None
        if result is None:raise ValueError('unknown feature mode')
        print(json.dumps({k:result.get(k) for k in ('status','new_contexts','registered_contexts','prepared_at_utc')}))
    except Exception as e:
        (ROOT/BASE).mkdir(parents=True,exist_ok=True);(ROOT/BASE/'failure_latest.json').write_bytes((json.dumps({'status':'BLOCKED','error':type(e).__name__+': '+str(e),'real_orders_authorized':False})+'\n').encode());raise
