import unittest
from datetime import datetime, timedelta, timezone
from p1_model_competition import shadow, score_group, evaluate, wilson, exact_mcnemar

EFFECTIVE = "2026-10-13T00:00:00+00:00"

def forecast(fid, at, h=1, majority="UP", reversal="DOWN", btc="UP"):
    issued=datetime.fromisoformat(at)
    return {"forecast_id":fid,"issued_at_utc":issued.isoformat(),
            "source_quote_utc":(issued-timedelta(seconds=1)).isoformat(),
            "target_utc":(issued+timedelta(hours=h)).isoformat(),
            "horizon_hours":str(h),"reference_price":"0.08",
            "majority_direction":majority,"doge_reversal_direction":reversal,
            "btc_momentum_direction":btc,"majority_p_up":"0.53"}

def score(fid, true="UP", h=1, issued="2026-10-13T01:00:00+00:00"):
    t=datetime.fromisoformat(issued)+timedelta(hours=h)
    f=forecast(fid,issued,h)
    return {"status":"scored","forecast_id":fid,"target_quote_utc":t.isoformat(),
            "target_price":"0.081" if true=="UP" else "0.079",
            "realized_up":true,"majority_correct":str(int(f["majority_direction"]==true)),
            "doge_reversal_correct":str(int(f["doge_reversal_direction"]==true)),
            "btc_momentum_correct":str(int(f["btc_momentum_direction"]==true))}

def policy():
    return {"status":"frozen_shadow_protocol","protocol_id":"test-p1","source_protocol_id":"test-v2",
            "effective_from_utc":EFFECTIVE, "primary_horizons_hours":[1,24],
            "automatic_promotion":False,"requires_manual_approval":True,
            "comparators":["doge_reversal","majority","btc_momentum","always_up","always_down"],
            "min_valid_nonoverlap_scores":100,"min_observation_days":30,
            "min_wilson95_lower_fraction":0.7,"bonferroni_p_max":0.0125,
            "min_discordant_pairs":30,"model_id":"test-shadow"}

def reports(selected, scored, now):
    v2={"status":"ok","locked_implementation_checked":True,"protocol_id":"test-v2",
        "effective_from_utc":"2026-10-12T02:00:00+00:00","audited_at_utc":now,
        "accuracy_70pct_validated":False,"horizons":{}}
    for h in ("1h","24h"):
        ids=selected if h=="1h" else []
        v2["horizons"][h]={"nonoverlap":{"selected_ids":ids,
            "due":len(ids),"scored":scored if h=="1h" else 0,"models":{}},
             "issuance":{"coverage_pct":100.0}}
    return v2,{"status":"ok","full_reachable_history_checked":True}

class LeagueTests(unittest.TestCase):
    def test_fixed_vote_only_exante_directions(self):
        self.assertEqual(shadow(forecast("a", EFFECTIVE)), "UP")
        self.assertEqual(shadow(forecast("a", EFFECTIVE, btc="DOWN")), "DOWN")

    def test_wilson_and_paired_exact(self):
        self.assertEqual(wilson(0,0),None)
        self.assertLess(wilson(1,1),0.3)
        self.assertAlmostEqual(exact_mcnemar(10,0),2/1024)
        self.assertEqual(exact_mcnemar(0,0),1.)
        self.assertTrue(0 <= exact_mcnemar(2000,0) <= 1)

    def test_missing_kept_in_denominator(self):
        f=forecast("a","2026-10-13T01:00:00+00:00")
        result=score_group([{"forecast":f,"score":None}],
                           datetime(2026,10,13,3,tzinfo=timezone.utc),
                           datetime.fromisoformat(EFFECTIVE),policy())
        self.assertEqual((result["due"],result["qualified_scores"],result["unresolved"]),(1,0,1))
        self.assertEqual(result["quote_coverage_pct"],0)
        self.assertFalse(result["eligible_for_manual_review_only"])

    def test_official_nonoverlap_same_cohort(self):
        f=forecast("a","2026-10-13T01:00:00+00:00")
        s=score("a")
        v2,v1=reports(["a"],1,"2026-10-13T03:00:00+00:00")
        result=evaluate(v2,v1,policy(),{"a":f},{"a":s})
        self.assertEqual(result["tracks"]["1h"]["qualified_scores"],1)
        self.assertEqual(result["tracks"]["1h"]["accuracy_pct"],100.)
        self.assertFalse(result["tracks"]["1h"]["eligible_for_manual_review_only"])
        self.assertFalse(result["claimed_validated_edge"])

    def test_pre_effective_excluded_but_not_erased(self):
        f=forecast("a","2026-10-12T23:00:00+00:00")
        s=score("a",issued="2026-10-12T23:00:00+00:00")
        v2,v1=reports(["a"],1,"2026-10-13T03:00:00+00:00")
        result=evaluate(v2,v1,policy(),{"a":f},{"a":s})
        self.assertEqual(result["tracks"]["1h"]["due"],0)
        self.assertEqual(result["tracks"]["1h"]["pre_effective_rows_excluded"],1)

    def test_mismatched_v2_score_blocks_not_substitutes(self):
        f=forecast("a","2026-10-13T01:00:00+00:00")
        s=score("a")
        v2,v1=reports(["a"],0,"2026-10-13T03:00:00+00:00")
        with self.assertRaisesRegex(ValueError,"differs from official"):
            evaluate(v2,v1,policy(),{"a":f},{"a":s})

    def test_wrong_label_blocks(self):
        f=forecast("a","2026-10-13T01:00:00+00:00")
        s=score("a")
        s["realized_up"]="DOWN"
        v2,v1=reports(["a"],1,"2026-10-13T03:00:00+00:00")
        with self.assertRaisesRegex(ValueError,"corrupt realized"):
            evaluate(v2,v1,policy(),{"a":f},{"a":s})

if __name__=="__main__":
    unittest.main()
