# Failure handling and recoverable backups

Approved scope: the user requested implementation on the latest local main after reviewing the in-chat solution. Implement audit items 6 and 7 only. Do not change cost/resource quotas, rate limits, storage quotas, or the photo queue capacity. Preserve all pre-existing uncommitted work.

## Task execution

The legacy workflow needs a transactionally exclusive queued-to-running claim. Persist the preparation/submission/query/download phase, remote task ID and result URL. Persist submitting before any potentially accepted POST; persist the ID before polling and the URL before download. An ambiguous submission with no durable ID must never automatically POST again. Safe local preparation may resume after process restart. Existing remote tasks resume by query/download. Same key with different input is a conflict. Startup must discover interrupted and queued legacy work without creating concurrent recovery dispatchers.

Portrait processing and uncertain-result lookup receive durable query windows: at most 60 logical query attempts or 1800 seconds, whichever occurs first. Reserve each attempt before network work. Consecutive query failures back off 5, 15, 30, then 60 seconds. Restart preserves the window. Expiry stops automatic polling, exposes a public stopped state/message, and permits an explicit fresh query window without another CreateAsset for ambiguous/already accepted submissions. Preserve prior window history. Do not change the 50-photo admission limit.

## Backup and restore

Support a quiesced snapshot of the entire configured storage root, excluding backup destinations and documented reconstructible caches/temporary artifacts. Cover all tenants, all SQLite databases using Connection.backup(), source media, completed outputs, continuation bases, workflow media, root private configuration, and explicitly configured deployment files. Include independent workflow/database paths through named roots or reject uncovered/unsupported backends clearly. PostgreSQL must fail closed until a tested backend is implemented; never silently claim complete backup.

Snapshots carry schema/version, timestamp, source roots, code revision, file sizes/hashes, and database inventory. Verify database integrity, manifest/file consistency and referenced local media before declaring success. Reject symlinks/path escapes. Publish atomically; interrupted or corrupt snapshots are not usable successes. Restore only into fresh explicit directories, never overwrite existing user data; remap exact root-boundary absolute paths, restore permissions, and verify before activation.

Disaster restoration is different from ordinary restart: write a durable restore hold, block external/background work and write APIs until explicit operator reconciliation; quarantine all nonterminal task families. Do not automatically resubmit a queued record restored from an old snapshot. Track tasks potentially accepted after the snapshot externally during the runbook reconciliation. No paid API calls in a drill.

Encrypt archived snapshots before uploading with independent backup credentials to a dedicated TOS location. Read back or verify strong checksum remotely before publishing a success receipt. Supply download/verify/restore commands and a 7 daily / 4 weekly / 3 monthly retention selection; deletion is explicit and restricted to verified backup objects, never application data. Application credentials must not manage backup objects. Encryption keys are supplied out of band and excluded from snapshots.

Provide systemd service/timer templates for daily 03:00 Asia/Shanghai backup and freshness checks, plus an operator-visible failure/freshness status and configurable notification hook. Do not install units or use real cloud credentials during this local task. Local backups are distinct from remotely verified backups. Targets: RPO 24 hours and RTO 4 hours; validate through drills, do not claim production compliance from synthetic tests.

## Verification

Concurrency must produce one accepted POST per logical submission. Test crash boundaries, stale/mismatched idempotency keys, bounded polling and restart, explicit check-only retry. Exercise backup/restore on real temporary SQLite databases plus media and configuration, corruption, traversal, wrong keys, remote upload/readback failures, retention boundaries, restore hold, and fresh target refusal. A full local synthetic drill must restore database records and file bytes with no external network. Production installation and real offsite verification remain deployment steps.
