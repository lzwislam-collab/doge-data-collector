#!/usr/bin/env python3
"""Read-only DOGE chain/whales/OKX-PoR monitoring quality gate.
Does NOT infer actual exchange deposits/withdrawals/net flow from address balances.
"""
import csv
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / 'data'
OUT = DATA / 'stage1' / 'latest.json'
NOW = datetime.now(timezone.utc)

def read_json(relative):
    return json.loads((DATA / relative).read_text(encoding='utf-8'))

def parse_time(value):
    if not value: raise ValueError('missing UTC timestamp')
    d = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if d.tzinfo is None: raise ValueError('naive timestamp')
    return d.astimezone(timezone.utc)

def hours_since(value):
    age = (NOW - parse_time(value)).total_seconds()/3600
    if age < -0.1: raise ValueError('future timestamp')
    return round(age, 2)

def rows_csv(relative, keys):
    path=DATA/relative
    if not path.exists():return {'exists':False,'rows':None,'duplicates':None}
    with path.open(newline='',encoding='utf-8') as f:
        rdr=csv.DictReader(f)
        if not set(keys).issubset(rdr.fieldnames or []):raise ValueError('columns missing: '+relative)
        seen=set(); duplicates=0; count=0
        for row in rdr:
            key=tuple(row[k] for k in keys)
            if key in seen: duplicates += 1
            seen.add(key); count += 1
    return {'exists':True,'rows':count,'duplicates':duplicates}

def main():
    issues=[]; result={'schema_version':1,'generated_at_utc':NOW.isoformat(),
      'data_scope':'DOGE chain activity, whale block scanner, OKX PoR address balance sampling',
      'exchange_netflow_verified':False,'scheduled_execution_verified':False,
      'source':['Dogechain public API','OKX September 2026 proof-of-reserves disclosed addresses'],
      'checks':{},'limitations':['Address balance deltas are not exchange inflow/outflow',
      'Whale transactions can be UTXO change/internal transfers',
      'PoR-listed addresses are neither ownership verified nor comprehensive',
      'Automated schedule run IDs and event types are not confirmed by this read-only audit']}
    try:
        a=read_json('onchain/latest.json')
        age=hours_since(a['observed_at_utc'])
        result['checks']['chain']={'status':a.get('status'),'age_hours':age,
          'block_height':a.get('metrics',{}).get('chain_height')}
        if a.get('status')!='ok' or age>24:issues.append('chain stale/error')
    except Exception as e:issues.append('chain unreadable: '+str(e)[:150])
    try:
        a=read_json('onchain/metrics_latest.json')
        age=hours_since(a['collected_at_utc'])
        result['checks']['daily_metrics']={'status':a.get('status'),'age_hours':age,
         'complete_day_utc':a.get('latest_complete_core_day_utc'),'historical_days':a.get('daily_observations')}
        if a.get('status')!='ok' or age>48:issues.append('daily activity stale/error')
    except Exception as e:issues.append('daily metrics unreadable: '+str(e)[:150])
    try:
        a=read_json('whales_blocks/latest.json')
        age=hours_since(a['ended_at_utc'])
        result['checks']['whales']={'status':a.get('status'),'age_hours':age,
          'completed_height':a.get('last_completed_height'),'tip_at_run':a.get('confirmed_tip_at_run'),
          'backlog_at_run_end':a.get('backlog_blocks_at_run_end'),
          'new_whale_transactions':a.get('new_whale_transactions'),
          'errors':a.get('errors')}
        if a.get('status')!='ok' or age>6 or a.get('errors'):issues.append('whale scanner stale/error')
        if (a.get('backlog_blocks_at_run_end') or 0)>100:issues.append('whale scanner significant backlog')
    except Exception as e:issues.append('whales unreadable: '+str(e)[:150])
    try:
        a=read_json('okx_address_balances/latest.json'); cp=read_json('okx_address_balances/checkpoint.json')
        age=hours_since(a['ended_at_utc'])
        frac=round(100*int(a['sampled_addresses_ever'])/int(a['addresses_in_allowlist']),4)
        result['checks']['okx_addresses']={'status':a.get('status'),'age_hours':age,
          'sampled':a['sampled_addresses_ever'],'total':a['addresses_in_allowlist'],
          'fraction_pct':frac,'next_index':cp.get('next_index'),'cycle':cp.get('cycle'),
          'errors':a.get('errors')}
        if a.get('status')!='ok' or age>6 or a.get('errors'):issues.append('OKX address sampler stale/error')
        if cp.get('next_index')!=a.get('next_index'):issues.append('OKX checkpoint mismatch')
        current=rows_csv('okx_address_balances/current_balances.csv',['address'])
        events=rows_csv('okx_address_balances/observations.csv',['cycle','address'])
        result['checks']['okx_current_csv']=current
        result['checks']['okx_observations_csv']=events
        if not current['exists'] or current['duplicates'] or current['rows']!=a['sampled_addresses_ever']:
            issues.append('OKX address CSV count/duplicate mismatch')
        if not events['exists'] or events['duplicates']:issues.append('OKX observations missing/duplicate')
    except Exception as e:issues.append('OKX sampler unreadable: '+str(e)[:150])
    result['issues']=issues
    result['status']='ok_partial_coverage' if not issues else 'needs_attention'
    OUT.parent.mkdir(parents=True,exist_ok=True)
    OUT.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'status':result['status'],'issues':issues},ensure_ascii=False))
    return 0 if not issues else 1

if __name__=='__main__':raise SystemExit(main())
