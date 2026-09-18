#!/usr/bin/env python3
"""Detect and mosaic every visible face locally with lightweight multi-track hold."""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import tempfile
import time
from pathlib import Path

import cv2
import numpy as np

from process_primary_face_mosaic import (
    choose_encoder,
    discover_ffmpeg,
    encoder_options,
    mosaic,
)
from hair_mosaic import HairSegmenter, mosaic_hair


def create_yunet_detector(model, input_size, score_threshold):
    """Create YuNet, falling back to an ASCII temp path on Windows."""
    try:
        return cv2.FaceDetectorYN.create(
            str(model), "", input_size, score_threshold, 0.3, 5000
        )
    except cv2.error as original:
        fallback_path = None
        try:
            with tempfile.NamedTemporaryFile(prefix="yunet_", suffix=".onnx", delete=False) as handle:
                handle.write(model.read_bytes())
                fallback_path = Path(handle.name)
            return cv2.FaceDetectorYN.create(
                str(fallback_path), "", input_size, score_threshold, 0.3, 5000
            )
        except Exception:
            raise original
        finally:
            if fallback_path:
                fallback_path.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--ffmpeg", type=Path)
    parser.add_argument("--encoder", default="auto")
    parser.add_argument("--detection-width", type=int, default=540)
    parser.add_argument("--score-threshold", type=float, default=0.65)
    parser.add_argument("--hold-frames", type=int, default=12)
    parser.add_argument("--no-hair", action="store_true")
    parser.add_argument("--hair-only", action="store_true", help="Skip face mosaic and mask only segmented hair.")
    parser.add_argument("--hair-update-hz", type=float, default=6.0)
    args = parser.parse_args()

    model = Path(__file__).parents[1] / "models" / "face_detection_yunet_2023mar.onnx"
    if not model.exists():
        raise RuntimeError(f"YuNet face detector model is missing: {model}")
    ffmpeg = discover_ffmpeg(args.ffmpeg)
    video_encoder = choose_encoder(ffmpeg, args.encoder)

    started = time.perf_counter()
    cap = cv2.VideoCapture(str(args.input))
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open input: {args.input}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    expected_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    dw = args.detection_width
    dh = round(height * dw / width)
    args.output.parent.mkdir(parents=True, exist_ok=True)

    detector = create_yunet_detector(model, (dw, dh), args.score_threshold)
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

    tracks = []
    previous_scene = None
    frame_index = direct_faces = held_faces = frames_with_masks = max_faces = 0

    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            reduced = cv2.resize(frame, (dw, dh), interpolation=cv2.INTER_AREA)
            scene = cv2.resize(cv2.cvtColor(reduced, cv2.COLOR_BGR2GRAY), (96, 96))
            cut = previous_scene is not None and cv2.mean(cv2.absdiff(scene, previous_scene))[0] > 25
            previous_scene = scene
            if cut:
                tracks = []

            hair_mask = (
                hair_segmenter.mask(frame, frame_index, force=cut)
                if hair_segmenter else None
            )

            detector.setInputSize((dw, dh))
            _, faces = detector.detect(reduced)
            detections = []
            if faces is not None:
                for face in faces:
                    x, y, w, h = face[:4]
                    if w < 7 or h < 7:
                        continue
                    detections.append({
                        "center": np.array([x + w / 2, y + h / 2], np.float32),
                        "size": np.array([w, h], np.float32),
                    })
            direct_faces += len(detections)

            pairs = []
            for ti, track in enumerate(tracks):
                predicted = track["center"] + track["velocity"]
                for di, detection in enumerate(detections):
                    distance = np.linalg.norm((detection["center"] - predicted) / [dw, dh])
                    size_change = abs(math.log(
                        max(float(np.prod(detection["size"])), 1.0)
                        / max(float(np.prod(track["size"])), 1.0)
                    ))
                    pairs.append((distance + 0.04 * size_change, ti, di))

            matched_tracks = set()
            matched_detections = set()
            for score, ti, di in sorted(pairs):
                if score > 0.24 or ti in matched_tracks or di in matched_detections:
                    continue
                track, detection = tracks[ti], detections[di]
                predicted = track["center"] + track["velocity"]
                center = 0.78 * detection["center"] + 0.22 * predicted
                track["velocity"] = 0.65 * track["velocity"] + 0.35 * (center - track["center"])
                track["center"] = center
                track["size"] = 0.72 * detection["size"] + 0.28 * track["size"]
                track["missed"] = 0
                matched_tracks.add(ti)
                matched_detections.add(di)

            next_tracks = []
            for ti, track in enumerate(tracks):
                if ti not in matched_tracks:
                    track["center"] = track["center"] + track["velocity"]
                    track["velocity"] *= 0.82
                    track["missed"] += 1
                    held_faces += 1
                if track["missed"] <= args.hold_frames:
                    next_tracks.append(track)
            for di, detection in enumerate(detections):
                if di not in matched_detections:
                    next_tracks.append({
                        "center": detection["center"], "size": detection["size"],
                        "velocity": np.zeros(2, np.float32), "missed": 0,
                    })
            tracks = next_tracks
            max_faces = max(max_faces, len(tracks))

            if tracks:
                frames_with_masks += 1
            for track in tracks:
                c = track["center"] * np.array([width / dw, height / dh], np.float32)
                s = track["size"] * np.array([width / dw, height / dh], np.float32)
                c[1] -= 0.06 * s[1]
                angles = np.linspace(0, 2 * np.pi, 48, endpoint=False)
                polygon = np.column_stack([
                    c[0] + 0.98 * s[0] * np.cos(angles),
                    c[1] + 1.10 * s[1] * np.sin(angles),
                ])
                polygon[:, 0] = np.clip(polygon[:, 0], 0, width - 1)
                polygon[:, 1] = np.clip(polygon[:, 1], 0, height - 1)
                if not args.hair_only:
                    frame = mosaic(frame, polygon.astype(np.int32))

            if hair_mask is not None:
                frame = mosaic_hair(frame, hair_mask)

            encoder.stdin.write(frame.tobytes())
            frame_index += 1
            if frame_index % 120 == 0:
                print(f"processed {frame_index}/{expected_frames}", flush=True)
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
    print(json.dumps({
        "input": str(args.input), "output": str(args.output),
        "width": width, "height": height, "fps": fps, "frames": frame_index,
        "direct_face_detections": direct_faces, "held_face_masks": held_faces,
        "frames_with_masks": frames_with_masks, "max_simultaneous_faces": max_faces,
        "elapsed_seconds": round(elapsed, 3), "video_encoder": video_encoder,
        "processing": "local", "mode": "all_faces",
        "hair_masking": hair_segmenter is not None,
        "hair_segmentation_refreshes": hair_segmenter.refreshes if hair_segmenter else 0,
    }, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
