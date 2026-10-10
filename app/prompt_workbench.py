"""Admin-only inventory, optimistic edits and an offline production-composer preview."""
from dataclasses import asdict, replace
import difflib
import hashlib
import json
from fastapi import HTTPException
from . import prompt_config, local_preferences, variation_settings, continuation_settings
from .reference_roles import ACCESSORY_LABELS

VARIATION_FIELDS={'template':'严格参考 · 默认创作正文','skill':'严格参考 · 创作 Skill',
    'motion_template':'丰富动作 · 默认创作正文','motion_skill':'丰富动作 · 创作 Skill',
    'user_priority_template':'用户意图优先 · 默认创作正文','user_priority_skill':'用户意图优先 · 创作 Skill'}


def overview(settings):
    overrides=prompt_config.load(settings)
    items=[{**x,'content':overrides.get(x['id'],x['default']),'customized':x['id'] in overrides,'source':'override'} for x in prompt_config.catalog()]
    config=variation_settings.load_config(settings);defaults=variation_settings.VariationConfig()
    for field,label in VARIATION_FIELDS.items():
        content=getattr(config,field);default=getattr(defaults,field)
        items.append(dict(id='variation.'+field,content=content,default=default,group='拍摄灵感',label=label,
            variables={},customized=content!=default,source='variation',condition='对应拍摄灵感模式的新任务；与“拍摄灵感配置”共用同一保存值。'))
    config_cont=continuation_settings.load_config(settings)
    items.append(dict(id='continuation.skill',content=config_cont.skill,default=continuation_settings.DEFAULT_SKILL,
        group='续写',label='视频续写 · 创作 Skill',variables={},customized=config_cont.skill!=continuation_settings.DEFAULT_SKILL,
        source='continuation',condition='生成目标时长超过参考时长、触发续写规划时使用。'))
    templates=local_preferences.list_shared_templates(settings)
    from .prompt_templates import EXCLUSIVE_PROMPT,yoyo_prompt,SCARF_HAND_CONSTRAINT
    builtins={'default-exclusive-v2':EXCLUSIVE_PROMPT+'\n\n'+SCARF_HAND_CONSTRAINT,
              'default-yoyo-v3':yoyo_prompt()+'\n\n'+SCARF_HAND_CONSTRAINT}
    for i,(_,value) in enumerate(local_preferences.DEFAULT_PROMPTS):builtins['default-'+str(i)]=value+'\n\n'+SCARF_HAND_CONSTRAINT
    for item in templates:
        default=builtins.get(item['id'],item['content'])
        items.append(dict(id='template.'+item['id'],content=item['content'],default=default,group='默认正文与共享模板',
            label=item['name']+(' · 当前默认' if item['is_default'] else ''),variables={},customized=item['content']!=default,
            source='template',condition='新草稿选择本模板时复制正文；已有草稿和已提交任务保留自身正文。'))
    revision=hashlib.sha256(json.dumps(items,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    return {'items':items,'templates':templates,'revision':revision,'prompt_mode':config.prompt_mode,
            'accessories':ACCESSORY_LABELS,'scope':'新提交任务冻结系统指令和联动规则；已有任务保留原配置。共享正文修改影响之后新建的草稿。'}


def save(settings,payload):
    if not isinstance(payload,dict) or set(payload)!={'revision','changes'} or not isinstance(payload['changes'],dict) or len(payload['changes'])!=1:
        raise ValueError('请一次保存一个提示词段落，并携带读取时的版本。')
    with prompt_config.lock:
        current=overview(settings)
        if payload['revision']!=current['revision']:raise HTTPException(409,'配置已被其他页面修改，请重新加载后再保存；当前编辑内容仍保留。')
        key,value=next(iter(payload['changes'].items()))
        item=next((x for x in current['items'] if x['id']==key),None)
        if item is None:raise ValueError('提示词条目不存在。')
        reset=value is None
        if reset:value=item['default']
        if not isinstance(value,str) or not value.strip() or '\x00' in value or len(value)>30000:raise ValueError('请输入有效的提示词正文。')
        if item['source']=='override':
            values=prompt_config.load(settings)
            if reset:values.pop(key,None)
            else:values[key]=value
            prompt_config.save(settings,values)
        elif item['source']=='variation':variation_settings.save_config(settings,{key.split('.',1)[1]:value})
        elif item['source']=='continuation':continuation_settings.save_config(settings,{'skill':value})
        else:
            template=next(x for x in current['templates'] if 'template.'+x['id']==key)
            local_preferences.save_shared_template(settings,template['name'],value,template['id'])
        return overview(settings)


def preview(settings,payload):
    allowed={'roles','prompt','rule_version','person_video','scene_description','follow_source','mode','inspiration','overrides'}
    if not isinstance(payload,dict) or set(payload)-allowed:raise ValueError('预览参数不正确。')
    roles=payload.get('roles',['人物','衣服']);person=payload.get('person_video',False)
    primary={'人物','衣服','发型','场景',*ACCESSORY_LABELS.values()}
    if not isinstance(roles,list) or len(roles)>70 or any(not isinstance(x,str) or x not in primary|{'人物补充','衣服补充'} for x in roles):raise ValueError('素材类别不正确。')
    if any(roles.count(x)>1 for x in primary) or (person and any(x.startswith('人物') for x in roles)):raise ValueError('主素材重复或人物模式冲突。')
    follow=payload.get('follow_source',False)
    if type(person)is not bool or type(follow)is not bool:raise ValueError('参考模式不正确。')
    prompt=payload.get('prompt','');scene=payload.get('scene_description','');idea=payload.get('inspiration','示例：自然展示服装，保持正常速度。')
    for value,limit in [(prompt,10000),(scene,2000),(idea,2000)]:
        if not isinstance(value,str) or len(value)>limit or '\x00' in value:raise ValueError('预览文字长度或格式不正确。')
    mode=payload.get('mode','normal')
    if mode not in ('normal','strict','motion','user_priority','continuation','inspiration'):raise ValueError('预览模式不正确。')
    rule=payload.get('rule_version','yoyo-v3')
    data=overview(settings);definitions={x['id']:x for x in data['items']}
    changes=payload.get('overrides',{})
    if not isinstance(changes,dict) or set(changes)-set(definitions):raise ValueError('未保存修改包含未知条目。')
    for key,value in changes.items():
        if not isinstance(value,str) or not value.strip() or len(value)>30000 or '\x00' in value:raise ValueError('未保存正文不正确。')
    custom={k:v for k,v in changes.items() if definitions[k]['source']=='override'}
    prompt_config.validate(custom)
    values={**prompt_config.load(settings),**custom}
    def content(key):return changes.get(key,definitions[key]['content'])
    if mode in ('continuation','inspiration'):
        with prompt_config.scope(values):
            if mode=='continuation':
                from .continuation_llm import system_prompt
                from .reference_prompt import compose_extension_prompt
                system=system_prompt(replace(continuation_settings.load_config(settings),skill=content('continuation.skill')),4,8)
                final=compose_extension_prompt(8,'示例：衔接结尾姿态，自然展示服装。')
                planner_input={'original_prompt':prompt,'source_duration':4,'target_duration':8,'reference_roles':'来自第一阶段任务的素材分工'}
            else:
                active=data['prompt_mode'];system=content('inspiration.'+active)
                final='请围绕以下用户灵感完善拍摄安排：\n'+idea if idea else '请生成一条人物穿搭展示的拍摄灵感。'
                planner_input={'说明':'使用“拍摄灵感配置”中当前选中的版本','当前版本':active}
        return {'system_prompt':system,'final_prompt':final,'planner_input':planner_input,'base_prompt':final,'changes':[],
                'applied':[],'bindings':[],'simulation':True,'message':'离线示例：续写示例由 4 秒延长到 8 秒；真实时长、关键帧和规划结果由实际任务提供。' if mode=='continuation' else '展示实际系统指令及用户消息，不调用模型。'}
    from .reference_prompt import compose_video_prompt
    plan=None;system='普通视频生成直接发送下面的合成正文，不单独发送 system 消息。'
    if mode!='normal':
        from .variation_llm import system_prompt
        config=replace(variation_settings.load_config(settings),prompt_mode=mode,
            **{field:content('variation.'+field) for field in VARIATION_FIELDS})
        with prompt_config.scope(values):system=system_prompt(config)
        plan={'summary':'示例摄影方案（非模型生成）','shots':[{'start':0,'end':4,'framing':'medium','angle':'eye_level','move':'static','action':'自然展示服装'}]}
        if mode in ('motion','user_priority'):plan.update(prompt_mode=mode,user_inspiration=idea or '自然展示服装')
    trace=[]
    kwargs=dict(person_video=person,scene_description=scene,follow_source=follow,prompt_rule_version=rule,variation_plan=plan)
    with prompt_config.scope(values,trace):
        final=compose_video_prompt(prompt,roles,**kwargs)
    with prompt_config.scope(values):
        base_roles=[r for r in roles if r in ('人物','衣服','人物补充','衣服补充')]
        base=compose_video_prompt(prompt,base_roles,**{**kwargs,'scene_description':''})
    # Filter transient legacy setup and inactive accessory strings out of the trace.
    seen=set();applied=[]
    for part in trace:
        if part['content'] in final and part['id'] not in seen:
            seen.add(part['id']);applied.append({**part,'label':definitions[part['id']]['label']})
    difference=[{'kind':{'- ':'removed','+ ':'added','  ':'same'}[line[:2]],'text':line[2:]}
        for line in difflib.ndiff(base.splitlines(),final.splitlines()) if line[:2] in ('- ','+ ')]
    return {'final_prompt':final,'base_prompt':base,'changes':difference,'applied':applied,'system_prompt':system,
            'planner_input':{'duration':4,'inspiration':idea,'default_template':config.active_template,'original_prompt':prompt,
                'scene_description':scene,'suggested_recipe':'中景平视，固定构图','recent_recipes':[]} if mode!='normal' else {},
            'bindings':[{'token':'@Video1','role':'动作参考视频'}]+([{'token':'@Video2','role':'人物参考视频'}] if person else [])+
                       [{'token':'@Image'+str(i),'role':r} for i,r in enumerate(roles,1)],
            'simulation':mode!='normal','message':'离线组合预览，不调用模型。拍摄方案为示例，实际方案取决于规划模型返回。' if mode!='normal' else '与实际视频提交共用同一组合函数；预览不调用模型。'}
