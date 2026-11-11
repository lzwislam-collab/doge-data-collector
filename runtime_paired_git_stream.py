"""Owned sequential raw-Git transport for the pinned paired audit only.

One original logical show causes one fresh batch request. No record, model,
qualification, error, or metadata result is cached. An incompatible stream is
closed before the current untouched original call is replayed.
"""
from contextlib import contextmanager
from collections import OrderedDict
from datetime import datetime,timedelta,timezone
import builtins
import hashlib
import json
import math
import os
from pathlib import Path
import queue
import re
import shutil
import subprocess
import sys
import threading
import types
import uuid
import runtime_process

IMPLEMENTATION='paired_fresh_git_blob_stream_v1'
REQUEST_TIMEOUT_SECONDS=30
MAX_BLOB_BYTES=64*1024*1024
_BLOBS={
    'paired_cohort_audit.py':'a6bdf0c54ffcf648384c756d400e742ab57a1d61',
    'paired_forward_cohort.py':'02e6b3ba51f063ba17cddcb98ff6445a08235c97',
    'publish_forward_evidence.py':'d982f4f8bd278d7a1505073bc35dd71b80eeb095',
}
_SPEC=re.compile(r'[0-9a-f]{40}:data/paired_forward_v1/(?:records/[A-Za-z0-9_-]+|prepared/[0-9a-f]{64})\.json\Z')
_HEADER=re.compile(rb'([0-9a-f]{40}) blob (0|[1-9][0-9]{0,10}) (PAIR-BLOB-[0-9a-f]{32}-[1-9][0-9]*)\n\Z')
_BATCH_FORMAT='--batch=%(objectname) %(objecttype) %(objectsize) %(rest)'


class _Unavailable(Exception):
    """Internal transport incompatibility; never a qualification result."""


def _raw(path):
    with path.open('rb') as handle:
        raw=handle.read(1024*1024+1)
    if len(raw)>1024*1024:raise _Unavailable()
    return raw.replace(b'\r\n',b'\n')


def _pinned(path):
    raw=_raw(path)
    if hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()!=_BLOBS[path.name]:
        raise _Unavailable()
    return compile(raw,str(path),'exec',dont_inherit=True)


def _function(function,code,namespace,defaults=None):
    return (isinstance(function,types.FunctionType) and function.__code__==code
            and function.__globals__ is namespace and
            (function.__defaults__ is None if defaults is None else
             type(function.__defaults__) is tuple and len(function.__defaults__)==len(defaults)
             and all(actual is expected for actual,expected in zip(function.__defaults__,defaults)))
            and function.__kwdefaults__ is None and function.__closure__ is None)


def _literal_defaults(actual,expected):
    if expected is None or type(expected) is bool:return actual is expected
    if type(actual) is not type(expected):return False
    if type(expected) is tuple:
        return len(actual)==len(expected) and all(_literal_defaults(a,b) for a,b in zip(actual,expected))
    if type(expected) is dict:
        return (all(type(key) is str for key in actual) and set(actual)==set(expected)
                and all(_literal_defaults(actual[key],value) for key,value in expected.items()))
    return actual==expected


def _eligible(root,namespace):
    try:
        if not isinstance(namespace,dict):return None
        cohort=namespace['cohort'];publication=cohort.publication
        modules=(('paired_forward_cohort.py',cohort),('publish_forward_evidence.py',publication))
        for name,module in modules:
            if type(module) is not types.ModuleType or Path(module.__file__).resolve()!=root/name:
                return None
            _pinned(root/name)
        code=_pinned(root/'paired_cohort_audit.py')
        special={'__name__','__doc__','__package__','__loader__','__spec__','__file__','__cached__','__builtins__'}
        if set(namespace)-set(code.co_names)-special:return None
        standard={'json':json,'hashlib':hashlib,'OrderedDict':OrderedDict,
                  'datetime':datetime,'timedelta':timedelta,'timezone':timezone}
        if any(namespace.get(key) is not value for key,value in standard.items()):return None
        if namespace.get('__builtins__') is not builtins.__dict__ and namespace.get('__builtins__') is not builtins:return None
        comparators={'frozen_production':'doge_reversal_direction','historical_majority':'majority_direction',
                     'btc_momentum':'btc_momentum_direction','always_up':None,'always_down':None}
        if type(namespace.get('COMPARATORS')) is not dict or list(namespace['COMPARATORS'].items())!=list(comparators.items()):return None
        codes=[c for c in code.co_consts if isinstance(c,types.CodeType)]
        names={c.co_name for c in codes}
        if len(names)!=len(codes) or names!={'nonoverlap','metrics','bounded_cache','verify_pair','evaluate','main'}:
            return None
        for child in codes:
            default={'verify_pair':(None,),'evaluate':(cohort.ROOT,None)}.get(child.co_name)
            if not _function(namespace.get(child.co_name),child,namespace,default):return None
        if (type(cohort.ROOT) is not type(root) or Path(cohort.ROOT).resolve()!=root or namespace.get('json') is not json
                or namespace.get('hashlib') is not hashlib):return None
        # These shared links are precisely the links used by verify_pair.
        for key in ('selection','quotes','frozen'):
            if namespace.get(key) is not getattr(cohort,key,None):return None
        pubcode=_pinned(root/'publish_forward_evidence.py')
        if set(publication.__dict__)-set(pubcode.co_names)-special:return None
        if (publication.frozen is not namespace['frozen'] or publication.evidence is not namespace['quotes']
                or cohort.json is not json or cohort.hashlib is not hashlib):return None
        commands=[c for c in pubcode.co_consts if isinstance(c,types.CodeType) and c.co_name=='command']
        original=publication.command
        if (len(commands)!=1 or not _function(original,commands[0],publication.__dict__)
                or publication.__dict__.get('subprocess') is not subprocess):return None
        path=Path(subprocess.__file__)
        standard=compile(_raw(path),str(path),'exec',dont_inherit=True)
        runs=[c for c in standard.co_consts if isinstance(c,types.CodeType) and c.co_name=='run']
        run=subprocess.run
        if (len(runs)!=1 or not isinstance(run,types.FunctionType) or run.__code__!=runs[0]
                or run.__globals__ is not subprocess.__dict__ or run.__defaults__ is not None
                or not _literal_defaults(run.__kwdefaults__,{'input':None,'capture_output':False,'timeout':None,'check':False})
                or run.__closure__ is not None):return None
        classes=[c for c in standard.co_consts if isinstance(c,types.CodeType) and c.co_name=='Popen']
        popen=subprocess.Popen
        if (len(classes)!=1 or type(popen) is not type or popen.__module__!='subprocess'
                or popen.__name__!='Popen' or popen.__bases__!=(object,) or '__new__' in vars(popen)):
            return None
        methods=[c for c in classes[0].co_consts if isinstance(c,types.CodeType)]
        for name,function in vars(popen).items():
            if name=='universal_newlines':
                if type(function) is not property or function.fdel is not None:return None
                for accessor in (function.fget,function.fset):
                    expected=[code for code in methods if code.co_name==name and
                              isinstance(accessor,types.FunctionType) and accessor.__code__==code]
                    if len(expected)!=1 or not _function(accessor,expected[0],subprocess.__dict__):return None
                continue
            if name in {code.co_name for code in methods} and not isinstance(function,types.FunctionType):return None
            if not isinstance(function,types.FunctionType):continue
            if name=='_on_error_fd_closer':
                wrapped=getattr(function,'__wrapped__',None)
                helpers=[c for c in contextmanager.__code__.co_consts if isinstance(c,types.CodeType) and c.co_name=='helper']
                if (len(helpers)!=1 or function.__code__!=helpers[0]
                        or function.__globals__ is not contextmanager.__globals__
                        or function.__closure__ is None or len(function.__closure__)!=1
                        or function.__closure__[0].cell_contents is not wrapped):return None
                function=wrapped
            if (not isinstance(function,types.FunctionType) or function.__globals__ is not subprocess.__dict__
                    or function.__code__.co_name!=('terminate' if os.name=='nt' and name=='kill' else name)
                    or sum(function.__code__==code for code in methods)!=1):return None
        defaults={'__init__':(-1,None,None,None,None,None,True,False,None,None,None,None,0,True,False,()),
                  'communicate':(None,None),'wait':(None,),'poll':None,'kill':None,'terminate':None,
                  '__enter__':None,'__exit__':None}
        for name,expected in defaults.items():
            function=vars(popen).get(name)
            if not isinstance(function,types.FunctionType) or not _literal_defaults(function.__defaults__,expected):return None
            keywords=({'user':None,'group':None,'extra_groups':None,'encoding':None,'errors':None,
                       'text':None,'umask':-1,'pipesize':-1,'process_group':None} if name=='__init__' else None)
            if not _literal_defaults(function.__kwdefaults__,keywords):return None
        return publication,original
    except (AttributeError,KeyError,OSError,TypeError,ValueError,SyntaxError,_Unavailable):
        return None


def _control(path):
    """Never allocate more than the explicit control-file cap, even on growth."""
    if path.is_symlink():raise _Unavailable()
    try:
        with path.open('rb') as handle:
            stat=os.fstat(handle.fileno())
            if stat.st_size>1024*1024:raise _Unavailable()
            raw=handle.read(1024*1024+1)
            after=os.fstat(handle.fileno())
    except FileNotFoundError:return None
    if (len(raw)>1024*1024 or (stat.st_dev,stat.st_ino,stat.st_size,stat.st_mtime_ns)!=
            (after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns)):
        raise _Unavailable()
    return stat,raw


def _file_stamp(path):
    found=_control(path)
    if found is None:return None
    stat,raw=found
    return (stat.st_dev,stat.st_ino,stat.st_size,stat.st_mtime_ns,
            hashlib.sha256(raw).digest())


def _pack_view(objects):
    pack=objects/'pack'
    if pack.is_symlink():raise _Unavailable()
    if not pack.exists():return
    with os.scandir(pack) as entries:
        for index,entry in enumerate(entries):
            if index>=512 or entry.is_symlink() or entry.name.endswith('.promisor'):
                raise _Unavailable()


def _environment():
    values=tuple(sorted((key,value) for key,value in os.environ.items()
                        if key.upper().startswith('GIT_') or key.upper() in
                        ('HOME','USERPROFILE','XDG_CONFIG_HOME','PATH','SYSTEMROOT')))
    if any(key.upper().startswith('GIT_') and key.upper() not in
           ('GIT_TERMINAL_PROMPT','GIT_OPTIONAL_LOCKS','GIT_PAGER','GIT_NO_LAZY_FETCH') for key,value in values):
        raise _Unavailable()
    if any(key.upper()=='GIT_NO_LAZY_FETCH' and value!='1' for key,value in values):raise _Unavailable()
    return values


class _View:
    """Relevant raw-object view only; HEAD and index remain the outer gate's job."""
    def __init__(self,root,trusted_object_directory):
        self.root=root;self.environment=_environment()
        self.executable=Path(shutil.which('git') or '').resolve()
        if not self.executable.is_file() or root.is_symlink():raise _Unavailable()
        self.git=root/'.git'
        if not self.git.is_dir() or self.git.is_symlink():raise _Unavailable()
        self.objects=[self.git/'objects']
        alternate=self.git/'objects/info/alternates'
        if alternate.exists():
            if trusted_object_directory is None:raise _Unavailable()
            raw=_control(alternate)[1]
            lines=raw.decode('utf-8').splitlines()
            if len(lines)!=1 or not lines[0]:raise _Unavailable()
            target=Path(lines[0])
            if not target.is_absolute():target=alternate.parent.parent/target
            target=target.resolve()
            if target!=Path(trusted_object_directory).resolve() or not target.is_dir() or target.is_symlink():
                raise _Unavailable()
            if (target/'info/alternates').exists() or (target/'info/http-alternates').exists():raise _Unavailable()
            self.objects.append(target)
        self.files={self.git/'config',self.git/'packed-refs',self.git/'info/grafts',alternate,
                    self.git/'objects/info/http-alternates'}
        if (self.git/'commondir').exists() or (self.git/'config.worktree').exists():raise _Unavailable()
        result=runtime_process.command(['git','config','--show-origin','-z','--list'],
                                       cwd=root,timeout=10).encode('utf-8')
        fields=result.split(b'\0')
        if fields[-1]!=b'' or len(fields)%2!=1:raise _Unavailable()
        for offset in range(0,len(fields)-1,2):
            origin=fields[offset].decode('utf-8');entry=fields[offset+1].decode('utf-8')
            key,separator,value=entry.partition('\n');key=key.lower()
            if not separator or not origin.startswith('file:'):raise _Unavailable()
            path=Path(origin[5:]);path=path if path.is_absolute() else root/path
            if path.is_symlink():raise _Unavailable()
            self.files.add(path.resolve())
            if (key.startswith(('include.','includeif.','extensions.')) or
                    key in ('core.worktree','core.alternaterefscommand','core.alternaterefsprefixes') or
                    key=='core.bare' and value.lower()!='false' or
                    key=='core.repositoryformatversion' and value!='0' or
                    key.endswith(('.promisor','.partialclonefilter'))):raise _Unavailable()
        # Watch absent default locations too: a new config must invalidate the view.
        for base in (os.environ.get('HOME'),os.environ.get('USERPROFILE')):
            if base:self.files.add(Path(base)/'.gitconfig')
        xdg=os.environ.get('XDG_CONFIG_HOME')
        if xdg:self.files.add(Path(xdg)/'git/config')
        else:
            home=os.environ.get('HOME') or os.environ.get('USERPROFILE')
            if home:self.files.add(Path(home)/'.config/git/config')
        self.snapshot=self.stamp()

    def stamp(self):
        if _environment()!=self.environment or Path(shutil.which('git') or '').resolve()!=self.executable:
            raise _Unavailable()
        if not self.git.is_dir() or self.git.is_symlink():raise _Unavailable()
        if (self.git/'commondir').exists() or (self.git/'config.worktree').exists():raise _Unavailable()
        if (self.git/'info/grafts').exists() or (self.git/'objects/info/http-alternates').exists():raise _Unavailable()
        replace=self.git/'refs/replace'
        if replace.is_symlink() or (self.git/'refs').is_symlink():raise _Unavailable()
        if replace.exists():
            with os.scandir(replace) as entries:
                if next(entries,None) is not None:raise _Unavailable()
        packed=self.git/'packed-refs'
        found=_control(packed)
        if found is not None and b' refs/replace/' in found[1]:raise _Unavailable()
        for objects in self.objects:
            if not objects.is_dir() or objects.is_symlink():raise _Unavailable()
            if (objects/'info/http-alternates').exists():raise _Unavailable()
            if objects!=self.git/'objects' and (objects/'info/alternates').exists():raise _Unavailable()
            _pack_view(objects)
        exe=self.executable.stat()
        directories=tuple((str(path),path.stat().st_dev,path.stat().st_ino) for path in (self.root,self.git,*self.objects))
        return (directories,(exe.st_dev,exe.st_ino,exe.st_size,exe.st_mtime_ns),
                tuple((str(path),_file_stamp(path)) for path in sorted(self.files,key=str)))

    def check(self):
        if self.stamp()!=self.snapshot:raise _Unavailable()


class _Batch:
    def __init__(self,root,report):
        self.report=report;self.process=None;self.thread=None
        self.nonce=uuid.uuid4().hex
        self.requests=queue.Queue(maxsize=1);self.responses=queue.Queue(maxsize=1)
        try:
            with runtime_process.defer_cancellation():
                self.process=subprocess.Popen(['git','cat-file',_BATCH_FORMAT],cwd=root,stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,bufsize=0,start_new_session=True)
                self.thread=threading.Thread(target=self._reader,name='paired-git-stream-reader',daemon=True)
                self.thread.start();report['processes']+=1
        except BaseException:
            if self.process is not None:self.close()
            raise

    def _read(self,count):
        result=bytearray()
        while len(result)<count:
            part=self.process.stdout.read(count-len(result))
            if not part:raise _Unavailable()
            result.extend(part)
        return bytes(result)

    def _reader(self):
        while True:
            request=self.requests.get()
            if request is None:return
            try:
                spec,marker=request
                raw=spec.encode('ascii')+b' '+marker+b'\n'
                if self.process.stdin.write(raw)!=len(raw):raise _Unavailable()
                self.process.stdin.flush()
                header=bytearray()
                while len(header)<192:
                    character=self._read(1);header.extend(character)
                    if character==b'\n':break
                match=_HEADER.fullmatch(header)
                if not match or match[3]!=marker:raise _Unavailable()
                size=int(match[2])
                if size>MAX_BLOB_BYTES:raise _Unavailable()
                body=self._read(size)
                if self._read(1)!=b'\n':raise _Unavailable()
                digest=hashlib.sha1();digest.update(b'blob '+str(size).encode()+b'\0');digest.update(body)
                if digest.hexdigest().encode()!=match[1]:raise _Unavailable()
                if self.process.poll() is not None:raise _Unavailable()
                self.responses.put((True,body))
            except Exception:
                self.responses.put((False,None));return

    def read(self,spec,timeout):
        self.report['requests']+=1
        marker=('PAIR-BLOB-'+self.nonce+'-'+str(self.report['requests'])).encode('ascii')
        self.requests.put_nowait((spec,marker))
        try:ok,body=self.responses.get(timeout=timeout)
        except queue.Empty as exc:raise _Unavailable() from exc
        if not ok or self.process.poll() is not None:raise _Unavailable()
        return body

    def close(self):
        with runtime_process.defer_cancellation():
            try:
                cleanup=runtime_process.terminate_group(self.process)
                self.report['reaped']=cleanup.get('leader_reaped') is True
            finally:
                try:self.requests.put_nowait(None)
                except queue.Full:pass
                if self.thread is not None and self.thread.ident is not None:
                    self.thread.join(timeout=runtime_process.KILL_WAIT_SECONDS)
                alive=self.thread is not None and self.thread.is_alive()
                self.report['reader_joined']=not alive
                if alive:raise RuntimeError('owned Git reader did not stop')
                for handle in (self.process.stdin,self.process.stdout):
                    if handle is not None:handle.close()


@contextmanager
def installed(root,main_globals,*,trusted_object_directory=None,request_timeout=REQUEST_TIMEOUT_SECONDS):
    """Install only after original imports; restore on every exit without output."""
    report=dict(installed=False,implementation=IMPLEMENTATION,requests=0,processes=0,
                verified_reads=0,fallback_calls=0,reaped=True,reader_joined=True,disabled=False)
    if type(request_timeout) not in (int,float) or not math.isfinite(request_timeout) or request_timeout<=0:
        raise ValueError('finite positive transport timeout required')
    try:
        root=Path(root).resolve();eligible=_eligible(root,main_globals)
        if eligible is None:raise _Unavailable()
    except Exception:
        yield report;return
    # Pure identity checks need no process. Own cancellation before starting
    # even the first bounded Git-view eligibility subprocess.
    with runtime_process.cancellation_signals():
        with _owned_install(root,eligible,report,trusted_object_directory,request_timeout) as active:
            yield active


@contextmanager
def _owned_install(root,eligible,report,trusted_object_directory,request_timeout):
    publication,original=eligible;batch=None
    try:view=_View(root,trusted_object_directory)
    except Exception:
        yield report;return
    owner=threading.get_ident()
    audit_root=root

    def disable():
        nonlocal batch
        report['disabled']=True
        if batch is not None:
            owned=batch;batch=None;owned.close()

    def command(root,*args):
        nonlocal batch
        call_root=root
        route=(len(args)==2 and args[0]=='show' and type(args[1]) is str and _SPEC.fullmatch(args[1]))
        try:same_root=Path(call_root).resolve()==audit_root
        except (OSError,ValueError,TypeError):same_root=False
        if not route or not same_root or threading.get_ident()!=owner:return original(call_root,*args)
        if not report['disabled']:
            try:
                view.check()
                if batch is None:
                    # Keep ownership assignment inside the same deferred region
                    # as Popen; a signal must not escape a returned constructor.
                    with runtime_process.defer_cancellation():
                        batch=_Batch(audit_root,report);report['reaped']=False;report['reader_joined']=False
                body=batch.read(args[1],request_timeout)
                view.check()
                report['verified_reads']+=1
                return body
            except Exception:
                disable()
        report['fallback_calls']+=1
        return original(call_root,*args)

    publication.command=command;report['installed']=True
    try:
        yield report
    finally:
        active_error=sys.exc_info()[1]
        publication.command=original
        if batch is not None:
            owned=batch;batch=None
            try:owned.close()
            except BaseException as cleanup_error:
                if active_error is None:raise
                active_error.process_cleanup_error_type=type(cleanup_error).__name__
