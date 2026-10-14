import copy,hashlib,io,json,unittest
from datetime import datetime,timedelta,timezone

NOW=datetime(2026,10,14,1,tzinfo=timezone.utc)

class Readiness(unittest.TestCase):
    def module(self):
        import evidence_readiness_v2 as m
        return m

    def row(self):
        return {'kind':'OI','capture_id':'x','first_seen_utc':(NOW-timedelta(minutes=10)).isoformat(),
                'usable_from_utc':(NOW-timedelta(minutes=10)).isoformat(),'raw_path':'data/derivatives_pit_v1/raw/'+'a'*64+'.json',
                'raw_sha256':hashlib.sha256(b'raw').hexdigest()}

    def server(self):
        m=self.module()
        return {'id':10,'repository':{'full_name':m.REPO},'path':m.WORKFLOW,'head_sha':'a'*40,
                'head_branch':'main','event':'workflow_dispatch','created_at':(NOW-timedelta(minutes=8)).isoformat()},[
                {'id':20,'run_id':10,'name':'source-publication','status':'completed','conclusion':'success',
                 'started_at':(NOW-timedelta(minutes=8)).isoformat(),'completed_at':(NOW-timedelta(minutes=7)).isoformat()}]

    def reader(self,head,path):
        m=self.module();r=self.row()
        return (m.canon(r)+b'\n') if path==m.LEDGER else b'raw'

    def test_successful_job_time_and_exact_source_bound(self):
        m=self.module();run,jobs=self.server()
        witness=m.publication(self.row(),[(run,jobs)],self.reader,NOW)
        self.assertEqual(witness['job_id'],20)
        self.assertEqual(witness['usable_from_utc'],(NOW-timedelta(minutes=7)).isoformat())

    def test_future_or_failed_job_and_wrong_raw_never_qualify(self):
        m=self.module();run,jobs=self.server()
        for change in ('future','failed','wrong_branch','wrong_raw'):
            r,j=copy.deepcopy(run),copy.deepcopy(jobs)
            if change=='future':j[0]['completed_at']=(NOW+timedelta(seconds=1)).isoformat()
            if change=='failed':j[0]['conclusion']='failure'
            if change=='wrong_branch':r['head_branch']='untrusted'
            reader=(lambda *a:b'forged') if change=='wrong_raw' else self.reader
            self.assertIsNone(m.publication(self.row(),[(r,j)],reader,NOW))

    def test_no_historical_backdating_and_no_caller_verified_flag(self):
        m=self.module();r=self.row();r['verified']=True
        self.assertIsNone(m.publication(r,[],self.reader,NOW))
        run,jobs=self.server()
        self.assertIsNone(m.publication(self.row(),[(run,jobs)],self.reader,NOW-timedelta(minutes=9)))

    def test_revision_selection_excludes_later_receipts(self):
        m=self.module();r=self.row();late=dict(r,capture_id='later',first_seen_utc=(NOW+timedelta(seconds=1)).isoformat())
        self.assertEqual(m.latest_asof([r,late],NOW)['OI']['capture_id'],'x')

    def test_coverage_has_all24_denominator_and_excludes_unpublished(self):
        m=self.module();rows=[dict(self.row(),capture_id=str(i),first_seen_utc=(NOW-timedelta(hours=i+1)).isoformat()) for i in range(24)]
        self.assertEqual(m.known_coverage(rows,'OI',NOW,set()),0)
        self.assertEqual(m.known_coverage(rows,'OI',NOW,{r['capture_id'] for r in rows}),1)

    def test_latest_unpublished_revision_does_not_fall_back(self):
        m=self.module();r=self.row();latest=dict(r,capture_id='new',first_seen_utc=(NOW-timedelta(minutes=1)).isoformat())
        self.assertEqual(m.latest_asof([r,latest],NOW)['OI']['capture_id'],'new')

class Retention(unittest.TestCase):
    def module(self):
        import user_managed_retention_v2 as m
        return m

    def config(self):
        return {'provider':'aws_s3_object_lock','bucket':'doge-archive','bucket_owner_account_id':'123456789012',
                'collector_account_id':'123456789012','key':'doge/evidence/'+hashlib.sha256(b'raw').hexdigest()+'.zip',
                'version_id':'version1','required_until_utc':'2028-10-16T00:00:00Z'}

    def client(self,mode='COMPLIANCE',body=b'raw',until=None):
        class Client:
            def get_object_lock_configuration(self,**kwargs):return {'ObjectLockConfiguration':{'ObjectLockEnabled':'Enabled'}}
            def get_bucket_versioning(self,**kwargs):return {'Status':'Enabled'}
            def get_object_retention(self,**kwargs):return {'Retention':{'Mode':mode,'RetainUntilDate':until or NOW+timedelta(days=740)}}
            def get_object(self,**kwargs):return {'Body':io.BytesIO(body),'VersionId':'version1','LastModified':NOW-timedelta(seconds=1)}
        class Identity:
            def get_caller_identity(self):return {'Account':'123456789012'}
        return Client(),Identity()

    def test_same_account_allowed_but_no_independent_custodian_claim(self):
        m=self.module();client,identity=self.client()
        r=m.verify(self.config(),hashlib.sha256(b'raw').hexdigest(),NOW,client,identity)
        self.assertTrue(r['locked_retention_verified'])
        self.assertFalse(r['independent_custodian_verified'])

    def test_missing_provider_never_verified(self):
        m=self.module();self.assertFalse(m.verify(None,'a'*64,NOW)['locked_retention_verified'])

    def test_bad_hash_governance_and_short_lock_rejected(self):
        m=self.module()
        for c in (self.client(mode='GOVERNANCE'),self.client(body=b'changed'),self.client(until=NOW+timedelta(days=1))):
            self.assertFalse(m.verify(self.config(),hashlib.sha256(b'raw').hexdigest(),NOW,*c)['locked_retention_verified'])

    def test_null_version_wrong_owner_and_future_object_rejected(self):
        m=self.module();c=self.config();c['version_id']='null';client,identity=self.client()
        self.assertFalse(m.verify(c,hashlib.sha256(b'raw').hexdigest(),NOW,client,identity)['locked_retention_verified'])
        c=self.config();c['bucket_owner_account_id']='bad'
        self.assertFalse(m.verify(c,hashlib.sha256(b'raw').hexdigest(),NOW,client,identity)['locked_retention_verified'])

    def test_after_target_verification_is_not_pre_target_evidence(self):
        m=self.module();client,identity=self.client()
        r=m.verify(self.config(),hashlib.sha256(b'raw').hexdigest(),NOW,client,identity,target_at=NOW-timedelta(seconds=2))
        self.assertTrue(r['locked_retention_verified']);self.assertFalse(r['locked_before_target_verified'])

    def test_future_object_clock_rejected(self):
        m=self.module();client,identity=self.client();original=client.get_object
        def future(**kwargs):
            response=original(**kwargs);response['LastModified']=NOW+timedelta(seconds=1);return response
        client.get_object=future
        self.assertFalse(m.verify(self.config(),hashlib.sha256(b'raw').hexdigest(),NOW,client,identity)['locked_retention_verified'])

    def test_optional_sdk_supports_conditional_upload_contract(self):
        try:import botocore.session
        except ImportError:self.skipTest('optional upload SDK not installed locally')
        shape=botocore.session.get_session().get_service_model('s3').operation_model('PutObject').input_shape
        self.assertIn('IfNoneMatch',shape.members);self.assertIn('ChecksumSHA256',shape.members)

    def test_upload_fails_without_approved_default_before_any_put(self):
        m=self.module();client,identity=self.client()
        with self.assertRaises(ValueError):m.upload(self.config(),b'raw',client,identity)

    def test_upload_explicit_lock_covers_fixed_review_deadline(self):
        m=self.module();client,identity=self.client();calls=[]
        client.get_object_lock_configuration=lambda **kw:{'ObjectLockConfiguration':{'ObjectLockEnabled':'Enabled','Rule':{'DefaultRetention':{'Mode':'COMPLIANCE','Days':730}}}}
        def put(**kwargs):calls.append(kwargs);return {'VersionId':'version1'}
        client.put_object=put
        client.get_object=lambda **kw:{'Body':io.BytesIO(b'raw'),'VersionId':'version1','LastModified':datetime.now(timezone.utc)-timedelta(seconds=1)}
        client.get_object_retention=lambda **kw:{'Retention':{'Mode':'COMPLIANCE','RetainUntilDate':datetime(2029,1,1,tzinfo=timezone.utc)}}
        result=m.upload(self.config(),b'raw',client,identity)
        self.assertTrue(result['locked_retention_verified'])
        self.assertEqual(calls[0]['ObjectLockMode'],'COMPLIANCE')
        self.assertGreaterEqual(calls[0]['ObjectLockRetainUntilDate'],datetime(2028,10,16,tzinfo=timezone.utc))
        self.assertEqual(calls[0]['IfNoneMatch'],'*')
