"""Independent PLUS research audit; frozen registered audit remains untouched."""
import phase1_research_audit as frozen


def market(path, step, now):
    """Require every registered asset, then retain all frozen quality checks."""
    frame, report = frozen.market(path, step, now)
    if set(report['symbols']) != set(frozen.SYMBOLS):
        raise ValueError('required symbols missing from market data')
    return frame, report
