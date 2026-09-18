# V2 Hair-Aware Masking Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** 将已有的本地头发语义分割与跟踪能力接入 FastAPI 视频打码流程和页面，同时保留现有 deface 人脸打码能力。

**Architecture:** `BlurOptions.mask_mode` 统一描述遮挡目标。默认 `face` 继续调用 ORB-HD/deface；`face_hair_primary`、`hair_primary`、`face_hair_all` 调用仓库内本地跟踪脚本，通过 Python 子进程处理视频、保留音频并输出 MP4。头发模式第一版使用马赛克，页面在选择头发模式时明确提示该约束。

**Tech Stack:** FastAPI, Pydantic, vanilla HTML/CSS/JavaScript, OpenCV, MediaPipe Tasks Vision, NumPy, FFmpeg/imageio-ffmpeg, existing YuNet and selfie multiclass models.

**Spec:** 用户已批准的 v2 设计：增加“人脸 / 人脸+头发 / 只遮头发 / 所有人物脸+头发”模式；保留 deface 兼容路径；接入现有本地头发分割脚本；先支持头发马赛克。

## Global Constraints

- 默认 `mask_mode=face`，现有请求和默认设置行为保持不变。
- 头发相关模式只允许 `style=mosaic`，否则返回清晰的 422 错误。
- 头发模式必须使用仓库内模型，不上传视频到外部服务。
- 视频输出必须保留音频，并使用本地 FFmpeg/硬件编码能力。
- 运行时文件仍保存在 `storage/work/<job_id>`，模型和脚本被版本控制。
- 所有新行为先写失败测试，再写生产代码。

---

### Task 1: Extend validated media options and local processor command

**Files:**
- Modify: `app/media.py`
- Create: `app/local_mosaic.py`
- Test: `tests/test_blur_settings.py`
- Test: `tests/test_local_mosaic.py`

**Interfaces:**
- `BlurOptions.mask_mode: Literal["face", "face_hair_primary", "hair_primary", "face_hair_all"]`
- `BlurOptions.robust_tracking: bool`
- `build_local_mosaic_command(input_path, output_path, options) -> list[str]`
- `run_local_mosaic(input_path, output_path, options) -> Path`
- `run_deface(...) -> Path` dispatches to local processor for non-face modes.

- [ ] **Step 1: Write failing tests** for valid modes, hair-style rejection, command flags, and dispatch.
- [ ] **Step 2: Run `python -m pytest tests/test_blur_settings.py tests/test_local_mosaic.py -q` and confirm failures are caused by missing v2 behavior.
- [ ] **Step 3: Implement a small `app/local_mosaic.py` wrapper that resolves the bundled script directory, maps each mode to the primary/all-face script, appends `--hair-only` and `--robust`, and runs the child process with captured output and a timeout.
- [ ] **Step 4: Extend `BlurOptions`, validate hair mode style, and dispatch from `run_deface` without changing the face-only command.
- [ ] **Step 5: Run focused tests and confirm they pass.

### Task 2: Add hair-only flags to the bundled local processors

**Files:**
- Modify: `storage/attached_local_face_mosaic_v3/local-face-mosaic-tracking/scripts/process_primary_face_mosaic.py`
- Modify: `storage/attached_local_face_mosaic_v3/local-face-mosaic-tracking/scripts/process_all_faces_mosaic.py`
- Test: `tests/test_local_mosaic.py`

**Interfaces:**
- Both scripts accept `--hair-only`.
- In hair-only mode they still segment and mosaic hair but skip face polygon compositing.
- Existing default behavior remains face plus hair.

- [ ] **Step 1: Add a failing source-level behavior test asserting both scripts expose `--hair-only` and guard face compositing.
- [ ] **Step 2: Run the focused test and confirm it fails.
- [ ] **Step 3: Add the flags and conditional face compositing.
- [ ] **Step 4: Run focused tests and confirm they pass.

### Task 3: Thread options through API, job logs, and UI

**Files:**
- Modify: `app/main.py`
- Modify: `app/jobs.py`
- Modify: `app/templates/index.html`
- Modify: `app/static/app.js`
- Modify: `app/static/styles.css`
- Test: `tests/test_ui_layout.py`
- Test: `tests/test_two_stage_flow.py`

**Interfaces:**
- POST `/api/jobs` accepts `mask_mode` and `robust_tracking`.
- Job logs identify the selected mask mode and local tracking.
- UI exposes four target modes and a stable tracking checkbox.
- UI displays a hair-mode note and disables incompatible non-mosaic styles while retaining server-side validation.

- [ ] **Step 1: Add failing tests for new form controls, API route parameters, and job log text.
- [ ] **Step 2: Run focused tests and confirm failures.
- [ ] **Step 3: Add form fields, JavaScript mode/style synchronization, and accessible explanatory text.
- [ ] **Step 4: Parse and pass options in `main.py`; update `jobs.py` logging.
- [ ] **Step 5: Run focused tests and confirm they pass.

### Task 4: Consolidate dependencies and document v2

**Files:**
- Modify: `requirements.txt`
- Modify: `README.md`
- Modify: `.env.example`
- Test: `tests/test_ui_layout.py`

- [ ] **Step 1: Add a failing documentation/layout assertion for the v2 mask modes and local model requirements.
- [ ] **Step 2: Run the focused test and confirm it fails.
- [ ] **Step 3: Add MediaPipe, NumPy, OpenCV contrib, imageio-ffmpeg, python-dotenv, and deface to the install requirements with compatible ranges; remove the separate manual deface install instruction.
- [ ] **Step 4: Document the four modes, primary/all-person behavior, hair-mode mosaic constraint, and model files.
- [ ] **Step 5: Run the complete test suite.

### Task 5: Verify and tag v2

**Files:**
- No source changes unless verification exposes a defect.

- [ ] **Step 1: Run compile checks for `app` and local scripts.
- [ ] **Step 2: Run `python -m pytest -q` with a workspace-local basetemp.
- [ ] **Step 3: Run a command-construction smoke check for all mask modes.
- [ ] **Step 4: Confirm the worktree diff contains no runtime video/output files.
- [ ] **Step 5: Commit as `feat: integrate hair-aware masking modes`.
- [ ] **Step 6: Create annotated tag `v2` and verify it points at HEAD.

