import copy
import importlib.util
import math
import hashlib
import json
import unittest
from datetime import datetime,timedelta,timezone

import phase1_research as research


class Shadow(unittest.TestCase):
    def module(self):
        self.assertIsNotNone(importlib.util.find_spec('learned_shadow_v1'),'future candidate module required')
        import learned_shadow_v1
        return learned_shadow_v1

    def policy(self):
        return {'protocol_id':'FIXTURE','effective_from_utc':'2026-10-16T00:00:00+00:00',
                'groups':list(research.variants()),'horizons_hours':[1,24],
                'max_issue_delay_minutes':45,'target_window_minutes':25,
                'slot_steps_hours':{'1':2,'24':25},'fee_per_side':.001,'slippage_per_side':.0005,
                'stress_multiplier':2,'notional_usdt':100,'family_size':1080,
                'minimum_samples':100,'minimum_days':90,'automatic_promotion':False}

    def packet(self,at,price=100):
        ms=int(at.timestamp()*1000)
        raw={'ticker':{'code':'0','data':[{'instId':'DOGE-USDT','ts':str(ms),'last':str(price)}]},
             'book':{'code':'0','data':[{'ts':str(ms),'asks':[[str(price+.1),'1000','0','1']],
                                      'bids':[[str(price-.1),'1000','0','1']]}]}}
        packet={'requested_at_utc':at.isoformat(),'captured_at_utc':at.isoformat(),'raw':raw,
                'source_urls':{'ticker':'https://www.okx.com/api/v5/market/ticker?instId=DOGE-USDT',
                               'book':'https://www.okx.com/api/v5/market/books?instId=DOGE-USDT&sz=400'}}
        return self.sync(packet)

    def sync(self,p):
        p['raw_text']={k:json.dumps(v) for k,v in p['raw'].items()}
        p['raw_sha256']={k:hashlib.sha256(v.encode()).hexdigest() for k,v in p['raw_text'].items()}
        return p

    def frame(self,at):
        last=int(at.timestamp()*1000)//3600000*3600000-3600000
        times=[last-(24-i)*3600000 for i in range(25)]
        return {'times':times,**{s:{'close':[100+i for i in range(25)],'volume':[100]*25,
              'seen':[t+3600000 for t in times]} for s in ('DOGE-USDT','BTC-USDT')}}

    def package(self):
        result={'trained_at_utc':'2026-10-14T00:00:00+00:00','models':{},'majority_p':{'1':.4,'24':.4}}
        for h in ('1','24'):
            result['models'][h]={}
            for name,groups in research.variants().items():
                cols=[i for g in groups for i in research.GROUPS[g]]
                result['models'][h][name]={'columns':cols,'mean':[0.]*len(cols),'scale':[1.]*len(cols),
                    'weights':[0.]*len(cols),'intercept':0.}
        return result

    def event(self):
        m=self.module();at=datetime(2026,10,16,0,5,tzinfo=timezone.utc)
        return m.issue(self.policy(),self.package(),self.frame(at),self.packet(at),at,1)

    def test_pre_activation_issue_is_rejected(self):
        m=self.module();at=datetime(2026,10,15,tzinfo=timezone.utc)
        with self.assertRaises(ValueError):m.issue(self.policy(),self.package(),self.frame(at),self.packet(at),at,1)

    def test_no_opportunistic_late_issue(self):
        m=self.module();at=datetime(2026,10,16,0,46,tzinfo=timezone.utc)
        with self.assertRaises(ValueError):m.issue(self.policy(),self.package(),self.frame(at),self.packet(at),at,1)

    def test_family_drift_and_overlapping_slots_rejected(self):
        m=self.module();p=self.policy();p['groups']=p['groups'][:-1]
        with self.assertRaises(ValueError):m.check_policy(p)
        p=self.policy();p['slot_steps_hours']['24']=24
        with self.assertRaises(ValueError):m.check_policy(p)

    def test_event_has_exact_same_quote_and_all_nine_candidates(self):
        e=self.event()
        self.assertEqual(len(e['candidate_p']),9)
        self.assertTrue(all(v==.5 for v in e['candidate_p'].values()))
        self.assertEqual(e['frozen_direction'],'DOWN')
        self.assertEqual(e['baseline_kind'],'frozen_rule_replay_not_original_production_issuance')
        self.assertFalse(e['real_orders_authorized'])

    def test_late_feature_receipt_rejected(self):
        m=self.module();at=datetime(2026,10,16,0,5,tzinfo=timezone.utc);f=self.frame(at)
        f['BTC-USDT']['seen'][-1]=int((at+timedelta(minutes=1)).timestamp()*1000)
        with self.assertRaises(ValueError):m.issue(self.policy(),self.package(),f,self.packet(at),at,1)

    def test_gap_and_stale_feature_window_rejected(self):
        m=self.module();at=datetime(2026,10,16,0,5,tzinfo=timezone.utc);f=self.frame(at)
        f['times'][10]+=1
        with self.assertRaises(ValueError):m.causal_features(f,at)
        f=self.frame(at-timedelta(hours=1))
        with self.assertRaises(ValueError):m.causal_features(f,at)

    def test_invalid_or_future_book_is_rejected(self):
        m=self.module();at=datetime(2026,10,16,0,5,tzinfo=timezone.utc)
        for change in ('crossed','future','nan','duplicate'):
            p=self.packet(at)
            if change=='crossed':p['raw']['book']['data'][0]['bids'][0][0]='200'
            elif change=='future':p['raw']['book']['data'][0]['ts']=str(int(at.timestamp()*1000)+1)
            elif change=='nan':p['raw']['ticker']['data'][0]['last']='nan'
            else:p['raw']['book']['data'][0]['asks']*=2
            with self.assertRaises(ValueError):m.quote(self.sync(p))

    def test_before_target_and_late_target_quotes_rejected(self):
        m=self.module();e=self.event();target=m.utc(e['target_utc'])
        for at in (target-timedelta(seconds=1),target+timedelta(minutes=26)):
            with self.assertRaises(ValueError):m.score(e,self.packet(at),self.policy())

    def test_paper_costs_are_same_event_and_stress_is_worse(self):
        m=self.module();e=self.event();target=m.utc(e['target_utc']);s=m.score(e,self.packet(target,110),self.policy())
        cost=s['economic_deltas']['full']
        self.assertGreater(cost['cash_net'],0)
        self.assertLess(cost['cash_stress_net'],cost['cash_net'])
        self.assertAlmostEqual(cost['hold_net'],0)
        self.assertEqual(s['y'],1)
        self.assertFalse(s['actual_fill_verified'])

    def test_missing_depth_keeps_label_but_not_fake_economics(self):
        m=self.module();e=self.event();p=self.packet(m.utc(e['target_utc']),110)
        p['raw']['book']['data'][0]['bids'][0][1]='.00001'
        s=m.score(e,self.sync(p),self.policy())
        self.assertEqual(s['y'],1)
        self.assertIsNone(s['economic_deltas']['full'])

    def test_due_slots_keep_missed_issuance_denominator(self):
        m=self.module();p=self.policy();at=datetime(2026,10,16,8,15,tzinfo=timezone.utc)
        rows=m.due_slots(p,at,1)
        self.assertEqual(len(rows),4)
        out=m.audit_records(p,[],[],at)
        self.assertEqual(out['horizons']['1']['expected'],4)
        self.assertEqual(out['horizons']['1']['missing_issuance'],4)

    def test_duplicate_event_or_score_rejected(self):
        m=self.module();e=self.event();now=m.utc(e['target_utc'])+timedelta(hours=2)
        with self.assertRaises(ValueError):m.audit_records(self.policy(),[e,e],[],now)

    def test_shifted_slot_phase_is_rejected(self):
        m=self.module();e=self.event();e['slot_utc']='2026-10-16T00:01:00+00:00'
        e['event_id']=str(int(m.utc(e['slot_utc']).timestamp()))+'_1'
        with self.assertRaises(ValueError):m.audit_records(self.policy(),[e],[],m.utc(e['target_utc']))

    def test_source_frame_is_reconstructed_from_real_csv(self):
        m=self.module();at=datetime(2026,10,16,0,5,tzinfo=timezone.utc);f=self.frame(at)
        header='symbol,open_utc_ms,close,volume_base,first_ingested_utc,source\n'
        rows=[]
        for symbol in ('DOGE-USDT','BTC-USDT'):
            for i,t in enumerate(f['times']):
                rows.append(f"{symbol},{t},{f[symbol]['close'][i]},100,{datetime.fromtimestamp((t+3600000)/1000,timezone.utc).isoformat()},OKX history-candles 1H confirmed\n")
        self.assertEqual(m.frame_from_csv((header+''.join(rows)).encode(),at),f)
        with self.assertRaises(ValueError):m.frame_from_csv((header+''.join(rows)+rows[-1]).encode(),at)

    def test_boolean_witness_cannot_pass(self):
        m=self.module();with_flag={'verified':True}
        self.assertFalse(m.server_witness({'x':b'bytes'},[with_flag],lambda head,path:b'bytes',
            datetime(2026,10,16,tzinfo=timezone.utc)))

    def test_empty_files_cannot_create_a_registration_witness(self):
        m=self.module();run={'head_sha':'a'*40,'path':m.WORKFLOW,'status':'completed','conclusion':'success',
             'event':'push','created_at':'2026-10-14T00:00:00Z','updated_at':'2026-10-14T00:05:00Z',
             'id':1,'repository':{'full_name':m.REPO}}
        self.assertFalse(m.server_witness({},[run],lambda head,path:b'irrelevant',datetime(2026,10,16,tzinfo=timezone.utc)))

    def test_registered_files_must_bind_policy_package_and_real_code(self):
        m=self.module()
        self.assertFalse(m.registration_files_valid(self.policy(),self.package(),{}))
        self.assertFalse(m.registration_files_valid(self.policy(),self.package(),{'x':b'bytes'}))

    def test_target_cannot_overlap_next_planned_slot(self):
        m=self.module();at=datetime(2026,10,16,0,44,tzinfo=timezone.utc)
        e=m.issue(self.policy(),self.package(),self.frame(at),self.packet(at),at,1)
        # Frozen maximum 25m grace is capped by the next ex-ante slot at 02:00.
        with self.assertRaises(ValueError):m.score(e,self.packet(datetime(2026,10,16,2,1,tzinfo=timezone.utc),110),self.policy())

    def test_server_witness_checks_actual_bytes_and_server_completion(self):
        m=self.module();before=datetime(2026,10,16,tzinfo=timezone.utc)
        run={'head_sha':'a'*40,'path':'.github/workflows/learned_shadow_v1.yml','status':'completed',
             'conclusion':'success','event':'push','created_at':'2026-10-14T00:00:00Z',
             'updated_at':'2026-10-14T00:05:00Z','id':1,'repository':{'full_name':'lzwislam-collab/doge-data-collector'}}
        self.assertTrue(m.server_witness({'x':b'bytes'},[run],lambda head,path:b'bytes',before))
        self.assertFalse(m.server_witness({'x':b'bytes'},[run],lambda head,path:b'changed',before))
        run['updated_at']='2026-10-16T00:05:00Z'
        self.assertFalse(m.server_witness({'x':b'bytes'},[run],lambda head,path:b'bytes',before))

    def test_unwitnessed_scores_cannot_enter_inference(self):
        m=self.module();e=self.event();at=m.utc(e['target_utc'])+timedelta(minutes=1)
        s=m.score(e,self.packet(m.utc(e['target_utc']),110),self.policy())
        out=m.audit_records(self.policy(),[e],[s],at)
        self.assertFalse(out['promotion_authorized'])
        self.assertEqual(out['horizons']['1']['witness_qualified'],0)

    def test_target_score_needs_its_own_exact_server_witness(self):
        m=self.module();e=self.event();target=m.utc(e['target_utc']);s=m.score(e,self.packet(target,110),self.policy())
        self.assertFalse(m.target_witness(e,s,[],lambda h,p:b'bytes',self.policy()))

    def test_http_raw_bytes_hash_is_checked(self):
        m=self.module();at=datetime(2026,10,16,0,5,tzinfo=timezone.utc);p=self.packet(at)
        p['raw_text']={'ticker':'{}','book':'{}'};p['raw_sha256']={'ticker':'a'*64,'book':'b'*64}
        with self.assertRaises(ValueError):m.quote(p)

    def test_unsupported_http_origin_cannot_enter_evidence(self):
        m=self.module();at=datetime(2026,10,16,0,5,tzinfo=timezone.utc);p=self.packet(at)
        p['source_urls']['ticker']='https://example.invalid/ticker'
        with self.assertRaises(ValueError):m.quote(p)

    def test_capture_and_book_after_deadline_cannot_be_cost_evidence(self):
        m=self.module();e=self.event();target=m.utc(e['target_utc']);deadline=m.target_deadline(e,self.policy())
        packet=self.packet(deadline-timedelta(seconds=1),110)
        packet['captured_at_utc']=(deadline+timedelta(seconds=1)).isoformat()
        packet['raw']['book']['data'][0]['ts']=str(int((deadline+timedelta(seconds=1)).timestamp()*1000))
        with self.assertRaises(ValueError):m.score(e,self.sync(packet),self.policy())

    def test_server_completed_before_capture_cannot_witness_target(self):
        m=self.module();e=self.event();target=m.utc(e['target_utc']);s=m.score(e,self.packet(target+timedelta(seconds=10),110),self.policy())
        run={'head_sha':'a'*40,'path':m.WORKFLOW,'status':'completed','conclusion':'success','event':'push',
            'created_at':target.isoformat(),'updated_at':(target+timedelta(seconds=5)).isoformat(),
            'id':1,'repository':{'full_name':m.REPO}}
        self.assertFalse(m.target_witness(e,s,[run],lambda h,p:m.encode(s),self.policy()))


if __name__=='__main__':unittest.main()
