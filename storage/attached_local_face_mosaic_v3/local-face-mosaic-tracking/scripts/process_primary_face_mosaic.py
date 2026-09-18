#!/usr/bin/env python3
"""Local primary-face detector, temporal tracker, mosaic compositor, and encoder."""

from __future__ import annotations

import argparse
import json
import math
import platform
import shutil
import subprocess
import time
from pathlib import Path

import cv2
import mediapipe as mp
import numpy as np

from hair_mosaic import HairSegmenter, mosaic_hair


def discover_ffmpeg(explicit=None):
    if explicit:
        path = str(explicit)
        if not Path(path).exists() and not shutil.which(path):
            raise RuntimeError(f"FFmpeg not found: {path}")
        return path
    system_ffmpeg = shutil.which("ffmpeg")
    if system_ffmpeg:
        return system_ffmpeg
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as exc:
        raise RuntimeError(
            "FFmpeg was not found. Install FFmpeg or run: "
            "python -m pip install imageio-ffmpeg"
        ) from exc


def choose_encoder(ffmpeg, requested):
    probe = subprocess.run(
        [ffmpeg, "-hide_banner", "-encoders"],
        capture_output=True, text=True, check=False,
    )
    available = probe.stdout + probe.stderr

    def works(encoder):
        test = subprocess.run(
            [
                ffmpeg, "-hide_banner", "-loglevel", "error",
                "-f", "lavfi", "-i", "color=c=black:s=64x64:d=0.04",
                "-frames:v", "1", "-c:v", encoder, "-f", "null", "-",
            ],
            capture_output=True, check=False,
        )
        return test.returncode == 0

    if requested != "auto":
        if requested not in available or not works(requested):
            raise RuntimeError(f"Requested FFmpeg encoder is unavailable: {requested}")
        return requested

    system = platform.system()
    preferred = []
    if system == "Darwin":
        preferred.append("h264_videotoolbox")
    elif system == "Windows":
        preferred.extend(["h264_nvenc", "h264_qsv", "h264_amf"])
    else:
        preferred.extend(["h264_nvenc", "h264_qsv"])
    preferred.append("libx264")
    for encoder in preferred:
        if encoder in available and works(encoder):
            return encoder
    raise RuntimeError("No supported H.264 encoder was found in FFmpeg")


def encoder_options(encoder):
    options = ["-c:v", encoder, "-pix_fmt", "yuv420p"]
    if encoder == "libx264":
        return options + ["-preset", "ultrafast", "-crf", "22"]
    return options + ["-b:v", "14M"]


def candidate(detection, dw, dh):
    box = detection.location_data.relative_bounding_box
    size = np.array([max(8.0, box.width * dw), max(8.0, box.height * dh)], np.float32)
    center = np.array(
        [(box.xmin + box.width / 2) * dw, (box.ymin + box.height / 2) * dh], np.float32
    )
    return center, size, float(size[0] * size[1])


def choose(candidates, previous_center, previous_area, dw, dh):
    if not candidates:
        return None
    if previous_center is None:
        expected = np.array([0.5 * dw, 0.3 * dh], np.float32)
        return min(
            candidates,
            key=lambda c: 4 * np.linalg.norm((c[0] - expected) / [dw, dh])
            - 0.12 * math.log(max(c[2], 1.0)),
        )

    def score(c):
        distance = np.linalg.norm((c[0] - previous_center) / [dw, dh])
        size_change = abs(math.log(max(c[2], 1.0) / max(previous_area, 1.0)))
        return 5.0 * distance + 0.2 * size_change

    best = min(candidates, key=score)
    distance = np.linalg.norm((best[0] - previous_center) / [dw, dh])
    return best if distance < 0.32 else None


def mosaic(frame, polygon):
    height, width = frame.shape[:2]
    x, y, w, h = cv2.boundingRect(polygon)
    px, py = max(8, int(w * 0.05)), max(8, int(h * 0.05))
    x0, y0 = max(0, x - px), max(0, y - py)
    x1, y1 = min(width, x + w + px), min(height, y + h + py)
    if x1 <= x0 or y1 <= y0:
        return frame
    roi = frame[y0:y1, x0:x1]
    bx = max(7, min(16, (x1 - x0) // 18))
    by = max(7, min(18, (y1 - y0) // 18))
    tiny = cv2.resize(roi, (bx, by), interpolation=cv2.INTER_AREA)
    pixelated = cv2.resize(tiny, (x1 - x0, y1 - y0), interpolation=cv2.INTER_NEAREST)
    local = polygon.copy()
    local[:, 0] -= x0
    local[:, 1] -= y0
    mask = np.zeros((y1 - y0, x1 - x0), np.uint8)
    cv2.fillConvexPoly(mask, cv2.convexHull(local), 255)
    feather = max(3, int(min(w, h) * 0.025)) | 1
    mask = cv2.GaussianBlur(mask, (feather, feather), 0)
    alpha = (mask.astype(np.float32) / 255.0)[..., None]
    roi[:] = (pixelated * alpha + roi * (1 - alpha)).astype(np.uint8)
    return frame


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--ffmpeg", type=Path)
    parser.add_argument("--encoder", default="auto")
    parser.add_argument("--detection-width", type=int, default=540)
    parser.add_argument("--tracking-width", type=int, default=960)
    parser.add_argument(
        "--robust", action="store_true",
        help="Use slower CSRT head/hair tracking during face-detector gaps.",
    )
    parser.add_argument("--no-hair", action="store_true")
    parser.add_argument("--hair-only", action="store_true", help="Skip face mosaic and mask only segmented hair.")
    parser.add_argument("--hair-update-hz", type=float, default=6.0)
    args = parser.parse_args()

    ffmpeg = discover_ffmpeg(args.ffmpeg)
    video_encoder = choose_encoder(ffmpeg, args.encoder)

    started = time.perf_counter()
    cap = cv2.VideoCapture(str(args.input))
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open input: {args.input}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    dw = args.detection_width
    dh = round(height * dw / width)
    tw = min(args.tracking_width, width)
    th = round(height * tw / width)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    hair_segmenter = None if args.no_hair else HairSegmenter(
        Path(__file__).parents[1], width, height, fps, args.hair_update_hz
    )

    command = [
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
        "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{width}x{height}",
        "-r", f"{fps:.8f}", "-i", "pipe:0", "-i", str(args.input),
        "-map", "0:v:0", "-map", "1:a:0?",
        *encoder_options(video_encoder), "-c:a", "aac", "-b:a", "192k",
        "-shortest", "-movflags", "+faststart", str(args.output),
    ]
    encoder = subprocess.Popen(command, stdin=subprocess.PIPE)
    previous_center = previous_size = None
    previous_area = None
    tracker = None
    tracker_box = None
    detected = tracked = held = missing = 0

    def start_tracker(track_frame, face_center, face_size):
        """Track a generous head/hair patch so profile faces survive detector gaps."""
        nonlocal tracker, tracker_box
        scale = np.array([tw / dw, th / dh], np.float32)
        center = face_center * scale
        size = face_size * scale
        box_w = float(np.clip(size[0] * 1.75, 28, tw * 0.45))
        box_h = float(np.clip(size[1] * 1.75, 28, th * 0.55))
        x = float(np.clip(center[0] - box_w / 2, 0, max(0, tw - box_w - 1)))
        y = float(np.clip(center[1] - box_h * 0.58, 0, max(0, th - box_h - 1)))
        tracker_box = tuple(int(round(v)) for v in (x, y, box_w, box_h))
        tracker = cv2.TrackerCSRT_create()
        tracker.init(track_frame, tracker_box)

    try:
        with mp.solutions.face_detection.FaceDetection(
            model_selection=1, min_detection_confidence=0.15
        ) as detector:
            index = 0
            while True:
                ok, frame = cap.read()
                if not ok:
                    break
                hair_mask = (
                    hair_segmenter.mask(frame, index)
                    if hair_segmenter else None
                )
                reduced = cv2.resize(frame, (dw, dh), interpolation=cv2.INTER_AREA)
                track_frame = (
                    cv2.resize(frame, (tw, th), interpolation=cv2.INTER_AREA)
                    if args.robust else None
                )

                predicted_center = previous_center
                predicted_size = previous_size
                tracker_ok = False
                new_tracker_box = None
                if args.robust and tracker is not None and tracker_box is not None:
                    tracker_ok, new_tracker_box = tracker.update(track_frame)
                    if tracker_ok:
                        ox, oy, ow, oh = tracker_box
                        nx, ny, nw, nh = new_tracker_box
                        old_center = np.array([ox + ow / 2, oy + oh / 2], np.float32)
                        new_center = np.array([nx + nw / 2, ny + nh / 2], np.float32)
                        delta = (new_center - old_center) * np.array([dw / tw, dh / th])
                        scale_change = float(np.clip(math.sqrt((nw * nh) / max(ow * oh, 1.0)), 0.85, 1.18))
                        # Reject a tracker jump to a reflection or background texture.
                        if np.linalg.norm(delta / [dw, dh]) < 0.13:
                            predicted_center = previous_center + delta
                            predicted_size = previous_size * scale_change
                            tracker_box = tuple(float(v) for v in new_tracker_box)
                        else:
                            tracker_ok = False

                result = detector.process(cv2.cvtColor(reduced, cv2.COLOR_BGR2RGB))
                options = [candidate(face, dw, dh) for face in (result.detections or [])]
                selected = choose(options, predicted_center, previous_area, dw, dh)
                if selected is not None:
                    raw_center, raw_size, _ = selected
                    if previous_center is None:
                        center, size = raw_center, raw_size
                    else:
                        center = 0.72 * raw_center + 0.28 * predicted_center
                        size = 0.65 * raw_size + 0.35 * predicted_size
                    previous_center, previous_size = center, size
                    previous_area = float(size[0] * size[1])
                    if args.robust:
                        start_tracker(track_frame, center, size)
                    detected += 1
                elif tracker_ok and predicted_center is not None:
                    center, size = predicted_center, predicted_size
                    previous_center, previous_size = center, size
                    previous_area = float(size[0] * size[1])
                    tracked += 1
                elif previous_center is not None:
                    center, size = previous_center, previous_size
                    held += 1
                else:
                    center = size = None
                    missing += 1

                if center is not None:
                    c = center * np.array([width / dw, height / dh], np.float32)
                    s = size * np.array([width / dw, height / dh], np.float32)
                    c[1] -= 0.08 * s[1]
                    angles = np.linspace(0, 2 * np.pi, 48, endpoint=False)
                    radius_x, radius_y = ((1.04, 1.14) if args.robust else (0.90, 1.02))
                    polygon = np.column_stack([
                        c[0] + radius_x * s[0] * np.cos(angles),
                        c[1] + radius_y * s[1] * np.sin(angles),
                    ])
                    polygon[:, 0] = np.clip(polygon[:, 0], 0, width - 1)
                    polygon[:, 1] = np.clip(polygon[:, 1], 0, height - 1)
                    if not args.hair_only:
                        frame = mosaic(frame, polygon.astype(np.int32))
                    if hair_mask is not None:
                        face_boxes = [(
                            float(center[0] / dw), float(center[1] / dh),
                            float(size[0] / dw), float(size[1] / dh),
                        )]
                        frame = mosaic_hair(frame, hair_mask, face_boxes)
                encoder.stdin.write(frame.tobytes())
                index += 1
                if index % 120 == 0:
                    print(f"processed {index}/{frames}", flush=True)
    finally:
        cap.release()
        if encoder.stdin:
            encoder.stdin.close()
        status = encoder.wait()
        if hair_segmenter:
            hair_segmenter.close()

    elapsed = time.perf_counter() - started
    if status:
        raise RuntimeError(f"FFmpeg failed with status {status}")
    result = {
        "input": str(args.input), "output": str(args.output), "width": width,
        "height": height, "fps": fps, "frames": frames, "detected": detected,
        "tracked": tracked, "held": held, "missing": missing,
        "elapsed_seconds": round(elapsed, 3),
        "video_encoder": video_encoder, "processing": "local",
        "mode": "robust" if args.robust else "fast",
        "hair_masking": hair_segmenter is not None,
        "hair_segmentation_refreshes": hair_segmenter.refreshes if hair_segmenter else 0,
    }
    print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
