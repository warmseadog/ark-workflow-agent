"""Prepare automatic virtual references after a production run is durably frozen."""
from dataclasses import asdict
import hashlib
import json
import time

from . import portrait_service, storage_settings
from .person_video import person_ids
from .portrait_generation import OFFICIAL_BASE, PortraitPending, PortraitPhotoError, verify
from .portrait_library import PortraitLibrary, validate_photo
from .production_store import Conflict

POLICIES = {'auto_virtual', 'existing_person', 'legacy_raw'}
WAIT_SECONDS = 1800


class PreparationError(ValueError):
    def __init__(self, message, *, kind='person_preparation_failed', retryable=False):
        super().__init__(message)
        self.error_kind = kind
        self.retryable = retryable


def input_policy(draft, store):
    policy = draft.get('person_input_policy')
    if policy is not None:
        if policy not in POLICIES:
            raise ValueError('人物来源模式不正确。')
        return policy
    if draft.get('person_id') or any(store.portrait_binding(ident) for ident in person_ids(draft)):
        return 'existing_person'
    return 'legacy_raw'


def input_digest(store, draft):
    content = [(asset['id'], asset['kind'], asset['sha256'])
        for asset in (store.get_asset(ident) for ident in person_ids(draft))]
    return hashlib.sha256(json.dumps(content, separators=(',', ':')).encode()).hexdigest()


def preflight(settings, store, draft, generation):
    """Local validation only: this must not create groups or enqueue photos."""
    if draft.get('person_id'):
        raise ValueError('已选择人物，请使用人物库模式；自动上传虚拟人不绑定其他人物。')
    if generation.protocol != 'ark' or generation.base_url.rstrip('/') != OFFICIAL_BASE:
        raise ValueError('自动入库虚拟人物需要火山官方模型接口，请检查模型设置。')
    lib = PortraitLibrary(settings)
    if not lib.config.ready:
        raise ValueError(lib.config.problem())
    ids = person_ids(draft)
    if not ids:
        raise ValueError('请上传虚拟人物参考素材。')
    for ident in ids:
        validate_photo(settings, store.get_asset(ident, private=True))
        binding = store.portrait_binding(ident)
        if binding and binding['fingerprint'] != lib.account:
            raise ValueError('人物素材的账号或项目已变更，请重新选择并核实已有素材。')
    lib.resolve_virtual_assets(ids)
    storage = storage_settings.load_config(settings)
    if not storage.ready:
        raise ValueError('自动人物入库需要配置并启用 TOS，请在后台检查对象存储设置。')
    return {'config': asdict(lib.config), 'account': lib.account,
        'input_digest': input_digest(store, draft)}


def _fail(store, ident, message, *, retryable=False, kind='person_preparation_failed'):
    store.update_preparation(ident, state='needs_attention' if retryable else 'failed',
        retryable=retryable, message=message, error_kind=kind, next_check=0)
    raise PreparationError(message, kind=kind, retryable=retryable)


def prepare_run(settings, store, run):
    """Return verified asset URIs, or yield the worker while ingestion proceeds."""
    ident = run['id']
    intent = run['private']['person_preparation']
    draft = run['snapshot']
    prep = store.get_preparation(ident)
    if not prep:
        raise PreparationError('人物准备记录缺失，请复制为新草稿。')
    try:
        current = portrait_service.load_config(settings)
        if portrait_service.fingerprint(current) != intent['account']:
            _fail(store, ident, '人物素材账号或项目已变更，请重新选择人物后提交。')
        if input_digest(store, draft) != intent['input_digest']:
            _fail(store, ident, '任务素材记录已变化，请重新上传人物素材。')
        for asset_id in person_ids(draft):
            validate_photo(settings, store.get_asset(asset_id, private=True))
        lib = PortraitLibrary(settings)
        if not prep['started_at']:
            start = time.time()
            store.update_preparation(ident, started_at=start, deadline_at=start + WAIT_SECONDS)
            prep = store.get_preparation(ident)
        if time.time() > prep['deadline_at']:
            _fail(store, ident, '人物准备超时，可继续检查原入库任务。',
                retryable=True, kind='person_preparation_timeout')
        if not prep['person_id']:
            store.update_preparation(ident, state='resolving', message='正在准备虚拟人物')
            result = lib.ensure_auto_virtual(person_ids(draft))
            store.update_preparation(ident, group_request_id=result.get('request_id'))
            if result['status'] != 'ready' or not result.get('person_id'):
                attempts = prep.get('attempts', 0) + 1
                if attempts <= 3:
                    store.update_preparation(ident, state='waiting', attempts=attempts,
                        message='正在核实虚拟人物入库结果', next_check=time.time() + 10 * attempts)
                    raise PortraitPending('正在核实虚拟人物入库结果')
                _fail(store, ident, result.get('message') or '人物入库结果待确认，请继续检查。',
                    retryable=True, kind='person_preparation_uncertain')
            store.update_preparation(ident, person_id=result['person_id'])
            prep = store.get_preparation(ident)
        person = lib.person(prep['person_id'], private=True)
        if person['person_type'] != 'AIGC':
            _fail(store, ident, '此素材属于真人，请从人物库选择已授权人物。')
        with store.connection() as db:
            hidden = db.execute('SELECT 1 FROM portrait_hidden_people WHERE person_id=? AND account=?',
                (person['id'], lib.account)).fetchone()
        if hidden:
            _fail(store, ident, '此人物已移除，请到人物库处理后重新提交。')
        uploads = dict(prep['uploads'])
        for asset_id in person_ids(draft):
            if asset_id not in uploads:
                uploads[asset_id] = lib.enqueue(person['id'], asset_id)['id']
                store.update_preparation(ident, uploads=uploads)
        if prep.get('retry_requested'):
            for photo_id in uploads.values():
                photo = lib.get_photo(photo_id, private=True)
                if photo['status'] == 'uncertain' or (photo['status'] == 'failed' and photo.get('retryable')):
                    lib.retry(photo_id)
            store.update_preparation(ident, retry_requested=False)
        for photo_id in uploads.values():
            photo = lib.get_photo(photo_id, private=True)
            if photo['status'] == 'failed':
                _fail(store, ident, photo['message'], retryable=bool(photo.get('retryable')),
                    kind='person_preparation_failed' if photo.get('retryable') else 'material_rejected')
            if photo['status'] == 'uncertain':
                _fail(store, ident, '人物素材提交结果待确认，可继续检查原记录。',
                    retryable=True, kind='person_preparation_uncertain')
        portrait = {'config': intent['config'], 'person_id': person['id'],
            'group_id': person['group_id'], 'person_type': 'AIGC', 'uploads': uploads, 'bindings': {}}
        try:
            uris = verify(portrait, store, wait_deadline=prep['deadline_at'])
        except PortraitPending:
            message = '正在准备虚拟人物，入库完成后自动生成'
            store.update_preparation(ident, state='waiting', message=message, next_check=time.time() + 10)
            raise PortraitPending(message) from None
        store.update_preparation(ident, state='ready', message='虚拟人物已就绪',
            retryable=False, next_check=0, attempts=0)
        return uris
    except (PreparationError, PortraitPending):
        raise
    except PortraitPhotoError as exc:
        _fail(store, ident, str(exc), retryable=exc.retryable, kind=exc.error_kind)
    except (portrait_service.PortraitError, Conflict) as exc:
        # These operations are read-only/idempotent; never retry an uncertain create.
        attempts = prep.get('attempts', 0) + 1
        if attempts <= 3 and time.time() <= (prep.get('deadline_at') or 0):
            store.update_preparation(ident, state='waiting', attempts=attempts,
                message='人物准备暂不可用，正在重新检查', next_check=time.time() + min(60, 10 * attempts))
            raise PortraitPending('人物准备暂不可用，正在重新检查') from None
        _fail(store, ident, str(exc), retryable=True)
    except (ValueError, LookupError, OSError) as exc:
        _fail(store, ident, str(exc) if isinstance(exc, (ValueError, LookupError)) else '人物文件无法读取，请重新上传。')
