"""Fail-closed admission for NEW feature research. Frozen cohorts are unchanged.

No model imports this module yet. A positive result is necessary, never sufficient
for model registration. Witness and coverage must come from independent audits.
"""
import math
from datetime import datetime, timedelta


def utc(value):
    at = datetime.fromisoformat(value.replace('Z', '+00:00')) if isinstance(value, str) else value
    if at.tzinfo is None or at.utcoffset() != timedelta(0):
        raise ValueError('explicit UTC required')
    return at


def qualify(policy, observation, at):
    result = {'research_allowed': False, 'production_allowed': False,
              'promotion_authorized': False, 'reasons': []}
    matches = [r for r in policy['features'] if r['id'] == observation.get('feature_id')]
    if len(matches) != 1 or matches[0]['decision'] != 'RESEARCH':
        result['reasons'].append('not_a_unique_research_candidate')
        return result
    try:
        at = utc(at)
        seen, usable = (utc(observation[k]) for k in ('first_seen_utc', 'usable_from_utc'))
        age = (at-seen).total_seconds()
        if not seen <= usable <= at or not 0 <= age <= matches[0]['max_age_seconds']:
            result['reasons'].append('unavailable_or_stale')
        if observation.get('feature_id') == 'FUNDING':
            if (observation.get('rate_type') != 'indicative_current_period'
                    or not at < utc(observation['funding_time_utc'])):
                result['reasons'].append('expired_or_unknown_indicative_funding')
        value = observation.get('value')
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            result['reasons'].append('missing_or_invalid_value')
        for key in ('raw_verified', 'public_pre_target_witness_verified'):
            if observation.get(key) is not True: result['reasons'].append(key+'_required')
        coverage = observation.get('source_coverage_fraction')
        if isinstance(coverage, bool) or not isinstance(coverage, (int, float)) or not math.isfinite(coverage) or not .9 <= coverage <= 1:
            result['reasons'].append('insufficient_source_coverage')
    except (KeyError, TypeError, ValueError, AttributeError):
        result['reasons'].append('invalid_provenance')
    result['research_allowed'] = not result['reasons']
    return result
