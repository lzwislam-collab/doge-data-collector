"""Offline regression: prospectively fixed regime candidate, no sample selection."""
import json
import tempfile
import unittest
from datetime import datetime,timedelta,timezone
from pathlib import Path

import p1_regime_forward_v1 as m

E=datetime(2026,10,14,tzinfo=timezone.utc)
def policy():
    return json.loads((Path(__file__).resolve().parent/m.PROTOCOL).read_text(encoding="utf-8"))
def features():
    return {
        "DOGE-USDT":{"return_1h":-.002,"return_24h":-.01,
            "realized_volatility_24h":.04,"volume_ratio_1h_to_prior24h":1.2},
        "BTC-USDT":{"return_1h":.006,"return_24h":.012,
            "realized_volatility_24h":.01,"volume_ratio_1h_to_prior24h":.8},
    }
def original(hour=1):
    at=E+timedelta(hours=hour)
    return {"forecast_id":"mock-"+str(hour),"issued_at_utc":at.isoformat(),
        "target_utc":(at+timedelta(hours=1)).isoformat(),"horizon_hours":"1",
        "reference_price":"0.08",
        "majority_direction":"DOWN","btc_momentum_direction":"UP",
        "doge_reversal_direction":"DOWN"}
def target_score(f,price=0.081,delta=4):
    return {"status":"scored","realized_up":"UP" if price>0.08 else "DOWN",
        "target_price":str(price),
        "target_quote_utc":(m.utc(f["target_utc"])+timedelta(minutes=delta)).isoformat()}


class Tests(unittest.TestCase):
    def test_activation_and_locked_rule_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);p=policy()
            (root/m.PROTOCOL).parent.mkdir(parents=True)
            (root/m.PROTOCOL).write_text(json.dumps(p),encoding="utf-8")
            self.assertEqual(m.evaluate(root,E-timedelta(seconds=1))["status"],"WAIT_ACTIVATION")
            for changes in ({"relative_strength_weight":.4},{"btc_weight_stress":1.5},
                            {"no_backfill":False},{"effective_from_utc":"2026-10-13T00:00:00+00:00"}):
                with self.assertRaises(ValueError):
                    m.verify_protocol(root,dict(p,**changes))

    def test_actual_btc_strength_in_stress_regime_and_no_probabilities(self):
        x=m.predict(features(),1,policy())
        self.assertTrue(x["stress_regime"])
        self.assertEqual(x["btc_weight"],2)
        self.assertEqual(x["direction"],"UP")
        self.assertIsNone(x["probability_up"])
        self.assertEqual(x["paper_action"],"NO_TRADE")
        self.assertIsNone(m.predict({**features(),"DOGE-USDT":{**features()["DOGE-USDT"],"return_1h":0,"return_24h":0},
            "BTC-USDT":{**features()["BTC-USDT"],"return_1h":0,"return_24h":0}},1,policy())["direction"])

    def test_invalid_or_future_information_rejected(self):
        f=features();f["DOGE-USDT"]["realized_volatility_24h"]=0
        with self.assertRaises(ValueError):m.predict(f,1,policy())
        f=features();f["DOGE-USDT"]["return_1h"]=float("nan")
        with self.assertRaises(ValueError):m.predict(f,1,policy())
        with self.assertRaises(ValueError):m.predict(features(),7,policy())

    def test_missing_quote_and_unverified_context_count_in_denominator(self):
        a=original(1);b=original(3);c=original(5)
        contexts={a["forecast_id"]:{"feature_status":"PREPARED_NOT_ISSUED",
            "_prepared_features":features()},
            b["forecast_id"]:{"feature_status":"PREPARED_NOT_ISSUED",
            "_prepared_features":features()}}
        x=m.score_track([a,b,c],contexts,{a["forecast_id"]:target_score(a)},
                        {b["forecast_id"]:"invalid public witness"},E,E+timedelta(hours=8),policy())
        self.assertEqual(x["matured_original_nonoverlap_slots"],3)
        self.assertEqual(x["scored"],1)
        self.assertEqual(x["missing_or_abstained"],2)
        self.assertEqual(x["missing_by_reason"]["not_verified_pre_target"],1)
        self.assertEqual(x["missing_by_reason"]["missing_context"],1)
        self.assertLessEqual(x["accuracy_missing_as_wrong_pct"],100/3+0.001)
        self.assertFalse(x["candidate_70pct_proven"])

    def test_qualified_realized_price_and_window_must_match(self):
        a=original(1);contexts={a["forecast_id"]:{"feature_status":"PREPARED_NOT_ISSUED",
            "_prepared_features":features()}}
        for bad in (target_score(a,price=0.081,delta=26),
                    dict(target_score(a),realized_up="DOWN")):
            with self.assertRaises(ValueError):
                m.score_track([a],contexts,{a["forecast_id"]:bad},{},E,E+timedelta(hours=3),policy())

    def test_two_horizons_are_distinct_and_no_prior_scores_used(self):
        row=original(1)
        contexts={row["forecast_id"]:{"feature_status":"PREPARED_NOT_ISSUED",
             "_prepared_features":features()}}
        # A prediction before tomorrow's frozen start never becomes a scored new challenger.
        old=dict(row,issued_at_utc=(E-timedelta(hours=1)).isoformat(),
                 target_utc=E.isoformat())
        z=m.score_track([old],contexts,{row["forecast_id"]:target_score(row)},{},
                        E,E+timedelta(hours=10),policy())
        self.assertEqual(z["matured_original_nonoverlap_slots"],0)

if __name__=="__main__":unittest.main()
