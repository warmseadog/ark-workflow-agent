# Administrator camera variation implementation plan

> Use executing-plans and test-driven-development. User approved implementation and server deployment on 2026-10-06; proceed without additional design approval.

**Goal:** Admin-only expand-to-edit camera variation, one task per click, optional inspiration, immutable original inputs, configurable LLM/template/Skill.

**Architecture:** Reuse production submission and queue with an explicit variation intent frozen in private task data. Persist public planning state separately, render material locks deterministically, bypass legacy camera locks only in variation mode. Independent global planner configuration follows existing private settings patterns. Empty inspiration uses default template; nonempty inspiration is data within the same constrained planner.

**Tech stack:** FastAPI, SQLite, requests, Jinja, existing browser JavaScript, pytest/Playwright, existing ECS release tool.

**Spec:** Approved design in this conversation. No derived-video input, no batch UI, no changes to saved original prompt. Identity/hair/outfit/accessories/scene/light/style stay fixed. Camera/framing/display focus vary. New tasks use same source assets. Admin and super_admin can operate/configure; ordinary users cannot. No automatic paid retries. Config/Skill/template frozen per submission.

## Tasks
- [x] 1. Write failing settings/planner tests; implement variation_settings.py and variation.py/variation_llm.py. Check JSON, timing, conflicts, material references and secret handling.
- [x] 2. Write failing API/worker/provider tests; add explicit request intent, idempotency checks, persistent planning/history, variation-only composer, resume behavior and release compatibility.
- [x] 3. Add admin-only inline editor and backend configuration section (model ID, endpoint, secret, timeout, template, Skill). Browser-test admin/user visibility, empty/filled submission and settings.
- [ ] 4. Run focused and full regression tests, independent review; fix substantive findings. Build complete release and execute inspect/check/deploy/verify through existing release entry point. Preserve production configuration/data and report live verification.

## Review focus
- Non-admin forged submissions/configuration; reject server-side.
- Uncertain submission retries cannot change inspiration or mode; preserve remote IDs.
- Source camera locks must never override a variation plan; ordinary generation remains unchanged.
- Never leak planner keys through task/config/error responses; endpoint changes clear old credentials.
- Plan failure must occur before paid video submission; immutable settings and plan reused on recovery.

## Progress / rulings
- Native managed worktree created from main; original workspace untouched.
- Initial baseline collection used inherited storage and failed opening SQLite. Rerun with isolated storage/database and dotenv disabled; no application regression inferred.
- Use one fresh final reviewer as required by executing-plans; implementation stays inline.
- Independent review completed; fixed variation audio-retry fallback, ordinary-user preparation retry, and transactional idempotency checks. Regression tests cover each finding.
- Focused feature/audio tests: 41 passed. Final permission and downgrade tests: 24 passed. Production/API/privacy/release tests: 69 passed, 1 POSIX-only skip.
- Full suite attempt revealed existing browser-test failures and was stopped at 43%; baseline account-navigation failure reproduced in the original checkout. Additional isolated regression checks are recorded in the delivery report. No claim of a fully green suite or real paid-model quality verification.
