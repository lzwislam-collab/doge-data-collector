"""Small offline regressions prepared for next-day PR75 acceptance."""
import io,json,os,sys,subprocess,tempfile,unittest,zipfile,struct
from pathlib import Path
from unittest.mock import patch
import oss_inventory_retention as m
from test_inventory_transactions import fixture

class FailureCleanup(unittest.TestCase):
 def test_failed_unlink_does_not_prevent_cleaning_other_owned_files(self):
  stream=io.BytesIO()
  with zipfile.ZipFile(stream,'w') as z:z.writestr('a.json',b'first');z.writestr('b.json',b'second')
  raw=stream.getvalue().replace(b'second',b'SECOND',1)
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);original=Path.unlink
   def unlink(path,*a,**k):
    if path.name=='b.json':raise PermissionError('simulated locked file')
    return original(path,*a,**k)
   with patch.object(Path,'unlink',unlink):
    with self.assertRaises(BaseExceptionGroup) as error:m.extract_artifact(raw,root)
   self.assertFalse((root/'a.json').exists());self.assertTrue((root/'b.json').exists())
   self.assertTrue(any(isinstance(e,zipfile.BadZipFile) for e in error.exception.exceptions))
   self.assertTrue(any(isinstance(e,PermissionError) for e in error.exception.exceptions))

class ZipFlags(unittest.TestCase):
 def test_special_inner_flags_rejected_before_read(self):
  for flag in (0x20,0x40):
   with self.subTest(flag=flag),tempfile.TemporaryDirectory() as tmp:
    stream=io.BytesIO()
    with zipfile.ZipFile(stream,'w') as z:z.writestr('bundle_manifest.json',b'{}')
    raw=bytearray(stream.getvalue());central=raw.index(bytes.fromhex('504b0102'))
    struct.pack_into('<H',raw,central+8,struct.unpack_from('<H',raw,central+8)[0]|flag)
    (Path(tmp)/'part.zip').write_bytes(raw)
    with self.assertRaisesRegex(ValueError,'flags|encryption'):m.validate_staged_packages(tmp)

class SourceBindings(unittest.TestCase):
 def test_mismatched_returned_ids_reject_before_download(self):
  for which in ('artifact','run'):
   with self.subTest(which=which),tempfile.TemporaryDirectory() as tmp:
    root=Path(tmp);_,get,_=fixture(root)
    def api(path):
     row=dict(get(path))
     if ('/artifacts/' in path)==(which=='artifact'):row['id']=999
     return row
    with patch.object(m,'github',side_effect=api),patch.object(m,'download_artifact',side_effect=AssertionError('download before locator validation')) as download:
     with self.assertRaisesRegex(ValueError,'provenance|identity'):m.stage(root)
     download.assert_not_called()

class EvidenceOutputs(unittest.TestCase):
 def test_existing_output_is_rejected_before_stress_and_preserved(self):
  for module in ('offline_inventory_boundary','offline_inventory_stress'):
   with self.subTest(module=module),tempfile.TemporaryDirectory() as tmp:
    path=Path(tmp)/'old-success.json';path.write_bytes(b'{"previous_success":true}')
    code="import sys;from unittest.mock import patch;import "+module+" as m;sys.argv=['tool','--output',"+repr(str(path))+"];\nwith patch.object(m,'stress',side_effect=RuntimeError('stress must not run')):\n try:m.main()\n except FileExistsError:raise SystemExit(0)\nraise SystemExit(3)"
    result=subprocess.run([sys.executable,'-B','-O','-c',code],capture_output=True,text=True,encoding='utf-8',cwd=Path(__file__).parent)
    self.assertEqual(result.returncode,0,result.stdout+result.stderr)
    self.assertEqual(path.read_bytes(),b'{"previous_success":true}')

class PreparedClosureMatrix(unittest.TestCase):
 def test_cancel_and_cleanup_failure_are_both_retained(self):
  with tempfile.TemporaryDirectory() as tmp:
   stream=io.BytesIO()
   with zipfile.ZipFile(stream,'w') as z:z.writestr('a.json',b'{}')
   with patch.object(zipfile.ZipExtFile,'read',side_effect=KeyboardInterrupt()),patch.object(Path,'unlink',side_effect=PermissionError('locked')):
    with self.assertRaises(BaseExceptionGroup) as error:m.extract_artifact(stream.getvalue(),tmp)
   self.assertTrue(any(isinstance(e,KeyboardInterrupt) for e in error.exception.exceptions))
   self.assertTrue(any(isinstance(e,PermissionError) for e in error.exception.exceptions))
 def test_failed_directory_cleanup_does_not_skip_other_directories(self):
  stream=io.BytesIO()
  with zipfile.ZipFile(stream,'w') as z:z.writestr('first/a.json',b'one');z.writestr('second/b.json',b'two')
  raw=stream.getvalue().replace(b'two',b'TWO',1)
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);original=Path.rmdir
   def rmdir(path,*a,**k):
    if path.name=='second':raise PermissionError('locked directory')
    return original(path,*a,**k)
   with patch.object(Path,'rmdir',rmdir):
    with self.assertRaises(BaseExceptionGroup):m.extract_artifact(raw,root)
   self.assertFalse((root/'first').exists());self.assertTrue((root/'second').exists())
 def test_outer_special_flags_reject_before_writes(self):
  for flag in (0x20,0x40):
   with self.subTest(flag=flag),tempfile.TemporaryDirectory() as tmp:
    stream=io.BytesIO()
    with zipfile.ZipFile(stream,'w') as z:z.writestr('a.json',b'{}')
    raw=bytearray(stream.getvalue());central=raw.index(bytes.fromhex('504b0102'))
    struct.pack_into('<H',raw,central+8,struct.unpack_from('<H',raw,central+8)[0]|flag)
    with self.assertRaises(ValueError):m.extract_artifact(bytes(raw),tmp)
    self.assertEqual(list(Path(tmp).iterdir()),[])
 def test_invalid_identity_types_and_unknown_expiry_reject(self):
  for field,value in (('id',True),('id',1.0),('expired',None),('expired',0)):
   with self.subTest(field=field,value=value),tempfile.TemporaryDirectory() as tmp:
    root=Path(tmp);_,get,_=fixture(root)
    def api(path):
     row=dict(get(path))
     if '/artifacts/' in path:row[field]=value
     return row
    with patch.object(m,'github',side_effect=api),patch.object(m,'download_artifact') as download:
     with self.assertRaises(ValueError):m.stage(root)
     download.assert_not_called()
 def test_evidence_write_is_exclusive_and_complete(self):
  from offline_inventory_stress import write_evidence_json
  with tempfile.TemporaryDirectory() as tmp:
   path=Path(tmp)/'result.json';write_evidence_json(path,{'verified':True});before=path.read_bytes()
   self.assertTrue(json.loads(before)['verified'])
   with self.assertRaises(FileExistsError):write_evidence_json(path,{'verified':False})
   self.assertEqual(path.read_bytes(),before)
 def test_failed_evidence_fsync_removes_only_new_output(self):
  import offline_inventory_stress as tool
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);previous=root/'previous.json';previous.write_bytes(b'old');output=root/'new.json'
   with patch.object(tool.os,'fsync',side_effect=OSError('write failure')):
    with self.assertRaises(OSError):tool.write_evidence_json(output,{'verified':True})
   self.assertFalse(output.exists());self.assertEqual(previous.read_bytes(),b'old')
 def test_output_created_between_preflight_and_publication_is_preserved(self):
  from offline_inventory_stress import evidence_destination,write_evidence_json
  with tempfile.TemporaryDirectory() as tmp:
   path=evidence_destination(Path(tmp)/'result.json');path.write_bytes(b'other operation')
   with self.assertRaises(FileExistsError):write_evidence_json(path,{'verified':True})
   self.assertEqual(path.read_bytes(),b'other operation')

class EvidencePublicationFailure(unittest.TestCase):
 def test_failed_fsync_and_cleanup_never_publish_success_json(self):
  import offline_inventory_stress as tool
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);output=root/'success.json'
   with patch.object(tool.os,'fsync',side_effect=OSError('fsync failed')),patch.object(Path,'unlink',side_effect=PermissionError('cleanup failed')):
    with self.assertRaises(BaseExceptionGroup) as error:tool.write_evidence_json(output,{'verified':True})
   self.assertFalse(output.exists())
   self.assertTrue(any(isinstance(e,OSError) for e in error.exception.exceptions))
   self.assertTrue(any(isinstance(e,PermissionError) for e in error.exception.exceptions))
 def test_publication_race_preserves_foreign_result_and_cleans_private_temp(self):
  import offline_inventory_stress as tool
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);output=root/'result.json'
   def link(source,destination):
    Path(destination).write_bytes(b'other result');raise FileExistsError('race')
   with patch.object(tool.os,'link',side_effect=link):
    with self.assertRaises(FileExistsError):tool.write_evidence_json(output,{'verified':True})
   self.assertEqual(output.read_bytes(),b'other result');self.assertEqual(list(root.iterdir()),[output])

if __name__=='__main__':unittest.main()
