"""Provider-specific video generation; uploads happen only for an explicit job."""
from __future__ import annotations

import base64
from contextlib import ExitStack, nullcontext
import logging
import mimetypes
from pathlib import Path
import re
import shutil
import time
import threading
from typing import Callable
from urllib.parse import quote, urlsplit

import requests

from .generation_settings import GenerationConfig
from .reference_roles import ACCESSORY_LABELS, ACCESSORY_RULES
from .audio_policy import AUDIO_COPYRIGHT_CODE


# The service runs one process with multiple production workers. Serialize only
# upload requests, not provider generation/polling or result downloads.
_upload_lock = threading.Lock()
_logger = logging.getLogger(__name__)


def _transport_errors(error):
    """Unwrap requests/urllib3 exceptions without logging their private text."""
    pending, seen, errors = [error], set(), []
    while pending:
        current = pending.pop()
        if not isinstance(current, BaseException) or id(current) in seen:
            continue
        seen.add(id(current))
        errors.append(current)
        pending.extend(current.args)
        pending.extend([current.__cause__, current.__context__])
    return errors


class ProviderError(RuntimeError):
    def __init__(self, message: str, *, retryable: bool = False,
                 error_kind: str = 'processing_failed', request_id: str | None = None,
                 provider_task_id: str | None = None, submission_uncertain: bool = False):
        super().__init__(message)
        self.retryable = retryable
        self.error_kind = error_kind
        self.request_id = request_id
        self.provider_task_id = provider_task_id
        self.submission_uncertain = submission_uncertain


def reference_order(faces: list[Path], clothes: list[Path]) -> list[tuple[Path, str]]:
    # Preserve the templates' @Image1 = person and @Image2 = clothes.
    ordered = [(p, '人物') for p in faces[:1]] + [(p, '衣服') for p in clothes[:1]]
    return ordered + [(p, '人物补充') for p in faces[1:]] + [(p, '衣服补充') for p in clothes[1:]]


class VideoProvider:
    def __init__(self, config: GenerationConfig, poll_seconds: float = 5, progress=None):
        self.config = config
        self._last_request_id: str | None = None
        self._content_roles = {}
        self.poll_seconds = poll_seconds
        self.progress = progress or (lambda message, percent: None)

    def _safe(self, value) -> str:
        if isinstance(value, dict):
            value = ' '.join(str(value[k]) for k in ('code','message') if value.get(k)) or '服务商未提供错误说明'
        text = str(value or '服务商未提供错误说明')
        if self.config.api_key:
            text = text.replace(self.config.api_key, '[已隐藏]')
        text = re.sub(r'data:[^\s]+', '[媒体内容已隐藏]', text)
        text = re.sub(r'https?://[^\s]+', '[资源地址已隐藏]', text, flags=re.I)
        text = re.sub(r'Bearer\s+\S+', 'Bearer [已隐藏]', text, flags=re.I)
        return text[:600]

    def _provider_error(self, message: str, *, detail=None, error_kind='processing_failed',
                        retryable=False, submission_uncertain=False) -> ProviderError:
        safe_detail = self._safe(detail) if detail is not None else ''
        material = str(detail).lower() if detail is not None else ''
        if AUDIO_COPYRIGHT_CODE.lower() in material:
            return ProviderError(message + safe_detail, error_kind='audio_copyright', request_id=self._last_request_id)
        if 'real person' in material or 'real_person' in material:
            error_kind, retryable, submission_uncertain = 'material_rejected', False, False
            indices = list(dict.fromkeys(int(x) for x in re.findall(r'content\s*\[\s*(\d+)\s*\]', material)))
            labels = [self._content_roles.get(i, f'content[{i}] 参考素材') for i in indices]
            reference = '、'.join(labels) or '参考素材'
            message = f'{reference}被服务商判定可能包含真人，请检查对应素材后重新提交。{safe_detail}'
        elif safe_detail:
            message += safe_detail
        return ProviderError(message, error_kind=error_kind, request_id=self._last_request_id,
                             retryable=retryable, submission_uncertain=submission_uncertain)

    def _request(self, method: str, path: str, **kwargs) -> dict:
        self._last_request_id = None
        submitting = method == 'POST' and path != '/uploads/videos'
        kind = 'query_unavailable' if method == 'GET' else ('submission_uncertain' if submitting else 'processing_failed')
        timeout = (60, 120) if method == 'POST' else (15, 30)
        started = None
        try:
            with _upload_lock if method == 'POST' else nullcontext():
                # requests uses the connection timeout while writing the body.
                # The old 15 seconds was insufficient for inline reference images.
                started = time.monotonic()
                response = requests.request(method, f'{self.config.base_url.rstrip("/")}{path}',
                    headers={'Authorization': f'Bearer {self.config.api_key}'},
                    timeout=timeout, allow_redirects=False, **kwargs)
        except requests.RequestException as exc:
            errors = _transport_errors(exc)
            if any(isinstance(error, requests.ConnectTimeout) for error in errors):
                phase, reason = 'connect', '连接模型接口超时，请稍后重试。'
            elif any(isinstance(error, TimeoutError) and 'write' in str(error).lower() for error in errors):
                phase, reason = 'write', '参考素材上传超时，请压缩参考图或稍后重试。'
            elif any(isinstance(error, requests.ReadTimeout) for error in errors):
                phase, reason = 'read', '等待模型接口响应超时。'
            elif any(isinstance(error, (requests.Timeout, TimeoutError)) for error in errors):
                phase, reason = 'timeout', '模型接口请求超时。'
            else:
                phase, reason = 'connection', '无法连接模型接口，请检查地址、端口和网络。'
            # No exception text, URL, headers, prompt or body enters the log.
            _logger.warning('provider_transport_error method=%s phase=%s elapsed_seconds=%.3f timeout=%s exception_types=%s',
                            method, phase, time.monotonic() - started if started is not None else 0,
                            timeout, ','.join(type(error).__name__ for error in errors))
            if submitting:
                reason += '提交结果不确定，请先在服务商控制台核对，避免重复提交。'
            raise self._provider_error(reason, error_kind=kind, retryable=method == 'GET', submission_uncertain=submitting) from None
        self._last_request_id = self._request_id(response.headers)
        try:
            payload = response.json()
        except ValueError:
            raise self._provider_error(f'模型接口返回了非 JSON 内容（HTTP {response.status_code}），请检查接口地址和格式。',
                error_kind=kind, retryable=method == 'GET', submission_uncertain=submitting) from None
        if isinstance(payload, dict):
            self._last_request_id = self._request_id(payload) or self._last_request_id
            if isinstance(payload.get('error'), dict):
                self._last_request_id = self._request_id(payload['error']) or self._last_request_id
        if not 200 <= response.status_code < 300:
            detail = (payload.get('error') or payload.get('message') or payload.get('detail')) if isinstance(payload, dict) else None
            uncertain = submitting and (response.status_code >= 500 or response.status_code in {408, 409})
            error_kind = 'query_unavailable' if method == 'GET' else ('submission_uncertain' if uncertain else 'configuration')
            raise self._provider_error(f'模型接口错误（HTTP {response.status_code}）：', detail=detail,
                error_kind=error_kind, retryable=method == 'GET' and response.status_code in {429, 500, 502, 503, 504},
                submission_uncertain=uncertain)
        if not isinstance(payload, dict):
            raise self._provider_error('模型接口响应格式不正确，请检查所选接口格式。', error_kind=kind,
                                       retryable=method == 'GET', submission_uncertain=submitting)
        if payload.get('success') is False or (payload.get('error') and not payload.get('status')):
            raise self._provider_error('', detail=payload.get('error') or payload.get('message'),
                                       error_kind='query_unavailable' if method == 'GET' else 'processing_failed')
        return payload

    def _request_id(self, values) -> str | None:
        for name in ('request_id', 'requestId', 'RequestId', 'X-Request-Id', 'X-Tt-Logid'):
            value = values.get(name)
            if isinstance(value, str) and value.strip():
                return self._safe(value)
        return None

    @staticmethod
    def _data_url(path: Path) -> str:
        mime = mimetypes.guess_type(path.name)[0] or 'image/png'
        if not mime.startswith('image/'):
            raise ProviderError('参考图格式无法识别，请使用 PNG、JPEG 或 WebP 图片。')
        return f'data:{mime};base64,{base64.b64encode(path.read_bytes()).decode("ascii")}'

    def generate(self, video: Path, faces: list[Path], clothes: list[Path], prompt: str,
                 output: Path, *, video_url: str | None = None,
                 on_submitted: Callable[[str], None] | None = None,
                 on_result: Callable[[str], None] | None = None,
                 resume_task_id: str | None = None, resume_result_url: str | None = None,
                 image_asset_uris: dict[str, str] | None = None,
                 person_video: Path | None = None, person_video_uri: str | None = None,
                 hairstyles: list[Path] | None = None, scenes: list[Path] | None = None,
                 scene_description: str = '', accessories: dict[str, list[Path]] | None = None,
                 reference_roles: dict[int, str] | None = None) -> dict:
        self._content_roles = reference_roles or {}
        # Recovery needs only the durable remote identity/result, never source files.
        if resume_result_url is not None:
            try:
                return self._download_result(resume_result_url, output, resume_task_id)
            except ProviderError as exc:
                if not resume_task_id or exc.error_kind != 'download_failed':
                    raise
                # Signed result URLs can expire. Refresh through the existing task;
                # _poll persists its new result URL and never submits another task.
                return self._poll(resume_task_id, output, on_result)
        if resume_task_id is not None:
            return self._poll(resume_task_id, output, on_result)
        from .model_catalog import capabilities
        limits = capabilities(self.config.model, self.config.protocol)
        if person_video is not None:
            if faces or self.config.protocol!='ark' or self.config.base_url.rstrip('/')!='https://ark.cn-beijing.volces.com/api/v3' or not isinstance(person_video_uri,str) or not re.fullmatch(r'asset://asset-[A-Za-z0-9-]{1,114}',person_video_uri):
                raise ProviderError('人物视频需使用火山官方已入库素材，且不能同时传入人物图片。',error_kind='configuration')
            from .person_video import validate_pair
            validate_pair(video,person_video,max_seconds=limits['max_video_seconds'])
        elif person_video_uri is not None:
            raise ProviderError('人物视频与授权编号不匹配。',error_kind='configuration')
        elif limits['follow_source']:
            from .person_video import validate_file
            validate_file(video,person=False,max_seconds=limits['max_video_seconds'])
        image_asset_uris = image_asset_uris or {}
        if image_asset_uris:
            if self.config.protocol != 'ark' or self.config.base_url.rstrip('/') != 'https://ark.cn-beijing.volces.com/api/v3':
                raise ProviderError('官方人物素材仅支持火山官方接口，请检查模型配置。',error_kind='configuration')
            if any(not re.fullmatch(r'asset://asset-[A-Za-z0-9-]{1,114}', value) for value in image_asset_uris.values()):
                raise ProviderError('官方人物素材编号无效，请重新导入。',error_kind='configuration')
            if set(image_asset_uris) - {str(path) for path in faces}:
                raise ProviderError('官方人物素材与所选人物图不匹配。',error_kind='configuration')
        if self.config.mode == 'mock':
            shutil.copyfile(video, output)
            return {'provider': 'mock', 'message': '本地演示：输出打码视频，未调用视频模型。'}
        problem = self.config.generation_problem(video_available=bool(video_url))
        if problem:
            raise ProviderError(problem, error_kind='configuration')
        hairstyles, scenes = hairstyles or [], scenes or []
        accessories = accessories or {}
        if set(accessories) - set(ACCESSORY_LABELS) or any(len(paths)>1 for paths in accessories.values()):
            raise ProviderError('配饰类别或图片数量不正确。', error_kind='configuration')
        references = reference_order(faces, clothes) + [(p,'发型') for p in hairstyles] + [(p,'场景') for p in scenes]
        references += [(p,label) for kind,label in ACCESSORY_LABELS.items() for p in accessories.get(kind,[])]
        self._content_roles = {i: f'第 {i} 张{kind}参考图' for i,(_,kind) in enumerate(references,1)}
        self._content_roles[len(references)+1] = '参考视频'
        if person_video is not None:self._content_roles[len(references)+2] = '人物参考视频'
        if self.config.protocol != 'adapter':
            if len(references) > limits['max_images']:
                raise ProviderError(f"人物、衣服、发型、场景和配饰参考图合计最多 {limits['max_images']} 张，请删除部分图片后重试。")
            if video.stat().st_size > 50 * 1024 * 1024:
                raise ProviderError('参考视频超过 50 MB，请压缩或缩短视频后重试。')
            if sum(p.stat().st_size for p, _ in references) > 45 * 1024 * 1024:
                raise ProviderError('参考图片总大小过大，请压缩图片后重试。')
        from .reference_prompt import strip_reference_rules, normalize_reference_mentions, strict_reference_rules
        prompt = normalize_reference_mentions(strip_reference_rules(prompt), person_video is not None)
        mapping = '；'.join(f'@Image{i}（图片{i}）为{kind}参考图' for i, (_, kind) in enumerate(references, 1))
        # Existing saved templates may still contain the original scene instruction.
        # Normalize the built-in phrases only; preserve custom text and give roles explicit priority.
        if scenes or scene_description.strip():
            for old,new in [('动作、镜头、场景和节奏','动作、镜头和节奏'),('动作、镜头和场景不变','动作、镜头不变'),('动作、镜头和场景','动作和镜头')]:
                prompt = prompt.replace(old,new)
        scene_rule = '保留原视频场景。' if not scenes else '场景以场景参考图为准，替换原视频背景，参考空间布局、光线和环境，不引入其中的人物。场景补充：'+scene_description.strip()+'。'
        if not scenes and scene_description.strip():
            scene_rule = '根据场景描述替换原视频背景，保留主体动作和镜头：'+scene_description.strip()+'。'
        accessory_rules = ''.join(ACCESSORY_LABELS[k]+'参考：'+ACCESSORY_RULES[k]+'仅采用该配饰，不引入图中人物或背景。' for k in ACCESSORY_LABELS if accessories.get(k))
        hair_rule = '发型沿用主人物参考图。' if not hairstyles else '发型以发型参考图为准，参考发长、轮廓、刘海、卷曲程度和发色；人物身份、五官和脸型仍以主人物参考图为准，不使用发型图的人脸或身份。'
        structured = f'@Video1（视频1）为动作与镜头参考视频，保留其动作、镜头和节奏。{mapping}。主参考确定人物身份和服装，补充参考用于细节，保持全片一致。用户要求：{prompt.strip()}。素材分工（涉及场景或发型的冲突要求以此为准）：{scene_rule}{hair_rule}{accessory_rules}'
        if person_video is not None:
            structured=structured.replace('主人物参考图','人物参考视频 @Video2')
            structured+='人物身份分工优先：@Video2（视频2）仅提供人物脸部身份与外貌；@Video1 仅提供动作、镜头与节奏，不采用视频2的动作、服装、背景、声音或台词。衣服以衣服参考图为准。'
        structured += '\n' + strict_reference_rules(references, person_video is not None)
        if limits['follow_source']:
            structured = '视频编辑任务：编辑 @Video1，按参考素材替换人物、服装及指定元素。唯一编辑目标为 @Video1，保持原视频时长、画面比例、动作与镜头节奏；其他视频仅作人物身份参考，不进行延长或新增镜头。\n' + structured
        self.progress('正在上传参考素材', 65)
        if self.config.protocol == 'toapis':
            with video.open('rb') as source:
                uploaded = self._request('POST', '/uploads/videos', files={'file': (video.name, source, 'video/mp4')})
            video_url = uploaded['data'].get('url') if isinstance(uploaded.get('data'), dict) else None
            if not isinstance(video_url, str) or urlsplit(video_url).scheme not in {'http', 'https'}:
                raise ProviderError('视频上传响应缺少可用地址，请检查接口格式。')
            body = {'model': self.config.model, 'prompt': structured, 'duration': self.config.duration,
                    'resolution': self.config.resolution, 'aspect_ratio': 'adaptive',
                    'image_with_roles': [{'url': self._data_url(p), 'role': 'reference_image'} for p, _ in references],
                    'video_with_roles': [{'url': video_url, 'role': 'reference_video'}]}
            endpoint = '/videos/generations'
            submitted = self._request('POST', endpoint, json=body)
        elif self.config.protocol == 'ark':
            if not video_url:
                raise ProviderError('缺少打码视频的公网地址，请检查工作台公网地址配置。')
            content = [{'type': 'text', 'text': structured}]
            content.extend({'type': 'image_url', 'image_url': {'url': image_asset_uris[str(p)] if str(p) in image_asset_uris else self._data_url(p)}, 'role': 'reference_image'} for p, _ in references)
            content.append({'type': 'video_url', 'video_url': {'url': video_url}, 'role': 'reference_video'})
            if person_video is not None:
                content.append({'type':'video_url','video_url':{'url':person_video_uri},'role':'reference_video'})
            endpoint = '/contents/generations/tasks'
            submitted = self._request('POST', endpoint, json={'model': self.config.model, 'content': content,
                'duration': -1 if limits['follow_source'] else self.config.duration, 'resolution': self.config.resolution, 'ratio': 'adaptive' if limits['follow_source'] else self.config.ratio,
                **({'generate_audio': self.config.generate_audio} if limits['audio_control'] else {})})
        else:
            endpoint = '/tasks'
            with ExitStack() as stack:
                files = [('video', (video.name, stack.enter_context(video.open('rb')), 'video/mp4'))]
                # Multipart names stay compatible with the documented custom adapter.
                for field, paths in [('face_image', faces), ('clothing_image', clothes), ('hairstyle_image', hairstyles), ('scene_image', scenes)] + [(kind+'_image',accessories.get(kind,[])) for kind in ACCESSORY_LABELS]:
                    files.extend((field, (p.name, stack.enter_context(p.open('rb')), mimetypes.guess_type(p.name)[0] or 'image/png')) for p in paths)
                submitted = self._request('POST', '/generations', files=files, data={
                    'prompt': structured, 'model': self.config.model, 'duration': self.config.duration,
                    'fps': self.config.fps, 'resolution': self.config.resolution})
        task = submitted.get('data') if isinstance(submitted.get('data'), dict) else submitted
        task_id = task.get('id') or task.get('task_id')
        if not isinstance(task_id, str) or not task_id.strip() or len(task_id) > 1024:
            raise self._provider_error('生成提交响应缺少有效任务 ID；提交结果不确定，请先核对服务商任务列表。',
                                       error_kind='submission_uncertain', submission_uncertain=True)
        # Persistence failures must propagate before any further remote operation.
        if on_submitted is not None:
            on_submitted(task_id)
        self.progress(f'模型任务已提交，等待生成（{self._safe(task_id)}）', 70)
        return self._poll(task_id, output, on_result)

    def extend(self, video: Path, prompt: str, output: Path, *, video_url=None,
               on_submitted=None, on_result=None, resume_task_id=None, resume_result_url=None):
        """Explicit continuation; never reuse the editing prompt constructor."""
        if resume_task_id or resume_result_url:
            return self.generate(video, [], [], prompt, output, resume_task_id=resume_task_id,
                                 resume_result_url=resume_result_url, on_result=on_result)
        from .model_catalog import capabilities
        from .person_video import validate_file
        if self.config.protocol != 'ark' or not capabilities(self.config.model,'ark')['follow_source']:
            raise ProviderError('智能续写需要 Seedance 2.5。',error_kind='configuration')
        if self.config.mode != 'http':
            raise ProviderError('演示模式不会调用智能续写，请在后台配置真实视频模型。',error_kind='configuration')
        problem = self.config.generation_problem(video_available=bool(video_url))
        if problem:
            raise ProviderError(problem,error_kind='configuration')
        validate_file(video,person=False,max_seconds=30)
        if type(self.config.duration) is not int or not 4 <= self.config.duration <= 30:
            raise ProviderError('续写输出时长需为 4–30 秒。',error_kind='configuration')
        self._content_roles = {1:'已生成的基础视频'}
        structured = (f'向后延长 @Video1（视频1），完整成片总时长 {self.config.duration} 秒。'
            '原有片段属于成片前段，保持该段内容；仅在其结尾之后发展新的动作和剧情。'
            '衔接视频末尾的姿态、动作方向、镜头和光线，人物身份、服装、发型、配饰和场景保持一致。'
            '新增内容按正常速度自然发展，不循环原动作、不慢放、不定格填充。续写内容：\n'+prompt.strip())
        submitted = self._request('POST','/contents/generations/tasks',json={
            'model':self.config.model,'content':[{'type':'text','text':structured},
                {'type':'video_url','video_url':{'url':video_url},'role':'reference_video'}],
            'omni_reference_task_type':'extend','duration':self.config.duration,
            'ratio':'adaptive','resolution':self.config.resolution,'generate_audio':self.config.generate_audio})
        task = submitted.get('data') if isinstance(submitted.get('data'),dict) else submitted
        task_id = task.get('id') or task.get('task_id')
        if not isinstance(task_id,str) or not task_id.strip() or len(task_id)>1024:
            raise self._provider_error('续写提交结果不确定，请核对服务商任务记录。',error_kind='submission_uncertain',submission_uncertain=True)
        if on_submitted:
            on_submitted(task_id)
        return self._poll(task_id,output,on_result)

    def _with_task(self, error: ProviderError, task_id: str | None) -> ProviderError:
        if task_id is not None and error.provider_task_id is None:
            error.provider_task_id = task_id
            error.args = (f'{error}（服务商任务 ID：{self._safe(task_id)}；重新提交前可在服务商控制台核对。）',)
        return error

    def _poll(self, task_id: str, output: Path, on_result: Callable[[str], None] | None) -> dict:
        endpoint = {'ark': '/contents/generations/tasks', 'toapis': '/videos/generations'}.get(self.config.protocol, '/tasks')
        deadline = time.monotonic() + 1800
        while time.monotonic() < deadline:
            for attempt in range(3):
                try:
                    payload = self._request('GET', f'{endpoint}/{quote(task_id, safe="")}')
                    break
                except ProviderError as exc:
                    if not exc.retryable or attempt == 2:
                        raise self._with_task(exc, task_id) from None
                    self.progress('任务已提交，查询暂时失败，正在重新查询', 75)
                    time.sleep(max(self.poll_seconds, 2))
            current = payload.get('data') if isinstance(payload.get('data'), dict) and 'status' in payload['data'] else payload
            state = str(current.get('status', '')).lower()
            if state in {'succeeded', 'success', 'completed', 'done'}:
                if self.config.protocol == 'ark':
                    url = current['content'].get('video_url') if isinstance(current.get('content'), dict) else None
                elif self.config.protocol == 'toapis':
                    data = current['result'].get('data') if isinstance(current.get('result'), dict) else []
                    data = data if isinstance(data, list) else []
                    url = data[0].get('url') if data and isinstance(data[0], dict) else None
                else:
                    url = current.get('output_url') or current.get('video_url')
                if not isinstance(url, str) or urlsplit(url).scheme not in {'http', 'https'}:
                    raise self._with_task(self._provider_error('模型任务完成，但没有返回可下载的视频地址。',
                        error_kind='query_unavailable', retryable=True), task_id)
                if on_result is not None:
                    on_result(url)
                return self._download_result(url, output, task_id)
            if state in {'failed', 'error', 'cancelled', 'canceled', 'expired'}:
                error = self._with_task(self._provider_error(f'模型任务{state}：',
                    detail=current.get('error') or current.get('message')), task_id)
                error.terminal_failure = True
                raise error
            if state not in {'queued', 'running', 'pending', 'processing', 'in_progress', 'submitted'}:
                raise self._with_task(self._provider_error(f'无法识别模型任务状态：{self._safe(state)}，请检查接口格式。',
                    error_kind='query_unavailable'), task_id)
            self.progress('模型正在排队，请稍候' if state in {'queued', 'pending'} else '模型正在生成视频，请稍候', 75 if state in {'queued', 'pending'} else 85)
            time.sleep(self.poll_seconds)
        raise self._with_task(self._provider_error('等待生成超时，任务可能仍在运行，请恢复查询或在服务商控制台查看。',
            error_kind='query_unavailable', retryable=True), task_id)

    def _download_result(self, url: str, output: Path, task_id: str | None) -> dict:
        self.progress('生成完成，正在下载视频', 95)
        try:
            self._download(url, output)
        except ProviderError as exc:
            exc.error_kind = 'download_failed'
            exc.retryable = True
            raise self._with_task(exc, task_id) from None
        except OSError:
            raise self._with_task(ProviderError('生成已完成，但无法保存视频，请检查本地磁盘空间和目录权限。',
                error_kind='download_failed', retryable=True), task_id) from None
        return {'provider': self.config.provider, 'task_id': task_id, 'message': '真实视频生成完成。'}

    def _download(self, url: str, output: Path) -> None:
        temporary = output.with_suffix('.part')
        try:
            # Result hosts are independent; never forward the provider's API key.
            with requests.get(url, stream=True, timeout=(15, 120)) as response:
                response.raise_for_status()
                content_type = response.headers.get('Content-Type', '').lower()
                if content_type.startswith('text/') or 'json' in content_type:
                    raise ProviderError('视频下载地址返回了网页，请检查服务商的任务结果。')
                total = 0
                with temporary.open('wb') as target:
                    for chunk in response.iter_content(1024 * 1024):
                        total += len(chunk)
                        if total > 512 * 1024 * 1024:
                            raise ProviderError('生成结果超过本地下载大小限制。')
                        target.write(chunk)
                if not total:
                    raise ProviderError('服务商返回的视频文件为空。')
            temporary.replace(output)
        except requests.RequestException:
            raise ProviderError('生成已完成，但下载视频失败，请在服务商控制台查看任务结果。') from None
        finally:
            temporary.unlink(missing_ok=True)
