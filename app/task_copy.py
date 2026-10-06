"""Copy authorized task inputs into the administrator's own tenant, never sessions."""
import hashlib
from pathlib import Path
import shutil
import tempfile
import uuid

from fastapi import HTTPException

from . import tenancy
from .accounts import Accounts, AccountError
from .artifacts import sha256_file
from .delegated_tasks import target_user
from .production_store import ProductionStore, now


def copy_draft(root, actor, user_id, run_id, key):
    from .production_router import _DRAFT_FIELDS
    from .model_catalog import TASK_FIELDS
    from .reference_roles import OPTIONAL_KINDS
    from .task_records import asset_ids
    from .portrait_library import PortraitLibrary
    from .shared_portraits import authorize_asset, provenance_store, SharedPortraitCatalog

    target = target_user(root, actor, user_id)
    if not isinstance(key, str) or not 1 <= len(key) <= 128:
        raise HTTPException(422, '缺少有效的复制操作标识。')
    source_settings = tenancy.user_settings(root, target)
    own_settings = tenancy.user_settings(root, actor)
    source = ProductionStore(source_settings.storage_dir)
    destination = ProductionStore(own_settings.storage_dir)
    source.require_visible(run_id)
    run = source.get_run(run_id)
    if run.get('error_kind') == 'submission_uncertain':
        raise HTTPException(409, '原任务提交结果待确认，请先查询原任务，避免重复生成。')
    ident = hashlib.sha256(('admin-copy-v1\0'+actor['id']+'\0'+user_id+'\0'+run_id+'\0'+key).encode()).hexdigest()[:32]

    def decorated(draft):
        draft['assets'] = [destination.get_asset(asset_id) for asset_id in asset_ids(draft)]
        return draft

    try:
        return decorated(destination.get_draft(ident))
    except LookupError:
        pass
    values = {k:v for k,v in run['snapshot'].items() if k in _DRAFT_FIELDS}
    values['model'] = {k:v for k,v in values.get('model', {}).items() if k in TASK_FIELDS}
    # Frozen tasks retain disabled editor options. Only actual inputs are needed
    # for reproduction; stale, unused references must not block a valid copy.
    for kind in OPTIONAL_KINDS:
        if not values.get(kind+'_enabled'):
            values[kind+'_asset_ids'] = []
    if values.get('person_reference_mode') == 'video':
        values['face_asset_ids'] = []
    else:
        values['person_video_asset_id'] = None
    values.pop('name', None)
    values['copied_from'] = {'user_id':user_id, 'run_id':run_id}
    source_library = PortraitLibrary(source_settings)
    own_library = PortraitLibrary(own_settings)
    provenance_store(destination)
    # A completed automatic preparation has a usable identity outside the snapshot.
    preparation = source.get_preparation(run_id)
    if preparation and preparation.get('person_id') and preparation.get('account') == source_library.account:
        values.update(person_id=preparation['person_id'], person_input_policy='existing_person')
    people = {}

    def remember_person(library, person_id):
        person = library.person(person_id, private=True)
        library.require_available(person_id)
        if library.account != own_library.account:
            raise HTTPException(409, '人物素材的账号或项目配置已变更，请重新选择。')
        # Copying must not silently restore an identity removed by the admin.
        own_library.require_available(person_id)
        people[person_id] = person
        return person

    if values.get('person_id'):
        remember_person(source_library, values['person_id'])
    files = []
    mapping = {}
    asset_root = own_settings.storage_dir / 'assets'
    asset_root.mkdir(parents=True, exist_ok=True)
    moved = []
    try:
        with tempfile.TemporaryDirectory(prefix='.task-copy-', dir=asset_root) as staging:
            for old_id in asset_ids(values):
                asset = source.get_asset(old_id, private=True)
                path = Path(asset['path']).resolve()
                if not path.is_relative_to((source_settings.storage_dir/'assets').resolve()) or not path.is_file():
                    raise HTTPException(409, '原任务素材文件已丢失，无法完整复制。')
                origin = authorize_asset(source_settings, old_id)
                new_id = uuid.uuid4().hex
                mapping[old_id] = new_id
                staged = Path(staging)/(new_id+path.suffix)
                shutil.copyfile(path, staged)
                if staged.stat().st_size != asset['size'] or sha256_file(staged) != asset['sha256']:
                    raise HTTPException(409, '原任务素材文件已变化，无法完整复制。')
                binding = source.portrait_binding(old_id)
                photo = person = provenance = None
                if origin:
                    library, photo = origin
                    person = remember_person(library, photo['person_id'])
                    own_library.require_available(person['id'], sha256=asset['sha256'])
                    if person['person_type'] == 'LivenessFace':
                        # Real-person revocation remains authoritative after copying.
                        SharedPortraitCatalog(own_settings).photo_source(photo['id'])
                        provenance = (photo['person_id'], photo['id'])
                    if photo['status'] == 'active' and photo.get('remote_id'):
                        binding = dict(remote_asset_id=photo['remote_id'], group_id=person['group_id'],
                                       project=library.config.project_name, fingerprint=library.account, status='Active')
                if binding:
                    pid = hashlib.sha256((binding['fingerprint']+binding['group_id']).encode()).hexdigest()[:32]
                    if pid not in people:
                        remember_person(source_library, pid)
                    own_library.require_available(pid, sha256=asset['sha256'])
                files.append((asset, new_id, staged, binding, photo, person, provenance))
            for field, value in list(values.items()):
                if field.endswith('_asset_id') and value:
                    values[field] = mapping[value]
                elif field.endswith('_asset_ids'):
                    values[field] = [mapping[x] for x in value]
            accounts = Accounts(tenancy.config_root(root))
            with accounts.active_user(actor['id']), destination.connection() as db:
                db.execute('BEGIN IMMEDIATE')
                existing = db.execute('SELECT * FROM production_drafts WHERE id=?', (ident,)).fetchone()
                if existing:
                    draft = destination._draft(existing)
                else:
                    for person in people.values():
                        db.execute('INSERT OR IGNORE INTO portrait_people (id,account,group_id,name,created,person_type) VALUES (?,?,?,?,?,?)',
                                   tuple(person[k] for k in ('id','account','group_id','name','created','person_type')))
                    for asset, new_id, staged, binding, photo, person, provenance in files:
                        final = asset_root/staged.name
                        staged.replace(final)
                        moved.append(final)
                        db.execute('INSERT INTO production_assets VALUES (?,?,?,?,?,?,?,?)',
                                   (new_id, asset['name'], asset['kind'], str(final), asset['size'], asset['mime'], asset['sha256'], now()))
                        if binding:
                            db.execute('INSERT INTO production_portraits VALUES (?,?,?,?,?,?,?)',
                                       (new_id, binding['remote_asset_id'], binding['group_id'], binding['project'], binding['fingerprint'], binding['status'], now()))
                        if photo and photo['status'] == 'active':
                            db.execute('INSERT OR IGNORE INTO portrait_photos VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                                       (uuid.uuid4().hex, own_library.account, person['id'], new_id, asset['sha256'], 'active',
                                        photo['remote_id'], '官方素材可用', 0, photo['created'], photo['checked']))
                        if provenance:
                            db.execute('INSERT INTO shared_portrait_provenance VALUES (?,?,?)', (new_id, *provenance))
                    draft = destination.create_draft(values, ident=ident, connection=db)
            moved.clear()  # Committed files now belong to the destination tenant.
            accounts.audit(actor['id'], 'copy_user_task', user_id+':'+run_id, {'resource':ident})
            return decorated(draft)
    except AccountError as exc:
        raise HTTPException(exc.status_code, exc.detail) from None
    except OSError:
        raise HTTPException(409, '素材复制失败，请检查存储空间后重试。') from None
    finally:
        for path in moved:
            path.unlink(missing_ok=True)
