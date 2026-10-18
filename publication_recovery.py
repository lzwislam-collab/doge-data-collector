"""Recover publication using remote IDs; frozen issuer/publisher stay unchanged."""
import csv, io, json, os, time, hashlib
from pathlib import Path
import publish_forward_evidence as base

REG=base.PATHS[0]
SOURCE='data/hourly_research/ohlc.csv'

def source_failure(stage,exc):
    failure={'status':'error','stage':stage,
             'errors':[type(exc).__name__+': '+str(exc)],'publication_verified':False}
    try:
        from okx_hourly_research_history import save_refresh_receipt
        save_refresh_receipt(failure)
    except Exception as receipt_error:
        failure['receipt_error']=type(receipt_error).__name__+': '+str(receipt_error)
    # Runner logs remain a fallback if the local artifact directory is unwritable.
    print(json.dumps({'hourly_source_failure':failure}))
    return failure

def ids(raw):
    rows=list(csv.DictReader(io.StringIO(raw.decode('utf-8'))))
    values=[r['forecast_id'] for r in rows]
    if len(values)!=len(set(values)) or any(not x for x in values):
        raise ValueError('duplicate/empty publication forecast ID')
    return set(values)

def publish(root, publish_fn):
    base.command(root,'fetch','origin','main')
    paths=base.command(root,'ls-tree','-r','--name-only','FETCH_HEAD').decode().splitlines()
    previous=ids(base.command(root,'show','FETCH_HEAD:'+REG)) if REG in paths else set()
    current=ids((root/REG).read_bytes())
    output=os.environ.pop('GITHUB_OUTPUT',None)
    source_raw=None
    source_error=None
    try:
        if os.environ.get('DOGE_VERIFY_HOURLY_PUBLICATION')=='1':
            try:
                from prospective_witness_budget import assert_source_fresh
                if os.environ.get('DOGE_HOURLY_REFRESH_FAILED')=='1':
                    raise ValueError('bounded source refresh failed; preserve original evidence publication')
                raw=(root/SOURCE).read_bytes()
                assert_source_fresh(raw,int(time.time()*1000))
                base.command(root,'add','-f','--',SOURCE)
                source_raw=raw
            except Exception as exc:
                # Only unstage this optional source; retain working bytes and all core evidence.
                base.command(root,'reset','HEAD','--',SOURCE)
                source_error=source_failure('PUBLICATION_INVOCATION_GUARD',exc)
        result=publish_fn(root)
    finally:
        if output is not None:os.environ['GITHUB_OUTPUT']=output
    if not isinstance(result,dict) or result.get('status')!='verified_remote_readback':
        raise ValueError('publication not independently read back')
    if source_raw is not None:
        try:
            remote=base.command(root,'show','FETCH_HEAD:'+SOURCE)
            if remote!=source_raw:raise ValueError('hourly source exact remote readback mismatch')
            result.update(hourly_source_public_readback=True,hourly_source_sha256=hashlib.sha256(source_raw).hexdigest(),hourly_source_status='verified_remote_readback')
        except Exception as exc:source_error=source_failure('SOURCE_REMOTE_READBACK',exc)
    if source_error is not None:
        result.update(hourly_source_public_readback=False,hourly_source_status='error',hourly_source_failure=source_error)
    # A post-push fetch/readback is part of the frozen publisher contract.
    public=ids(base.command(root,'show','FETCH_HEAD:'+REG))
    if not current.issubset(public):raise ValueError('recovered forecast missing remotely')
    new=sorted(current-previous)
    result.update(new_predictions=bool(new),new_ids=new,
                  recovery_reference='fetched_remote_main_before_publication')
    if output:
        with open(output,'a',encoding='utf-8') as f:
            f.write('new_predictions='+str(bool(new)).lower()+'\n')
            if source_error is not None:f.write('hourly_source_failed=true\n')
    return result

def main():
    import paired_forward_cohort as pair
    root=Path(__file__).resolve().parent
    def paired_publish(root):
        if pair.publish(root)!=0:raise ValueError('paired publication rejected')
        return json.loads((root/pair.BASE/'publication_latest.json').read_bytes())
    result=publish(root,paired_publish)
    pair.frozen.write(root/pair.BASE/'publication_latest.json',json.dumps(result,indent=2)+'\n')
    print(json.dumps(result))

if __name__=='__main__':main()
