# Local administration and workflow implementation plan

> Execution: approved design in the conversation; user explicitly requested implementation locally. Apply writing-plans, TDD and focused parallel agents for independent file ownership. No push, deployment or paid model calls.

**Goal:** Implement requirements 4–10: original-duration reset, account deletion/name rules, delegated drafts, phase timings, three roles, comparison playback, private prompts and global concurrency.

**Architecture:** Extend existing FastAPI/SQLite tenant stores and single-dispatcher queue. Centralize roles, keep actor and target tenant explicit for delegated APIs, serialize ordinary-user responses without system prompts, persist phase timing and scheduling configuration.

**Tech stack:** Python/FastAPI/SQLite, vanilla JS, pytest/Playwright.

**Spec:** User-approved design in the immediately preceding conversation, summarized below.

## Constraints and rulings
- Work in current local checkout, preserving existing account IDs/passwords/drafts/history. User's local implementation instruction supplies execution authorization; no repeated approval gates. Existing clean checkout permits in-place feature branch.
- user/admin/super_admin; initial legacy admin migrates to super_admin; administrators manage ordinary users only. Only super_admin edits system settings/global concurrency and manages administrators. Last enabled super_admin protected transactionally.
- New usernames 2–20 normalized characters; existing 1–64 usernames remain valid for login. Soft deletion, revoke sessions, cancel queued tasks, reject deletion while running; super_admin restores deleted accounts.
- Delegated restoration creates a new draft in original user's tenant, never overwrites old drafts/runs, preserves prompt version and snapshot values, creates fresh provider submissions, uses original owner's quotas, records actor/owner/source provenance. Deleted/disabled accounts cannot receive new delegated generation.
- Ordinary users cannot read/write template/system prompt fields or full preview, including snapshots/continuation plans. Server defaults and immutable saved prompt versions still apply; ordinary UI does not depend on prompt preview. Admins retain editor.
- Reset duration clears segment start/fixed duration/continuation target, saves draft; model limitations stay truthful. Comparison source is original action video and result, clip offset honored, source freezes at end during continuation; one audio stream, responsive controls, release media on close.
- Stage metrics: waiting, masking, upload, model, other, paused; cache explicitly marked, unknown historical/interrupted durations never fabricated. Model timing includes provider queue. Existing total timing preserved.
- Global logical concurrency hot reload, 1 through actual worker capacity (current hard cap 50), initialize from current runtime value; lowering drains, no cancellation; tenant quotas and FIFO/fairness preserved. Queue capacity remains distinct.

## Review focus
- Old long username can log in after stricter registration; last-super deletion/demotion races cannot lock out administration.
- All ordinary-user API responses and browser HTML/JS exclude system prompt content, including copied and historical drafts and continuation plans.
- Delegated draft tenant IDs, assets and media validated independently of current admin tenant; repeating restoration/submission is idempotent.
- Deletion versus dispatch/new submission must not create work for deleted accounts; reduced global limit must not cancel running tasks.
- Phase retries/recovery and cropped/extended comparison media do not double count time or misalign source.

## Task A — Accounts and permissions
Files: app/permissions.py (new), accounts.py, authentication.py, access_control.py, tenancy.py, user_admin.py, static/account.js, static/users.js, templates/users.html; dedicated tests.
Interfaces: permissions.is_admin(user), is_super_admin(user), can_manage(actor,target); account public records include deleted_at and permissions/capabilities as needed.
- [x] Write and observe failing migration/role/username/delete/restore tests.
- [x] Implement migration, lifecycle and role-scoped account management; expose API and UI.
- [x] Run account/auth/security tests, report intentional role expectation changes.

## Task B — Scheduling and stage timing
Files: app/scheduling_settings.py (new), app/run_phases.py (new), production_worker.py, production_store.py, continuation.py, video_provider.py instrumentation, templates/static admin settings via separate module; dedicated tests.
Interfaces: phase summary under run.timing.phases with keys waiting/masking/upload/model/other, entries seconds/status; scheduling router returned by get_router(settings_getter), root integrates registration.
- [x] RED tests for stage accumulation/cache/recovery and dynamic global cap/drain.
- [x] Implement persistent phases and explicit real work boundaries; scheduling settings/API and independent UI module.
- [x] Verify existing timing/worker/queue tests; communicate API schema to parent.

## Task C — Duration and video comparison
Files: static/generation-options.js, templates/generation_options_panel.html, static/production-runs.js, static/video-comparison.js (new), static/video-comparison.css (new); dedicated browser tests.
- [x] RED browser coverage reset including clip/continuation, comparison offset/sync/end/resource cleanup.
- [x] Add reset and comparison controls using existing scoped media URLs; root loads new assets in pages.
- [x] Verify mobile/desktop duration and playback regressions.

## Task D — Prompt privacy and delegated workflows (root)
Files: app/prompt_visibility.py, app/delegated_tasks.py (new), main.py, production_router.py, task_records.py, local_preferences.py, static/production.js, static/link-templates.js, templates/production_panel.html and page scripts.
- [x] RED ordinary template/preview/snapshot/write-leak tests; delegated ownership/idempotence tests.
- [x] Implement public response projection and server-owned prompt fields, role-aware rendering and editor dependencies.
- [x] Add explicit delegated endpoints and editor context, preserve owner/actor and prompt provenance, connect task actions.
- [x] Verify template/API/permissions and delegated browser behavior.

## Task E — Integration and review
- [x] Integrate role checks across modules; ensure system configuration is super-admin-only and operational tools remain available to admins.
- [x] Add new schema release capability guards; cache bust changed page scripts.
- [x] Run meaningful focused tests followed by full suite with isolated storage; review changes and fix findings.
- [x] Back up local databases before migration, verify local roles/templates/history and finish with clean local commit, no remote changes.

## Ledger
- Baseline main fabcb2e, clean working tree. Prior release verified; test storage permission issues require isolated fresh directories and elevated screenshot writes where necessary.
- Independent review fixes: deterministic active-admin migration fallback; move legacy prompt rules out of public static scripts into an authenticated editor endpoint; lock template fields while saving and protect unsaved edits; scope delegated task lists to their owner and supported actions.
- Local backup: `exports/admin-workflow-local-backup-20261005-201138/`. Four SQLite databases copied with SQLite backup; integrity checks and per-table row fingerprints verified. After local schema initialization every pre-existing table fingerprint remained unchanged. This local storage had no accounts database; no account credentials were created. Global concurrency initialized to current capacity 50.
- Focused final privacy/editor review: 14 passed. Offline browser fixture compatibility: 50 passed. Changed/new JavaScript syntax checks and diff whitespace checks passed. Desktop/mobile administrator workspace inspected at 1440 and 390 pixels.
- Full-suite findings corrected: unauthenticated tenant previews must not look up an account; offline Jinja/browser fixtures must supply the explicit role gate and protected editor asset; administrator template browser coverage now targets the operational page and verifies ordinary users receive 403; failed-preview assertions compare against existing legacy history rather than assume an empty global history. The local-preview regression failed before its fix; preview/privacy/template recheck: 34 passed, 1 platform skip.
- The separate legacy `browser_auto_virtual`, `browser_portrait`, and `browser_production_session` scripts received the template fixture adaptation, but their unrelated old fixtures (too-short source, non-video bytes, obsolete control) are not part of the pytest suite and were not rewritten. No provider calls were made.
- Final stable-tree full regression: `pytest tests -q -x -rs --durations=12 -p no:cacheprovider --basetemp=.tmp-admin-final-suite` with independent storage: **1380 passed, 3 skipped, 1 warning in 964.88 seconds**. Skips: two Windows symlink-privilege checks and one POSIX-only permission check. Warning: existing Starlette/AnyIO deprecation. Full log: `.tmp-admin-final-suite.log`.
