# Virtual library implementation report

Task 2 of 2026-09-27-accessories-virtual-library.md implemented in the approved active checkout; no commits or paid provider submissions.

## Implementation

- `portrait_people.person_type` migrates existing rows to LivenessFace; AIGC is explicit. Migration executes under BEGIN IMMEDIATE, including concurrent first imports. No AIGC item has `verified=true`.
- ArkPortraitClient has an explicit type selector, defaulting to LivenessFace. Official GetAssetGroup, ListAssetGroups, ListAssets and GetAsset use that type; CreateAssetGroup only permits AIGC. CreateAsset retains official image schema and private TOS upload.
- Durable group creation stores the request before the upstream call. Ambiguous/error responses become uncertain, never automatically resubmitted. Same request and same unresolved name deduplicate across reload and process restart. Sync can find the official group for selection; uncertain creation records remain visible for manual reconciliation rather than asserting that a name match proves creation.
- Photos retain the existing queued/uploading/submitting/processing/uncertain/failed/active state machine. AIGC group verification runs before upload and again before marking active. Generation snapshots freeze person type and reverify the current account/project, image hash, official group type and official Active asset before emitting asset://. Imported official IDs require verified metadata and never fall back to arbitrary raw images.
- Imported verified images are registered as usable local photos with a thumbnail. Successful-run count is separate from photo availability. Existing real imports, drafts, verification sessions and default client behavior remain compatible.
- Production picker has explicit real/virtual filters with selection-aware restore; the virtual library dialog supports create, sync, choose, ID import and official usable-photo listing. Existing picker provides local rename. Upload stays in the existing face uploader for the selected typed person.
- Admin overview adds shared portrait config, TOS/credential readiness, explicit read-only AIGC query, local group/photo state counts, recent failures, last-success time/model (mock labeled).

## Integration hooks

Root owns and has been notified to include `/static/virtual-library.css` and `/static/virtual-library.js` in production/studio, and JS/CSS in admin settings. Update productionPortraits.importAsset to pass `(asset.portrait.group_id, asset.person_type || 'LivenessFace')` to portraitPeople.selectGroup, and use virtual-specific success wording. Import endpoint returns top-level person_type.

API additions: POST `/api/portrait/people` {name,person_type:AIGC,request_id}; typed POST `/people/sync`; typed `/people/resolve`; GET `/assets?person_type=AIGC`; typed POST `/import`; GET `/virtual/status`; POST `/virtual/test`. Existing endpoints default to real type.

## Verification

Initial four contract tests were observed failing on missing type-aware constructor, person type persistence and durable creation. Expanded import test was observed failing because imported photos did not yet count as usable; fixed by recording only freshly verified official imports. Concurrent import regression detected and fixed a schema migration race.

Targeted suite: tests/test_virtual_library.py, tests/test_portrait_library.py, tests/test_portrait_api.py, tests/test_portrait_service.py, tests/test_portrait_sessions.py: 64 passed. Post-filter/admin targeted virtual+library suite: 16 passed. Node --check passed on both portrait-people.js and virtual-library.js. Root is conducting full-suite/browser checks.

## Official references and account limits

Primary documentation consulted: https://docs.volcengine.com/docs/ark/list-asset-groups-api?lang=zh ; https://docs.volcengine.com/docs/ark/get-asset-group-api?lang=zh ; https://docs.volcengine.com/docs/ark/list-assets-api?lang=zh ; https://docs.volcengine.com/docs/ark/get-asset-api?lang=zh . These specify AIGC/LivenessFace group separation and project/asset/group/status fields. Create API pages are JavaScript-only in the text browser; the existing CreateAsset contract is retained and CreateAssetGroup uses the official action with Name/GroupType/ProjectName, followed by strict GetAssetGroup verification.

Root reported actual read-only AIGC listing succeeds for the configured account, including non-person groups such as shoe/product collections. UI explicitly notes AIGC groups may contain non-person assets. Read access never claims creation or video-generation entitlement. No unrestricted public-person catalog endpoint was established; only metadata accessible to current credentials/project is imported. No paid upload/generation entitlement was claimed or tested by this delegated task.

Ruling: uncertain creation cannot be safely retried automatically or reconciled from a nonunique group name; retain durable uncertainty and allow official sync/selection instead. This favors avoiding duplicate upstream creation over automatic retry convenience.

## Review fixes

Fresh verified imports now acquire the same process lock as the photo worker before file validation and SQLite upsert. Existing same-account/person/hash records keep their job ID but are promoted to active with the newly verified remote ID, message, checked time and zero next-check. The lock is acquired before opening a DB transaction, preventing the in-flight worker from overwriting reconciliation without introducing lock inversion. Local bytes, path, size, static image format and hash are checked; upstream-verified imports are exempt only from upload dimension recommendations.

Person generated_count excludes snapshot.model.mode=mock. Five new regressions were first observed failing: queued, failed, uncertain imports; import versus submitting worker race; mock-versus-real success count. All five were fixed, including a tampered-file assertion. Final targeted portrait suite: 69 passed.
