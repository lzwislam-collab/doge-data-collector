"""Deterministic PAPER-ONLY lifecycle drill. Cannot write production pointers."""
from datetime import datetime,timezone
import phase2_forward_v2 as f
import phase3_independent as independent

def registry():
 return {'schema':'DOGE_PHASE3_PAPER_REGISTRY_V1','candidate_identity':independent.policy()['model_identity'],
  'protocol_id':f.policy()['protocol_id'],'candidate_model_path':f.MODEL,
  'frozen_fallback_package_sha256':'c93004aadc669c074f51a4cb6411769c3d84d388b31773d1251823b1d45ccad3',
  'frozen_fallback_package_path':f.old.PACKAGE,'expires_utc':f.policy()['model_expires_utc'],
  'assessment_id':independent.policy()['assessment_id'],'real_activation_implemented':False,
  'production_pointer_writes_authorized':False,'real_orders_authorized':False}

def _sealed(value):return {**value,'state_sha256':f.digest(value)}

def genesis(reg):
 if reg!=registry():raise ValueError('unregistered candidate/fallback')
 return _sealed({'schema':'DOGE_PHASE3_PAPER_STATE_V1','simulation_only':True,'status':'REGISTERED','sequence':0,
  'registry_sha256':f.digest(reg),'candidate_identity':reg['candidate_identity'],
  'paper_pointer':reg['frozen_fallback_package_sha256'],'previous_state_sha256':None,
  'production_pointer_changed':False,'real_orders_authorized':False})

def synthetic_review_fixture(reg):
 return {'synthetic':True,'model_identity':reg['candidate_identity'],'assessment_id':reg['assessment_id'],
  'statistical_gate_passed':True,'independent_full_audit':True,'external_immutable_retention_verified':True,
  'reviewer_attestation':'SYNTHETIC_REVIEW_FOR_DRILL_ONLY','review_sha256':f.digest({'synthetic':True,'registry':reg})}

def _review(ev,reg,storage):
 expected=synthetic_review_fixture(reg)
 keys=('synthetic','model_identity','assessment_id','statistical_gate_passed','independent_full_audit','review_sha256')
 if any(ev.get(k)!=expected[k] for k in keys):raise ValueError('complete exact synthetic review required; real activation not implemented')
 if storage and any(ev.get(k)!=expected[k] for k in ('external_immutable_retention_verified','reviewer_attestation')):raise ValueError('storage/independent approval missing or mismatched')

def transition(state,command,reg,at,evidence):
 if reg!=registry() or state.get('registry_sha256')!=f.digest(reg):raise ValueError('candidate/fallback version changed')
 if state.get('state_sha256')!=f.digest({k:v for k,v in state.items() if k!='state_sha256'}) or state.get('simulation_only') is not True:raise ValueError('unsafe/tampered paper state')
 if at.tzinfo is None or at.utcoffset().total_seconds()!=0:raise ValueError('UTC required')
 if 'at_utc' in state and at<f.utc(state['at_utc']):raise ValueError('paper state time reversed')
 status=state['status'];pointer=state['paper_pointer'];expired=at>=f.utc(reg['expires_utc'])
 if command=='start_shadow':
  if status!='REGISTERED' or expired:raise ValueError('invalid/repeated/expired shadow activation')
  status='SHADOW'
 elif command=='independent_review':
  if status!='SHADOW' or expired:raise ValueError('review from wrong/expired state')
  _review(evidence,reg,False);status='REVIEW_READY'
 elif command=='paper_activate':
  if status!='REVIEW_READY' or expired:raise ValueError('review before paper activation required')
  _review(evidence,reg,True);status='PAPER_ACTIVE';pointer=reg['candidate_identity']
 elif command in ('manual_stop','health_check'):
  if status not in ('SHADOW','REVIEW_READY','PAPER_ACTIVE'):raise ValueError('no active state to check/stop')
  unsafe=command=='manual_stop' or expired or evidence.get('integrity_ok') is not True or evidence.get('severe_loss') is True
  if unsafe:
   status='ROLLED_BACK' if status=='PAPER_ACTIVE' else 'RETIRED' if expired else 'REJECTED'
   pointer=reg['frozen_fallback_package_sha256']
 else:raise ValueError('unknown lifecycle command')
 return _sealed({'schema':state['schema'],'simulation_only':True,'status':status,'sequence':state['sequence']+1,
  'registry_sha256':f.digest(reg),'candidate_identity':reg['candidate_identity'],'paper_pointer':pointer,
  'previous_state_sha256':state['state_sha256'],'at_utc':at.isoformat(),'command':command,
  'evidence_sha256':f.digest(evidence),'production_pointer_changed':False,'real_orders_authorized':False})

def drill(reg):
 at=datetime(2026,10,20,tzinfo=timezone.utc);ev=synthetic_review_fixture(reg);s=genesis(reg);chain=[s]
 for command in ('start_shadow','independent_review','paper_activate','manual_stop'):
  s=transition(s,command,reg,at,ev);chain.append(s)
 return {'synthetic':True,'actual_production_switch':False,'final_status':s['status'],'chain':chain,
  'real_activation_implemented':False,'real_orders_authorized':False}
