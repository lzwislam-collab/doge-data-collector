"""Independent terminal-run receipt; never reconstructs lost forecast evidence."""
import json, os, urllib.request
from datetime import datetime, timezone
from pathlib import Path

REPO='lzwislam-collab/doge-data-collector'
WORKFLOW='Backfill DOGE BTC ETH 1H extreme-event research history'

def github_json(path):
    token=os.environ['GITHUB_TOKEN']
    request=urllib.request.Request('https://api.github.com/repos/'+REPO+path,
        headers={'Authorization':'Bearer '+token,'Accept':'application/vnd.github+json','User-Agent':'DOGE-Engineering-Receipt/1'})
    with urllib.request.urlopen(request,timeout=20) as response:return json.load(response)

def pages(request,path,key):
    rows=[]
    for page in range(1,101):
        batch=request(path+'?per_page=100&page='+str(page))[key]
        rows.extend(batch)
        if len(batch)<100:return rows
    raise ValueError('terminal receipt pagination incomplete')

def audit(event,request=github_json):
    if event.get('repository',{}).get('full_name')!=REPO:raise ValueError('unexpected repository')
    run_id=int(event['id']);attempt=int(event['run_attempt'])
    run=request('/actions/runs/'+str(run_id))
    if run['id']!=run_id or run['run_attempt']!=attempt:raise ValueError('run attempt changed; retry audit with current attempt')
    jobs=pages(request,f'/actions/runs/{run_id}/attempts/{attempt}/jobs','jobs')
    artifacts=pages(request,f'/actions/runs/{run_id}/artifacts','artifacts')
    steps=[s for j in jobs for s in j.get('steps',[])]
    def success(name):return any(s.get('name')==name and s.get('conclusion')=='success' for s in steps)
    names={a['name'] for a in artifacts if not a.get('expired',False)}
    gaps=[]
    if not success('Persist data and diagnostics'):gaps.append('persist_not_verified')
    if f'doge-forward-evidence-{run_id}-{attempt}' not in names:gaps.append('forecast_artifact_missing')
    if f'doge-plus-evidence-v1-0-{run_id}-{attempt}' not in names:gaps.append('source_archive_artifact_missing')
    if any(s.get('name')=='Preserve newly prepared daily PIT package without claiming WORM' and s.get('conclusion')=='failure' for s in steps):gaps.append('daily_pit_upload_failed')
    if run.get('conclusion')!='success':gaps.append('parent_run_not_successful')
    return {'schema_version':1,'observed_at_utc':datetime.now(timezone.utc).isoformat(),
        'run':{k:run.get(k) for k in ('id','run_attempt','head_sha','event','created_at','updated_at','conclusion')},
        'status':'INCOMPLETE_EXECUTION' if gaps else 'EXECUTION_METADATA_COMPLETE_NOT_CONTENT_VERIFIED',
        'gaps':gaps,'jobs':[{'id':j['id'],'conclusion':j.get('conclusion'),'steps':j.get('steps',[])} for j in jobs],
        'available_artifacts':[{k:a.get(k) for k in ('id','name','size_in_bytes','digest','expired')} for a in artifacts],
        'engineering_accepted':False,'artifact_content_readback_verified':False,
        'unpersisted_evidence_recovered':False,'independent_retention_verified':False,
        'next_action':'Inspect available evidence and resume genuine current collection; missing historical quotes remain missing'}

def main():
    event=json.loads(Path(os.environ['GITHUB_EVENT_PATH']).read_text(encoding='utf-8'))['workflow_run']
    path=Path('engineering_completion_receipt.json')
    try:out=audit(event)
    except Exception as exc:
        out={'status':'COMPLETION_AUDIT_BLOCKED','error':type(exc).__name__+': '+str(exc)[:300],
             'engineering_accepted':False,'unpersisted_evidence_recovered':False}
        path.write_text(json.dumps(out,indent=2)+'\n',encoding='utf-8');raise
    path.write_text(json.dumps(out,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:out[k] for k in ('status','gaps','engineering_accepted')}))

if __name__=='__main__':main()
