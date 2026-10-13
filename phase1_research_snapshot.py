"""Export immutable research input from the registered Git commit, never reset main."""
import argparse
import io
import json
import subprocess
import tarfile
from pathlib import Path
import phase1_research_audit as audit

ROOT=Path(__file__).resolve().parent


def export(repo,out,frozen):
    if out.exists() and any(out.iterdir()):raise ValueError('snapshot output must be empty')
    commit=frozen['source_commit']
    if len(commit)!=40 or any(c not in '0123456789abcdef' for c in commit):raise ValueError('exact Git commit required')
    raw=subprocess.check_output(['git','-c','core.autocrlf=false','archive','--format=tar',commit],cwd=repo)
    out.mkdir(parents=True,exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(raw),mode='r:') as archive:
        for member in archive:
            target=audit.safe(out,member.name)
            if member.isdir():target.mkdir(parents=True,exist_ok=True)
            elif member.isfile():
                target.parent.mkdir(parents=True,exist_ok=True)
                with target.open('xb') as stream:stream.write(archive.extractfile(member).read())
            else:raise ValueError('links/devices not allowed in snapshot')
    return audit.verify(out,frozen)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True);args=p.parse_args()
    frozen=json.loads((ROOT/'research/phase1_v1/frozen_assets.json').read_bytes())
    print(json.dumps(export(ROOT,args.out,frozen),sort_keys=True))
