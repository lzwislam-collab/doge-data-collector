"""Append-only reachable Git diff checks; no external tamperproof claim."""
import os,subprocess
def guard(root,path,current_bytes):
    def cmd(*args):return subprocess.run(['git',*args],cwd=root,check=True,capture_output=True).stdout
    try:commits=cmd('log','--reverse','--format=%H','--',path).decode().splitlines()
    except subprocess.CalledProcessError:
        if os.environ.get('GITHUB_ACTIONS')=='true':raise ValueError('full Git source history unavailable')
        return False
    historical=[]
    for commit in commits:
        diff=cmd('show','--format=','--unified=0',commit,'--',path).decode('utf-8')
        for line in diff.splitlines():
            if line.startswith('-') and not line.startswith('---'):raise ValueError('committed append ledger altered/deleted: '+path)
            if line.startswith('+') and not line.startswith('+++'):historical.append(line[1:])
    expected=('\n'.join(historical)+'\n').encode() if historical else b''
    if not current_bytes.startswith(expected):raise ValueError('committed ledger prefix/tail changed: '+path)
    return True
