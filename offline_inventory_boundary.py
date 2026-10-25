"""Opt-in offline physical resource-boundary test, NOT a semantic inventory test.

--full writes 21 GiB downloaded + 20 GiB expanded consumer files. Sparse input
contains zero-filled .zip/.json resources, deliberately not frozen evidence.
No cloud calls or production reads; temporary files are cleaned on exit.
"""
import argparse,json,platform,socket,sys,tempfile,time
from pathlib import Path
from unittest.mock import patch
import oss_inventory_retention as inventory
from offline_inventory_stress import sparse_zip,zero_member,allocated,peak_rss,evidence_destination,write_evidence_json


def fixture_size(members,prefix):
 cursor=prefix;directory_bytes=0
 for name,member in members:
  length=len(name.encode('utf-8'));offset=cursor
  cursor+=30+length+member['size']
  directory_bytes+=46+length+(12 if offset>=0xffffffff else 0)
 return cursor+directory_bytes+22+(76 if cursor>=0xffffffff or len(members)>=65535 else 0)


def prefix_for_size(members,transport):
 prefix=transport-fixture_size(members,0)
 for _ in range(8):
  delta=transport-fixture_size(members,prefix)
  if delta==0 and prefix>=0:return prefix
  prefix+=delta
 raise ValueError('cannot fit ZIP64 fixture to requested transport boundary')


def stress(full=False):
 started=time.monotonic()
 transport=inventory.ARTIFACT_LIMIT if full else 8*1024**2
 zip_bytes=inventory.STORED_ZIP_LIMIT if full else 1024**2
 meta_bytes=inventory.INVENTORY_METADATA_LIMIT if full else 1024**2
 zip_count,meta_count=(64,448) if full else (2,2)
 expanded=zip_count*zip_bytes+meta_count*meta_bytes
 plan_peak=transport+expanded+inventory.DISK_RESERVE
 cleaned=False
 with tempfile.TemporaryDirectory(prefix='offline-resource-boundary-') as tmp:
  root=Path(tmp);fixture=root/'fixture';workspace=root/'workspace';workspace.mkdir()
  if inventory.shutil.disk_usage(workspace).free<plan_peak+1024**3:raise ValueError('insufficient free disk for physical boundary test')
  zip_member=zero_member(zip_bytes);metadata=zero_member(meta_bytes)
  members=[('container-%03d.zip'%i,zip_member) for i in range(zip_count)]+[('metadata-%03d.json'%i,metadata) for i in range(meta_count)]
  prefix=prefix_for_size(members,transport)
  if prefix<0:raise ValueError('transport budget below fixture size')
  source=fixture/'boundary.zip';descriptor=sparse_zip(source,members,prefix_bytes=prefix)
  if descriptor['size']!=transport:raise ValueError('fixture transport boundary mismatch')
  plan=inventory.artifact_capacity(source)
  if plan['expanded_bytes']!=expanded or plan['peak_additional_disk_bytes']!=plan_peak:
   raise ValueError('fixture capacity boundary mismatch')
  print(json.dumps({'phase':'capacity','plan':plan}),flush=True)
  download=workspace/'download.zip';destination=workspace/'expanded';destination.mkdir()
  with patch.object(inventory,'github_response',side_effect=lambda p:source.open('rb')):
   inventory.download_artifact({'id':1,'size_in_bytes':transport,'digest':descriptor['sha']},download)
  inventory.extract_artifact(download,destination)
  files=list(destination.iterdir());expected={name:member['size'] for name,member in members}
  if any(p.is_symlink() or not p.is_file() for p in files) or {p.name:p.stat().st_size for p in files}!=expected:
   raise ValueError('expanded file names/sizes do not match resource fixture')
  observation={'consumer_allocated_bytes':allocated(download)+sum(allocated(p) for p in files),'fixture_allocated_bytes':sum(allocated(p) for p in fixture.iterdir()),'peak_process_rss_bytes':peak_rss()}
  print(json.dumps({'phase':'maximum_simultaneous_files','observation':observation}),flush=True)
 cleaned=not root.exists()
 if not cleaned:raise ValueError('resource boundary temporary files were not cleaned')
 return {'schema':'OFFLINE_RESOURCE_BOUNDARY_V1','full':full,'platform':sys.platform,'python_version':platform.python_version(),'synthetic_only':True,'network_blocked':True,'semantic_inventory_test':False,'frozen_verifier_executed':False,'zip_member_payloads_are_synthetic_resource_bytes':True,'transport_and_extraction_passed':True,'outer_crc_checked_during_extraction':True,'temporary_files_cleaned':cleaned,'capacity':plan,'observation':observation,'elapsed_seconds':round(time.monotonic()-started,3),'limitations':['No claim that zero-filled inner ZIP/JSON files are valid inventory','Tests consumer transport/extraction resource budgets only; synthetic inventory accepted by the frozen verifier has separate evidence','Windows measurements do not establish Linux symlink behavior or cloud runner capacity','File allocation and RSS are process/private-file observations, not entire OS/volume maxima']}


def main():
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--full',action='store_true');parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
 output=evidence_destination(args.output)
 with patch.object(socket.socket,'connect',side_effect=AssertionError('offline')),patch.object(socket.socket,'connect_ex',side_effect=AssertionError('offline')):result=stress(args.full)
 write_evidence_json(output,result);print(json.dumps(result),flush=True)

if __name__=='__main__':main()
