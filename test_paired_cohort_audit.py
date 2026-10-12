import unittest
import math
import tempfile
from pathlib import Path
from unittest.mock import patch
from datetime import datetime,timedelta,timezone
import paired_cohort_audit as a


class AuditTests(unittest.TestCase):
    def entry(self,fid,action='NO_TRADE',score='UP'):
        return {'forecast_id':fid,'issued_at_utc':'2026-10-12T16:00:00+00:00',
            'target_utc':'2026-10-12T17:00:00+00:00','horizon_hours':'1',
            'pair':{'challenger':{'direction':'UP','p_up':.7},'production':{
                'doge_reversal_direction':'DOWN','majority_direction':'UP','btc_momentum_direction':'UP'},
                'decision':{'paper_action':action}},'outcome':score,'qualified':True}

    def test_no_trade_stays_in_full_accuracy(self):
        x=self.entry('x');y=self.entry('y','PAPER_LONG','DOWN')
        result=a.metrics([x,y],0)
        self.assertEqual(result['challenger_accuracy_pct'],50)
        self.assertEqual(result['due'],2)
        self.assertEqual(result['paper_long_n'],1)
        self.assertEqual(result['paper_long_accuracy_pct'],0)

    def test_missing_pair_remains_due_denominator(self):
        x=self.entry('x');y=self.entry('y');y.update(pair=None,outcome=None,qualified=False)
        result=a.metrics([x,y],0)
        self.assertEqual((result['due'],result['qualified'],result['missing']),(2,1,1))
        self.assertEqual(result['accuracy_if_missing_wrong_pct'],50)

    def test_overlap_reservation_not_changed_by_no_trade_or_missing(self):
        x=self.entry('x');y=self.entry('y');x['pair']=None
        self.assertEqual([r['forecast_id'] for r in a.nonoverlap([y,x])],['x'])

    def test_comparisons_use_same_observed_outcome(self):
        result=a.metrics([self.entry('x')],1)
        self.assertEqual(result['paired']['frozen_production']['wins'],1)
        self.assertFalse(result['paired']['frozen_production']['passes_familywise_10_tests'])

    def test_no_samples_are_wait_not_zero_accuracy(self):
        result=a.metrics([],0)
        self.assertIsNone(result['challenger_accuracy_pct'])
        self.assertIsNone(result['quote_pair_coverage_pct'])

    def test_pair_requires_actual_pre_target_public_presence(self):
        import paired_selection_v1 as s
        c=a.cohort;now=datetime(2026,10,12,17,tzinfo=timezone.utc)
        end=int(now.timestamp()*1000)-3600000
        times=[end-(1199-i)*3600000 for i in range(1200)]
        prices={'DOGE-USDT':[.1*math.exp(.01*math.sin(i/9)) for i in range(1200)],'BTC-USDT':[100*math.exp(.01*math.sin(i/13)) for i in range(1200)]}
        candidate=s.fit_candidate(times,prices,1);candidate['frozen_features']={'reversal':'DOWN','majority':'UP','btc_momentum':'UP'}
        prepared={'prepared_at_utc':(now-timedelta(seconds=1)).isoformat(),'source_close_utc':now.isoformat(),
                  'source_sha256':'a'*64,'candidates':{'1':candidate},'book':None,'book_error':'unavailable'}
        original={'forecast_id':'real','model_id':'frozen','issued_at_utc':now.isoformat(),'target_utc':(now+timedelta(hours=1)).isoformat(),
                  'source_hour_start_utc':now.isoformat(),'source_quote_utc':now.isoformat(),'reference_price':'.1','horizon_hours':'1',
                  'majority_direction':'UP','doge_reversal_direction':'DOWN','btc_momentum_direction':'UP'}
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);c.save_snapshot(root,prepared)
            pair=c.make_pair(original,prepared,'b'*64,now);c.append(root/c.REG,[pair]);pair=c.read_chain(root/c.REG)[0]
            proof={'publication':{'observed_public_utc':(now+timedelta(seconds=2)).isoformat(),'head_sha':'c'*40}}
            binding={'real|ISSUE':{'quote_id':'b'*64}}
            snapshot=(root/c.BASE/'prepared'/(pair['prepared_snapshot_sha256']+'.json')).read_bytes()
            with patch.object(a.quotes,'verify_bound'),patch.object(c.publication,'command',side_effect=[c.record_path(root/c.REG,'real').read_bytes(),snapshot]):
                self.assertTrue(a.verify_pair(root,pair,original,None,proof,binding))
            with patch.object(a.quotes,'verify_bound'),patch.object(c.publication,'command',return_value=b''):
                with self.assertRaisesRegex(ValueError,'absent'):a.verify_pair(root,pair,original,None,proof,binding)
            proof['publication']['observed_public_utc']=(now+timedelta(hours=2)).isoformat()
            with patch.object(a.quotes,'verify_bound'):
                with self.assertRaisesRegex(ValueError,'witnessed'):a.verify_pair(root,pair,original,None,proof,binding)


if __name__=='__main__':unittest.main()
