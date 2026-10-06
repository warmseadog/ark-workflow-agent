# Admin task copies and image preview

> **For agentic workers:** Use superpowers:executing-plans to implement task by task.

**Goal:** Administrators copy another user's task and its inputs into their own account, then generate without changing either login; all displayed reference images support enlargement and the main editor omits file names.

**Architecture:** Add an explicit admin copy endpoint, using tenant-local asset files and a fresh draft. Preserve existing portrait authorization and copied immutable prompt/model settings; never copy provider task IDs or credentials. Keep the old delegated API compatible but use the new endpoint from task records. Add a shared accessible image dialog wired to explicit image targets.

**Tech Stack:** FastAPI, SQLite, vanilla JavaScript, pytest and Playwright.

**Spec:** User request and confirmed ownership: new drafts, materials and outputs belong to the administrator.

## Constraints and review focus

- Preserve unrelated MediaKit work and original users' files, drafts, sessions and task records.
- Verify cross-tenant access controls, idempotency/concurrency, missing/changed files, active portrait bindings and revoked references.
- Verify image aspect ratio, keyboard/close behavior, original image URLs, mobile layout and upload/selection controls.
- Release remains subject to the already identified server disk capacity blocker; do not remove backups without authorization.

## Task 1: Administrator-owned copies

- [x] Add failing API tests for copied materials, independent owner/quota, session retention and permissions.
- [x] Implement app/task_copy.py and connect app/task_records.py; retain validated portrait references and immutable inputs.
- [x] Wire production-runs.js to copy into the administrator editor without delegated login/navigation.
- [x] Run targeted API and full browser copy-to-submit regression tests.

## Task 2: Image preview and concise materials

- [x] Add browser regressions for full image view, closing/focus, filenames and reference actions.
- [x] Implement shared image viewer; wire editor, task details and library image targets, keeping video playback intact.
- [x] Remove redundant filename rendering; update asset cache versions and run relevant browser checks.

## Task 3: Verify and handoff

- [x] Independent review, resolve substantive findings, run relevant final checks.
- [x] Report actual tested behavior and deployment state without claiming cloud generation or deployment not performed.

Validation: 91 Windows API/browser regressions passed; 31 Linux candidate API regressions passed; formal server release preflight passed. Revision ends in `7d3ccb9a4a2e0f47`. Deployment completed on 2026-10-06 after extending the existing root partition and ext4 filesystem to the user's expanded 50 GiB cloud disk. Release receipt is verified, public health and static resource checks passed. The old rollback snapshot was neither downloaded nor removed. See `docs/deployment-20261006-mediakit-admin-copy.md` for the release and backup identifiers.
