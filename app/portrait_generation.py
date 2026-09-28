"""Validate official asset identity at enqueue and again immediately before generation."""
from dataclasses import asdict
from pathlib import Path
from . import portrait_service
from .person_video import person_ids

class PortraitPending(Exception):
    pass


OFFICIAL_BASE='https://ark.cn-beijing.volces.com/api/v3'


def prepare(settings, store, draft, generation):
    if draft.get('person_id'):
        from .portrait_library import PortraitLibrary
        lib=PortraitLibrary(settings)
        person=lib.person(draft['person_id'],private=True)
        if generation.protocol!='ark' or generation.base_url.rstrip('/')!=OFFICIAL_BASE:
            raise ValueError('官方人物素材仅支持火山官方模型接口。')
        uploads={ident:lib.enqueue(person['id'],ident)['id'] for ident in person_ids(draft)}
        return {'config':asdict(lib.config),'person_id':person['id'],'group_id':person['group_id'],
                'person_type':person['person_type'],'uploads':uploads,'bindings':{}}
    bindings={ident:store.portrait_binding(ident) for ident in person_ids(draft)}
    bindings={ident:value for ident,value in bindings.items() if value}
    if not bindings:return None
    if generation.protocol!='ark' or generation.base_url.rstrip('/')!=OFFICIAL_BASE:
        raise ValueError('官方人物素材仅支持火山官方模型接口，请检查模型设置。')
    config=portrait_service.load_config(settings)
    if not config.ready:raise ValueError(config.problem())
    fingerprint=portrait_service.fingerprint(config)
    for binding in bindings.values():
        if binding['fingerprint']!=fingerprint:
            raise ValueError('人物素材的账号或项目配置已变更，请重新同步并选择已授权人物。')
    from .portrait_library import PortraitLibrary
    lib=PortraitLibrary(settings)
    for binding in bindings.values():
        with store.connection() as db:
            person=db.execute('SELECT person_type FROM portrait_people WHERE account=? AND group_id=?',(lib.account,binding['group_id'])).fetchone()
        binding['person_type']=person['person_type'] if person else 'LivenessFace'
    snapshot={'config':asdict(config),'bindings':{ident:{k:binding[k] for k in ('remote_asset_id','group_id','project','person_type')} for ident,binding in bindings.items()}}
    verify(snapshot,store)
    return snapshot


def verify(snapshot, store):
    config=portrait_service.PortraitConfig(**snapshot['config'])
    from types import SimpleNamespace
    current=portrait_service.load_config(SimpleNamespace(storage_dir=store.storage))
    if portrait_service.fingerprint(config)!=portrait_service.fingerprint(current):
        raise ValueError('人物素材账号或项目已变更，请重新选择人物和照片。')
    def client(person_type='LivenessFace', asset_type='Image'):
        if asset_type=='Video':return portrait_service.ArkPortraitClient(config,person_type=person_type,asset_type=asset_type)
        return (portrait_service.ArkPortraitClient(config) if person_type=='LivenessFace'
                else portrait_service.ArkPortraitClient(config,person_type=person_type))
    api=client(snapshot.get('person_type','LivenessFace'))
    image_uris={}
    if snapshot.get('uploads'):
        from types import SimpleNamespace
        from .portrait_library import PortraitLibrary, validate_photo
        import time
        lib=PortraitLibrary(SimpleNamespace(storage_dir=store.storage))
        if portrait_service.fingerprint(config)!=lib.account:
            raise ValueError('人物账号或项目已变更，请重新选择人物和照片。')
        person=lib.person(snapshot['person_id'],private=True)
        if person['person_type']!=snapshot.get('person_type','LivenessFace') or person['group_id']!=snapshot['group_id']:
            raise ValueError('任务人物类型或素材组已变化，请重新选择。')
        waiting=False
        for ident,job_id in snapshot['uploads'].items():
            photo=lib.get_photo(job_id,private=True)
            if photo['person_id']!=snapshot['person_id']:
                raise ValueError('任务照片与所选人物不匹配。')
            if photo['status']=='failed': raise ValueError(photo['message'])
            if photo['status']!='active':
                if photo['status']=='uncertain': raise ValueError('照片提交结果待确认，请在人物照片处查看状态后重新提交。')
                if time.time()-photo['created']>1800:
                    raise ValueError('官方照片校验仍未完成，请待照片可用后重新提交视频。')
                waiting=True
        if waiting: raise PortraitPending('等待人物素材校验，可继续准备其他视频')
        for ident,job_id in snapshot['uploads'].items():
            photo=lib.get_photo(job_id,private=True)
            asset=store.get_asset(ident,private=True)
            path=validate_photo(SimpleNamespace(storage_dir=store.storage),asset)
            if asset['sha256']!=photo['sha256']: raise ValueError('照片内容与校验记录不一致。')
            media_api=client(person['person_type'],'Video' if asset['kind']=='person_video' else 'Image')
            remote=media_api.get_asset(photo['remote_id'])
            if remote['group_id']!=snapshot['group_id']: raise ValueError('照片授权人物发生变化。')
            image_uris[str(path)]='asset://'+photo['remote_id']
    for ident,binding in snapshot['bindings'].items():
        asset=store.get_asset(ident,private=True)
        remote=client(binding.get('person_type','LivenessFace'),'Video' if asset['kind']=='person_video' else 'Image').get_asset(binding['remote_asset_id'])
        if any(remote[k]!=binding[k] for k in ('remote_asset_id','group_id','project')):
            raise ValueError('真人素材授权信息发生变化，请重新同步。')
        asset=store.get_asset(ident,private=True)
        path=Path(asset['path'])
        if not path.is_file():raise ValueError('真人素材本地预览文件丢失，请重新导入。')
        image_uris[str(path)]='asset://'+binding['remote_asset_id']
    return image_uris
