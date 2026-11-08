"""Engineering time reserve; frozen source selection and eligibility are unchanged.

The workflow cap is a planning limit, not an interrupt for in-flight commands.
It preserves time for the existing capture handoff, persistence and archive tail.
"""
from datetime import timedelta
from pathlib import Path

import p2_paired_execution_v3_1 as engine


class WorkflowBudgetExhausted(TimeoutError):
    def __init__(self, budget):
        super().__init__('workflow budget exhausted before required archive tail reserve')
        self.budget = budget


def plan(rows, now, maximum, reserve_seconds, *, workflow_started_at=None,
         workflow_timeout_seconds=None):
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
    result = {'duration_seconds': duration, 'maximum_seconds': maximum,
              'archive_reserve_seconds': reserve_seconds, 'planned_at_utc': now.isoformat(),
              'selected_forecast_id': latest['forecast_id'] if latest else None,
              'selected_target_utc': latest['target_utc'] if latest else None,
              'formal_eligibility_claimed': False}
    if workflow_started_at is not None or workflow_timeout_seconds is not None:
        if (workflow_started_at is None or type(workflow_timeout_seconds) is not int
                or not 0 < workflow_timeout_seconds <= 3900):
            raise ValueError('workflow start and bounded job timeout are required together')
        started = engine.utc(workflow_started_at)
        if now.tzinfo is None or now.utcoffset() != timedelta(0):
            raise ValueError('UTC workflow clock required')
        elapsed = (now-started).total_seconds()
        if elapsed < 0:
            raise ValueError('workflow start cannot be in the future')
        # Run 42433449972 spent 400 seconds beyond its nominal capture duration.
        # Reserve ten minutes for setup/cycle/final publication rather than
        # repeating that observed overrun with no room left for the tail.
        # This remains a planning margin, not a hard subprocess timeout.
        completion_margin = 600
        available = int(workflow_timeout_seconds-elapsed-reserve_seconds-completion_margin)
        result['duration_seconds'] = max(0, min(duration, available))
        result['workflow_budget'] = {
            'started_at_utc': started.isoformat(),
            'timeout_seconds': workflow_timeout_seconds,
            'elapsed_seconds': elapsed,
            'tail_reserve_seconds': reserve_seconds,
            'cycle_and_publication_margin_seconds': completion_margin,
            'available_capture_seconds': available,
            'soft_limit_only': True,
        }
        if available < 1:
            raise WorkflowBudgetExhausted(result)
    return result


def from_root(root, now, maximum, reserve_seconds, *, workflow_started_at=None,
              workflow_timeout_seconds=None):
    root = Path(root)
    policy = engine.guard(root)
    rows = engine.frozen.read_rows(root / 'data/direction_v42/forward_registry.csv', engine.frozen.FCOLS)
    effective = engine.utc(policy['effective_from_utc'])
    return plan([r for r in rows.values() if engine.utc(r['issued_at_utc']) >= effective],
                now, maximum, reserve_seconds,
                workflow_started_at=workflow_started_at,
                workflow_timeout_seconds=workflow_timeout_seconds)
