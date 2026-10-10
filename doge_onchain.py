#!/usr/bin/env python3
"""DOGE mainnet on-chain snapshot. Dogechain API v3; no token; no invented flow metrics.

Run: python doge_onchain.py
Writes: data/onchain/latest.json and data/onchain/daily.csv.
"""
import csv
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from decimal import Decimal, InvalidOperation

BASE = 'https://dogechain.com/api/v3'
OUT = Path(__file__).resolve().parent / 'data' / 'onchain'
KEYS = ('observed_at_utc', 'observed_at_ms', 'chain_height', 'hashrate', 'total_supply_doge', 'richlist_addresses_count', 'richlist_top10_balance_doge', 'richlist_top10_pct_of_supply', 'status')


def get(endpoint):
    request = urllib.request.Request(BASE + endpoint, headers={'Accept':'application/json','User-Agent':'doge-data-collector-onchain/1.0'})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=18) as response:
                payload = json.load(response)
            if not isinstance(payload,dict) or payload.get('status') != 'success' or not isinstance(payload.get('data'), (dict,list)):
                raise ValueError('API envelope status/data invalid')
            return payload['data']
        except (urllib.error.URLError, TimeoutError, ValueError) as err:
            if attempt == 2: raise RuntimeError('endpoint unavailable '+endpoint+': '+str(err)) from err
            time.sleep(attempt + 1)


def decimal(value):
    try:
        d=Decimal(str(value))
    except (InvalidOperation,TypeError) as err:
        raise ValueError('Invalid decimal') from err
    if not d.is_finite() or d < 0: raise ValueError('Nonfinite/negative decimal')
    return d


def extract_network(data):
    # Preserve raw provider fields: public API schemas can evolve.
    info = data.get('info', data) if isinstance(data, dict) else {}
    height = info.get('block_count', info.get('height', info.get('block_height', info.get('blocks'))))
    rate = info.get('hashrate', info.get('hash_rate'))
    return (int(height) if height is not None and str(height).isdigit() else None,
            str(rate) if rate is not None else None)


def richest_list(data):
    # Parse only when provider returns an explicit list of address records.
    if isinstance(data,list): return data
    if not isinstance(data,dict): return []
    for name in ('rows','addresses','richlist','items','results'):
        if isinstance(data.get(name),list): return data[name]
    return []


def richest_balance(row):
    if not isinstance(row,dict): return None
    for key in ('balance','confirmed_balance','balance_doge','amount'):
        if key in row:
            return decimal(row[key])
    return None


def collect():
    started=int(time.time()*1000)
    stamp=datetime.fromtimestamp(started/1000,timezone.utc).isoformat()
    result={'schema_version':1,'source':'https://dogechain.com/api/v3/',
            'network':'Dogecoin mainnet (not EVM Dogechain)',
            'observed_at_utc':stamp,'observed_at_ms':started,
            'status':'unavailable','metrics':{},'raw':{},'errors':{},
            'not_supported':['exchange_netflow_unverified','true_entity_level_whale_flow','active_addresses_without_validated_series'],
            'caveats':['Richlist addresses are not entities; custody/exchange clustering is unverified',
                       'Top 10 concentration is address-level, not investor-level',
                       'Historical network metrics require separate validated series']}
    for label,route in [('network','/network'),('supply','/supply'),('richlist_page1','/richlist?page=1')]:
        try: result['raw'][label]=get(route)
        except Exception as exc: result['errors'][label]=str(exc)[:320]
    supply=result['raw'].get('supply')
    network=result['raw'].get('network')
    riches=result['raw'].get('richlist_page1')
    if isinstance(supply,dict):
        try:
            result['metrics']['total_supply_doge']=str(decimal(supply['supply']))
            result['metrics']['supply_block_height']=int(supply['height']) if 'height' in supply else None
        except (ValueError,KeyError,TypeError) as exc:result['errors']['supply_parse']=str(exc)
    if isinstance(network,dict):
        height,rate=extract_network(network)
        result['metrics']['chain_height']=height
        result['metrics']['hashrate_raw_hps']=rate
        if height is None or rate is None:
            result['errors']['network_parse']='block_count or hashrate missing from network.info'
    if riches is not None:
        records=richest_list(riches)
        if records:
            result['metrics']['richlist_addresses_count']=len(records)
            try:
                top10=[richest_balance(row) for row in records[:10]]
                if len(top10)==10 and all(b is not None for b in top10):
                    total=sum(top10)
                    result['metrics']['richlist_top10_balance_doge']=str(total)
                    if 'total_supply_doge' in result['metrics'] and decimal(result['metrics']['total_supply_doge'])>0:
                        result['metrics']['richlist_top10_pct_of_supply']=str((total/decimal(result['metrics']['total_supply_doge']))*100)
            except (ValueError,TypeError,ArithmeticError) as exc:result['errors']['richlist_parse']=str(exc)
        else:result['errors']['richlist_parse']='Richlist returned but address list structure not recognized; raw retained'
    essential=('supply','network','richlist_page1')
    successful=sum(x in result['raw'] for x in essential)
    required=('total_supply_doge','chain_height','hashrate_raw_hps','richlist_top10_balance_doge','richlist_top10_pct_of_supply')
    if successful == 3 and any(result['metrics'].get(k) is None for k in required):
        result['errors']['missing_metrics']='Missing required parsed metric(s): '+', '.join(k for k in required if result['metrics'].get(k) is None)
    result['status']='ok' if successful==3 and not result['errors'] else ('partial' if successful else 'unavailable')
    OUT.mkdir(parents=True,exist_ok=True)
    destination=OUT/'latest.json'
    temp=destination.with_suffix('.json.tmp')
    temp.write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    os.replace(temp,destination)
    daily=OUT/'daily.csv'
    existing=[]
    if daily.exists():
        with daily.open(newline='',encoding='utf-8') as stream:
            reader=csv.DictReader(stream)
            if tuple(reader.fieldnames or ())!=KEYS: raise ValueError('Unexpected daily schema; no silent migration')
            existing=list(reader)
    # UTC date snapshot; reruns replace the same day, no duplicate dates.
    date=stamp[:10]
    row={'observed_at_utc':stamp,'observed_at_ms':started,
         'chain_height':result['metrics'].get('chain_height') or result['metrics'].get('supply_block_height') or '',
         'hashrate':result['metrics'].get('hashrate_raw_hps') or '',
         'total_supply_doge':result['metrics'].get('total_supply_doge') or '',
         'richlist_addresses_count':result['metrics'].get('richlist_addresses_count') or '',
         'richlist_top10_balance_doge':result['metrics'].get('richlist_top10_balance_doge') or '',
         'richlist_top10_pct_of_supply':result['metrics'].get('richlist_top10_pct_of_supply') or '',
         'status':result['status']}
    prior={r['observed_at_utc'][:10]:r for r in existing}
    prior[date]=row
    temp=daily.with_suffix('.csv.tmp')
    with temp.open('w',encoding='utf-8',newline='') as f:
        w=csv.DictWriter(f,fieldnames=KEYS)
        w.writeheader();w.writerows(prior[k] for k in sorted(prior))
    os.replace(temp,daily)
    print(json.dumps({'status':result['status'],'observed_at_utc':stamp,'metrics':result['metrics'],'errors':result['errors']},ensure_ascii=False))
    return 0 if result['status']=='ok' else 1

if __name__=='__main__':sys.exit(collect())
