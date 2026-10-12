import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from three_risk_gates import (
    utc, policy_guard, fit_fold, inventory_36, record_no_trade,
    stage_gate, read_decisions, FEATURE_NAMES
)

EFF="2026-10-14T02:00:00+00:00"

def config():
    return {"status":"frozen_governance_protocol","protocol_id":"test-gates",
      "effective_from_utc":EFF,"no_real_orders":True,"no_dca_changes":True,
      "trade_gate":{"action":"NO_TRADE"},
      "feature_ablation":{"candidate_columns":FEATURE_NAMES},
      "source_protocols":{"v2":"test-v2","p2":"test-p2","p1_adaptive":"test-p1"},
      "acceptance":{"p0_issuance_coverage_pct":90,"p0_target_coverage_pct":90,
                    "p0_30d_days":30,"p0_30d_coverage_pct":99,
                    "p1_preliminary_days":30,"p1_preliminary_min_nonoverlap":100,
                    "p1_wilson_lower_pct":70,
                    "p1_replication_days":90,"p1_replication_min_nonoverlap":300,
                    "p2_min_days":30,"p2_min_complete_intervals":100}}

def scorefixture():
    v2={"status":"ok","locked_implementation_checked":True,
        "protocol_id":"test-v2",
        "effective_from_utc":"2026-10-12T02:00:00+00:00",
        "economic_edge_validated":False,
        "horizons":{"24h":{"nonoverlap":{
            "scored":2,"due":2,"quote_coverage_pct":100.,
            "models":{"doge_reversal":{"wilson95_lower_pct_descriptive_only":0.}}},
            "issuance":{"coverage_pct":40.}}}}
    p2={"status":"ok","protocol_id":"test-p2","real_profit_validated":False,
        "effective_from_utc":"2026-10-13T02:00:00+00:00",
        "paper_checkpoint_excess_review_candidate":False,"completed_prefix_intervals":0,
        "execution_bid_ask_verified":False}
    p1={"status":"ok","protocol_id":"test-p1"}
    p0={"status":"ok","rolling_7d":{
        "1h":{"seven_full_days_observed":False, "coverage_pct":50.,
              "target_coverage_pct":100., "target_due":2,"expected":3, "issued":1},
        "24h":{"seven_full_days_observed":False, "coverage_pct":None,
               "target_coverage_pct":None,"target_due":0,"expected":0,"issued":0}}}
    return v2,p0,p1,p2

class ThreeRiskGateTests(unittest.TestCase):
    def test_utc_rejects_naive_and_nonzero_offsets(self):
        with self.assertRaises(ValueError): utc("2026-10-14T02:00:00")
        with self.assertRaises(ValueError): utc("2026-10-14T11:00:00+09:00")

    def test_protocol_never_allows_trade(self):
        p=config()
        p["trade_gate"]["action"]="BUY"
        with self.assertRaisesRegex(ValueError,"trading signal"): policy_guard(p)
        p=config();p["no_dca_changes"]=False
        with self.assertRaisesRegex(ValueError,"DCA"):policy_guard(p)

    def test_purged_fold_rejects_tiny_training(self):
        prices={"DOGE-USDT":[1.+i*.001 for i in range(250)],
                "BTC-USDT":[2.+i*.001 for i in range(250)]}
        with self.assertRaisesRegex(ValueError,"purged"):
            fit_fold(prices,list(range(24,80)),list(range(80,110)),24,0)

    def test_purged_fold_runs_fixed_group_on_history_only(self):
        prices={"DOGE-USDT":[1.+i*.001 for i in range(440)],
                "BTC-USDT":[2.+i*.002 for i in range(440)]}
        full,n = fit_fold(prices,list(range(24,281)),list(range(283,312)),1,0)
        drop,_ = fit_fold(prices,list(range(24,281)),list(range(283,312)),1,1)
        self.assertGreater(n,100)
        self.assertTrue(0<=full<=1 and 0<=drop<=1)

    def test_not_claim_70_percent_at_one_sample(self):
        v2,p0,p1,p2=scorefixture()
        output=stage_gate(config(),v2,p0,p1,p2,
                          utc("2026-10-14T11:00:00+00:00"))
        self.assertEqual(output["overall"],"REVISE")
        self.assertFalse(output["may_claim_70pct"])
        self.assertFalse(output["p1"]["preliminary_evidence_candidate"])

    def test_100_sample_not_automatic_success(self):
        v2,p0,p1,p2=scorefixture()
        cohort=v2["horizons"]["24h"]["nonoverlap"]
        cohort["scored"]=cohort["due"]=100
        cohort["models"]["doge_reversal"]["wilson95_lower_pct_descriptive_only"]=72.
        v2["horizons"]["24h"]["issuance"]["coverage_pct"]=100
        p0["audited_at_utc"]="2027-01-17T23:00:00+00:00"
        p0["rolling_7d"]={h:dict(seven_full_days_observed=True,coverage_pct=100.,
                                 target_coverage_pct=100.,target_due=7,
                                 expected=7,issued=7) for h in ("1h","24h")}
        v2["economic_edge_validated"]=False
        results=stage_gate(config(),v2,p0,p1,p2,
                           utc("2027-01-18T00:00:00+00:00"))
        self.assertEqual(results["p0"]["seven_day_initial"],"PASS")
        self.assertTrue(results["p1"]["preliminary_evidence_candidate"])
        self.assertFalse(results["p1"]["signal_edge_validated"])
        self.assertEqual(results["overall"],"REVISE")

        stale=stage_gate(config(),v2,p0,p1,p2,utc("2027-01-19T00:00:00+00:00"))
        self.assertEqual(stale["p0"]["seven_day_initial"],"WAIT")
        self.assertFalse(stale["p0"]["p0_monitor_fresh_under_6h"])

    def test_no_trade_record_preserves_earlier_clock(self):
        # Existing supplementary P0 metrics must never create a trading approval.
        with TemporaryDirectory() as d:
            root=Path(d)
            f={"forecast_id":"a","issued_at_utc":"2026-10-14T03:00:00+00:00",
               "target_utc":"2026-10-15T03:00:00+00:00",
               "doge_reversal_direction":"UP"}
            v2={"protocol_id":"test-v2","horizons":{"24h":{"nonoverlap":{"selected_ids":["a"]}}}}
            with patch("three_risk_gates.immutable.history_receipts"), \
                 patch("three_risk_gates.frozen.read_rows",return_value={"a":f}):
                first=record_no_trade(root,config(),v2,utc("2026-10-14T03:30:00+00:00"))
                second=record_no_trade(root,config(),v2,utc("2026-10-14T04:30:00+00:00"))
            self.assertEqual(first["new_records"],1)
            self.assertEqual(second["new_records"],0)
            entry=read_decisions(root)["a"]
            self.assertEqual(entry["decision"],"NO_TRADE")
            self.assertEqual(entry["timely_pretrade_decision"],"1")
            self.assertEqual(entry["decision_recorded_at_utc"],"2026-10-14T03:30:00+00:00")

    def test_thirty_day_rate_source_is_checked_without_profit_promotion(self):
        v2,p0,p1,p2=scorefixture()
        now=utc("2027-01-18T00:00:00+00:00")
        p0["audited_at_utc"]="2027-01-17T23:00:00+00:00"
        p0["rolling_30d"]={h:dict(full_window_observed=True,coverage_pct=100.,
                           target_coverage_pct=100.,target_due=30,expected=720 if h=="1h" else 30,issued=720 if h=="1h" else 30,
                           rolling_window_hours=720) for h in ("1h","24h")}
        result=stage_gate(config(),v2,p0,p1,p2,now)
        self.assertTrue(result["p0"]["thirty_day_rate_source_available"])
        self.assertEqual(result["p0"]["p0_30d_higher_reliability"],"PASS")
        self.assertFalse(result["may_claim_reliable_realized_profit"])
        p0["rolling_30d"]["1h"]["coverage_pct"]=98.9
        result=stage_gate(config(),v2,p0,p1,p2,now)
        self.assertEqual(result["p0"]["p0_30d_higher_reliability"],"WAIT")

    def test_stale_thirty_day_success_is_not_current_success(self):
        v2,p0,p1,p2=scorefixture()
        p0["rolling_30d"]={h:dict(full_window_observed=True,coverage_pct=100.,
                           target_coverage_pct=100.,target_due=30,expected=30,issued=30,
                           rolling_window_hours=720) for h in ("1h","24h")}
        result=stage_gate(config(),v2,p0,p1,p2,utc("2027-01-18T00:00:00+00:00"))
        self.assertEqual(result["p0"]["p0_30d_higher_reliability"],"WAIT")

    def test_late_decision_is_not_pretrade_proof(self):
        with TemporaryDirectory() as d:
            root=Path(d)
            f={"forecast_id":"a","issued_at_utc":"2026-10-14T03:00:00+00:00",
               "target_utc":"2026-10-15T03:00:00+00:00",
               "doge_reversal_direction":"DOWN"}
            v2={"protocol_id":"test-v2","horizons":{"24h":{"nonoverlap":{"selected_ids":["a"]}}}}
            with patch("three_risk_gates.immutable.history_receipts"), \
                 patch("three_risk_gates.frozen.read_rows",return_value={"a":f}):
                out=record_no_trade(root,config(),v2,utc("2026-10-14T05:10:00+00:00"))
            self.assertEqual(out["timely_audited_records"],0)
            self.assertEqual(read_decisions(root)["a"]["timely_pretrade_decision"],"0")

    def test_no_trade_decisions_reject_duplicate_rows(self):
        with TemporaryDirectory() as d:
            root=Path(d)
            p=root/"data/stage5/three_risk_gates/no_trade_decisions.csv"
            p.parent.mkdir(parents=True)
            p.write_text("forecast_id,issued_at_utc,target_utc,decision_recorded_at_utc,decision,reason,timely_pretrade_decision,source_protocol_id\n"+
                         "a,x,y,z,NO_TRADE,r,0,x\n"*2)
            with self.assertRaisesRegex(ValueError,"duplicate"):read_decisions(root)

if __name__=="__main__":
    unittest.main()
