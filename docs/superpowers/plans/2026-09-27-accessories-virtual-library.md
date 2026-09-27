# Accessories and virtual characters implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Track steps below.

**Goal:** Implement the user-approved six accessories, expanded optional scene, and official virtual-person library end to end.
**Architecture:** Extend existing durable drafts/runs and portrait API with explicit AIGC person type. Preserve existing LivenessFace semantics. Reference ordering is shared by generation and error mapping.
**Tech Stack:** FastAPI, SQLite, vanilla JS, pytest.
**Spec:** User-approved design in conversation, 2026-09-27; user explicitly instructed start execution.

## Global constraints
- Accessories: bag 包包, hat 帽子, watch 手表, shoes 鞋子, necklace 项链, glasses 眼镜. One picture per category; optional enabled boolean; draft persistence and snapshots.
- Scene always expanded and optional. Do not collapse via JS restoration.
- Current Ark image total <=9; disabled stored references do not count. No silent dropping.
- Virtual official asset type AIGC; real type LivenessFace. No fake certification or unsupported fallback to raw image. Verify account/project/type/status before submission.
- Keep keys redacted. No purchases or repeated uncertain provider submissions.
- Existing source checkout is codex/scene-hairstyle, tracked clean. Work here to preserve active local application context. User explicitly approved execution; no repeated design approval.

## Review Focus
- Existing real-person assets and drafts remain valid.
- Multiple changed asset ordering yields accurate content[index] error names.
- Disabled accessories survive reload but never enter provider request.
- Pending/failed/unknown virtual upload cannot become usable accidentally.
- Virtual creation timeout never automatically creates duplicates; account mismatch rejected.

## Task 1: Accessories and scene (root)
Files: production_router/store/worker, video_provider, reference_prompt/media, production.js/css/templates and tests.
Interfaces: draft fields {kind}_asset_ids and {kind}_enabled; kinds bag/hat/watch/shoes/necklace/glasses. VideoProvider.generate(..., accessories: dict[str,list[Path]]|None=None). Single manifest order face1,clothing1,remaining faces/clothes,hairstyle,scene,accessories in listed order.
- [ ] Write failing API/provider tests for category validation, snapshot persistence and counted enabled references, precise role errors.
- [ ] Implement durable fields and worker/provider forwarding, role rules.
- [ ] Implement controls, previews, toggles, live count and restore; make scene noncollapsible.
- [ ] Run targeted and browser checks.

## Task 2: Virtual library (one delegated implementer)
Files owned: portrait_service/library/generation/router, portrait-people.js, new virtual JS/CSS/templates, tests/test_virtual_library.py. Do not edit root-owned files; report tiny integration changes needed.
Interfaces: existing person_id stays; people add person_type AIGC/LivenessFace. /api/portrait/people supports virtual create/sync; photos existing workflow accepts AIGC by explicit type. Production router continues calling PortraitLibrary.person, prepare/verify. UI integration through portraitPeople and portrait-person-changed event. Additional UI mount dynamically or separate include, no production.js edits.
- [ ] Write failing tests proving virtual and real types segregate and AIGC request/URI validation.
- [ ] Implement official AIGC group create/list/get/asset upload with entitlement errors, local durable state and existing TOS.
- [ ] Implement virtual library creation/selection/rename/sync/photos, usable and generated-success distinction; import Asset ID only when verified official metadata accessible, explain any unsupported public API.
- [ ] Add backend config/status exposure and frontend admin section via separate mount; root adds includes if needed.
- [ ] Run relevant regression tests; report actual account limitations.

## Task 3: Integration and release (root with fresh review)
- [ ] Run full pytest; browser upload/draft/selection/responsive checks.
- [ ] Fresh code review of diff; fix material findings.
- [ ] Read-only production readiness check, package code only, safe service rollout if idle, verify health and UI.
- [ ] Official permissions check and smallest permitted generation validation using selected usable virtual asset; do not assert completion if privileges/content block it.
