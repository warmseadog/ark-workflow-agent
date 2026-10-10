---
name: local-face-mosaic-tracking
description: Quickly add local, temporally tracked mosaics to the primary person or every visible face and hairstyle in one video or a large batch, preserve audio, verify coverage, and report elapsed time and estimated compute cost. Use when the user asks to blur, pixelate, censor, or 打码 faces or hair without uploading videos or spending generation credits.
---

# Local Face Mosaic Tracking

Process the supplied video entirely on the user's computer. Do not upload it to ChatCut or another service unless the user separately asks for that.

## Workflow

1. Confirm the exact readable input path and derive a new output filename; never overwrite the source.
2. Speed is the default priority. First run `scripts/process_primary_face_mosaic.py INPUT OUTPUT` in fast mode. The script discovers FFmpeg and a supported H.264 encoder automatically.
3. When the user says every/all faces or 所有人脸, run `scripts/process_all_faces_mosaic.py INPUT OUTPUT`. This uses the bundled YuNet model, resets tracks at shot changes, and mosaics every accepted face rather than selecting one identity.
4. For a folder or a large batch, read [references/portability.md](references/portability.md), benchmark one representative clip, then run `scripts/batch_face_mosaic.py` once for the whole folder.
5. For primary-face mode, `missing=0` is required. For all-face mode, inspect the contact sheet because frames can legitimately contain no face; logs alone cannot prove complete coverage.
6. Verify codec, dimensions, duration, and audio with `mdls`, `ffprobe`, or equivalent.
7. Extract one contact sheet covering the whole timeline. Inspect denser samples only when it reveals a questionable interval; do not spend time on redundant checks.
8. If fast primary-face mode visibly exposes a substantial part of the face or loses the main person, rerun with `--robust`. Do not use it merely to chase tiny cosmetic imperfections.

## Quality standard

- Track the primary person, not simply the largest face in every frame.
- The mask follows the detected facial-skin box but expands beyond forehead, cheeks, jaw, and side profile. Prefer slightly excessive coverage over exposing one-third or half of a face.
- Mask the complete hairstyle by default, including buns, ponytails, crown volume, flyaways, and long hair. Use the bundled multiclass semantic model's hair class instead of enlarging the face ellipse. It separates hair from face skin, body skin, and clothes, so long hair can be mosaicked down its full length without covering the shirt or dress underneath.
- Refresh hair segmentation at about 6 Hz and immediately at shot changes to balance edge coverage and speed. A one-pixel low-resolution dilation is the safety margin for bun and flyaway edges; do not increase it enough to swallow clothing.
- Fast mode uses face detection, temporal smoothing, and short position holds. Robust mode adds local CSRT head/hair texture tracking for genuine profile-turn or detector-gap failures.
- All-face mode uses the bundled YuNet detector, multi-face association, short motion prediction, and shot-change resets. Slight false-positive coverage is preferable to leaving a real bystander identifiable.
- Preserve the original orientation, frame rate, duration, and audio.
- Deliver an H.264/AAC MP4 unless the user requests another format.

## Cost and privacy reporting

Report measured processing time separately from one-time dependency setup and visual review time. State that inference and hardware encoding are local and use the computer's CPU/GPU/media engine. They consume electricity and hardware time but no per-video API or ChatCut generation fee. Estimate electricity from a conservative 20–100 W device-power range and the measured processing seconds; do not present it as an exact meter reading.

Hair masking is enabled by default and is slower than face-only masking because the semantic model must classify hair separately from clothing. Use `--no-hair` only when the user explicitly prioritizes absolute speed over hairstyle replacement safety.

For installation on another computer, supported encoder routing, or batch options, read [references/portability.md](references/portability.md). The portable dependencies are pinned in `requirements.txt`. Installing them is a one-time setup and may use internet bandwidth, but processing the user's videos is local.
