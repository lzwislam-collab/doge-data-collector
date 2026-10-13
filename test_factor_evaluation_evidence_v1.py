import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

try:
    import factor_evaluation_evidence_v1 as evidence
except ModuleNotFoundError:
    evidence=None


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(evidence,'evidence adapter is not implemented')

    def test_public_freeze_requires_server_run_and_exact_files_before_activation(self):
        files={'config/protocol.json':b'{"effective_from_utc":"2026-10-15T00:00:00+00:00"}',
               'code.py':b'print(1)\n'}
        run={'id':7,'head_sha':'a'*40,'created_at':'2026-10-13T12:00:00Z',
             'updated_at':'2026-10-13T12:10:00Z','status':'completed','conclusion':'success',
             'path':'.github/workflows/factor_evaluation_v1.yml','run_attempt':1}
        self.assertTrue(evidence.public_freeze(files,[run],lambda head,path:files[path],
                         '2026-10-15T00:00:00+00:00')['verified'])
        for field,value in [('created_at','2026-10-16T00:00:00Z'),
                            ('updated_at','2026-10-16T00:00:00Z'),('conclusion','failure'),
                            ('path','.github/workflows/another.yml')]:
            other=copy.deepcopy(run);other[field]=value
            self.assertFalse(evidence.public_freeze(files,[other],lambda head,path:files[path],
                             '2026-10-15T00:00:00+00:00')['verified'])
        self.assertFalse(evidence.public_freeze(files,[run],lambda head,path:b'changed',
                         '2026-10-15T00:00:00+00:00')['verified'])

    def test_boolean_attestation_cannot_replace_raw_public_freeze(self):
        self.assertFalse(evidence.public_freeze({'code.py':b'x'},[],lambda *_:b'x',
                         '2026-10-15T00:00:00+00:00')['verified'])

    def test_changed_registered_code_or_policy_is_blocked(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'config').mkdir();(root/'system').mkdir()
            policy={'protocol_id':'TEST','effective_from_utc':'2026-10-15T00:00:00+00:00'}
            body=json.dumps(policy).encode();(root/'config/factor_evaluation_v1.json').write_bytes(body)
            (root/'factor_evaluation_v1.py').write_bytes(b'x\n')
            registry={'version_id':'TEST','protocol_sha256':hashlib.sha256(body).hexdigest(),
                      'code_sha256_lf':{'factor_evaluation_v1.py':hashlib.sha256(b'x\n').hexdigest()}}
            path=root/'system/factor_evaluation_v1_registry.json';path.write_text(json.dumps(registry))
            self.assertEqual(evidence.check_freeze(root)['protocol_id'],'TEST')
            (root/'factor_evaluation_v1.py').write_bytes(b'y\n')
            with self.assertRaises(ValueError):evidence.check_freeze(root)

    def test_missing_or_same_event_cost_mismatch_is_not_silently_zero(self):
        # The adapter must not count a different public head's book as a fill.
        book={'captured_at_utc':'2026-10-16T00:01:00+00:00',
              'eligible_sources':{'event':{'source_verified':True,'observed_public_utc':'2026-10-16T00:00:10+00:00',
                                         'source_witness_head_sha':'bad','pair_sha256':'pair'}}}
        item={'forecast_id':'event','pair_sha256':'pair','observed_public_utc':'2026-10-16T00:00:10+00:00',
              'source_witness_head_sha':'good'}
        self.assertFalse(evidence.book_eligible(book,item))
        book['eligible_sources']['event']['source_witness_head_sha']='good'
        self.assertTrue(evidence.book_eligible(book,item))
        book['eligible_sources']['event']['pair_sha256']='other'
        self.assertFalse(evidence.book_eligible(book,item))

    def test_real_depth_replay_counts_costs_and_keeps_missing_exit_unknown(self):
        from datetime import datetime,timedelta,timezone
        t=datetime(2026,10,16,tzinfo=timezone.utc)
        stamp=lambda m:(t+timedelta(minutes=m)).isoformat()
        pair={'production':{'forecast_id':'e','horizon_hours':'1','issued_at_utc':stamp(0),'target_utc':stamp(60)},
              'record_sha256':'pair','decision':{'paper_action':'NO_TRADE'}}
        public={'observed_public_utc':stamp(.1),'head_sha':'head'}
        item={'forecast_id':'e','source_verified':True,'pair_sha256':'pair',
              'observed_public_utc':stamp(.1),'source_witness_head_sha':'head'}
        books=[]
        for minute in (1,20,40,61,80):
            books.append({'requested_at_utc':stamp(minute-.01),'captured_at_utc':stamp(minute),
                'quote':{'quote_utc':stamp(minute),'spread_bps':0},'asks':[[100,1000]],
                'bids':[[100,1000]],'book_sha256':str(minute),'eligible_sources':{'e':item}})
        r=evidence.economic_deltas(None,books,pair,{'paper_action':'PAPER_LONG'},public,t+timedelta(minutes=85))
        self.assertIsNotNone(r)
        self.assertAlmostEqual(r['cash_net'],100*.999*.9995/(100*1.001*1.0005)-1)
        self.assertLess(r['cash_stress_net'],r['cash_net'])
        self.assertEqual(r['hold_net'],0)
        self.assertIsNone(evidence.economic_deltas(None,books[:3],pair,{'paper_action':'PAPER_LONG'},public,t+timedelta(minutes=85)))

    def test_change_proposal_requires_intact_rollback_and_future_activation(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'old.py').write_bytes(b'old');(root/'new.py').write_bytes(b'new')
            report={'protocol_id':'P','group':'FOUR_HOUR','status':'REVIEW_PROMOTION','horizon_hours':1}
            raw=json.dumps(report).encode();(root/'report.json').write_bytes(raw)
            proposal={'protocol_id':'P','group':'FOUR_HOUR','horizon_hours':1,'change':'PROMOTE',
                'review_report_path':'report.json','review_report_sha256':hashlib.sha256(raw).hexdigest(),
                'old_version':'OLD','new_version':'NEW','effective_from_utc':'2026-11-01T00:00:00+00:00',
                'rollback_files_sha256':{'old.py':hashlib.sha256(b'old').hexdigest()},
                'new_files_sha256':{'new.py':hashlib.sha256(b'new').hexdigest()}}
            self.assertFalse(evidence.validate_change(root,proposal,'2026-10-20T00:00:00+00:00')['production_allowed'])
            (root/'old.py').write_bytes(b'changed')
            with self.assertRaises(ValueError):evidence.validate_change(root,proposal,'2026-10-20T00:00:00+00:00')

    def test_forged_feature_value_cannot_pass_prepared_hash_only(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            data={'value':2.,'evidence':[],'covered_sources':[]}
            with self.assertRaises(ValueError):evidence.replay_feature(root,'NEWS_TONE',data,'2026-10-16T00:00:00+00:00',[])

    def test_valid_old_raw_observation_cannot_replace_latest_asof_feature(self):
        import enhancement_sources as sources
        from datetime import datetime,timezone
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);seen=datetime(2026,10,16,tzinfo=timezone.utc)
            raw=json.dumps({'data':[{'value':'30','timestamp':'1792022400'},
                                    {'value':'80','timestamp':'1792108800'}]}).encode()
            path=root/'raw.json';path.write_bytes(raw)
            observations=sources.parse('BTC_FEAR_GREED_PROXY',raw,seen)
            for row in observations:row.update(raw_path='raw.json',raw_sha256=hashlib.sha256(raw).hexdigest(),requested_at_utc=seen.isoformat())
            old={'value':-.8,'evidence':[observations[0]]}
            with self.assertRaises(ValueError):evidence.replay_feature(root,'BTC_SENTIMENT',old,seen.isoformat(),observations)


if __name__=='__main__':unittest.main()
