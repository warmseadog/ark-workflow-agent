"""Local production API: durable media, editable drafts and immutable runs."""
from __future__ import annotations
from dataclasses import asdict
import mimetypes
from pathlib import Path
import shutil
import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, Query
from fastapi.responses import FileResponse
from .production_store import ProductionStore, Conflict
from . import generation_settings, storage_settings, admin_settings, media, production_worker
from .media_errors import MediaPipelineError
from .person_video import is_video, person_ids, validate_file, validate_pair
from .reference_roles import ACCESSORY_LABELS, OPTIONAL_KINDS

_MODEL_FIELDS = {'provider','protocol','mode','base_url','model','duration','fps','resolution','public_base_url'}
_DRAFT_FIELDS = {'person_reference_mode','person_video_asset_id','name','person_id','source_asset_id','face_asset_ids','clothing_asset_ids','hairstyle_asset_ids','scene_asset_ids','hairstyle_enabled','hairstyle_mask','scene_enabled','scene_description','prompt','mask','model'}
_DRAFT_FIELDS |= {kind+suffix for kind in ACCESSORY_LABELS for suffix in ('_asset_ids','_enabled')}
_DRAFT_FIELDS.add('person_input_policy')
_MASK_FIELDS = {'blur_style','style','shape','mask_mode','robust_tracking','mask_scale','mosaic_size','threshold','detection_size','keep_audio'}


def get_router(settings_getter, local_guard):
    router = APIRouter(prefix='/api/production', dependencies=[Depends(local_guard)])
    def store(): return ProductionStore(settings_getter().storage_dir)
    def public_model():
        return {k:v for k,v in asdict(generation_settings.load_config(settings_getter())).items() if k in _MODEL_FIELDS}
    def guarded(operation):
        try: return operation()
        except HTTPException: raise
        except Conflict as exc: raise HTTPException(409,str(exc)) from None
        except LookupError as exc: raise HTTPException(404,str(exc)) from None
        except (ValueError,TypeError,MediaPipelineError) as exc: raise HTTPException(422,str(exc)) from None

    def decorate_draft(draft):
        ids = ([draft['source_asset_id']] if draft.get('source_asset_id') else []) + draft['face_asset_ids'] + draft['clothing_asset_ids'] + [ident for kind in OPTIONAL_KINDS for ident in draft.get(kind+'_asset_ids',[])]
        if draft.get('person_video_asset_id'):ids.append(draft['person_video_asset_id'])
        draft['assets'] = [store().get_asset(x) for x in dict.fromkeys(ids)]
        return draft

    def decorate_run(run):
        root = settings_getter().storage_dir
        run['defaced_url'] = '/api/production/runs/'+run['id']+'/defaced' if (root/'work'/run['id']/'defaced.mp4').is_file() else None
        run['download_url'] = '/api/production/runs/'+run['id']+'/download' if run['status']=='succeeded' and (root/'outputs'/(run['id']+'.mp4')).is_file() else None
        run['snapshot'] = decorate_draft(run['snapshot'])
        return run

    def validate_changes(values, current=None):
        if set(values)-_DRAFT_FIELDS:
            raise ValueError('草稿包含不支持的字段。')
        from .person_preparation import POLICIES
        if 'person_input_policy' in values:
            if not isinstance(values['person_input_policy'], str) or values['person_input_policy'] not in POLICIES:
                raise ValueError('人物来源模式不正确。')
        elif 'person_id' in values:
            if values['person_id']:
                values['person_input_policy'] = 'existing_person'
            elif (current or {}).get('person_input_policy') == 'existing_person':
                values['person_input_policy'] = 'legacy_raw'
        effective = {**(current or {}), **values}
        if effective.get('person_input_policy') == 'auto_virtual' and effective.get('person_id'):
            raise ValueError('已选择人物，请使用人物库模式；自动上传虚拟人不绑定其他人物。')
        if 'person_reference_mode' in values and values['person_reference_mode'] not in ('image','video'):
            raise ValueError('人物参考请选择图片或视频。')
        if values.get('person_video_asset_id') is not None:
            if not isinstance(values['person_video_asset_id'],str) or store().get_asset(values['person_video_asset_id'])['kind']!='person_video':
                raise ValueError('请选择人物视频素材。')
        if 'person_id' in values and values['person_id'] is not None:
            if not isinstance(values['person_id'],str): raise ValueError('请选择有效人物。')
            from .portrait_library import PortraitLibrary
            PortraitLibrary(settings_getter()).person(values['person_id'])
        for field,limit in [('name',120),('scene_description',2000)]:
            if field in values and (not isinstance(values[field],str) or len(values[field])>limit):
                raise ValueError('草稿名称或提示词长度不正确。')
        if 'prompt' in values:
            from .reference_prompt import strip_reference_rules
            prompt = values['prompt']
            if not isinstance(prompt,str) or len(prompt)>14000 or len(strip_reference_rules(prompt))>10000:
                raise ValueError('提示词正文最多 10000 字，请缩短后重试。')
        for field in (kind+'_enabled' for kind in OPTIONAL_KINDS):
            if field in values and type(values[field]) is not bool: raise ValueError('参考图启用状态不正确。')
        for field,kind in [(kind+'_asset_ids',kind) for kind in ('face','clothing',*OPTIONAL_KINDS)]:
            if field in values:
                ids=values[field]
                if not isinstance(ids,list) or len(ids)>(1 if kind in OPTIONAL_KINDS else 20) or any(not isinstance(x,str) for x in ids) or len(set(ids))!=len(ids):
                    raise ValueError('参考素材列表不正确。')
                for ident in ids:
                    try: asset=store().get_asset(ident)
                    except LookupError: raise ValueError('参考素材不存在，请重新选择。') from None
                    if asset['kind']!=kind: raise ValueError('参考素材类型不匹配。')
        if 'source_asset_id' in values and values['source_asset_id'] is not None and (not isinstance(values['source_asset_id'],str) or len(values['source_asset_id'])>100):
            raise ValueError('视频素材标识不正确。')
        if values.get('source_asset_id'):
            try: asset=store().get_asset(values['source_asset_id'])
            except LookupError: raise ValueError('视频素材不存在，请重新选择。') from None
            if asset['kind']!='video': raise ValueError('请选择视频素材。')
        if 'hairstyle_mask' in values:
            from .hairstyle_mask import options
            values['hairstyle_mask'] = options(values['hairstyle_mask'])
        if 'mask' in values:
            if not isinstance(values['mask'],dict) or set(values['mask'])-_MASK_FIELDS: raise ValueError('打码设置不正确。')
            options=production_worker.mask_options(values['mask'])
            if options.style=='img': raise ValueError('此工作台暂不支持图片覆盖模式。')
        if 'model' in values:
            if not isinstance(values['model'],dict) or set(values['model'])-_MODEL_FIELDS: raise ValueError('草稿模型配置不接受密钥或未知字段。')
            base=(current or {}).get('model') or public_model()
            normalized=generation_settings.resolve_config(settings_getter(),{**base,**values['model']})
            values['model']={k:v for k,v in asdict(normalized).items() if k in _MODEL_FIELDS}
        return values

    def register(path, name, kind):
        ident = uuid.uuid4().hex
        root=settings_getter().storage_dir/'assets'
        root.mkdir(parents=True,exist_ok=True)
        suffix=path.suffix.lower()
        destination=root/(ident+suffix)
        if path!=destination: shutil.copyfile(path,destination)
        from .artifacts import sha256_file
        return store().add_asset(ident,name,kind,destination,destination.stat().st_size,
                                mimetypes.guess_type(name)[0] or 'application/octet-stream',sha256_file(destination))

    @router.post('/assets')
    def upload_asset(kind: str=Form(...), file: UploadFile=File(...)):
        if kind not in {'video','person_video','face','clothing',*OPTIONAL_KINDS}: raise HTTPException(422,'不支持的素材类型。')
        name=Path((file.filename or '').replace('\\','/')).name
        suffix=Path(name).suffix.lower()
        supported={'.mp4','.mov','.webm','.mkv','.avi','.m4v'} if kind=='video' else {'.png','.jpg','.jpeg','.webp','.gif','.bmp','.tif','.tiff','.heic','.heif','.avif'}
        if kind=='person_video':supported={'.mp4','.mov'}
        if suffix not in supported: raise HTTPException(422,'请选择有效的视频或图片文件。')
        limit=(settings_getter().max_upload_mb if kind=='video' else 50 if kind=='person_video' else 20)*1024*1024
        root=settings_getter().storage_dir/'assets'
        root.mkdir(parents=True,exist_ok=True)
        path=root/(uuid.uuid4().hex+suffix)
        try:
            total=0
            with path.open('wb') as output:
                while chunk:=file.file.read(1024*1024):
                    total+=len(chunk)
                    if total>limit: raise HTTPException(413,'素材超过大小限制。')
                    output.write(chunk)
            if not total: raise HTTPException(422,'素材为空，请重新选择。')
            if kind=='person_video':guarded(lambda:validate_file(path))
            return guarded(lambda:register(path,name,kind))
        finally:
            path.unlink(missing_ok=True)

    @router.post('/hairstyle/preview')
    def hairstyle_preview(payload:dict):
        def operation():
            from .hairstyle_mask import process_hairstyle
            ident = payload.get('asset_id')
            if not isinstance(ident,str):raise ValueError('请选择发型参考图。')
            result = process_hairstyle(settings_getter(),store().get_asset(ident,private=True),payload.get('settings'))
            return {'url':'/api/production/hairstyle/preview/'+result['key'],
                    'faces_detected':result['faces_detected'],'settings':result['settings']}
        return guarded(operation)

    @router.get('/hairstyle/preview/{key}')
    def hairstyle_preview_file(key:str):
        import re
        if not re.fullmatch('[0-9a-f]{64}',key):raise HTTPException(404,'预览不存在。')
        path = settings_getter().storage_dir/'cache'/'hairstyle-mask'/(key+'.png')
        if not path.is_file():raise HTTPException(404,'预览不存在，请重新生成。')
        return FileResponse(path,media_type='image/png',headers={'Cache-Control':'private, max-age=3600'})

    @router.post('/assets/import')
    def import_asset(payload: dict):
        text=payload.get('text')
        if not isinstance(text,str) or not 0<len(text)<=10000: raise HTTPException(422,'请填写有效的分享链接。')
        def operation():
            import tempfile
            root=settings_getter().storage_dir/'imports'
            root.mkdir(parents=True,exist_ok=True)
            with tempfile.TemporaryDirectory(prefix='draft-',dir=root) as tmp:
                path=media.download_video(text,Path(tmp)/'reference.mp4',settings_getter())
                if not 0<path.stat().st_size<=settings_getter().max_upload_mb*1024*1024:
                    raise ValueError('视频为空或超过大小限制。')
                return register(path,'reference'+path.suffix,'video')
        return guarded(operation)

    @router.get('/assets/{ident}/file')
    def asset_file(ident: str):
        asset=guarded(lambda:store().get_asset(ident,private=True))
        path=Path(asset['path']).resolve()
        if not path.is_relative_to((settings_getter().storage_dir/'assets').resolve()) or not path.is_file():
            raise HTTPException(404,'素材文件不存在。')
        return FileResponse(path,media_type=asset['mime'],headers={'X-Content-Type-Options':'nosniff'})

    @router.post('/drafts')
    def create_draft(payload: dict):
        def operation():
            values={}
            if payload.get('copy_from') is not None and not isinstance(payload['copy_from'],str): raise ValueError('草稿标识不正确。')
            if payload.get('copy_from'):
                old=store().get_draft(payload['copy_from'])
                values={k:v for k,v in old.items() if k in _DRAFT_FIELDS}
                values['name']=values.get('name','视频')+' 副本'
            else:
                values={'model':public_model(),'mask':{'blur_style':'mosaic','mask_mode':'face','mask_scale':1.4,'threshold':0.2,'keep_audio':True,'robust_tracking':False},
                        'prompt':'保持@Video1原视频的动作、镜头和节奏；应用@Image1人物参考图；应用@Image2服装参考图，保持自然稳定。'}
            if 'name' in payload: values['name']=payload['name']
            if 'person_input_policy' in payload: values['person_input_policy']=payload['person_input_policy']
            validate_changes(values)
            return decorate_draft(store().create_draft(values))
        return guarded(operation)

    @router.get('/drafts')
    def list_drafts():
        return guarded(lambda:{'items':[decorate_draft(x) for x in store().list_drafts()]})

    @router.get('/drafts/{ident}')
    def get_draft(ident: str): return guarded(lambda:decorate_draft(store().get_draft(ident)))

    @router.put('/drafts/{ident}')
    def save_draft(ident: str,payload: dict):
        def operation():
            revision=payload.get('revision')
            if type(revision) is not int: raise ValueError('缺少草稿版本，请重新打开草稿。')
            values=validate_changes({k:v for k,v in payload.items() if k!='revision'},store().get_draft(ident))
            return decorate_draft(store().save_draft(ident,revision,values))
        return guarded(operation)

    @router.post('/runs')
    def submit_run(payload: dict):
        def operation():
            key=payload.get('idempotency_key')
            revision=payload.get('revision')
            if not isinstance(key,str) or not 1<=len(key)<=128 or type(revision) is not int: raise ValueError('缺少有效的提交标识或草稿版本。')
            draft_id=payload.get('draft_id')
            if not isinstance(draft_id,str) or not 1<=len(draft_id)<=100: raise ValueError('草稿标识不正确。')
            previous=store().run_by_key(key)
            if previous:
                if previous['id'] in store().deleted_run_ids(): raise Conflict('此前提交的任务已删除，请刷新并发起新的提交。')
                if previous['draft_id']!=draft_id or previous['revision']!=revision: raise Conflict('提交标识已用于另一份输入。')
                return decorate_run(previous)
            draft=store().get_draft(draft_id)
            if draft['revision'] != revision:
                raise Conflict('草稿发生变化，请保存后重新提交。')
            if not draft['source_asset_id'] or not person_ids(draft) or not draft['clothing_asset_ids']:
                raise ValueError('请选择动作视频、人物参考和衣服图。')
            validate_changes({k:v for k,v in draft.items() if k in _DRAFT_FIELDS})
            config=generation_settings.resolve_config(settings_getter(),draft['model'])
            from .person_preparation import input_policy, preflight
            policy = input_policy(draft, store())
            if policy == 'existing_person' and not draft.get('person_id') and not any(store().portrait_binding(x) for x in person_ids(draft)):
                raise ValueError('请先从人物库选择人物或素材。')
            if is_video(draft):
                if not draft.get('person_id') and policy != 'auto_virtual':raise ValueError('人物视频请先选择人物并入库检查。')
                from .portrait_generation import OFFICIAL_BASE
                if config.protocol!='ark' or config.base_url.rstrip('/')!=OFFICIAL_BASE:
                    raise ValueError('人物视频目前支持火山官方接口，请在后台检查模型配置。')
                if not config.model.startswith(('doubao-seedance-2-0','doubao-seedance-2.0')):
                    raise ValueError('人物视频目前按 Seedance 2.0 接入，请在后台选择对应模型。')
                validate_pair(store().get_asset(draft['source_asset_id'],private=True)['path'],store().get_asset(draft['person_video_asset_id'],private=True)['path'])
            storage=storage_settings.load_config(settings_getter())
            problem=admin_settings.generation_problem(config,storage)
            if problem: raise HTTPException(409,problem)
            extra_ids = [ident for kind in OPTIONAL_KINDS if draft.get(kind+'_enabled',False) for ident in draft.get(kind+'_asset_ids',[])]
            if config.protocol!='adapter' and (0 if is_video(draft) else len(draft['face_asset_ids']))+len(draft['clothing_asset_ids'])+len(extra_ids)>9:
                raise ValueError('人物、衣服、发型、场景和配饰参考图合计最多 9 张，请关闭部分可选项。')
            for ident in [draft['source_asset_id']]+person_ids(draft)+draft['clothing_asset_ids']+extra_ids:
                if not Path(store().get_asset(ident,private=True)['path']).is_file(): raise ValueError('素材文件不存在，请重新导入。')
            from .portrait_generation import prepare
            private={'generation':asdict(config),'storage':asdict(storage)}
            if policy == 'auto_virtual':
                private['person_preparation'] = preflight(settings_getter(),store(),draft,config)
            else:
                portrait=prepare(settings_getter(),store(),draft,config)
                if portrait: private['portrait']=portrait
            run=store().create_run(draft_id,revision,key,private)
            production_worker.wake(settings_getter())
            return decorate_run(run)
        return guarded(operation)

    def legacy_runs():
        from .jobs import store as jobs
        result=[]
        deleted = store().deleted_run_ids()
        for job in jobs.list():
            if 'legacy-'+job.id in deleted: continue
            value=job.public()
            value.update(id='legacy-'+job.id, name='历史视频 '+job.created_at[:16].replace('T',' '), legacy=True,
                         stage='complete' if job.status=='succeeded' else 'legacy',snapshot={},draft_id=None,
                         error_kind='material_rejected' if 'may contain real person' in (job.error or '') else None,
                         request_id=None,provider_task_id=None,can_delete=job.status not in {'running','queued'})
            if value['error_kind']=='material_rejected': value['message']='人物参考图未通过模型检查，请复制为草稿后修改。'
            value['name']=store().run_name(value['id'],value['name'])
            result.append(value)
        return result

    @router.get('/runs')
    def list_runs(page:int | None=Query(None,ge=1),page_size:int=Query(10,ge=1,le=50)):
        def operation():
            if page is not None:
                result=store().page_runs(page,page_size,legacy_runs())
                for item in result['items']:
                    if not item['legacy']:
                        path=settings_getter().storage_dir/'outputs'/(item['id']+'.mp4')
                        item['download_url']='/api/production/runs/'+item['id']+'/download' if item['status']=='succeeded' and path.is_file() else None
                return result
            items=[decorate_run(x) for x in store().list_runs()]+legacy_runs()
            return {'items':sorted(items,key=lambda x:x['created_at'],reverse=True)}
        return guarded(operation)

    @router.get('/runs/{ident}')
    def get_run(ident: str):
        def operation():
            store().require_visible(ident)
            if ident.startswith('legacy-'):
                value = next((x for x in legacy_runs() if x['id']==ident), None)
                if value is None: raise LookupError('找不到任务。')
                return value
            return decorate_run(store().get_run(ident))
        return guarded(operation)

    @router.put('/runs/{ident}/name')
    def rename_run(ident:str,payload:dict):
        def operation():
            store().require_visible(ident)
            if ident.startswith('legacy-'):
                from .jobs import store as jobs
                if not jobs.get(ident[7:]):raise LookupError('找不到任务。')
            return store().rename_run(ident,payload.get('name'))
        return guarded(operation)

    @router.delete('/runs/{ident}')
    def delete_run(ident: str):
        def operation():
            if ident.startswith('legacy-'):
                from .jobs import store as jobs
                with jobs._lock:
                    job = jobs.get(ident[7:])
                    store().delete_run(ident, legacy_status=job.status if job else None)
            else:
                store().delete_run(ident)
            return {'id':ident, 'deleted':True}
        return guarded(operation)

    @router.post('/runs/{ident}/cancel')
    def cancel_run(ident: str):
        def operation():
            store().require_visible(ident)
            return decorate_run(store().cancel_run(ident))
        return guarded(operation)

    @router.post('/runs/{ident}/resume')
    def resume_run(ident: str):
        def operation():
            store().require_visible(ident)
            run=store().resume_run(ident)
            production_worker.wake(settings_getter())
            return decorate_run(run)
        return guarded(operation)

    @router.post('/runs/{ident}/person-preparation/retry')
    def retry_person_preparation(ident: str):
        def operation():
            store().require_visible(ident)
            run = store().retry_preparation(ident)
            production_worker.wake(settings_getter())
            return decorate_run(run)
        return guarded(operation)

    @router.post('/runs/{ident}/copy')
    def copy_run(ident: str):
        def operation():
            store().require_visible(ident)
            if ident.startswith('legacy-'):
                from .jobs import store as jobs
                job=jobs.get(ident[7:])
                if not job: raise LookupError('找不到历史任务。')
                work=settings_getter().storage_dir/'work'/job.id
                values={'name':'历史视频 副本','model':public_model()}
                sources=list(work.glob('source.*'))
                if sources: values['source_asset_id']=register(sources[0],sources[0].name,'video')['id']
                for kind in ('face','clothing'):
                    values[kind+'_asset_ids']=[register(p,p.name,kind)['id'] for p in sorted(work.glob(kind+'-*')) if p.is_file()]
            else:
                old=store().get_run(ident)['snapshot']
                values={k:v for k,v in old.items() if k in _DRAFT_FIELDS}
                values['name']=store().get_run(ident)['name']+' 副本'
                preparation = store().get_preparation(ident)
                if preparation and preparation.get('person_id'):
                    from .portrait_library import PortraitLibrary
                    lib = PortraitLibrary(settings_getter())
                    if lib.account == preparation['account']:
                        values.update(person_id=preparation['person_id'], person_input_policy='existing_person')
            return decorate_draft(store().create_draft(values))
        return guarded(operation)

    @router.get('/runs/{ident}/playback')
    def playback_status(ident:str):
        from .playback import status
        return guarded(lambda:status(settings_getter(),ident))

    @router.get('/runs/{ident}/playback/{quality}')
    def playback_file(ident:str,quality:str):
        from .playback import media_path
        path=guarded(lambda:media_path(settings_getter(),ident,quality))
        return FileResponse(path,media_type='video/mp4',headers={'Cache-Control':'private, max-age=86400'})

    @router.get('/runs/{ident}/{kind}')
    def run_file(ident: str,kind: str):
        guarded(lambda:store().require_visible(ident))
        run=guarded(lambda:store().get_run(ident))
        if kind=='defaced': path=settings_getter().storage_dir/'work'/run['id']/'defaced.mp4'
        elif kind=='download' and run['status']=='succeeded': path=settings_getter().storage_dir/'outputs'/(run['id']+'.mp4')
        else: raise HTTPException(404,'视频尚未就绪。')
        if not path.is_file(): raise HTTPException(404,'视频文件不存在。')
        import re
        filename=re.sub(r'[\\/:*?"<>|]', '_',run['name']).strip(' .') or '视频'
        return FileResponse(path,media_type='video/mp4',filename=filename+'.mp4' if kind=='download' else None,headers={'Cache-Control':'private, max-age=3600'})

    return router
