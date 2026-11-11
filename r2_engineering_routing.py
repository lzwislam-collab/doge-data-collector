"""Keep original R2 admission and route its final request to compatible runtime.

All module bindings are scoped to a synchronous call. No journal is written and
no audit is dispatched here: the compatible publisher owns its remote audit
intent. Actual workflow rows and original deadlines are never relabelled.
"""
import base64
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import os
from pathlib import Path
import re
import subprocess
import time
from urllib.error import HTTPError
from urllib.parse import urlencode

import runtime_r2_bridge as original
import r2_engineering_dispatch as dispatch
import r2_engineering_runtime as engine

CACHE_BYTES = 64*1024*1024

class _Client:
    """Reuse the caller's provider budget; never start another transport client."""
    def __init__(self, base):
        self.base = base
        remaining = max(0, 40-float(getattr(base, 'spent', 0)))
        self.end = min(getattr(base, 'end', float('inf')), time.monotonic()+remaining)
    def _check(self):
        if time.monotonic() >= self.end:
            raise TimeoutError('engineering routing total budget exhausted')
    def api(self, path):
        self._check();value = self.base.api(path);self._check();return value
    def post(self, path, payload):
        self._check()
        if isinstance(self.base, dispatch.Client):
            value = self.base.post(path, payload)
        elif hasattr(self.base, 'request'):
            value = self.base.request(path, payload)
        else:
            value = self.base.post(path, payload)
        self._check();return value
    def cancel(self, run_id):
        self._check()
        if not callable(getattr(self.base, 'cancel', None)):
            raise RuntimeError('known stale run cancellation unavailable; intent is not retried')
        value = self.base.cancel(run_id);self._check();return value


def _blob(raw):
    return hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()


def _local_git(root, head, path, end):
    env = dict(os.environ, GIT_NO_LAZY_FETCH='1')
    def read(*args):
        remaining = end-time.monotonic()
        if remaining <= 0:
            raise TimeoutError('engineering identity read budget exhausted')
        return subprocess.run(['git', '--no-replace-objects', '-c', 'safe.directory=*', *args],
            cwd=root, env=env, capture_output=True, timeout=min(5, remaining),
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    try:
        size = read('cat-file', '-s', head+':'+path)
        if size.returncode:
            return None
        if len(size.stdout) > 20 or not 0 <= int(size.stdout) <= 32000000:
            raise ValueError('engineering object exceeds bound')
        value = read('show', head+':'+path)
        if value.returncode:
            return None
        if len(value.stdout) != int(size.stdout):
            raise ValueError('local engineering object size changed')
        return value.stdout
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError('engineering identity child killed and reaped') from exc


def _read_bytes(root, client, state, head, path):
    allowed = (path in engine.TRANSPORT_FILES | original.frozen.FIXED |
               {original.frozen.f.REGISTRATION, original.frozen.SOURCE, engine.MANIFEST}
               or re.fullmatch(re.escape(original.frozen.f.BASE)+r'/(events|scores)/[0-9]+_(formal|canary)\.json', path))
    if not isinstance(head, str) or not dispatch.HEX.fullmatch(head) or not allowed:
        raise ValueError('unsafe pinned engineering read')
    cache = state.setdefault('engineering_routing_bytes', {})
    key = (head, path)
    if key in cache:
        return cache[key]
    raw = _local_git(root, head, path, client.end)
    if raw is None:
        try:
            value = client.api('/contents/'+path+'?'+urlencode({'ref':head}))
        except HTTPError as exc:
            if exc.code == 404 and dispatch._local_absence(head, path, client.end):
                return b''
            raise
        if (value.get('type') != 'file' or value.get('path') != path
                or not dispatch.HEX.fullmatch(str(value.get('sha', '')))
                or type(value.get('size')) is not int or not 0 <= value['size'] <= 32000000):
            raise ValueError('invalid pinned engineering metadata')
        # A matching local blob is exact immutable content, even when a moving
        # main commit is not yet available locally. This avoids oversized API
        # responses for the unchanged historical candle file.
        local = Path(root)/path
        if local.is_file() and not local.is_symlink() and local.stat().st_size == value['size']:
            candidate = local.read_bytes()
            if _blob(candidate) == value['sha']:
                raw = candidate
        if raw is None:
            blob = value
            if value.get('encoding') == 'none':
                blob = client.api('/git/blobs/'+value['sha'])
                if blob.get('sha') != value['sha'] or blob.get('size') != value['size']:
                    raise ValueError('pinned engineering blob metadata changed')
            if blob.get('encoding') != 'base64' or not isinstance(blob.get('content'), str):
                raise ValueError('pinned engineering bytes unavailable')
            raw = base64.b64decode(blob['content'].replace('\n', ''), validate=True)
        if len(raw) != value['size'] or _blob(raw) != value['sha']:
            raise ValueError('pinned engineering bytes do not match blob')
    while cache and (len(cache) >= 192 or sum(map(len, cache.values()))+len(raw) > CACHE_BYTES):
        cache.pop(next(iter(cache)))
    if len(raw) > CACHE_BYTES:
        return raw
    cache[key] = raw
    return raw


def _deadline(choice, view):
    p = view['policy'];purpose = choice['purpose'];f = original.frozen.f
    if purpose in ('source_witness', 'issue'):
        return min(view['slot']+timedelta(seconds=p['issue_window_seconds']), f.utc(p['observation_end_utc']))
    events = {event['event_id']:event for event in view['events']}
    if purpose not in ('target', 'binding', 'prediction_witness') or not choice['event_ids']:
        raise ValueError('unknown original routing obligation')
    return min(f.deadline(events[key], p) if purpose == 'target' else f.utc(events[key]['target_utc'])
               for key in choice['event_ids'])


class _Routed(Exception):
    def __init__(self, result):
        self.result = result


@contextmanager
def _scope(root, client, state, capture):
    saved = (original._choice, original._server_runs, original._server_bytes,
             original._active, original.frozen.frozen.api, original.frozen.read_at,
             engine.read_identity_at)
    def choose(*args, **kwargs):
        result = saved[0](*args, **kwargs)
        capture.update(choice=result, view=args[3])
        return result
    def active(api, workflow):
        # Source/prediction audit work now belongs to the compatible publisher;
        # preserve pending original publishers and check the new publisher too.
        return saved[3](api, original.PUBLISHER) or saved[3](api, dispatch.LIVE)
    def read(head, path):
        return _read_bytes(root, client, state, head, path)
    with engine.context():
        original._choice = choose
        original._server_runs = lambda api, extra=(): engine.runs_server(extra)
        original._server_bytes = lambda root, ignored, state, head, path: read(head, path)
        original._active = active
        original.frozen.frozen.api = client.api
        original.frozen.read_at = read
        engine.read_identity_at = read
        try:
            yield
        finally:
            (original._choice, original._server_runs, original._server_bytes,
             original._active, original.frozen.frozen.api, original.frozen.read_at,
             engine.read_identity_at) = saved


def route_r2(root, publication, state, *, clock=None, client=None):
    original._main_only()
    clock = clock or (lambda:datetime.now(timezone.utc))
    bounded = _Client(client or dispatch.Client())
    capture = {}
    class RouteClient:
        api = bounded.api
        def request(self, path, payload=None):
            if payload is None:
                return bounded.api(path)
            if path not in ('/actions/workflows/'+original.AUDIT+'/dispatches',
                            '/actions/workflows/'+original.PUBLISHER+'/dispatches'):
                raise ValueError('unexpected original router mutation')
            choice, view = capture['choice'], capture['view']
            raw = _read_bytes(root, bounded, state, view['head'], original.frozen.SOURCE)
            expected = {original.frozen.SOURCE:_blob(raw)}
            expected[dispatch.ENGINEERING_MANIFEST] = dispatch.committed_manifest_blob(root)
            for key in choice.get('event_ids', []):
                if key in view['hashes']:
                    expected[original.frozen.f.BASE+'/events/'+key+'.json'] = view['hashes'][key]
            result = dispatch.dispatch_bound(dispatch.LIVE, expected, _deadline(choice, view),
                client=bounded, clock=clock, mode='publish', state=state.setdefault('engineering_bound_dispatches', {}))
            raise _Routed({**result, 'workflow':dispatch.LIVE, 'purpose':choice['purpose'],
                'event_ids':choice.get('event_ids', []), 'existing_canary_count':view['existing_canary_count'],
                'witness_or_forecast_verified':False})
    with _scope(Path(root), bounded, state, capture):
        try:
            return original.route_r2(root, publication, state, clock=clock, client=RouteClient())
        except _Routed as outcome:
            return outcome.result
