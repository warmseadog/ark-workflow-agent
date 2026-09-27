# Accessories and virtual library review — 2026-09-27

Reviewed the implementation against `2026-09-27-accessories-virtual-library.md`, including all tracked implementation diffs, new reference-role definitions, virtual UI, and focused tests. No application code was changed. No provider calls or remote-server operations were made.

## Findings

1. **[P2] Reconcile an existing photo record after a verified import.** `app/portrait_library.py:225–227` uses `INSERT OR IGNORE`, but the table is unique on `(account, person_id, sha256)`. If the same image has an existing failed, uncertain, processing, or queued upload record, importing a freshly verified Active official asset leaves that old record and remote ID unchanged. `enqueue()` subsequently returns that stale row, so the imported photo can remain unusable; an existing queued record can also still create an unnecessary official asset. Reconcile the matching row with the freshly verified remote ID/status, coordinating with the photo worker so an in-flight result cannot overwrite the reconciliation. Isolated reproduction created a failed row and then called `record_verified_import(..., 'asset-verified')`: the row remained `{status: failed, remote_id: None, message: old rejection}`.

2. **[P2] Preserve reference-role mapping when resuming a task.** `app/video_provider.py:142–143` returns to polling before the new role map at `app/video_provider.py:164–165` is built. `app/production_worker.py:46–66` also only loads references for an initial submission. After a restart or manual resume, an asynchronous rejection naming `content[4]` therefore reports generic `content[4]` instead of the actual scene, hairstyle, or accessory role. Persist or rebuild the reference-role manifest from the saved snapshot before resuming, without requiring the original files to exist. Isolated mocked recovery reproduced the fallback `content[4]` label.

## Scope and checks

- Official imports call GetAsset and verify Active/Image/project plus GetAssetGroup type/project before local import; generation repeats remote verification with explicit AIGC type. Existing real-person defaults remain LivenessFace.
- Account fingerprints and explicit person type are persisted for generation authorization; ambiguous virtual group creation is durably deduplicated by request ID and same pending name.
- Accessory draft validation, optional-enabled count, worker forwarding, and initial-submission reference order were inspected. The fixture tests cover disabled persistence and initial content-index mapping.
- Reviewed the latest selector tabs, admin counters/history, import integration, and lock guard. No additional concrete blocker identified in those paths.
- This review did not independently run the full regression suite or paid/live provider validation; root agent owns those checks. Findings are based on code paths and the two isolated reproductions above.

## Scoped re-review addendum
Both findings resolved and independently re-reviewed: reconciled imports lock before transaction and UPSERT retains job ID; recovered role map rebuilt without files before recovery branches. No remaining blocker in fixes.
