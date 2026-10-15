import copy
import unittest
import json
import tempfile
from pathlib import Path
import p2_plus_review as review


class Diagnostics(unittest.TestCase):
    def test_local_audit_excludes_issued_unmatured_formal_event(self):
        from test_phase3_independent import Independent
        import phase2_forward_v2 as forward
        from datetime import timedelta
        _,_,events,_,_,_=Independent().cohort()
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);folder=root/forward.BASE/'events';folder.mkdir(parents=True)
            (folder/'event.json').write_text(json.dumps(events[0]),encoding='utf-8')
            r=review.local_audit(root,forward.utc(events[0]['issued_at_utc'])+timedelta(minutes=10))
            self.assertEqual(r['diagnostics']['scheduled_denominator'],0)
            self.assertEqual(r['diagnostics']['paired_observed'],0)
            self.assertEqual(r['formal_issued'],1)
            self.assertEqual(r['formal_pending_maturity'],1)

    def test_pending_scored_event_cannot_replace_missing_matured_slot(self):
        from test_phase3_independent import Independent
        import phase2_forward_v2 as forward
        from datetime import timedelta
        _,_,events,scores,_,_=Independent().cohort()
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);base=root/forward.BASE
            for folder in ('events','scores'):(base/folder).mkdir(parents=True)
            (base/'events'/'pending.json').write_text(json.dumps(events[1]),encoding='utf-8')
            (base/'scores'/'pending.json').write_text(json.dumps(scores[1]),encoding='utf-8')
            at=forward.utc(events[1]['issued_at_utc'])+timedelta(minutes=10)
            r=review.local_audit(root,at)
            self.assertEqual(r['formal_issued'],1)
            self.assertEqual(r['formal_pending_maturity'],1)
            self.assertEqual(r['diagnostics']['scheduled_denominator'],1)
            self.assertEqual(r['diagnostics']['missing_or_unqualified'],1)
            self.assertEqual(r['diagnostics']['paired_observed'],0)

    def rows(self):
        events = [{'event_id':str(i), 'cohort':'formal', 'candidate_p':{'selected':p,'frozen_full':.5}} for i,p in enumerate((.9,.8,.2,.1))]
        scores = [{'event_id':str(i),'y':y} for i,y in enumerate((1,0,0,1))]
        return events,scores

    def test_recall_and_calibration_are_computed_on_same_events(self):
        e,s=self.rows();r=review.diagnostics(e,s,4)
        self.assertEqual(r['models']['selected']['up_recall'],.5)
        self.assertEqual(r['models']['selected']['down_recall'],.5)
        self.assertAlmostEqual(r['models']['selected']['brier'],.375)
        self.assertEqual(sum(x['n'] for x in r['models']['selected']['calibration']),4)

    def test_missing_event_never_shrinks_schedule_or_claims_success(self):
        e,s=self.rows();r=review.diagnostics(e,s[1:],5)
        self.assertEqual(r['scheduled_denominator'],5)
        self.assertEqual(r['missing_or_unqualified'],2)
        self.assertEqual(r['status'],'INCOMPLETE_DIAGNOSTIC_ONLY')
        self.assertFalse(r['promotion_authorized'])

    def test_duplicates_boolean_labels_and_nonfinite_probability_rejected(self):
        e,s=self.rows()
        for ee,ss in ((e+e[:1],s),(e,s+s[:1])):
            with self.assertRaises(ValueError):review.diagnostics(ee,ss,4)
        for key,value in (('y',True),('y',2)):
            bad=copy.deepcopy(s);bad[0][key]=value
            with self.assertRaises(ValueError):review.diagnostics(e,bad,4)
        e[0]['candidate_p']['selected']=float('nan')
        with self.assertRaises(ValueError):review.diagnostics(e,s,4)

    def test_single_class_has_no_invented_recall(self):
        e,s=self.rows()
        for row in s:row['y']=1
        self.assertIsNone(review.diagnostics(e,s,4)['models']['selected']['down_recall'])

    def test_zero_schedule_and_qualified_mask_fail_closed(self):
        r=review.diagnostics([],[],0)
        self.assertEqual(r['status'],'NO_FORMAL_EVIDENCE')
        e,s=self.rows();r=review.diagnostics(e,s,4,qualified_ids={'0'})
        self.assertEqual(r['paired_observed'],1)
        self.assertEqual(r['missing_or_unqualified'],3)

    def test_canary_score_without_cohort_is_excluded_like_frozen_assessor(self):
        r=review.diagnostics([{'event_id':'1_canary','cohort':'canary'}], [{'event_id':'1_canary','y':1}],0)
        self.assertEqual(r['paired_observed'],0)

    def test_future_draft_has_numeric_thresholds_but_no_activation(self):
        draft=review.future_protocol_draft()
        r=review.validate_future_protocol(draft)
        self.assertFalse(r['eligible_for_prospective_registration'])
        self.assertFalse(r['production_authorized'])
        self.assertIn('model_sha256 missing',r['blockers'])
        self.assertEqual(draft['rollback']['maximum_paper_drawdown_pct'],10)

    def test_even_complete_witness_cannot_activate_a_draft(self):
        draft=review.future_protocol_draft()
        for key in draft['identity_bindings']:draft['identity_bindings'][key]='a'*64
        draft['observation_start_utc']='2026-11-01T00:00:00Z'
        draft['public_registration_witness']={'protocol_sha256':review.protocol_digest(draft),
            'published_at_utc':'2026-10-30T00:00:00Z','immutable_locator':'https://example.org/commit/abc'}
        r=review.validate_future_protocol(draft)
        self.assertTrue(r['eligible_for_prospective_registration'])
        self.assertFalse(r['production_authorized'])
        self.assertFalse(r['runtime_activation_authorized'])
        draft['public_registration_witness']['published_at_utc']='2026-11-02T00:00:00Z'
        self.assertFalse(review.validate_future_protocol(draft)['eligible_for_prospective_registration'])
