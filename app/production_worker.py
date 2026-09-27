"""Bounded background production queue with durable provider recovery."""
from __future__ import annotations
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import shutil
import threading

from .production_store import ProductionStore
from .generation_settings import GenerationConfig
from .storage_settings import StorageConfig, upload_redacted_video
from .reference_media import publish_video
from .media import BlurOptions, run_deface
from .video_provider import VideoProvider, ProviderError

_preprocess_lock = threading.Lock()
_managers = {}
_managers_lock = threading.Lock()


def mask_options(values):
    values = dict(values)
    if 'blur_style' in values:
        values['style'] = values.pop('blur_style')
    for key in ('detection_size',):
        if values.get(key) == '': values[key] = None
    return BlurOptions(**values)


def execute_run(settings, store, run):
    ident = run['id']
    snapshot, private = run['snapshot'], run['private']
    work = settings.storage_dir / 'work' / ident
    work.mkdir(parents=True, exist_ok=True)
    defaced = work / 'defaced.mp4'
    output = settings.storage_dir / 'outputs' / (ident + '.mp4')
    output.parent.mkdir(parents=True, exist_ok=True)
    config = GenerationConfig(**private['generation'])
    storage = StorageConfig(**private['storage'])
    provider_id, result_url = run.get('provider_task_id'), run.get('result_url')
    try:
        faces, clothes, video_url = [], [], None
        image_asset_uris = {}
        extra_references = {}
        if not provider_id and not result_url:
            if private.get('portrait'):
                from .portrait_generation import verify, PortraitPending
                store.update_run(ident,stage='authorizing',message='正在核实人物照片',progress=2)
                try: image_asset_uris=verify(private['portrait'],store)
                except PortraitPending as pending:
                    store.update_run(ident,status='queued',stage='authorizing',message=str(pending),progress=2)
                    return
            source = store.get_asset(snapshot['source_asset_id'], private=True)
            faces = [Path(store.get_asset(x, private=True)['path']) for x in snapshot['face_asset_ids']]
            clothes = [Path(store.get_asset(x, private=True)['path']) for x in snapshot['clothing_asset_ids']]
            for kind, argument in [('hairstyle','hairstyles'),('scene','scenes')]:
                if snapshot.get(kind+'_enabled',False):
                    extra_references[argument] = [Path(store.get_asset(x,private=True)['path']) for x in snapshot.get(kind+'_asset_ids',[])]
            if extra_references.get('scenes'):
                extra_references['scene_description'] = snapshot.get('scene_description','')
            options = mask_options(snapshot['mask'])
            key = hashlib.sha256((source['sha256'] + json.dumps(options.model_dump(mode='json'), sort_keys=True) + settings.deface_bin + ':v1').encode()).hexdigest()
            cache = settings.storage_dir / 'cache' / 'redacted' / (key + '.mp4')
            store.update_run(ident, stage='preprocess', message='等待本地视频预处理', progress=5)
            with _preprocess_lock:
                if not defaced.is_file():
                    if cache.is_file():
                        shutil.copyfile(cache, defaced)
                    else:
                        store.update_run(ident, message='正在处理视频打码', progress=15)
                        temporary = work / 'defaced.tmp.mp4'
                        run_deface(Path(source['path']), temporary, settings, options)
                        temporary.replace(defaced)
                        cache.parent.mkdir(parents=True, exist_ok=True)
                        cache_tmp = cache.with_suffix('.tmp.mp4')
                        shutil.copyfile(defaced, cache_tmp)
                        cache_tmp.replace(cache)
            if config.mode == 'http' and config.protocol == 'ark':
                store.update_run(ident, stage='upload', message='正在上传打码视频', progress=55)
                if storage.enabled:
                    video_url = upload_redacted_video(defaced, settings, storage)
                else:
                    video_url = publish_video(defaced, settings.storage_dir, config.public_base_url)
            store.update_run(ident, stage='submitting', message='正在提交模型任务', progress=65)
        def progress(message, percent):
            store.update_run(ident, message=message, progress=percent)
        def submitted(task_id):
            store.update_run(ident, provider_task_id=task_id, stage='generating', message='模型任务已接收', progress=70)
        def result(url):
            store.update_run(ident, result_url=url, stage='downloading', message='正在下载生成结果', progress=95)
        client = VideoProvider(config, settings.seedance_poll_seconds, progress)
        client.generate(defaced, faces, clothes, snapshot['prompt'], output, video_url=video_url,
                        on_submitted=submitted, on_result=result,
                        resume_task_id=provider_id, resume_result_url=result_url, **extra_references,
                        **({'image_asset_uris':image_asset_uris} if image_asset_uris else {}))
        store.update_run(ident, status='succeeded', stage='complete', message='生成完成', progress=100,
                         error=None, error_kind=None)
    except Exception as exc:
        current = store.get_run(ident, private=True)
        kind = getattr(exc, 'error_kind', None) or 'processing_failed'
        uncertain = bool(getattr(exc, 'submission_uncertain', False))
        if not isinstance(exc, ProviderError) and current['stage'] == 'submitting':
            uncertain = True
        if not isinstance(exc, ProviderError) and current.get('provider_task_id'):
            kind = 'download_failed' if current['stage']=='downloading' else 'query_unavailable'
        if uncertain:
            kind = 'submission_uncertain'
        needs_attention = uncertain or kind in {'query_unavailable', 'download_failed'}
        message = VideoProvider(config)._safe(str(exc))
        if kind == 'material_rejected':
            message = '人物参考图未通过模型检查，请修改素材后创建新任务。'
        store.update_run(ident, status='needs_attention' if needs_attention else 'failed',
                         error=VideoProvider(config)._safe(str(exc)), error_kind=kind,
                         request_id=getattr(exc,'request_id',None), message=message)


class QueueManager:
    def __init__(self, settings):
        self.settings = settings
        self.store = ProductionStore(settings.storage_dir)
        self.stop = threading.Event()
        self.threads = []
        self.lockfile = None

    def start(self):
        # A process-scoped lock prevents reloads/multiple API workers from recovering
        # or dispatching the same in-flight provider submission twice.
        path = self.settings.storage_dir / '.production-worker.lock'
        handle = path.open('a+b')
        try:
            # Windows denies reads of the byte while another process owns it.
            handle.seek(0, 2)
            if handle.tell() == 0:
                handle.write(b'0'); handle.flush()
            handle.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            return False
        self.lockfile = handle
        self.store.recover()
        from .portrait_library import PortraitLibrary
        PortraitLibrary(self.settings).recover()
        thread = threading.Thread(target=self.portrait_loop, daemon=True, name="portrait-worker")
        thread.start(); self.threads.append(thread)
        for _ in range(2):
            thread = threading.Thread(target=self.loop, daemon=True, name='production-worker')
            thread.start(); self.threads.append(thread)
        return True

    def portrait_loop(self):
        from .portrait_library import PortraitLibrary
        while not self.stop.is_set():
            try: PortraitLibrary(self.settings).process_one()
            except Exception: pass
            self.stop.wait(1)

    def loop(self):
        while not self.stop.is_set():
            try:
                run = self.store.claim_next()
                if run:
                    execute_run(self.settings, self.store, run)
                    continue
            except Exception:
                # A transient local database error must not kill the dispatcher.
                pass
            self.stop.wait(1)


def wake(settings):
    key = str(settings.storage_dir.resolve())
    with _managers_lock:
        old = _managers.get(key)
        if old and old.stop.is_set() and all(not thread.is_alive() for thread in old.threads):
            old.lockfile.close()
            _managers.pop(key, None)
        if key not in _managers:
            manager = QueueManager(settings)
            if manager.start():
                _managers[key] = manager


def shutdown(settings):
    key = str(settings.storage_dir.resolve())
    with _managers_lock:
        manager = _managers.get(key)
        if not manager:
            return
        manager.stop.set()
    for thread in manager.threads:
        thread.join(timeout=1)
    with _managers_lock:
        if all(not thread.is_alive() for thread in manager.threads):
            if manager.lockfile:
                manager.lockfile.close()
            _managers.pop(key, None)
