# Durable production implementation plan

Approved design: user's preceding proposal and instruction to start fixing.
Goal: editable concurrent drafts, durable local assets/session, independent queued runs, actionable failures and safe recovery.
Architecture: extend current FastAPI app with a production router/store/worker. Existing jobs stay readable as history; new production runs own immutable snapshots. Frontend keeps current picker UX and adds draft/run controls. No paid generation during testing.

## Contracts
Namespace /api/production. Local-only same-origin access.
Asset: {id, name, kind: video|face|clothing, url, size, mime}; persistent files in storage/assets.
POST /assets multipart file + kind -> Asset.
POST /assets/import JSON {text} -> Asset (same TikHub/Fake-IP downloader).
GET /assets/{id}/file -> media bytes.
Draft shape: {id,name,revision,source_asset_id,face_asset_ids,clothing_asset_ids,prompt,mask,model,updated_at,assets:[Asset]}.
POST /drafts JSON optional {name,copy_from} -> draft.
GET /drafts -> {items}; GET /drafts/{id}; PUT /drafts/{id} JSON editable fields + revision (optimistic conflict 409).
POST /runs JSON {draft_id,revision,idempotency_key} -> Run. Same key returns same immutable task.
Run: {id,draft_id,name,status,stage,message,progress,error,error_kind,request_id,provider_task_id,created_at,updated_at,snapshot,download_url,defaced_url,legacy:boolean}.
GET /runs -> {items}; GET /runs/{id}; POST /runs/{id}/copy -> draft; POST /runs/{id}/cancel only queued; POST /runs/{id}/resume only needs_attention with provider ID or downloadable result.
Draft mask = current form fields with typed checkbox/number values; model = {provider,protocol,mode,base_url,model,duration,fps,resolution,public_base_url}, no keys. Submission resolves saved secrets against same endpoint; creates private immutable config record for worker.
Run states: queued,running,succeeded,failed,cancelled,needs_attention. stages preprocess,upload,submitting,generating,downloading.
Errors material_rejected,configuration,submission_uncertain,query_unavailable,download_failed,processing_failed.

## Work packages
- [x] Main: tests + local asset/draft/run store, API, queued worker, immutable inputs, legacy recovery, lifecycle.
- [x] Frontend agent: production.js + session/tasks JS, templates/CSS, browser regressions; use contracts above. autosave, new/copy/switch drafts, snapshot submit, nonblocking run cards, persistent refresh, no duplicate POST.
- [x] Provider agent: split durable model submit/poll recovery via callbacks and resume. ProviderError structured properties. Unit tests.
- [x] Integrate, run full isolated tests and browser lifecycle tests, review, reload local app only when no active task.

## Constraints / decisions
- Preserve user's dirty working tree; no reset/commit of unrelated work.
- Existing /api/jobs preview APIs remain compatible. New queued runs reuse preprocessed content by file hash and validated mask config.
- Drafts and assets persist until explicit deletion. Do not delete user data in cleanup.
- Bounded concurrency: two background workers, single local preprocessing lock.
- Persist provider ID before polling. Never automatically resubmit uncertain POST. Reuse provider ID on restart/query retry.
- This is local workspace persistence, not multi-user accounts or cloud session storage.
- Ruling: use a scoped production SQLite store under storage for new draft/queue metadata; do not retrofit incompatible older workflow/manual-review schema. Legacy tasks are read-only history in new list, not dispatched twice.

## Acceptance
A can run while editing/submitting B. Refresh restores all assets/prompt/parameters. Inputs immutable. Submit deduplicated. Material refusal mapped to first face reference without auto-retry. Restart with provider ID only polls. Uncertain submission remains needs_attention. Queue cancel works. Existing TikHub/Fake-IP/TOS/preview/template behavior maintained.

## Completion evidence
- 313 pytest tests passed with isolated storage; one existing dependency deprecation warning.
- Desktop/mobile offline browser regressions and real isolated server integration passed (mock provider; detector fixture).
- Integration review findings fixed and re-reviewed.
- Local service reloaded after confirming no running legacy jobs. Actual browser restored recovered source/face/clothing and retained them after refresh; 18 historical tasks visible.
- Existing TOS enabled. No paid generation submitted.
