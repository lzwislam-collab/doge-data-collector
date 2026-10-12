"""Public source snapshots and append-only first-availability/revision ledger."""
import csv,hashlib,io,json,math,re,sys,xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.request import Request,urlopen
import macro_collector
import evidence_append_guard

ROOT=Path(__file__).resolve().parent;BASE='data/enhancement_sources_v1'
URLS={'DXY_YAHOO_UNOFFICIAL':'https://query1.finance.yahoo.com/v8/finance/chart/DX-Y.NYB?range=5y&interval=1d',
 'USD_BROAD_FRED_PROXY':'https://fred.stlouisfed.org/graph/fredgraph.csv?id=DTWEXBGS',
 'FED_NEWS':'https://www.federalreserve.gov/feeds/press_monetary.xml',
 'ECB_NEWS':'https://www.ecb.europa.eu/rss/press.html',
 'CRYPTO_NEWS':'https://www.coindesk.com/arc/outboundfeeds/rss/',
 'BTC_FEAR_GREED_PROXY':'https://api.alternative.me/fng/?limit=0&format=json'}
POS={'approval','approved','growth','inflow','rally','surge','recovery'}
NEG={'hack','fraud','lawsuit','ban','outflow','crash','exploit','liquidation'}
def utc(s):
    x=datetime.fromisoformat(s.replace('Z','+00:00'))
    if x.tzinfo is None:raise ValueError('timezone required')
    return x.astimezone(timezone.utc)
def canon(x):return json.dumps(x,sort_keys=True,separators=(',',':'),ensure_ascii=False)
def digest(x):return hashlib.sha256(canon(x).encode()).hexdigest()
def tone(title):
    words=set(re.findall(r'[a-z]+',title.lower()));a=len(words&POS);b=len(words&NEG)
    return (a-b)/max(1,a+b)
def parse(sid,raw,seen):
    stamp=seen.isoformat();rows=[]
    def add(key,value,published=None,**extra):
        if not math.isfinite(float(value)):raise ValueError('nonfinite value')
        if published is not None and published>seen:raise ValueError('future provider timestamp')
        rows.append({'source_id':sid,'observation_key':str(key),'value':float(value),
          'published_at_utc':published.isoformat() if published else None,'first_seen_utc':stamp,
          'usable_from_utc':stamp,'release_time_independently_verified':False,**extra})
    if sid=='USD_BROAD_FRED_PROXY':
        for day,value in macro_collector.parse_csv('DTWEXBGS',raw).items():
            if day>seen.date().isoformat():raise ValueError('future dollar observation')
            add(day,value,observation_date=day,is_dxy=False,scope='Federal Reserve nominal broad dollar index; NOT ICE DXY')
    elif sid=='DXY_YAHOO_UNOFFICIAL':
        obj=json.loads(raw);result=obj['chart']['result'][0]
        if result['meta']['symbol']!='DX-Y.NYB':raise ValueError('different dollar instrument')
        for tick,value in zip(result['timestamp'],result['indicators']['quote'][0]['close']):
            if value is None:continue
            date=datetime.fromtimestamp(int(tick),timezone.utc)
            if date.date()>=seen.date():continue
            if float(value)<=0:raise ValueError('invalid DXY quote')
            add(date.date().isoformat(),value,observation_date=date.date().isoformat(),is_dxy=True,
                scope='Yahoo DX-Y.NYB daily closes, unofficial vendor; ICE licensing/official history not verified')
    elif sid=='BTC_FEAR_GREED_PROXY':
        for item in json.loads(raw)['data']:
            value=int(item['value']);published=datetime.fromtimestamp(int(item['timestamp']),timezone.utc)
            if not 0<=value<=100:raise ValueError('sentiment out of range')
            add(published.date().isoformat(),value,published,scope='Alternative.me Bitcoin-focused Fear & Greed proxy; not DOGE sentiment',
                attribution='Alternative.me https://alternative.me/crypto/fear-and-greed-index/')
    else:
        tree=ET.fromstring(raw)
        for item in tree.findall('.//item'):
            title=(item.findtext('title') or '').strip();link=(item.findtext('link') or '').strip()
            date=item.findtext('pubDate') or item.findtext('{http://purl.org/dc/elements/1.1/}date')
            if not title or not link or not date:continue
            try:published=parsedate_to_datetime(date)
            except (ValueError,TypeError):published=utc(date)
            if published.tzinfo is None:raise ValueError('news publication timezone absent')
            published=published.astimezone(timezone.utc)
            if not link.startswith(('https://','http://')):raise ValueError('invalid news URL')
            title=title[:2000]
            add(item.findtext('guid') or link,tone(title),published,title=title,url=link,
                score_version='FIXED_HEADLINE_TONE_V1',scope='Headline lexicon tone proxy, not DOGE directional signal or full article sentiment')
    if not rows:raise ValueError('no valid source observations')
    keys=[r['observation_key'] for r in rows]
    if len(keys)!=len(set(keys)):raise ValueError('duplicate provider keys')
    return rows
def read(root=ROOT):
    path=root/BASE/'observations.jsonl';rows=[];previous=None;verified=set()
    evidence_append_guard.guard(root,path.relative_to(root).as_posix(),path.read_bytes() if path.exists() else b'')
    if path.exists():
        for line in path.read_text(encoding='utf-8').splitlines():
            r=json.loads(line);check=dict(r);sha=check.pop('record_sha256')
            if sha!=digest(check) or r['previous_sha256']!=previous:raise ValueError('source ledger chain changed')
            previous=sha;rows.append(r)
            raw=root/r['raw_path']
            expected=BASE+'/raw/'+r['raw_sha256'][:2]+'/'+r['raw_sha256']+'.txt'
            if r['raw_path']!=expected:raise ValueError('source raw path escape')
            if r['raw_sha256'] not in verified:
                if hashlib.sha256(raw.read_bytes()).hexdigest()!=r['raw_sha256']:raise ValueError('source raw snapshot changed')
                verified.add(r['raw_sha256'])
    return rows
def fetch(sid):
    requested=datetime.now(timezone.utc);url=URLS[sid]
    req=Request(url,headers={'User-Agent':'Mozilla/5.0 (DOGE evidence research; public sources only)','Accept':'application/json,text/csv,application/rss+xml,*/*'})
    with urlopen(req,timeout=12) as response:raw=response.read(2000001);actual=response.geturl()
    if len(raw)>2000000:raise ValueError('source response exceeds cap')
    raw.decode('utf-8-sig');seen=datetime.now(timezone.utc)
    return raw,requested,seen,actual
def collect(root=ROOT):
    old=read(root);latest={}
    for r in old:latest[r['source_id'],r['observation_key']]=r
    report={'schema_version':1,'started_at_utc':datetime.now(timezone.utc).isoformat(),'sources':{},'source_scope':'Free public evidence only; no subscription or trading','new_rows':0,'real_orders_authorized':False}
    folder=root/BASE;folder.mkdir(parents=True,exist_ok=True);pending=[]
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures={sid:pool.submit(fetch,sid) for sid in URLS}
        for sid,future in futures.items():
            try:
                raw,requested,seen,actual=future.result();sha=hashlib.sha256(raw).hexdigest();path=folder/'raw'/sha[:2]/(sha+'.txt');path.parent.mkdir(parents=True,exist_ok=True)
                if path.exists() and path.read_bytes()!=raw:raise ValueError('raw hash collision/tamper')
                if not path.exists():path.write_bytes(raw)
                data=parse(sid,raw,seen)
                for row in data:
                    row.update(source_url=URLS[sid],resolved_url=actual,raw_sha256=sha,raw_path=path.relative_to(root).as_posix(),requested_at_utc=requested.isoformat())
                    key=sid,row['observation_key'];prior=latest.get(key)
                    payload={k:row.get(k) for k in ('value','published_at_utc','title','url','score_version')}
                    oldpayload={k:prior.get(k) for k in payload} if prior else None
                    if payload==oldpayload:continue
                    row['revision_of_sha256']=prior['record_sha256'] if prior else None
                    row['previous_sha256']=(pending[-1]['record_sha256'] if pending else old[-1]['record_sha256'] if old else None)
                    row['record_sha256']=digest(row);pending.append(row);latest[key]=row
                report['sources'][sid]={'status':'ok','captured_at_utc':seen.isoformat(),'observations_on_response':len(data),'raw_sha256':sha,'raw_path':path.relative_to(root).as_posix(),
                    'historical_first_availability_before_capture_verified':False,'official_ice_dxy_licensed_history_verified':False if sid.startswith('DXY') else None}
            except Exception as exc:report['sources'][sid]={'status':'BLOCKED','error':type(exc).__name__+': '+str(exc)[:300],'no_zero_value_fabricated':True}
    if pending:
        with (folder/'observations.jsonl').open('ab') as f:
            for r in pending:f.write((canon(r)+'\n').encode())
    report.update(new_rows=len(pending),total_rows=len(old)+len(pending),ended_at_utc=datetime.now(timezone.utc).isoformat(),
        status='ok' if all(r['status']=='ok' for r in report['sources'].values()) else 'PARTIAL_SOURCE_BLOCKED')
    (folder/'latest.json').write_bytes((json.dumps(report,indent=2)+'\n').encode());return report
if __name__=='__main__':
    result=collect();print(json.dumps({'status':result['status'],'new_rows':result['new_rows'],'sources':{k:v['status'] for k,v in result['sources'].items()}}))
