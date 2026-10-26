"""Read-only supplementary diagnostics. Never replaces the hash-frozen assessor.

Observed-label recall/calibration are descriptive, with explicit missing counts.
They are not additional significance claims or a new retrospective protocol.
"""
import argparse
import hashlib
import json
import math
from datetime import datetime, timezone, timedelta
from pathlib import Path


def future_protocol_draft():
    """Proposed thresholds only; deliberately missing prospective identities."""
    return {'schema':'DOGE_PLUS_P2_FUTURE_DRAFT_20261015','status':'DRAFT_NOT_REGISTERED',
        'automatic_promotion':False,'runtime_activation':False,'real_orders_authorized':False,
        'observation_start_utc':None,'observation_days':90,'slot_seconds':7200,'horizon_seconds':3600,
        'identity_bindings':{k:None for k in ('model_sha256','feature_schema_sha256','training_data_sha256',
            'frozen_v42_sha256','baseline_definitions_sha256','code_sha256')},
        'baselines':['frozen_v42_same_event','constant_05','frozen_majority','frozen_rule','cash','buy_hold'],
        'promotion':{'minimum_nonoverlap_events':100,'minimum_events_per_label':50,
            'minimum_up_recall_lower':.55,'minimum_down_recall_lower':.55,
            'maximum_fixed_bin_ece':.10,'minimum_calibration_bin_events':20,
            'minimum_accuracy_lower':.70,'accuracy_excess_lower':.01,'brier_excess_lower':.002,
            'familywise_alpha':.05,'family_size_upper_bound':10000,'fixed_looks':2,
            'maximum_claims_per_look':80,'minimum_regime_events':20,'maximum_regime_brier_harm':.01,
            'minimum_nominal_and_stress_excess_pct_points':0,'minimum_coverage_pct':100},
        'rollback':{'maximum_cumulative_paper_loss_pct':5,'maximum_paper_drawdown_pct':10,
            'maximum_consecutive_missing_books':1,'maximum_book_gap_seconds':900,
            'maximum_model_training_data_age_days':120,'integrity_failures_tolerated':0},
        'costs':{'fee_per_side':.001,'slippage_per_side':.0005,'stress_multiplier':2},
        'public_registration_witness':None,
        'semantics':{'rollback_thresholds':'stop paper candidate when loss/drawdown >= threshold, gap/age > threshold, or missing count >= threshold; retain original frozen fallback',
            'recall':'fixed-look multiplicity-adjusted lower bounds; each label minimum required',
            'calibration':'10 fixed bins, ECE <= threshold; every occupied bin requires minimum count; descriptive cap plus Brier inferential gate',
            'economics':'strictly positive excess over all baselines in nominal and double-cost same-period paper ledgers',
            'witness':'exact draft digest publicly witnessed before first slot; remote byte replay and independent review required separately'}}


def protocol_digest(draft):
    payload={k:v for k,v in draft.items() if k!='public_registration_witness'}
    return hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def validate_future_protocol(draft):
    """Check preparation completeness, never assert public authenticity or activate."""
    blockers=[]
    expected=future_protocol_draft()
    for key in ('schema','status','automatic_promotion','runtime_activation','real_orders_authorized',
                'observation_days','slot_seconds','horizon_seconds','baselines','promotion','rollback','costs','semantics'):
        if draft.get(key)!=expected[key]:blockers.append(key+' differs from proposed protocol')
    bindings=draft.get('identity_bindings',{})
    if not isinstance(bindings,dict):bindings={}
    for key in expected['identity_bindings']:
        value=bindings.get(key)
        if not isinstance(value,str) or len(value)!=64 or any(c not in '0123456789abcdef' for c in value):
            blockers.append(key+' missing')
    try:
        start=datetime.fromisoformat(draft['observation_start_utc'].replace('Z','+00:00'))
        witness=draft['public_registration_witness']
        published=datetime.fromisoformat(witness['published_at_utc'].replace('Z','+00:00'))
        if start.tzinfo is None or published.tzinfo is None or not published<start:
            raise ValueError('witness must precede first slot')
        if witness['protocol_sha256']!=protocol_digest(draft) or not witness['immutable_locator'].startswith('https://'):
            raise ValueError('digest/locator missing')
    except (KeyError,TypeError,ValueError,AttributeError):blockers.append('pre-window exact public witness missing/invalid')
    return {'eligible_for_prospective_registration':not blockers,'blockers':blockers,
        'public_witness_authenticity_verified':False,'production_authorized':False,'runtime_activation_authorized':False,
        'status':'DRAFT_NOT_REGISTERED'}


def diagnostics(events, scores, scheduled, qualified_ids=None):
    if type(scheduled) is not int or scheduled < 0:
        raise ValueError('invalid scheduled denominator')
    def index(rows):
        out = {}
        for row in rows:
            if row.get('cohort', 'formal') != 'formal' or row['event_id'].endswith('_canary'):
                continue
            key = row['event_id']
            if key in out:
                raise ValueError('duplicate event or score')
            out[key] = row
        return out
    events, scores = index(events), index(scores)
    if len(events) > scheduled or any(k not in events for k in scores):
        raise ValueError('schedule overflow or orphan score')
    pairs = []
    for key, event in events.items():
        for name in ('selected', 'frozen_full'):
            p = event['candidate_p'][name]
            if isinstance(p, bool) or not isinstance(p, (int,float)) or not math.isfinite(p) or not 0 <= p <= 1:
                raise ValueError('invalid probability')
        if key not in scores:
            continue
        y = scores[key]['y']
        if type(y) is not int or y not in (0,1):
            raise ValueError('invalid binary label')
        if qualified_ids is None or key in qualified_ids:
            pairs.append((event,y))
    result = {'scheduled_denominator':scheduled, 'paired_observed':len(pairs),
        'missing_or_unqualified':scheduled-len(pairs), 'models':{},
        'status':'NO_FORMAL_EVIDENCE' if scheduled == 0 else 'INCOMPLETE_DIAGNOSTIC_ONLY' if len(pairs)<scheduled else 'COMPLETE_DIAGNOSTIC_ONLY',
        'promotion_authorized':False, 'significance_claim':False,
        'scope':'same-event observed-label diagnostics; missing labels cannot define class denominators',
        'qualification_verified':qualified_ids is not None}
    for name in ('selected','frozen_full'):
        classes = {y:[e['candidate_p'][name] for e,yy in pairs if yy==y] for y in (0,1)}
        bins=[]
        for i in range(10):
            members=[(e['candidate_p'][name],y) for e,y in pairs if min(int(e['candidate_p'][name]*10),9)==i]
            bins.append({'lower':i/10, 'upper':(i+1)/10,'upper_inclusive':i==9,'n':len(members),
                'mean_probability':sum(p for p,y in members)/len(members) if members else None,
                'observed_up_rate':sum(y for p,y in members)/len(members) if members else None})
        result['models'][name]={'up_denominator':len(classes[1]),'down_denominator':len(classes[0]),
            'up_recall':sum(p>=.5 for p in classes[1])/len(classes[1]) if classes[1] else None,
            'down_recall':sum(p<.5 for p in classes[0])/len(classes[0]) if classes[0] else None,
            'brier':sum((e['candidate_p'][name]-y)**2 for e,y in pairs)/len(pairs) if pairs else None,
            'calibration':bins}
    return result


def local_audit(root, at):
    """Inventory original local bytes only. No public witness is inferred."""
    import phase2_forward_v2 as forward
    import phase3_independent as independent
    base=root/forward.BASE
    events=[json.loads(p.read_bytes()) for p in sorted((base/'events').glob('*.json'))]
    scores=[json.loads(p.read_bytes()) for p in sorted((base/'scores').glob('*.json'))]
    assessment=independent.evaluate(independent.policy(),events,scores,[],at,False,False)
    missing={r['event_id'] for r in assessment['missing']}
    policy=forward.policy();start=forward.utc(policy['effective_from_utc'])
    matured={str(int((start+timedelta(seconds=i*policy['slot_seconds'])).timestamp()))+'_formal'
             for i in range(assessment['scheduled_events'])}
    eligible=matured-missing
    matured_events=[e for e in events if e['event_id'] in matured]
    matured_scores=[s for s in scores if s['event_id'] in matured]
    files={str(p.relative_to(root)).replace('\\','/'):hashlib.sha256(p.read_bytes()).hexdigest()
           for folder in ('events','scores') for p in sorted((base/folder).glob('*.json'))}
    return {'audited_at_utc':at.isoformat(),'source':'local checkout original event/score bytes; no network or deployment verification',
        'source_sha256':files,'canary_events':sum(e.get('cohort')=='canary' for e in events),
        'formal_issued':sum(e.get('cohort')=='formal' for e in events),
        'formal_pending_maturity':sum(e.get('cohort')=='formal' and e['event_id'] not in matured for e in events),
        'formal_start_utc':forward.policy()['effective_from_utc'],'independent_assessment':assessment,
        'diagnostics':diagnostics(matured_events,matured_scores,assessment['scheduled_events'],eligible),
        'automatic_collection_verified':False,'production_promotion_authorized':False}


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--at',required=True)
    args=parser.parse_args()
    at=datetime.fromisoformat(args.at.replace('Z','+00:00'))
    if at.tzinfo is None:
        raise ValueError('aware audit clock required')
    print(json.dumps(local_audit(Path(__file__).resolve().parent,at.astimezone(timezone.utc)),indent=2,allow_nan=False))
