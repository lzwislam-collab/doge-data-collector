"""Recover publication using remote IDs; frozen issuer/publisher stay unchanged."""
import csv, io, json, os
from pathlib import Path
import publish_forward_evidence as base

REG=base.PATHS[0]

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
    try:result=publish_fn(root)
    finally:
        if output is not None:os.environ['GITHUB_OUTPUT']=output
    if not isinstance(result,dict) or result.get('status')!='verified_remote_readback':
        raise ValueError('publication not independently read back')
    # A post-push fetch/readback is part of the frozen publisher contract.
    public=ids(base.command(root,'show','FETCH_HEAD:'+REG))
    if not current.issubset(public):raise ValueError('recovered forecast missing remotely')
    new=sorted(current-previous)
    result.update(new_predictions=bool(new),new_ids=new,
                  recovery_reference='fetched_remote_main_before_publication')
    if output:
        with open(output,'a',encoding='utf-8') as f:
            f.write('new_predictions='+str(bool(new)).lower()+'\n')
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
