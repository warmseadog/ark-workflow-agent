"""Tenant-local, single-process redaction previews over production assets.

Register get_router(request_settings_getter, local_guard) in the authenticated
app. No directories, database connections or executor are created on import.
The process-wide limits include queued AND running work (2/tenant, 20 total).
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import shutil
import threading
import uuid

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import FileResponse

_admission_lock = threading.Lock()
_executor = None
_initialized = set()
_pending = {}
_MASK_FIELDS = frozenset({'blur_style', 'style', 'shape', 'mask_mode',
                          'robust_tracking', 'mask_scale', 'mosaic_size',
                          'threshold', 'detection_size', 'keep_audio'})
_FAILED = '预览处理失败，请重试。'
_RESTARTED = '服务重启中断了预览，请重新提交。'
_PRIVATE = {'Cache-Control': 'private, no-store', 'X-Content-Type-Options': 'nosniff'}


def _store(settings):
    # production_worker -> media -> config creates default storage on import.
    # Keep those imports inside requests/workers, never at module import time.
    from .production_store import ProductionStore
    store = ProductionStore(settings.storage_dir)
    key = store.path.resolve()
    with _admission_lock:
        if key not in _initialized:
            with store.connection() as db:
                db.execute('''CREATE TABLE IF NOT EXISTS production_previews (
                    id TEXT PRIMARY KEY, status TEXT NOT NULL,
                    source_asset_id TEXT NOT NULL, mask TEXT NOT NULL,
                    created_at TEXT NOT NULL, error TEXT)''')
                db.execute("UPDATE production_previews SET status='failed', error=? "
                           "WHERE status IN ('queued','running')", (_RESTARTED,))
                db.execute('''CREATE UNIQUE INDEX IF NOT EXISTS production_previews_inflight
                    ON production_previews(source_asset_id,mask)
                    WHERE status IN ('queued','running')''')
            _initialized.add(key)
    return store


def _inside(root, *parts):
    """Resolve links/junctions before any media read or write."""
    root = Path(root).resolve()
    path = root.joinpath(*parts).resolve()
    if not path.is_relative_to(root) or path == root:
        raise ValueError('Invalid storage path')
    return path


def _source(store, ident):
    try:
        asset = store.get_asset(ident, private=True)
    except LookupError:
        raise HTTPException(404, '视频素材不存在。') from None
    if asset['kind'] != 'video':
        raise HTTPException(422, '请选择视频素材。')
    try:
        assets = _inside(store.storage, 'assets')
        path = Path(asset['path']).resolve()
        if not path.is_relative_to(assets) or not path.is_file():
            raise ValueError('Invalid source')
    except (OSError, ValueError):
        raise HTTPException(404, '视频素材不存在。') from None
    return asset, path


def _normalize(values):
    from .production_worker import mask_options
    if not isinstance(values, dict) or set(values) - _MASK_FIELDS:
        raise ValueError('Invalid mask')
    if 'style' in values and 'blur_style' in values and values['style'] != values['blur_style']:
        raise ValueError('Conflicting styles')
    options = mask_options(values)
    if options.style == 'img':
        raise ValueError('Image replacement is not supported')
    return json.dumps(options.model_dump(mode='json', exclude={'replace_image'}),
                      sort_keys=True, separators=(',', ':'))


def _mask(settings, store, payload):
    from . import redaction_settings
    try:
        defaults = _normalize(redaction_settings.load_config(settings))
        if 'mask' not in payload:
            return defaults
        requested = _normalize(payload['mask'])
        if requested == defaults:
            return requested
        # A browser's savedMask is accepted only as a saved, tenant-owned draft
        # snapshot for this source. POST is not an alternative settings editor.
        for draft in store.list_drafts():
            if draft.get('source_asset_id') == payload['source_asset_id']:
                try:
                    if _normalize(draft.get('mask')) == requested:
                        return requested
                except (ValueError, TypeError):
                    continue
    except (ValueError, TypeError):
        pass
    raise HTTPException(422, '打码设置无效，请使用全局默认或当前素材已保存的草稿设置。')


def _row(store, ident):
    with store.connection() as db:
        row = db.execute('SELECT * FROM production_previews WHERE id=?', (ident,)).fetchone()
    if row is None:
        raise HTTPException(404, '预览不存在。')
    return dict(row)


def _output(store, ident):
    # Only server-generated identifiers may be used to construct file paths.
    if len(ident) != 32 or any(c not in '0123456789abcdef' for c in ident):
        raise ValueError('Invalid preview identifier')
    return _inside(store.storage, 'work', 'previews', ident, 'defaced.mp4')


def _public(store, row):
    status = row['status']
    progress, message = {'queued': (0, '等待视频打码预览'),
                         'running': (15, '正在处理视频打码预览'),
                         'defaced': (100, '打码预览完成'),
                         'failed': (0, '预览处理失败，请重新提交。')}[status]
    result = {'id': row['id'], 'status': status, 'progress': progress,
              'message': message, 'error': row['error']}
    if status == 'defaced':
        try:
            if _output(store, row['id']).is_file():
                result['defaced_url'] = '/api/previews/' + row['id'] + '/file'
        except (OSError, ValueError):
            pass
    return result


def _update(store, ident, status, error=None):
    with store.connection() as db:
        db.execute('UPDATE production_previews SET status=?,error=? WHERE id=?',
                   (status, error, ident))


def _work(settings, store, ident, source_id, mask):
    try:
        from . import production_worker, redaction_service
        from .source_clip import clip_video, fingerprint as clip_fingerprint
        values = json.loads(mask)
        values.pop('service_fingerprint', None)
        clip = values.pop('source_clip', None)
        ratio = values.pop('ratio', 'adaptive')
        options = production_worker.mask_options(values)
        from .preprocessing_limits import cache_writer, local_lock
        # Local previews remain queued while local processing is occupied.
        # Recheck the source after that wait, before any source bytes are read.
        with local_lock if redaction_service.load_config(settings).mode == 'local' else nullcontext():
            asset, source = _source(store, source_id)
        output = _output(store, ident)
        key = hashlib.sha256((asset['sha256'] +
            json.dumps(options.model_dump(mode='json'), sort_keys=True) +
            redaction_service.fingerprint(settings) + clip_fingerprint(clip)).encode()).hexdigest()
        cache = _inside(store.storage, 'cache', 'redacted', key + '.mp4')
        # Only identical tenant/cache inputs serialize; cloud calls share media's limiter.
        with cache_writer(cache):
            _update(store, ident, 'running')
            output.parent.mkdir(parents=True, exist_ok=True)
            temporary = output.with_name('defaced.tmp.mp4')
            if cache.is_file():
                shutil.copyfile(cache, temporary)
            else:
                with local_lock:
                    _, source = _source(store, source_id)
                    source = clip_video(source,output.parent/'source-clip.mp4',clip)
                production_worker.run_deface(source, temporary, settings, options)
            if not temporary.is_file() or temporary.stat().st_size == 0:
                raise ValueError('No preview output')
            temporary.replace(output)
            if not cache.is_file():
                cache.parent.mkdir(parents=True, exist_ok=True)
                cache_tmp = _inside(store.storage, 'cache', 'redacted', key + '.tmp.mp4')
                shutil.copyfile(output, cache_tmp)
                cache_tmp.replace(cache)
            from .video_framing import reframe_video
            with local_lock:
                framed = reframe_video(output, output.with_name('framed.mp4'), ratio)
            if framed != output:
                framed.replace(output)
        # Finish and release admission atomically: polling a terminal state must
        # not race with a stale pending count on the next POST.
        with _admission_lock:
            _update(store, ident, 'defaced')
            _release(store)
    except Exception:
        with _admission_lock:
            try:
                _update(store, ident, 'failed', _FAILED)
            finally:
                _release(store)


def _release(store):
    key = store.path.resolve()
    remaining = _pending.get(key, 0) - 1
    if remaining > 0:
        _pending[key] = remaining
    else:
        _pending.pop(key, None)


def _submit(settings, store, source_id, mask):
    global _executor
    from .production_store import now
    key = store.path.resolve()
    with _admission_lock:
        with store.connection() as db:
            existing = db.execute("SELECT * FROM production_previews WHERE source_asset_id=? "
                                  "AND mask=? AND status IN ('queued','running')",
                                  (source_id, mask)).fetchone()
            if existing:
                return _public(store, dict(existing))
            if _pending.get(key, 0) >= 2 or sum(_pending.values()) >= 20:
                raise HTTPException(429, '预览队列已满，请稍后重试。', headers={'Retry-After': '2'})
            ident = uuid.uuid4().hex
            db.execute('INSERT INTO production_previews VALUES (?,?,?,?,?,?)',
                       (ident, 'queued', source_id, mask, now(), None))
        _pending[key] = _pending.get(key, 0) + 1
        try:
            if _executor is None:
                _executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='redaction-preview')
            _executor.submit(_work, settings, store, ident, source_id, mask)
        except Exception:
            try:
                _update(store, ident, 'failed', _FAILED)
            finally:
                _release(store)
        return _public(store, _row(store, ident))


def get_router(settings_getter, local_guard):
    """All three endpoints resolve settings in the authenticated request context."""
    router = APIRouter(prefix='/api/previews', dependencies=[Depends(local_guard)])

    def context():
        current = settings_getter()
        frozen = replace(current, storage_dir=Path(current.storage_dir).resolve())
        return frozen, _store(frozen)

    @router.post('')
    def create_preview(payload: dict, response: Response):
        response.headers.update(_PRIVATE)
        if (set(payload) - {'source_asset_id', 'mask', 'source_clip', 'ratio'} or
                not isinstance(payload.get('source_asset_id'), str) or
                not 1 <= len(payload['source_asset_id']) <= 100):
            raise HTTPException(422, '请提供有效的视频素材标识和打码设置。')
        settings, store = context()
        _source(store, payload['source_asset_id'])
        mask = _mask(settings, store, payload)
        from .video_framing import validate_ratio
        try:
            ratio = validate_ratio(payload.get('ratio', 'adaptive'))
        except ValueError as error:
            raise HTTPException(422, str(error)) from None
        if payload.get('source_clip') is not None:
            from .source_clip import normalize_clip, validate_source
            try:
                clip = normalize_clip(payload['source_clip'])
                _, source = _source(store, payload['source_asset_id'])
                validate_source(source, clip)
            except ValueError as error:
                raise HTTPException(422, str(error)) from None
            mask = json.dumps({**json.loads(mask), 'source_clip': clip}, sort_keys=True)
        if ratio != 'adaptive':
            mask = json.dumps({**json.loads(mask), 'ratio': ratio}, sort_keys=True)
        from . import redaction_service
        settings = redaction_service.freeze(settings)
        mask = json.dumps({**json.loads(mask), 'service_fingerprint': redaction_service.fingerprint(settings)}, sort_keys=True)
        from .prompt_visibility import project
        return project(_submit(settings, store, payload['source_asset_id'], mask), settings)

    @router.get('/{ident}')
    def get_preview(ident: str, response: Response):
        response.headers.update(_PRIVATE)
        _, store = context()
        from .prompt_visibility import project
        return project(_public(store, _row(store, ident)), settings_getter())

    @router.api_route('/{ident}/file', methods=['GET','HEAD'])
    def preview_file(ident: str):
        _, store = context()
        row = _row(store, ident)
        if row['status'] != 'defaced':
            raise HTTPException(409, '预览尚未完成。')
        try:
            output = _output(store, ident)
            if not output.is_file():
                raise ValueError('Missing preview')
        except (ValueError, OSError):
            raise HTTPException(404, '预览文件不存在，请重新生成。') from None
        from .media_transport import media_file_response
        return media_file_response(settings_getter(), output, media_type='video/mp4')

    return router
