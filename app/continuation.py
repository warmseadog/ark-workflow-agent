"""Opt-in duration planning and durable second-stage video continuation."""
from dataclasses import asdict
from decimal import Decimal
import math


def normalize_target(value):
    if value is None:
        return None
    if type(value) not in (int,float) or not math.isfinite(value) or not 2 <= value <= 30:
        raise ValueError('目标时长需为 2–30 秒。')
    return value


def should_extend(source_duration, target_duration):
    return Decimal(str(target_duration)) - Decimal(str(source_duration)) >= Decimal('1')


def preflight(settings, store, draft, config):
    """No network or paid preparation: freeze configuration before queueing."""
    target = normalize_target(draft.get('target_duration'))
    if target is None:
        return None
    from .model_catalog import capabilities
    from .source_clip import validate_source
    if not capabilities(config.model,config.protocol)['follow_source']:
        raise ValueError('智能续写目前仅支持 Seedance 2.5 视频编辑模型。')
    clip = draft.get('source_clip')
    if clip and clip.get('retime'):
        raise ValueError('目标时长与放慢设置不能同时使用，请重新选择时长。')
    source = store.get_asset(draft['source_asset_id'],private=True)
    seconds = validate_source(source['path'],clip,max_seconds=30)['duration']
    if target < seconds - .02:
        raise ValueError('缩短时长需同步选择对应的原视频片段。')
    if not should_extend(seconds,target):
        return None
    if seconds < 4:
        raise ValueError('智能续写的基础片至少需要 4 秒，请选择更长的原片或片段。')
    from .continuation_settings import load_config
    llm = load_config(settings)
    problem = llm.problem()
    if problem:
        raise ValueError(problem)
    if config.mode != 'http':
        raise ValueError('智能续写需要真实视频模型，请在后台关闭演示模式并配置视频接口。')
    return {'config':asdict(llm),'source_duration':seconds,'target_duration':target,
            'skill_version':llm.skill_version}


def publish_base(path, settings, config, storage):
    from .storage_settings import upload_redacted_video
    from .reference_media import publish_video
    return (upload_redacted_video(path,settings,storage) if storage.enabled
            else publish_video(path,settings.storage_dir,config.public_base_url))


def execute(settings, store, run, base, output, config, storage):
    from dataclasses import replace
    from .continuation_settings import ContinuationConfig
    from .continuation_llm import plan_continuation
    from .continuation_media import ending_frames, finalize
    from .person_video import probe
    from .production_worker import authorize_run_inputs
    from .reference_roles import snapshot_content_roles
    from .video_provider import VideoProvider
    ident=run['id']
    store.set_phase(ident, 'other')
    frozen=run['private']['continuation']
    state=store.get_continuation(ident)
    store.update_run(ident,stage='continuation_planning',message='正在分析基础片结尾并扩写后续剧情',progress=88)
    # The generated file must never become a shortcut around source revocation.
    authorize_run_inputs(settings,store,run['snapshot'])
    actual=probe(base)['duration']
    target=frozen['target_duration']
    if actual >= target:
        raise ValueError('基础片时长已达到目标，无法建立续写区间，请核对服务商输出。')
    plan=state.get('plan')
    if not plan:
        frames=ending_frames(base,base.parent/'continuation-frames')
        # Keep the measured timeline even when the planner response is rejected.
        store.update_continuation(ident,source_duration=actual,target_duration=target,
            llm_model=frozen['config']['model'],skill_version=frozen['skill_version'],
            frame_timestamps=[f['timestamp'] for f in frames])
        store.set_phase(ident, 'model')
        plan=plan_continuation(ContinuationConfig(**frozen['config']),
            original_prompt=run['snapshot']['prompt'],frames=frames,source_duration=actual,
            target_duration=target,reference_roles=snapshot_content_roles(run['snapshot']))
        store.set_phase(ident, 'other')
        state=store.update_continuation(ident,plan=plan)
    url=None
    remote_id,remote_url=state.get('provider_task_id'),state.get('result_url')
    if not remote_id and not remote_url:
        store.update_run(ident,stage='continuation_upload',message='正在上传基础片以续写后续剧情',progress=89)
        if storage.enabled:
            store.set_phase(ident, 'upload')
        url=publish_base(base,settings,config,storage)
        store.set_phase(ident, 'other')
        authorize_run_inputs(settings,store,run['snapshot'])
        store.update_run(ident,stage='continuation_submitting',message='正在提交剧情续写任务',progress=90)
    def submitted(task_id):
        store.update_continuation(ident,provider_task_id=task_id)
        store.update_run(ident,stage='continuation_generating',message='正在生成新增剧情',progress=92)
        store.set_phase(ident, 'model')
    def result(result_url):
        store.set_phase(ident, 'other')
        store.update_continuation(ident,result_url=result_url)
        store.update_run(ident,stage='continuation_downloading',message='正在下载续写结果',progress=96)
    extension=base.parent/'extended.mp4'
    client=VideoProvider(replace(config,duration=math.ceil(target),ratio='adaptive'),settings.seedance_poll_seconds,
        lambda msg,pct:store.update_run(ident,message='续写：'+msg,progress=90+round(pct/20)))
    client.phase_callback = lambda phase: store.set_phase(ident, phase)
    if remote_id and not remote_url:
        store.set_phase(ident, 'model')
    try:
        client.extend(base,plan['continuation_prompt'],extension,video_url=url,
            resume_task_id=remote_id,resume_result_url=remote_url,on_submitted=submitted,on_result=result)
    except Exception as error:
        if getattr(error,'terminal_failure',False):
            store.update_continuation(ident,terminal_failure=True)
        raise
    store.set_phase(ident, 'other')
    store.update_run(ident,stage='continuation_finalizing',message='正在校验续写成片时长',progress=98)
    authorize_run_inputs(settings,store,run['snapshot'])
    finalize(extension,output,target)
    store.update_continuation(ident,complete=True)
