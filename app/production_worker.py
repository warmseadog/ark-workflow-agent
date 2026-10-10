"""Bounded background production queue with durable provider recovery."""
from __future__ import annotations
from dataclasses import asdict
from contextlib import nullcontext
import json
import os
from pathlib import Path
import shutil
import threading
import time
import uuid

from .production_store import ProductionStore
from .generation_settings import GenerationConfig
from .storage_settings import StorageConfig, upload_redacted_video
from .reference_media import publish_video
from .media import BlurOptions, run_deface
from .hairstyle_mask import process_hairstyle
from .video_provider import VideoProvider, ProviderError
from .source_clip import clip_video
from .video_framing import reframe_video
from .reference_roles import ACCESSORY_LABELS, snapshot_content_roles
from . import scheduling_settings
from .preprocessing_limits import local_lock
from .prompt_config import frozen_run

_preprocess_lock = local_lock
_managers = {}
_managers_lock = threading.Lock()


def mask_options(values):
    values = dict(values)
    if 'blur_style' in values:
        values['style'] = values.pop('blur_style')
    for key in ('detection_size',):
        if values.get(key) == '': values[key] = None
    return BlurOptions(**values)


def authorize_run_inputs(settings, store, snapshot):
    """Check only inputs sent by this snapshot, and identify the rejected material."""
    from .person_video import person_ids
    from .shared_portraits import authorize_asset
    inputs = [('动作参考视频', [snapshot['source_asset_id']]),
              ('人物参考素材', person_ids(snapshot)),
              ('服装参考图', snapshot.get('clothing_asset_ids', []))]
    for kind, label in {'hairstyle':'发型', 'scene':'场景', **ACCESSORY_LABELS}.items():
        if snapshot.get(kind+'_enabled', False):
            inputs.append((label+'参考图', snapshot.get(kind+'_asset_ids', [])))
    checked = set()
    for label, identifiers in inputs:
        for asset_id in identifiers:
            if asset_id in checked: continue
            checked.add(asset_id)
            name = '未找到的素材'
            try:
                name = store.get_asset(asset_id)['name']
                authorize_asset(settings, asset_id)
            except (LookupError, ValueError, PermissionError) as error:
                raise ValueError(f'{label}「{name}」不可用：{error} 请更换或移除该素材；可选参考也可以关闭后重新提交。') from None


def run_reference_roles(store, snapshot):
    """Add original video names without making remote recovery depend on files."""
    roles = snapshot_content_roles(snapshot)
    for index, role in roles.items():
        if role not in {'参考视频', '人物参考视频'}:
            continue
        label = '动作参考视频' if role == '参考视频' else role
        asset_id = snapshot.get('source_asset_id' if role == '参考视频' else 'person_video_asset_id')
        try:
            name = store.get_asset(asset_id).get('name', '') if asset_id else ''
        except (LookupError, OSError, ValueError):
            name = ''
        # Names are display metadata; strip any client path before sanitizing.
        name = str(name).replace('\\', '/').rsplit('/', 1)[-1]
        from .security import safe_error
        name = safe_error(name, limit=120)
        roles[index] = f'{label}「{name}」' if name else label
    return roles


@frozen_run
def execute_run(settings, store, run):
    ident = run['id']
    if store.get_run(ident)['status'] in {'cancelled', 'succeeded'}:
        return
    snapshot, private = run['snapshot'], run['private']
    work = settings.storage_dir / 'work' / ident
    work.mkdir(parents=True, exist_ok=True)
    defaced = work / 'defaced.mp4'
    output = settings.storage_dir / 'outputs' / (ident + '.mp4')
    output.parent.mkdir(parents=True, exist_ok=True)
    config = GenerationConfig(**private['generation'])
    storage = StorageConfig(**private['storage'])
    provider_id, result_url = run.get('provider_task_id'), run.get('result_url')
    continuation_intent = private.get('continuation')
    continuation_state = store.get_continuation(ident) if continuation_intent else {}
    base_ready = continuation_state.get('base_ready',False)
    continuation_started = bool(base_ready)
    base_output = work/'base.mp4' if continuation_intent else output
    try:
        from . import redaction_service
        settings = redaction_service.freeze(settings)
        faces, clothes, video_url = [], [], None
        image_asset_uris = {}
        extra_references = {'reference_roles':run_reference_roles(store, snapshot)}
        if not provider_id and not result_url and not base_ready:
            authorize_run_inputs(settings, store, snapshot)
            from .model_catalog import capabilities
            if config.mode=='http' and capabilities(config.model,config.protocol)['person_video']:
                from .source_clip import validate_source
                validate_source(store.get_asset(snapshot['source_asset_id'],private=True)['path'],
                    snapshot.get('source_clip'),max_seconds=capabilities(config.model,config.protocol)['max_video_seconds'])
            if private.get('portrait') or private.get('person_preparation'):
                from .portrait_generation import verify, PortraitPending
                store.update_run(ident,stage='authorizing',message='正在核实人物素材',progress=2)
                try:
                    if private.get('person_preparation'):
                        from .person_preparation import prepare_run
                        image_asset_uris = prepare_run(settings, store, run)
                    else:
                        image_asset_uris=verify(private['portrait'],store,settings=settings)
                except PortraitPending as pending:
                    store.update_run(ident,status='queued',stage='authorizing',message=str(pending),progress=2)
                    return
            if store.get_run(ident)['status'] == 'cancelled':
                return
            source = store.get_asset(snapshot['source_asset_id'], private=True)
            from .person_video import is_video, validate_pair
            from .model_catalog import capabilities
            source_path = Path(source['path'])
            if snapshot.get('source_clip'):
                if not capabilities(config.model,config.protocol)['follow_source']:
                    raise ValueError('当前模型不支持指定视频片段。')
                clip_message = '正在放慢动作视频以匹配时长' if snapshot['source_clip'].get('retime') == 'slow' else '正在截取动作视频片段'
                store.update_run(ident,stage='preprocess',message=clip_message,progress=3)
                store.set_phase(ident, 'waiting')
                with _preprocess_lock:
                    store.set_phase(ident, 'other')
                    source_path = clip_video(source_path,work/'source-clip.mp4',snapshot['source_clip'],max_seconds=capabilities(config.model,config.protocol)['max_video_seconds'])
            faces = [] if is_video(snapshot) else [Path(store.get_asset(x, private=True)['path']) for x in snapshot['face_asset_ids']]
            if is_video(snapshot):
                person_path=Path(store.get_asset(snapshot['person_video_asset_id'],private=True)['path'])
                validate_pair(source_path,person_path,max_seconds=capabilities(config.model,config.protocol)['max_video_seconds'])
                person_uri=image_asset_uris.pop(str(person_path),None)
                from .config import local_demo_enabled
                if not (local_demo_enabled() and config.mode == 'mock'):
                    if not person_uri:raise ValueError('人物视频尚未通过官方检查，请重新选择。')
                    extra_references.update(person_video=person_path,person_video_uri=person_uri)
                # Offline demo copies the redacted source; it neither sends the
                # identity video nor invents an official asset URI for it.
            clothes = [Path(store.get_asset(x, private=True)['path']) for x in snapshot['clothing_asset_ids']]
            for kind, argument in [('hairstyle','hairstyles'),('scene','scenes')]:
                if snapshot.get(kind+'_enabled',False):
                    assets = [store.get_asset(x,private=True) for x in snapshot.get(kind+'_asset_ids',[])]
                    if kind=='hairstyle':
                        store.update_run(ident,stage='preprocess',message='正在处理发型参考图人脸打码',progress=4)
                        from .run_phases import observe
                        with observe(lambda phase, **kw: store.set_phase(ident, phase, **kw)):
                            extra_references[argument] = [process_hairstyle(settings,asset,snapshot.get('hairstyle_mask',{}))['path'] for asset in assets]
                        store.set_phase(ident, 'other')
                        from .artifacts import sha256_file
                        records = [{'source_asset_id':asset['id'], 'source_sha256':asset['sha256'],
                                    'output_sha256':sha256_file(path),
                                    'settings':snapshot.get('hairstyle_mask',{})}
                                   for asset,path in zip(assets,extra_references[argument])]
                        (work/'hairstyle-references.json').write_text(json.dumps(records),encoding='utf-8')
                    else: extra_references[argument] = [Path(asset['path']) for asset in assets]
            accessories = {kind: [Path(store.get_asset(x,private=True)['path']) for x in snapshot.get(kind+'_asset_ids',[])]
                           for kind in ACCESSORY_LABELS if snapshot.get(kind+'_enabled',False)}
            if accessories: extra_references['accessories'] = accessories
            if snapshot.get('scene_enabled',False):
                extra_references['scene_description'] = snapshot.get('scene_description','')
            options = mask_options(snapshot['mask'])
            store.update_run(ident, stage='preprocess', message='等待视频预处理', progress=5)
            store.set_phase(ident, 'waiting')
            if store.get_run(ident)['status'] == 'cancelled':
                return
            # Every attempt before provider submission starts from the original source.
            # Submitted provider tasks are polled above without entering this branch.
            store.update_run(ident, message='正在处理视频打码', progress=15)
            temporary = work / 'defaced.tmp.mp4'
            temporary.unlink(missing_ok=True)
            store.set_phase(ident, 'masking')
            from .run_phases import observe
            try:
                with observe(lambda phase, **kw: store.set_phase(ident, phase, **kw)):
                    run_deface(source_path, temporary, settings, options)
                temporary.replace(defaced)
            finally:
                temporary.unlink(missing_ok=True)
            store.set_phase(ident, 'other')
            if capabilities(config.model, config.protocol)['follow_source'] and config.ratio != 'adaptive':
                store.update_run(ident, message='正在调整参考视频画面比例', progress=45)
                store.set_phase(ident, 'waiting')
                with _preprocess_lock:
                    store.set_phase(ident, 'other')
                    framed = reframe_video(defaced, work/'framed.mp4', config.ratio)
                if framed != defaced:
                    framed.replace(defaced)
            if store.get_run(ident)['status'] == 'cancelled':
                return
            if config.mode=='http' and capabilities(config.model,config.protocol)['person_video']:
                from .reference_adaptation import adapt_video, record_adaptation
                store.update_run(ident,message='正在检查参考视频尺寸',progress=46)
                adapted,notice=adapt_video(defaced,work/'model-input'/uuid.uuid4().hex,
                    max_seconds=capabilities(config.model,config.protocol)['max_video_seconds'])
                if notice:
                    # Public video sharing admits only task-scoped masked files.
                    defaced=work/'model-input'/'defaced.mp4'
                    defaced.parent.mkdir(parents=True,exist_ok=True)
                    shutil.copyfile(adapted,defaced)
                    record_adaptation(work,{**notice,'label':'动作参考视频'})
                extra_references['on_input_adapted']=lambda notice:record_adaptation(work,notice)
            if private.get('variation'):
                from .variation import prepare as prepare_variation
                extra_references['variation_plan']=prepare_variation(settings,store,run,defaced,faces,clothes,extra_references,config)
                extra_references['on_prompt']=lambda text:store.update_variation(ident,final_prompt=text)
            if config.mode == 'http' and config.protocol == 'ark':
                if store.get_run(ident)['status'] == 'cancelled':
                    return
                store.update_run(ident, stage='upload', message='正在上传打码视频', progress=55)
                if storage.enabled:
                    store.set_phase(ident, 'upload')
                    video_url = upload_redacted_video(defaced, settings, storage)
                else:
                    video_url = publish_video(defaced, settings.storage_dir, config.public_base_url)
                store.set_phase(ident, 'other')
            # Check after preprocessing/upload and immediately before a NEW
            # provider submission. Existing task polling keeps its frozen data.
            authorize_run_inputs(settings, store, snapshot)
            if not store.begin_submission(ident):
                return
        def progress(message, percent):
            store.update_run(ident, message=message, progress=percent)
        def submitted(task_id):
            store.update_run(ident, provider_task_id=task_id, stage='generating', message='模型任务已接收', progress=70)
            store.set_phase(ident, 'model')
        def result(url):
            store.set_phase(ident, 'other')
            store.update_run(ident, result_url=url, stage='downloading', message='正在下载生成结果', progress=95)
        if not base_ready:
            client = VideoProvider(config, settings.seedance_poll_seconds, progress)
            client.phase_callback = lambda phase: store.set_phase(ident, phase)
            if provider_id and not result_url:
                store.set_phase(ident, 'model')
            client.generate(defaced, faces, clothes, snapshot['prompt'], base_output, video_url=video_url,
                            prompt_rule_version=snapshot.get('prompt_rule_version','legacy-v1'),
                            on_submitted=submitted, on_result=result,
                            resume_task_id=provider_id, resume_result_url=result_url, **extra_references,
                            **({'image_asset_uris':image_asset_uris} if image_asset_uris else {}))
            store.set_phase(ident, 'other')
        if continuation_intent:
            continuation_started = True
            store.update_continuation(ident,base_ready=True)
            from .continuation import execute
            execute(settings,store,run,base_output,output,config,storage)
        store.update_run(ident, status='succeeded', stage='complete', message='生成完成', progress=100,
                         error=None, error_kind=None)
    except Exception as exc:
        current = store.get_run(ident, private=True)
        kind = getattr(exc, 'error_kind', None) or 'processing_failed'
        uncertain = bool(getattr(exc, 'submission_uncertain', False))
        if continuation_started and current['stage']=='continuation_submitting' and not isinstance(exc,ProviderError):
            uncertain = not store.get_continuation(ident).get('provider_task_id')
        if not isinstance(exc, ProviderError) and current['stage'] == 'submitting':
            uncertain = True
        if not continuation_started and not isinstance(exc, ProviderError) and current.get('provider_task_id'):
            kind = 'download_failed' if current['stage']=='downloading' else 'query_unavailable'
        if uncertain:
            kind = 'submission_uncertain'
        variation_retry = bool(private.get('variation') and current['stage']=='variation_planning' and not store.get_variation(ident).get('plan',{}).get('blocked'))
        needs_attention = variation_retry or continuation_started or uncertain or kind in {'query_unavailable', 'download_failed'} or bool(getattr(exc, 'retryable', False) and private.get('person_preparation') and current['stage'] == 'authorizing')
        from .security import safe_error, configured_secrets, secret_values
        message = safe_error(str(exc), configured_secrets(settings) | secret_values(private))
        store.update_run(ident, status='needs_attention' if needs_attention else 'failed',
                         error=message, error_kind=kind,
                         request_id=getattr(exc,'request_id',None), message=message)


class QueueManager:
    def __init__(self, settings):
        from .tenancy import root_settings
        self.settings = root_settings(settings)
        self.worker_count = scheduling_settings.worker_capacity()
        scheduling_settings.load_config(self.settings, capacity=self.worker_count)
        # Keep the legacy root store available to existing callers.
        self.store = ProductionStore(self.settings.storage_dir)
        self.stop = threading.Event()
        self.threads = []
        self.lockfile = None
        self._dispatch_lock = threading.Lock()
        self._stores = {self._tenant_key(self.settings): self.store}
        self._initialized = set()
        self._active = {}
        self._last_tenant = {}
        self._last_expiry = {}

    @staticmethod
    def _tenant_key(settings):
        return str(settings.storage_dir.resolve())

    def _ready_tenants(self):
        """Refresh accounts and recover each store once, under _dispatch_lock.

        Recovery includes disabled accounts, but only enabled, fully recovered
        stores are eligible for work. A failed recovery is retried before use;
        no worker can start on a partially initialized tenant.
        """
        from .tenancy import tenant_settings
        from .portrait_library import PortraitLibrary
        ready = []
        for user, settings in tenant_settings(self.settings, include_disabled=True):
            try:
                key = self._tenant_key(settings)
                if key not in self._initialized:
                    store = self._stores.get(key)
                    if store is None:
                        store = ProductionStore(settings.storage_dir)
                        self._stores[key] = store
                    store.recover()
                    PortraitLibrary(settings).recover()
                    with store.connection() as db:
                        db.execute("UPDATE production_playbacks SET status='queued' WHERE status='processing'")
                    self._initialized.add(key)
                if time.monotonic()-self._last_expiry.get(key,0) >= 3:
                    self._stores[key].expire_queued()
                    self._last_expiry[key] = time.monotonic()
                if user.get('enabled', True) and not user.get('deleted_at'):
                    ready.append((key, user, settings, self._stores[key]))
            except Exception:
                # A broken tenant must not prevent the others from progressing.
                continue
        return ready

    def _rotated(self, tenants, kind):
        keys = [entry[0] for entry in tenants]
        last = self._last_tenant.get(kind)
        start = keys.index(last) + 1 if last in keys else 0
        return tenants[start:] + tenants[:start]

    def next_job(self):
        """Claim (tenant_settings, store, run), or None; pair with release().

        Selection, durable claim and slot reservation are serialized across all
        video workers. The cursor advances only on a successful video claim.
        """
        with self._dispatch_lock:
            limit = scheduling_settings.load_config(self.settings, capacity=self.worker_count)['global_concurrency']
            if sum(self._active.values()) >= limit:
                return None
            for key, user, settings, store in self._rotated(self._ready_tenants(), 'video'):
                try:
                    from .tenancy import enabled
                    from .accounts import Accounts
                    guard = Accounts(self.settings.storage_dir).active_user(user['id']) if enabled() else nullcontext(user)
                    with guard as current_user:
                        limit = max(0, int(current_user.get('max_concurrent', 1)))
                        if self._active.get(key, 0) >= limit:
                            continue
                        run = store.claim_next()
                except Exception:
                    continue
                if run:
                    self._active[key] = self._active.get(key, 0) + 1
                    self._last_tenant['video'] = key
                    return settings, store, run
        return None

    def release(self, settings):
        """Release one video reservation, including after errors or requeueing."""
        key = self._tenant_key(settings)
        with self._dispatch_lock:
            remaining = self._active.get(key, 0) - 1
            if remaining > 0:
                self._active[key] = remaining
            else:
                self._active.pop(key, None)

    def _next_auxiliary_settings(self, kind):
        with self._dispatch_lock:
            tenants = self._rotated(self._ready_tenants(), kind)
            if tenants:
                key, _, settings, _ = tenants[0]
                # Advance even when processing finds no work or raises.
                self._last_tenant[kind] = key
                return settings
        return None

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
        try:
            with self._dispatch_lock:
                self._ready_tenants()
        except Exception:
            handle.close()
            self.lockfile = None
            raise
        playback_thread=threading.Thread(target=self.playback_loop,daemon=True,name='playback-worker')
        playback_thread.start();self.threads.append(playback_thread)
        thread = threading.Thread(target=self.portrait_loop, daemon=True, name="portrait-worker")
        thread.start(); self.threads.append(thread)
        for _ in range(self.worker_count):
            thread = threading.Thread(target=self.loop, daemon=True, name='production-worker')
            thread.start(); self.threads.append(thread)
        return True

    def playback_loop(self):
        from .playback import process_one
        while not self.stop.is_set():
            try:
                settings = self._next_auxiliary_settings('playback')
                if settings is not None:
                    process_one(settings)
            except Exception:pass
            self.stop.wait(3)

    def portrait_loop(self):
        from .portrait_library import PortraitLibrary
        while not self.stop.is_set():
            try:
                settings = self._next_auxiliary_settings('portrait')
                if settings is not None:
                    PortraitLibrary(settings).process_one()
            except Exception: pass
            self.stop.wait(1)

    def loop(self):
        while not self.stop.is_set():
            try:
                job = self.next_job()
                if job:
                    settings, store, run = job
                    try:
                        execute_run(settings, store, run)
                    except Exception:
                        # Even failures before execute_run's provider handler must
                        # close the observed running interval. Never resubmit an
                        # ambiguous or already accepted provider task automatically.
                        current = store.get_run(run['id'], private=True)
                        if current['status'] == 'running':
                            uncertain = current['stage'] == 'submitting' and not current.get('provider_task_id')
                            remote = bool(current.get('provider_task_id') or current.get('result_url'))
                            kind = ('submission_uncertain' if uncertain else
                                    'query_unavailable' if remote else 'processing_failed')
                            store.update_run(run['id'], status='needs_attention' if uncertain or remote else 'failed',
                                             error_kind=kind, message='任务处理已中断，请查看任务详情后重试或继续查询。')
                    finally:
                        self.release(settings)
                    continue
            except Exception:
                # A transient local database error must not kill the dispatcher.
                pass
            self.stop.wait(1)


def wake(settings):
    from .tenancy import root_settings
    settings = root_settings(settings)
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
    from .tenancy import root_settings
    settings = root_settings(settings)
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
