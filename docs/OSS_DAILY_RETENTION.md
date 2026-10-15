# OSS daily inventory retention

The existing daily PIT package is copied to a real user-managed external OSS BucketWorm archive. No scheduler, collector, frozen forecast or investment rule is changed. The new workflow follows completion of the existing hourly research workflow; it only uploads a new published daily inventory. Unchanged inventory runs explicitly skip without claiming fresh readback.

The dedicated role trusts only `repo:lzwislam-collab/doge-data-collector:ref:refs/heads/main`, via GitHub OIDC and 1800-second STS credentials. Permissions allow bucket owner/WORM/version metadata reads, and Put/Get/GetVersion under the single `doge/evidence/` prefix. No delete, retention administration, bucket policy changes or long-lived key is used. Actual provider STS identity is read before upload.

All inventory ZIP shards and the exact index are validated by the existing `verify_inventory_bundle`. Each is stored by SHA256 with forbidden overwrite, AES256 and MD5 transport verification, then read by non-null VersionId and SHA256 checked. Owner, region, versioning and Locked WORM >=730 days are required. Existing same-name objects are pinned and reread, never silently trusted from their key. Public audit JSON records version IDs, server times, calculated BucketWorm deadlines, identity, request IDs and execution links. The private OSS objects remain the evidence; GitHub is the public receipt index.

`audit/oss_retention/latest.json` is only generated after all shards and index pass actual API readback. Failure must leave the audit unverified, even if some uploads succeeded. A successful manual run does not establish a future automatic upload; unchanged-inventory skips are not new uploads.

This protects one daily snapshot and its included files, not every forecast later that day. Upload now cannot establish historical pre-target retention. Different provider from GitHub does not prove an independent human custodian. Existing model policies are not relaxed: a 730-day lock starting 2026-10-15 does not satisfy the older fixed 2028-10-16 deadline; the per-object receipt reports this separately. Owner account closure and payment default remain external limits. No deletion/overwrite probe is performed against evidence.

Storage, GET/PUT and network readback cost money. Content-addressed objects are not re-uploaded; successful daily receipts skip repeated downloads on subsequent events. No resource pack, acceleration or KMS subscription is purchased. Budget/low-balance alerts still need live verification; they are not a hard cap.

Run `python -m unittest -q test_oss_inventory_retention` for failure boundaries. Fixtures are never cloud evidence.
