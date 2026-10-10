from dataclasses import replace
from io import BytesIO
import asyncio

from fastapi import UploadFile


def test_empty_file_field_does_not_conflict_with_share_text(monkeypatch, tmp_path):
    from app import jobs, main

    store = jobs.JobStore()
    monkeypatch.setattr(main, "store", store)
    monkeypatch.setattr(main, "settings", replace(main.settings, storage_dir=tmp_path))

    started = []

    class HeldThread:
        def __init__(self, target, args, daemon):
            self.args = args

        def start(self):
            started.append(self.args)

    monkeypatch.setattr(main, "Thread", HeldThread)

    asyncio.run(
        main.create_job(
            video=UploadFile(filename="", file=BytesIO()),
            video_url="复制打开抖音 https://v.douyin.com/abc123/。",
            candidate_id=None,
            replace_image=None,
            blur_style="mosaic",
            blur_shape="ellipse",
            mask_mode="face",
            robust_tracking=None,
            mask_scale=1.4,
            mosaic_size=20,
            threshold=0.2,
            detection_size=None,
            keep_audio=True,
        )
    )

    assert len(started) == 1
    assert started[0][2] is None
    assert started[0][3] == "https://v.douyin.com/abc123/"




