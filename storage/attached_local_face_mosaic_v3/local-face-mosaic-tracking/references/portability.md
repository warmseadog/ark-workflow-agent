# Portable setup and batch use

Use this reference only when installing the skill on another computer or processing a folder of videos.

## Install on another computer

1. Copy the entire `local-face-mosaic-tracking` folder into the user's Codex skills directory. Typical locations are `~/.codex/skills/` on macOS/Linux and `%USERPROFILE%\.codex\skills\` on Windows.
2. Use Python 3.10 or 3.11 when possible.
3. From the skill folder, install the local runtime with `python -m pip install -r requirements.txt` (use `python3` where required).
4. Restart Codex so it discovers the copied skill.

The dependency installation may download packages and an FFmpeg binary once. Video processing itself stays local.

## Hardware selection

The processor discovers FFmpeg automatically. It prefers Apple VideoToolbox on macOS; NVIDIA NVENC, Intel Quick Sync, or AMD AMF on Windows; NVIDIA NVENC or Intel Quick Sync on Linux; and `libx264` CPU encoding as the portable fallback. Pass `--encoder NAME` only when automatic selection is unsuitable.

Face detection and OpenCV compositing remain primarily CPU work. A faster GPU helps mainly when the selected FFmpeg build exposes a supported hardware encoder. Do not promise GPU scaling without benchmarking the target machine.

## Batch processing

Run:

```text
python scripts/batch_face_mosaic.py INPUT_FOLDER OUTPUT_FOLDER
```

The default is primary-face fast mode with hair-aware masking, one worker, non-recursive, and skip-existing. Useful options are `--all-faces`, `--recursive`, `--workers 2`, `--robust`, `--no-hair`, and `--overwrite`. `--robust` applies to primary-face mode; all-face mode uses its own shot-aware multi-face tracker. Use `--no-hair` only for an explicitly requested face-only speed run.

For an unfamiliar computer, benchmark one representative clip first. For a large batch, try one and two workers on two clips; keep two workers only when measured wall-clock time improves without encoder failures or thermal throttling. Do not multiply conversational latency per file: launch one batch command and report its final summary.

Estimate completion from the representative clip's measured ratio:

```text
estimated batch seconds = total source duration × sample processing seconds / sample duration
```

Add 10–25% for file setup, verification, and occasional retries. Report pure processing time separately from total delivery time.
