#!/usr/bin/env python3
"""V4.1 DOGE empirical CLOSE-to-CLOSE interval forecasts. Research only."""
import csv,hashlib,io,json,math,os,subprocess
from datetime import datetime,timezone,timedelta,date
from pathlib import Path

ROOT=Path(__file__).resolve().parent
BASE=ROOT/'data/forecast_v41'
DAILY=ROOT/'data/history/daily.csv'
DAY_MS=86400000
HORIZONS=(1,7,30)
TRAIN=730
ALPHA=0.2
MAX_REGISTRATION_LATENCY_MIN=120
MODEL='doge_empirical_730d_v4_1'
COLS=['forecast_id','registered_utc','anchor_date_utc','anchor_close','target_date_utc','horizon_days','model_id','training_n','p10','p50','p90','historical_up_frequency','baseline_p10','baseline_p50','baseline_p90','baseline_up_frequency','input_digest']
DOWNSIDE={1:0.10,7:0.20,30:0.40}

def iso(ms):
 return datetime.fromtimestamp(ms/1000,timezone.utc).date().isoformat()
def atomic(path,text):
 path.parent.mkdir(parents=True,exist_ok=True)
 tmp=path.with_name(path.name+'.tmp');tmp.write_text(text,encoding='utf-8');os.replace(tmp,path)

def load_prices(require_fresh=True,now=None):
 if now is None:now=datetime.now(timezone.utc)
 symbols={s:{} for s in ('DOGE-USDT','BTC-USDT','ETH-USDT')}
 with DAILY.open(newline='',encoding='utf-8') as f:
  reader=csv.DictReader(f)
  expected={'symbol','open_utc_ms','open','high','low','close','volume_base'}
  if not expected.issubset(reader.fieldnames or []):raise ValueError('missing daily OHLCV columns')
  for r in reader:
   s=r['symbol'];t=int(r['open_utc_ms'])
   if s not in symbols or t<0 or t%DAY_MS:raise ValueError('invalid UTC symbol or timestamp')
   if t in symbols[s]:raise ValueError('duplicate daily symbol/date: '+s+':'+str(t))
   o,hi,lo,c,v=(float(r[k]) for k in ('open','high','low','close','volume_base'))
   if not all(map(math.isfinite,(o,hi,lo,c,v))) or min(o,lo,c)<=0 or v<0 or hi<max(o,lo,c) or lo>min(o,c):
    raise ValueError('invalid OHLCV '+s+':'+str(t))
   symbols[s][t]=c
 if not symbols['DOGE-USDT']:raise ValueError('empty DOGE daily history')
 for s,values in symbols.items():
  stamps=sorted(values)
  if any(b-a!=DAY_MS for a,b in zip(stamps,stamps[1:])):raise ValueError('daily gaps: '+s)
  if stamps[-1]!=max(symbols['DOGE-USDT']):raise ValueError('unaligned latest symbol: '+s)
 stamps=sorted(symbols['DOGE-USDT'])
 history=json.loads((ROOT/'data/history/latest.json').read_text(encoding='utf-8'))
 if history.get('status')!='ok':raise ValueError('history collection not ok')
 q=history.get('quality',{})
 if q.get('duplicate_keys')!=0:raise ValueError('historical duplicate keys reported')
 for s in symbols:
  item=q.get('symbols',{}).get(s,{})
  if item.get('last_utc')!=iso(stamps[-1]) or item.get('missing_days_between_endpoints')!=0:
   raise ValueError('history audit mismatch: '+s)
 if require_fresh and iso(stamps[-1])!=(now.date()-timedelta(days=1)).isoformat():
  raise ValueError('stale UTC daily anchor '+iso(stamps[-1])+' expected yesterday UTC')
 return stamps,[symbols['DOGE-USDT'][t] for t in stamps]

def quantile(sorted_values,p):
 at=(len(sorted_values)-1)*p;left=int(at);fraction=at-left
 return sorted_values[left]*(1-fraction)+sorted_values[min(left+1,len(sorted_values)-1)]*fraction

def prediction(prices,anchor,h,rolling=True):
 stop=anchor-h
 if stop<0:raise ValueError('insufficient forward-matured training')
 start=max(0,stop-TRAIN+1) if rolling else 0
 values=sorted(math.log(prices[k+h]/prices[k]) for k in range(start,stop+1))
 if len(values)<365:raise ValueError('fewer than 365 matured training labels')
 lo=quantile(values,0.1);med=quantile(values,0.5);hi=quantile(values,0.9)
 frequency=(1+sum(x>0 for x in values))/(len(values)+2)
 downside=(1+sum(x<=math.log1p(-DOWNSIDE[h]) for x in values))/(len(values)+2)
 return {'p10_r':lo,'p50_r':med,'p90_r':hi,'historical_up_frequency':frequency,
         'historical_large_down_frequency':downside,'n':len(values)}

def interval_score(low,high,realized):
 return (high-low)+10*(low-realized if realized<low else 0)+10*(realized-high if realized>high else 0)

def walk_forward(prices,h):
 first=max(TRAIN+h,len(prices)-h-365)
 rows=[]
 for idx in range(first,len(prices)-h):
  candidate=prediction(prices,idx,h,True)
  reference=prediction(prices,idx,h,False)
  actual=math.log(prices[idx+h]/prices[idx])
  rows.append((idx,actual,candidate,reference))
 def summarize(items):
  if not items:return {'n':0}
  def one(which):
   values=[(a,b[which]) for _,a,*rest in [] for b in []] if False else None
   preds=[r[which] for _,_,*r in items]
   ys=[a for _,a,*_ in items]
   n=len(items)
   coverage=sum(p['p10_r']<=y<=p['p90_r'] for y,p in zip(ys,preds))/n
   interval=sum(interval_score(p['p10_r'],p['p90_r'],y) for y,p in zip(ys,preds))/n
   width=sum(p['p90_r']-p['p10_r'] for p in preds)/n
   brier=sum((p['historical_up_frequency']-int(y>0))**2 for y,p in zip(ys,preds))/n
   mederr=sum(abs(p['p50_r']-y) for y,p in zip(ys,preds))/n
   return {'coverage_pct':round(coverage*100,2),'mean_interval_score_log_pct':round(interval*100,4),
           'mean_width_log_pct':round(width*100,4),'up_frequency_brier':round(brier,6),
           'median_abs_log_return_error_pct':round(mederr*100,4)}
  return {'n':len(items),'rolling':one(0),'expanding':one(1)}
 nonoverlap=[];prev=-10**9
 for row in rows:
  if row[0]-prev>=h:nonoverlap.append(row);prev=row[0]
 return {'all_anchors_overlapping':summarize(rows),'nonoverlap_fixed_start_phase':summarize(nonoverlap),
         'holdout_start_anchor_index':first,'target_horizon_days':h,
         'warning':'Overlapping h-day outcomes are correlated; fixed-phase nonoverlap is low-power at long horizons.'}

def verify_append_only(existing):
 revisions=subprocess.run(['git','log','--reverse','--format=%H','--','data/forecast_v41/registry.csv'],
  cwd=ROOT,text=True,capture_output=True,check=True).stdout.splitlines()
 previous={}
 for rev in revisions:
  raw=subprocess.run(['git','show',rev+':data/forecast_v41/registry.csv'],
   cwd=ROOT,text=True,capture_output=True,check=True).stdout
  reader=csv.DictReader(io.StringIO(raw))
  if reader.fieldnames!=COLS:raise ValueError('historical v4.1 registry schema mismatch')
  current={}
  for row in reader:
   fid=row['forecast_id']
   if fid in current:raise ValueError('historical duplicate '+fid)
   current[fid]=row
  for fid,entry in previous.items():
   if current.get(fid)!=entry:raise ValueError('retroactive forecast changed '+fid)
  previous=current
 for fid,entry in previous.items():
  if existing.get(fid)!=entry:raise ValueError('existing forecast changed '+fid)

def build(now=None):
 if now is None:now=datetime.now(timezone.utc)
 times,prices=load_prices(True,now)
 anchor=len(prices)-1
 output={'schema_version':1,'generated_utc':now.isoformat(),'status':'ok','anchor_date_utc':iso(times[-1]),
         'anchor_close':prices[-1],'model_id':MODEL,'training_days':TRAIN,
         'source':'OKX confirmed 1Dutc DOGE-USDT close','research_only':True,'predictive_edge_validated':False,
         'forecast_type':'UTC closed daily CLOSE to future UTC daily CLOSE, not intraday extremes',
         'price_intervals':{},'limitations':['Empirical quantiles and up-frequency are uncalibrated',
          'Rolling backtest explores 365 recent anchors, not a fresh untouched test set',
          'No 365d probability; no position sizing or execution instructions']}
 registry=BASE/'registry.csv';existing={}
 if registry.exists():
  with registry.open(newline='',encoding='utf-8') as f:
   reader=csv.DictReader(f)
   if reader.fieldnames!=COLS:raise ValueError('v4.1 registry header mismatch')
   for row in reader:
    fid=row['forecast_id']
    if fid in existing:raise ValueError('duplicate forecast '+fid)
    existing[fid]=row
 verify_append_only(existing)
 digest=hashlib.sha256((iso(times[-1])+':'+format(prices[-1],'.12g')).encode()).hexdigest()
 anchor_closed_at=datetime.combine(datetime.fromtimestamp(times[-1]/1000,timezone.utc).date()+timedelta(days=1),datetime.min.time(),timezone.utc)
 elapsed_min=(now-anchor_closed_at).total_seconds()/60
 register_allowed=0<=elapsed_min<=MAX_REGISTRATION_LATENCY_MIN
 output['forecast_clock_definition']='UTC prior daily close to target UTC daily close; NOT 24h after registration'
 output['origin_close_at_utc']=anchor_closed_at.isoformat()
 output['reported_at_utc']=now.isoformat()
 output['target_window_elapsed_before_issue_minutes']=round(max(0,elapsed_min),2)
 output['from_report_time_exact_24h_forecast']=False
 output['registration_latency_minutes']=round(elapsed_min,2)
 output['registration_limit_minutes']=MAX_REGISTRATION_LATENCY_MIN
 output['registration_window_ok']=register_allowed
 output['registration_warning']=None if register_allowed else 'Late/missing run: descriptive distribution only, no new prospective forecast registered'
 generated=[];backtests={}
 for h in HORIZONS:
  p=prediction(prices,anchor,h,True);baseline=prediction(prices,anchor,h,False)
  expected=iso(times[-1]+h*DAY_MS)
  values={'p10':prices[-1]*math.exp(p['p10_r']),'p50':prices[-1]*math.exp(p['p50_r']),
    'p90':prices[-1]*math.exp(p['p90_r'])}
  fid=MODEL+'_'+iso(times[-1])+'_'+str(h)+'d'
  output['price_intervals'][str(h)+'d']={'target_utc':expected,'training_matured_n':p['n'],
   'p10_p50_p90_usdt':{k:round(v,8) for k,v in values.items()},
   'historical_up_frequency_pct':round(p['historical_up_frequency']*100,3),
   'research_large_down_threshold_pct':-int(DOWNSIDE[h]*100),
   'historical_large_down_frequency_pct':round(p['historical_large_down_frequency']*100,3),
   'validated_probability':False,'historical_sample_only':True,
   'remaining_target_minutes_after_registration':round((datetime.combine(datetime.fromtimestamp((times[-1]+h*DAY_MS)/1000,timezone.utc).date()+timedelta(days=1),datetime.min.time(),timezone.utc)-now).total_seconds()/60,2)}
  output['price_intervals'][str(h)+'d']['prospective_registered_or_existing']=fid in existing or register_allowed
  backtests[str(h)+'d']=walk_forward(prices,h)
  if fid not in existing and register_allowed:
   row=dict(zip(COLS,[fid,now.isoformat(),iso(times[-1]),format(prices[-1],'.12g'),expected,str(h),MODEL,str(p['n']),
     format(values['p10'],'.12g'),format(values['p50'],'.12g'),format(values['p90'],'.12g'),
     format(p['historical_up_frequency'],'.10f'),
     format(prices[-1]*math.exp(baseline['p10_r']),'.12g'),
     format(prices[-1]*math.exp(baseline['p50_r']),'.12g'),
     format(prices[-1]*math.exp(baseline['p90_r']),'.12g'),
     format(baseline['historical_up_frequency'],'.10f'),digest]))
   existing[fid]=row;generated.append(fid)
 if generated:
  buf=io.StringIO();w=csv.DictWriter(buf,fieldnames=COLS);w.writeheader()
  w.writerows(existing[x] for x in sorted(existing))
  atomic(registry,buf.getvalue())
 output['registered_new_forecasts']=len(generated);output['registry_rows']=len(existing)
 output['new_ids']=generated
 backtests={'schema_version':1,'run_utc':now.isoformat(),'source_cutoff_utc':iso(times[-1]),
   'research_only':True,'proven_edge':False,'evaluation':'walk-forward; only fully matured outcomes; compares rolling 730d vs expanding-history empirical baselines',
   'horizons':backtests}
 return output,backtests

def main():
 try:
  obj,back=build();atomic(BASE/'backtest_latest.json',json.dumps(back,indent=2)+'\n')
 except Exception as exc:
  obj={'schema_version':1,'generated_utc':datetime.now(timezone.utc).isoformat(),
       'status':'blocked','reason':type(exc).__name__+': '+str(exc)[:350],
       'research_only':True,'predictive_edge_validated':False}
 atomic(BASE/'latest.json',json.dumps(obj,indent=2)+'\n')
 print(json.dumps({'status':obj['status'],'new':obj.get('registered_new_forecasts'),'reason':obj.get('reason')}))
 return 0 if obj['status']=='ok' else 1
if __name__=='__main__':raise SystemExit(main())
