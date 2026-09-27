# Scene and hairstyle reference implementation plan

Goal: Implement the user-approved four-panel layout with optional scene image and a nested hairstyle image, durable drafts and actual provider submission.
Spec: User design approved in this conversation: scene collapsible fourth panel, hairstyle is an uploaded image (not text), optional inputs preserve current workflow.
Architecture: Extend existing asset kinds and draft JSON; add optional provider keyword parameters, preserve original image numbering, derive role instructions at submission. No schema migration or new dependency.
Constraints: one scene and one hairstyle image; all active images count toward current 9-image limit; collapse does not disable; disabled assets retained in drafts and marked unused in run details. No changes to identity authorization; raw extra images remain subject to provider checks. Never overwrite custom templates.

Task 1: Durable data + generation. Add failing API and outbound payload tests. Extend upload, validation, snapshot, worker and provider. Verify pytest.
Task 2: UI + templates. Add failing browser coverage for both images, disable, collapse, reload and snapshot independence. Reuse existing upload/preview controls; neutral defaults plus role instructions for every template. Verify desktop/mobile.
Task 3: Full verification, independent review, commit and deploy. Preserve ECS original service/Nginx. Check active queue before restarting new service. Public UI/auth and existing media checks, no paid generation.

Review focus: old drafts with absent fields; disabled image exclusion; image numbering with multiple face/clothing references; reload and rapid edits; private person binding unchanged; custom templates never overwritten; mobile overflow.

Ledger:
- Ruling: continue in current workspace as previously requested for this project's local changes; use a feature branch for this change and preserve all pre-existing untracked files.
- Pre-flight: asset kind names scene/hairstyle must match API, worker, provider and UI. Enable state independent of details open state. Prompt override enforced centrally for every template.

- Task 1 complete: API/provider new tests first failed for unsupported kinds and missing optional parameters, then 23 targeted tests passed. Worker integration verifies active/disabled outbound payload and immutable scene description.
- Task 2 complete: browser first failed on missing scene control, then passed at 1440/900/390. Old flow passed 1440/390. Screenshots inspected. All images restore; no horizontal overflow.
- Task 3 verification: full pytest 309 passed, one existing Starlette deprecation warning; node syntax and git diff whitespace checks passed. Independent review pending final note.
- Ruling: optional image controls are not identity authorization; hairstyle copy explicitly asks for images without recognizable faces, and provider checks remain intact. No paid generation used in tests. Existing saved custom templates stay unchanged; known built-in scene phrases are normalized only in outbound prompts with scene images active.
- Final independent review: no critical/important findings. Documented optional custom adapter fields. Extra-specific delayed-upload extension remains optional; the existing synchronous capture/version loop and generic delayed upload tests remain unchanged. Ready for isolated release.
- Task 3 complete: deployed dc276cb as isolated ECS release; public 1440/390 new controls/layout/redacted playback checks passed; anonymous401/cross-origin403/old site200. Original service PID and Nginx hashes unchanged. Feature branch retained, no remote push requested.
