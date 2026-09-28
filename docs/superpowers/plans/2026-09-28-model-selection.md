# Model selection and Seedance 2.5 Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Separate per-draft model choices from server connection settings and support Seedance 2.5 video editing.

**Architecture:** A server-owned capability catalog supplies the editor and validates submissions. The existing admin connection form stays in the backend; a new editor panel persists only model/duration/resolution. Immutable runs retain private resolved connection settings.

**Tech Stack:** FastAPI, Python, vanilla JavaScript, Jinja, pytest, Playwright.

**Spec:** User-approved proposal in this conversation (2026-09-28): frontend model/parameters, backend connection/catalog administration, Seedance 2.5 support.

## Global Constraints

- Preserve the existing upload-first changes and existing production service configuration.
- Seedance 2.5 edits follow the source video (`duration=-1`, `ratio=adaptive`); MP4 output remains compatible with current downloads.
- 2.5: 30 images, 30 seconds total input video; 2.0: 9 images, 15 seconds. Keep existing conservative file-size limits.
- No external paid generation is implied by local verification; account entitlement must be clearly distinguished from configuration.
- Keep existing default model; unverified new models are managed/confirmed in admin before frontend use.

## Review Focus

- Stale drafts and custom providers must not redirect requests or move server credentials.
- Changing model must not mutate defaults, another draft, or frozen runs.
- Unsupported resolution/duration must fail server-side even if the browser is bypassed.
- Model changes, cancel, draft restore, and browser reload must preserve applied choices.
- 2.5 input video checks must agree in router, worker, provider and frontend.

### Task 1: Catalog and task isolation
Files: new `app/model_catalog.py`, `tests/test_model_catalog.py`; modify `app/generation_settings.py`, `app/main.py`, `app/production_router.py`.
Interfaces: `capabilities(model, protocol)`, `catalog(settings)`, `save_catalog(settings,payload)`, `resolve_task_config(settings,payload,require_enabled=False)`.
- [x] Add failing API tests for private-free choices, admin enablement, server capability validation, task isolation, legacy endpoint rejection.
- [x] Run tests and confirm missing behavior.
- [x] Implement catalog with atomic scoped configuration storage and model-aware generation validation; task payloads cannot change connections.
- [x] Run catalog and existing generation/production tests.

### Task 2: Seedance 2.5 generation
Files: `app/person_video.py`, `app/production_router.py`, `app/production_worker.py`, `app/video_provider.py`, `tests/test_seedance25.py`.
- [x] Add failing tests for 30-second combined references, overflow rejection, 30-image cap and provider edit request fields.
- [x] Implement model-aware validation throughout; force the editing prompt and duration -1 for 2.5 production tasks.
- [x] Run provider, person-video and production tests.

### Task 3: Editor and admin UI
Files: new `app/templates/generation_options_panel.html`, `app/static/generation-options.js`, `app/static/model-catalog.js`, `tests/browser_model_selection.py`; modify editor/admin templates and `app/static/production.js`.
- [x] Add failing real-API browser regression for frontend selection/apply/cancel/reload, resolution switching, no global writes, and admin enablement.
- [x] Implement editor panel and admin catalog; render model limits from API; apply only on explicit apply.
- [x] Verify at 1440 and 390 widths and inspect screenshots.
- [x] Run full project suite, related browser regressions, independent final code review; update `docs/model-settings.md`.

## Execution ledger

- Ruling: Execute inline under the user's explicit instruction to start; no extra design approval round.
- Ruling: Existing workspace contains the prior released upload-first work; preserve it and build on this workspace, no reset or unrelated commit.
- Ruling: Catalog confirmation is an explicit admin acknowledgement of a real successful generation, not a connectivity test. New models remain pending until confirmed.
- Task 1: complete. Initial catalog suite: 9 failed / 2 passed; implementation plus existing config/production API: 38 passed.
- Task 2: complete. Seedance 2.5 suite: 4 expected failures before implementation; combined provider/person-video/production suite: 46 passed.
- Task 3: complete. Browser test first failed because frontend exposed API Key; final desktop 1440 and mobile 390 checks pass for apply/cancel/restore, resolution changes and admin catalog writes.
- Final review: independent agent review_model_selection found default-acceptance inheritance, duration validation before normalization, and per-category image capacity issues. Each has a reproduced failing regression and a passing fix; combined focused suite: 22 passed.
- Final: Ruling on declined review scope: account entitlement/quality require real generation; deployment is not part of this local implementation; prior upload-first changes preserved. Existing 2.0 image-only source validation remains unchanged; new 2.5 source/pair checks are enforced before submission. Final browser acceptance was performed by the implementer.
- Final verification: `.venv/Scripts/python.exe -u -m pytest tests -q -p no:cacheprovider --basetemp=storage/test-model-selection-final` -> 435 passed, one existing Starlette/anyio deprecation warning. Invalid media fixtures also emit decoder messages without failed tests.
- Root `pytest` still fails collection on existing untracked `video-production-module/tests` with ImportPathMismatchError; no exported copy was removed or changed.
- Browser regressions passed: model selection (1440/390), model connection in admin (1440/390), person upload (1440/390), person video (1440/390), durable production session (1440/390), auto virtual (image/video, legacy and real TestClient integration). `git diff --check` passed.
- No deployment, account acceptance mutation, paid generation or Git commit performed in this implementation turn.
- Subsequent explicit user deployment authorization: released `20260928T130023Z-model-selection` at 21:00:23 Asia/Shanghai. 49 focused release tests passed; 18 code/static files and public APIs independently verified. Existing default, account acceptance flags and user data preserved. Health 200, service active with zero restarts, prior release and database/config backup retained. No paid generation performed.
