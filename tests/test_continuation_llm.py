import json
from dataclasses import replace

import pytest
import requests


def valid_plan():
    return {'ending_state': '人物刚转向镜头。', 'invariants': ['相同人物、服装和场景'],
            'beats': [{'start': 5.0, 'end': 7.0, 'action': '自然迈步靠近镜头。'},
                      {'start': 7.0, 'end': 8.0, 'action': '轻转肩膀展示衣服。'}],
            'continuation_prompt': '从参考结尾自然接续，人物保持正常速度走近镜头并轻转肩膀。'}


@pytest.fixture
def invoke(tmp_path, monkeypatch):
    from app.continuation_settings import ContinuationConfig
    from app.continuation_llm import plan_continuation
    image = tmp_path / 'tail.jpg'
    image.write_bytes(b'\xff\xd8\xfftest-image')
    config = ContinuationConfig(api_key='secret-test-key')
    captured = []
    def run(plan=None, fail=None, **overrides):
        def post(url, **kwargs):
            captured.append((url, kwargs))
            if fail:
                raise fail
            response = requests.Response()
            response.status_code = 200
            response._content = json.dumps({'choices': [{'message': {'content': json.dumps(valid_plan() if plan is None else plan)}}]}).encode()
            return response
        monkeypatch.setattr('app.continuation_llm.requests.post', post)
        args = dict(original_prompt='保留这段原始描述：不要改写我。', frames=[{'timestamp': 4.9, 'path': image}],
                    source_duration=5.0, target_duration=8.0, reference_roles={'person': '@Image1'})
        args.update(overrides)
        return plan_continuation(config, **args)
    return run, captured


def test_multimodal_request_preserves_prompt_as_data_and_returns_valid_plan(invoke):
    run, captured = invoke
    result = run()
    assert result == valid_plan()
    url, request = captured[0]
    assert url.endswith('/chat/completions')
    payload = request['json']
    content = payload['messages'][1]['content']
    data = json.loads(content[0]['text'])
    assert data['original_prompt'] == '保留这段原始描述：不要改写我。'
    assert data['source_duration'] == 5 and data['target_duration'] == 8
    assert any(item.get('image_url', {}).get('url', '').startswith('data:image/jpeg;base64,') for item in content)
    assert request['allow_redirects'] is False and request['timeout'] == 90
    assert payload['thinking'] == {'type': 'disabled'}


@pytest.mark.parametrize('change', [
    {'ending_state': ''}, {'invariants': []}, {'continuation_prompt': 'x' * 16001},
    {'beats': [{'start': 0, 'end': 8, 'action': 'rewind'}]},
    {'beats': [{'start': 5, 'end': 7, 'action': 'a'}]},
    {'beats': [{'start': 5, 'end': 7, 'action': 'a'}, {'start': 6, 'end': 8, 'action': 'b'}]},
    {'beats': [{'start': 5, 'end': 6, 'action': 'a'}, {'start': 7, 'end': 8, 'action': 'b'}]},
    {'beats': [{'start': 5, 'end': float('nan'), 'action': 'a'}]},
])
def test_invalid_model_plans_rejected(invoke, change):
    run, _ = invoke
    with pytest.raises(ValueError):
        run({**valid_plan(), **change})


@pytest.mark.parametrize('prompt', ['保持 @Image1 的脸和 @Image2 的衣服。','沿 @Video2 动作继续。','采用图片1的发型。','用参考视频2的镜头。'])
def test_plan_cannot_reference_materials_absent_from_second_stage(invoke,prompt):
    run,_=invoke
    with pytest.raises(ValueError):run({**valid_plan(),'continuation_prompt':prompt})


def test_network_failure_sanitized_and_no_retry(invoke):
    run, captured = invoke
    with pytest.raises(ValueError) as error:
        run(fail=requests.Timeout('secret-test-key provider body'))
    assert 'secret-test-key' not in str(error.value) and 'provider body' not in str(error.value)
    assert len(captured) == 1


def test_non_continuation_is_rejected_before_network(invoke):
    run, captured = invoke
    with pytest.raises(ValueError):
        run(target_duration=5.0)
    assert captured == []


def test_actual_base_duration_allows_less_than_one_second_of_continuation(invoke):
    run, _ = invoke
    plan = {**valid_plan(), 'beats': [{'start': 8.04, 'end': 9.0, 'action': '自然接续当前步伐。'}]}
    assert run(plan, source_duration=8.04, target_duration=9.0) == plan


def test_target_above_thirty_seconds_is_rejected_before_network(invoke):
    run, captured = invoke
    with pytest.raises(ValueError):
        run(target_duration=31.0)
    assert captured == []


def test_even_small_timeline_gaps_are_rejected(invoke):
    run, _ = invoke
    plan = valid_plan()
    plan['beats'][1]['start'] = 7.005
    with pytest.raises(ValueError):
        run(plan)


@pytest.mark.parametrize('source,target', [
    (11.041666666666666, 17), (7.708333333333333, 20),
    (8.04, 9), (12.345678, 18.765432),
])
def test_decimal_rounding_of_outer_boundaries_is_canonicalized(invoke, source, target):
    run, captured = invoke
    plan = {**valid_plan(), 'beats': [
        {'start': round(source, 2), 'end': round(target, 2), 'action': '自然接续。'}]}
    result = run(plan, source_duration=source, target_duration=target)
    assert result['beats'][0]['start'] == source
    assert result['beats'][-1]['end'] == target
    assert plan['beats'][0]['start'] == round(source, 2)  # Preserve raw input for diagnosis.
    system = captured[0][1]['json']['messages'][0]['content']
    assert json.dumps({'start': source, 'end': target}, ensure_ascii=False) in system
    assert len(captured) == 1


@pytest.mark.parametrize('start,end', [
    (11, 17), (11.039, 17), (11.041666666666666, 16.99),
])
def test_wrong_boundaries_are_not_treated_as_rounding(invoke, start, end):
    run, _ = invoke
    with pytest.raises(ValueError) as error:
        run({**valid_plan(), 'beats': [{'start': start, 'end': end, 'action': '继续。'}]},
            source_duration=11.041666666666666, target_duration=17)
    assert 'beats[0]' in str(error.value)
    assert '17' in str(error.value)
    assert getattr(error.value, 'error_kind', None) == 'continuation_plan_invalid'


@pytest.mark.parametrize('source,start', [(11.125, 11.12), (11.125, 11.13), (11.135, 11.13), (11.135, 11.14)])
def test_halfway_boundary_accepts_both_standard_rounding_conventions(invoke, source, start):
    run, _ = invoke
    plan = {**valid_plan(), 'beats': [{'start': start, 'end': 17, 'action': '继续。'}]}
    result = run(plan, source_duration=source, target_duration=17)
    assert result['beats'][0]['start'] == source


def test_rounding_cannot_collapse_short_extension_or_hide_internal_gap(invoke):
    run, _ = invoke
    plan = {**valid_plan(), 'beats': [{'start': 8.01, 'end': 8.01, 'action': '继续。'}]}
    with pytest.raises(ValueError):
        run(plan, source_duration=8.005, target_duration=8.014)
    plan['beats'] = [{'start': 11.04, 'end': 13, 'action': '继续。'},
                     {'start': 13.005, 'end': 17, 'action': '继续。'}]
    with pytest.raises(ValueError) as error:
        run(plan, source_duration=11.041666666666666, target_duration=17)
    assert 'beats[1].start' in str(error.value)


@pytest.mark.parametrize('change,field', [
    ({'invariants': {}}, 'invariants'),
    ({'ending_state': ''}, 'ending_state'),
    ({'beats': [{'start': 'secret-test-key', 'end': 8, 'action': 'a'}]}, 'beats[0].start'),
    ({'beats': [{'start': 5, 'end': 8, 'action': ''}]}, 'beats[0].action'),
])
def test_invalid_plan_identifies_field_without_echoing_private_values(invoke, change, field):
    run, _ = invoke
    with pytest.raises(ValueError) as error:
        run({**valid_plan(), **change})
    assert field in str(error.value)
    assert 'secret-test-key' not in str(error.value)


@pytest.mark.parametrize('status,body', [
    (401, b'{"error":"secret-test-key provider-private-error"}'),
    (302, b'{"redirect":"secret-test-key"}'),
    (200, b'{"choices":[]}'),
    (200, b'provider-private-error secret-test-key'),
    (200, json.dumps({'choices': [{'message': {'content': '```json\n{}\n```'}}]}).encode()),
    (200, json.dumps({'choices': [{'message': {'content': '{"ending_state":"a","ending_state":"b"}'}}]}).encode()),
])
def test_provider_errors_and_non_strict_json_are_sanitized(tmp_path, monkeypatch, status, body):
    from app.continuation_llm import plan_continuation
    from app.continuation_settings import ContinuationConfig
    image = tmp_path / 'tail.png'
    image.write_bytes(b'png-test-fixture')
    def post(*args, **kwargs):
        response = requests.Response()
        response.status_code = status
        response._content = body
        response._content_consumed = True
        return response
    monkeypatch.setattr('app.continuation_llm.requests.post', post)
    with pytest.raises(ValueError) as error:
        plan_continuation(ContinuationConfig(api_key='secret-test-key'), original_prompt='原始提示词',
                          frames=[{'timestamp': 4.9, 'path': image}], source_duration=5,
                          target_duration=8, reference_roles={})
    assert 'secret-test-key' not in str(error.value)
    assert 'provider-private-error' not in str(error.value)
