"""Waive only a genuine current-attempt late skip, never transport failure."""
import json, math, sys
from datetime import datetime, timezone
from pathlib import Path

def utc(value):
    result=datetime.fromisoformat(value.replace('Z','+00:00'))
    if result.tzinfo is None:raise ValueError('timezone required')
    return result.astimezone(timezone.utc)

def verify(report,issue,run_start):
    if report.get('status')!='stale_hour_no_new_registration' or report.get('new_ids')!=[]:
        raise ValueError('not a genuine late skip')
    if issue.get('status')!='failed' or type(issue.get('production_new')) is not int or issue['production_new']!=0 or issue.get('pair_error') is not None:
        raise ValueError('partial issuance or paired failure cannot be waived')
    capture=issue['capture'];events=capture['events']
    if capture.get('status')!='failed' or capture.get('attempts')!=1 or len(events)!=1:
        raise ValueError('only a single returned late attempt can be waived')
    attempt=events[0]
    if type(attempt.get('exit_code')) is not int or attempt['exit_code']!=1 or attempt.get('error') or capture.get('error') or capture.get('registry_changed'):
        raise ValueError('transport/mutation failure cannot be waived')
    started=utc(attempt['started_at_utc']);generated=utc(report['generated_utc']);observed=utc(issue['observed_at_utc'])
    if not utc(run_start)<=started<=generated<=observed or (observed-started).total_seconds()>900:
        raise ValueError('report/receipt is not from the current attempt')
    lag=(generated-utc(report['feature_close_at_utc'])).total_seconds()/60
    declared=report['feature_lag_min']
    if type(declared) not in (int,float) or not math.isfinite(declared) or lag<=45 or abs(declared-lag)>0.011:
        raise ValueError('late feature clock mismatch')
    return {'status':'CURRENT_LATE_BUT_SAFE','generated_at_utc':generated.isoformat(),'new_predictions':False}

def main():
    path=Path(sys.argv[2])
    if sys.argv[1]=='begin':
        path.write_text(json.dumps({'started_at_utc':datetime.now(timezone.utc).isoformat()})+'\n',encoding='utf-8')
    elif sys.argv[1]=='verify':
        root=Path(__file__).resolve().parent
        report=json.loads((root/'data/direction_v42/forward_latest.json').read_bytes())
        issue=json.loads((root/'data/paired_forward_v1/issue_latest.json').read_bytes())
        start=json.loads(path.read_bytes())['started_at_utc']
        print(json.dumps(verify(report,issue,start)))
    else:raise ValueError('unknown lateness gate mode')

if __name__=='__main__':main()
