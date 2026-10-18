"""Witness diagnostics and opt-in local source gate; no issuer or scorer.

The source gate can block publication invocation. It never grants frozen
eligibility or promises remote witness completion before a deadline.
"""
from datetime import datetime,timedelta
import math
import csv,io

def utc(value):
 at=datetime.fromisoformat(value.replace('Z','+00:00'))
 if at.tzinfo is None or at.utcoffset()!=timedelta(0):raise ValueError('UTC required')
 return at

def assess(now,deadline,reserve_seconds):
 if isinstance(reserve_seconds,bool) or not isinstance(reserve_seconds,(int,float)) or not math.isfinite(reserve_seconds) or reserve_seconds<=0:
  raise ValueError('positive finite diagnostic reserve required')
 remaining=(utc(deadline)-utc(now)).total_seconds()
 return {'seconds_remaining':remaining,'reserve_seconds':reserve_seconds,
  'budget_sufficient':remaining>reserve_seconds,'publication_verified':False,
  'issuance_authorized':False,'scope':'diagnostic reserve is not a statistical latency bound or frozen-policy change'}

def exact_witness(expected_digest,observed_digest,completed_at,deadline):
 return bool(expected_digest) and expected_digest==observed_digest and utc(completed_at)<utc(deadline)

def assert_source_fresh(raw,now_ms,reserve_seconds=90):
 """Local invocation gate, not a promise of future server completion."""
 hour=3600000;required=(now_ms//hour-1)*hour
 keys={(r['symbol'],int(r['open_utc_ms'])) for r in csv.DictReader(io.StringIO(raw.decode('utf-8-sig')))}
 if any((s,required) not in keys for s in ('DOGE-USDT','BTC-USDT','ETH-USDT')):
  raise ValueError('publication source stale after preparation')
 if ((now_ms//hour+1)*hour-now_ms)/1000<=reserve_seconds:
  raise ValueError('publication boundary reserve exhausted; no timestamp/eligibility relaxation')
