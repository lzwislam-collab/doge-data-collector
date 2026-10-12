"""Retry only transient transport failures before registry mutation. No backfill."""
import hashlib, json, socket, sys, time, urllib.error
from datetime import datetime, timezone
import quote_evidence_v2 as evidence

def state(root):
    paths=('data/direction_v42/forward_registry.csv','data/direction_v42/forward_scores.csv',evidence.BINDINGS)
    return {p: hashlib.sha256((root/p).read_bytes()).hexdigest() if (root/p).exists() else None for p in paths}

def capture(mode, root=None, collect=None, sleep=None):
    root=root or evidence.ROOT; collect=collect or evidence.collect; sleep=sleep or time.sleep
    before=state(root); attempts=[]
    for number in range(1,4):
        entry={'attempt':number,'started_at_utc':datetime.now(timezone.utc).isoformat()}
        try:
            code=collect(root,mode)
            entry['exit_code']=code; attempts.append(entry)
            return {'status':'ok' if code==0 else 'failed','attempts':number,'events':attempts}
        except Exception as exc:
            entry['error']=type(exc).__name__+': '+str(exc)[:300]; attempts.append(entry)
            transient=isinstance(exc,(urllib.error.URLError,TimeoutError,ConnectionError,socket.timeout))
            if isinstance(exc,urllib.error.HTTPError): transient=exc.code in (408,429,500,502,503,504)
            changed=state(root)!=before
            if number==3 or changed or not transient:
                return {'status':'failed','attempts':number,'events':attempts,'registry_changed':changed,'error':entry['error']}
            sleep(number*2)

def main():
    mode=sys.argv[1] if len(sys.argv)>1 else 'watch'
    if mode not in ('watch','direction'): raise ValueError('invalid capture mode')
    out=capture(mode);out.update(mode=mode,observed_at_utc=datetime.now(timezone.utc).isoformat())
    evidence.frozen.write(evidence.ROOT/f'data/p0_operations/retry_{mode}.json',json.dumps(out,indent=2)+'\n')
    print(json.dumps(out))
    return 0 if out['status']=='ok' else 1

if __name__=='__main__': raise SystemExit(main())
