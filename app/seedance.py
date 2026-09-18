from __future__ import annotations

import shutil
import time
from pathlib import Path
from typing import Any

import requests

from .config import Settings


class SeedanceError(RuntimeError):
    """Raised when the Seedance adapter cannot submit or retrieve a result."""


class SeedanceClient:
    """Seedance adapter with a local mock and a configurable HTTP contract.

    The mock mode is intentional: it allows the full upload/deface/download UI to
    be tested before API credentials and a provider-specific SDK are configured.
    The HTTP mode expects POST /generations and GET /tasks/{task_id}; the payload
    is documented in README.md and can be adapted to the selected Seedance API.
    """

    def __init__(self, settings: Settings):
        self.settings = settings

    @property
    def mode(self) -> str:
        return self.settings.seedance_mode

    def generate(
        self,
        video_path: Path,
        face_path: Path | None,
        clothing_path: Path | None,
        prompt: str,
        output_path: Path,
    ) -> dict[str, Any]:
        structured_prompt = (
            "@Video1 只参考原视频中的动作、镜头运动、构图和节奏。"
            "@Image1 只参考人物身份、脸型、五官和发型。"
            "@Image2 只参考服装款式、颜色、材质和配饰。"
            "保持人物和服装在镜头之间稳定，不新增其他人物。"
            f"用户要求：{prompt.strip()}"
        )
        if self.mode == "mock":
            shutil.copyfile(video_path, output_path)
            return {
                "provider": "mock",
                "message": "Seedance 未配置，已用打码后视频作为本地演示输出。",
                "output": str(output_path),
            }
        if self.mode != "http":
            raise SeedanceError(f"不支持的 SEEDANCE_MODE：{self.mode}")
        if not self.settings.seedance_api_url or not self.settings.seedance_api_key:
            raise SeedanceError("SEEDANCE_MODE=http 时必须配置 SEEDANCE_API_URL 和 SEEDANCE_API_KEY。")

        headers = {"Authorization": f"Bearer {self.settings.seedance_api_key}"}
        files = {"video": video_path.open("rb")}
        if face_path:
            files["face_image"] = face_path.open("rb")
        if clothing_path:
            files["clothing_image"] = clothing_path.open("rb")
        try:
            response = requests.post(
                f"{self.settings.seedance_api_url}/generations",
                headers=headers,
                data={"prompt": structured_prompt},
                files=files,
                timeout=60,
            )
            response.raise_for_status()
            payload = response.json()
            task_id = payload.get("task_id") or payload.get("id")
            if not task_id:
                raise SeedanceError("Seedance 提交响应中没有 task_id。")
            deadline = time.time() + 1800
            while time.time() < deadline:
                status_response = requests.get(
                    f"{self.settings.seedance_api_url}/tasks/{task_id}",
                    headers=headers,
                    timeout=30,
                )
                status_response.raise_for_status()
                status = status_response.json()
                state = str(status.get("status", "")).lower()
                if state in {"succeeded", "success", "completed", "done"}:
                    result_url = status.get("output_url") or status.get("video_url")
                    if not result_url:
                        raise SeedanceError("Seedance 任务完成，但响应中没有 output_url。")
                    self._download_result(result_url, output_path)
                    return {"provider": "http", "task_id": task_id, "output": str(output_path)}
                if state in {"failed", "error", "cancelled"}:
                    raise SeedanceError(status.get("error") or "Seedance 任务失败。")
                time.sleep(self.settings.seedance_poll_seconds)
            raise SeedanceError("Seedance 任务等待超时。")
        finally:
            for file_obj in files.values():
                file_obj.close()

    @staticmethod
    def _download_result(url: str, destination: Path) -> None:
        with requests.get(url, stream=True, timeout=120) as response:
            response.raise_for_status()
            with destination.open("wb") as target:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        target.write(chunk)
