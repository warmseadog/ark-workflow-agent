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


def get_router(settings_getter, local_guard):
    router=APIRouter(prefix='/api/portrait',dependencies=[Depends(local_guard)])
    def store(): return ProductionStore(settings_getter().storage_dir)
    def client():
        config=portrait_service.load_config(settings_getter())
        if not config.ready: raise HTTPException(409,config.problem())
        return portrait_service.ArkPortraitClient(config),config
    def guarded(operation):
        try: return operation()
        except HTTPException: raise
        except (ValueError,LookupError) as exc: raise HTTPException(422,str(exc)) from None

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
    def assets():
        def operation():
            api,_=client()
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
            api,config=client()
            remote=api.get_asset(remote_id)
            fingerprint=portrait_service.fingerprint(config)
            existing=store().find_portrait(remote_id,fingerprint)
            if existing:
                asset=store().get_asset(existing,private=True)
                if Path(asset['path']).is_file():
                    store().bind_portrait(existing,remote,fingerprint)
                    return store().get_asset(existing)
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
                return store().get_asset(selected)
            finally:
                temporary.unlink(missing_ok=True)
        return guarded(operation)

    return router
