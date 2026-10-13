import copy, hashlib, io, json, tempfile, unittest, zipfile
from pathlib import Path
from datetime import datetime, timedelta, timezone
try:
    import pit_retention_preparation as module
except ImportError:
    module=None

NOW=datetime(2026,10,13,2,tzinfo=timezone.utc)
def digest(row):return hashlib.sha256(json.dumps(row,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()

class PreparationTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(module,'PIT/retention preparation module missing')
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
    def row(self,seen=NOW,value=1,prior=None,previous=None):
        raw=b'{"source":"fixture"}';sha=hashlib.sha256(raw).hexdigest()
        path='data/enhancement_sources_v1/raw/'+sha[:2]+'/'+sha+'.txt'
        local=self.root/path;local.parent.mkdir(parents=True,exist_ok=True);local.write_bytes(raw)
        r={'source_id':'FIXTURE','observation_key':'day','source_url':'https://example.test',
           'value':value,'requested_at_utc':(seen-timedelta(seconds=1)).isoformat(),
           'first_seen_utc':seen.isoformat(),'usable_from_utc':seen.isoformat(),
           'published_at_utc':None,'raw_path':path,'raw_sha256':sha,
           'revision_of_sha256':prior,'previous_sha256':previous}
        r['record_sha256']=digest(r);return r
    def rewrite_hash(self,r):r.pop('record_sha256');r['record_sha256']=digest(r)
    def test_actual_first_receipt_blocks_historical_date_and_late_revision(self):
        first=self.row();late=self.row(NOW+timedelta(hours=1),value=2,prior=first['record_sha256'],previous=first['record_sha256'])
        self.assertEqual(module.select_asof(self.root,[first,late],NOW,3600),[first])
        self.assertEqual(module.select_asof(self.root,[first],NOW-timedelta(seconds=1),3600),[])
    def test_stale_observation_is_removed_without_zero_substitution(self):
        self.assertEqual(module.select_asof(self.root,[self.row()],NOW+timedelta(hours=2),3600),[])
    def test_revision_cannot_move_receipt_clock_backwards(self):
        first=self.row();late=self.row(NOW-timedelta(seconds=1),value=2,prior=first['record_sha256'],previous=first['record_sha256'])
        with self.assertRaises(ValueError):module.select_asof(self.root,[first,late],NOW,3600)
    def test_equal_receipt_revision_is_not_an_independent_version(self):
        first=self.row();late=self.row(NOW,value=2,prior=first['record_sha256'],previous=first['record_sha256'])
        with self.assertRaises(ValueError):module.select_asof(self.root,[first,late],NOW,3600)
    def test_raw_tamper_duplicate_revision_and_chain_mutation_rejected(self):
        for defect in ('raw','chain','revision','duplicate','future_publication','backdated_usable','nonfinite'):
            r=self.row()
            if defect=='raw':(self.root/r['raw_path']).write_bytes(b'forged')
            elif defect=='chain':r['previous_sha256']='a'*64
            elif defect=='revision':r['revision_of_sha256']='a'*64
            elif defect=='future_publication':r['published_at_utc']=(NOW+timedelta(seconds=1)).isoformat()
            elif defect=='backdated_usable':r['usable_from_utc']=(NOW-timedelta(seconds=1)).isoformat()
            elif defect=='nonfinite':r['value']=float('nan')
            self.rewrite_hash(r)
            with self.assertRaises(ValueError,msg=defect):module.select_asof(self.root,[r,r] if defect=='duplicate' else [r],NOW,3600)
    def test_path_escape_and_symlink_rejected(self):
        r=self.row();r['raw_path']='../outside';self.rewrite_hash(r)
        with self.assertRaises(ValueError):module.select_asof(self.root,[r],NOW,3600)
    def test_input_is_not_mutated_and_timezone_is_required(self):
        r=self.row();before=copy.deepcopy(r);module.select_asof(self.root,[r],NOW,3600);self.assertEqual(r,before)
        with self.assertRaises(ValueError):module.select_asof(self.root,[r],NOW.replace(tzinfo=None),3600)
    def test_invalid_age_limits_fail_closed(self):
        for age in (0,-1,float('nan'),float('inf'),True):
            with self.assertRaises(ValueError):module.select_asof(self.root,[self.row()],NOW,age)
    def test_bundle_contains_exact_bytes_and_no_external_verification_claim(self):
        path=self.root/'data/input.json';path.parent.mkdir();path.write_bytes(b'{"a":1}')
        bundle,receipt=module.prepare_bundle(self.root,{'data/input.json':hashlib.sha256(path.read_bytes()).hexdigest()},NOW)
        with zipfile.ZipFile(bundle) as archive:
            self.assertEqual(archive.read('data/input.json'),path.read_bytes())
            manifest=json.loads(archive.read('bundle_manifest.json'))
        self.assertEqual(receipt['status'],'NOT_CONFIGURED');self.assertFalse(receipt['independent_retention_verified'])
        self.assertEqual(manifest['prepared_at_utc'],NOW.isoformat())
        self.assertFalse(manifest['historical_first_publication_verified'])
    def test_bundle_rejects_hash_mismatch_escape_empty_and_secret_paths(self):
        path=self.root/'data/input';path.parent.mkdir();path.write_bytes(b'x')
        for objects in ({},{'data/input':'a'*64},{'../outside':'a'*64},{'.aws/credentials':'a'*64}):
            with self.assertRaises(ValueError):module.prepare_bundle(self.root,objects,NOW)
    def test_no_config_does_not_contact_external_clients(self):
        self.assertEqual(module.verify_external(None,'a'*64,NOW,client=object())['status'],'NOT_CONFIGURED')
    def test_output_directory_symlink_cannot_redirect_package_writes(self):
        path=self.root/'data/input';path.parent.mkdir();path.write_bytes(b'x')
        with tempfile.TemporaryDirectory() as outside:
            try:(self.root/module.BASE).symlink_to(outside,target_is_directory=True)
            except OSError:self.skipTest('platform cannot create symlinks; Linux CI must exercise this test')
            with self.assertRaises(ValueError):module.prepare_bundle(self.root,{'data/input':hashlib.sha256(b'x').hexdigest()},NOW)
            self.assertEqual(list(Path(outside).iterdir()),[])
    def test_latest_symlink_cannot_overwrite_outside_file(self):
        destination=self.root/module.BASE/'latest.json';destination.parent.mkdir(parents=True)
        with tempfile.TemporaryDirectory() as outside:
            victim=Path(outside)/'victim';victim.write_bytes(b'keep')
            try:destination.symlink_to(victim)
            except OSError:self.skipTest('platform cannot create symlinks; Linux CI must exercise this test')
            with self.assertRaises(ValueError):module.write_latest(self.root,{'status':'NOT_CONFIGURED'})
            self.assertEqual(victim.read_bytes(),b'keep')

class S3Client:
    def __init__(self,body=b'proof',mode='COMPLIANCE',version='v1',owner='222222222222',account='111111111111',duration=90):
        self.body=body;self.mode=mode;self.version=version;self.owner=owner;self.account=account;self.duration=duration;self.calls=[]
    def get_caller_identity(self):return {'Account':self.account}
    def get_object_lock_configuration(self,**kw):
        if kw['ExpectedBucketOwner']!=self.owner:raise ValueError('wrong owner')
        return {'ObjectLockConfiguration':{'ObjectLockEnabled':'Enabled'}}
    def get_bucket_versioning(self,**kw):return {'Status':'Enabled'}
    def get_object_retention(self,**kw):return {'Retention':{'Mode':self.mode,'RetainUntilDate':NOW+timedelta(days=self.duration)}}
    def get_object(self,**kw):
        self.calls.append(kw)
        return {'Body':io.BytesIO(self.body),'VersionId':self.version,'LastModified':NOW-timedelta(seconds=1)}

class ExternalTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(module,'PIT/retention preparation module missing')
        self.config={'provider':'aws_s3_object_lock','bucket':'evidence','key':'bundles/a.zip','version_id':'v1','bucket_owner_account_id':'222222222222','collector_account_id':'111111111111','minimum_retention_days':90}
        self.sha=hashlib.sha256(b'proof').hexdigest()
    def check(self,client,config=None):return module.verify_external(config or self.config,self.sha,NOW,client=client,identity_client=client,target_at=NOW+timedelta(hours=1))
    def test_pinned_readback_compliance_duration_and_distinct_owner_required(self):
        client=S3Client();r=self.check(client)
        self.assertTrue(r['independent_retention_verified']);self.assertTrue(r['pre_target_retention_verified'])
        self.assertEqual(client.calls[0]['VersionId'],'v1');self.assertEqual(client.calls[0]['ExpectedBucketOwner'],'222222222222')
        self.assertFalse(r['historical_first_publication_verified'])
    def test_wrong_hash_short_governance_version_owner_and_identity_fail(self):
        for client in (S3Client(body=b'forged'),S3Client(mode='GOVERNANCE'),S3Client(version='null'),S3Client(owner='333333333333'),S3Client(account='333333333333'),S3Client(duration=89)):
            self.assertFalse(self.check(client)['independent_retention_verified'])
    def test_same_account_invalid_duration_and_null_version_fail_before_network(self):
        for change in ({'bucket_owner_account_id':'111111111111'},{'minimum_retention_days':0},{'minimum_retention_days':True},{'version_id':'null'}):
            config=dict(self.config,**change);self.assertFalse(self.check(object(),config)['independent_retention_verified'])
    def test_late_timestamp_is_not_backdated_to_pre_target_evidence(self):
        r=module.verify_external(self.config,self.sha,NOW,client=S3Client(),identity_client=S3Client(),target_at=NOW-timedelta(hours=1))
        self.assertTrue(r['independent_retention_verified']);self.assertFalse(r['pre_target_retention_verified'])
    def test_object_existing_before_target_does_not_prove_a_later_lock_existed(self):
        r=module.verify_external(self.config,self.sha,NOW,client=S3Client(),identity_client=S3Client(),target_at=NOW-timedelta(microseconds=1))
        self.assertTrue(r.get('pre_target_package_existence_verified'))
        self.assertFalse(r['pre_target_retention_verified'])
    def test_invalid_target_clock_never_leaves_partial_success(self):
        r=module.verify_external(self.config,self.sha,NOW,client=S3Client(),identity_client=S3Client(),target_at='invalid')
        self.assertFalse(r['independent_retention_verified']);self.assertEqual(r['status'],'NOT_VERIFIED')
    def test_denial_is_not_treated_as_success(self):
        class Denied(S3Client):
            def get_object_retention(self,**kw):raise PermissionError('not permitted')
        self.assertEqual(self.check(Denied())['status'],'NOT_VERIFIED')

if __name__=='__main__':unittest.main()
