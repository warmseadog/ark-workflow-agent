# Exclusive reference prompts implementation plan

> Execution: implement inline using executing-plans and test-driven-development; user approved the design and explicitly requested local, GitHub and server synchronization.

**Goal:** Add 默认提示词 as the new-task default, retain the existing text as 默认提示词2, and bind every enabled optional element exclusively to its own reference.

**Architecture:** Store template ID and rule version in drafts and frozen run snapshots. The v2 preview and provider share a pure Python composer; v1 drafts retain the existing composer. Migrate shared templates once without resurrecting deleted defaults or rewriting drafts.

**Tech stack:** FastAPI, SQLite, vanilla JavaScript, pytest and Playwright; existing verified full-bundle release workflow.

**Spec:** User-approved design in this chat: absent independent hair uses main identity hair (image or identity video); scene fallback uses action video; independent hair/scene/accessories exclude all competing sources; existing drafts and copied historical runs retain their version.

## Constraints and review focus

- No paid model calls during verification; no credential changes.
- Template edits retain their rule version; personal copies inherit it; old clients remain v1.
- Preview rejects unsupported roles/version and handles stale asynchronous replies and network failure visibly.
- New-task defaults come from saved template content, not a hard-coded textarea or list order.
- Shared migration runs only at root, once; renamed/custom/deleted templates and historical snapshots remain intact.
- Preview and submission must agree for image/video identity, supplemental images, disabled elements, scene text and model follow-source mode.

## Task 1: Templates and draft metadata

- [x] Add failing migration/default/copy/version-validation tests in tests/test_exclusive_prompts.py.
- [x] Add app/prompt_templates.py constants and SQLite migration in app/local_preferences.py; persist rule_version on template CRUD.
- [x] Add prompt_template_id and prompt_rule_version validation, saved-default new draft creation, and snapshot preservation through existing JSON persistence.
- [x] Run focused template/API/permission tests.

## Task 2: Shared composer and frontend

- [x] Write failing source-isolation and preview/provider parity tests.
- [x] Add compose_exclusive_prompt(prompt, roles, person_video=False, scene_description='', follow_source=False) in app/reference_prompt.py.
- [x] Add authenticated /api/production/prompt-preview endpoint and pass frozen rule version through worker/provider.
- [x] Add frontend template metadata restoration and read-only final v2 preview, using the server composer; gate submission on a current successful preview. Keep v1 editor rules unchanged.
- [x] Test stale responses, selected template restoration, default new tasks, switching v1/v2, enabled/disabled elements and copies.

## Task 3: Verify and synchronize

- [ ] Run relevant backend/browser tests, then full regression; request independent review and address findings.
- [ ] Commit and merge locally; push GitHub main without force.
- [ ] Inspect current server identity, build complete verified bundle, stage/check/deploy with existing release tool and backup.
- [ ] Run template migration after backup, verify live template count/content, version/default behavior, static assets, health and unchanged historical data.
- [ ] Record final commit, release identity, backup and validation results.

## Execution ledger

- Local checkout clean on main at start; using codex/exclusive-reference-prompts in the existing user workspace to support requested local sync.
- User has already approved implementation and publication; no additional design approval needed.
- RED: seven new migration/composer/API tests failed on missing behavior; GREEN: 67 related backend tests passed.
- Independent review found no actionable P1/P2 defects. Added requested edge coverage: out-of-order preview success cannot overwrite current input; preview failure prevents /runs; follow-source preview exactly matches provider submission. Three editor browser tests passed, and 14 follow-source/admin-browser/compatibility tests passed.
- Ruling: use a separate read-only full preview for v2, leaving the base editor editable, to avoid asynchronous responses moving the typing caret or overwriting user edits.
- Added release capability exclusive-prompts-v2 after a failing downgrade test demonstrated that old readers would otherwise accept the changed SQLite schema.
- Integrated origin/main f42820d (MIRA editorial UI); 184 live app/ui files match the previous reconciled base 5b402d9.
- Local template database backed up and synchronized to the two requested templates; only the three untouched obsolete bundled templates already deleted online were removed locally.
- Concurrent first-read probe reproduced duplicate-column migration failure; BEGIN IMMEDIATE now serializes schema inspection and migration. Rechecking template API, permissions and browser flows after this fix.
