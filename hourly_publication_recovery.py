"""One fresh optional-source retry after the single-writer service has stopped.

Does not issue or score forecasts. Frozen publication validation and the source
freshness/boundary gate remain in publication_recovery and its publisher.
"""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

import publication_recovery as publication


def write_receipt(path, result):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    with temporary.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, indent=2)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def recover(root, refresh_fn, publish_fn):
    root = Path(root)
    receipt_path = 'data/hourly_publication_recovery_v1/' + uuid.uuid4().hex + '.json'
    result = {'schema_version': 1, 'recovered': False, 'receipt_path': receipt_path,
              'started_at_utc': datetime.now(timezone.utc).isoformat(),
              'scope': 'fresh optional source only; no forecast or score repair'}
    output = os.environ.pop('GITHUB_OUTPUT', None)
    prior_verify = os.environ.get('DOGE_VERIFY_HOURLY_PUBLICATION')
    prior_failed = os.environ.get('DOGE_HOURLY_REFRESH_FAILED')
    try:
        if output:
            with open(output, 'a', encoding='utf-8') as stream:
                stream.write('recovered=false\n')
        publication.base.command(root, 'fetch', 'origin', 'main')
        remote_ids = publication.ids(publication.base.command(root, 'show', 'FETCH_HEAD:' + publication.REG))
        current_ids = publication.ids((root / publication.REG).read_bytes())
        if current_ids != remote_ids:
            raise ValueError('unpublished or mismatched original registry; recovery cannot publish originals')
        refresh_fn(root)
        os.environ['DOGE_VERIFY_HOURLY_PUBLICATION'] = '1'
        os.environ['DOGE_HOURLY_REFRESH_FAILED'] = '0'
        published = publication.publish(root, publish_fn)
        if (published.get('hourly_source_public_readback') is not True
                or published.get('hourly_source_status') != 'verified_remote_readback'
                or published.get('new_predictions') is not False
                or published.get('new_ids') != []):
            raise ValueError('source recovery not verified without new originals')
        result.update(recovered=True, publication=published)
    except Exception as exc:
        result.update(error=type(exc).__name__ + ': ' + str(exc),
                      completed_at_utc=datetime.now(timezone.utc).isoformat())
        write_receipt(root / receipt_path, result)
        raise
    finally:
        for key, previous in [('DOGE_VERIFY_HOURLY_PUBLICATION', prior_verify),
                              ('DOGE_HOURLY_REFRESH_FAILED', prior_failed)]:
            if previous is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = previous
        if output is not None:
            os.environ['GITHUB_OUTPUT'] = output
    result['completed_at_utc'] = datetime.now(timezone.utc).isoformat()
    write_receipt(root / receipt_path, result)
    # A durable receipt and exact remote readback must both precede the success output.
    if output:
        with open(output, 'a', encoding='utf-8') as stream:
            stream.write('recovered=true\n')
    return result


def main():
    import paired_forward_cohort as pair
    root = Path(__file__).resolve().parent
    def refresh(root):
        env = dict(os.environ, DOGE_HOURLY_PUBLICATION_REFRESH='1')
        subprocess.run([sys.executable, 'okx_hourly_research_history.py'],
                       cwd=root, env=env, check=True, timeout=45)
    def publish(root):
        if pair.publish(root) != 0:
            raise ValueError('paired publication rejected')
        return json.loads((root / pair.BASE / 'publication_latest.json').read_bytes())
    print(json.dumps(recover(root, refresh, publish)))


if __name__ == '__main__':
    main()
