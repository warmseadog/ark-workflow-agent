import base64
import pytest
from app.generation_settings import GenerationConfig
from app.video_provider import VideoProvider
from app.local_preferences import DEFAULT_PROMPTS


def capture(tmp_path, monkeypatch, protocol="ark", person_video=False, prompt="自行改款，增加镜头"):
    paths=[]
    for name in ("motion.mp4","person.png","clothes.png","person-extra.png","clothes-extra.png","hair.png","scene.png","bag.png","identity.mp4"):
        p=tmp_path/name;p.write_bytes(name.encode());paths.append(p)
    calls=[]
    def request(self,method,path,**kwargs):
        if path=="/uploads/videos":return {"data":{"url":"https://example.test/motion.mp4"}}
        calls.append(kwargs);return {"id":"test-task"}
    monkeypatch.setattr(VideoProvider,"_request",request)
    monkeypatch.setattr(VideoProvider,"_poll",lambda *a:{})
    monkeypatch.setattr("app.person_video.validate_pair",lambda *a:None)
    config=GenerationConfig(mode="http",api_key="fixture",protocol=protocol,
        provider="custom" if protocol=="adapter" else protocol,
        base_url="https://ark.cn-beijing.volces.com/api/v3",model="fixture")
    options={"person_video":paths[8],"person_video_uri":"asset://asset-identity"} if person_video else {}
    VideoProvider(config).generate(paths[0],[] if person_video else [paths[1],paths[3]],
        [paths[2],paths[4]],prompt,tmp_path/"out.mp4",video_url="https://example.test/motion.mp4",
        hairstyles=[paths[5]],scenes=[paths[6]],accessories={"bag":[paths[7]]},**options)
    call=calls[0]
    text=call["data"]["prompt"] if protocol=="adapter" else call["json"]["content"][0]["text"] if protocol=="ark" else call["json"]["prompt"]
    return text,call


@pytest.mark.parametrize("protocol",["ark","toapis","adapter"])
def test_strict_contract_reaches_provider_with_actual_image_order(tmp_path,monkeypatch,protocol):
    text,call=capture(tmp_path,monkeypatch,protocol,prompt="自行改款，增加镜头\n【素材联动】\n@Image9 旧编号\n【联动结束】")
    assert "【严格参考】" in text and "禁止自行增加动作、镜头或改变动作顺序" in text
    assert "人物主参考：@Image1" in text and "服装主参考：@Image2" in text
    assert "@Image3 仅补充人物" in text and "@Image4 仅补充服装" in text
    assert "@Image5 仅控制发型" in text and "@Image6 仅控制场景" in text and "@Image7 仅控制包包" in text
    assert "参考素材在各自负责范围内优先于文字描述" in text
    assert "自行改款，增加镜头" in text and "旧编号" not in text
    assert text.rstrip().endswith("【严格参考结束】")
    if protocol=="ark":
        images=[item for item in call["json"]["content"] if item["type"]=="image_url"]
        assert [base64.b64decode(item["image_url"]["url"].split(",")[1]) for item in images]==[
            b"person.png",b"clothes.png",b"person-extra.png",b"clothes-extra.png",b"hair.png",b"scene.png",b"bag.png"]
        assert "weight" not in call["json"]


@pytest.mark.parametrize("prompt",[p[1] for p in DEFAULT_PROMPTS])
def test_video_identity_remaps_builtin_templates_and_supplement_indices(tmp_path,monkeypatch,prompt):
    text,call=capture(tmp_path,monkeypatch,person_video=True,prompt=prompt)
    assert "人物主参考：@Video2" in text and "服装主参考：@Image1" in text
    assert "@Image2 仅补充服装" in text and "@Image3 仅控制发型" in text
    user=text.split("用户要求：",1)[1].split("素材分工",1)[0]
    assert "@Video2" in user and "@Image2" not in user
    assert "不采用视频2的动作、服装、背景、声音或台词" in text
    content=call["json"]["content"]
    videos=[item for item in content if item["type"]=="video_url"]
    assert [item["video_url"]["url"] for item in videos]==["https://example.test/motion.mp4","asset://asset-identity"]


@pytest.mark.parametrize("prompt",[p[1] for p in DEFAULT_PROMPTS])
def test_template_mapping_roundtrip_keeps_original_and_custom_text(prompt):
    from app.reference_prompt import normalize_reference_mentions
    video=normalize_reference_mentions(prompt,True)
    assert normalize_reference_mentions(video,True)==video
    assert normalize_reference_mentions(video,False)==prompt
    custom="@Image3 的背景，@Image10 的灯光，自定义要求"
    assert normalize_reference_mentions(custom,True)==custom
