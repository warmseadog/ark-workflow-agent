"""Admin-only, uncached tests of unsaved masking parameters.

Single-process admission matches the application's worker model. Files are
tenant-local; the next submission retains only the latest three test folders.
"""
from dataclasses import replace
import json
import logging
from pathlib import Path
import re
import shutil
import threading
import time
import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from . import media, redaction_service, redaction_settings
from .media_validation import save_upload
from .media_errors import MediaPipelineError
from .preprocessing_limits import cloud_slots, local_lock

_lock = threading.RLock()
_jobs = {}
_ACTIVE = {'uploading', 'queued', 'running'}
_BASE = '/api/admin/redaction-tests'
_logger = logging.getLogger(__name__)


def _root(settings):
    storage = Path(settings.storage_dir).resolve()
    root = (storage / 'work' / 'redaction-tests').resolve()
    if not root.is_relative_to(storage):
        raise HTTPException(400, '测试目录无效。')
    return root


def _folder(root, ident):
    if not re.fullmatch('[0-9a-f]{32}', ident):
        raise HTTPException(404, '测试结果不存在或已清理，请重新测试。')
    path = (root / ident).resolve()
    if not path.is_relative_to(root):
        raise HTTPException(404, '测试目录无效。')
    return path


def _cleanup(root):
    """Called under admission lock; never remove active processing folders."""
    active = {ident for (owner, ident), job in _jobs.items() if owner == root and job['status'] in _ACTIVE}
    folders = sorted((p for p in root.glob('*') if p.is_dir() and re.fullmatch('[0-9a-f]{32}', p.name)
                      and p.name not in active), key=lambda p: p.stat().st_mtime_ns, reverse=True)
    for path in folders[2:]:
        shutil.rmtree(_folder(root, path.name))
        _jobs.pop((root, path.name), None)
    # Retire small in-memory records from roots not visited again. Disk retention
    # is enforced when each root next submits a test, including after a restart.
    for key, job in list(_jobs.items()):
        if job['status'] not in _ACTIVE and time.time() - job['created'] > 86400:
            _jobs.pop(key, None)


def _public(job):
    result = {key: job[key] for key in ('id', 'status', 'profile', 'values', 'message')}
    if job['status'] == 'succeeded':
        result['output_url'] = f"{_BASE}/{job['id']}/file"
    return result


def _launch(*args):
    threading.Thread(target=_work, args=args, daemon=True, name='admin-redaction-test').start()


def _work(settings, root, ident, source, options):
    key = (root, ident)
    output = _folder(root, ident) / 'result.mp4'
    with _lock:
        _jobs[key].update(status='running', message='正在打码，请稍候…')
    try:
        config = redaction_service.load_config(settings)
        if config.mode == 'local':
            media.run_deface(source, output, settings, options)
        else:
            # A test must show the selected provider's actual output, never a
            # silently substituted local fallback after an external failure.
            with cloud_slots:
                redaction_service.process(source, output, settings, options, config)
        with local_lock:
            redaction_service.validate_output(output, settings, source)
        status, message = 'succeeded', '打码完成，可播放对比；调整参数后可再次测试。'
    except Exception as exc:
        output.unlink(missing_ok=True)
        status = 'failed'
        from .security import configured_secrets, safe_error
        detail = (safe_error(exc, configured_secrets(settings), limit=600) if isinstance(exc, MediaPipelineError)
                  else '处理发生异常，请检查本地服务日志后重试。')
        message = ('本地打码失败：' + detail if settings.redaction_service.mode == 'local'
                   else '外部 API 打码失败：' + detail + ' 本次测试未转为本地打码。')
        _logger.warning('Redaction test %s failed (%s): %s', ident, type(exc).__name__, detail)
    finally:
        # The browser keeps the original as a local object URL. Do not retain
        # uploaded sources or processor intermediates alongside the result.
        for path in output.parent.iterdir():
            if path != output:
                try:
                    if path.is_dir():
                        resolved = path.resolve()
                        if resolved.is_relative_to(output.parent):
                            shutil.rmtree(resolved)
                    else:
                        path.unlink(missing_ok=True)
                except OSError:
                    pass
    with _lock:
        _jobs[key].update(status=status, message=message)


def _configuration(settings, profile, values, connection):
    try:
        values, connection = json.loads(values), json.loads(connection)
        if not isinstance(values, dict) or not isinstance(connection, dict):
            raise ValueError()
        if profile == 'mediakit':
            normalized = redaction_settings.MediaKitOptions.model_validate(values).model_dump()
        elif profile in {'local', 'http'}:
            defaults = redaction_settings._defaults(settings)
            if set(values) - set(defaults) or (profile == 'http' and (values.get('mask_mode', 'face') != 'face' or values.get('local_options') is not None)):
                raise ValueError()
            if profile == 'local':
                defaults['local_options'] = media.LocalMosaicOptions().model_dump()
            normalized = redaction_settings.validate({**defaults, **values})
        else:
            raise ValueError()
    except (ValueError, TypeError):
        raise HTTPException(422, '打码参数无效，请检查所选方式对应的参数范围。') from None
    config = redaction_service.load_config(settings)
    if profile == 'local':
        config = replace(config, mode='local')
    else:
        from .mediakit_redaction import matches
        if (not config.endpoint or connection != {'endpoint': config.endpoint, 'timeout_seconds': config.timeout_seconds}
                or (profile == 'mediakit') != matches(config.endpoint)):
            raise HTTPException(422, '请先保存外部 API 接口配置，再测试当前打码参数。')
        if profile == 'mediakit' and not config.api_key:
            raise HTTPException(422, '请先配置并保存 MediaKit API Key。')
        config = replace(config, mode='http')
    mask = redaction_settings.mask_values(profile, normalized)
    options = media.BlurOptions.model_validate({**{k: v for k, v in mask.items() if k != 'blur_style'}, 'style': mask['blur_style']})
    return replace(settings, redaction_service=config), normalized, options


def get_router(settings_getter, local_guard):
    router = APIRouter(prefix=_BASE, dependencies=[Depends(local_guard)])

    @router.post('')
    def create_test(video: UploadFile = File(...), profile: str = Form(..., max_length=20),
                    values: str = Form(..., max_length=16384), connection: str = Form('{}', max_length=4096)):
        settings, normalized, options = _configuration(settings_getter(), profile, values, connection)
        root, ident = _root(settings), uuid.uuid4().hex
        key = (root, ident)
        with _lock:
            active = [owner for (owner, _), job in _jobs.items() if job['status'] in _ACTIVE]
            if root in active or len(active) >= 2:
                raise HTTPException(429, '已有打码测试正在处理，请等待完成后再试。')
            _cleanup(root)
            _jobs[key] = {'id': ident, 'status': 'uploading', 'profile': profile, 'values': normalized,
                          'message': '等待打码处理…', 'created': time.time()}
        folder = _folder(root, ident)
        source = folder / ('source' + Path(video.filename or '').suffix.lower())
        try:
            save_upload(video, source, min(settings.max_upload_mb, 200) * 1024 * 1024, 'video')
            with _lock:
                _jobs[key]['status'] = 'queued'
            _launch(settings, root, ident, source, options)
        except Exception:
            with _lock:
                _jobs.pop(key, None)
            if folder.exists():
                shutil.rmtree(_folder(root, ident))
            raise
        return _public(_jobs[key])

    def get_job(settings, ident):
        root = _root(settings)
        with _lock:
            job = _jobs.get((root, ident))
            if job is None:
                raise HTTPException(404, '测试结果不存在或已清理，请重新测试。')
            return dict(job)

    @router.get('/{ident}')
    def status(ident: str):
        return _public(get_job(settings_getter(), ident))

    @router.api_route('/{ident}/file', methods=['GET', 'HEAD'])
    def output(ident: str):
        settings = settings_getter()
        job = get_job(settings, ident)
        if job['status'] != 'succeeded':
            raise HTTPException(409, '打码测试尚未成功完成。')
        path = _folder(_root(settings), ident) / 'result.mp4'
        if not path.is_file():
            raise HTTPException(404, '测试结果已清理，请重新测试。')
        from .media_transport import media_file_response
        return media_file_response(settings, path, media_type='video/mp4')

    return router
