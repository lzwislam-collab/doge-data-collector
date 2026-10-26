# PIT marker publication after archive drain

Production run 40241638129 uploaded artifact 12620730069 and safely drained
the archive writer, but the marker-only commit's pull --rebase failed five
times because the runner still contained unstaged tracked diagnostic bytes.
The subsequent visible PIT guard failed. Upload success does not prove tail success.

Publish the already validated artifact marker using a temporary Git index
based on fetched remote main, with exactly one path replaced. Commit-tree
and ordinary non-force push leave the runner HEAD, index, and drained capture
bytes untouched. On a concurrent remote update, fetch and retry at most five
times. Refuse publication if remote preparation bytes differ from the local
preparation; require exact marker remote readback before success.

The existing preparation validation, successful artifact outputs, safe drain
condition, single writer and visible failure guards remain mandatory.
No frozen model, protocol, manifest or ledger is edited. No new trigger.

Validation: a real bare Git remote fixture preserves dirty capture bytes,
the original index and HEAD, publishes only the marker, and rejects a changed
preparation. Linux full suite and complete history integrity must pass before
deployment. Actual production tail success remains required after deployment;
PR81 conditional source recovery remains unverified when skipped.
