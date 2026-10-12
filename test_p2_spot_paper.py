import hashlib
import json
import unittest
from datetime import datetime, timedelta, timezone
from p2_spot_paper import (simulate,trade_long,verify_qualified_cohort,
                           evaluate, max_dd, utc, positive, matured_missing_issuance_slots)

START="2026-10-13T02:10:00+00:00"
EFFECTIVE="2026-10-13T02:00:00+00:00"

def config():
    return {"status":"frozen_paper_protocol","protocol_id":"test-p2",
            "effective_from_utc":EFFECTIVE,"source_protocol_id":"test-v2",
            "horizon_hours":24,"starting_equity_usdt":10000.,
            "official_signal":"doge_reversal_direction",
            "comparator_signal":"btc_momentum_direction",
            "execution_cost_assumptions":{"taker_fee_fraction_each_side":.001,
                "slippage_fraction_each_side":.0005,
                "half_spread_fraction_each_side":.00025,
                "stress_multiplier":2},
            "min_observation_days":30,"min_completed_independent_intervals":100,
            "min_issuance_coverage_fraction":.9,"min_quote_coverage_fraction":.9}

def source(fid="a",issued=START,up=True,signal="UP"):
    base=datetime.fromisoformat(issued)
    f={"forecast_id":fid,"issued_at_utc":base.isoformat(),
       "target_utc":(base+timedelta(hours=24)).isoformat(),
       "horizon_hours":"24","reference_price":"0.10000000",
       "doge_reversal_direction":signal,"btc_momentum_direction":"DOWN",
       "majority_direction":"UP"}
    q=(base+timedelta(hours=24,minutes=3)).isoformat()
    s={"status":"scored","target_quote_utc":q,
       "target_price":"0.11" if up else "0.09",
       "realized_up":"UP" if up else "DOWN",
       "doge_reversal_correct":str(int(signal==("UP" if up else "DOWN"))),
       "btc_momentum_correct":str(int("DOWN"==("UP" if up else "DOWN"))),
       "majority_correct":str(int("UP"==("UP" if up else "DOWN")))}
    return f,s

def snapshot(f,s, now="2026-10-14T05:00:00+00:00",
             due=1,scored=1,ids=None):
    if ids is None:ids=[f["forecast_id"]]
    bb=json.dumps([f]).encode()
    ss=json.dumps([s]).encode()
    v1={"status":"ok","full_reachable_history_checked":True,
        "registry_sha256":hashlib.sha256(bb).hexdigest(),
        "scores_sha256":hashlib.sha256(ss).hexdigest()}
    v2={"status":"ok","protocol_id":"test-v2","locked_implementation_checked":True,
        "economic_edge_validated":False,"effective_from_utc":"2026-10-12T02:00:00+00:00",
        "audited_at_utc":now,
        "horizons":{"24h":{"nonoverlap":{"selected_ids":ids,"due":due,"scored":scored},
                            "issuance":{"coverage_pct":100.},
                            "quote_coverage_pct":100.}}}
    return v2,v1,(bb,ss)

class Tests(unittest.TestCase):
    def test_fee_slip_and_spread_all_reduce_cash(self):
        full,*_=trade_long(10000.,0.1,0.11,0.,0.,0.)
        cost,qty,parts=trade_long(10000.,0.1,0.11,.001,.0005,.00025)
        self.assertGreater(full,cost)
        self.assertGreater(qty,0)
        self.assertGreater(sum(parts.values()),0)
        self.assertTrue(all(v>0 for v in parts.values()))

    def test_strategy_vs_buy_hold_and_cash(self):
        f,s=source()
        base=simulate([(f,s)],config(),"doge_reversal",1.)
        btc=simulate([(f,s)],config(),"btc_momentum",1.)
        self.assertGreater(base["net_return_pct"],0)
        self.assertEqual(btc["net_return_pct"],0)
        self.assertEqual(base["hold_net_return_pct"],base["net_return_pct"])
        self.assertEqual(base["trade_legs"],2)
        self.assertEqual(base["cash_net_return_pct"],0)

    def test_down_signal_cash_never_short(self):
        f,s=source(up=False,signal="DOWN")
        a=simulate([(f,s)],config(),"doge_reversal",1.)
        self.assertEqual(a["net_return_pct"],0)
        self.assertEqual(a["trade_legs"],0)
        self.assertEqual(a["ledger"][0]["doge_bought_net"],0)

    def test_double_cost_is_less_profitable(self):
        f,s=source()
        low=simulate([(f,s)],config(),"doge_reversal",1.)
        high=simulate([(f,s)],config(),"doge_reversal",2.)
        self.assertLess(high["net_return_pct"],low["net_return_pct"])

    def test_path_drawdown_not_just_final_loss(self):
        self.assertAlmostEqual(max_dd([10000,11000,9000,12000]),100*2/11)

    def test_reject_bad_label_and_future_quote(self):
        f,s=source()
        now=utc("2026-10-14T05:00:00+00:00")
        v2,v1,_=snapshot(f,s)
        wrong=dict(s,realized_up="DOWN")
        with self.assertRaisesRegex(ValueError,"outcome label"):
            verify_qualified_cohort(v2,{"a":f},{"a":wrong},now)
        wrong2=dict(s,target_quote_utc="2026-10-14T06:00:00+00:00")
        with self.assertRaisesRegex(ValueError,"late target"):
            verify_qualified_cohort(v2,{"a":f},{"a":wrong2},now)

    def test_duplicate_official_selection_blocks(self):
        f,s=source()
        v2,_,_=snapshot(f,s,ids=["a","a"])
        with self.assertRaisesRegex(ValueError,"duplicate"):
            verify_qualified_cohort(v2,{"a":f},{"a":s},
                                    utc("2026-10-14T05:00:00+00:00"))

    def test_v2_score_mismatch_blocks_instead_of_replacing(self):
        f,s=source()
        v2,_,_=snapshot(f,s,scored=0)
        with self.assertRaisesRegex(ValueError,"official denominator"):
            verify_qualified_cohort(v2,{"a":f},{"a":s},
                                    utc("2026-10-14T05:00:00+00:00"))

    def test_missing_quote_stops_prefix(self):
        f,s=source()
        v2,v1,raw=snapshot(f,s,scored=0)
        v2["horizons"]["24h"]["quote_coverage_pct"]=0.
        out=evaluate(v2,v1,config(),{"a":f},{},raw)
        self.assertEqual(out["completed_prefix_intervals"],0)
        self.assertEqual(out["blocked_forecast_id"],"a")
        self.assertIsNone(out["strategy"]["net_return_pct"])

    def test_one_real_score_still_no_profit_claim(self):
        f,s=source()
        v2,v1,raw=snapshot(f,s)
        out=evaluate(v2,v1,config(),{"a":f},{"a":s},raw)
        self.assertEqual(out["completed_prefix_intervals"],1)
        self.assertFalse(out["economic_edge_validated"])
        self.assertFalse(out["execution_bid_ask_verified"])
        self.assertFalse(out["paper_checkpoint_excess_review_candidate"])

    def test_precohort_old_forecast_not_backfilled(self):
        f,s=source(issued="2026-10-12T03:00:00+00:00")
        v2,v1,raw=snapshot(f,s,now="2026-10-14T05:00:00+00:00")
        out=evaluate(v2,v1,config(),{"a":f},{"a":s},raw)
        self.assertEqual(out["eligible_24h_nonoverlap_forecasts"],0)
        self.assertIsNone(out["strategy"]["net_return_pct"])

    def test_matured_missing_issuance_is_not_silently_skipped(self):
        effective=utc(EFFECTIVE)
        before=utc("2026-10-15T02:24:00+00:00")
        after=utc("2026-10-15T02:26:00+00:00")
        self.assertEqual(matured_missing_issuance_slots({},effective,before),[])
        self.assertEqual(matured_missing_issuance_slots({},effective,after),[EFFECTIVE])
        f,s=source()
        self.assertEqual(matured_missing_issuance_slots({"a":f},effective,after),[])

    def test_hash_mismatch_blocks(self):
        f,s=source()
        v2,v1,raw=snapshot(f,s)
        with self.assertRaisesRegex(ValueError,"snapshot"):
            evaluate(v2,v1,config(),{"a":f},{"a":s},(b"tampered",raw[1]))

if __name__=="__main__":
    unittest.main()
