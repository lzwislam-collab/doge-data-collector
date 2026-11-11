"""Versioned readiness entry point with original current/historical distinctions."""
from contextlib import contextmanager
import forward_readiness as original
import r2_engineering_runtime as engine
import r2_engineering_routing as routing
import r2_engineering_dispatch as dispatch


@contextmanager
def dispatch_context():
    """Redirect only R2 engineering work; original18 and other handlers stay exact."""
    saved = original.dispatch_once, original.trusted_completion
    state = {}
    client = None
    def send(name):
        nonlocal client
        if name not in (routing.original.AUDIT, routing.original.PUBLISHER):
            return saved[0](name)
        if client is None:
            client = dispatch.Client()
        return routing.route_r2(original.ROOT, None, state, client=client)
    def trusted(row, jobs):
        if row.get('path', '').split('@')[0] != engine.ENGINEERING_AUDIT:
            return saved[1](row, jobs)
        repo = engine.f.old.REPO
        if (row.get('repository', {}).get('full_name') != repo
                or row.get('head_repository', {}).get('full_name') != repo
                or row.get('head_branch') != 'main' or row.get('status') != 'completed'
                or row.get('conclusion') != 'success' or row.get('pull_requests')
                or row.get('event') not in ('push', 'workflow_dispatch', 'workflow_run')):
            return False
        audits = [job for job in jobs if job.get('name') == 'audit']
        if (len(audits) != 1 or audits[0].get('run_id') != row.get('id')
                or audits[0].get('status') != 'completed' or audits[0].get('conclusion') != 'success'):
            return False
        return all(any(step.get('name') == name and step.get('conclusion') == 'success'
                       for step in audits[0].get('steps', [])) for name in (
            'All tests and original frozen history',
            'Original frozen registration and real publication replay through engineering v1',
            'Preserve full engineering audit and failures'))
    original.dispatch_once, original.trusted_completion = send, trusted
    try:
        yield
    finally:
        original.dispatch_once, original.trusted_completion = saved


def run(mode):
    with engine.context(), dispatch_context():
        return engine.versioned_result(original.run(mode))


def main():
    saved = original.run
    with engine.context(), dispatch_context():
        original.run = lambda mode: engine.versioned_result(saved(mode))
        try:
            return original.main()
        finally:
            original.run = saved


if __name__ == '__main__':
    main()
