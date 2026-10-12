"""Strict operational monitor. Frozen V2 scoring/protocol remains authoritative."""
import hashlib, json
from datetime import datetime, timedelta, timezone
import forward_validation as base
import forward_validation_v2 as v2
import direction_forward_v42 as frozen
import quote_evidence_v2 as evidence

def reconcile(rows, scores, eligible, effective, now, runs, horizon=1):
    interval=timedelta(hours=horizon)
    grace=timedelta(minutes=45) if horizon==1 else interval
    closed=max(0,int((now-effective-grace).total_seconds()//interval.total_seconds())+1)
    first=max(0,closed-int(168/horizon))
    groups={}
    for row in rows:
        if int(row['horizon_hours'])!=horizon: continue
        issued=frozen.utc(row['issued_at_utc'])
        if effective<=issued<=now:
            slot=int((issued-effective).total_seconds()//interval.total_seconds())
            groups.setdefault(slot,[]).append(row)
    slots=[];due=[]
    # Keep original activation and closed slot boundaries; rolling seven days is supplementary.
    for i in range(first,closed):
        start=effective+i*interval;end=start+grace
        candidates=sorted(groups.get(i,[]),key=lambda r:(r['issued_at_utc'],r['forecast_id']))
        attempts=[r for r in runs if r.get('path','').split('@')[0]=='.github/workflows/okx_hourly_extreme_history.yml'
                  and start<=frozen.utc(r['created_at'])<start+interval]
        status='no_workflow_run' if not attempts else 'workflow_failed' if all(r.get('conclusion')=='failure' for r in attempts) else 'no_issuance'
        selected=candidates[:1] if horizon==24 else candidates
        if selected:
            row=selected[0];stamp=frozen.utc(row['issued_at_utc'])
            if horizon==1 and len(selected)>1: status='duplicate_issuance'
            elif stamp>end or (horizon==1 and frozen.utc(row['source_hour_start_utc'])!=start): status='late_issuance'
            elif row['forecast_id'] not in eligible: status='quote_or_publication_unverified'
            else: status='verified_issued'
            for row in selected:
                if frozen.utc(row['target_utc'])+timedelta(minutes=25)<=now: due.append(row)
        slots.append({'slot_start_utc':start.isoformat(),'deadline_utc':end.isoformat(),'status':status,
                      'forecast_ids':[r['forecast_id'] for r in candidates],
                      'run_ids':sorted(r['id'] for r in attempts),
                      'runs':[{'id':r['id'],'event':r.get('event'),'conclusion':r.get('conclusion'),'created_at':r['created_at']} for r in sorted(attempts,key=lambda r:r['id'])]})
    issued=sum(s['status']=='verified_issued' for s in slots)
    scored=sum(r['forecast_id'] in eligible and scores.get(r['forecast_id'],{}).get('status')=='scored' for r in due)
    return {'expected':len(slots),'issued':issued,'missing':len(slots)-issued,
            'coverage_pct':100*issued/len(slots) if slots else None,'slots':slots,
            'target_due':len(due),'target_scored':scored,'target_missing':len(due)-scored,
            'target_coverage_pct':100*scored/len(due) if due else None,
            'seven_full_days_observed':closed>=int(168/horizon),'rolling_window_hours':168,
            'original_effective_from_utc':effective.isoformat()}

def evaluate(root=frozen.ROOT, now=None, runs=None):
    now=now or datetime.now(timezone.utc)
    protocol=json.loads((root/v2.PROTOCOL).read_text());v2.check_protocol(protocol);v2.check_code(root,protocol)
    base.locked_file(root,v2.PROTOCOL)
    runs=base.fetch_runs() if runs is None else runs
    if not v2.protocol_witness(root,protocol,runs,False): raise ValueError('missing original protocol witness')
    legacy=base.evaluate(root,now,runs,persist_witnesses=False)
    registry=frozen.read_rows(root/base.REG,frozen.FCOLS);scores=frozen.read_rows(root/base.SCORES,frozen.SCOLS)
    bindings=frozen.read_rows(root/evidence.BINDINGS,evidence.BCOLS)
    base.history_receipts(root,evidence.BINDINGS,evidence.BCOLS,bindings)
    effective=frozen.utc(protocol['effective_from_utc']);eligible=set();valid_scores={};proofs={};rejected={}
    for fid,row in registry.items():
        if frozen.utc(row['issued_at_utc'])<effective: continue
        try:
            evidence.verify_bound(root,row,None,bindings)
            witness=legacy['verified_remote_head_witnesses'].get(fid)
            if not witness: raise ValueError('no pre-target GitHub server run witness')
            issued=frozen.utc(row['issued_at_utc']);public=frozen.utc(witness['observed_public_utc'])
            if not issued-timedelta(seconds=1)<=public<=issued+timedelta(minutes=45) or public>=frozen.utc(row['target_utc']): raise ValueError('late public witness')
            evidence.verify_public_issue(root,row,bindings,witness['head_sha'])
            # Binding itself must also exist at the server-witnessed public commit.
            import csv,io
            snap={r['forecast_id']:r for r in csv.DictReader(io.StringIO(base.git(root,'show',witness['head_sha']+':'+evidence.BINDINGS)))}
            if snap.get(fid+'|ISSUE')!=bindings.get(fid+'|ISSUE'): raise ValueError('ISSUE binding absent at public head')
            eligible.add(fid);proofs[fid]={'issued_at_utc':row['issued_at_utc'],'source_quote_utc':row['source_quote_utc'],
                'quote_id':bindings[fid+'|ISSUE']['quote_id'],'publication':witness,'commit':legacy['commit_receipts_not_publication_times'].get(fid)}
            score=scores.get(fid)
            if score:
                evidence.verify_bound(root,row,score,bindings)
                valid_scores[fid]=score
        except (ValueError,base.subprocess.CalledProcessError) as exc: rejected[fid]=str(exc)
    horizons={str(h)+'h':reconcile(list(registry.values()),valid_scores,eligible,effective,now,runs,h) for h in (1,24)}
    from okx_hourly_research_history import assess
    import csv
    with (root/'data/hourly_research/ohlc.csv').open(newline='',encoding='utf-8') as stream: hours=list(csv.DictReader(stream))
    keyed={(r['symbol'],int(r['open_utc_ms'])):r for r in hours}
    if len(keyed)!=len(hours): raise ValueError('duplicate hourly source key')
    source=assess(keyed)
    for symbol,item in source.items():
        age=(now-frozen.utc(item['last_confirmed_start_utc'])-timedelta(hours=1)).total_seconds()/60
        item['feature_age_minutes']=round(age,2)
    official=json.loads((root/'data/stage5/forward_validation_v2/latest.json').read_text())
    return {'schema_version':1,'status':'ok','audited_at_utc':now.isoformat(),'input_head':base.git(root,'rev-parse','HEAD'),
            'scope':'supplementary strict P0 operational monitor; does not replace frozen V2 scores',
            'frozen_protocol_id':protocol['protocol_id'],'frozen_implementation_verified':True,
            'official_frozen_v2_issuance':{h:m['issuance'] for h,m in official.get('horizons',{}).items()},
            'rolling_7d':horizons,'forecast_proofs':proofs,'rejected_evidence':rejected,'hourly_source':source,
            'monitor_code_sha256':hashlib.sha256((root/'p0_operational_audit.py').read_bytes()).hexdigest(),
            'p0_7d_accepted':all(m['seven_full_days_observed'] and (m['coverage_pct'] or 0)>=90 and m['target_due']>0 and (m['target_coverage_pct'] or 0)>=90 for m in horizons.values()),
            'p1_accuracy_accepted':False,'p2_profit_accepted':False,'no_retrospective_prediction_backfill':True}

def main():
    try: out=evaluate()
    except Exception as exc: out={'status':'blocked','audited_at_utc':datetime.now(timezone.utc).isoformat(),'error':str(exc),'p0_7d_accepted':False}
    frozen.write(frozen.ROOT/'data/p0_operations/latest.json',json.dumps(out,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'status':out['status'],'p0_7d_accepted':out['p0_7d_accepted'],'error':out.get('error')}))
    return 0 if out['status']=='ok' else 1

if __name__=='__main__': raise SystemExit(main())
