"""Equivalent full-history state checks at the live publication boundary.

The original publisher and frozen validator are unchanged. Only Git transport
for history_receipts differs: every revision and first receipt is still checked
in its original order. Unknown dependency identities or native framing fall back
to the complete original function. No result or history survives a call.
"""
import csv
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import re
import subprocess

import forward_validation as base
import publish_forward_evidence as publisher


IMPLEMENTATION = 'runtime_publication_state_native_batch_v1'
BATCH_SIZE = 128
DEPENDENCY_BLOBS = {
    'publish_forward_evidence.py': 'd982f4f8bd278d7a1505073bc35dd71b80eeb095',
    'forward_validation.py': 'f0831c7d38581543f84a85639c6d69a7305ff382',
}
_OID = re.compile(r'(?:[0-9a-f]{40}|[0-9a-f]{64})\Z')


class _NativeCompatibility(Exception):
    pass


def _dependency_reason():
    try:
        for module, name in ((publisher, 'publish_forward_evidence.py'),
                             (base, 'forward_validation.py')):
            body = Path(module.__file__).read_bytes()
            actual = hashlib.sha1(b'blob ' + str(len(body)).encode('ascii') + b'\0' + body).hexdigest()
            if actual != DEPENDENCY_BLOBS[name]:
                return 'dependency_identity_changed'
    except (OSError, TypeError, AttributeError):
        return 'dependency_identity_unavailable'
    return None


def _deliver(diagnostics, report):
    # Reporting must never replace a real validation exception or success.
    try:
        if isinstance(diagnostics, dict):
            diagnostics.clear()
            diagnostics.update(report)
    except Exception:
        pass


def _report():
    return {'implementation': IMPLEMENTATION, 'mode': 'native_batch',
            'status': 'running', 'attempted_commit_count': 0, 'native_batch_calls': 0}


def _log_output(root, relative):
    return subprocess.run(['git', 'log', '--reverse', '--format=%H%x00%cI', '--', relative],
                          cwd=root, check=True, capture_output=True).stdout


def _batch_check_output(root, revisions, relative):
    # %(rest) binds each resolved blob to its requested commit, independently
    # from the later content frame. Whitespace paths use the original reader.
    request = ''.join(rev + ':' + relative + ' ' + rev + '\n' for rev in revisions).encode('utf-8')
    return subprocess.run(['git', 'cat-file',
                           '--batch-check=%(objectname) %(objecttype) %(objectsize) %(rest)'],
                          cwd=root, input=request, check=True, capture_output=True).stdout


def _batch_output(root, objects):
    request = ('\n'.join(objects) + '\n').encode('ascii')
    return subprocess.run(['git', 'cat-file', '--batch'], cwd=root, input=request,
                          check=True, capture_output=True).stdout


def _metadata(raw):
    try:
        lines = raw.decode('utf-8').strip().splitlines()
        entries = [line.split('\0') for line in lines]
        if any(len(parts) != 2 or not _OID.fullmatch(parts[0]) for parts in entries):
            raise _NativeCompatibility('log_framing')
        if len({parts[0] for parts in entries}) != len(entries):
            raise _NativeCompatibility('log_oid_sequence')
        return entries
    except UnicodeError as exc:
        raise _NativeCompatibility('log_encoding') from exc


def _objects(raw, revisions):
    try:
        lines = raw.decode('ascii').splitlines()
        if len(lines) != len(revisions):
            raise _NativeCompatibility('object_resolution_count')
        objects = []
        for line, revision in zip(lines, revisions):
            parts = line.split(' ')
            if (len(parts) != 4 or not _OID.fullmatch(parts[0]) or parts[1] != 'blob'
                    or not parts[2].isascii() or not parts[2].isdigit() or parts[3] != revision):
                raise _NativeCompatibility('object_resolution_identity')
            objects.append((parts[0], int(parts[2])))
        return objects
    except (UnicodeError, ValueError) as exc:
        raise _NativeCompatibility('object_resolution_framing') from exc


def _bodies(raw, objects):
    offset = 0
    bodies = []
    for oid, size in objects:
        end = raw.find(b'\n', offset)
        expected = (oid + ' blob ' + str(size)).encode('ascii')
        if end < 0 or raw[offset:end] != expected:
            raise _NativeCompatibility('blob_identity_or_order')
        start, stop = end + 1, end + 1 + size
        if stop >= len(raw) or raw[stop:stop + 1] != b'\n':
            raise _NativeCompatibility('blob_length_or_delimiter')
        body = raw[start:stop]
        algorithm = hashlib.sha1 if len(oid) == 40 else hashlib.sha256
        if algorithm(b'blob ' + str(len(body)).encode('ascii') + b'\0' + body).hexdigest() != oid:
            raise _NativeCompatibility('blob_content_hash')
        bodies.append(body)
        offset = stop + 1
    if offset != len(raw):
        raise _NativeCompatibility('unexpected_blob_tail')
    return bodies


def _native_history(root, relative, columns, current, report):
    # Preserve the original shallow check before log/history/current validation.
    if base.git(root, 'rev-parse', '--is-shallow-repository') != 'false':
        raise ValueError('full git history required')
    if not isinstance(relative, str) or any(c.isspace() or c == '\0' for c in relative):
        raise _NativeCompatibility('unsupported_path_framing')
    entries = _metadata(_log_output(root, relative))
    report['attempted_commit_count'] = len(entries)
    report['ordered_oids_sha256'] = hashlib.sha256(
        ('\n'.join(rev for rev, stamp in entries) + '\n').encode('ascii')).hexdigest()
    prior, receipts = {}, {}
    for offset in range(0, len(entries), BATCH_SIZE):
        chunk = entries[offset:offset + BATCH_SIZE]
        revisions = [rev for rev, stamp in chunk]
        report['native_batch_calls'] += 1
        objects = _objects(_batch_check_output(root, revisions, relative), revisions)
        bodies = _bodies(_batch_output(root, [oid for oid, size in objects]), objects)
        for (rev, stamp), raw in zip(chunk, bodies):
            # subprocess text=True uses universal newlines before stdout.strip().
            body = raw.decode('utf-8').replace('\r\n', '\n').replace('\r', '\n').strip()
            reader = csv.DictReader(io.StringIO(body))
            if reader.fieldnames != columns:
                raise ValueError('historic schema revised: ' + relative)
            snap = {}
            for row in reader:
                fid = row['forecast_id']
                if not fid or fid in snap:
                    raise ValueError('duplicate historical ID')
                snap[fid] = row
            if any(snap.get(k) != v for k, v in prior.items()):
                raise ValueError('committed record revised: ' + relative)
            # Original timestamp parsing comes after this revision's CSV and
            # append checks. Bad native metadata replays the entire original.
            try:
                if not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}[+-][0-9]{2}:[0-9]{2}', stamp):
                    raise _NativeCompatibility('commit_time_format')
                committed = datetime.fromisoformat(stamp).astimezone(timezone.utc).isoformat()
            except (ValueError, OverflowError) as exc:
                raise _NativeCompatibility('commit_time_format') from exc
            for fid in snap.keys() - prior.keys():
                receipts[fid] = {'commit_sha': rev, 'committed_at_utc': committed}
            prior = snap
    if any(current.get(k) != v for k, v in prior.items()):
        raise ValueError('current record revised: ' + relative)
    return receipts


def history_receipts(root, relative, columns, current, diagnostics=None):
    """Return original complete receipts or its error, without cross-call cache."""
    report = _report()
    report['relative'] = relative if isinstance(relative, str) else '<non-string>'
    succeeded = False
    try:
        reason = _dependency_reason()
        if reason is None:
            try:
                result = _native_history(root, relative, columns, current, report)
            except (_NativeCompatibility, subprocess.CalledProcessError, OSError) as exc:
                reason = str(exc) if isinstance(exc, _NativeCompatibility) else 'git_read_error'
            else:
                succeeded = True
                return result
        report.update(mode='legacy_full_fallback', fallback_reason=reason)
        # Even a late chunk failure replays every original revision from scratch.
        result = base.history_receipts(root, relative, columns, current)
        succeeded = True
        return result
    finally:
        report['status'] = 'ok' if succeeded else 'error'
        _deliver(diagnostics, report)


def validate_state(root, diagnostics=None):
    """Keep the original complete publication validation and exception order."""
    report = _report()
    report['histories'] = []
    succeeded = False
    try:
        reason = _dependency_reason()
        if reason is not None:
            report.update(mode='legacy_full_fallback', fallback_reason=reason)
            result = publisher.validate_state(root)
            succeeded = True
            return result
        import forward_validation_v2 as v2
        frozen, evidence = publisher.frozen, publisher.evidence
        protocol = json.loads((root / v2.PROTOCOL).read_text())
        v2.check_protocol(protocol)
        v2.check_code(root, protocol)
        registry = frozen.read_rows(root / base.REG, frozen.FCOLS)
        scores = frozen.read_rows(root / base.SCORES, frozen.SCOLS)
        bindings = frozen.read_rows(root / evidence.BINDINGS, evidence.BCOLS)
        base.validate_scores(registry, scores, publisher.datetime.now(publisher.timezone.utc))
        for path, columns, rows in ((base.REG, frozen.FCOLS, registry),
                                    (base.SCORES, frozen.SCOLS, scores),
                                    (evidence.BINDINGS, evidence.BCOLS, bindings)):
            history_report = {}
            try:
                history_receipts(root, path, columns, rows, diagnostics=history_report)
            finally:
                report['histories'].append(history_report)
                report['attempted_commit_count'] += history_report.get('attempted_commit_count', 0)
                report['native_batch_calls'] += history_report.get('native_batch_calls', 0)
                if history_report.get('mode') == 'legacy_full_fallback':
                    report.update(mode='legacy_full_fallback',
                                  fallback_reason=history_report.get('fallback_reason', 'history_fallback'))
        effective = frozen.utc(json.loads(
            (root / 'data/stage5/forward_validation_protocol_v2.json').read_text())['effective_from_utc'])
        for fid, row in registry.items():
            if frozen.utc(row['issued_at_utc']) >= effective:
                evidence.verify_bound(root, row, scores.get(fid), bindings)
        succeeded = True
    finally:
        report['status'] = 'ok' if succeeded else 'error'
        _deliver(diagnostics, report)


def emit_diagnostics(diagnostics, validation_succeeded):
    """Bounded stdout evidence; no payloads, file writes or validation effects."""
    try:
        keys = ('implementation', 'mode', 'status', 'attempted_commit_count',
                'native_batch_calls', 'ordered_oids_sha256', 'fallback_reason', 'relative')
        row = {key: diagnostics[key] for key in keys if key in diagnostics}
        if 'histories' in diagnostics:
            row['histories'] = [{key: item[key] for key in keys if key in item}
                                for item in diagnostics['histories'][:3]]
        row.update(kind='runtime_publication_state_validation', implementation=IMPLEMENTATION,
                   module_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                   validation_status='ok' if validation_succeeded else 'error')
        print(json.dumps(row, sort_keys=True, allow_nan=False), flush=True)
    except Exception:
        pass
