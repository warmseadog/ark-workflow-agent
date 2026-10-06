import json
from pathlib import Path
import pytest
import requests
from app.variation_llm import plan_variation, validate_plan
from app.variation_settings import VariationConfig
from tests.test_variation import example
from tests.media_fixtures import image_bytes


def test_multimodal_request_separates_inspiration_and_template(tmp_path,monkeypatch):
    path=tmp_path/'参考.png';path.write_bytes(image_bytes())
    calls=[]
    def post(url,**kw):
        calls.append((url,kw));r=requests.Response();r.status_code=200
        r._content=json.dumps({'choices':[{'message':{'content':json.dumps(example())}}]}).encode();return r
    monkeypatch.setattr('app.variation_llm.requests.post',post)
    c=VariationConfig(api_key='never-expose',model='chosen-vision-model')
    result=plan_variation(c,context={'duration':8,'inspiration':'先拍衣服','default_template':c.template},frames=[{'path':path,'timestamp':0}],references=[('服装',path)])
    body=calls[0][1]['json']
    assert result==example() and body['model']=='chosen-vision-model'
    assert len([x for x in body['messages'][1]['content'] if x['type']=='image_url'])==2
    assert calls[0][1]['allow_redirects'] is False
    assert 'never-expose' not in json.dumps(body)


def test_transport_failure_does_not_expose_secret(monkeypatch):
    monkeypatch.setattr('app.variation_llm.requests.post',lambda *a,**kw:(_ for _ in ()).throw(requests.Timeout('secret-private-key')))
    with pytest.raises(ValueError) as e:plan_variation(VariationConfig(api_key='secret-private-key'),context={'duration':8},frames=[],references=[])
    assert 'secret-private-key' not in str(e.value)


def test_blocked_inspiration_and_invalid_enum_shape():
    p=example();p.update(blocked=True,shots=[],conflicts=['换场景与固定条件冲突'])
    assert validate_plan(p,8)['blocked']
    p=example();p['shots'][0]['move']={'untrusted':'value'}
    with pytest.raises(ValueError):validate_plan(p,8)
