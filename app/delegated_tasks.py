"""Explicit administrator scope for tenant editing, without impersonating sessions."""
import hashlib
import re
from fastapi import HTTPException
from . import tenancy
from .accounts import Accounts, AccountError


def target_user(root, actor, user_id, *, active=True):
    from .permissions import can_manage
    try:
        target = Accounts(tenancy.config_root(root)).get_user(user_id)
    except AccountError:
        raise HTTPException(404, '用户不存在。') from None
    if not can_manage(actor, target):
        raise HTTPException(403, '不能代操作该账号。')
    if active and (not target['enabled'] or target.get('deleted_at')):
        raise HTTPException(409, '该账号已停用或删除，不能创建或编辑任务。')
    return target


def resolve_request(request, root, actor):
    """Called after authentication/CSRF; only an explicit scoped surface rewrites."""
    path = request.url.path
    if not path.startswith('/api/admin/delegated/'):
        return None
    match = re.fullmatch(r'/api/admin/delegated/([a-f0-9]{32})/(production|portrait|previews)(/.*)?', path)
    if not match:
        raise HTTPException(404, '代操作地址无效。')
    user_id, resource, suffix = match.groups()
    suffix = suffix or ''
    method = request.method
    allowed = False
    if resource == 'production':
        allowed = ((method in {'GET','HEAD'} and re.fullmatch(r'/(?:model-options|drafts(?:/[a-f0-9]{32})?|assets(?:/[a-f0-9]{32}(?:/(?:file|thumbnail|preview|reference-status))?)?|runs(?:/[a-f0-9]{32}(?:/(?:playback(?:/(?:original|smooth))?|download|poster|defaced|base))?)?)', suffix))
                   or (method == 'POST' and suffix in {'/assets','/assets/import','/drafts','/runs','/prompt-preview','/hairstyle/preview'})
                   or (method in {'GET','HEAD'} and re.fullmatch(r'/hairstyle/preview/[a-f0-9]+', suffix))
                   or (method == 'PUT' and re.fullmatch(r'/drafts/[a-f0-9]{32}', suffix)))
    elif resource == 'portrait':
        allowed = ((method in {'GET','HEAD'} and re.fullmatch(r'/(?:config|virtual/status|people(?:/[^/]+/(?:photos|reference|thumbnail))?|photos(?:/[a-f0-9]{32}/(?:file|thumbnail))?)', suffix))
                   or (method == 'POST' and (suffix == '/photos' or re.fullmatch(r'/photos/[a-f0-9]{32}/(?:use|retry)', suffix))))
    elif resource == 'previews':
        allowed = (method == 'POST' and suffix == '') or (method in {'GET','HEAD'} and re.fullmatch(r'/[a-f0-9]{32}(?:/file)?', suffix))
    if not allowed:
        raise HTTPException(403, '此操作不在代操作范围内。')
    target = target_user(root, actor, user_id)
    request.state.delegated_user = target
    path = '/api/'+resource+suffix
    request.scope['path'] = path
    request.scope['raw_path'] = path.encode('ascii')
    if method in {'GET','HEAD'}:
        request.state.admin_media_access = (user_id, 'delegated', resource+suffix)
    return tenancy.user_settings(root, target)


def restore_draft(root, actor, user_id, run_id, key):
    from .production_store import ProductionStore
    from .production_router import _DRAFT_FIELDS
    from .prompt_visibility import project
    target = target_user(root, actor, user_id)
    if not isinstance(key, str) or not 1 <= len(key) <= 128:
        raise HTTPException(422, '缺少有效的恢复操作标识。')
    effective = tenancy.user_settings(root, target)
    store = ProductionStore(effective.storage_dir)
    store.require_visible(run_id)
    run = store.get_run(run_id)
    if run.get('error_kind') == 'submission_uncertain':
        raise HTTPException(409, '原任务提交结果待确认，请先查询原任务，避免重复生成。')
    ident = hashlib.sha256((actor['id']+'\0'+user_id+'\0'+run_id+'\0'+key).encode()).hexdigest()[:32]
    values = {k:v for k,v in run['snapshot'].items() if k in _DRAFT_FIELDS}
    values.pop('name', None)
    values['delegation'] = {'actor_id':actor['id'], 'owner_id':user_id, 'source_run_id':run_id}
    accounts = Accounts(tenancy.config_root(root))
    with accounts.active_user(user_id):
        try:
            draft = store.get_draft(ident)
        except LookupError:
            draft = store.create_draft(values, ident=ident)
    draft['assets'] = []
    from .task_records import asset_ids
    for asset_id in asset_ids(draft):
        try: draft['assets'].append(store.get_asset(asset_id))
        except LookupError: pass
    draft['delegated_user'] = {'id':user_id, 'username':target['username']}
    accounts.audit(actor['id'], 'restore_user_draft', user_id+':'+run_id,
                   {'resource':ident})
    return project(draft, effective)
