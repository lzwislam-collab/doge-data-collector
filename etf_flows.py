#!/usr/bin/env python3
"""Public Farside BTC/ETH ETF flows, daily USD millions. Research only.
Website parsing is fragile; fail closed if table schema changes.
"""
import csv, hashlib, io, json, os, re, sys, time, urllib.request
from datetime import datetime, date, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent/'data'/'etf'
SOURCES={'BTC':'https://farside.co.uk/btc/','ETH':'https://farside.co.uk/eth/'}
FIELDS=['asset','trade_date_us','total_usd_m','reported_funds','funds_with_values','source_url','first_seen_utc','last_checked_utc','revision_count']
REVISIONS=['asset','trade_date_us','old_total_usd_m','new_total_usd_m','observed_at_utc','source_url']
DATE=re.compile(r'^\d{1,2}\s+[A-Za-z]{3}\s+20\d{2}$')

def utc(): return datetime.now(timezone.utc).isoformat()
def atomic(path, content):
    tmp=path.with_name(path.name+'.tmp');tmp.write_text(content,encoding='utf-8');os.replace(tmp,path)
def csv_dump(fields, rows):
    s=io.StringIO();w=csv.DictWriter(s,fieldnames=fields);w.writeheader();w.writerows(rows);return s.getvalue()
def number(s):
    s=' '.join(s.replace('\xa0',' ').split()).replace(',','').replace('−','-').strip()
    if s in ('','-','N/A','n/a','—','–'):return None
    if s.startswith('(') and s.endswith(')'):s='-'+s[1:-1]
    try: x=Decimal(s)
    except InvalidOperation:raise ValueError('Invalid ETF monetary value: '+repr(s))
    if not x.is_finite():raise ValueError('Nonfinite amount')
    return x

def load_csv(path,fields):
    if not path.exists():return []
    with path.open(newline='',encoding='utf-8') as f:
        rd=csv.DictReader(f)
        if rd.fieldnames!=fields:raise ValueError('Existing CSV schema mismatch: '+str(path))
        return list(rd)

def scrape(asset,url):
    req=urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0 (compatible; DOGE-research-ETF/1.0)','Accept':'text/html'})
    with urllib.request.urlopen(req,timeout=35) as res:
        html=res.read(5_000_000)
    soup=BeautifulSoup(html,'html.parser')
    candidates=[]
    for table in soup.select('table'):
        rows=[]
        for tr in table.select('tr'):
            cells=[' '.join(td.stripped_strings).replace('\xa0',' ').strip() for td in tr.find_all(['td','th'],recursive=False)]
            if cells and DATE.fullmatch(cells[0]):rows.append(cells)
        if rows:candidates.append(rows)
    if not candidates:raise ValueError('No Farside date-table found; do not manufacture ETF flows')
    rows=max(candidates,key=len)
    result={}
    for cells in rows:
        trade_date=datetime.strptime(cells[0],'%d %b %Y').date()
        if trade_date>datetime.now(timezone.utc).date():continue
        vals=[number(v) for v in cells[1:]]
        if len(vals)<2:raise ValueError('ETF row has no fund/total columns')
        total=vals[-1]
        # Missing/dash Total means pending publication, NOT zero.
        if total is None:continue
        funds=vals[:-1]
        if not any(v is not None for v in funds):continue
        if all(v is not None for v in funds) and abs(sum(funds)-total)>Decimal('1.5'):
            raise ValueError(f'Fund sum differs from total for {asset} {trade_date}')
        key=trade_date.isoformat()
        if key in result:raise ValueError('Duplicate ETF trading date on provider page: '+key)
        result[key]={'asset':asset,'trade_date_us':key,'total_usd_m':str(total),
                     'reported_funds':str(len(funds)), 'funds_with_values':str(sum(v is not None for v in funds)),
                     'source_url':url}
    if not result:raise ValueError('No complete dated ETF flow rows')
    return result

def main():
    ROOT.mkdir(parents=True,exist_ok=True)
    started=utc();existing_path=ROOT/'daily_flows.csv';revision_path=ROOT/'revisions.csv'
    result={'schema_version':1,'started_at_utc':started,'ended_at_utc':None,'status':'error',
            'unit':'USD millions','source':'Farside public BTC and ETH ETF tables',
            'errors':{},'latest_trade_date':{},'published_rows':{},'new_rows':0,'revised_rows':0,
            'caveats':['Farside is third-party compiled US-listed spot ETF flow data',
                       'Publication may lag and source values can be revised',
                       'Weekends/holidays and unpublished values are missing, not zero',
                       'ETH and BTC tables may cover different dates',
                       'No point-in-time knowledge of historical revisions before collection started']}
    try:
        existing=load_csv(existing_path,FIELDS)
        old={}
        for row in existing:
            key=(row['asset'],row['trade_date_us'])
            if key in old:raise ValueError('Duplicate stored ETF primary key')
            old[key]=row
        revisions=load_csv(revision_path,REVISIONS)
        # Fetch both before mutating anything: never commit one asset as full-success.
        fetched={asset:scrape(asset,url) for asset,url in SOURCES.items()}
        for asset,rows in fetched.items():
            result['latest_trade_date'][asset]=max(rows)
            result['published_rows'][asset]=len(rows)
            for d,new in rows.items():
                key=(asset,d)
                if key not in old:
                    old[key]={**new,'first_seen_utc':started,'last_checked_utc':started,'revision_count':'0'}
                    result['new_rows']+=1
                else:
                    record=old[key]
                    if Decimal(record['total_usd_m'])!=Decimal(new['total_usd_m']):
                        revisions.append({'asset':asset,'trade_date_us':d,'old_total_usd_m':record['total_usd_m'],
                                          'new_total_usd_m':new['total_usd_m'],'observed_at_utc':started,'source_url':new['source_url']})
                        record['revision_count']=str(int(record['revision_count'])+1)
                        result['revised_rows']+=1
                    record.update(new);record['last_checked_utc']=started
        sorted_rows=[old[k] for k in sorted(old,key=lambda x:(x[1],x[0]))]
        atomic(existing_path,csv_dump(FIELDS,sorted_rows))
        atomic(revision_path,csv_dump(REVISIONS,revisions))
        result['stored_rows']=len(sorted_rows)
        result['duplicates']=0
        result['status']='ok'
    except Exception as exc:
        result['errors']['collector']=type(exc).__name__+': '+str(exc)[:300]
    result['ended_at_utc']=utc()
    atomic(ROOT/'latest.json',json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'status':result['status'],'new':result['new_rows'],'errors':result['errors']}),flush=True)
    return 0 if result['status']=='ok' else 1
if __name__=='__main__':sys.exit(main())
