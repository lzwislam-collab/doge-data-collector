import csv
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
import p0_open_target_guard as g


class GuardTests(unittest.TestCase):
    def check(self, minutes):
        now=datetime(2026,10,12,13,tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); path=root/g.diagnostics.SOURCES['production_registry']; path.parent.mkdir(parents=True)
            row={'forecast_id':'real','model_id':'frozen','issued_at_utc':(now-timedelta(hours=1)).isoformat(),
                 'target_utc':(now+timedelta(minutes=minutes)).isoformat()}
            with path.open('w',newline='') as f:
                w=csv.DictWriter(f,fieldnames=list(row));w.writeheader();w.writerow(row)
            score=root/g.diagnostics.SOURCES['production_scores']
            score.write_text('forecast_id,status\n',encoding='utf-8')
            calls=[]
            def capture(mode,root): calls.append(mode); return {'status':'ok','attempts':1}
            result=g.run(root,now,capture)
            return result,calls

    def test_open_target_gets_bounded_existing_capture(self):
        result,calls=self.check(-20)
        self.assertEqual(calls,['watch']); self.assertEqual(result['status'],'ok')

    def test_expired_target_never_triggers_retrospective_capture(self):
        result,calls=self.check(-26)
        self.assertEqual(calls,[]); self.assertEqual(result['status'],'SKIP_NO_OPEN_TARGET')

    def test_future_target_waits(self):
        result,calls=self.check(1)
        self.assertEqual(calls,[])

    def test_missing_production_inputs_are_blocked_not_empty_target_set(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError,'required CSV'):
                g.run(Path(directory),datetime(2026,10,12,tzinfo=timezone.utc))

    def test_empty_or_wrong_production_header_is_blocked(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'bad.csv';path.write_text('garbage\n',encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'CSV header'):
                g.diagnostics.read_csv(path,required_columns=('forecast_id','status'))


if __name__=='__main__': unittest.main()
