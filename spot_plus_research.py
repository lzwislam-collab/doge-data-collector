#!/usr/bin/env python3
"""DOGE Spot Prediction Plus v0.1: reproducible research, NEVER automated trading.

Distinguishes prospective V4.2 24h quote-direction forecasts from a separately
labeled historical daily OPEN->CLOSE spot-paper experiment.
Candidate signals are post-selected research; chronological split is NOT an
untouched blind holdout. Do not alter frozen V4.2 or DOGE DCA rules.
"""
import csv,io,json,math,os
from datetime import datetime,timedelta,timezone
from pathlib import Path
from spot_plus_context import inspect as inspect_five_pillars

ROOT=Path(__file__).resolve().parent
OUT=ROOT/'data/stage5/spot_plus/latest.json'
DAY=86_400_000
INITIAL=10_000.0
FEE=0.001
SLIPPAGE=0.0005
MODELS=('doge_one_day_lag_reversal','doge_one_day_lag_momentum','btc_one_day_lag_momentum')
COLUMNS=('symbol','open_utc_ms','open','high','low','close','volume_base','source')

def utc(s):
 d=datetime.fromisoformat(s.replace('Z','+00:00'))
 if d.tzinfo is None or d.utcoffset()!=timedelta(0):
  raise ValueError('UTC timestamp required')
 return d.astimezone(timezone.utc)

def rows(path,required):
 with path.open(newline='',encoding='utf-8-sig') as stream:
  r=csv.DictReader(stream)
  if not set(required).issubset(r.fieldnames or []):
   raise ValueError('invalid CSV columns: '+path.as_posix())
  return list(r)

def daily(root=ROOT,now=None):
 if now is None:now=datetime.now(timezone.utc)
 if now.utcoffset()!=timedelta(0):raise ValueError('UTC now required')
 lines=rows(root/'data/history/daily.csv',COLUMNS)
 out={k:[] for k in ('DOGE-USDT','BTC-USDT','ETH-USDT')}
 seen=set()
 for r in lines:
  symbol=r['symbol']
  if symbol not in out:continue
  start=int(r['open_utc_ms'])
  if (symbol,start) in seen:raise ValueError('duplicate day: '+symbol)
  seen.add((symbol,start))
  if start%DAY:raise ValueError('non aligned UTC daily timestamp')
  if datetime.fromtimestamp((start+DAY)/1000,timezone.utc)>now:
   raise ValueError('unconfirmed daily candle '+symbol)
  o,h,l,c,v=(float(r[k]) for k in ('open','high','low','close','volume_base'))
  if not all(math.isfinite(x) for x in (o,h,l,c,v)) or min(o,h,l,c)<=0 or v<0:
   raise ValueError('invalid OHLCV '+symbol)
  if not l<=min(o,c)<=max(o,c)<=h:raise ValueError('inconsistent OHLC '+symbol)
  if 'OKX' not in r['source'] or 'confirmed' not in r['source']:
   raise ValueError('unverified daily source')
  out[symbol].append({'t':start,'o':o,'h':h,'l':l,'c':c})
 for symbol,entry in out.items():
  entry.sort(key=lambda x:x['t'])
  if len(entry)<500 or any(b['t']-a['t']!=DAY for a,b in zip(entry,entry[1:])):
   raise ValueError('short or incomplete daily series '+symbol)
 if out['BTC-USDT'][-1]['t']!=out['DOGE-USDT'][-1]['t'] or out['ETH-USDT'][-1]['t']!=out['DOGE-USDT'][-1]['t']:
  raise ValueError('latest daily BTC ETH DOGE not synchronized')
 return out

def signal(bars,day_index,mode,btc=None):
 """At day i open, use ONLY data through day i-2 close: one full daily-bar lag."""
 if day_index<3:raise ValueError('at least three prior bars required')
 if mode=='doge_one_day_lag_reversal':
  return bars[day_index-2]['c']<=bars[day_index-3]['c']
 if mode=='doge_one_day_lag_momentum':
  return bars[day_index-2]['c']>bars[day_index-3]['c']
 if mode=='btc_one_day_lag_momentum':
  b=btc[day_index-2]['c']
  return b>btc[day_index-3]['c']
 raise ValueError('unknown mode')

def simulate(bars,start,mode,btc=None,fee=FEE,slippage=SLIPPAGE):
 if start<3 or start>=len(bars):raise ValueError('invalid test split')
 if not 0<=fee<=.02 or not 0<=slippage<=.02:raise ValueError('invalid friction')
 if btc is not None and len(btc)!=len(bars):raise ValueError('unaligned BTC')
 equity=INITIAL;cash=INITIAL;tokens=0.0;peak=INITIAL;maxdd=0.0
 fees=0.0;slip_cost=0.0;trades=0;exposed=0;correct=0;n=0
 daily_return=[]
 for i in range(start,len(bars)):
  want=mode=='buy_and_hold' or (False if mode=='cash' else signal(bars,i,mode,btc))
  open_price=bars[i]['o'];close=bars[i]['c']
  if want and cash>0:
   exec_price=open_price*(1+slippage)
   units=cash/(exec_price*(1+fee))
   fees+=units*exec_price*fee
   slip_cost+=units*open_price*slippage
   tokens=units;cash=0.0;trades+=1
  elif not want and tokens>0:
   exec_price=open_price*(1-slippage)
   fees+=tokens*exec_price*fee
   slip_cost+=tokens*open_price*slippage
   cash=tokens*exec_price*(1-fee)
   tokens=0.0;trades+=1
  if want:exposed+=1
  if mode not in ('buy_and_hold','cash'):
   actual_up=close>open_price
   correct+=int(want==actual_up)
   n+=1
  new_equity=cash+tokens*close
  daily_return.append(new_equity/equity-1)
  equity=new_equity
  peak=max(peak,equity);maxdd=max(maxdd,1-equity/peak)
 # At research horizon, every residual DOGE position is ASSUMED liquidated.
 # Without this, held strategies are marked at mid/last close while cash
 # has already paid selling costs, overstating immediately realizable PnL.
 marked_equity=equity
 terminal_sell_price=bars[-1]['c']*(1-slippage)
 terminal_proceeds=tokens*terminal_sell_price*(1-fee)
 if tokens:
  fees+=tokens*terminal_sell_price*fee
  slip_cost+=tokens*bars[-1]['c']*slippage
  trades+=1
 terminal_equity=cash+terminal_proceeds
 maxdd=max(maxdd,1-terminal_equity/peak)
 years=(len(bars)-start)/365.0
 return {'initial_usdt':INITIAL,'final_usdt':round(terminal_equity,2),
  'pre_liquidation_mark_to_market_usdt':round(marked_equity,2),
  'terminal_liquidation_assumed':True,
  'net_total_return_pct':round(100*(terminal_equity/INITIAL-1),2),
  'cagr_pct':round(100*((terminal_equity/INITIAL)**(1/years)-1),2) if terminal_equity>0 else -100,
  'max_drawdown_pct':round(100*maxdd,2),'trades':trades,
  'days_exposed_pct':round(100*exposed/(len(bars)-start),2),
  'estimated_total_fees_usdt':round(fees,2),
  'estimated_total_slippage_usdt':round(slip_cost,2),
  'observed_fees_usdt':round(fees,2),
  'observed_slippage_usdt':round(slip_cost,2),
  'open_to_close_direction_accuracy_pct':round(100*correct/n,2) if n else None,
  'direction_scored_days':n,
  'assumptions':{'fee_per_side':fee,'slippage_per_side':slippage,
   'funding_yield':0,'borrowing':False,'shorting':False,
   'liquidation':False,'terminal_spot_sale_included':True,
   'frictions_are_assumptions_not_observed_fills':True,
   'market_impact_beyond_assumed_slippage':False}}

def prices_scenario(bars,reference):
 """Distribution of known past daily close-to-close moves; NOT a confidence interval."""
 series=[bars[i]['c']/bars[i-1]['c']-1 for i in range(len(bars)-90,len(bars))]
 if len(series)!=90:raise ValueError('bad scenario window')
 series.sort()
 def quantile(p):
  k=p*(len(series)-1);j=math.floor(k)
  return series[j]*(1-(k-j))+series[min(len(series)-1,j+1)]*(k-j)
 return {'period_days':90,'p10_return_pct':round(100*quantile(.1),2),
  'p90_return_pct':round(100*quantile(.9),2),
  'scenario_reference_price':reference,
  'p10_implied_price':round(reference*(1+quantile(.1)),6),
  'p90_implied_price':round(reference*(1+quantile(.9)),6),
  'probability_forecast_calibrated':False,
  'horizon_mismatch':'historical UTC close-to-close distribution, NOT registered issuance quote to target'}

def latest_forward(root,now):
 reg=rows(root/'data/direction_v42/forward_registry.csv',
  ('forecast_id','issued_at_utc','reference_price','target_utc',
   'horizon_hours','doge_reversal_direction','model_id'))
 valid=[r for r in reg if r['horizon_hours']=='24'
    and utc(r['issued_at_utc'])<=now and utc(r['target_utc'])>now]
 if not valid:return {'status':'no_pending_24h_forecast'}
 valid.sort(key=lambda r:utc(r['issued_at_utc']))
 row=valid[-1]
 quote_at=utc(row['issued_at_utc'])
 return {'status':'pre_registered_pending','forecast_id':row['forecast_id'],
  'issued_at_utc':quote_at.isoformat(),'target_utc':utc(row['target_utc']).isoformat(),
  'reference_price_usdt':float(row['reference_price']),
  'direction':row['doge_reversal_direction'],
  'forecast_age_minutes':round((now-quote_at).total_seconds()/60,1),
  'not_a_new_plus_model_forecast':True,
  'live_forecast_model_id':row['model_id']}

def evaluate(root=ROOT,now=None):
 if now is None:now=datetime.now(timezone.utc)
 if now.utcoffset()!=timedelta(0):raise ValueError('UTC now required')
 lock=json.loads((root/'data/stage5/pre_registration.json').read_text(encoding='utf-8'))
 if lock.get('status')!='frozen_protocol' or lock.get('model_id')!='DOGE_DIRECTION_ISSUED_QUOTE_SHADOW_V42_1':
  raise ValueError('frozen original source validation failed')
 q=json.loads((root/'data/history/latest.json').read_text(encoding='utf-8'))
 if q.get('status')!='ok' or q.get('quality',{}).get('duplicate_keys')!=0:
  raise ValueError('daily collector quality gate failed')
 d=daily(root,now);dog=d['DOGE-USDT']
 # Align BTC/ETH to DOGE dates without filling missing observations.
 lookup={v['t']:v for v in d['BTC-USDT']}
 btc=[lookup.get(v['t']) for v in dog]
 if any(v is None for v in btc):raise ValueError('BTC comparison timeline missing dates')
 test_start=int(len(dog)*.70)
 # No rule-fitting on either partition. This split is still NONPRISTINE research
 # because all candidate choices are authored with hindsight knowledge.
 simulations={key:simulate(dog,test_start,key,btc) for key in
  (*MODELS,'buy_and_hold','cash')}
 variations={mode:{'half_friction':simulate(dog,test_start,mode,btc,FEE/2,SLIPPAGE/2)['net_total_return_pct'],
                   'base_friction':simulations[mode]['net_total_return_pct'],
                   'double_friction':simulate(dog,test_start,mode,btc,FEE*2,SLIPPAGE*2)['net_total_return_pct']}
             for mode in MODELS}
 last_reported_at=datetime.fromtimestamp((dog[-1]['t']+DAY)/1000,timezone.utc)
 forward=latest_forward(root,now)
 reference=forward['reference_price_usdt'] if forward['status']=='pre_registered_pending' else dog[-1]['c']
 def date_of(i):return datetime.fromtimestamp(dog[i]['t']/1000,timezone.utc).date().isoformat()
 context=inspect_five_pillars(root,now)
 return {'schema_version':1,'status':'ok','generated_at_utc':now.isoformat(),
  'product':'DOGE 现货盈利预测 Plus v0.1 | research-only',
  'source':'OKX confirmed 1Dutc OHLCV; immutable V4.2 issuance-quote registry',
  'source_daily_latest_confirmed_start_utc':date_of(len(dog)-1)+'T00:00:00Z',
  'source_daily_last_available_at_utc':last_reported_at.isoformat(),
  'data_quality':{'doge_daily_rows':len(dog),'btc_comparison_rows':len(btc),
                  'missing_dates':0,'duplicate_daily_keys':0},
  'study':{'test_split':'chronological_last_30_percent',
   'not_pristine_holdout':True,'candidate_rules_selected_after_historical_exploration':True,
   'test_start_utc':date_of(test_start),'test_end_utc':date_of(len(dog)-1),
   'test_days':len(dog)-test_start,'signal_timing':'use only day i-2 and i-3 confirmed closes; execute at day i OPEN, at least one full daily bar delayed',
   'target_definition':'daily UTC OPEN-to-CLOSE direction; distinct from frozen V4.2 issuance-to-24h-ticker direction',
   'execution':'spot only full position or cash; no trading within daily bar; initial 10000 USDT',
   'fee_each_side_pct':FEE*100,'slippage_each_side_pct':SLIPPAGE*100,
   'assumed_terminal_spot_sale_included':True,
   'candidate_hypothesis_only':True},
  'historical_spot_paper':simulations,
  'five_pillar_evidence_audit':context,
  'historical_relative_net_return_vs_hold_pct_points':{
    m:round(simulations[m]['net_total_return_pct']-simulations['buy_and_hold']['net_total_return_pct'],2)
    for m in MODELS},
  'friction_sensitivity_return_pct':variations,
  'prospective_V42_reference':forward,
  'historical_volatility_scenarios':prices_scenario(dog,reference),
  'current_spot_action':'WAIT_FOR_PROSPECTIVE_ECONOMIC_VALIDATION',
  'profit_edge_vs_hold_verified':False,'direction_accuracy_70pct_verified':False,
  'five_pillars_have_validated_numeric_weights':False,
  'live_trading_authorized':False,'dca_rules_changed':False,
  'pending_economic_validation':['freeze independent spot-plus paper execution rules before forward comparison',
    'compare realized after-cost net equity and max drawdown to buy-and-hold and cash over multiple regimes',
    'log actual executable quotes at the decision and settlement moments',
    'measure no-signal coverage and genuine target observation completeness',
    'do not cherry-pick best historical candidate and call it an ex-ante trading advantage'],
  'limitations':['Past OKX 1D candles cannot reproduce fills at intraday bid/ask; slippage is an assumption',
    'Historical 30% split is NOT blind out-of-sample because strategies were formulated with historical awareness',
    'Historical accuracy for OPEN-to-CLOSE is not V4.2 24h issuance-to-quote accuracy',
    'No leverage, no shorting, no passive fill or exchange minimum-order constraints modeled',
    'BTC/ETH ETF and macro releases lack verified historical point-in-time vintages; excluded from simulated signals',
    'The original 36 items were reconciled historically as a summary, not verified as 36 independent model inputs',
    'All five pillars are consulted as evidence, but only PIT-defensible price data feeds the historical spot experiment',
    'Research shows gross market regimes and hypothetical execution, not future profitability']}

def main():
 OUT.parent.mkdir(parents=True,exist_ok=True)
 try:result=evaluate()
 except Exception as exc:
  result={'schema_version':1,'status':'blocked',
   'generated_at_utc':datetime.now(timezone.utc).isoformat(),
   'error':type(exc).__name__+': '+str(exc)[:350],
   'live_trading_authorized':False,'profit_edge_vs_hold_verified':False,
   'direction_accuracy_70pct_verified':False}
 tmp=OUT.with_suffix('.json.tmp')
 tmp.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
 os.replace(tmp,OUT)
 print(json.dumps({'status':result['status'],
  'test_days':result.get('study',{}).get('test_days'),
  'edge_verified':result.get('profit_edge_vs_hold_verified'),
  'error':result.get('error')},ensure_ascii=False))
 return 0 if result['status']=='ok' else 1

if __name__=='__main__':
 raise SystemExit(main())
