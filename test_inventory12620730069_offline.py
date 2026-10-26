"""Offline source-binding regressions using real metadata and log receipt."""
import copy,json,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import oss_inventory_retention as m
inputs=json.loads("{\"artifact\":{\"id\":12620730069,\"name\":\"doge-pit-preparation-40241638129-1\",\"size_in_bytes\":1505256749,\"url\":\"https://api.github.com/repos/lzwislam-collab/doge-data-collector/actions/artifacts/12620730069\",\"archive_download_url\":\"https://api.github.com/repos/lzwislam-collab/doge-data-collector/actions/artifacts/12620730069/zip\",\"expired\":false,\"created_at\":\"2026-10-26T00:42:13Z\",\"expires_at\":\"2027-01-23T22:44:59Z\",\"updated_at\":\"2026-10-26T00:42:13Z\",\"digest\":\"sha256:083cf1bc77802bc398074eff89ef2b2bb23b5ee8d756aadf137641230bec92d8\",\"workflow_run\":{\"id\":40241638129,\"repository_id\":1412901178,\"head_repository_id\":1412901178,\"head_branch\":\"main\",\"head_sha\":\"300912566b6922ce775c9b4c65556a2e0fcfb4d6\"}},\"run\":{\"id\":40241638129,\"head_sha\":\"300912566b6922ce775c9b4c65556a2e0fcfb4d6\",\"head_branch\":\"main\",\"event\":\"push\",\"path\":\".github/workflows/okx_hourly_extreme_history.yml\",\"repository\":{\"full_name\":\"lzwislam-collab/doge-data-collector\"},\"status\":\"completed\",\"conclusion\":\"cancelled\"},\"logged_marker\":{\"status\":\"GITHUB_ARTIFACT_UPLOAD_RECORDED_NOT_WORM\",\"recorded_at_utc\":\"2026-10-26T00:45:14.153407+00:00\",\"artifact_id\":\"12620730069\",\"artifact_archive_sha256\":\"083cf1bc77802bc398074eff89ef2b2bb23b5ee8d756aadf137641230bec92d8\",\"run_id\":\"40241638129\",\"bundle_sha256\":\"cbb65e4850a8e30289d12b8eea79b4f6e1068dc9dcd0ea3f56030e05de944c56\",\"implementation_sha256\":\"7180d1842723960c1ca079eae6d7cf64cde6c654e44321fd5637875e2c3ef1b7\",\"independent_retention_verified\":false,\"remote_bundle_content_readback_verified\":false,\"confirmation_source\":\"successful actions/upload-artifact@v4 outputs; not a WORM claim\"},\"remote_latest\":{\"status\":\"NOT_CONFIGURED\",\"independent_retention_verified\":false,\"bundle_path\":\"data/pit_retention_preparation_v1/bundles/cbb65e4850a8e30289d12b8eea79b4f6e1068dc9dcd0ea3f56030e05de944c56.index.json\",\"bundle_sha256\":\"cbb65e4850a8e30289d12b8eea79b4f6e1068dc9dcd0ea3f56030e05de944c56\",\"byte_count\":1397884,\"objects\":7973,\"prepared_at_utc\":\"2026-10-26T00:30:11.523770+00:00\",\"upload_performed\":false,\"storage_provisioned\":false,\"credentials_required_in_chat\":false,\"bundle_kind\":\"SHARDED_INVENTORY_INDEX\",\"part_count\":29,\"all_part_versions_and_retention_required\":true,\"source_ledger_rows\":9744,\"receipt_fresh_asof_rows\":0,\"receipt_freshness_hours\":48,\"source_observation_age_limits_verified\":false,\"model_use_authorized\":false,\"historical_first_publication_verified\":false,\"retention_export_path\":\"data/plus_evidence_v1_0/sources/retention_exports/f7b3562e7ee84748985d3f346ba74623.json\",\"observed_at_utc\":\"2026-10-26T00:30:11.523770+00:00\",\"generated_new_bundle\":true,\"implementation_sha256\":\"7180d1842723960c1ca079eae6d7cf64cde6c654e44321fd5637875e2c3ef1b7\",\"source_manifest_path\":\"data/plus_evidence_v1_0/sources/manifests/20261026T002808633773Z_f10618b900d44ec285e3d8bf8311a83f.json\",\"source_manifest_sha256\":\"a1bebe0c66c24cbda8d77c5c7858804ca84331bdd775d6c7c01980f46ee6c964\",\"run_id\":\"40241638129\",\"run_attempt\":\"1\",\"scope\":\"once-per-UTC-day inventory package, not every later forecast protected\"},\"remote_published_marker\":{\"status\":\"GITHUB_ARTIFACT_UPLOAD_RECORDED_NOT_WORM\",\"recorded_at_utc\":\"2026-10-25T01:25:25.559518+00:00\",\"artifact_id\":\"12581914557\",\"artifact_archive_sha256\":\"d6974f86bfffe7365e6d43f90e6b9cfea194c380acf493100c3959084c48a1d2\",\"run_id\":\"40149442049\",\"bundle_sha256\":\"d67d384240449d462447e8dddfc99c6757b1f1d167f3953fe87e2ebd97593e9d\",\"implementation_sha256\":\"7180d1842723960c1ca079eae6d7cf64cde6c654e44321fd5637875e2c3ef1b7\",\"independent_retention_verified\":false,\"remote_bundle_content_readback_verified\":false,\"confirmation_source\":\"successful actions/upload-artifact@v4 outputs; not a WORM claim\"}}")
class TransferBoundary(Exception):pass
class RealSourceBinding(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.stage=Path(self.tmp.name)
  self.base=self.stage/'data/pit_retention_preparation_v1';self.base.mkdir(parents=True)
  (self.base/'latest.json').write_text(json.dumps(inputs['remote_latest']))
  (self.base/'artifact_publication.json').write_text(json.dumps(inputs['logged_marker']))
 def invoke(self,artifact=None,run=None):
  artifact=artifact or inputs['artifact'];run=run or inputs['run']
  def api(path):
   if path=='/actions/artifacts/12620730069':return artifact
   if path=='/actions/runs/40241638129':return run
   raise AssertionError('unexpected request '+path)
  with patch.object(m,'github',side_effect=api),patch.object(m,'download_artifact',side_effect=TransferBoundary) as transfer:
   try:m.stage(self.stage)
   finally:self.transfer_calls=transfer.call_count
 def test_genuine_metadata_and_original_log_receipt_reach_transfer_boundary(self):
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
if __name__=='__main__':unittest.main()

