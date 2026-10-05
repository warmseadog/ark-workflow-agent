from __future__ import annotations

import shutil
import time
from contextlib import ExitStack
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote

import requests

from .config import Settings
from .secure_transport import validate_endpoint


class SeedanceError(RuntimeError):
    """Raised when the Seedance adapter cannot submit or retrieve a result."""

    def __init__(self, message: str, *, submission_uncertain: bool = False):
        super().__init__(message)
        self.submission_uncertain = submission_uncertain


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
        *,
        on_submitting: Callable[[], Any] | None = None,
        on_submitted: Callable[[str], Any] | None = None,
        on_result: Callable[[str], Any] | None = None,
        resume_task_id: str | None = None,
        resume_result_url: str | None = None,
        should_stop: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        structured_prompt = (
            "@Video1 只参考原视频中的动作、镜头运动、构图和节奏。"
            "@Image1 只参考人物身份、脸型、五官和发型。"
            "@Image2 只参考服装款式、颜色、材质和配饰。"
            "保持人物和服装在镜头之间稳定，不新增其他人物。"
            f"用户要求：{prompt.strip()}"
        )
        if self.mode == "mock" and not (resume_task_id or resume_result_url):
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

        try:
            base_url = validate_endpoint(self.settings.seedance_api_url)
        except ValueError as exc:
            raise SeedanceError(str(exc)) from None
        headers = {"Authorization": f"Bearer {self.settings.seedance_api_key}"}
        task_id = resume_task_id
        try:
            if resume_result_url:
                if should_stop and should_stop():
                    raise SeedanceError("任务已暂停，可稍后继续下载。")
                try:
                    self._download_result(resume_result_url, output_path)
                    return {"provider": "http", "task_id": task_id, "output": str(output_path)}
                except SeedanceError:
                    if not task_id:
                        raise
                    # Signed result links can expire; only query the original task.
                    if on_submitted:
                        on_submitted(task_id)
            if not task_id:
                with ExitStack() as stack:
                    files = {"video": stack.enter_context(video_path.open("rb"))}
                    if face_path:
                        files["face_image"] = stack.enter_context(face_path.open("rb"))
                    if clothing_path:
                        files["clothing_image"] = stack.enter_context(clothing_path.open("rb"))
                    if should_stop and should_stop():
                        raise SeedanceError("任务已暂停，尚未提交。")
                    if on_submitting:
                        on_submitting()
                    try:
                        response = requests.post(
                            f"{base_url}/generations", headers=headers,
                            data={"prompt": structured_prompt}, files=files,
                            timeout=60, allow_redirects=False,
                        )
                        if not 200 <= response.status_code < 300:
                            raise SeedanceError(f"Seedance 提交结果无法确认（HTTP {response.status_code}），请核对服务商记录。",
                                                submission_uncertain=True)
                        payload = response.json()
                        task_id = payload.get("task_id") or payload.get("id")
                        if not isinstance(task_id, (str, int)) or not str(task_id).strip():
                            raise SeedanceError("Seedance 提交响应中没有有效 task_id，请核对服务商记录。",
                                                submission_uncertain=True)
                        task_id = str(task_id)
                    except (requests.RequestException, ValueError, TypeError, AttributeError):
                        raise SeedanceError("Seedance 提交结果无法确认，请核对服务商记录，勿重复提交。",
                                            submission_uncertain=True) from None
                    if on_submitted:
                        on_submitted(task_id)
            deadline = time.monotonic() + 1800
            while time.monotonic() < deadline:
                if should_stop and should_stop():
                    raise SeedanceError("任务已暂停，可稍后继续查询。")
                status_response = requests.get(
                    f"{base_url}/tasks/{quote(str(task_id), safe='')}",
                    headers=headers,
                    timeout=30,
                    allow_redirects=False,
                )
                if not 200 <= status_response.status_code < 300:
                    raise SeedanceError(f'Seedance 查询失败（HTTP {status_response.status_code}），请检查接口配置。')
                status = status_response.json()
                state = str(status.get("status", "")).lower()
                if state in {"succeeded", "success", "completed", "done"}:
                    result_url = status.get("output_url") or status.get("video_url")
                    if not result_url:
                        raise SeedanceError("Seedance 任务完成，但响应中没有 output_url。")
                    if on_result:
                        on_result(result_url)
                    if should_stop and should_stop():
                        raise SeedanceError("任务已暂停，可稍后继续下载。")
                    self._download_result(result_url, output_path)
                    return {"provider": "http", "task_id": task_id, "output": str(output_path)}
                if state in {"failed", "error", "cancelled"}:
                    raise SeedanceError('Seedance 任务失败，请在服务商控制台检查任务详情。')
                time.sleep(self.settings.seedance_poll_seconds)
            raise SeedanceError("Seedance 任务等待超时。")
        except (requests.RequestException, ValueError, TypeError):
            raise SeedanceError('Seedance 接口请求失败，请检查接口配置和网络。') from None

    @staticmethod
    def _download_result(url: str, destination: Path) -> None:
        try:
            url = validate_endpoint(url, allow_query=True)
        except ValueError as exc:
            raise SeedanceError(str(exc)) from None
        try:
            with requests.get(url, stream=True, timeout=120, allow_redirects=False) as response:
                if not 200 <= response.status_code < 300:
                    raise SeedanceError('Seedance 视频下载失败，请检查最终 HTTPS 下载地址。')
                with destination.open("wb") as target:
                    for chunk in response.iter_content(chunk_size=1024 * 1024):
                        if chunk:
                            target.write(chunk)
        except requests.RequestException:
            raise SeedanceError('Seedance 视频下载失败，请检查服务商任务结果。') from None
