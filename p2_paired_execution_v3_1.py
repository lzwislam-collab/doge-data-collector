"""Independent prospective selective spot PAPER ledger. Never orders or fills.

Separate horizons, first eligible nonoverlap reservations, original public
paired decisions, fresh after-public books, visible-depth sweeps, declared
costs, continuous hold/cash benchmarks and sampled (not continuous) drawdown.
"""
import hashlib
import json
import math
import os
import re
import sys
from collections import OrderedDict
from datetime import datetime,timedelta,timezone
from decimal import Decimal,ROUND_DOWN
from pathlib import Path
from urllib.request import Request,urlopen
import paired_forward_cohort as source
import paired_cohort_audit as paired_audit
import forward_execution_evidence as depth
import direction_forward_v42 as frozen
import quote_evidence_v2 as quote_source

ROOT=Path(__file__).resolve().parent
BASE='data/p2_paired_execution_v3_1'
EVENTS=BASE+'/books.jsonl'
POLICY='data/stage5/p2_paired_execution_v3_1_protocol.json'
SYSTEM='system/versions/DOGE_P2_PAIRED_EXECUTION_V3_1_20261013_0000.json'
CODE_FILES=('direction_baseline_v42.py', 'direction_forward_v42.py', 'forward_execution_evidence.py', 'forward_validation.py', 'forward_validation_v2.py', 'p1_adaptive_v2.py', 'p1_model_competition.py', 'p2_execution_registration_v3_1.py', 'p2_paired_execution_v3_1.py', 'paired_cohort_audit.py', 'paired_forward_cohort.py', 'paired_selection_v1.py', 'publish_forward_evidence.py', 'quote_capture_retry.py', 'quote_evidence_v2.py', 'spot_plus_quote_watch.py', 'stage5_70pct_audit.py', 'stage5_forward_quality.py')
INITIAL=1000.0
FEE=.001
SLIP=.0005
MAX_ENTRY_SECONDS=600
MAX_SPREAD_BPS=20
GAP_SECONDS=1800


def utc(s):return source.utc(s)
def digest(obj):return source.digest(obj)


def positive(x):
    x=float(x)
    if not math.isfinite(x) or x<=0:raise ValueError('invalid positive amount')
    return x


def cost(x):
    x=float(x)
    if not math.isfinite(x) or not 0<=x<.1:raise ValueError('invalid friction assumption')
    return x


def band(levels,buy_side):
    if not levels:raise ValueError('missing book levels')
    for row in levels:positive(row[0]);positive(row[1])
    best=positive(levels[0][0])
    return [r for r in levels if positive(r[0])<=best*1.01] if buy_side else [r for r in levels if positive(r[0])>=best*.99]


def buy(asks,cash,fee,slippage,extra_spread,lot_size=None,min_size=None):
    cash=positive(cash);fee=cost(fee);slippage=cost(slippage);extra_spread=cost(extra_spread)
    raw_budget=cash/((1+fee)*(1+slippage)*(1+extra_spread))
    sweep=depth.sweep(band(asks,True),raw_budget,cash=True)
    reserved_quantity=sweep['units']
    if lot_size is not None:
        lot=Decimal(str(positive(lot_size)));units=Decimal(str(sweep['units']))
        quantity=float((units/lot).to_integral_value(rounding=ROUND_DOWN)*lot)
        if quantity<=0 or quantity<float(min_size or lot):raise ValueError('paper size below instrument lot/minimum')
        sweep=depth.sweep(band(asks,True),quantity)
        reserved_quantity=quantity
    gross=sweep['notional_usdt'];spread=gross*extra_spread;slip=(gross+spread)*slippage
    fee_cash=(gross+spread+slip)*fee
    if gross+spread+slip+fee_cash>cash*(1+1e-12):raise ValueError('paper purchase exceeds cash')
    return {'quantity':reserved_quantity,'gross_usdt':gross,'vwap':sweep['vwap'],
        'cash_spent_usdt':gross+spread+slip+fee_cash,
        'costs':{'buy_fee_usdt':fee_cash,'entry_extra_slippage_usdt':slip,'entry_stress_extra_spread_usdt':spread},
        'actual_fill':False}


def sell(bids,quantity,fee,slippage,extra_spread,lot_size=None,min_size=None):
    quantity=positive(quantity);fee=cost(fee);slippage=cost(slippage);extra_spread=cost(extra_spread)
    if lot_size is not None:
        lot=Decimal(str(positive(lot_size)))
        if quantity<float(min_size or lot) or Decimal(str(quantity))%lot!=0:
            raise ValueError('position cannot fully liquidate under instrument lot/minimum')
    sweep=depth.sweep(band(bids,False),quantity)
    gross=sweep['notional_usdt'];spread=gross*extra_spread;slip=(gross-spread)*slippage;fee_cash=(gross-spread-slip)*fee
    return {'cash_usdt':gross-spread-slip-fee_cash,'gross_usdt':gross,'vwap':sweep['vwap'],
        'costs':{'sell_fee_usdt':fee_cash,'exit_extra_slippage_usdt':slip,'exit_stress_extra_spread_usdt':spread},'actual_fill':False}


def reserve(items):
    out=[];end=None
    for row in sorted(items,key=lambda x:(x['issued_at_utc'],x['forecast_id'])):
        issued=utc(row['issued_at_utc'])
        if end is None or issued>=end:
            out.append(row);end=utc(row['target_utc'])+timedelta(minutes=25)
    return out


class Account:
    def __init__(self,stress=False):
        self.cash=INITIAL;self.quantity=0.;self.stress=stress;self.started=False
        self.costs={};self.buy_legs=0;self.sell_legs=0;self.peak=INITIAL;self.dd=0.;self.last_value=INITIAL

    def parameters(self,book):
        extra=book['quote']['spread_bps']/20000 if self.stress else 0
        rules=book.get('instrument') or {}
        return FEE*(2 if self.stress else 1),SLIP*(2 if self.stress else 1),extra,rules.get('lot_size'),rules.get('min_size')

    def entry(self,book):
        if self.quantity:raise ValueError('paper cannot buy twice without exiting')
        leg=buy(book['asks'],self.cash,*self.parameters(book))
        self.cash=max(0.,self.cash-leg['cash_spent_usdt']);self.quantity=leg['quantity'];self.started=True;self.buy_legs+=1
        self.add_costs(leg['costs']);return leg

    def exit(self,book):
        if not self.quantity:return None
        leg=sell(book['bids'],self.quantity,*self.parameters(book))
        self.cash+=leg['cash_usdt'];self.quantity=0.;self.sell_legs+=1;self.add_costs(leg['costs']);return leg

    def add_costs(self,values):
        for k,v in values.items():self.costs[k]=self.costs.get(k,0)+v

    def value(self,book):
        return self.cash+(sell(book['bids'],self.quantity,*self.parameters(book))['cash_usdt'] if self.quantity else 0)

    def mark(self,book):
        value=self.value(book);self.peak=max(self.peak,value);self.dd=max(self.dd,(self.peak-value)/self.peak*100);self.last_value=value
        return value

    def summary(self,prefix_value):
        return {'initial_equity_usdt':INITIAL,'cash_usdt':self.cash,'open_quantity':self.quantity,
            'started':self.started,'buy_legs':self.buy_legs,'sell_legs':self.sell_legs,
            'prefix_liquidation_equity_usdt':prefix_value,
            'prefix_net_return_pct':(prefix_value/INITIAL-1)*100 if prefix_value is not None else None,
            'latest_available_liquidation_proxy_usdt':self.last_value,
            'sampled_drawdown_pct':self.dd,'recorded_trade_cost_components_usdt':dict(self.costs),
            'valuation_assumes_liquidation_costs':True,'actual_fill':False}


class Frame:
    def __init__(self,items):
        self.items=reserve(items);self.index=0;self.current=None;self.ledger=[]
        self.strategy=Account();self.stress=Account(True);self.hold=Account();self.stress_hold=Account(True)
        self.blocked_id=None;self.blocked_reason=None;self.last_quote=None;self.gaps=0;self.missing_marks=0
        self.last_wait=None;self.last_mark=None

    def block(self,item,reason):
        self.blocked_id=item['forecast_id'];self.blocked_reason=reason

    def expire(self,now):
        if self.blocked_id or self.index>=len(self.items):return
        item=self.items[self.index];target=utc(item['target_utc'])
        if self.current:
            if now>target+timedelta(minutes=25):self.block(item,'MISSING_EXIT_BOOK_IN_WINDOW')
        else:
            public=utc(item['observed_public_utc']) if item.get('observed_public_utc') else utc(item['issued_at_utc'])+timedelta(minutes=45)
            deadline=min(public+timedelta(seconds=MAX_ENTRY_SECONDS),target)
            if now>deadline:self.block(item,'MISSING_ENTRY_BOOK_OR_PUBLIC_PAIR_IN_WINDOW')

    def on_book(self,book):
        try:self.process_book(book)
        finally:
            # Continuous hold valuation must not disappear during cash/entry waits.
            if self.hold.started:
                try:
                    for account in (self.strategy,self.stress,self.hold,self.stress_hold):account.mark(book)
                    self.last_mark=utc(book['quote']['quote_utc'])
                except ValueError:self.missing_marks+=1

    def process_book(self,book):
        at=utc(book['captured_at_utc']);qt=utc(book['quote']['quote_utc']);self.expire(at)
        if self.last_quote and (qt-self.last_quote).total_seconds()>GAP_SECONDS and self.hold.started:self.gaps+=1
        self.last_quote=qt
        if not self.blocked_id and self.index<len(self.items):
            item=self.items[self.index];target=utc(item['target_utc'])
            if not self.current:
                if 'eligible_sources' in book:
                    captured_source=book['eligible_sources'].get(item['forecast_id'])
                    if not captured_source or not captured_source['source_verified']:
                        self.last_wait='WAIT_SOURCE_VERIFIED_AT_CAPTURE';return
                    if any(captured_source[k]!=item[k] for k in ('pair_sha256','paper_action','observed_public_utc','target_utc')):
                        raise ValueError('paper source context changed between capture and replay')
                if not item['source_verified'] or not item.get('observed_public_utc'):
                    self.last_wait='WAIT_PUBLIC_PAIR';return
                public=utc(item['observed_public_utc'])
                if qt<=public or utc(book['requested_at_utc'])<public or qt>=target:return
                if (qt-public).total_seconds()>MAX_ENTRY_SECONDS:return
                if book['quote']['spread_bps']>MAX_SPREAD_BPS:
                    self.last_wait='WAIT_ENTRY_SPREAD';return
                if item['paper_action'] not in ('PAPER_LONG','NO_TRADE'):raise ValueError('unknown precommitted paper action')
                # Test all needed purchases before mutating any account.
                needed=[self.hold,self.stress_hold] if not self.hold.started else []
                if item['paper_action']=='PAPER_LONG':needed+=[self.strategy,self.stress]
                try:
                    for account in needed:buy(book['asks'],account.cash,*account.parameters(book))
                    # Require observable immediate liquidation capacity as well.
                    for account in needed:
                        leg=buy(book['asks'],account.cash,*account.parameters(book));sell(book['bids'],leg['quantity'],*account.parameters(book))
                except ValueError:self.last_wait='WAIT_ENTRY_DEPTH';return
                entry={}
                for name,account in [('strategy',self.strategy),('double_cost',self.stress)]:
                    if item['paper_action']=='PAPER_LONG':entry[name]=account.entry(book)
                if not self.hold.started:self.hold.entry(book);self.stress_hold.entry(book)
                self.current={'forecast_id':item['forecast_id'],'entry_book_sha256':book['book_sha256'],
                    'entry_quote_utc':book['quote']['quote_utc'],'paper_action':item['paper_action'],
                    'pair_sha256':item['pair_sha256'],'entry_legs':entry}
                self.last_wait=None
            if self.current and qt>=target and at<=target+timedelta(minutes=25):
                if book['quote']['spread_bps']>MAX_SPREAD_BPS:self.last_wait='WAIT_EXIT_SPREAD'
                else:
                    try:
                        # Baseline and strategy must all be observable at the same quote.
                        for account in (self.strategy,self.stress,self.hold,self.stress_hold):account.value(book)
                    except ValueError:self.last_wait='WAIT_EXIT_DEPTH'
                    else:
                        legs={'strategy':self.strategy.exit(book),'double_cost':self.stress.exit(book)}
                        values={'strategy':self.strategy.mark(book),'double_cost':self.stress.mark(book),
                                'hold':self.hold.mark(book),'double_cost_hold':self.stress_hold.mark(book)}
                        row=dict(self.current,exit_book_sha256=book['book_sha256'],exit_quote_utc=book['quote']['quote_utc'],
                                 exit_legs=legs,equity_usdt=values,actual_fill=False)
                        self.ledger.append(row);self.current=None;self.index+=1;self.last_wait=None

    def summary(self,now):
        self.expire(now)
        tail_age=(now-self.last_mark).total_seconds() if self.last_mark else None
        tail_gap=bool(self.hold.started and (tail_age is None or tail_age>GAP_SECONDS))
        values=self.ledger[-1]['equity_usdt'] if self.ledger else {}
        strategy=self.strategy.summary(values.get('strategy'));hold=self.hold.summary(values.get('hold'))
        stress=self.stress.summary(values.get('double_cost'));stress_hold=self.stress_hold.summary(values.get('double_cost_hold'))
        n=len(self.ledger);due=sum(utc(i['target_utc'])+timedelta(minutes=25)<=now for i in self.items)
        difference=lambda x,y:None if x is None or y is None else x-y
        return {'selected_intervals':len(self.items),'matured_due_intervals':due,'completed_prefix_intervals':n,
            'completed_due_intervals':sum(utc(i['target_utc'])+timedelta(minutes=25)<=now for i in self.items[:n]),
            'blocked_forecast_id':self.blocked_id,'blocked_reason':self.blocked_reason,'waiting_reason':self.last_wait,
            'strategy':strategy,'hold':hold,'double_cost':stress,'double_cost_hold':stress_hold,
            'cash_net_return_pct':0,'excess_over_hold_pct_points':difference(strategy['prefix_net_return_pct'],hold['prefix_net_return_pct']),
            'excess_over_cash_pct_points':strategy['prefix_net_return_pct'],
            'double_cost_excess_over_hold_pct_points':difference(stress['prefix_net_return_pct'],stress_hold['prefix_net_return_pct']),
            'no_trade_completed':sum(x['paper_action']=='NO_TRADE' for x in self.ledger),
            'paper_long_completed':sum(x['paper_action']=='PAPER_LONG' for x in self.ledger),
            'path_gap_count':self.gaps+int(tail_gap),'tail_gap_seconds':tail_age if tail_gap else 0,
            'last_valid_mark_utc':self.last_mark.isoformat() if self.last_mark else None,
            'latest_mark_age_seconds':tail_age,'latest_mark_fresh':tail_age is not None and tail_age<=GAP_SECONDS,
            'tail_path_gap':tail_gap,'missing_mark_count':self.missing_marks,'continuous_drawdown_verified':False,
            'real_profit_validated':False,'ledger':self.ledger,'open_interval':self.current}


def replay(items,books,now):
    frames={h:Frame([r for r in items if int(r['horizon_hours'])==h]) for h in (1,24)}
    previous=None
    for book in books:
        qt=utc(book['quote']['quote_utc']);seen=utc(book['captured_at_utc']);requested=utc(book['requested_at_utc'])
        if previous and qt<=previous:raise ValueError('nonmonotonic replay book')
        if not requested<=seen<=now or not 0<=(seen-qt).total_seconds()<=30:raise ValueError('future/stale replay book')
        previous=qt
        for frame in frames.values():frame.on_book(book)
    return {str(h)+'h':frame.summary(now) for h,frame in frames.items()}


def code_hashes(root):return {p:hashlib.sha256((root/p).read_bytes().replace(b'\r\n',b'\n')).hexdigest() for p in CODE_FILES}


def guard(root):
    policy=json.loads((root/POLICY).read_text(encoding='utf-8'));registered=json.loads((root/SYSTEM).read_text(encoding='utf-8'))
    pointer=json.loads((root/'system/current_p2_execution_shadow_v3_1.json').read_text(encoding='utf-8'))
    if (registered['code_sha256_lf']!=code_hashes(root) or registered['protocol_sha256']!=hashlib.sha256((root/POLICY).read_bytes()).hexdigest()
        or registered.get('engineering_audit_pass') is not True or registered.get('real_orders_authorized') is not False
        or pointer.get('registry_sha256')!=hashlib.sha256((root/SYSTEM).read_bytes()).hexdigest()
        or pointer.get('registry_path')!=SYSTEM or pointer.get('version_id')!=policy['protocol_id']
        or pointer.get('real_orders_authorized') is not False or pointer.get('authorized_action')!='NO_TRADE'
        or registered.get('version_id')!=policy['protocol_id'] or registered.get('effective_from_utc')!=policy['effective_from_utc']):raise ValueError('P2 execution system/code integrity mismatch')
    if not policy['no_real_orders'] or not policy['no_backfill'] or not policy['no_dca_changes']:raise ValueError('P2 scope boundary changed')
    return policy


def read_events(root):
    path=root/EVENTS;rows=[];previous=None;last=None
    if not path.exists():return []
    for line in path.read_text(encoding='utf-8').splitlines():
        row=json.loads(line);check=dict(row);actual=check.pop('record_sha256')
        if actual!=digest(check) or row['previous_sha256']!=previous:raise ValueError('P2 event hash chain changed')
        stamp=utc(row['quote_utc'])
        if last and stamp<=last:raise ValueError('P2 event quote nonmonotonic')
        last=stamp;previous=actual;rows.append(row)
    return rows


def history_guard(root):
    current=read_events(root);previous=[];last_hash=None
    revisions=source.publication.command(root,'log','--reverse','--format=%H','--',EVENTS).decode().splitlines()
    for commit in revisions:
        patch=source.publication.command(root,'show','--format=','--unified=0','--no-renames',commit,'--',EVENTS).decode('utf-8')
        in_hunk=False
        for line in patch.splitlines():
            if line.startswith('@@'):
                m=re.match(r'@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@',line)
                if not m or int(m[2] or 1)!=0 or int(m[1])!=len(previous) or int(m[3])!=len(previous)+1:raise ValueError('P2 historic tail removed/revised')
                in_hunk=True;continue
            if in_hunk:
                if not line.startswith('+'):raise ValueError('P2 historic record modified')
                row=json.loads(line[1:]);check=dict(row);actual=check.pop('record_sha256')
                if actual!=digest(check) or row['previous_sha256']!=last_hash:raise ValueError('P2 historic event hash mismatch')
                previous.append(row);last_hash=actual
    if current[:len(previous)]!=previous:raise ValueError('P2 committed event tail removed/changed')
    return current


def instrument_rules(payload):
    if payload.get('code')!='0' or len(payload.get('data',[]))!=1:raise ValueError('instrument response invalid')
    item=payload['data'][0]
    if item.get('instId')!='DOGE-USDT' or item.get('instType')!='SPOT' or item.get('state')!='live':
        raise ValueError('instrument identity/state not live DOGE spot')
    for key in ('lotSz','minSz','tickSz'):positive(item[key])
    return {'lot_size':item['lotSz'],'min_size':item['minSz'],'tick_size':item['tickSz'],'state':'live','instId':'DOGE-USDT'}


def fetch_book():
    instrument_url='https://www.okx.com/api/v5/public/instruments?instType=SPOT&instId=DOGE-USDT'
    with urlopen(Request(instrument_url,headers={'User-Agent':'DOGE-P2-Paired-Execution/2'}),timeout=8) as response:instrument_raw=response.read(200_001)
    if len(instrument_raw)>200_000:raise ValueError('oversized instrument response')
    rules=instrument_rules(json.loads(instrument_raw));instrument_seen=datetime.now(timezone.utc)
    requested=datetime.now(timezone.utc)
    with urlopen(Request(depth.URL,headers={'User-Agent':'DOGE-P2-Paired-Execution/2'}),timeout=12) as response:raw=response.read(2_000_001)
    seen=datetime.now(timezone.utc)
    if len(raw)>2_000_000:raise ValueError('oversized fresh book')
    snapshot={'schema_version':1,'source_url':depth.URL,'requested_at_utc':requested.isoformat(),
              'captured_at_utc':seen.isoformat(),'raw_response_utf8':raw.decode('utf-8'),'real_fill':False,
              'instrument_source_url':instrument_url,'instrument_raw_response_utf8':instrument_raw.decode('utf-8'),
              'instrument_observed_at_utc':instrument_seen.isoformat(),'instrument':rules}
    payload=json.loads(raw);depth.validate_book(payload,requested,seen)
    return snapshot


def snapshot_book(snapshot,qid):
    payload=json.loads(snapshot['raw_response_utf8'])
    q=depth.validate_book(payload,utc(snapshot['requested_at_utc']),utc(snapshot['captured_at_utc']))
    data=payload['data'][0]
    rules=instrument_rules(json.loads(snapshot['instrument_raw_response_utf8']))
    if rules!=snapshot['instrument'] or not 0<=(utc(snapshot['requested_at_utc'])-utc(snapshot['instrument_observed_at_utc'])).total_seconds()<=30:
        raise ValueError('instrument context mismatch/future/stale')
    tick=Decimal(rules['tick_size'])
    if any(Decimal(str(level[0]))%tick!=0 for side in ('asks','bids') for level in data[side]):raise ValueError('book price not aligned to instrument tick')
    return {'requested_at_utc':snapshot['requested_at_utc'],'captured_at_utc':snapshot['captured_at_utc'],
            'quote':q,'asks':data['asks'],'bids':data['bids'],'book_sha256':qid,'instrument':rules}


def save_book(root,snapshot,engineering=False):
    body=(source.causal.canon(snapshot)+'\n').encode();qid=hashlib.sha256(body).hexdigest()
    directory='engineering_books' if engineering else 'snapshots'
    path=root/BASE/directory/qid[:2]/(qid+'.json');path.parent.mkdir(parents=True,exist_ok=True)
    if path.exists() and path.read_bytes()!=body:raise ValueError('P2 book hash collision/tamper')
    if not path.exists():
        with path.open('xb') as f:f.write(body)
    return qid,path.relative_to(root).as_posix()


def source_items(root,now,effective,prior_verified=None):
    source_policy=source.guard(root);pairs=source.history_guard(root)
    for path in ('data/direction_v42/forward_registry.csv',quote_source.BINDINGS,'data/p0_operations/latest.json','data/paired_forward_v1/audit_latest.json'):
        if not (root/path).exists():raise ValueError('required P2 source missing: '+path)
    original=frozen.read_rows(root/'data/direction_v42/forward_registry.csv',frozen.FCOLS)
    bindings=frozen.read_rows(root/quote_source.BINDINGS,quote_source.BCOLS)
    operations=json.loads((root/'data/p0_operations/latest.json').read_text(encoding='utf-8'))
    audit=json.loads((root/'data/paired_forward_v1/audit_latest.json').read_text(encoding='utf-8'))
    if (operations.get('status')!='ok' or audit.get('status')!='ok'
        or not 0<=(now-utc(operations['audited_at_utc'])).total_seconds()<=900
        or not 0<=(now-utc(audit['evaluated_at_utc'])).total_seconds()<=900):raise ValueError('P2 public pair evidence stale/not verified')
    if hashlib.sha256((root/'data/direction_v42/forward_registry.csv').read_bytes()).hexdigest()!=audit['input_registry_sha256']:
        raise ValueError('P2 original registry differs from paired audited version')
    if (audit.get('protocol_id')!=source_policy['protocol_id'] or audit.get('registered_pairs')!=len(pairs)
        or not isinstance(audit.get('rejected_or_missing'),dict)):
        raise ValueError('P2 source audit cohort differs from current paired records')
    mapped={r['production']['forecast_id']:r for r in pairs};items=[];cache={'prepared':OrderedDict(),'public_prepared':OrderedDict()}
    for fid,row in original.items():
        h=int(row['horizon_hours']);issued=utc(row['issued_at_utc'])
        if h not in (1,24) or issued<effective:continue
        if issued>now:raise ValueError('future P2 source forecast')
        items.append({'forecast_id':fid,'horizon_hours':h,'issued_at_utc':row['issued_at_utc'],'target_utc':row['target_utc'],
            'paper_action':None,'observed_public_utc':None,'source_verified':False,'pair_sha256':None,
            'source_witness_head_sha':None,'source_error':'pair_or_publication_missing',
            'paired_audit_verified':bool(fid in mapped and mapped[fid]['production']==row
                and fid in operations.get('forecast_proofs',{}) and fid not in audit['rejected_or_missing']
                and issued<=utc(audit['evaluated_at_utc'])
                and utc(mapped[fid]['decision_recorded_at_utc'])<=utc(audit['evaluated_at_utc']))})
    chosen={x['forecast_id'] for h in (1,24) for x in reserve([r for r in items if r['horizon_hours']==h])}
    for item in items:
        fid=item['forecast_id']
        if fid not in chosen:continue
        pair=mapped.get(fid);proof=operations.get('forecast_proofs',{}).get(fid)
        if not pair or not proof:continue
        try:
            previous=(prior_verified or {}).get(fid)
            public=proof['publication']
            reusable=bool(previous and fid not in audit.get('rejected_or_missing',{})
                and pair['production']==original[fid] and previous['pair_sha256']==pair['record_sha256']
                and previous['source_witness_head_sha']==public['head_sha']
                and previous['observed_public_utc']==public['observed_public_utc'])
            if not reusable:paired_audit.verify_pair(root,pair,original[fid],None,proof,bindings,cache)
            item.update(paper_action=pair['decision']['paper_action'],observed_public_utc=proof['publication']['observed_public_utc'],
                        source_verified=True,pair_sha256=pair['record_sha256'],source_witness_head_sha=public['head_sha'],source_error=None)
        except Exception as exc:item['source_error']=str(exc)[:200]
    return items


def append_event(root,snapshot,items):
    old=read_events(root);qid,path=save_book(root,snapshot);book=snapshot_book(snapshot,qid)
    if old and utc(book['quote']['quote_utc'])<=utc(old[-1]['quote_utc']):raise ValueError('duplicate/nonmonotonic P2 capture')
    captured=utc(book['captured_at_utc']);relevant=[i for h in (1,24) for i in reserve([r for r in items if int(r['horizon_hours'])==h])
        if utc(i['issued_at_utc'])<=captured<=utc(i['target_utc'])+timedelta(minutes=25)]
    row={'schema_version':1,'book_sha256':qid,'snapshot_path':path,'quote_utc':book['quote']['quote_utc'],
        'captured_at_utc':book['captured_at_utc'],'source_items':relevant,
        'previous_sha256':old[-1]['record_sha256'] if old else None,'real_orders_authorized':False}
    row['record_sha256']=digest(row);target=root/EVENTS;target.parent.mkdir(parents=True,exist_ok=True)
    with target.open('a',encoding='utf-8',newline='\n') as f:f.write(source.causal.canon(row)+'\n');f.flush();os.fsync(f.fileno())
    return row


def event_books(root,events):
    for row in events:
        expected=BASE+'/snapshots/'+row['book_sha256'][:2]+'/'+row['book_sha256']+'.json'
        if row['snapshot_path']!=expected:raise ValueError('P2 snapshot path outside namespace')
        path=root/row['snapshot_path'];body=path.read_bytes()
        if hashlib.sha256(body).hexdigest()!=row['book_sha256']:raise ValueError('P2 raw book snapshot hash mismatch')
        snap=json.loads(body);book=snapshot_book(snap,row['book_sha256'])
        if book['quote']['quote_utc']!=row['quote_utc'] or book['captured_at_utc']!=row['captured_at_utc']:raise ValueError('P2 event/snapshot clock mismatch')
        book['eligible_sources']={i['forecast_id']:i for i in row['source_items']}
        yield book


def run(root=ROOT,capture=True):
    policy=guard(root);now=datetime.now(timezone.utc);effective=utc(policy['effective_from_utc']);events=history_guard(root)
    prior={}
    for event in events:
        for item in event['source_items']:
            fid=item['forecast_id']
            if item['source_verified']:
                if fid in prior and prior[fid]!=item:raise ValueError('P2 committed paper decision/public context changed')
                prior[fid]=item
    items=source_items(root,now,effective,prior)
    for item in items:
        if item['forecast_id'] in prior and prior[item['forecast_id']]!=item:raise ValueError('P2 source identity changed after capture')
    if now<effective:
        result={'status':'ok','activation_state':'WAIT_ACTIVATION','captured_books':len(events),'portfolios':replay(items,[],now)}
    else:
        if capture:
            snap=fetch_book()
            if utc(snap['requested_at_utc'])<effective:raise ValueError('P2 capture request before activation')
            append_event(root,snap,items);events=read_events(root);now=datetime.now(timezone.utc)
        # First observation of source identity cannot be silently revised on later runs.
        result={'status':'ok','activation_state':'ACTIVE_PAPER_SHADOW','captured_books':len(events),
                'portfolios':replay(items,event_books(root,events),now)}
    result.update(schema_version=3,version_id=policy['protocol_id'],audited_at_utc=now.isoformat(),
        effective_from_utc=effective.isoformat(),source='Precommitted paired decisions and fresh post-public OKX visible books',
        source_issuance_and_pair_coverage='See original P0 and paired V1 audit; missing original/pair slots are not repaired by this layer',
        exact_real_profit_validated=False,actual_account_fee_verified=False,real_orders_authorized=False,
        cash_interest_modeled=False,continuous_drawdown_verified=False,old_p2_ledger_changed=False,
        caveat='Visible-depth sweeps and all costs are PAPER assumptions; no exchange fills, queue position, latency/impact guarantee or continuous drawdown proof.')
    age=max(0,(now-effective).total_seconds()/86400)
    closed=max(0,int((now-effective-timedelta(minutes=45)).total_seconds()//3600)+1)
    for h,portfolio in result['portfolios'].items():
        group=[i for i in items if int(i['horizon_hours'])==int(h[:-1])]
        original_slots={int((utc(i['issued_at_utc'])-effective).total_seconds()//3600) for i in group}
        verified_slots={int((utc(i['issued_at_utc'])-effective).total_seconds()//3600) for i in group if i.get('paired_audit_verified') is True}
        original=sum(i in original_slots for i in range(closed));verified=sum(i in verified_slots for i in range(closed))
        due=portfolio['matured_due_intervals'];complete=portfolio['completed_due_intervals']
        selected=reserve(group);verified_selected=sum(i['source_verified'] for i in selected)
        portfolio.update(selected_source_intervals=len(selected),verified_selected_source_intervals=verified_selected,
            selected_source_verification_pct=100*verified_selected/len(selected) if selected else None,
            source_coverage_denominator='All closed hourly original source slots, separate from fixed nonoverlap execution reservations',
            observation_days=age,expected_closed_source_hour_slots=closed,
            original_source_issue_slots=original,verified_pair_slots=verified,
            missing_original_or_verified_pair_slots=closed-verified,
            paired_issue_coverage_pct=100*verified/closed if closed else None,
            due_book_interval_coverage_pct=100*complete/due if due else None)
        portfolio['eligible_for_independent_paper_review_only']=bool(age>=30 and complete>=100
            and not portfolio['blocked_forecast_id'] and selected and verified_selected/len(selected)>=.9
            and portfolio['latest_mark_fresh'] and closed and verified/closed>=.9 and due and complete/due>=.9
            and not portfolio['path_gap_count'] and not portfolio['missing_mark_count']
            and (portfolio['excess_over_hold_pct_points'] or 0)>0
            and (portfolio['excess_over_cash_pct_points'] or 0)>0
            and (portfolio['double_cost_excess_over_hold_pct_points'] or 0)>0
            and (portfolio['double_cost']['prefix_net_return_pct'] or 0)>0)
    frozen.write(root/BASE/'latest.json',json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    frozen.write(root/'reports/DOGE_P2_PAIRED_EXECUTION_V3_1.md','# DOGE P2 配对决定与公开后盘口 V3.1\n\n结论：'+result['activation_state']+'；真实盈利与交易许可未通过。\n\n截止UTC：'+now.isoformat()+'。\n\n1h/24h分账、固定不重叠预约、手续费/可见点差/滑点及双倍压力、连续持币/现金对照。缺入场/出场阻断完整前缀；采样回撤不等于连续最大回撤。\n')
    return result


def smoke(root=ROOT):
    guard(root);snapshot=fetch_book();qid,path=save_book(root,snapshot,True);book=snapshot_book(snapshot,qid)
    rules=book['instrument']
    entry=buy(book['asks'],INITIAL,FEE,SLIP,0,rules['lot_size'],rules['min_size'])
    exit_=sell(book['bids'],entry['quantity'],FEE,SLIP,0,rules['lot_size'],rules['min_size'])
    result={'status':'ok','scope':'Same-book cost/depth engineering check; not an interval, trade, or formal return',
        'captured_at_utc':snapshot['captured_at_utc'],'book_sha256':qid,'snapshot_path':path,
        'spread_bps':book['quote']['spread_bps'],'instrument':rules,'entry':entry,'liquidation_proxy':exit_,
        'new_formal_intervals':0,'real_orders_authorized':False}
    frozen.write(root/BASE/'engineering_smoke_latest.json',json.dumps(result,indent=2)+'\n');return result


if __name__=='__main__':
    try:
        mode=sys.argv[1] if len(sys.argv)>1 else 'capture'
        if mode not in ('capture','audit','smoke'):raise ValueError('unknown P2 mode')
        result=smoke() if mode=='smoke' else run(capture=mode=='capture')
        print(json.dumps({'status':result['status'],'activation_state':result.get('activation_state'),
            'captured_books':result.get('captured_books'),'book_sha256':result.get('book_sha256')}))
    except Exception as exc:
        error={'status':'BLOCKED','error':type(exc).__name__+': '+str(exc),'observed_at_utc':datetime.now(timezone.utc).isoformat(),'real_orders_authorized':False}
        frozen.write(ROOT/BASE/'failure_latest.json',json.dumps(error,indent=2)+'\n');print(json.dumps(error));raise SystemExit(1)
