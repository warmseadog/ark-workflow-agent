"""Frozen variation intent and durable one-video camera planning."""
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
from .reference_roles import OPTIONAL_KINDS, ACCESSORY_LABELS
from .variation_settings import load_config, VariationConfig
from .variation_llm import RECIPES, plan_variation

BLOCKED_MESSAGE = '拍摄灵感与人物、穿搭或场景等固定条件冲突，请调整灵感后重新生成。'


def normalize_request(value):
    if not isinstance(value,dict) or set(value)!={'inspiration'}:raise ValueError('换拍法请求格式不正确。')
    text=value['inspiration']
    if not isinstance(text,str) or len(text)>2000 or '\x00' in text:raise ValueError('拍摄灵感最多 2000 字。')
    return {'inspiration':text.strip()}


def preflight(settings,store,draft,request):
    config=load_config(settings)
    if config.problem():raise ValueError(config.problem())
    # Hash visual inputs, including content hashes, independent of draft IDs.
    fields=['source_asset_id','source_clip','person_reference_mode','person_video_asset_id','face_asset_ids','clothing_asset_ids','prompt','prompt_rule_version']
    fields += [kind+suffix for kind in OPTIONAL_KINDS for suffix in ('_asset_ids','_enabled')]+['scene_description']
    values={key:draft.get(key) for key in fields}
    active=[draft['source_asset_id'],*(draft.get('face_asset_ids',[]) if draft.get('person_reference_mode')!='video' else [draft['person_video_asset_id']]),*draft['clothing_asset_ids']]
    active += [x for k in OPTIONAL_KINDS if draft.get(k+'_enabled') for x in draft.get(k+'_asset_ids',[])]
    values['hashes']=[store.get_asset(x)['sha256'] for x in active]
    return {**request,'config':asdict(config),'group_key':hashlib.sha256(json.dumps(values,sort_keys=True,ensure_ascii=False).encode()).hexdigest(),
            'skill_version':config.skill_version}


def source_frames(video,directory):
    import cv2
    from .person_video import probe
    info=probe(video);directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    capture=cv2.VideoCapture(str(video));frames=[]
    try:
        count=max(1,round(info['duration']*info['fps']))
        for i in range(4):
            index=round((count-1)*i/3);capture.set(cv2.CAP_PROP_POS_FRAMES,index)
            ok,frame=capture.read()
            if not ok:raise ValueError('无法读取换拍法参考视频。')
            h,w=frame.shape[:2]
            if max(h,w)>960:frame=cv2.resize(frame,(round(w*960/max(h,w)),round(h*960/max(h,w))))
            ok,encoded=cv2.imencode('.jpg',frame)
            if not ok:raise ValueError('无法准备换拍法参考帧。')
            path=directory/f'frame-{i}.jpg';path.write_bytes(encoded.tobytes())
            frames.append({'path':path,'timestamp':index/info['fps']})
    finally:capture.release()
    return info['duration'],frames


def prepare(settings,store,run,video,faces,clothes,extra,config):
    ident=run['id'];state=store.get_variation(ident)
    if state.get('plan'):
        if state['plan']['blocked']:raise ValueError(BLOCKED_MESSAGE)
        return state['plan']
    store.update_run(ident,stage='variation_planning',message='正在结合素材与灵感设计新拍法',progress=48)
    frozen=run['private']['variation'];llm=VariationConfig(**frozen['config'])
    duration,frames=source_frames(video,Path(video).parent/'variation-frames')
    from .model_catalog import capabilities
    if not capabilities(config.model,config.protocol)['follow_source'] and config.duration>0:duration=config.duration
    references=[('人物参考',p) for p in faces]+[('穿搭参考',p) for p in clothes]
    for key,label in [('hairstyles','独立发型'),('scenes','独立场景')]:references += [(label,p) for p in extra.get(key,[])]
    for kind,paths in extra.get('accessories',{}).items():references += [(ACCESSORY_LABELS[kind],p) for p in paths]
    if extra.get('person_video'):
        _,person_frames=source_frames(extra['person_video'],Path(video).parent/'variation-person-frames')
        references += [('人物身份参考（不提供动作和背景）',f['path']) for f in person_frames[:2]]
    context={'duration':duration,'inspiration':frozen['inspiration'],'default_template':llm.active_template,
        'original_prompt':run['snapshot']['prompt'],'scene_description':extra.get('scene_description',''),
        'suggested_recipe':state['recipe'],'recent_recipes':state['recent_recipes']}
    def diagnostic(value):
        # Private prompt-bearing data must not enter the public variation state.
        import os,tempfile
        directory=settings.storage_dir/'work'/ident
        directory.mkdir(parents=True,exist_ok=True)
        fd,name=tempfile.mkstemp(dir=directory,prefix='.plan-',suffix='.json')
        path=Path(name)
        try:
            with os.fdopen(fd,'w',encoding='utf-8') as stream:json.dump(value,stream,ensure_ascii=False)
            path.replace(directory/'variation-diagnostic.json')
        finally:path.unlink(missing_ok=True)
    plan=plan_variation(llm,context=context,frames=frames,references=references,on_diagnostic=diagnostic)
    if llm.prompt_mode == 'user_priority':
        # This provenance comes from the frozen task, never from model output.
        plan={**plan,'prompt_mode':llm.prompt_mode,'user_inspiration':frozen['inspiration']}
    store.update_variation(ident,plan=plan,llm_model=llm.model,skill_version=frozen['skill_version'])
    # Planner explanations may quote private prompts. Keep them in the plan,
    # never copy them into public task error/message fields.
    if plan['blocked']:raise ValueError(BLOCKED_MESSAGE)
    return plan
