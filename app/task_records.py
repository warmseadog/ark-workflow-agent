"""Read-only administrator task records, with explicit tenant and run authorization."""
from pathlib import Path
import re

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from . import tenancy, playback
from .accounts import Accounts
from .production_store import ProductionStore, unknown_timing
from .reference_roles import OPTIONAL_KINDS
from .media_transport import media_file_response


def legacy_records(settings):
    if not tenancy.legacy_allowed(settings):
        return []
    from .jobs import store as jobs
    store = ProductionStore(settings.storage_dir)
    deleted = store.deleted_run_ids()
    result = []
    for job in jobs.list():
        if 'legacy-'+job.id in deleted:
            continue
        value = job.public()
        value.update(id='legacy-'+job.id, name='历史视频 '+job.created_at[:16].replace('T',' '), legacy=True,
                     stage='complete' if job.status=='succeeded' else 'legacy', snapshot={}, draft_id=None,
                     error_kind='material_rejected' if 'may contain real person' in (job.error or '') else None,
                     request_id=None, provider_task_id=None, can_delete=job.status not in {'running','queued'},
                     timing=unknown_timing())
        if value['error_kind']=='material_rejected':
            value['message']='人物参考图未通过模型检查，请复制为草稿后修改。'
        value['name']=store.run_name(value['id'],value['name'])
        result.append(value)
    return result


def asset_ids(snapshot):
    ids = [snapshot.get('source_asset_id'), snapshot.get('person_video_asset_id')]
    for kind in ('face', 'clothing', *OPTIONAL_KINDS):
        ids.extend(snapshot.get(kind+'_asset_ids', []))
    return list(dict.fromkeys(ident for ident in ids if ident))


def defaced_path(settings, run):
    if run.get('legacy'):
        root = (settings.storage_dir/'work'/run['id'][7:]).resolve()
        path = (root/(run.get('defaced_name') or 'defaced.mp4')).resolve()
        if not path.is_relative_to(root):
            raise LookupError('视频文件不可用。')
        return path
    return settings.storage_dir/'work'/run['id']/'defaced.mp4'


def get_router(settings_getter, admin):
    def require_admin(request: Request):
        return admin(request)

    router = APIRouter(prefix='/api/admin/task-records', dependencies=[Depends(require_admin)])

    def guarded(operation):
        try:
            return operation()
        except PermissionError as error:
            raise HTTPException(403, str(error)) from None
        except LookupError as error:
            raise HTTPException(404, str(error)) from None
        except ValueError as error:
            raise HTTPException(422, str(error)) from None

    def scope(user_id):
        user = next((u for u in Accounts(settings_getter().storage_dir).list_users() if u['id']==user_id), None)
        if not user:
            raise HTTPException(404, '用户不存在。')
        settings = tenancy.user_settings(settings_getter(), user)
        return user, settings, ProductionStore(settings.storage_dir)

    def record(user_id, run_id):
        user, settings, store = scope(user_id)
        store.require_visible(run_id)
        if run_id.startswith('legacy-'):
            run = next((r for r in legacy_records(settings) if r['id']==run_id), None)
            if not run:
                raise LookupError('找不到任务。')
        else:
            run = store.get_run(run_id)
        return user, settings, store, run

    def base(user, run):
        return '/api/admin/task-records/'+user['id']+'/'+run['id']

    def media_access(request, user_id, run_id, resource, response):
        # The session middleware records this only after the actual response
        # status is known, so missing files and rejected ranges are not successes.
        request.state.admin_media_access = (user_id, run_id, resource)
        return response

    def decorate(user, settings, run, actor):
        run.update(user_id=user['id'], username=user['username'], read_only=user['id']!=actor['id'])
        if run['read_only']:
            for key in ('can_cancel','can_resume','can_delete','can_retry_preparation','can_retry_without_audio'):
                run[key] = False
        try:
            available = run['status']=='succeeded' and playback.source_path(settings,run['id']).is_file()
        except LookupError:
            available = False
        run['download_url'] = base(user,run)+'/download' if available else None
        return run

    @router.get('')
    def records(request: Request, user_id: str='', page: int=Query(1,ge=1,le=1000000), page_size: int=Query(10,ge=1,le=50)):
        actor = admin(request)
        tenants = tenancy.tenant_settings(settings_getter(), include_disabled=True)
        users = [{key:u[key] for key in ('id','username','enabled')} for u,_ in tenants]
        if user_id:
            tenants = [(u,s) for u,s in tenants if u['id']==user_id]
            if not tenants:
                raise HTTPException(404, '用户不存在。')
        candidates = []
        total = active = 0
        for user, settings in tenants:
            result = ProductionStore(settings.storage_dir).page_runs(1, page*page_size, legacy_records(settings))
            total += result['total']
            active += result['active_count']
            candidates.extend((item,user,settings) for item in result['items'])
        pages = max(1, (total+page_size-1)//page_size)
        page = min(page,pages)
        candidates.sort(key=lambda row:(row[0]['created_at'],row[0]['id'],row[1]['id']), reverse=True)
        items = [decorate(user,settings,item,actor) for item,user,settings in candidates[(page-1)*page_size:page*page_size]]
        # List polling does not add audit noise; opening individual records is audited.
        return dict(items=items,users=users,total=total,active_count=active,page=page,pages=pages,page_size=page_size)

    @router.get('/{user_id}/{run_id}')
    def detail(request: Request, user_id: str, run_id: str):
        def operation():
            user, settings, store, run = record(user_id,run_id)
            decorate(user,settings,run,admin(request))
            run['defaced_url'] = base(user,run)+'/defaced' if defaced_path(settings,run).is_file() else None
            snapshot = run['snapshot']
            snapshot['assets'] = []
            for ident in asset_ids(snapshot):
                try:
                    asset = store.get_asset(ident)
                except LookupError:
                    continue  # Missing historical material must not hide the task itself.
                asset_base = base(user,run)+'/assets/'+ident
                asset.update(url=asset_base+'/file',thumbnail_url=asset_base+'/thumbnail')
                snapshot['assets'].append(asset)
            Accounts(settings_getter().storage_dir).audit(admin(request)['id'],'view_user_task',user_id+':'+run_id)
            return run
        return guarded(operation)

    @router.get('/{user_id}/{run_id}/playback')
    def playback_status(user_id: str, run_id: str):
        def operation():
            user, settings, _, run = record(user_id,run_id)
            result = playback.status(settings,run_id)
            prefix = base(user,run)
            result['original_url'] = prefix+('/playback/original' if result['status']=='ready' else '/download')
            result['smooth_url'] = prefix+'/playback/smooth' if result['status']=='ready' else None
            return result
        return guarded(operation)

    @router.api_route('/{user_id}/{run_id}/playback/{quality}', methods=['GET','HEAD'])
    def playback_file(request: Request, user_id: str, run_id: str, quality: str):
        def operation():
            _, settings, _, _ = record(user_id,run_id)
            response = media_file_response(settings,playback.media_path(settings,run_id,quality),media_type='video/mp4')
            return media_access(request,user_id,run_id,'playback/'+quality,response)
        return guarded(operation)

    @router.api_route('/{user_id}/{run_id}/assets/{asset_id}/{kind}', methods=['GET','HEAD'])
    def asset_file(request: Request, user_id: str, run_id: str, asset_id: str, kind: str):
        def operation():
            _, settings, _, run = record(user_id,run_id)
            if asset_id not in asset_ids(run['snapshot']):
                raise LookupError('此素材不属于该任务。')
            from .asset_preview import preview_asset
            asset = preview_asset(settings,asset_id)
            if kind=='thumbnail':
                from .asset_thumbnails import thumbnail_response
                response = thumbnail_response(settings,asset)
            elif kind=='file':
                response = media_file_response(settings,Path(asset['path']),media_type=asset['mime'])
            else:
                raise LookupError('素材不存在。')
            return media_access(request,user_id,run_id,'assets/'+asset_id+'/'+kind,response)
        return guarded(operation)

    @router.api_route('/{user_id}/{run_id}/{kind}', methods=['GET','HEAD'])
    def run_file(request: Request, user_id: str, run_id: str, kind: str):
        def operation():
            _, settings, _, run = record(user_id,run_id)
            if kind=='download' and run['status']=='succeeded':
                path = playback.source_path(settings,run_id)
            elif kind=='defaced':
                path = defaced_path(settings,run)
            else:
                raise LookupError('视频尚未就绪。')
            if not path.is_file():
                raise LookupError('视频文件不存在。')
            filename = re.sub(r'[\\/:*?"<>|]','_',run['name']).strip(' .') or '视频'
            response = media_file_response(settings,path,media_type='video/mp4',filename=filename+'.mp4' if kind=='download' else None)
            return media_access(request,user_id,run_id,kind,response)
        return guarded(operation)

    return router
