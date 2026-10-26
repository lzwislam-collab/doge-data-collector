"""Offline source-binding regressions. Local fixture is NOT a published receipt."""
import copy,json,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import oss_inventory_retention as m
inputs=json.loads("{\"artifact\":{\"id\":12623035164,\"name\":\"doge-pit-preparation-40248370100-1\",\"size_in_bytes\":1511705224,\"url\":\"https://api.github.com/repos/lzwislam-collab/doge-data-collector/actions/artifacts/12623035164\",\"archive_download_url\":\"https://api.github.com/repos/lzwislam-collab/doge-data-collector/actions/artifacts/12623035164/zip\",\"expired\":false,\"created_at\":\"2026-10-26T01:48:57Z\",\"expires_at\":\"2027-01-24T00:14:19Z\",\"updated_at\":\"2026-10-26T01:48:57Z\",\"digest\":\"sha256:3069ca9212394a30825a5d6eca02da2ee40f1848dc3d0a7b3960cb157e62c114\",\"workflow_run\":{\"id\":40248370100,\"repository_id\":1412901178,\"head_repository_id\":1412901178,\"head_branch\":\"main\",\"head_sha\":\"938cb65765475c13479188f688c995efd0c77a67\"}},\"run\":{\"id\":40248370100,\"head_sha\":\"938cb65765475c13479188f688c995efd0c77a67\",\"head_branch\":\"main\",\"event\":\"schedule\",\"path\":\".github/workflows/okx_hourly_extreme_history.yml\",\"repository\":{\"full_name\":\"lzwislam-collab/doge-data-collector\"},\"status\":\"completed\",\"conclusion\":\"cancelled\"},\"binding_fixture\":{\"artifact_id\":\"12623035164\",\"run_id\":\"40248370100\",\"artifact_archive_sha256\":\"3069ca9212394a30825a5d6eca02da2ee40f1848dc3d0a7b3960cb157e62c114\",\"bundle_sha256\":\"00a29b3d897d2e178d7d7fbd6ef901dcdea0eea4711b8a2b1fa0bea2fec4f7d3\",\"implementation_sha256\":\"7180d1842723960c1ca079eae6d7cf64cde6c654e44321fd5637875e2c3ef1b7\"},\"remote_latest\":{\"status\":\"NOT_CONFIGURED\",\"independent_retention_verified\":false,\"bundle_path\":\"data/pit_retention_preparation_v1/bundles/00a29b3d897d2e178d7d7fbd6ef901dcdea0eea4711b8a2b1fa0bea2fec4f7d3.index.json\",\"bundle_sha256\":\"00a29b3d897d2e178d7d7fbd6ef901dcdea0eea4711b8a2b1fa0bea2fec4f7d3\",\"byte_count\":1401898,\"objects\":7996,\"prepared_at_utc\":\"2026-10-26T01:36:52.890683+00:00\",\"upload_performed\":false,\"storage_provisioned\":false,\"credentials_required_in_chat\":false,\"bundle_kind\":\"SHARDED_INVENTORY_INDEX\",\"part_count\":29,\"all_part_versions_and_retention_required\":true,\"source_ledger_rows\":9744,\"receipt_fresh_asof_rows\":0,\"receipt_freshness_hours\":48,\"source_observation_age_limits_verified\":false,\"model_use_authorized\":false,\"historical_first_publication_verified\":false,\"retention_export_path\":\"data/plus_evidence_v1_0/sources/retention_exports/e7ec1cc847d947fdb26687ef7721ac77.json\",\"observed_at_utc\":\"2026-10-26T01:36:52.890683+00:00\",\"generated_new_bundle\":true,\"implementation_sha256\":\"7180d1842723960c1ca079eae6d7cf64cde6c654e44321fd5637875e2c3ef1b7\",\"source_manifest_path\":\"data/plus_evidence_v1_0/sources/manifests/20261026T013449351533Z_bffc2ad7681c445e92b6e5ad4df07a1f.json\",\"source_manifest_sha256\":\"572449ec649887364d17743a75c1d327da0eb019632d04a76436c9b74a66250b\",\"run_id\":\"40248370100\",\"run_attempt\":\"1\",\"scope\":\"once-per-UTC-day inventory package, not every later forecast protected\"},\"remote_published_marker\":{\"status\":\"GITHUB_ARTIFACT_UPLOAD_RECORDED_NOT_WORM\",\"recorded_at_utc\":\"2026-10-25T01:25:25.559518+00:00\",\"artifact_id\":\"12581914557\",\"artifact_archive_sha256\":\"d6974f86bfffe7365e6d43f90e6b9cfea194c380acf493100c3959084c48a1d2\",\"run_id\":\"40149442049\",\"bundle_sha256\":\"d67d384240449d462447e8dddfc99c6757b1f1d167f3953fe87e2ebd97593e9d\",\"implementation_sha256\":\"7180d1842723960c1ca079eae6d7cf64cde6c654e44321fd5637875e2c3ef1b7\",\"independent_retention_verified\":false,\"remote_bundle_content_readback_verified\":false,\"confirmation_source\":\"successful actions/upload-artifact@v4 outputs; not a WORM claim\"},\"fixture_note\":\"LOCAL DIAGNOSTIC FIXTURE ONLY: actual upload outputs+preparation metadata; no original completed marker in producer log, no remote publication claim\"}")
class TransferBoundary(Exception):pass
class RealSourceBinding(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.stage=Path(self.tmp.name)
  self.base=self.stage/'data/pit_retention_preparation_v1';self.base.mkdir(parents=True)
  (self.base/'latest.json').write_text(json.dumps(inputs['remote_latest']))
  (self.base/'artifact_publication.json').write_text(json.dumps(inputs['binding_fixture']))
 def invoke(self,artifact=None,run=None):
  artifact=artifact or inputs['artifact'];run=run or inputs['run']
  def api(path):
   if path=='/actions/artifacts/12623035164':return artifact
   if path=='/actions/runs/40248370100':return run
   raise AssertionError('unexpected request '+path)
  with patch.object(m,'github',side_effect=api),patch.object(m,'download_artifact',side_effect=TransferBoundary) as transfer:
   try:m.stage(self.stage)
   finally:self.transfer_calls=transfer.call_count
 def test_actual_metadata_and_preparation_fixture_reach_transfer_boundary(self):
  with self.assertRaises(TransferBoundary):self.invoke()
  self.assertEqual(self.transfer_calls,1)
 def test_current_remote_marker_mismatch_rejects_before_any_api(self):
  (self.base/'artifact_publication.json').write_text(json.dumps(inputs['remote_published_marker']))
  with patch.object(m,'github',side_effect=AssertionError('API before marker guard')):
   with self.assertRaisesRegex(ValueError,'publication binding mismatch'):m.stage(self.stage)
 def test_wrong_embedded_run_rejected(self):
  a=copy.deepcopy(inputs['artifact']);a['workflow_run']['id']+=1
  with self.assertRaises(ValueError):self.invoke(artifact=a)
  self.assertEqual(self.transfer_calls,0)
 def test_wrong_source_commit_rejected(self):
  a=copy.deepcopy(inputs['artifact']);a['workflow_run']['head_sha']='b'*40
  with self.assertRaises(ValueError):self.invoke(artifact=a)
  self.assertEqual(self.transfer_calls,0)
 def test_archive_digest_mismatch_rejected(self):
  a=copy.deepcopy(inputs['artifact']);a['digest']='sha256:'+'b'*64
  with self.assertRaises(ValueError):self.invoke(artifact=a)
  self.assertEqual(self.transfer_calls,0)
 def test_expired_artifact_rejected(self):
  a=copy.deepcopy(inputs['artifact']);a['expired']=True
  with self.assertRaises(ValueError):self.invoke(artifact=a)
  self.assertEqual(self.transfer_calls,0)
 def test_wrong_repository_rejected(self):
  r=copy.deepcopy(inputs['run']);r['repository']['full_name']='other/repo'
  with self.assertRaises(ValueError):self.invoke(run=r)
  self.assertEqual(self.transfer_calls,0)
 def test_pull_request_target_rejected(self):
  r=copy.deepcopy(inputs['run']);r['event']='pull_request_target'
  with self.assertRaises(ValueError):self.invoke(run=r)
  self.assertEqual(self.transfer_calls,0)
 def test_prior7973_inventory_receipt_cannot_cover_new7996_inventory(self):
  prior=dict(inputs['binding_fixture'],artifact_id='12620730069',run_id='40241638129',bundle_sha256='cbb65e4850a8e30289d12b8eea79b4f6e1068dc9dcd0ea3f56030e05de944c56')
  (self.base/'artifact_publication.json').write_text(json.dumps(prior))
  with patch.object(m,'github',side_effect=AssertionError('API before wrong-inventory rejection')):
   with self.assertRaisesRegex(ValueError,'publication binding mismatch'):m.stage(self.stage)

if __name__=='__main__':unittest.main()


