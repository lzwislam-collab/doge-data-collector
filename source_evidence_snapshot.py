#!/usr/bin/env python3
"""Supplemental evidence inventory. Local append-only storage is not WORM.

Availability starts at actual capture, never a source's historical observation
date. This module does not grant permission to fit weights or alter forecasts.
"""
import argparse
import csv
import hashlib
import io
import json
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path

DEFAULT_STORAGE_NAMESPACE = 'data/evidence_snapshot'
PLUS_STORAGE_NAMESPACE = 'data/plus_evidence_v1_0/sources'
VERSION_ID = 'DOGE_PLUS_EVIDENCE_V1_0_20261012'


def storage_path(root, storage_namespace=None):
    namespace = DEFAULT_STORAGE_NAMESPACE if storage_namespace is None else str(storage_namespace)
    parts = namespace.replace('\\', '/').split('/')
    if (not parts or parts[0] != 'data' or any(p in ('', '.', '..') or ':' in p for p in parts)
            or len(parts) < 2):
        raise ValueError('unsafe storage namespace')
    path = (Path(root)/Path(*parts)).resolve()
    if not path.is_relative_to(Path(root).resolve()/'data'):
        raise ValueError('unsafe storage namespace')
    for child in ('blobs', 'manifests', 'reports', 'retention_exports'):
        if not (path/child).resolve().is_relative_to(path):
            raise ValueError('unsafe storage namespace child')
    return path, '/'.join(parts)


SOURCES = {
    'onchain': (1, 'data/onchain/metrics_latest.json', 'collected_at_utc', 36),
    'confirmed_whales': (1, 'data/whales_blocks/latest.json', 'ended_at_utc', 2),
    'utxo_candidate': (1, 'data/stage1/rolling_por_utxo_latest.json', 'generated_at_utc', 36),
    'schedule_evidence': (1, 'data/stage1/schedule_evidence.json', 'generated_at_utc', 10),
    'sampled_liquidations': (2, 'data/liquidations/latest.json', 'ended_at_utc', 3),
    'liquidity_snapshot': (2, 'data/liquidity/latest.json', 'collected_at_utc', 3),
    'etf_report': (3, 'data/etf/latest.json', 'ended_at_utc', 96),
    'etf_flows': (3, 'data/etf/daily_flows.csv', 'first_seen_utc', 96),
    'macro': (4, 'data/macro/latest.json', 'ended_at_utc', 48),
    'policy_calendar': (4, 'data/policy_events/latest.json', 'ended_at_utc', 48),
    'history': (5, 'data/history/latest.json', 'ended_at_utc', 36),
    'forward_audit': (5, 'data/stage5/latest.json', 'generated_at_utc', 36),
    'error_review': (5, 'data/stage5/error_review/latest.json', 'generated_at_utc', 36),
}
LIMITATIONS = {
    'ENTITY': 'Whale transactions and UTXO candidates do not establish entity ownership or verified exchange netflows.',
    'GLOBAL_LIQUIDATIONS': 'Sampled venue notices and short listening windows do not establish whole-market or full-day liquidation totals.',
    'ETF_VINTAGES': 'Trade date is not availability. No pre-first-seen training or complete historical publication-vintage claim.',
    'MACRO_CAUSAL': 'Observation dates and scheduled calendars do not establish release-time surprises or causal DOGE effects.',
    'TRAINED_WEIGHTS': 'Five pillars are evidence inventory only. No new fitted coefficients, 70% accuracy, or trading edge authorized.',
    'LOCAL_RETENTION': 'Local exclusive writes, hashes and predecessor links detect some mutations and missing predecessors. Deletion of the newest suffix or whole archive cannot be proven locally; independent retention lock is not established.',
}


def clock_utc():
    return datetime.now(timezone.utc)


def utc(value):
    parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ValueError('explicit UTC timestamp required')
    return parsed


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def exclusive(path, raw):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('xb') as stream:
        stream.write(raw)


def prior_versions(root, now, storage_namespace=None):
    """Check captured bytes before reusing a version's earlier availability."""
    storage, namespace = storage_path(root, storage_namespace)
    versions = {}
    manifests = {}
    predecessors = set()
    supporting_paths = set()
    for path in sorted((storage/'manifests').glob('*.json')):
        raw_manifest = path.read_bytes()
        record = json.loads(raw_manifest)
        relative = str(path.relative_to(root)).replace('\\', '/')
        manifests[relative] = digest(raw_manifest)
        previous = record.get('previous_manifest_path')
        if previous:
            predecessor = (root/previous).resolve()
            if not predecessor.is_relative_to((storage/'manifests').resolve()):
                raise ValueError('unsafe predecessor path')
            if not predecessor.exists():
                raise ValueError('missing predecessor manifest')
            if digest(predecessor.read_bytes()) != record.get('previous_manifest_sha256'):
                raise ValueError('predecessor manifest hash mismatch')
            if previous == relative:
                raise ValueError('self-linked predecessor')
            predecessors.add(previous)
        if record.get('storage_namespace', DEFAULT_STORAGE_NAMESPACE) != namespace:
            raise ValueError('manifest namespace mismatch')
        captured = utc(record['captured_at_utc'])
        if captured > now:
            raise ValueError('archive capture time is in future')
        supporting_paths.update(record.get('supporting_evidence', {}))
        evidence_items = list(record['sources'].items()) + list(record.get('supporting_evidence', {}).items())
        for name, item in evidence_items:
            if not item.get('sha256'):
                continue
            archive = (root/item['archive_path']).resolve()
            if not archive.is_relative_to((storage/'blobs').resolve()):
                raise ValueError('unsafe archive path')
            if digest(archive.read_bytes()) != item['sha256']:
                raise ValueError('archive content hash mismatch')
            first = utc(item['first_seen_utc'])
            if first > captured:
                raise ValueError('availability after capture')
            key = (name, item['sha256'])
            # Derive availability from actual recorded captures, not a mutable
            # per-source claim about when the content was first observed.
            versions[key] = min(versions.get(key, captured), captured)
    tips = set(manifests)-predecessors
    if manifests and len(tips)!=1:
        raise ValueError('ambiguous predecessor chain')
    tip = next(iter(tips)) if tips else None
    return versions, tip, manifests.get(tip), supporting_paths


def supporting_files(root, storage_namespace=None):
    """Archive public operational evidence, without interpreting its timestamps."""
    _, namespace = storage_path(root, storage_namespace)
    paths = {'data/direction_v42/forward_registry.csv', 'data/direction_v42/forward_scores.csv',
             'data/stage5/pre_registration.json', 'data/quote_evidence_v2/bindings.csv',
             'data/p0_operations/publication_latest.json'}
    for path in (root/'data').rglob('*'):
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        if (relative.startswith(namespace+'/') or relative.startswith('data/evidence_snapshot/') or
                relative.startswith(PLUS_STORAGE_NAMESPACE+'/')):
            continue
        name = path.name
        if (('protocol' in name and name.endswith('.json')) or 'pre_registration' in name
                or 'registry' in name or 'scores' in name or name.endswith('.activation.json')
                or relative.startswith('data/quote_evidence_v2/')
                or relative.startswith('data/p0_operations/')
                or relative == 'data/forward_execution_evidence/observations.jsonl'
                or relative.startswith('data/plus_evidence_v1_0/execution/')):
            paths.add(relative)
    return paths


def timestamps(raw, path, field):
    if path.endswith('.csv'):
        reader = csv.DictReader(io.StringIO(raw.decode('utf-8-sig')))
        if field not in (reader.fieldnames or []):
            raise ValueError('source timestamp column missing')
        rows = list(reader)
        if not rows:
            raise ValueError('source has no timestamped rows')
        values = [r[field] for r in rows]
        return values, 'snapshot_research_only'
    data = json.loads(raw.decode('utf-8-sig'))
    if not isinstance(data, dict) or not data.get(field):
        raise ValueError('source timestamp missing')
    return [data[field]], data.get('status', 'unknown')


def capture(root, cutoff=None, storage_namespace=None):
    root = Path(root).resolve()
    storage, namespace = storage_path(root, storage_namespace)
    now = utc(clock_utc().isoformat())
    cutoff = now if cutoff is None else utc(cutoff.isoformat())
    if cutoff > now:
        raise ValueError('report cutoff cannot be future')
    versions, previous, previous_sha, known_support = prior_versions(root, now, namespace)
    record = {'schema_version': 2, 'storage_namespace': namespace,
              'version_id': VERSION_ID if namespace == PLUS_STORAGE_NAMESPACE else ('LEGACY_EVIDENCE_SNAPSHOT' if namespace == DEFAULT_STORAGE_NAMESPACE else namespace), 'captured_at_utc': now.isoformat(),
              'report_cutoff_utc': cutoff.isoformat(), 'pillar_count': 5,
              'trained_weights_authorized': False, 'local_append_only_not_worm': True,
              'shared_cutoff_does_not_mean_shared_refresh': True,
              'previous_manifest_path': previous, 'previous_manifest_sha256': previous_sha,
              'limitations': LIMITATIONS, 'sources': {}, 'supporting_evidence': {},
              'supporting_scope': 'Byte-exact operational registry/score/protocol/publication/quote/execution archives only; no semantic source timestamps inferred'}
    for name, (pillar, path, field, hours) in SOURCES.items():
        item = {'pillar': pillar, 'source_file': path, 'timestamp_field': field,
                'max_age_hours': hours, 'allowed_for_model': False,
                'available_as_of_cutoff': False}
        record['sources'][name] = item
        source = root/path
        if not source.exists():
            item['state'] = 'missing'
            continue
        raw = source.read_bytes()
        sha = digest(raw)
        archive = namespace+'/blobs/'+sha+'.bin'
        blob = root/archive
        if blob.exists():
            if digest(blob.read_bytes()) != sha:
                raise ValueError('archive content hash mismatch')
        else:
            try:
                exclusive(blob, raw)
            except FileExistsError:
                if digest(blob.read_bytes()) != sha:
                    raise ValueError('archive content hash mismatch')
        first = versions.get((name, sha), now)
        item.update(sha256=sha, byte_count=len(raw), archive_path=archive,
                    first_seen_utc=first.isoformat())
        try:
            values, status = timestamps(raw, path, field)
            observed = [utc(v) for v in values]
            newest, oldest = max(observed), min(observed)
            age = (cutoff-newest).total_seconds()/3600
            future = newest > cutoff
            stale = age > hours
            item.update(source_status=status, source_timestamps_utc=[v.isoformat() for v in observed],
                        source_utc=newest.isoformat(), oldest_source_utc=oldest.isoformat(),
                        age_hours=round(age, 4), future_timestamp=future, stale=stale)
            healthy = status in ('ok', 'ok_sample_complete', 'all_evidence_matched', 'snapshot_research_only')
            item['state'] = 'future' if future else 'stale' if stale else 'current' if healthy else 'partial_or_unhealthy'
            item['available_as_of_cutoff'] = first <= cutoff and not future
        except (ValueError, KeyError, TypeError, UnicodeError) as exc:
            item.update(state='invalid', error=str(exc))
    for path in sorted(supporting_files(root, namespace) | known_support):
        item = {'source_file': path, 'state': 'missing', 'allowed_for_model': False,
                'semantic_timestamp_inferred': False,
                'evidence_role': 'current_version_execution' if path.startswith('data/plus_evidence_v1_0/execution/') else 'archived_context'}
        record['supporting_evidence'][path] = item
        source = root/path
        if not source.exists():
            continue
        raw = source.read_bytes()
        sha = digest(raw)
        archive = namespace+'/blobs/'+sha+'.bin'
        blob = root/archive
        if blob.exists():
            if digest(blob.read_bytes()) != sha:
                raise ValueError('supporting archive content hash mismatch')
        else:
            exclusive(blob, raw)
        first = versions.get((path, sha), now)
        item.update(state='captured_bytes_only', sha256=sha, byte_count=len(raw), archive_path=archive,
                    first_seen_utc=first.isoformat(), available_as_of_cutoff=first <= cutoff)
    complete = (all(s['state']=='current' for s in record['sources'].values())
                and all(s['state']!='missing' for s in record['supporting_evidence'].values()))
    record['status'] = 'complete_inventory_with_limits' if complete else 'incomplete_or_stale_inventory'
    identifier = now.strftime('%Y%m%dT%H%M%S%fZ')+'_'+uuid.uuid4().hex
    manifest = storage/'manifests'/f'{identifier}.json'
    report = storage/'reports'/f'{identifier}.md'
    record['manifest_path'] = str(manifest.relative_to(root)).replace('\\', '/')
    record['report_path'] = str(report.relative_to(root)).replace('\\', '/')
    exclusive(manifest, (json.dumps(record, ensure_ascii=False, indent=2)+'\n').encode('utf-8'))
    lines = ['# Supplemental five-pillar source evidence', '', f"Status: {record['status']}",
             f'Captured: {now.isoformat()}; report cutoff: {cutoff.isoformat()}',
             '', 'Common cutoff does not imply simultaneous source refresh. First seen means captured availability; observation dates cannot backdate it.',
             '', '| Source | State | Observed source UTC | First captured UTC | Threshold hours |', '|---|---|---|---|---|']
    for name, item in record['sources'].items():
        lines.append(f"| {name} | {item['state']} | {item.get('source_utc', 'unavailable')} | {item.get('first_seen_utc', 'unavailable')} | {item['max_age_hours']} |")
    lines.extend(['', '## Supporting operational evidence', '', record['supporting_scope'], ''])
    lines.extend(f"- {p}: {v['state']}; first captured {v.get('first_seen_utc', 'unavailable')}" for p,v in record['supporting_evidence'].items())
    lines.extend(['', '## Limits', '']+[f'- {k}: {v}' for k,v in LIMITATIONS.items()])
    exclusive(report, ('\n'.join(lines)+'\n').encode('utf-8'))
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument('--storage-namespace', help='Safe relative storage root; defaults to legacy namespace')
    parser.add_argument('--cutoff', help='Optional UTC audit cutoff; never backdates captured availability')
    args = parser.parse_args()
    result = capture(args.root, utc(args.cutoff) if args.cutoff else None, args.storage_namespace)
    print(json.dumps({k: result[k] for k in ('status', 'captured_at_utc', 'manifest_path', 'report_path')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
