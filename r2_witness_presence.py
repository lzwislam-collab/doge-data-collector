"""Scoped execution adapter for the unchanged frozen R2 runtime.

A synchronous run/main invocation keeps the original guards and CLI. Only a
404 for an exact event/score path proven absent from a locally known immutable
Git tree is classified as a rejected witness candidate. This module does not
install or authorize a workflow integration.
"""
from contextlib import contextmanager
from urllib.error import HTTPError

import forward_readiness as readiness
import phase2_forward_runtime_v2 as r


@contextmanager
def witness_presence():
    """Temporarily apply the existing presence rule; always restore the reader."""
    original = r.read_at

    def read_candidate(head, path):
        try:
            return original(head, path)
        except HTTPError as exc:
            if exc.code == 404 and readiness._verified_candidate_absence(head, path):
                raise readiness.WitnessObjectAbsent(
                    'immutable witness event/score object absent') from exc
            raise

    r.read_at = read_candidate
    try:
        yield
    finally:
        r.read_at = original


def run(mode):
    with witness_presence():
        return r.run(mode)


def main():
    with witness_presence():
        return r.main()


if __name__ == '__main__':
    main()
