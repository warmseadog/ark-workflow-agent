# Automatic virtual person implementation plan

> **For agentic workers:** Use superpowers:subagent-driven-development or superpowers:executing-plans. Track the checklist and evidence below.

**Goal:** New UI drafts accept direct virtual-person images/videos and automatically ingest before generation, preserving real-person and legacy flows.
**Architecture:** Freeze `person_input_policy` in drafts/runs; persist preparation separately from immutable snapshots. Reuse portrait ingestion and worker authorizing stage. Independent library and UI work run alongside root-owned store/router/worker orchestration.
**Tech Stack:** FastAPI, SQLite, Python, vanilla JS, pytest, Playwright.
**Spec:** ../specs/2026-09-28-auto-virtual-person-design.md (user approved and requested execution).

## Global constraints
- New UI explicitly requests auto_virtual. Missing policy retains old API/draft semantics; existing_person and legacy_raw remain supported.
- Existing official assets keep official checks; AIGC never becomes verified真人. No raw-image fallback after failed official preparation.
- Remote create starts after atomic run creation; stable content/account-scoped request identities survive retry, concurrency and restart.
- Never mutate run.snapshot. Keep private config and runtime preparation separate.
- Keep existing person-video media/model checks and reference limits. No remote production mutation or paid generation during automated tests.
- Worktree: C:/Users/Administrator/.codex/worktrees/auto-virtual-person/方舟工作流agent; Python: E:/方舟工作流agent/.venv/Scripts/python.exe.

## Review focus
- Sole real person must never be automatically selected for a new auto_virtual draft.
- Uncertain group creation, cancellation, repeated submits and restart must not duplicate remote creates or resume cancelled video generation.
- Hidden/ambiguous/real associations cannot be silently reused as AIGC.
- Already submitted video tasks must resume without preparation or local input files.
- Switching media, draft or person during upload must not attach late results to new input; prepared identity counts/copies correctly.

## Task 1: Portrait library primitives (delegated)
Files: portrait_library.py, portrait_service.py, tests/test_auto_virtual_library.py.
Interfaces: PortraitLibrary.resolve_virtual_assets(asset_ids) -> person dict or None; raise ValueError for real/hidden/conflicting matches. PortraitLibrary.ensure_auto_virtual(asset_ids) -> {status,person_id,request_id,message}; stable request and remote group reconciliation. Root calls it outside SQLite transaction after run creation. Existing create_virtual contract preserved. Root owns statistics integration after this task.
- [x] Write and observe failing tests for precise reuse, concurrent stable identity, persisted remote ID and uncertain reconciliation.
- [x] Implement helpers by reusing group requests, enqueue and typed official client; no network in resolve_virtual_assets.
- [x] Run new tests plus existing virtual/portrait library and service tests; report evidence and limitations.

## Task 2: Durable preparation and production integration (root)
Files: new person_preparation.py; production_store/router/worker.py; portrait_generation.py; tests/test_person_preparation.py.
Interfaces: private.person_preparation contains frozen account fingerprint/config and input digest; production_person_preparations is run_id keyed and stores runtime JSON. prepare_run(settings,store,run) -> path-to-asset URI map or PortraitPending/PreparationError. Public run.person_preparation whitelist and can_retry_preparation; POST /runs/{id}/person-preparation/retry.
- [x] Write failing tests covering new/old policy, preflight, no remote mutation before committed run, queue waits/recovery, photo rejection, retry and cancellation.
- [x] Implement atomic intent creation, stable runtime checkpoints, typed preflight and authorizing integration.
- [x] Cover video auto path, frozen inputs, old official bindings, remote-task resume, copies and person success statistics.
- [x] Run targeted production/portrait/video suites.

## Task 3: Frontend (delegated)
Files: production.js, portrait-people.js, production-runs.js, production_panel.html, production.css, production/studio templates as needed; new browser_auto_virtual.py and relevant UI regressions.
Interfaces: person_input_policy values auto_virtual|existing_person|legacy_raw; POST /drafts accepts explicit policy; run.person_preparation provides {state,person_id,message}; can_retry_preparation flag and retry endpoint above. No policy means legacy fallback by person_id/bindings. New upload waits only for local save before submit, not Active. Video still must satisfy existing constraints.
- [x] Add failing browser coverage for empty/sole-real library auto defaults, image/video direct uploads and one-click submit.
- [x] Implement default policy and chooser, preserve explicit existing-person selection and old draft restore, guard async callbacks on policy/person/draft.
- [x] Add task preparation state/retry display and verify desktop/mobile plus old picker/photo/session browsers.

## Task 4: Review and verification
- [x] Inspect combined diff and resolve cross-task interface differences.
- [x] Run full app suite via python -m pytest tests; run meaningful browser regressions and JS syntax.
- [x] Fresh reviewer checks whole feature and review focus. Fix material findings with regression tests.
- [x] Record implementation/test evidence and outstanding real-provider acceptance; deliver local branch without deploying or asserting paid generation succeeded.

## Execution ledger
- Ruling: User's explicit “开始执行” authorizes implementation of the reviewed design without another plan approval round. The implementation checklist refines that design, not a scope expansion.
- Ruling: Native worktree isolates main; reuse the existing Python environment rather than reinstall dependencies. Tests use worktree code and temporary storage only.
- Baseline started before implementation. Existing untracked files remain in original checkout.
- Implementation: stable account/content-scoped AIGC group reconciliation, persisted photo failure retryability, run preparation state with automatic bounded rechecks, separate retry action, and resolved-person copies/statistics are implemented. New drafts explicitly select auto_virtual; old API and drafts retain legacy inference.
- Regression fixes: reject old-account official bindings; wait against this run's deadline instead of reused photo age; preserve retryability across photo-worker status changes; prevent deleted-run retry resurrection; serialize fresh SQLite WAL transitions without replaying business writes.
- Independent reviewer found the photo-status race and deleted-run retry race. Both were reproduced with failing tests, fixed, and re-reviewed. Final scoped review also identified a transient person-directory refresh failure: fixed by recording observed identities only after a successful read and retrying failed reads on the next normal run refresh. Its failing-then-passing browser regression verifies recovery without a tight retry loop or changing the user's input.
- Backend final verification: `python -m pytest tests -q --basetemp .tmp-auto-suite-final` → **413 passed**, 1 existing AnyIO/Starlette deprecation warning, 60.84 s. Targeted library/preparation/API/store verification before that: **55 passed**.
- Browser verification by frontend implementer: new auto flow at 1440/390, old raw/bound drafts, and real TestClient/SQLite local image/video submit; existing production-session, portrait-people, person-photos and person-video scripts each at 1440/390. No live provider traffic.
- Root verification initially ran the new browser script directly: all four UI scenarios passed, but the added backend integration lacked repository-root imports. The script now initializes its repository root. Final direct `python tests/browser_auto_virtual.py` exits 0: desktop auto, mobile sole-real isolation, legacy raw, legacy bound, and real TestClient/SQLite image/video integration all pass.
- JavaScript syntax checks for production.js, portrait-people.js and production-runs.js, plus diff whitespace checks, pass. Desktop/mobile screenshots were visually inspected. No further backend code changed after the final 413-test pass.
- Documentation: `docs/auto-virtual-person.md` records behavior, setup, compatibility, persistence and rollout scope. No deployment or paid generation was performed; historical Active-but-not-found provider behavior still requires current-account real generation acceptance.
