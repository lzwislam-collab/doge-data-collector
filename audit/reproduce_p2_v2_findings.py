"""Read-only audit reproductions; intentionally expose frozen V2 defects."""
import json
from datetime import timedelta
from test_p2_paired_execution_v2 import PaperTests
import p2_paired_execution_v2 as p

t = PaperTests(); t.setUp()
rows = [t.item(str(i), offset=i) for i in range(120)]
selected = p.reserve(rows)
rows24 = [t.item(str(i), offset=i, h=24) for i in range(120)]
selected24 = p.reserve(rows24)
tail = t.replay([t.item()], [t.book(m) for m in (1, 20, 40, 61)], 600)['1h']
result = {
    'coverage_denominator_defect': {
        'all_hourly_source_rows': len(rows),
        'one_hour_reserved': len(selected),
        'one_hour_max_reported_verified_pct': 100*len(selected)/len(rows),
        'twenty_four_hour_reserved': len(selected24),
        'twenty_four_hour_max_reported_verified_pct': 100*len(selected24)/len(rows24),
        'required_pct': 90,
        'note': 'Frozen source_items verifies only reserved IDs, but run divides verified slots by all closed hourly slots.'
    },
    'tail_path_gap_defect': {
        'last_book_minutes': 61, 'audit_minutes': 600,
        'tail_seconds': (600-61)*60, 'maximum_path_seconds': p.GAP_SECONDS,
        'reported_path_gap_count': tail['path_gap_count'],
        'completed_prefix_intervals': tail['completed_prefix_intervals'],
        'note': 'No next book arrives: the tail gap is absent from summary.'
    },
    'synthetic_reproduction_only': True,
    'not_a_real_forecast_or_profit_interval': True
}
assert len(selected) == 60 and len(selected24) == 5
assert tail['path_gap_count'] == 0 and tail['completed_prefix_intervals'] == 1
print(json.dumps(result, indent=2))

