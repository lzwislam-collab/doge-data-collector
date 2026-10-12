import json
import math
import tempfile
import unittest
from datetime import datetime,timedelta,timezone
from pathlib import Path

import p1_adaptive_v2 as m

def policy():
    return {
      "protocol_id":"test-future","model_id":"SHADOW_TRAINABLE_TEST",
      "effective_from_utc":"2026-10-14T00:00:00+00:00",
      "max_quote_age_seconds":120,"max_candle_staleness_hours":3,
      "target_quote_window_minutes":25,"min_discordant_pairs":30,
      "bonferroni_per_comparison":0.01,"minimum_valid_nonoverlap":100,
      "minimum_observation_days":30,"required_issuance_coverage":0.9,
      "required_quote_coverage":0.9,"wilson95_lower_min":0.7,
      "fit":{"training_max_anchors":4000,"minimum_train":100,"holdout_fraction":0.2,
             "iterations":80,"learning_rate":0.09,"l2":0.006}
    }

def toy_series(n=4300):
    start=datetime(2026,1,1,tzinfo=timezone.utc).timestamp()*1000
    times=[int(start+i*3600000) for i in range(n)]
    doge=[.08*math.exp(.00007*i+.012*math.sin(i/37)) for i in range(n)]
    btc=[80000*math.exp(.00002*i+.009*math.sin(i/43)) for i in range(n)]
    return times,{"DOGE-USDT":doge,"BTC-USDT":btc}

class AdaptiveP1Tests(unittest.TestCase):
    def test_trainable_one_hour_and_24h_are_causal_and_deterministic(self):
        times,prices=toy_series()
        for h in (1,24):
            w,meta=m.fit_asof(times,prices,h,len(times)-1,policy())
            self.assertEqual(len(w),6)
            self.assertTrue(all(math.isfinite(x) for x in w))
            self.assertGreater(meta["n"],100)
            self.assertLessEqual(m.utc(meta["last_label_utc"]),
                datetime.fromtimestamp((times[-1]+3600000)/1000,timezone.utc))
            self.assertGreaterEqual(meta["holdout_accuracy"],0)
            self.assertLessEqual(meta["holdout_accuracy"],1)
            self.assertEqual((w,meta),m.fit_asof(times,prices,h,len(times)-1,policy()))

    def test_future_history_does_not_change_past_issuance_model(self):
        times,prices=toy_series()
        anchor=4100
        for h in (1,24):
            a=m.fit_asof(times,prices,h,anchor,policy())
            extended={k:[*v[:anchor+1],*(x*2 for x in v[anchor+1:])]
                      for k,v in prices.items()}
            b=m.fit_asof(times,extended,h,anchor,policy())
            self.assertEqual(a,b)

    def test_pre_effective_never_issues_or_fetches(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(m.issue(Path(directory),
                datetime(2026,10,13,tzinfo=timezone.utc),policy(),{}),[])

    def test_true_matured_quote_and_missing_quote_have_distinct_records(self):
        at=datetime(2026,10,14,6,tzinfo=timezone.utc)
        f={"forecast_id":"a","issued_at_utc":at.isoformat(),
           "target_utc":(at+timedelta(hours=1)).isoformat(),
           "reference_price":"0.08","direction":"UP","p_up":"0.66",
           "issue_quote_sha256":"f"*64}
        s={}
        target=at+timedelta(hours=1)
        quote=(target+timedelta(minutes=6),target+timedelta(minutes=6),
               0.081,"abc","dummy.json")
        with tempfile.TemporaryDirectory() as directory:
            m.score_due(Path(directory),at+timedelta(hours=2),policy(),{"a":f},s,
                        evidence=[quote],receipts=False)
        self.assertEqual(s["a"]["status"],"scored")
        self.assertEqual(s["a"]["correct"],"1")
        self.assertAlmostEqual(float(s["a"]["brier"]),(1-.66)**2)
        with tempfile.TemporaryDirectory() as directory:
            missed={}
            m.score_due(Path(directory),at+timedelta(hours=2),policy(),
                        {"a":f},missed,evidence=[],receipts=False)
        self.assertEqual(missed["a"]["status"],"unscorable")
        self.assertEqual(missed["a"]["target_price"],"")

    def test_no_quote_before_target_or_after_25min(self):
        at=datetime(2026,10,14,6,tzinfo=timezone.utc)
        f={"forecast_id":"a","issued_at_utc":at.isoformat(),
           "target_utc":(at+timedelta(hours=1)).isoformat(),
           "reference_price":"0.08","direction":"UP","p_up":"0.62"}
        target=at+timedelta(hours=1)
        inputs=[(target-timedelta(seconds=1),target-timedelta(seconds=1),0.1,"q1","q1"),
                (target+timedelta(minutes=26),target+timedelta(minutes=26),0.1,"q2","q2")]
        with tempfile.TemporaryDirectory() as directory:
            s={}
            m.score_due(Path(directory),target+timedelta(hours=2),policy(),
                        {"a":f},s,evidence=inputs,receipts=False)
            self.assertEqual(s["a"]["status"],"unscorable")

    def test_forecast_registry_and_score_append_only_checks(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            path=root/"registry.csv"
            m.write_rows(path,{"id":{"forecast_id":"id","status":"test"}},["forecast_id","status"])
            self.assertEqual(m.rows(path,["forecast_id","status"])["id"]["status"],"test")
            path.write_text("forecast_id,status\nid,test\nid,fake\n")
            with self.assertRaisesRegex(ValueError,"duplicate"):
                m.rows(path,["forecast_id","status"])

    def test_24h_overlap_is_prereserved_even_when_first_quote_missing(self):
        a=datetime(2026,10,14,6,tzinfo=timezone.utc)
        p=policy()
        records={}
        scores={}
        for idx,clock in enumerate((a,a+timedelta(hours=23,minutes=55))):
            fid=str(idx)
            records[fid]={"forecast_id":fid,"horizon_hours":"24",
                          "issued_at_utc":clock.isoformat(),
                          "target_utc":(clock+timedelta(hours=24)).isoformat(),
                          "direction":"UP","frozen_rule_recomputed":"UP",
                          "historical_majority":"UP","btc_momentum":"UP",
                          "always_up":"UP","always_down":"DOWN","p_up":".6"}
            if idx==1:
                scores[fid]={"status":"scored","correct":"1","realized_up":"UP","brier":"0.16"}
        summary=m.report(p,records,scores,datetime(2026,10,17,tzinfo=timezone.utc))
        track=summary["horizons"]["24h"]
        self.assertEqual(track["fixed_nonoverlap_selected"],1)
        self.assertEqual(track["overlap_excluded"],1)
        self.assertEqual(track["due"],1)
        self.assertEqual(track["scored"],0)

    def test_manual_promotion_and_five_way_threshold(self):
        p=policy()
        result=m.report(p,{}, {},datetime(2026,12,1,tzinfo=timezone.utc))
        self.assertFalse(result["automatic_promotion"])
        for h in ("1h","24h"):
            self.assertFalse(result["horizons"][h]["promotion_authorized"])
            self.assertFalse(result["horizons"][h]["promotion_review_eligible_only"])
            self.assertEqual(set(result["horizons"][h]["paired"]),set(m.NAMES))
            self.assertEqual(p["bonferroni_per_comparison"],.05/5)

if __name__=="__main__":
    unittest.main()
