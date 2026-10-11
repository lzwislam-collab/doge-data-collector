#!/usr/bin/env python3
"""Offline checks for the integrated DOGE Spot Plus simulation and evidence gates."""
import csv,json,tempfile,unittest
from datetime import datetime,timedelta,timezone
from pathlib import Path
from spot_plus_research import daily,signal,simulate,prices_scenario,evaluate
from spot_plus_context import inspect

DAY=86400000
NOW=datetime(2026,10,11,12,15,tzinfo=timezone.utc)
H=['symbol','open_utc_ms','open','high','low','close','volume_base','source']

def dump(path,data):
 path.parent.mkdir(parents=True,exist_ok=True)
 path.write_text(json.dumps(data),encoding='utf-8')

def writecsv(path,header,records):
 path.parent.mkdir(parents=True,exist_ok=True)
 with path.open('w',encoding='utf-8',newline='') as f:
  w=csv.DictWriter(f,fieldnames=header);w.writeheader();w.writerows(records)

class SpotPlusTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
  ts=int(datetime(2025,5,1,tzinfo=timezone.utc).timestamp()*1000)
  days=510;rows=[]
  for sym,mult in [('DOGE-USDT',1),('BTC-USDT',500000),('ETH-USDT',30000)]:
   p=0.10*mult
   for i in range(days):
    op=p;cl=op*(1+(0.011 if i%3!=0 else -0.016))
    rows.append(dict(symbol=sym,open_utc_ms=ts+i*DAY,open=op,high=max(op,cl)*1.001,
       low=min(op,cl)*0.999,close=cl,volume_base=100000+i,
       source='OKX history-candles confirmed 1Dutc'))
    p=cl
  self.records=rows
  writecsv(self.root/'data/history/daily.csv',H,rows)
  dump(self.root/'data/history/latest.json',{'status':'ok','ended_at_utc':NOW.isoformat(),
    'quality':{'duplicate_keys':0,'symbols':{'DOGE-USDT':{'last_utc':'2026-09-22'}}}})
  dump(self.root/'data/stage5/pre_registration.json',
       {'status':'frozen_protocol','model_id':'DOGE_DIRECTION_ISSUED_QUOTE_SHADOW_V42_1'})
  dump(self.root/'data/quality/latest.json',{'status':'ok_with_coverage_limits',
       'critical_failures':[],'audited_at_utc':NOW.isoformat()})
  dump(self.root/'data/stage5/latest.json',{'status':'ok','generated_at_utc':NOW.isoformat(),
      'prospective_70pct_validated':False,'directional_prediction_edge_proven':False})
  dump(self.root/'data/stage5/error_review/latest.json',
     {'status':'ok','generated_at_utc':NOW.isoformat()})
  # Missing optional data is a visible 'missing' outcome, not a model feature.
  writecsv(self.root/'data/direction_v42/forward_registry.csv',
   ['forecast_id','issued_at_utc','reference_price','target_utc','horizon_hours',
    'doge_reversal_direction','model_id'],[
   {'forecast_id':'locked-up','issued_at_utc':'2026-10-11T11:00:00+00:00',
    'reference_price':'0.08521','target_utc':'2026-10-12T11:00:00+00:00',
    'horizon_hours':'24','doge_reversal_direction':'UP',
    'model_id':'DOGE_DIRECTION_ISSUED_QUOTE_SHADOW_V42_1'}])

 def tearDown(self):self.tmp.cleanup()

 def test_price_history_aligns_and_is_deduped(self):
  x=daily(self.root,NOW)
  self.assertEqual(len(x['DOGE-USDT']),510)
  self.assertEqual(len(x['BTC-USDT']),510)

 def test_lagged_signal_does_not_see_todays_or_yesterdays_close(self):
  bars=daily(self.root,NOW)['DOGE-USDT']
  before=signal(bars,50,'doge_one_day_lag_reversal')
  bars[49]['c']*=100
  bars[50]['c']*=100
  self.assertEqual(signal(bars,50,'doge_one_day_lag_reversal'),before)

 def test_no_short_or_leverage_and_cash_benchmark(self):
  bars=daily(self.root,NOW)['DOGE-USDT']
  x=simulate(bars,400,'cash')
  self.assertEqual(x['trades'],0)
  self.assertEqual(x['final_usdt'],10000)
  self.assertEqual(x['max_drawdown_pct'],0)

 def test_fee_and_slippage_can_only_worsen_buy_hold(self):
  bars=daily(self.root,NOW)['DOGE-USDT']
  cost=simulate(bars,400,'buy_and_hold',fee=.001,slippage=.0005)
  nocost=simulate(bars,400,'buy_and_hold',fee=0,slippage=0)
  self.assertLess(cost['final_usdt'],nocost['final_usdt'])
  self.assertEqual(cost['trades'],2)  # buy plus assumed terminal liquidation
  self.assertEqual(cost['direction_scored_days'],0)
  self.assertTrue(cost['terminal_liquidation_assumed'])
  self.assertLess(cost['final_usdt'],cost['pre_liquidation_mark_to_market_usdt'])
  self.assertTrue(cost['assumptions']['frictions_are_assumptions_not_observed_fills'])

 def test_terminal_liquidation_consistent_across_cash_and_position(self):
  bars=daily(self.root,NOW)['DOGE-USDT']
  hold=simulate(bars,400,'buy_and_hold',fee=.002,slippage=.002)
  self.assertGreater(hold['estimated_total_fees_usdt'],0)
  self.assertNotIn('observed_fees_usdt',hold)
  self.assertNotIn('observed_slippage_usdt',hold)
  self.assertTrue(hold['assumptions']['frictions_are_assumptions_not_observed_fills'])
  self.assertEqual(hold['final_usdt'],round(hold['pre_liquidation_mark_to_market_usdt']*(1-.002)*(1-.002),2))
  cash=simulate(bars,400,'cash',fee=.002,slippage=.002)
  self.assertEqual(cash['final_usdt'],cash['pre_liquidation_mark_to_market_usdt'])
  self.assertEqual(cash['trades'],0)

 def test_intraday_low_risk_is_not_hidden_by_close_only_max_drawdown(self):
  bars=daily(self.root,NOW)['DOGE-USDT']
  # One artificial intraday crash fully recovers at the same UTC close:
  # close-only equity cannot capture this exposure.
  bars[402]['l']=min(bars[402]['o'],bars[402]['c'])*.00001
  r=simulate(bars,400,'buy_and_hold')
  self.assertGreater(r['intraday_low_drawdown_floor_pct'],r['max_drawdown_pct'])
  self.assertGreater(r['intraday_low_drawdown_floor_pct'],95)
  self.assertIn('floor',r['intraday_risk_definition'])

 def test_cash_has_no_intraday_spot_drawdown(self):
  bars=daily(self.root,NOW)['DOGE-USDT']
  r=simulate(bars,400,'cash')
  self.assertEqual(r['intraday_low_drawdown_floor_pct'],0)

 def test_model_never_assumes_short_on_down(self):
  bars=daily(self.root,NOW)['DOGE-USDT']
  x=simulate(bars,400,'doge_one_day_lag_reversal')
  self.assertGreaterEqual(x['trades'],1)
  self.assertLessEqual(x['days_exposed_pct'],100)
  self.assertEqual(x['assumptions']['shorting'],False)

 def test_no_70_or_profit_claim_based_on_backtest(self):
  report=evaluate(self.root,NOW)
  self.assertEqual(report['status'],'ok')
  self.assertFalse(report['profit_edge_vs_hold_verified'])
  self.assertFalse(report['direction_accuracy_70pct_verified'])
  self.assertFalse(report['live_trading_authorized'])
  self.assertEqual(report['five_pillar_evidence_audit']['pillar_count'],5)
  self.assertEqual(report['prospective_V42_reference']['reference_price_usdt'],.08521)
  self.assertEqual(report['prospective_V42_reference']['direction'],'UP')
  self.assertTrue(report['study']['not_pristine_holdout'])
  self.assertTrue(report['five_pillar_evidence_audit']['original_36_items']['original_google_sheet_full_catalog_not_validated_here'])

 def test_excluded_lookahead_etf_fred_and_fake_whale_netflow(self):
  report=evaluate(self.root,NOW)
  c=report['five_pillar_evidence_audit']
  self.assertFalse(c['model_input_authorization']['all_5_pillars_merged_into_single_numeric_forecast'])
  self.assertIn('historical_revised_CPI_as_released',
                c['model_input_authorization']['unsupported'])
  self.assertFalse(c['pillars']['1_onchain_whales_exchange']['observations']['verified_exchange_netflow'])

 def test_duplicate_daily_stamp_blocks(self):
  self.records.append(dict(self.records[-1]))
  writecsv(self.root/'data/history/daily.csv',H,self.records)
  with self.assertRaisesRegex(ValueError,'duplicate day'):
   evaluate(self.root,NOW)

 def test_broken_ohlc_blocks(self):
  self.records[0]['low']=9999999
  writecsv(self.root/'data/history/daily.csv',H,self.records)
  with self.assertRaisesRegex(ValueError,'inconsistent OHLC'):
   evaluate(self.root,NOW)

 def test_missing_etf_is_not_treated_as_zero_flow(self):
  x=inspect(self.root,NOW)
  self.assertEqual(x['pillars']['3_etf']['observations']['state'],'missing')
  self.assertNotIn('net_flow_m_usd',x['pillars']['3_etf']['observations'])

 def test_liquidation_ok_and_partial_are_accepted_without_claiming_full_coverage(self):
  stamp=NOW.isoformat()
  for status in ('ok','partial'):
   dump(self.root/'data/liquidations/latest.json',
     {'status':status,'ended_at_utc':stamp,'listen_seconds':480})
   context=inspect(self.root,NOW)
   entry=context['pillars']['2_liquidations_liquidity']
   self.assertEqual(entry['sources']['sampled_liquidations']['state'],'current')
   self.assertFalse(entry['observations']['liquidation_full_market_coverage'])
   self.assertIn('global_liquidation_total',entry['unverified_features'])

 def test_actual_unhealthy_liquidation_status_remains_visible(self):
  dump(self.root/'data/liquidations/latest.json',
    {'status':'error','ended_at_utc':NOW.isoformat(),'listen_seconds':480})
  entry=inspect(self.root,NOW)['pillars']['2_liquidations_liquidity']
  self.assertEqual(entry['sources']['sampled_liquidations']['state'],'unhealthy')

 def test_future_snapshot_timestamp_rejected(self):
  future=NOW+timedelta(days=1)
  dump(self.root/'data/whales_blocks/latest.json',
   {'status':'ok','ended_at_utc':future.isoformat()})
  with self.assertRaisesRegex(ValueError,'future report timestamp'):
   inspect(self.root,NOW)

 def test_future_etf_first_seen_blocks_backfill_leakage(self):
  p=self.root/'data/etf/daily_flows.csv'
  writecsv(p,['asset','trade_date_us','total_usd_m','first_seen_utc'],[
   {'asset':'BTC','trade_date_us':'2026-10-09','total_usd_m':'50',
    'first_seen_utc':(NOW+timedelta(days=2)).isoformat()}])
  with self.assertRaisesRegex(ValueError,'future ETF first seen'):
   inspect(self.root,NOW)

 def test_no_realtime_context_claim_from_stale_quality(self):
  q={'status':'ok_with_coverage_limits','critical_failures':[],
     'audited_at_utc':(NOW-timedelta(hours=7)).isoformat()}
  dump(self.root/'data/quality/latest.json',q)
  self.assertTrue(inspect(self.root,NOW)['quality_audit_stale_for_3h_realtime_claim'])

 def test_actual_36_item_mapping_accepted_only_with_all_numbered_decisions(self):
  mapping=self.root/'data/stage5/spot_plus/legacy36_map.csv'
  names=['Source36#','Legacy item','Decision','New section','Audit note','Version']
  decisions=['KEEP_MERGE']*21+['BACKEND_ONLY']*8+['REMOVE_SCORE']*7
  rows=[{'Source36#':str(i+1),'Legacy item':'item '+str(i+1),
         'Decision':decision,'New section':'F01','Audit note':'test','Version':'V3'}
        for i,decision in enumerate(decisions)]
  writecsv(mapping,names,rows)
  context=inspect(self.root,NOW)
  self.assertTrue(context['original_36_items']['item_level_current_mapping_reconciled'])
  self.assertTrue(context['original_36_items']['no_claim_all_36_are_signal_features'])
  rows[14]['Source36#']='14'
  writecsv(mapping,names,rows)
  with self.assertRaisesRegex(ValueError,'missing duplicate'):
   inspect(self.root,NOW)

 def test_90day_history_scenario_not_called_probability(self):
  b=daily(self.root,NOW)['DOGE-USDT']
  x=prices_scenario(b,.085)
  self.assertFalse(x['probability_forecast_calibrated'])
  self.assertLess(x['p10_implied_price'],x['p90_implied_price'])

if __name__=='__main__':unittest.main(verbosity=2)
