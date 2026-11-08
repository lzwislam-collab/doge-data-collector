import io, unittest, hashlib, zipfile, tempfile, json
from unittest.mock import patch
from pathlib import Path
from types import SimpleNamespace as NS
from datetime import datetime, timezone
import oss_inventory_retention as m
NOW=datetime(2026,10,15,5,10,tzinfo=timezone.utc)

class Missing(Exception):
    status=404

class Bucket:
    def __init__(self):
        self.state='Locked'; self.owner=m.OWNER; self.versioning='Enabled'; self.days=730
        self.data={}; self.writes=0; self.return_version='v1'; self.corrupt=False
    def get_bucket_info(self):
        return NS(owner=NS(id=self.owner),location='oss-cn-hangzhou',acl=NS(grant='private'))
    def get_bucket_versioning(self):return NS(status=self.versioning)
    def get_bucket_worm(self):return NS(state=self.state,worm_state=None,worm_id='lock1',retention_period_days=self.days,creation_date='2026-10-15T05:00:00Z')
    def head_object(self,key,**kw):
        if key not in self.data:raise Missing()
        return NS(headers={'x-oss-version-id':'v1'})
    def put_object(self,key,raw,headers):
        assert headers['x-oss-forbid-overwrite']=='true'
        self.writes+=1; self.data[key]=raw
        return NS(headers={'x-oss-version-id':'v1'})
    def get_object(self,key,params):
        assert params=={'versionId':'v1'}
        raw=self.data[key]+(b'bad' if self.corrupt else b'')
        body=io.BytesIO(raw)
        return NS(headers={'x-oss-version-id':self.return_version,'date':'Thu, 15 Oct 2026 05:10:00 GMT','x-oss-server-side-encryption':'AES256'},
                  last_modified=1792040520,read=body.read,close=body.close,request_id='request1')

class Tests(unittest.TestCase):
    def test_locked_owner_and_versioning_required(self):
        for field,bad in [('state','InProgress'),('owner','other'),('versioning','Suspended'),('days',729)]:
            b=Bucket();setattr(b,field,bad)
            with self.subTest(field=field),self.assertRaises(ValueError):m.check_bucket(b)
    def test_upload_pins_version_and_reads_every_byte(self):
        b=Bucket();lock=m.check_bucket(b);r=m.upload_one(b,b'proof','.zip',lock,now=NOW)
        self.assertEqual(r['readback_sha256'],hashlib.sha256(b'proof').hexdigest())
        self.assertEqual(r['version_id'],'v1');self.assertEqual(b.writes,1)
        self.assertFalse(r['independent_custodian_verified'])
    def test_existing_bytes_not_uploaded_again(self):
        b=Bucket();lock=m.check_bucket(b)
        m.upload_one(b,b'proof','.zip',lock,now=NOW);r=m.upload_one(b,b'proof','.zip',lock,now=NOW)
        self.assertEqual(b.writes,1);self.assertFalse(r['upload_performed'])
    def test_wrong_version_or_corrupt_bytes_rejected(self):
        for field,bad in [('return_version','v2'),('corrupt',True)]:
            b=Bucket();setattr(b,field,bad)
            with self.subTest(field=field),self.assertRaises(ValueError):m.upload_one(b,b'proof','.zip',m.check_bucket(b),now=NOW)
    def test_expired_protection_rejected(self):
        b=Bucket()
        with self.assertRaises(ValueError):m.upload_one(b,b'proof','.zip',m.check_bucket(b),now=datetime(2030,1,1,tzinfo=timezone.utc))
    def test_bad_extension_rejected(self):
        b=Bucket()
        with self.assertRaises(ValueError):m.upload_one(b,b'proof','/../../bad',m.check_bucket(b))
    def test_artifact_path_escape_rejected(self):
        stream=io.BytesIO()
        with zipfile.ZipFile(stream,'w') as z:z.writestr('../escape',b'bad')
        with tempfile.TemporaryDirectory() as p,self.assertRaises(ValueError):m.extract_artifact(stream.getvalue(),Path(p))
    def test_duplicate_archive_paths_rejected(self):
        stream=io.BytesIO()
        with zipfile.ZipFile(stream,'w') as z:
            z.writestr('latest.json',b'1');z.writestr('latest.json',b'2')
        with tempfile.TemporaryDirectory() as p,self.assertRaises(ValueError):m.extract_artifact(stream.getvalue(),Path(p))

class PublishedInventoryTests(unittest.TestCase):
    """Published artifacts and a newer pending preparation are different generations."""
    PUBLISHED='4aaf7b0c06df0eb0d54e1e00cf8666681df3ebe680db3356ec246437500a4b58'
    PENDING='21ea66cb74ea7688c13483c5c52460442624fe2f4e04ee1d517c6d1956aa00a9'

    def fixture(self,root,matched=False,receipt=True):
        base=root/'data/pit_retention_preparation_v1';base.mkdir(parents=True)
        prior={'status':'NOT_CONFIGURED','bundle_sha256':self.PUBLISHED if matched else self.PENDING,
               'run_id':'41285657738' if matched else '42391088937',
               'prepared_at_utc':'2026-11-01T09:31:31Z' if matched else '2026-11-07T17:24:27Z'}
        marker={'status':'GITHUB_ARTIFACT_UPLOAD_RECORDED_NOT_WORM',
                'bundle_sha256':self.PUBLISHED,'run_id':'41285657738',
                'artifact_id':'13078152229','artifact_archive_sha256':'b'*64,
                'recorded_at_utc':'2026-11-01T09:48:11Z'}
        (base/'latest.json').write_text(json.dumps(prior))
        (base/'artifact_publication.json').write_text(json.dumps(marker))
        if receipt:
            saved=root/'audit/oss_retention'/(self.PUBLISHED+'.json');saved.parent.mkdir(parents=True)
            saved.write_text(json.dumps({'status':'LOCKED_DAILY_INVENTORY_VERIFIED','bundle_sha256':self.PUBLISHED,
                                        'artifact_id':13078152229,'source_run_id':41285657738,
                                        'artifact_digest':'sha256:'+'b'*64}))
        return base,prior,marker

    def published_artifact(self,root,marker,evidence=b'synthetic test evidence',prepared='2026-11-01T09:31:31Z'):
        inner=io.BytesIO()
        with zipfile.ZipFile(inner,'w') as archive:archive.writestr('evidence.txt',evidence)
        bundle=inner.getvalue();sha=hashlib.sha256(bundle).hexdigest()
        published={'bundle_sha256':sha,'bundle_path':'data/pit_retention_preparation_v1/bundles/'+sha+'.zip',
                   'objects':1,'prepared_at_utc':prepared,'run_id':marker['run_id']}
        outer=io.BytesIO()
        with zipfile.ZipFile(outer,'w') as archive:
            archive.writestr('latest.json',json.dumps(published));archive.writestr('bundles/'+sha+'.zip',bundle)
        raw=outer.getvalue();digest=hashlib.sha256(raw).hexdigest()
        marker.update(bundle_sha256=sha,artifact_archive_sha256=digest)
        (root/'data/pit_retention_preparation_v1/artifact_publication.json').write_text(json.dumps(marker))
        artifact={'id':int(marker['artifact_id']),'size_in_bytes':len(raw),'digest':'sha256:'+digest,
                  'expired':False,'workflow_run':{'id':int(marker['run_id'])}}
        run={'id':int(marker['run_id']),'head_branch':'main','event':'schedule','head_sha':'c'*40,
             'repository':{'full_name':'lzwislam-collab/doge-data-collector'},
             'path':'.github/workflows/okx_hourly_extreme_history.yml',
             'html_url':'https://github.com/lzwislam-collab/doge-data-collector/actions/runs/'+marker['run_id']}
        replies={'/actions/artifacts/'+marker['artifact_id']:json.dumps(artifact).encode(),
                 '/actions/runs/'+marker['run_id']:json.dumps(run).encode(),
                 '/actions/artifacts/'+marker['artifact_id']+'/zip':raw}
        return published,bundle,replies

    def test_new_preparation_preserves_verified_published_inventory_and_pending_state(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);base,prior,marker=self.fixture(root)
            before={p:p.read_bytes() for p in root.rglob('*.json')}
            with patch.object(m,'github_response',side_effect=AssertionError('unexpected network')):
                try:ready=m.stage(root)
                except ValueError as exc:self.fail('A newer unpublished preparation blocked the verified published inventory: '+str(exc))
                self.assertFalse(ready)
            status=json.loads((root/'oss-retention-output/status.json').read_bytes())
            self.assertEqual(status['status'],'WAIT_NEW_INVENTORY_PUBLICATION')
            self.assertEqual(status['bundle_sha256'],self.PUBLISHED)
            self.assertEqual(status['current_preparation_bundle_sha256'],self.PENDING)
            self.assertFalse(status['current_preparation_artifact_published'])
            self.assertFalse(status['fresh_object_readback_performed'])
            self.assertEqual({p:p.read_bytes() for p in before},before)

    def test_matching_verified_inventory_remains_idempotent(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);self.fixture(root,matched=True)
            # Keep the pre-existing same-generation receipt compatibility.
            receipt=root/'audit/oss_retention'/(self.PUBLISHED+'.json')
            receipt.write_text(json.dumps({'status':'LOCKED_DAILY_INVENTORY_VERIFIED','bundle_sha256':self.PUBLISHED}))
            with patch.object(m,'github_response',side_effect=AssertionError('unexpected network')):
                self.assertFalse(m.stage(root))
            status=json.loads((root/'oss-retention-output/status.json').read_bytes())
            self.assertEqual(status['status'],'SKIP_PREVIOUSLY_VERIFIED_INVENTORY')
            self.assertFalse(status['fresh_object_readback_performed'])

    def test_unproven_preparation_generation_is_still_rejected(self):
        for change in ({'run_id':'41285657738'}, {'prepared_at_utc':'2026-10-31T00:00:00Z'},
                       {'bundle_sha256':'invalid'}, {'prepared_at_utc':'2099-01-01T00:00:00Z'},
                       {'prepared_at_utc':'2026-11-07T17:24:27'}, {'run_id':'041285657738'},
                       {'status':'BLOCKED'}):
            with self.subTest(change=change),tempfile.TemporaryDirectory() as temp:
                root=Path(temp);base,prior,_=self.fixture(root);prior.update(change)
                (base/'latest.json').write_text(json.dumps(prior))
                with patch.object(m,'github_response',side_effect=AssertionError('unexpected network')):
                    with self.assertRaisesRegex(ValueError,'publication binding mismatch'):m.stage(root)

    def test_pending_generation_never_uses_an_unconfirmed_publication_marker(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);base,_,marker=self.fixture(root);marker['status']='PENDING'
            (base/'artifact_publication.json').write_text(json.dumps(marker))
            with patch.object(m,'github_response',side_effect=AssertionError('unexpected network')):
                with self.assertRaisesRegex(ValueError,'publication binding mismatch'):m.stage(root)

    def test_pending_generation_rejects_damaged_or_changed_marker_source(self):
        cases=[('artifact_id',None),('artifact_id','broken'),('artifact_id',True),
               ('artifact_id','0'),('artifact_id','13078152230'),('artifact_id',13078152229.0),
               ('run_id',None),('run_id','broken'),('run_id','41285657739'),
               ('artifact_archive_sha256',None),('artifact_archive_sha256','not-a-sha256'),
               ('artifact_archive_sha256','a'*64)]
        for field,value in cases:
            with self.subTest(field=field,value=value),tempfile.TemporaryDirectory() as temp:
                root=Path(temp);base,_,marker=self.fixture(root)
                if value is None:marker.pop(field)
                else:marker[field]=value
                (base/'artifact_publication.json').write_text(json.dumps(marker))
                with patch.object(m,'github_response',side_effect=AssertionError('unexpected network')):
                    with self.assertRaises(ValueError):m.stage(root)
                self.assertFalse((root/'oss-retention-output/status.json').exists())

    def test_pending_generation_requires_complete_matching_receipt_source(self):
        cases=[('artifact_id',None),('source_run_id',None),('artifact_digest',None),
               ('artifact_id','broken'),('artifact_id',13078152230),('artifact_id',True),
               ('source_run_id',41285657739),('source_run_id',41285657738.0),
               ('artifact_digest','sha256:invalid'),('artifact_digest','sha256:'+'a'*64),
               ('artifact_digest','sha512:'+'b'*64),('status','FAIL'),('bundle_sha256','a'*64)]
        for field,value in cases:
            with self.subTest(field=field,value=value),tempfile.TemporaryDirectory() as temp:
                root=Path(temp);self.fixture(root)
                receipt=root/'audit/oss_retention'/(self.PUBLISHED+'.json')
                old=json.loads(receipt.read_bytes())
                if value is None:old.pop(field)
                else:old[field]=value
                receipt.write_text(json.dumps(old));before=receipt.read_bytes()
                with patch.object(m,'github_response',side_effect=AssertionError('unexpected network')):
                    with self.assertRaises(ValueError):m.stage(root)
                self.assertFalse((root/'oss-retention-output/status.json').exists())
                self.assertEqual(receipt.read_bytes(),before)

    def test_delayed_previous_publication_remains_rejected(self):
        # recorded_at is the publication time, not preparation time. Without
        # another ordering proof, a delayed old publication still fails closed.
        for recorded in ('2026-11-07T17:24:27Z','2026-11-07T18:00:00Z','2099-01-01T00:00:00Z'):
            with self.subTest(recorded=recorded),tempfile.TemporaryDirectory() as temp:
                root=Path(temp);base,_,marker=self.fixture(root);marker['recorded_at_utc']=recorded
                (base/'artifact_publication.json').write_text(json.dumps(marker))
                with patch.object(m,'github_response',side_effect=AssertionError('unexpected network')):
                    with self.assertRaisesRegex(ValueError,'publication binding mismatch'):m.stage(root)

    def test_newly_published_bundle_is_staged_instead_of_reusing_old_receipt(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);base,prior,marker=self.fixture(root)
            receipt=root/'audit/oss_retention'/(self.PUBLISHED+'.json');before=receipt.read_bytes()
            with patch.object(m,'github_response',side_effect=AssertionError('unexpected network')):
                self.assertFalse(m.stage(root))
            marker.update(artifact_id='14000000001',run_id=prior['run_id'],recorded_at_utc='2026-11-07T17:30:00Z')
            published,bundle,replies=self.published_artifact(root,marker,b'newly published evidence',prior['prepared_at_utc'])
            prior['bundle_sha256']=published['bundle_sha256']
            (base/'latest.json').write_text(json.dumps(prior))
            with patch.object(m,'github_response',side_effect=lambda path:io.BytesIO(replies[path])), \
                 patch.object(m.subprocess,'check_output',return_value='c'*40+'\n'):
                self.assertTrue(m.stage(root))
            source=json.loads((root/'oss-retention-output/source.json').read_bytes())
            self.assertEqual(source['bundle_sha256'],published['bundle_sha256'])
            self.assertEqual(source['artifact_id'],14000000001)
            self.assertEqual(source['source_run_id'],42391088937)
            self.assertEqual(source['artifact_digest'],'sha256:'+marker['artifact_archive_sha256'])
            self.assertTrue(source['current_preparation_artifact_published'])
            self.assertEqual((root/'oss-inventory-stage'/published['bundle_path']).read_bytes(),bundle)
            self.assertEqual(receipt.read_bytes(),before)

    def test_unretained_published_artifact_is_verified_independently_of_pending_latest(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);base,prior,marker=self.fixture(root,receipt=False)
            published,bundle,replies=self.published_artifact(root,marker)
            with patch.object(m,'github_response',side_effect=lambda path:io.BytesIO(replies[path])), \
                 patch.object(m.subprocess,'check_output',return_value='c'*40+'\n'):
                try:ready=m.stage(root)
                except ValueError as exc:self.fail('The exact published artifact could not be staged: '+str(exc))
                self.assertTrue(ready)
            source=json.loads((root/'oss-retention-output/source.json').read_bytes())
            self.assertEqual(source['bundle_sha256'],published['bundle_sha256'])
            self.assertEqual(source['current_preparation_bundle_sha256'],self.PENDING)
            self.assertFalse(source['current_preparation_artifact_published'])
            self.assertEqual((root/'oss-inventory-stage'/published['bundle_path']).read_bytes(),bundle)
            self.assertEqual(json.loads((base/'latest.json').read_bytes()),prior)

if __name__=='__main__':unittest.main()
