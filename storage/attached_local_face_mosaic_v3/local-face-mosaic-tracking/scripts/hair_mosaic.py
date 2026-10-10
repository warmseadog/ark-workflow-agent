"""Hair-only semantic masking helpers for local face mosaic scripts."""

from __future__ import annotations

from pathlib import Path

import cv2
import mediapipe as mp
import numpy as np


class HairSegmenter:
    """Run the bundled multiclass model sparsely and retain its hair-only mask."""

    def __init__(self, skill_dir: Path, width: int, height: int, fps: float, update_hz=6.0):
        model = skill_dir / "models" / "selfie_multiclass_256x256.tflite"
        if not model.exists():
            raise RuntimeError(f"Hair segmentation model is missing: {model}")
        self.width = 256
        self.height = max(64, round(height * self.width / width))
        self.interval = max(1, round(fps / update_hz))
        options = mp.tasks.vision.ImageSegmenterOptions(
            base_options=mp.tasks.BaseOptions(model_asset_buffer=model.read_bytes()),
            output_category_mask=True,
        )
        self.segmenter = mp.tasks.vision.ImageSegmenter.create_from_options(options)
        self.current = None
        self.refreshes = 0

    def mask(self, frame, frame_index: int, force=False):
        if self.current is None or force or frame_index % self.interval == 0:
            small = cv2.resize(frame, (self.width, self.height), interpolation=cv2.INTER_AREA)
            image = mp.Image(
                image_format=mp.ImageFormat.SRGB,
                data=cv2.cvtColor(small, cv2.COLOR_BGR2RGB),
            )
            result = self.segmenter.segment(image)
            # Official model classes: 1 hair, 3 face skin, 4 clothes.
            hair = (result.category_mask.numpy_view() == 1).astype(np.uint8)
            hair = cv2.morphologyEx(hair, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
            # One low-resolution pixel catches bun/flyaway edges without swallowing clothing.
            self.current = cv2.dilate(hair, np.ones((3, 3), np.uint8), iterations=1)
            self.refreshes += 1
        return self.current

    def close(self):
        self.segmenter.close()


def mosaic_hair(frame, hair_mask, face_boxes=None, mosaic_size=None):
    """Pixelate hair components, optionally only those connected to target faces."""
    count, labels, stats, _ = cv2.connectedComponentsWithStats(hair_mask, 8)
    frame_h, frame_w = frame.shape[:2]
    mask_h, mask_w = hair_mask.shape
    allowed = set()

    for label in range(1, count):
        x, y, w, h, area = stats[label]
        if area < 5:
            continue
        if face_boxes:
            component = labels == label
            keep = False
            for cx, cy, fw, fh in face_boxes:
                sx0 = int(np.clip((cx - 0.85 * fw) * mask_w, 0, mask_w - 1))
                sx1 = int(np.clip((cx + 0.85 * fw) * mask_w, 0, mask_w))
                sy0 = int(np.clip((cy - 1.10 * fh) * mask_h, 0, mask_h - 1))
                sy1 = int(np.clip((cy + 0.50 * fh) * mask_h, 0, mask_h))
                if sx1 > sx0 and sy1 > sy0 and component[sy0:sy1, sx0:sx1].any():
                    keep = True
                    break
            if not keep:
                continue
        allowed.add(label)

    for label in allowed:
        x, y, w, h, _ = stats[label]
        x0 = max(0, int(x * frame_w / mask_w) - 4)
        y0 = max(0, int(y * frame_h / mask_h) - 4)
        x1 = min(frame_w, int((x + w) * frame_w / mask_w) + 4)
        y1 = min(frame_h, int((y + h) * frame_h / mask_h) + 4)
        if x1 <= x0 or y1 <= y0:
            continue
        local_mask = cv2.resize(
            (labels[y:y + h, x:x + w] == label).astype(np.uint8) * 255,
            (x1 - x0, y1 - y0), interpolation=cv2.INTER_NEAREST,
        )
        feather = max(3, int(min(x1 - x0, y1 - y0) * 0.015)) | 1
        local_mask = cv2.GaussianBlur(local_mask, (feather, feather), 0)
        roi = frame[y0:y1, x0:x1]
        bx = max(7, min(20, (x1 - x0) // 14))
        by = max(7, min(24, (y1 - y0) // 14))
        if mosaic_size is not None:
            bx, by = max(1, (x1 - x0) // mosaic_size), max(1, (y1 - y0) // mosaic_size)
        tiny = cv2.resize(roi, (bx, by), interpolation=cv2.INTER_AREA)
        pixels = cv2.resize(tiny, (x1 - x0, y1 - y0), interpolation=cv2.INTER_NEAREST)
        alpha = (local_mask.astype(np.float32) / 255.0)[..., None]
        roi[:] = (pixels * alpha + roi * (1 - alpha)).astype(np.uint8)
    return frame
