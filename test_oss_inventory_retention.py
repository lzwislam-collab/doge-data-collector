import io, unittest, hashlib, zipfile, tempfile
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
    def get_bucket_worm(self):return NS(worm_state=self.state,worm_id='lock1',retention_period_days=self.days,creation_date='2026-10-15T05:00:00Z')
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

if __name__=='__main__':unittest.main()
