"""Invocation-local immutable Git text transport; no verdicts are cached."""
from collections import OrderedDict
from contextlib import contextmanager
from functools import wraps
import hashlib
import os
from pathlib import Path
import re
import stat
import subprocess
import threading
import types

FORWARD_BLOB = 'f0831c7d38581543f84a85639c6d69a7305ff382'
MAX_BYTES = 8 * 1024 * 1024
MAX_ENTRIES = 16
PATHS = ('data/direction_v42/forward_registry.csv', 'data/stage5/forward_validation_protocol.json')
_ENV_ALLOWED = {'GIT_OPTIONAL_LOCKS': ('0','1'), 'GIT_NO_LAZY_FETCH': ('1',),
                'GIT_TERMINAL_PROMPT': ('0',), 'GIT_LFS_SKIP_SMUDGE': ('1',),
                'GIT_CONFIG_NOSYSTEM': ('1',), 'GIT_PAGER': ('cat','')}


class _UnknownView(ValueError):
    pass


def _eligible(base, root):
    try:
        path = root/'forward_validation.py'
        if Path(base.__file__).resolve() != path: return False
        with path.open('rb') as stream: raw = stream.read(1024*1024+1)
        if len(raw) > 1024*1024: return False
        raw = raw.replace(b'\r\n',b'\n')
        if hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest() != FORWARD_BLOB:
            return False
        compiled = compile(raw,str(path),'exec',dont_inherit=True)
        for name in ('git','remote_witnesses'):
            function = getattr(base,name,None)
            codes = [x for x in compiled.co_consts if isinstance(x,types.CodeType) and x.co_name==name]
            if (not isinstance(function,types.FunctionType) or function.__name__!=name
                    or len(codes)!=1 or function.__code__ != codes[0]
                    or function.__globals__ is not base.__dict__):
                return False
        if (base.git.__defaults__ is not None or base.git.__kwdefaults__ is not None
                or base.remote_witnesses.__kwdefaults__ is not None):
            return False
        defaults=base.remote_witnesses.__defaults__
        if type(defaults) is not tuple or len(defaults)!=1 or defaults[0] is not False:
            return False
        return True
    except (OSError,ValueError,TypeError,AttributeError,SyntaxError):
        return False


def _environment():
    values = {}
    for key,value in os.environ.items():
        if key.startswith('GIT_'):
            if key not in _ENV_ALLOWED or value not in _ENV_ALLOWED[key]: raise _UnknownView()
            values[key]=value
    for key in ('PATH','PATHEXT','HOME','USERPROFILE','HOMEDRIVE','HOMEPATH','XDG_CONFIG_HOME'):
        values[key]=os.environ.get(key)
    return tuple(sorted(values.items()))


def _file(path, limit):
    try: before = path.lstat()
    except FileNotFoundError: return None
    if not stat.S_ISREG(before.st_mode) or before.st_size > limit: raise _UnknownView()
    with path.open('rb') as stream: body = stream.read(limit+1)
    after = path.lstat()
    fields = lambda value: (value.st_mode,value.st_size,value.st_mtime_ns,value.st_ctime_ns,
                             value.st_dev,value.st_ino)
    if len(body)>limit or fields(before)!=fields(after): raise _UnknownView()
    return fields(after),hashlib.sha256(body).digest()


class _View:
    def __init__(self, root, original):
        self.root=root; self.original=original; self.environment=_environment()
        self.gitdir=Path(original(root,'rev-parse','--absolute-git-dir')).resolve()
        common=Path(original(root,'rev-parse','--git-common-dir'))
        common=(root/common).resolve() if not common.is_absolute() else common.resolve()
        if self.gitdir != root/'.git' or common != self.gitdir or self.gitdir.is_symlink():
            raise _UnknownView()
        if not re.fullmatch('[0-9a-f]{40}',original(root,'rev-parse','--verify','HEAD')):
            raise _UnknownView()
        if original(root,'for-each-ref','--format=%(refname)','refs/replace'): raise _UnknownView()
        # Origins are read without values: credentials are neither saved nor emitted.
        text=original(root,'config','--includes','--show-origin','--name-only','--null','--list')
        pieces=text.split('\0')
        if pieces and pieces[-1]=='': pieces.pop()
        if len(pieces)%2: raise _UnknownView()
        self.configs=set()
        for origin,key in zip(pieces[::2],pieces[1::2]):
            lower=key.lower()
            if (not origin.startswith('file:') or lower.startswith(('include.','includeif.'))
                    or lower in ('extensions.partialclone','extensions.worktreeconfig','core.worktree','core.fsmonitor')
                    or lower.endswith('.promisor')):
                raise _UnknownView()
            path=Path(origin[5:]); self.configs.add((root/path).resolve() if not path.is_absolute() else path.resolve())
        profile_root=Path(os.path.expanduser('~'))
        self.configs.add(profile_root/'.gitconfig')
        xdg=os.environ.get('XDG_CONFIG_HOME')
        self.configs.add((Path(xdg) if xdg else profile_root/'.config')/'git/config')
        self.initial=self.snapshot()

    def snapshot(self):
        if _environment()!=self.environment: raise _UnknownView()
        for name in ('info/grafts','refs/replace'):
            path=self.gitdir/name
            try: path.lstat()
            except FileNotFoundError: pass
            else: raise _UnknownView()
        result={}
        for name in ('HEAD','config','config.worktree','index','packed-refs','shallow','objects/info/alternates'):
            result['git/'+name]=_file(self.gitdir/name,4*1024*1024)
        for path in self.configs: result['config/'+str(path)]=_file(path,1024*1024)
        refs=self.gitdir/'refs'
        if refs.is_symlink(): raise _UnknownView()
        paths=[]
        if refs.exists():
            pending=[refs]
            while pending:
                directory=pending.pop()
                with os.scandir(directory) as entries:
                    for entry in entries:
                        if len(paths)>=512: raise _UnknownView()
                        path=Path(entry.path);paths.append(path)
                        if entry.is_dir(follow_symlinks=False):pending.append(path)
        paths.sort()
        for path in paths:
            metadata=path.lstat()
            if stat.S_ISDIR(metadata.st_mode):
                result['refdir/'+str(path.relative_to(refs))]=(metadata.st_ino,metadata.st_mtime_ns,metadata.st_ctime_ns)
            else:
                result['ref/'+str(path.relative_to(refs))]=_file(path,65536)
        return result

    def unchanged(self):
        try: return self.snapshot()==self.initial
        except (OSError,ValueError,TypeError): return False


class _RawReads:
    def __init__(self, root, original, view, report):
        self.root=root;self.original=original;self.view=view;self.report=report
        self.cache=OrderedDict();self.retained=0;self.enabled=True;self.owner=threading.get_ident()

    def clear(self):
        self.cache.clear();self.retained=0;self.report['cached_bytes']=0;self.report['entries']=0

    def invalidate(self):
        if self.enabled:self.report['view_invalidations']+=1
        self.enabled=False;self.clear()

    def read(self, root, *args):
        eligible=(type(root) is type(self.root) and root==self.root and len(args)==2
                  and args[0]=='show' and type(args[1]) is str
                  and re.fullmatch('[0-9a-f]{40}:(?:'+ '|'.join(re.escape(p) for p in PATHS)+')',args[1]))
        if not eligible or threading.get_ident()!=self.owner or not self.enabled:
            return self.original(root,*args)
        if not self.view.unchanged():
            self.invalidate();return self.original(root,*args)
        key=args[1]
        if key in self.cache:
            self.cache.move_to_end(key);self.report['hits']+=1
            return self.cache[key].decode('utf-8')
        self.report['misses']+=1
        # Never catch or store an original Git failure.
        text=self.original(root,*args)
        self.report['successful_reads']+=1
        if not self.view.unchanged():
            self.invalidate();return text
        if type(text) is not str:return text
        body=text.encode('utf-8')
        size=len(key.encode('utf-8'))+len(body)
        if size>MAX_BYTES:return text
        while self.cache and (len(self.cache)>=MAX_ENTRIES or self.retained+size>MAX_BYTES):
            old,old_body=self.cache.popitem(last=False)
            self.retained-=len(old.encode('utf-8'))+len(old_body)
            self.report['evictions']+=1
        if MAX_ENTRIES>0:
            self.cache[key]=body;self.retained+=size
            self.report['cached_bytes']=self.retained;self.report['entries']=len(self.cache)
            self.report['peak_bytes']=max(self.report['peak_bytes'],self.retained)
            self.report['peak_entries']=max(self.report['peak_entries'],len(self.cache))
        return text


@contextmanager
def installed(base, root):
    """Wrap only the pinned original remote function; all its predicates still run."""
    captured_root=Path(root).resolve()
    report=dict(implementation='witness_git_raw_lru_v1',installed=False,native_calls=0,
        fallback_calls=0,hits=0,misses=0,successful_reads=0,evictions=0,entries=0,
        cached_bytes=0,view_invalidations=0,max_bytes=MAX_BYTES,max_entries=MAX_ENTRIES,
        peak_bytes=0,peak_entries=0)
    if not _eligible(base,captured_root):
        yield report;return
    original_git=base.git;original_remote=base.remote_witnesses
    lock=threading.RLock()

    @wraps(original_remote)
    def remote(root, registry, prior, runs, persist_cache=False):
        with lock:
            if (type(root) is not type(captured_root) or root!=captured_root or base.git is not original_git
                    or not callable(getattr(base,'history_receipts',None))):
                report['fallback_calls']+=1
                return original_remote(root,registry,prior,runs,persist_cache=persist_cache)
            try:view=_View(root,original_git)
            except (OSError,ValueError,TypeError,subprocess.CalledProcessError):
                report['fallback_calls']+=1
                return original_remote(root,registry,prior,runs,persist_cache=persist_cache)
            previous_history=base.history_receipts
            reads=_RawReads(root,original_git,view,report)
            report['native_calls']+=1

            def history(*args,**kwargs):
                # Keep the actual native provider's strict original-git identity.
                base.git=original_git
                succeeded=False
                try:
                    value=previous_history(*args,**kwargs);succeeded=True;return value
                finally:
                    base.history_receipts=history
                    base.git=reads.read if succeeded else original_git

            base.history_receipts=history
            try:
                return original_remote(root,registry,prior,runs,persist_cache=persist_cache)
            finally:
                base.git=original_git;base.history_receipts=previous_history;reads.clear()

    base.remote_witnesses=remote;report['installed']=True
    try:yield report
    finally:base.remote_witnesses=original_remote
