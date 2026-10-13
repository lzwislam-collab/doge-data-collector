"""Future learned-candidate contracts. Pure draft issuance; no scheduler or orders.

Actual eligibility is derived from exact bytes and GitHub server clocks, never
from verified=True in an input file. Repository history is not external WORM.
"""
import hashlib
import json
import math
import csv
import io
from pathlib import Path
from datetime import datetime,timedelta,timezone

import numpy as np
import phase1_research as research
import forward_execution_evidence as depth
import factor_evaluation_v1 as evaluation

REPO='lzwislam-collab/doge-data-collector'
WORKFLOW='.github/workflows/learned_shadow_v1.yml'
POLICY='config/learned_shadow_v1.json'
PACKAGE='research/learned_shadow_v1/model_package.json'
ROLLING='research/learned_shadow_v1/rolling.json'
REGISTRY='system/learned_shadow_v1_registry.json'
TRAIN_POLICY='config/phase1_research_v1.json'
URLS={'ticker':'https://www.okx.com/api/v5/market/ticker?instId=DOGE-USDT',
      'book':'https://www.okx.com/api/v5/market/books?instId=DOGE-USDT&sz=400'}
CODE_FILES=('learned_shadow_v1.py','learned_shadow_bundle.py','phase1_research.py','phase1_research_audit.py',
            'factor_evaluation_v1.py','forward_execution_evidence.py','direction_forward_v42.py','direction_baseline_v42.py')
REGISTERED_FILES=set(CODE_FILES)|{POLICY,PACKAGE,ROLLING,REGISTRY,TRAIN_POLICY}


def utc(value):return evaluation.utc(value)
def sha(raw):return hashlib.sha256(raw).hexdigest()
def encode(value):return research.encode(value)
def digest(value):return sha(encode(value))


def check_policy(p):
    if (p['groups']!=list(research.variants()) or p['horizons_hours']!=[1,24]
        or p['family_size']!=1080 or p['automatic_promotion'] is not False
        or p['max_issue_delay_minutes']!=45 or p['target_window_minutes']!=25):
        raise ValueError('frozen future candidate family changed')
    utc(p['effective_from_utc'])
    if p['slot_steps_hours']!={'1':2,'24':25}:raise ValueError('nonoverlap slots changed')
    for key in ('fee_per_side','slippage_per_side','notional_usdt','stress_multiplier'):
        v=p[key]
        if isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or v<=0:
            raise ValueError('invalid cost policy')
    return p


def target_deadline(e,p):
    return min(utc(e['target_utc'])+timedelta(minutes=p['target_window_minutes']),
               utc(e['slot_utc'])+timedelta(hours=p['slot_steps_hours'][str(e['horizon_hours'])]))


def quote(packet):
    requested,captured=utc(packet['requested_at_utc']),utc(packet['captured_at_utc'])
    if not 0<=(captured-requested).total_seconds()<=20:raise ValueError('invalid request chronology')
    raw=packet['raw'];ticker=raw['ticker']
    if (packet.get('source_urls')!=URLS or set(raw)!=set(URLS)
        or set(packet.get('raw_text',{}))!=set(URLS) or set(packet.get('raw_sha256',{}))!=set(URLS)):
        raise ValueError('complete fixed OKX HTTP origins and original bytes required')
    for key,text in packet['raw_text'].items():
        if sha(text.encode())!=packet['raw_sha256'][key] or json.loads(text)!=raw[key]:
            raise ValueError('raw HTTP bytes changed')
    if ticker.get('code')!='0' or len(ticker.get('data',[]))!=1:raise ValueError('bad ticker')
    r=ticker['data'][0];qt=datetime.fromtimestamp(int(r['ts'])/1000,timezone.utc)
    price=float(r['last'])
    if r['instId']!='DOGE-USDT' or not math.isfinite(price) or price<=0 or not 0<=(captured-qt).total_seconds()<=120:
        raise ValueError('invalid/future/stale ticker')
    book=None;book_error=None
    if raw.get('book') is not None:
        try:
            book=depth.validate_book(raw['book'],requested,captured)
            book.update({side:raw['book']['data'][0][side] for side in ('asks','bids')})
        except ValueError as exc:
            if 'insufficient visible depth' not in str(exc):raise
            book_error=str(exc)
    else:book_error='raw book unavailable'
    return {'price':price,'at':qt,'captured':captured,'book':book,'book_error':book_error,'packet_sha256':digest(packet)}


def causal_features(frame,at):
    last=int(at.timestamp()*1000)//3600000*3600000-3600000
    ts=frame['times']
    if len(ts)!=25 or ts!=[last-(24-i)*3600000 for i in range(25)]:
        raise ValueError('exact latest 25 confirmed hours required')
    for symbol in ('DOGE-USDT','BTC-USDT'):
        r=frame[symbol]
        if any(len(r[k])!=25 for k in ('close','volume','seen')):raise ValueError('input lengths differ')
        for i in range(25):
            c,v=r['close'][i],r['volume'][i]
            seen=r['seen'][i]
            if (isinstance(c,bool) or isinstance(v,bool) or not math.isfinite(c) or not math.isfinite(v)
                or c<=0 or v<0 or seen is None or not ts[i]+3600000<=seen<=int(at.timestamp()*1000)):
                raise ValueError('invalid or unavailable feature receipt')
    d=np.log(frame['DOGE-USDT']['close']);b=np.log(frame['BTC-USDT']['close']);v=frame['DOGE-USDT']['volume']
    mean=float(np.mean(v[:-1]))
    if mean<=0:raise ValueError('empty volume basis')
    x=[d[-1]-d[-2],d[-1]-d[-5],d[-1]-d[0],float(np.std(np.diff(d))),
       float(np.max(d)-np.min(d)),v[-1]/mean-1,b[-1]-b[-2],b[-1]-b[-5],b[-1]-b[0]]
    return research.canonical([float(v) for v in x])


def frame_from_csv(raw,at):
    at=utc(at);last=int(at.timestamp()*1000)//3600000*3600000-3600000
    wanted=[last-(24-i)*3600000 for i in range(25)]
    keys=set();series={s:{} for s in ('DOGE-USDT','BTC-USDT')}
    for r in csv.DictReader(io.StringIO(raw.decode('utf-8-sig'))):
        symbol=r['symbol']
        if symbol not in series:continue
        t=int(r['open_utc_ms']);key=(symbol,t)
        if key in keys:raise ValueError('duplicate source bar')
        keys.add(key)
        if t not in wanted:continue
        if r['source']!='OKX history-candles 1H confirmed':raise ValueError('source changed')
        series[symbol][t]=(float(r['close']),float(r['volume_base']),int(utc(r['first_ingested_utc']).timestamp()*1000))
    if any(set(series[s])!=set(wanted) for s in series):raise ValueError('source frame missing')
    f={'times':wanted,**{s:{'close':[series[s][t][0] for t in wanted],
        'volume':[series[s][t][1] for t in wanted],'seen':[series[s][t][2] for t in wanted]} for s in series}}
    causal_features(f,at);return f


def issue(p,package,frame,packet,at,h):
    check_policy(p);at=utc(at);start=utc(p['effective_from_utc'])
    if h not in p['horizons_hours'] or at<start:raise ValueError('before activation or unregistered horizon')
    step=p['slot_steps_hours'][str(h)]*3600
    slot=start+timedelta(seconds=int((at-start).total_seconds()//step)*step)
    if not slot<=at<slot+timedelta(minutes=45):raise ValueError('outside fixed issue window')
    if utc(package['trained_at_utc'])>=at:raise ValueError('model not frozen before issuance')
    q=quote(packet)
    if not q['captured']<=at or (at-q['captured']).total_seconds()>30:raise ValueError('quote not known/fresh at issuance')
    x=causal_features(frame,at);probs={}
    if set(package['models'][str(h)])!=set(p['groups']):raise ValueError('model family incomplete')
    for name,groups in research.variants().items():
        model=package['models'][str(h)][name];columns=[i for g in groups for i in research.GROUPS[g]]
        if model['columns']!=columns:raise ValueError('registered feature columns changed')
        probs[name]=research.predict(model,[[x[i] for i in columns]])[0]
    d=frame['DOGE-USDT']['close'];b=frame['BTC-USDT']['close']
    event={'schema_version':1,'event_id':str(int(slot.timestamp()))+'_'+str(h),
           'protocol_id':p['protocol_id'],'slot_utc':slot.isoformat(),'issued_at_utc':at.isoformat(),
           'target_utc':(at+timedelta(hours=h)).isoformat(),'horizon_hours':h,
           'model_package_sha256':digest(package),'policy_sha256':digest(p),
           'frame':frame,'entry_packet':packet,'features':x,'reference_price':q['price'],
           'candidate_p':probs,'frozen_direction':'UP' if d[-1]<=d[-1-h] else 'DOWN',
           'btc_direction':'UP' if b[-1]>b[-1-h] else 'DOWN','paired_p':package['majority_p'][str(h)],
           'regime':'UP' if d[-1]>d[0] else 'DOWN',
           'baseline_kind':'frozen_rule_replay_not_original_production_issuance',
           'status':'DRAFT_UNWITNESSED','public_eligibility_verified':False,
           'real_orders_authorized':False,'promotion_authorized':False}
    return research.canonical(event)


def paper_net(entry,exit,p,multiplier):
    if entry['book'] is None or exit['book'] is None:raise ValueError('same-event sufficient raw depth missing')
    a=depth.sweep(entry['book']['asks'],p['notional_usdt'],cash=True)
    b=depth.sweep(exit['book']['bids'],a['units'])
    fee=p['fee_per_side']*multiplier;slip=p['slippage_per_side']*multiplier
    if fee>=1 or slip>=1:raise ValueError('costs outside paper range')
    return b['notional_usdt']*(1-fee)*(1-slip)/(a['notional_usdt']*(1+fee)*(1+slip))-1


def score(event,packet,p):
    check_policy(p);entry=quote(event['entry_packet']);exit=quote(packet);target=utc(event['target_utc'])
    if (not target<=exit['at']<=exit['captured']<target_deadline(event,p)
        or not target<=utc(packet['requested_at_utc'])<=exit['captured']):
        raise ValueError('outside prospective target quote window')
    # Book and price are separately timed; entry/exit are indicative snapshots,
    # not continuous fills, no future reconstructed OHLC target is accepted.
    economics={};errors={}
    try:
        if exit['book'] is None:raise ValueError('raw target depth missing or insufficient')
        book_at=datetime.fromtimestamp(exit['book']['ts_ms']/1000,timezone.utc)
        if not target<=book_at<=exit['captured']<target_deadline(event,p):raise ValueError('exit book outside target window')
        nominal=paper_net(entry,exit,p,1);stress=paper_net(entry,exit,p,p['stress_multiplier'])
        paired_up=event['paired_p']>=.5
        for name,prob in event['candidate_p'].items():
            n=nominal if prob>=.5 else 0.;s=stress if prob>=.5 else 0.
            economics[name]={'paired_net':n-(nominal if paired_up else 0.),'cash_net':n,'hold_net':n-nominal,
                'paired_stress_net':s-(stress if paired_up else 0.),'cash_stress_net':s,'hold_stress_net':s-stress}
    except ValueError as exc:
        economics={name:None for name in p['groups']};errors={'economics':str(exc)}
    return research.canonical({'event_id':event['event_id'],'event_sha256':digest(event),
        'observed_at_utc':exit['captured'].isoformat(),'target_packet':packet,
        'y':int(exit['price']>entry['price']),'economic_deltas':economics,'cost_errors':errors,
        'actual_fill_verified':False,'real_orders_authorized':False})


def due_slots(p,at,h):
    check_policy(p);at=utc(at);slot=utc(p['effective_from_utc']);answer=[]
    while slot+timedelta(hours=h,minutes=70)<=at:
        answer.append(slot);slot+=timedelta(hours=p['slot_steps_hours'][str(h)])
    return answer


def server_witness(files,runs,read_at,before,after=None):
    """Only successful trusted workflow clocks and exact server commit bytes."""
    if not files:return None
    before=utc(before);after=utc(after) if after is not None else None
    for r in sorted(runs,key=lambda x:x.get('created_at','')):
        try:
            created,finished=utc(r['created_at']),utc(r['updated_at']);head=r['head_sha']
            if (r['repository']['full_name']!=REPO or r['path'].split('@')[0]!=WORKFLOW
                or r['status']!='completed' or r['conclusion']!='success'
                or r['event'] not in ('push','workflow_dispatch') or len(head)!=40
                or any(c not in '0123456789abcdef' for c in head)
                or not created<=finished<before or after is not None and created<after):continue
            if all(read_at(head,path)==raw for path,raw in files.items()):
                return {'run_id':r['id'],'head_sha':head,'created_at':r['created_at'],'completed_by':r['updated_at']}
        except (KeyError,ValueError,TypeError,OSError):continue
    return None


def registration_files_valid(p,package,files):
    try:
        if set(files)!=REGISTERED_FILES:return False
        if files[POLICY]!=encode(p) or files[PACKAGE]!=encode(package):return False
        registry=json.loads(files[REGISTRY]);expected=registry['file_sha256_lf']
        if set(expected)!=REGISTERED_FILES-{REGISTRY}:return False
        if any(sha(files[name].replace(b'\r\n',b'\n'))!=value for name,value in expected.items()):return False
        root=Path(__file__).resolve().parent
        if any(files[name].replace(b'\r\n',b'\n')!=(root/name).read_bytes().replace(b'\r\n',b'\n') for name in CODE_FILES):return False
        if registry['version_id']!=p['protocol_id'] or not utc(package['trained_at_utc'])<utc(p['effective_from_utc']):return False
        rolling=json.loads(files[ROLLING]);reported=rolling.pop('result_sha256')
        if reported!=digest(rolling) or package['rolling_artifact_sha256']!=sha(files[ROLLING]):return False
        if rolling['identity']['market_input_sha256']!=package['training_input_sha256']:return False
        if rolling['identity']['research_policy_sha256']!=sha(files[TRAIN_POLICY]):return False
        if rolling['horizons']['1']['test_events']<1 or rolling['horizons']['24']['test_events']<1:return False
        if package['training_availability_scope']!='all_input_and_target_receipts_known_at_training_not_historical_PIT':return False
        return True
    except (KeyError,ValueError,OSError,TypeError):return False


def target_witness(e,s,runs,read_at,p):
    eligible=[]
    for run in runs:
        try:
            if utc(run['updated_at'])>=utc(s['observed_at_utc']):eligible.append(run)
        except (KeyError,ValueError,TypeError):continue
    return server_witness({'data/learned_shadow_v1/scores/'+e['event_id']+'.json':encode(s)},
                          eligible,read_at,target_deadline(e,p),utc(e['target_utc']))


def audit_records(p,events,scores,now):
    """Local structural summary only. No caller-supplied witness flags admitted."""
    check_policy(p);now=utc(now);emap={};smap={}
    for e in events:
        key=e['event_id']
        if key in emap:raise ValueError('duplicate event')
        h=e['horizon_hours'];issued=utc(e['issued_at_utc']);slot=utc(e['slot_utc'])
        if (h not in (1,24) or utc(e['target_utc'])!=issued+timedelta(hours=h) or issued>now
            or not slot<=issued<slot+timedelta(minutes=45) or slot<utc(p['effective_from_utc'])):
            raise ValueError('invalid event chronology')
        if key!=str(int(slot.timestamp()))+'_'+str(h) or e['policy_sha256']!=digest(p):
            raise ValueError('event identity/protocol changed')
        if (slot-utc(p['effective_from_utc'])).total_seconds()%(p['slot_steps_hours'][str(h)]*3600):
            raise ValueError('slot phase changed')
        if set(e['candidate_p'])!=set(p['groups']):raise ValueError('missing candidates')
        if any(isinstance(v,bool) or not math.isfinite(v) or not 0<=v<=1 for v in e['candidate_p'].values()):
            raise ValueError('invalid probability')
        emap[key]=e
    for s in scores:
        key=s['event_id']
        if key in smap or key not in emap:raise ValueError('duplicate/orphan score')
        if s['event_sha256']!=digest(emap[key]) or score(emap[key],s['target_packet'],p)!=s:
            raise ValueError('score replay mismatch')
        if utc(s['observed_at_utc'])>now:raise ValueError('future score')
        smap[key]=s
    result={'protocol_id':p['protocol_id'],'audited_at_utc':now.isoformat(),'horizons':{},
            'promotion_authorized':False,'validated_70pct':False,'real_orders_authorized':False,
            'production_predictions_modified':False,'external_immutable_retention_verified':False}
    for h in (1,24):
        slots=due_slots(p,now,h);ids={str(int(s.timestamp()))+'_'+str(h) for s in slots}
        issued=ids&set(emap);scored=ids&set(smap)
        result['horizons'][str(h)]={'expected':len(ids),'issued':len(issued),'missing_issuance':len(ids-issued),
            'target_scored_locally':len(scored),'missing_target':len(issued-scored),
            'witness_qualified':0,'status':'WAIT_INDEPENDENT_BYTES_AND_SERVER_WITNESSES',
            'raw_cost_complete':sum(all(smap[k]['economic_deltas'][g] is not None for g in p['groups']) for k in scored)}
    return result


def verified_rows(p,package,events,scores,files,runs,read_at,now):
    """Bridge to existing inference only after model, source and event witnesses.

    Input source snapshot must be server-witnessed BEFORE issuance; forecast
    exact bytes must be successful/public BEFORE target. Raw HTTPS captures are
    collector observations, not exchange signatures or authenticated fills.
    """
    audit_records(p,events,scores,now)
    registration=(server_witness(files,runs,read_at,utc(p['effective_from_utc']))
                  if registration_files_valid(p,package,files) else None)
    answer={};rejected={};scores={s['event_id']:s for s in scores}
    for e in events:
        key=e['event_id']
        try:
            if not registration:raise ValueError('future registration witness missing')
            if set(e['entry_packet'].get('raw_text',{}))!={'ticker','book'}:
                raise ValueError('original HTTP response bytes missing')
            if e['model_package_sha256']!=digest(package):raise ValueError('model package identity changed')
            replay=issue(p,package,e['frame'],e['entry_packet'],utc(e['issued_at_utc']),e['horizon_hours'])
            if any(e[k]!=v for k,v in replay.items()):raise ValueError('prediction replay mismatch')
            source_path=e['input_source_path'];source_raw=read_at(e['input_source_commit'],source_path)
            if source_path!='data/hourly_research/ohlc.csv':raise ValueError('source path not registered')
            if sha(source_raw)!=e['input_source_sha256']:raise ValueError('input source bytes changed')
            if frame_from_csv(source_raw,utc(e['issued_at_utc']))!=e['frame']:
                raise ValueError('feature frame does not match server-source bars')
            if not server_witness({source_path:source_raw},runs,read_at,utc(e['issued_at_utc'])):
                raise ValueError('source availability lacks pre-issuance server witness')
            event_path='data/learned_shadow_v1/events/'+key+'.json'
            if not server_witness({**files,event_path:encode(e)},runs,read_at,utc(e['target_utc']),utc(e['issued_at_utc'])):
                raise ValueError('forecast lacks pre-target public server witness')
            s=scores.get(key)
            if s is not None:
                if set(s['target_packet'].get('raw_text',{}))!={'ticker','book'}:
                    raise ValueError('original target HTTP response bytes missing')
                if not target_witness(e,s,runs,read_at,p):
                    raise ValueError('target raw packet lacks in-window exact server publication witness')
            for group in p['groups']:
                prob=e['candidate_p'][group]
                answer.setdefault((e['horizon_hours'],group),[]).append({'event_id':key,
                    'horizon_hours':e['horizon_hours'],'group':group,'issued_at_utc':e['issued_at_utc'],
                    'target_utc':e['target_utc'],'candidate_p':prob,'candidate_direction':'UP' if prob>=.5 else 'DOWN',
                    'paired_p':e['paired_p'],'paired_direction':'UP' if e['paired_p']>=.5 else 'DOWN',
                    'frozen_direction':e['frozen_direction'],'regime':e['regime'],'y':s['y'] if s else None,
                    'paper_action':'PAPER_LONG' if prob>=.5 else 'NO_TRADE',
                    'economic_deltas':s['economic_deltas'][group] if s else None,
                    'rolling_validation_verified':True})
        except (KeyError,ValueError,OSError,TypeError) as exc:rejected[key]=str(exc)
    return registration,answer,rejected
