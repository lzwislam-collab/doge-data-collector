import csv
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from minimal_direction_report import evaluate, render, MODEL, REG, SCORES, V2, PUBLICATION

T = datetime(2026, 10, 13, 1, 10, tzinfo=timezone.utc)
def at(h=0, minutes=0):
    return (T + timedelta(hours=h, minutes=minutes)).isoformat()

class MinimalDirectionReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.id1 = "fixed_1h"
        self.id24 = "fixed_24h"
        self.reg = []
        for fid,h in ((self.id1,1),(self.id24,24)):
            self.reg.append(dict(forecast_id=fid, model_id=MODEL, issued_at_utc=at(-1),
                source_quote_utc=at(-1,minutes=-1), source_hour_start_utc=at(-1,minutes=-10),
                reference_price="0.08147", horizon_hours=str(h),target_utc=at(h-1),
                majority_p_up="0.5",majority_direction="DOWN",
                doge_reversal_direction="UP",btc_momentum_direction="DOWN",
                feature_sha256="a"*64))
        self.scores = []
        self.v2 = {"protocol_id":"DOGE_FORWARD_RAW_QUOTE_BOUND_V2_20261012_0200",
                   "locked_implementation_checked":True, "audited_at_utc":at(-.5),
                   "effective_from_utc":at(-2),
                   "accuracy_70pct_validated":False,
                   "horizons":{
                     "1h":{"issuance":{"expected":10,"issued":8,"missing":2,"coverage_pct":80},
                           "nonoverlap":{"scored":2,"models":{"doge_reversal":{"n":2,"correct":1,"accuracy_pct":50}}}},
                     "24h":{"nonoverlap":{"scored":0,"models":{"doge_reversal":{"n":0,"correct":0,"accuracy_pct":None}}}}}}
        self.pub = {"status":"verified_remote_readback","new_ids":[self.id1,self.id24],
                    "verified_at_utc":at(-1,minutes=1)}
        self.save()

    def save(self):
        for path,records in ((REG,self.reg),(SCORES,self.scores)):
            f=self.root/path;f.parent.mkdir(parents=True,exist_ok=True)
            fields=list(records[0].keys()) if records else ["forecast_id","status","observed_at_utc",
              "target_quote_utc","target_price","realized_up","majority_correct",
              "doge_reversal_correct","btc_momentum_correct","reason"]
            with f.open("w",newline="",encoding="utf-8") as fd:
                w=csv.DictWriter(fd,fieldnames=fields);w.writeheader();w.writerows(records)
        for path,data in ((V2,self.v2),(PUBLICATION,self.pub)):
            f=self.root/path;f.parent.mkdir(parents=True,exist_ok=True)
            f.write_text(json.dumps(data))

    def test_single_direction_and_revise(self):
        result=evaluate(self.root,T)
        self.assertEqual(result["horizons"]["1h"]["direction"],"UP")
        self.assertEqual(result["horizons"]["24h"]["direction"],"UP")
        self.assertEqual(result["horizons"]["1h"]["official_nonoverlap_correct"],1)
        self.assertEqual(result["status"],"REVISE")
        self.assertNotIn("可能上涨",render(result))
        self.assertIn("50.00%",render(result))

    def test_flat_is_down_wrong_and_no_rewrite(self):
        self.scores=[dict(forecast_id=self.id1,status="scored",observed_at_utc=at(minutes=1),
                          target_quote_utc=at(),target_price="0.08147",realized_up="DOWN",
                          majority_correct="1",doge_reversal_correct="0",
                          btc_momentum_correct="1",reason="")]
        self.save()
        self.assertEqual(evaluate(self.root,T+timedelta(minutes=2))["horizons"]["1h"]["most_recent_due"]["result"],"错误")

    def test_score_inconsistent_fails_closed(self):
        self.scores=[dict(forecast_id=self.id1,status="scored",observed_at_utc=at(minutes=1),
                          target_quote_utc=at(),target_price="0.08",realized_up="UP",
                          majority_correct="0",doge_reversal_correct="1",
                          btc_momentum_correct="0",reason="")]
        self.save()
        with self.assertRaisesRegex(ValueError,"conflicts"):
            evaluate(self.root,T+timedelta(minutes=2))

    def test_publication_not_verified_is_explicit(self):
        self.pub["new_ids"]=[]
        self.save()
        output=evaluate(self.root,T)
        self.assertFalse(output["horizons"]["1h"]["publication_verified"])
        self.assertEqual(output["horizons"]["1h"]["forecast_state"],"公开登记待核验")

    def test_duplicate_id_rejected(self):
        self.reg.append(dict(self.reg[0]))
        self.save()
        with self.assertRaisesRegex(ValueError,"duplicate"):
            evaluate(self.root,T)

    def test_missing_quote_not_substituted(self):
        self.scores=[dict(forecast_id=self.id1,status="unscorable_missing_target_quote",
                          observed_at_utc=at(minutes=26),target_quote_utc="",target_price="",
                          realized_up="",majority_correct="",doge_reversal_correct="",
                          btc_momentum_correct="",reason="late")]
        self.save()
        result=evaluate(self.root,T+timedelta(minutes=30))
        self.assertEqual(result["horizons"]["1h"]["most_recent_due"]["result"],"到期报价缺失")

    def test_future_outcome_rejected(self):
        self.scores=[dict(forecast_id=self.id1,status="scored",observed_at_utc=at(minutes=1),
                          target_quote_utc=at(),target_price="0.08",realized_up="DOWN",
                          majority_correct="1",doge_reversal_correct="0",
                          btc_momentum_correct="1",reason="")]
        self.save()
        with self.assertRaisesRegex(ValueError,"future outcome"):
            evaluate(self.root,T)

    def test_prefreeze_due_prediction_not_substituted(self):
        old=dict(self.reg[1])
        old["forecast_id"]="legacy_24h"
        old["issued_at_utc"]=at(-30)
        old["source_quote_utc"]=at(-30,minutes=-1)
        old["target_utc"]=at(-6)
        self.reg.append(old)
        self.save()
        self.assertIsNone(evaluate(self.root,T)["horizons"]["24h"]["most_recent_due"])

    def test_future_target_and_early_score_rejected(self):
        self.reg[0]["target_utc"]=at(10)
        self.save()
        with self.assertRaisesRegex(ValueError,"invalid issued"):
            evaluate(self.root,T)

if __name__=="__main__":
    unittest.main()
