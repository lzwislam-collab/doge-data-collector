"""Second bounded quote pass only for genuinely open targets after slow steps.

Runs inside the existing serialized hourly workflow, after frozen integrity and
first capture. Expired windows never cause a capture or a reconstructed score.
"""
import json
from datetime import datetime, timezone
from pathlib import Path
import forward_diagnostics as diagnostics
import quote_capture_retry as retry


def run(root=diagnostics.ROOT, now=None, capture=None):
    root=Path(root); now=now or datetime.now(timezone.utc)
    reg=diagnostics.read_csv(root/diagnostics.SOURCES['production_registry'],required=True,
        required_columns=('forecast_id','model_id','issued_at_utc','target_utc'))
    scores=diagnostics.read_csv(root/diagnostics.SOURCES['production_scores'],required=True,
        required_columns=('forecast_id','status'))
    radar=diagnostics.deadlines(reg,{r['forecast_id']:r for r in scores},now)
    open_ids=[r['forecast_id'] for r in radar if r['state'] in ('DUE_OPEN','DUE_URGENT')]
    result={'observed_at_utc':now.isoformat(),'open_ids':open_ids,'historical_backfill_allowed':False}
    if not open_ids: result.update(status='SKIP_NO_OPEN_TARGET',attempts=0)
    else:
        result['capture']=(capture or retry.capture)('watch',root=root)
        result.update(status=result['capture']['status'],attempts=result['capture']['attempts'])
    return result


if __name__=='__main__':
    try:
        result=run(); print(json.dumps(result))
        raise SystemExit(1 if result['status']=='failed' else 0)
    except Exception as exc:
        print(json.dumps({'status':'BLOCKED','error':str(exc)})); raise SystemExit(1)
