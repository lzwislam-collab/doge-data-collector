"""Read-only verification; writes a new receipt beside this audit only."""
import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import phase3_runtime as frozen
import plus_p1_audit as p1
import p2_plus_review as p2

now = datetime.now(timezone.utc)
frozen.guard()
_, hourly = p1.market(ROOT / 'data/hourly_research/ohlc.csv', 3600000, now)
_, daily = p1.market(ROOT / 'data/history/daily.csv', 86400000, now)
base = '59dab912fd08bc3033355dc76696b13be37a4dd7'
changed = subprocess.check_output(['git', 'diff', '--name-status', base], cwd=ROOT, text=True)
if any(line and not line.startswith('A\t') for line in changed.splitlines()):
    raise ValueError('original tracked file changed')
out = Path(__file__).resolve().parent
tests = (out / 'tests.log').read_text(encoding='utf-8')
if '\nOK (skipped=5)' not in tests and '\nOK\n' not in tests:
    raise ValueError('complete successful test log required')
draft = json.loads((out / 'p2/future_shadow_protocol_draft.json').read_bytes())
receipt = {
    'audited_at_utc': now.isoformat(), 'source_base_commit': base,
    'mode': 'LOCAL_READ_ONLY_ENGINEERING_REVIEW_NO_REMOTE_ISSUANCE_CERTIFICATION',
    'original_file_changes': [], 'frozen_guard_passed': True,
    'rolling_archive': frozen.rolling_audit(), 'hourly': hourly, 'daily': daily,
    'p2_local': p2.local_audit(ROOT, now),
    'inactive_future_protocol_validation': p2.validate_future_protocol(draft),
    'test_summary': [line for line in tests.splitlines() if line.startswith(('Ran ', 'OK', 'FAILED'))],
    'evidence_sha256': {str(p.relative_to(out)).replace('\\', '/'): hashlib.sha256(p.read_bytes()).hexdigest()
                        for p in sorted(out.rglob('*')) if p.is_file() and p.name != 'integration_receipt.json'},
    'production_model_changed': False, 'promotion_authorized': False,
    'predictions_backfilled': False, 'existing_schedules_changed': False,
    'p0_acceptance_claimed': False, 'validated_70pct': False, 'profit_validated': False,
}
(out / 'integration_receipt.json').write_bytes((json.dumps(receipt, sort_keys=True, indent=2, allow_nan=False)+'\n').encode('utf-8'))
print(json.dumps({'frozen_guard_passed': True, 'original_file_changes': [], 'test_summary': receipt['test_summary'],
                  'promotion_authorized': False, 'p0_acceptance_claimed': False}))
