"""Validate official asset identity at enqueue and again immediately before generation."""
from dataclasses import asdict
from pathlib import Path
from . import portrait_service

OFFICIAL_BASE='https://ark.cn-beijing.volces.com/api/v3'


def prepare(settings, store, draft, generation):
    bindings={ident:store.portrait_binding(ident) for ident in draft['face_asset_ids']}
    bindings={ident:value for ident,value in bindings.items() if value}
    if not bindings:return None
    if generation.protocol!='ark' or generation.base_url.rstrip('/')!=OFFICIAL_BASE:
        raise ValueError('已授权真人素材仅支持火山官方模型接口，请检查模型设置。')
    config=portrait_service.load_config(settings)
    if not config.ready:raise ValueError(config.problem())
    fingerprint=portrait_service.fingerprint(config)
    for binding in bindings.values():
        if binding['fingerprint']!=fingerprint:
            raise ValueError('真人素材的账号或项目配置已变更，请重新同步并选择已授权人物。')
    snapshot={'config':asdict(config),'bindings':{ident:{k:binding[k] for k in ('remote_asset_id','group_id','project')} for ident,binding in bindings.items()}}
    verify(snapshot,store)
    return snapshot


def verify(snapshot, store):
    config=portrait_service.PortraitConfig(**snapshot['config'])
    api=portrait_service.ArkPortraitClient(config)
    image_uris={}
    for ident,binding in snapshot['bindings'].items():
        remote=api.get_asset(binding['remote_asset_id'])
        if any(remote[k]!=binding[k] for k in ('remote_asset_id','group_id','project')):
            raise ValueError('真人素材授权信息发生变化，请重新同步。')
        asset=store.get_asset(ident,private=True)
        path=Path(asset['path'])
        if not path.is_file():raise ValueError('真人素材本地预览文件丢失，请重新导入。')
        image_uris[str(path)]='asset://'+binding['remote_asset_id']
    return image_uris
