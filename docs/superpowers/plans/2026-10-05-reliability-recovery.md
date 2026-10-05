# Reliability and recovery implementation plan

> Implementation uses test-driven changes, parallel independent owners, and an independent final review. User explicitly authorized execution in the local main working directory.

**Goal:** Complete audit items 6 and 7 locally while preserving existing resource-admission edits.

**Spec:** ../specs/2026-10-05-reliability-recovery-design.md

**Architecture:** Extend existing SQLite task states rather than replace the main production executor. Add offline backup/restore and encrypted offsite transport modules plus service templates. Add one restore-hold boundary consumed by background startup and HTTP handling.

**Global constraints:** No real provider calls, live storage mutations, deployment, commits of unrelated work, or resource-policy changes. Tests disable dotenv and use temporary storage; cloud transport is mocked. Python 3.10 and Windows local tests/Linux systemd deployment.

## Task 1: Legacy execution safety

- Files: app/workflow_store.py, app/workflow_worker.py, app/workflow_router.py, app/seedance.py, app/static/workflow.js; focused tests.
- First reproduce duplicate execution, uncertain acceptance and lost queued work with failing tests.
- Implement claim_stage_task and durable provider state; keep route responses compatible.
- Add recover/start/stop functions for a single restart-safe legacy dispatcher, with explicit check-only resume endpoint.
- Root integrates lifecycle into app/main.py after interface is reported.
- Verify concurrency, all crash boundaries, recovery and old workflow/transport regressions.

## Task 2: Finite portrait query windows

- Files: app/portrait_library.py, app/portrait_router.py, app/static/portrait*.js and the minimal photo UI consumer changes; focused tests. Coordinate any production.js change with root.
- First reproduce indefinite polling, restart reset and unsafe retry behavior.
- Persist 60-attempt/1800-second windows, reserve before I/O, stop with visible state and retain historical windows.
- Explicit retry of a stopped accepted/uncertain item only starts queries, never another create.
- Verify migration of old DBs and compatibility with person preparation and existing photo UI.

## Task 3: Offline backup and restore engine

- New files: app/backup.py (snapshot/verify/restore CLI), tests/test_backup_recovery.py. Agent owns only these files.
- First write roundtrip and failure-mode tests with real temp databases/files.
- Expose create_snapshot(storage_root, destination, *, extra_roots=None, code_revision='', database_url=None, quiesced=False), verify_snapshot(snapshot), restore_snapshot(snapshot, destination, *, extra_destinations=None). Report final signatures before integration.
- Snapshot all durable state with manifest and SQLite integrity verification; fail closed on unsupported or external unconfigured database paths.
- Restore to fresh targets, remap paths, write .restore-hold.json and quarantine nonterminal work. No application imports or network.
- Validate corruption, paths/symlinks, unsupported DB, backup exclusion, incomplete snapshots, and restored file references.

## Task 4: Integration, offsite backup and operational drill

- Root owns new app/recovery_guard.py, app/backup_transport.py, deploy/ecs/backup*.py and service/timer/config templates, requirements files, app/main.py lifecycle and docs/reliability-recovery.md.
- Test restore hold (background/API deny), encryption integrity/wrong key, cloud readback failures, freshness and retention before implementing.
- Integrate offline create/verify/restore with a maintenance wrapper that stops application writers for the snapshot and always attempts to restore original service state.
- Restart service before archive encryption/upload. Only a verified remote object earns an offsite success receipt; never delete data to make space.
- Supply explicit encrypted download/recovery path and runbook, enable no live schedules or notifications.
- Run focused tests, full tests/, isolated end-to-end restore drill, then independent review and resolve findings.

## Review focus

- Concurrent same-key requests and process crash between claiming, POST and persisting provider identity.
- Poll budget survives restart and manual retry cannot turn uncertainty into another create.
- Media paths, SQLite WAL and independently configured roots survive restore together.
- Tampered archives, root overlap, symlinks, existing targets and wrong encryption keys cannot overwrite data.
- Old snapshot queued work is held before any external side effect; failed offsite verification cannot reset backup freshness.
