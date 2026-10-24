"""Opt-in synthetic offline inventory stress. Never reads production inventory.

--full exercises 64 shards * 256 MiB source bytes. The fixture is sparse, but
consumer download/extraction writes real files; allow at least 34 GiB free.
Linux is reported only when sys.platform actually starts with linux.
"""
import argparse,ctypes,hashlib,json,os,platform,socket,struct,sys,tempfile,time,zipfile,zlib
from datetime import datetime,timezone
from pathlib import Path
from unittest.mock import patch
import oss_inventory_retention as inventory
import pit_retention_preparation as frozen

CHUNK=1024*1024

def sparse_stream(path):
    path.parent.mkdir(parents=True,exist_ok=True)
    stream=path.open('xb')
    if os.name=='nt':
        import msvcrt
        from ctypes import wintypes
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        call=kernel.DeviceIoControl
        call.argtypes=[wintypes.HANDLE,wintypes.DWORD,ctypes.c_void_p,wintypes.DWORD,ctypes.c_void_p,wintypes.DWORD,ctypes.POINTER(wintypes.DWORD),ctypes.c_void_p]
        call.restype=wintypes.BOOL;returned=wintypes.DWORD()
        if not call(msvcrt.get_osfhandle(stream.fileno()),0x900C4,None,0,None,0,ctypes.byref(returned),None):
            stream.close();raise ctypes.WinError(ctypes.get_last_error())
    return stream

def hash_file(path):
    sha=hashlib.sha256();crc=0
    with path.open('rb') as source:
        for chunk in iter(lambda:source.read(CHUNK),b''):
            sha.update(chunk);crc=zlib.crc32(chunk,crc)
    return sha.hexdigest(),crc

def zero_member(size):
    sha=hashlib.sha256();crc=0;block=b'\0'*min(CHUNK,size)
    for offset in range(0,size,len(block)):
        chunk=block[:min(len(block),size-offset)];sha.update(chunk);crc=zlib.crc32(chunk,crc)
    return {'size':size,'sha':sha.hexdigest(),'crc':crc,'segments':[]}

def byte_member(raw):
    return {'size':len(raw),'sha':hashlib.sha256(raw).hexdigest(),'crc':zlib.crc32(raw),'segments':[(0,raw)]}

def sparse_zip(path,members,prefix_bytes=0):
    """Produce real ZIP/ZIP64 bytes, with holes only where payload bytes are zero."""
    if type(prefix_bytes) is not int or prefix_bytes<0:raise ValueError('invalid sparse prefix')
    central=[];segments=[]
    with sparse_stream(path) as stream:
        def put(offset,raw):
            stream.seek(offset);stream.write(raw);segments.append((offset,raw))
        cursor=prefix_bytes
        for name,member in members:
            encoded=name.encode('utf-8');size=member['size'];crc=member['crc'];offset=cursor
            if size>=0xffffffff:raise ValueError('fixture member too large')
            header=struct.pack('<4s5H3L2H',b'PK\x03\x04',20,0x800,0,0,33,crc,size,size,len(encoded),0)+encoded
            put(cursor,header);body=cursor+len(header)
            for relative,raw in member['segments']:put(body+relative,raw)
            cursor=body+size
            extra=b'' if offset<0xffffffff else struct.pack('<HHQ',1,8,offset)
            central.append(struct.pack('<4s6H3L5H2L',b'PK\x01\x02',0x0314,45 if extra else 20,0x800,0,0,33,crc,size,size,len(encoded),len(extra),0,0,0,0o100644<<16,min(offset,0xffffffff))+encoded+extra)
        directory=cursor;raw=b''.join(central);put(cursor,raw);cursor+=len(raw)
        zip64=directory>=0xffffffff or len(members)>=65535
        if zip64:
            record=struct.pack('<4sQ2H2L4Q',b'PK\x06\x06',44,45,45,0,0,len(members),len(members),len(raw),directory)
            put(cursor,record);locator=struct.pack('<4sLQL',b'PK\x06\x07',0,cursor,1);cursor+=len(record);put(cursor,locator);cursor+=len(locator)
        end=struct.pack('<4s4H2LH',b'PK\x05\x06',0,0,min(len(members),65535),min(len(members),65535),len(raw),min(directory,0xffffffff),0)
        put(cursor,end);cursor+=len(end);stream.truncate(cursor)
    sha,crc=hash_file(path)
    return {'size':cursor,'sha':sha,'crc':crc,'segments':segments}

def allocated(path):
    if os.name!='nt':return path.stat().st_blocks*512
    from ctypes import wintypes
    call=ctypes.WinDLL('kernel32',use_last_error=True).GetCompressedFileSizeW
    call.argtypes=[wintypes.LPCWSTR,ctypes.POINTER(wintypes.DWORD)];call.restype=wintypes.DWORD
    high=wintypes.DWORD();low=call(str(path),ctypes.byref(high))
    if low==0xffffffff and ctypes.get_last_error():raise ctypes.WinError(ctypes.get_last_error())
    return (high.value<<32)|low

def peak_rss():
    if os.name!='nt':
        import resource
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*(1 if sys.platform=='darwin' else 1024)
    from ctypes import wintypes
    class Counters(ctypes.Structure):
        _fields_=[('cb',wintypes.DWORD),('faults',wintypes.DWORD)]+[(name,ctypes.c_size_t) for name in ('peak','working','peak_paged','paged','peak_nonpaged','nonpaged','pagefile','peak_pagefile')]
    record=Counters();record.cb=ctypes.sizeof(record)
    kernel=ctypes.WinDLL('kernel32');kernel.GetCurrentProcess.restype=wintypes.HANDLE
    call=ctypes.WinDLL('psapi').GetProcessMemoryInfo
    call.argtypes=[wintypes.HANDLE,ctypes.c_void_p,wintypes.DWORD];call.restype=wintypes.BOOL
    if not call(kernel.GetCurrentProcess(),ctypes.byref(record),record.cb):raise OSError('cannot measure memory')
    return record.peak

def stress(parts,source_bytes):
    started=time.monotonic();observations=[]
    with tempfile.TemporaryDirectory(prefix='offline-inventory-stress-') as tmp:
        root=Path(tmp);fixture=root/'fixture';workspace=root/'workspace';workspace.mkdir()
        block=zero_member(source_bytes);objects={};pieces=[];outer=[]
        for number in range(parts):
            name='data/synthetic/'+str(number)+'.bin';objects[name]=block['sha']
            manifest=frozen.canonical({'objects':{name:block['sha']},'synthetic_offline_fixture':True})
            path=fixture/(str(number)+'.zip')
            piece=sparse_zip(path,[('bundle_manifest.json',byte_member(manifest)),(name,block)])
            relative='data/pit_retention_preparation_v1/bundles/'+piece['sha']+'.zip'
            pieces.append({'bundle_path':relative,'bundle_sha256':piece['sha'],'byte_count':piece['size'],'objects':1})
            outer.append(('bundles/'+piece['sha']+'.zip',piece))
            if number%8==0:print(json.dumps({'phase':'fixture','shards':number+1}),flush=True)
        index=frozen.canonical({'schema_version':2,'bundle_kind':'SHARDED_INVENTORY_INDEX','per_part_uncompressed_cap':frozen.LIMIT_BYTES,'parts':pieces,'objects':objects})
        index_sha=hashlib.sha256(index).hexdigest();now=datetime.now(timezone.utc).isoformat()
        prior={'bundle_kind':'SHARDED_INVENTORY_INDEX','bundle_path':'data/pit_retention_preparation_v1/bundles/'+index_sha+'.index.json','bundle_sha256':index_sha,'objects':parts,'prepared_at_utc':now}
        outer += [('bundles/'+index_sha+'.index.json',byte_member(index)),('latest.json',byte_member(frozen.canonical(prior)))]
        artifact=fixture/'artifact.zip';container=sparse_zip(artifact,outer)
        plan=inventory.artifact_capacity(artifact)
        if inventory.shutil.disk_usage(workspace).free<plan['peak_additional_disk_bytes']+1024**3:raise ValueError('not enough free disk for requested real offline stress')
        base=workspace/'data/pit_retention_preparation_v1';base.mkdir(parents=True)
        (base/'latest.json').write_bytes(frozen.canonical(prior))
        (base/'artifact_publication.json').write_text(json.dumps({'bundle_sha256':index_sha,'artifact_id':1,'run_id':2,'artifact_archive_sha256':container['sha']}))
        metadata={'id':1,'size_in_bytes':container['size'],'digest':'sha256:'+container['sha'],'expired':False,'workflow_run':{'id':2}}
        run={'id':2,'head_branch':'main','event':'push','repository':{'full_name':'lzwislam-collab/doge-data-collector'},'path':'.github/workflows/okx_hourly_extreme_history.yml','head_sha':'c'*40,'html_url':'https://example.invalid/synthetic-offline-run'}
        def observe(phase):
            observations.append({'phase':phase,'allocated_private_bytes':sum(allocated(p) for p in root.rglob('*') if p.is_file()),'peak_process_rss_bytes':peak_rss()})
            print(json.dumps(observations[-1]),flush=True)
        original_download=inventory.download_artifact;original_extract=inventory.extract_artifact
        def download(*args):
            result=original_download(*args);observe('download_verified');return result
        def extract(*args):
            result=original_extract(*args);observe('expanded_archive_still_present');return result
        def get(path):return metadata if '/artifacts/' in path else run
        print(json.dumps({'phase':'capacity','plan':plan}),flush=True)
        with patch.object(inventory,'github',side_effect=get),patch.object(inventory,'github_response',side_effect=lambda path:artifact.open('rb')),patch.object(inventory,'download_artifact',side_effect=download),patch.object(inventory,'extract_artifact',side_effect=extract),patch.object(inventory.subprocess,'check_output',return_value='d'*40+'\n'):
            ready=inventory.stage(workspace)
        source=json.loads((workspace/'oss-retention-output/source.json').read_bytes())
        if ready is not True or source.get('inventory_objects')!=parts:
            raise ValueError('synthetic inventory staging/count acceptance failed')
        if source.get('inventory_capacity')!=plan:raise ValueError('synthetic inventory capacity acceptance failed')
        if list(workspace.glob('oss-artifact-*')):raise ValueError('private staging temporary files remain')
        observe('frozen_verifier_passed')
        return {'schema':'OFFLINE_INVENTORY_CAPACITY_STRESS_V1','platform':sys.platform,'linux_runtime':sys.platform.startswith('linux'),'linux_symlink_tests_verified':False,'python_version':platform.python_version(),'os_release':platform.release(),
                'synthetic_only':True,'frozen_verifier_passed':True,'shards':parts,'raw_source_bytes':parts*source_bytes,
                'artifact_bytes':container['size'],'fixture_allocated_bytes':sum(allocated(p) for p in fixture.rglob('*') if p.is_file()),
                'capacity':plan,'observations':observations,'elapsed_seconds':round(time.monotonic()-started,3),
                'limitations':['STORE synthetic ZIPs accepted by the unchanged frozen verifier; not real production artifacts',
                               'No finite maximum frozen-producer container size; this proves the tested consumer domain only']}

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--full',action='store_true');parser.add_argument('--parts',type=int,default=4);parser.add_argument('--source-bytes',type=int,default=1024*1024);parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    parts=64 if args.full else args.parts;source_bytes=frozen.LIMIT_BYTES if args.full else args.source_bytes
    if not 2<=parts<=64 or not 1<=source_bytes<=frozen.LIMIT_BYTES:raise ValueError('invalid synthetic inventory dimensions')
    with patch.object(socket.socket,'connect',side_effect=AssertionError('offline: network forbidden')),patch.object(socket.socket,'connect_ex',side_effect=AssertionError('offline: network forbidden')):
        result=stress(parts,source_bytes)
    args.output.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8');print(json.dumps(result),flush=True)

if __name__=='__main__':main()
