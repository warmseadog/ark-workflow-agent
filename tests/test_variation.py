from dataclasses import replace
import pytest
from app.config import settings
from app import variation_settings as config
from app.variation_llm import validate_plan
from app.reference_prompt import compose_exclusive_prompt


def example():
    return {'summary':'缓慢推进展示穿搭','accepted_requests':[], 'conflicts':[], 'blocked':False,
            'shots':[{'start':0,'end':8,'framing':'medium','angle':'eye_level','move':'dolly_in',
                      'action':'保持原有展示动作，自然站立'}]}


def test_private_config_independent_and_key_bound_to_endpoint(tmp_path):
    s=replace(settings,storage_dir=tmp_path,config_root=None)
    first=config.save_config(s,{'api_key':'secret-one','template':'同素材换拍法','model':'chosen-model'})
    assert first.model=='chosen-model' and first.template=='同素材换拍法'
    assert 'secret-one' not in repr(first) and 'api_key' not in first.public()
    assert config.save_config(s,{'api_key':''}).api_key=='secret-one'
    assert config.save_config(s,{'base_url':'https://other.example/v1'}).api_key==''
    assert not (tmp_path/'private'/'continuation-settings.json').exists()


@pytest.mark.parametrize('change',[{'template':''},{'enabled':'true'},{'timeout_seconds':False},{'skill':''}])
def test_invalid_config_atomic(tmp_path,change):
    s=replace(settings,storage_dir=tmp_path,config_root=None)
    before=config.load_config(s)
    with pytest.raises(ValueError):config.save_config(s,change)
    assert config.load_config(s)==before


def test_plan_timing_and_locked_materials():
    assert validate_plan(example(),8)['shots'][0]['move']=='dolly_in'
    for field,value in [('end',7),('move','teleport'),('action','换成红裙'),('action','参考 @Image99')]:
        p=example();p['shots'][0][field]=value
        with pytest.raises(ValueError):validate_plan(p,8)


def test_variation_replaces_only_camera_lock_and_preserves_roles():
    p=example()
    text=compose_exclusive_prompt('保持原运镜', ['人物','衣服','发型','场景','耳环'], variation_plan=p)
    assert '缓慢推进' in text and '@Image5' in text and '耳环' in text
    assert '严格遵循动作顺序' not in text and '保持原运镜' not in text
    assert '光照' in text and '视觉风格' in text
    normal=compose_exclusive_prompt('original',['人物','衣服'])
    assert '严格遵循动作顺序' in normal
