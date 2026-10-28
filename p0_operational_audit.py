"""Strict operational monitor. Frozen V2 scoring/protocol remains authoritative."""
import hashlib, json
from datetime import datetime, timedelta, timezone
import forward_validation as base
import forward_validation_v2 as v2
import direction_forward_v42 as frozen
import quote_evidence_v2 as evidence

# Original public identities already recorded in P2 books and this fixed P0 proof.
# These locators are not replacement publication times or edits to witness history.
_CAPTURED_SOURCE_PROOF_COMMIT='2ff6e82aa5872d47e7e63e7e87017af85ebfef92'
_CAPTURED_SOURCE_WITNESSES={
    'DOGE_DIRECTION_ISSUED_QUOTE_SHADOW_V42_1_1792987200000_1h':
        ('40272071041','aeeb87094691b34757c92b44da1a41aa130912c4','2026-10-26T05:01:43+00:00'),
    'DOGE_DIRECTION_ISSUED_QUOTE_SHADOW_V42_1_1793008800000_1h':
        ('40311990494','43b4a2ad11d8773b56dce2bdd30b50f1778d7f5f','2026-10-26T11:02:05+00:00')}

def recover_captured_source_witness(root,row,bindings):
    fid=row['forecast_id']
    if fid not in _CAPTURED_SOURCE_WITNESSES:raise ValueError('forecast outside original source recovery scope')
    rid,head,observed=_CAPTURED_SOURCE_WITNESSES[fid]
    repo='lzwislam-collab/doge-data-collector'
    witness={'forecast_id':fid,'run_id':rid,'head_sha':head,'observed_public_utc':observed,
             'run_url':f'https://github.com/{repo}/actions/runs/{rid}'}
    base.git(root,'merge-base','--is-ancestor',_CAPTURED_SOURCE_PROOF_COMMIT,'HEAD')
    proof=json.loads(base.git(root,'show',_CAPTURED_SOURCE_PROOF_COMMIT+':data/p0_operations/latest.json'))
    proof=proof.get('forecast_proofs',{}).get(fid,{})
    if (proof.get('publication')!=witness or proof.get('issued_at_utc')!=row['issued_at_utc']
        or proof.get('source_quote_utc')!=row['source_quote_utc']
        or proof.get('quote_id')!=bindings.get(fid+'|ISSUE',{}).get('quote_id')):
        raise ValueError('original source proof identity mismatch')
    run=base.github_json('/actions/runs/'+rid)
    if (str(run.get('id'))!=rid or run.get('head_sha')!=head
        or run.get('html_url')!=witness['run_url']
        or run.get('repository',{}).get('full_name')!=repo
        or run.get('head_repository',{}).get('full_name')!=repo
        or frozen.utc(run.get('created_at',''))!=frozen.utc(observed)):
        raise ValueError('original source run identity mismatch')
    # Reuse frozen server/head/registry checks without changing CSV or cache.
    return base.remote_witnesses(root,{fid:row},{fid:witness},[run],persist_cache=False)[fid]

def fetch_operational_runs(since, request=None, until=None):
    from urllib.parse import urlencode
    request=request or base.github_json
    workflow=request('/actions/workflows/okx_hourly_extreme_history.yml')['id']
    rows={};until=until or datetime.now(timezone.utc);start=since
    # GitHub limits filtered run searches to 1000 results, regardless of pagination.
    # Daily partitions keep 6/hour plus push/manual attempts safely below this limit.
    while start<until:
        end=min(start+timedelta(days=1),until)
        span=start.isoformat().replace('+00:00','Z')+'..'+end.isoformat().replace('+00:00','Z')
        for page in range(1,11):
            query=urlencode({'per_page':100,'page':page,'created':span})
            batch=request('/actions/workflows/'+str(workflow)+'/runs?'+query)['workflow_runs']
            for run in batch:rows[run['id']]=run
            if len(batch)<100:break
        else:raise ValueError('one-day operational query exceeds 1000 records; cannot classify missing runs')
        start=end
    return list(rows.values())

def alert_reasons(report):
    """Flag *all* missing issuance, not just the last slot.

    One stable daily coverage alert per horizon prevents a 10-minute poll from
    creating a new Issue comment on every run when earlier slots are missing.
    The detailed missing slots remain in rolling_7d for forensic inspection.
    """
    reasons=[]
    at=frozen.utc(report['audited_at_utc']) if report.get('audited_at_utc') else None
    for h,metric in report.get('rolling_7d',{}).items():
        slots=metric.get('slots',[])
        expected=int(metric.get('expected',len(slots)))
        issued=int(metric.get('issued',sum(x['status']=='verified_issued' for x in slots)))
        if expected<0 or issued<0 or issued>expected or expected!=len(slots):
            raise ValueError('P0 slot-count mismatch for '+h)
        if expected and issued/expected<.90:
            if not at:
                # Backward-compatible unit-test reports may omit audit time.
                # Use a fixed non-realtime marker, not an invented execution time.
                day='unknown'
            else:
                day=at.date().isoformat()
            reasons.append({'horizon':h,'kind':'issuance_coverage_below_90pct',
                'audit_day_utc':day,'required_pct':90})
        if slots and slots[-1]['status']!='verified_issued':
            reasons.append({'horizon':h,'kind':'missing_issuance',
                'slot_start_utc':slots[-1]['slot_start_utc'],'status':slots[-1]['status']})
        if metric.get('target_missing',0):
            reasons.append({'horizon':h,'kind':'missing_target_evidence',
                'forecast_ids':metric['target_missing_ids']})
    return reasons

def reconcile(rows, scores, eligible, effective, now, runs, horizon=1, window_days=7):
    if window_days not in (7,30) or horizon not in (1,24):
        raise ValueError('unsupported operational window/horizon')
    interval=timedelta(hours=horizon)
    grace=timedelta(minutes=45) if horizon==1 else interval
    closed=max(0,int((now-effective-grace).total_seconds()//interval.total_seconds())+1)
    window_hours=window_days*24
    first=max(0,closed-int(window_hours/horizon))
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
    missing_ids=sorted(r['forecast_id'] for r in due if r['forecast_id'] not in eligible or scores.get(r['forecast_id'],{}).get('status')!='scored')
    scored=len(due)-len(missing_ids)
    return {'expected':len(slots),'issued':issued,'missing':len(slots)-issued,
            'coverage_pct':100*issued/len(slots) if slots else None,'slots':slots,
            'target_due':len(due),'target_scored':scored,'target_missing':len(due)-scored,
            'target_missing_ids':missing_ids,
            'target_coverage_pct':100*scored/len(due) if due else None,
            'seven_full_days_observed':closed>=int(168/horizon),'rolling_window_hours':window_hours,
            'full_window_observed':closed>=int(window_hours/horizon),
            'window_accepted':bool(closed>=int(window_hours/horizon) and slots
                and 100*issued/len(slots)>=(99 if window_days==30 else 90)
                and due and 100*scored/len(due)>=(99 if window_days==30 else 90)),
            'original_effective_from_utc':effective.isoformat()}

def evaluate(root=frozen.ROOT, now=None, runs=None):
    now=now or datetime.now(timezone.utc)
    protocol=json.loads((root/v2.PROTOCOL).read_text());v2.check_protocol(protocol);v2.check_code(root,protocol)
    base.locked_file(root,v2.PROTOCOL)
    if runs is None:
        recent=base.fetch_runs()
        operational=fetch_operational_runs(max(frozen.utc(protocol['effective_from_utc']),now-timedelta(days=30,hours=24)),until=now)
        runs=list({r['id']:r for r in recent+operational}.values())
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
            issued=frozen.utc(row['issued_at_utc'])
            if fid in _CAPTURED_SOURCE_WITNESSES and (not witness
                or not issued-timedelta(seconds=1)<=frozen.utc(witness['observed_public_utc'])<=issued+timedelta(minutes=45)
                or frozen.utc(witness['observed_public_utc'])>=frozen.utc(row['target_utc'])):
                witness=recover_captured_source_witness(root,row,bindings)
            if not witness: raise ValueError('no pre-target GitHub server run witness')
            public=frozen.utc(witness['observed_public_utc'])
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
    thirty={str(h)+'h':reconcile(list(registry.values()),valid_scores,eligible,effective,now,runs,h,window_days=30) for h in (1,24)}
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
    out={'schema_version':1,'status':'ok','audited_at_utc':now.isoformat(),'input_head':base.git(root,'rev-parse','HEAD'),
            'scope':'supplementary strict P0 operational monitor; does not replace frozen V2 scores',
            'frozen_protocol_id':protocol['protocol_id'],'frozen_implementation_verified':True,
            'official_frozen_v2_issuance':{h:m['issuance'] for h,m in official.get('horizons',{}).items()},
            'rolling_7d':horizons,'rolling_30d':thirty,
            'p0_30d_accepted':all(m['window_accepted'] for m in thirty.values()),
            'forecast_proofs':proofs,'rejected_evidence':rejected,'hourly_source':source,
            'monitor_code_sha256':hashlib.sha256((root/'p0_operational_audit.py').read_bytes()).hexdigest(),
            'p0_7d_accepted':all(m['seven_full_days_observed'] and (m['coverage_pct'] or 0)>=90 and m['target_due']>0 and (m['target_coverage_pct'] or 0)>=90 for m in horizons.values()),
            'p1_accuracy_accepted':False,'p2_profit_accepted':False,'no_retrospective_prediction_backfill':True}
    out['alert_reasons']=alert_reasons(out)
    return out

def main():
    try: out=evaluate()
    except Exception as exc: out={'status':'blocked','audited_at_utc':datetime.now(timezone.utc).isoformat(),'error':str(exc),'p0_7d_accepted':False}
    frozen.write(frozen.ROOT/'data/p0_operations/latest.json',json.dumps(out,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'status':out['status'],'p0_7d_accepted':out['p0_7d_accepted'],'error':out.get('error')}))
    return 0 if out['status']=='ok' else 1

if __name__=='__main__': raise SystemExit(main())
