"""Publish current genuine evidence, then read it back from the remote."""
import csv,hashlib,io,json,os,subprocess,time
from datetime import datetime,timezone
import direction_forward_v42 as frozen
import forward_validation as base
import quote_evidence_v2 as evidence

PATHS=('data/direction_v42/forward_registry.csv','data/direction_v42/forward_scores.csv',
       'data/direction_v42/forward_latest.json',evidence.BINDINGS)

def command(root,*args):
    return subprocess.run(['git',*args],cwd=root,check=True,capture_output=True).stdout

def verify_readback(root,head,expected):
    for path,data in expected.items():
        try: actual=command(root,'show',head+':'+path)
        except subprocess.CalledProcessError:return False
        # CSV/status files may use platform line endings; raw snapshots are exact bytes.
        if '/snapshots/' not in path:
            data=data.replace(b'\r\n',b'\n');actual=actual.replace(b'\r\n',b'\n')
        if actual!=data:return False
    return True

def publish(root, remote='origin', validate=None):
    reg=PATHS[0]
    try: old=command(root,'show','HEAD:'+reg).decode('utf-8')
    except subprocess.CalledProcessError:old='forecast_id\n'
    previous={r['forecast_id'] for r in csv.DictReader(io.StringIO(old))}
    current={r['forecast_id'] for r in csv.DictReader(io.StringIO((root/reg).read_text(encoding='utf-8')))} if (root/reg).exists() else set()
    paths=[p for p in PATHS if (root/p).exists()]
    paths+=sorted(p.relative_to(root).as_posix() for p in (root/evidence.BASE/'snapshots').glob('*/*.json'))
    if paths: command(root,'add','-f','--',*paths)
    if command(root,'diff','--cached','--name-only').strip():command(root,'commit','-m','data: publish genuine prospective quote evidence')
    for attempt in range(1,4):
        try:
            command(root,'pull','--rebase','--autostash',remote,'main')
            if validate:validate(root)
            command(root,'push',remote,'HEAD:main')
            break
        except subprocess.CalledProcessError:
            subprocess.run(['git','rebase','--abort'],cwd=root,capture_output=True)
            if attempt==3:raise
            time.sleep(attempt*2)
    head=command(root,'rev-parse','HEAD').decode().strip()
    expected={p:(root/p).read_bytes() for p in paths}
    command(root,'fetch',remote,'main')
    if not verify_readback(root,'FETCH_HEAD',expected):raise ValueError('remote evidence readback mismatch')
    # Compare the actual remote public content; successful push alone is insufficient.
    return {'status':'verified_remote_readback','commit_sha':head,
            'remote_head':command(root,'rev-parse','FETCH_HEAD').decode().strip(),
            'new_predictions':bool(current-previous),'new_ids':sorted(current-previous),
            'verified_files':len(expected),'verified_at_utc':datetime.now(timezone.utc).isoformat(),
            'registry_sha256':hashlib.sha256(expected[reg]).hexdigest() if reg in expected else None}

def validate_state(root):
    import forward_validation_v2 as v2
    protocol=json.loads((root/v2.PROTOCOL).read_text());v2.check_protocol(protocol);v2.check_code(root,protocol)
    # Do not publish if the frozen evidence collector left a partial ISSUE/TARGET binding.
    registry=frozen.read_rows(root/base.REG,frozen.FCOLS);scores=frozen.read_rows(root/base.SCORES,frozen.SCOLS)
    bindings=frozen.read_rows(root/evidence.BINDINGS,evidence.BCOLS)
    base.validate_scores(registry,scores,datetime.now(timezone.utc))
    for path,cols,rows in [(base.REG,frozen.FCOLS,registry),(base.SCORES,frozen.SCOLS,scores),(evidence.BINDINGS,evidence.BCOLS,bindings)]:base.history_receipts(root,path,cols,rows)
    effective=frozen.utc(json.loads((root/'data/stage5/forward_validation_protocol_v2.json').read_text())['effective_from_utc'])
    for fid,row in registry.items():
        if frozen.utc(row['issued_at_utc'])>=effective:evidence.verify_bound(root,row,scores.get(fid),bindings)
def main():
    root=frozen.ROOT
    command(root,'config','user.name','github-actions[bot]')
    command(root,'config','user.email','41898282+github-actions[bot]@users.noreply.github.com')
    validate_state(root)
    out=publish(root,validate=validate_state)
    frozen.write(root/'data/p0_operations/publication_latest.json',json.dumps(out,indent=2)+'\n')
    if os.environ.get('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'],'a',encoding='utf-8') as stream:
            stream.write('new_predictions='+str(out['new_predictions']).lower()+'\n')
    print(json.dumps(out));return 0

if __name__=='__main__':raise SystemExit(main())
