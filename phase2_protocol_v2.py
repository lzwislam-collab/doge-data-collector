"""Validation-only future protocol drafts; no forecast writer or activation."""
import hashlib
import phase1_research as prior

STATES=('UP_HIGH_VOL','UP_LOW_VOL','DOWN_HIGH_VOL','DOWN_LOW_VOL')

def prepare(result,p):
 candidates=[];rejected=[]
 for h,hr in result['horizons'].items():
  folds=hr['folds']
  if not folds or not folds[-1].get('selected_candidate'):
   rejected.append({'horizon':h,'reasons':['NO_LATEST_VALIDATION_MODEL']});continue
  fold=folds[-1];name=fold['selected_candidate'];score=fold['validation'][name]['brier'];base=fold['validation_probability_baselines']
  reasons=[]
  if any(base[k]['brier']-score<p['min_validation_gain'] for k in ('half','majority')):reasons.append('INSUFFICIENT_VALIDATION_BRIER_GAIN')
  for state in STATES:
   evidence=fold['validation_regimes'].get(state,{}).get(name,{})
   if evidence.get('n',0)<p['min_regime_validation_n']:reasons.append('INSUFFICIENT_REGIME_'+state)
   elif evidence['brier']>.25+p['max_regime_harm']:reasons.append('REGIME_HARM_'+state)
  if reasons:rejected.append({'horizon':h,'hypothesis':name,'reasons':reasons});continue
  raw=prior.encode(fold['models'][name])
  candidates.append({'horizon_hours':int(h),'fold':fold['fold'],'hypothesis':name,'model_sha256':hashlib.sha256(raw).hexdigest(),
                     'validation_brier':score,'validation_only_screen_passed':True,'production_allowed':False})
 return {'status':'DRAFT_PENDING_NEW_PUBLIC_REGISTRATION_AND_FORWARD_RUNTIME' if candidates else 'WAIT_ELIGIBLE_VALIDATION_CANDIDATE',
   'candidates':candidates,'rejected':rejected,'selection_evidence':'LATEST_FOLD_VALIDATION_ONLY_TEST_RESULTS_IGNORED',
   'earliest_proposed_effective_utc':'2026-10-20T00:00:00Z','original18_effective_utc_unchanged':'2026-10-16T00:00:00Z',
   'family_size_upper_bound':10000,'minimum_calendar_days':90,'minimum_nonoverlap_events':100,
   'promotion_requires':['new exact model/data/code public preregistration and server witness before first issue',
       'all scheduled events, missing predictions/targets and independent raw forward evidence',
       'dependency-robust fixed-look family-adjusted lower confidence bound >=0.70',
       'same-event probability/direction baseline excess and cross-regime no material harm',
       'same-event raw bid/ask/depth fees/slippage nominal and stress net returns',
       'independent review and explicit production version change with rollback'],
   'public_witness_verified':False,'forward_runtime_installed':False,'auto_issuance_authorized':False,
   'production_promotion_authorized':False,'real_orders_authorized':False,'validated_70pct':False}
