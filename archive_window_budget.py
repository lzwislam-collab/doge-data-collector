"""Engineering time reserve; frozen source selection and eligibility are unchanged."""
from pathlib import Path

import p2_paired_execution_v3_1 as engine


def plan(rows, now, maximum, reserve_seconds):
    if not 0 < maximum <= 2400 or reserve_seconds != 1800:
        raise ValueError('bounded foreground duration and fixed archive reserve required')
    active = []
    for horizon in (1, 24):
        # Include the complete original prefix before selecting reservations.
        # Filtering by current time first would silently reselect excluded IDs.
        selected = engine.reserve([r for r in rows if int(r['horizon_hours']) == horizon])
        active.extend(r for r in selected
                      if engine.utc(r['issued_at_utc']) <= now < engine.utc(r['target_utc']))
    latest = max(active, key=lambda r: engine.utc(r['target_utc'])) if active else None
    duration = maximum
    if latest:
        duration = max(1, min(maximum, int((engine.utc(latest['target_utc']) - now).total_seconds()) - reserve_seconds))
    return {'duration_seconds': duration, 'maximum_seconds': maximum,
            'archive_reserve_seconds': reserve_seconds, 'planned_at_utc': now.isoformat(),
            'selected_forecast_id': latest['forecast_id'] if latest else None,
            'selected_target_utc': latest['target_utc'] if latest else None,
            'formal_eligibility_claimed': False}


def from_root(root, now, maximum, reserve_seconds):
    root = Path(root)
    policy = engine.guard(root)
    rows = engine.frozen.read_rows(root / 'data/direction_v42/forward_registry.csv', engine.frozen.FCOLS)
    effective = engine.utc(policy['effective_from_utc'])
    return plan([r for r in rows.values() if engine.utc(r['issued_at_utc']) >= effective],
                now, maximum, reserve_seconds)

