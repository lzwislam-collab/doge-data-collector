"""Bounded collector publication retries; conflicts preserve first raw evidence."""
import re
import subprocess
from pathlib import Path

ROOT=Path(__file__).resolve().parent


def allowed(path):
    return (path in ('data/latest.json','data/hourly.csv','data/derivatives_pit_v1/latest.json',
                     'data/derivatives_pit_v1/observations.jsonl','data/derivatives_pit_v1/failures.jsonl')
            or re.fullmatch(r'data/derivatives_pit_v1/raw/[a-f0-9]{64}\.json',path) is not None)


def command(*args):return subprocess.run(['git',*args],cwd=ROOT).returncode


def publish(call=command):
    for attempt in range(3):
        if call('push','origin','HEAD:main')==0:return True
        if call('pull','--rebase','origin','main')!=0:
            call('rebase','--abort');return False
    return False


def main():
    subprocess.run(['git','config','user.name','github-actions[bot]'],cwd=ROOT,check=True)
    subprocess.run(['git','config','user.email','41898282+github-actions[bot]@users.noreply.github.com'],cwd=ROOT,check=True)
    paths=['data/latest.json','data/hourly.csv']
    paths.extend(p.relative_to(ROOT).as_posix() for p in (ROOT/'data/derivatives_pit_v1').rglob('*') if p.is_file())
    if any(not allowed(p) or (ROOT/p).is_symlink() for p in paths):raise ValueError('unsafe collector output')
    subprocess.run(['git','add','-f',*paths],cwd=ROOT,check=True)
    staged=subprocess.check_output(['git','diff','--cached','--name-only'],cwd=ROOT).decode().splitlines()
    if any(not allowed(p) for p in staged):raise ValueError('staged protected asset')
    if not staged:return
    subprocess.run(['git','commit','-m','data: update OKX market snapshot'],cwd=ROOT,check=True)
    if not publish():raise RuntimeError('publication blocked; first raw bytes retained in always-upload artifact')


if __name__=='__main__':main()
