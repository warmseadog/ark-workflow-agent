"""Local official-portrait import. QR presentation never grants authorization."""
from __future__ import annotations
from io import BytesIO
from pathlib import Path
from urllib.parse import urlsplit
import hashlib
import re
import uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from . import portrait_service
from .portrait_sessions import public_base
from .production_store import ProductionStore

CONSOLE_URL = 'https://console.volcengine.com/ark/region:ark+cn-beijing/experience/vision?modelId=doubao-seedance-2-0-260128'


def _use_local_reference(lib, ident):
    """Select already verified media exclusively from this tenant's library."""
    from .portrait_library import validate_photo

    photo = lib.get_photo(ident, private=True)
    if photo['status'] != 'active' or not photo['remote_id']:
        raise ValueError('人物素材尚未通过官方检查，请稍后刷新。')
    person = lib.person(photo['person_id'], private=True)
    asset = lib.store.get_asset(photo['asset_id'], private=True)
    if asset['kind'] not in {'face', 'person_video'} or asset['sha256'] != photo['sha256']:
        raise ValueError('人物素材与本地校验记录不匹配，请重新选择。')
    # Verified imports can be below upload recommendations; bytes, format and
    # tenant-local path must still validate. Videos retain full video validation.
    validate_photo(lib.settings, asset, require_upload_dimensions=False)
    asset_type = 'Video' if asset['kind'] == 'person_video' else 'Image'
    remote = lib.api(person['person_type'], asset_type).get_asset(photo['remote_id'])
    if (remote['remote_asset_id'] != photo['remote_id']
            or remote['group_id'] != person['group_id']
            or remote['project'] != lib.config.project_name
            or remote['asset_type'] != asset_type
            or remote['person_type'] != person['person_type']
            or remote['status'] != 'Active'):
        raise ValueError('素材与所选人物、项目或类型不匹配，请重新选择。')
    lib.store.bind_portrait(asset['id'], remote, lib.account)
    return {**lib.store.get_asset(asset['id']), 'person_id': person['id'],
            'person_type': person['person_type']}


def get_router(settings_getter, local_guard):
    router=APIRouter(prefix='/api/portrait',dependencies=[Depends(local_guard)])
    def store(): return ProductionStore(settings_getter().storage_dir)
    def client(person_type='LivenessFace'):
        config=portrait_service.load_config(settings_getter())
        if not config.ready: raise HTTPException(409,config.problem())
        return (portrait_service.ArkPortraitClient(config) if person_type=='LivenessFace' else portrait_service.ArkPortraitClient(config,person_type=person_type)),config
    def guarded(operation):
        try: return operation()
        except HTTPException: raise
        except PermissionError as exc: raise HTTPException(403,str(exc)) from None
        except LookupError as exc: raise HTTPException(404,str(exc)) from None
        except ValueError as exc: raise HTTPException(422,str(exc)) from None

    def library():
        from .portrait_library import PortraitLibrary
        return PortraitLibrary(settings_getter())

    def catalog():
        from .shared_portraits import SharedPortraitCatalog
        return SharedPortraitCatalog(settings_getter())

    @router.get('/people')
    def people(removed:bool=False): return guarded(lambda: {'items': catalog().people(removed=removed)})

    @router.post('/people')
    def create_person(payload:dict):
        def operation():
            if payload.get('person_type')!='AIGC': raise ValueError('真人请通过本人认证添加。')
            return library().create_virtual(payload.get('name'),payload.get('request_id'))
        return guarded(operation)

    @router.get('/virtual/status')
    def virtual_status():
        from . import storage_settings
        lib=library()
        return {**lib.virtual_status(),'person_type':'AIGC','ready':lib.config.ready,
                'project_name':lib.config.project_name,'tos_ready':storage_settings.load_config(settings_getter()).ready,
                'permission':'unchecked','requests':lib.virtual_requests(),
                'message':'使用当前项目 AK/SK；只读检查通过后仍需官方允许创建和生成。'}

    @router.post('/virtual/test')
    def virtual_test():
        def operation():
            api,_=client('AIGC')
            groups=api.list_groups()
            return {'ok':True,'group_count':len(groups),'message':f'AIGC 素材组只读查询成功，共 {len(groups)} 组；不代表创建或生成权限已开通。'}
        return guarded(operation)

    @router.post('/people/sync')
    def sync_people(payload:dict=None):
        def operation():
            person_type=(payload or {}).get('person_type','LivenessFace')
            api,_=client(person_type); lib=library()
            for group in api.list_groups(): lib.add_person(group['Id'],group.get('Name',''),person_type)
            return {'items':lib.people()}
        return guarded(operation)

    @router.post('/people/resolve')
    def resolve_person(payload:dict):
        def operation():
            person_type=payload.get('person_type','LivenessFace')
            api,_=client(person_type)
            group=api.get_group(payload.get('group_id'))
            return library().add_person(group['Id'],group.get('Name',''),person_type)
        return guarded(operation)

    @router.put('/people/{ident}')
    def rename_person(ident:str,payload:dict):
        return guarded(lambda:catalog().require_manage(ident).rename(ident,payload.get('name')))

    @router.delete('/people/{ident}')
    def remove_person(ident:str):
        return guarded(lambda:catalog().remove_person(ident))

    @router.post('/people/{ident}/restore')
    def restore_person(ident:str):
        return guarded(lambda:catalog().restore_person(ident))

    @router.get('/people/{ident}/photos')
    def person_photos(ident:str):
        return guarded(lambda: {'items':catalog().photos_for_person(ident)})

    @router.get('/people/{ident}/reference')
    def person_reference(ident:str):
        def operation():
            cat=catalog()
            for photo in cat.photos_for_person(ident):
                if photo['kind']=='face' and photo['status']=='active':
                    _,raw=cat.photo_source(photo['id'])
                    return {'remote_asset_id':raw['remote_id']}
            raise ValueError('还没有可用照片。')
        return guarded(operation)

    @router.get('/people/{ident}/thumbnail')
    def thumbnail(ident:str):
        from .asset_thumbnails import thumbnail_response
        def operation():
            cat=catalog()
            for photo in cat.photos_for_person(ident):
                if photo['kind']=='face':
                    lib,raw=cat.photo_source(photo['id'])
                    return thumbnail_response(lib.settings,lib.store.get_asset(raw['asset_id'],private=True))
            raise LookupError('人物缩略图尚未生成。')
        return guarded(operation)

    @router.post('/photos')
    def create_photo(payload:dict):
        def operation():
            if not all(isinstance(payload.get(k),str) for k in ('person_id','asset_id')):
                raise ValueError('请选择人物并上传照片。')
            source=catalog().require_manage(payload['person_id'])
            from .shared_portraits import authorize_asset
            authorize_asset(settings_getter(),payload['asset_id'])
            person=source.person(payload['person_id'],private=True)
            library().add_person(person['group_id'],person['name'],person['person_type'])
            result=library().enqueue(payload['person_id'],payload['asset_id'])
            from .production_worker import wake
            wake(settings_getter())
            return result
        return guarded(operation)

    @router.post('/photos/{ident}/use')
    def use_reference(ident:str):
        return guarded(lambda:catalog().use_reference(ident))

    @router.delete('/photos/{ident}')
    def remove_photo(ident:str):
        return guarded(lambda:catalog().remove_photo(ident))

    @router.api_route('/photos/{ident}/file', methods=['GET','HEAD'])
    def photo_file(ident:str):
        from fastapi.responses import FileResponse
        def operation():
            lib,photo=catalog().photo_source(ident)
            asset=lib.store.get_asset(photo['asset_id'],private=True)
            from .portrait_library import validate_photo
            path=validate_photo(lib.settings,asset,require_upload_dimensions=False)
            from .media_transport import media_file_response
            return media_file_response(settings_getter(),path,media_type=asset['mime'])
        return guarded(operation)

    @router.get('/photos/{ident}/thumbnail')
    def photo_thumbnail(ident:str):
        from .asset_thumbnails import thumbnail_response
        def operation():
            lib,photo=catalog().photo_source(ident)
            return thumbnail_response(lib.settings,lib.store.get_asset(photo['asset_id'],private=True))
        return guarded(operation)

    @router.get('/photos')
    def photos(ids:str=''):
        def operation():
            keys=list(dict.fromkeys(ids.split(','))) if ids else []
            if len(keys)>20 or any(not re.fullmatch(r'[a-f0-9]{32}',key) for key in keys):
                raise ValueError('照片查询参数不正确。')
            lib=library()
            return {'items':[catalog().photo_source(key)[0].get_photo(key) for key in keys]}
        return guarded(operation)

    @router.post('/photos/{ident}/retry')
    def retry_photo(ident:str):
        def operation():
            lib,photo=catalog().photo_source(ident)
            catalog().require_manage(photo['person_id'])
            return lib.retry(ident)
        return guarded(operation)

    @router.get('/config')
    def config():
        return {**portrait_service.public_config(settings_getter()),'console_url':CONSOLE_URL,'mode':'automatic' if public_base() else 'console_invitation'}

    @router.put('/config')
    def save_config(payload:dict):
        guarded(lambda:portrait_service.save_config(settings_getter(),payload))
        return config()

    @router.post('/test')
    def test():
        def operation():
            api,_=client()
            items=api.list_assets()
            return {'ok':True,'message':f'真人素材接口访问成功，查到 {len(items)} 张可用图片。'}
        return guarded(operation)

    @router.get('/assets')
    def assets(person_type:str='LivenessFace'):
        def operation():
            api,_=client(person_type)
            return {'items':[{'id':a['remote_asset_id'],'name':a['name'],'group_id':a['group_id'],
                             'status':a['status'],'asset_type':a['asset_type']} for a in api.list_assets()]}
        return guarded(operation)

    @router.post('/qr')
    def qr(payload:dict):
        url=payload.get('url')
        try:
            if not isinstance(url,str) or not 1<=len(url)<=4096 or any(ord(c)<32 for c in url):raise ValueError()
            parsed=urlsplit(url.strip())
            if (parsed.scheme!='https' or parsed.hostname not in {'ark.volcengine.com','console.volcengine.com'}
                    or parsed.username or parsed.password or parsed.port not in {None,443}):raise ValueError()
        except ValueError:
            raise HTTPException(422,'请粘贴火山方舟官方生成的 HTTPS 真人邀约链接。') from None
        import qrcode
        from qrcode.image.svg import SvgPathImage
        from qrcode.exceptions import DataOverflowError
        try:
            output=BytesIO()
            qrcode.make(url.strip(),image_factory=SvgPathImage,box_size=6,border=4).save(output)
        except DataOverflowError:
            raise HTTPException(422,'认证链接过长，请重新复制官方邀约链接。') from None
        return Response(output.getvalue(),media_type='image/svg+xml',headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})

    @router.post('/import')
    def import_asset(payload:dict):
        def operation():
            remote_id=payload.get('remote_asset_id')
            if not isinstance(remote_id,str) or not re.fullmatch(r'asset-[A-Za-z0-9-]{1,114}',remote_id):
                raise ValueError('请选择有效的官方素材编号。')
            person_type=payload.get('person_type','LivenessFace')
            api,config=client(person_type)
            remote=api.get_asset(remote_id)
            # Persist type before imported bindings are discovered by the directory.
            person=library().add_person(remote['group_id'],person_type=person_type)
            catalog().require_manage(person['id'])
            fingerprint=portrait_service.fingerprint(config)
            existing=store().find_portrait(remote_id,fingerprint)
            if existing:
                asset=store().get_asset(existing,private=True)
                if Path(asset['path']).is_file():
                    store().bind_portrait(existing,remote,fingerprint)
                    library().record_verified_import(person['id'],existing,remote_id)
                    return {**store().get_asset(existing),'person_type':person_type}
            root=settings_getter().storage_dir/'assets'
            root.mkdir(parents=True,exist_ok=True)
            ident=uuid.uuid4().hex
            temporary=root/(ident+'.import')
            destination=None
            try:
                portrait_service.download_image(remote,temporary)
                from PIL import Image
                with Image.open(temporary) as picture:
                    extension={'PNG':'.png','JPEG':'.jpg','WEBP':'.webp'}.get(picture.format)
                if extension is None:raise ValueError('真人参考图仅支持 PNG、JPEG 或 WebP。')
                destination=root/(ident+extension)
                temporary.replace(destination)
                name=(remote.get('name') or '已授权人物')[:100]+extension
                mime={'.png':'image/png','.jpg':'image/jpeg','.webp':'image/webp'}[extension]
                selected=store().register_portrait(ident,name,destination,destination.stat().st_size,mime,
                    hashlib.sha256(destination.read_bytes()).hexdigest(),remote,fingerprint)
                if selected!=ident:destination.unlink(missing_ok=True)
                library().record_verified_import(person['id'],selected,remote_id)
                return {**store().get_asset(selected),'person_type':person_type}
            finally:
                temporary.unlink(missing_ok=True)
        return guarded(operation)

    return router


def get_admin_router(settings_getter, local_guard):
    router=APIRouter(prefix='/api/admin/portrait-access',dependencies=[Depends(local_guard)])
    def operation(callback):
        from .shared_portraits import SharedPortraitCatalog
        try:
            cat=SharedPortraitCatalog(settings_getter())
            if not cat.admin: raise PermissionError('仅管理员可分配真人权限。')
            return callback(cat)
        except PermissionError as exc: raise HTTPException(403,str(exc)) from None
        except LookupError as exc: raise HTTPException(404,str(exc)) from None
        except ValueError as exc: raise HTTPException(422,str(exc)) from None

    @router.get('')
    def policies():
        return operation(lambda cat:{'items':[{**cat.policy(p['id']),'name':p['name']} for p in cat.people() if p['person_type']=='LivenessFace']})

    @router.put('/{person_id}')
    def save_policy(person_id:str,payload:dict):
        return operation(lambda cat:cat.set_policy(person_id,payload.get('mode'),payload.get('user_ids')))

    return router
