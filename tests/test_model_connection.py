from dataclasses import replace
from unittest.mock import Mock

import pytest
import requests

from app.generation_settings import GenerationConfig


KEY = 'unit-test-secret-key'


@pytest.fixture
def network(monkeypatch):
    response = Mock(status_code=200)
    response.json.return_value = {'items': [], 'total': 0}
    get = Mock(return_value=response)
    monkeypatch.setattr(requests, 'get', get)
    monkeypatch.setattr(requests, 'post', Mock(side_effect=AssertionError('No generation allowed')))
    return get, response


def probe(config):
    from app.model_connection import test_connection
    return test_connection(config)


def config(**overrides):
    return replace(GenerationConfig(mode='http', api_key=KEY), **overrides)


def test_ark_read_only_probe_proves_auth_not_generation(network):
    get, _ = network
    result = probe(config())
    assert result['ok'] is True
    assert result['status'] == 'connected'
    assert '鉴权' in result['message']
    assert '生成' in result['message']
    assert type(result['latency_ms']) is int
    args, kwargs = get.call_args
    assert args == ('https://ark.cn-beijing.volces.com/api/v3/contents/generations/tasks',)
    assert kwargs['params'] == {'page_size': 1}
    assert kwargs['headers']['Authorization'] == f'Bearer {KEY}'
    assert kwargs['allow_redirects'] is False
    assert kwargs['timeout'] == 15
    assert KEY not in str(result)


@pytest.mark.parametrize('protocol', ['toapis', 'adapter'])
def test_model_catalog_checks_selected_model(network, protocol):
    get, response = network
    response.json.return_value = {'data': [{'id': 'other'}, {'id': 'selected'}]}
    result = probe(config(protocol=protocol, base_url='https://provider.example/v1/', model='selected'))
    assert result['ok'] is True
    assert result['status'] == 'model_visible'
    assert '目录' in result['message']
    assert '生成' in result['message']
    assert get.call_args.args == ('https://provider.example/v1/models',)
    assert get.call_args.kwargs['allow_redirects'] is False
    assert get.call_args.kwargs['params'] == ({'type': 'video'} if protocol == 'toapis' else {})


def test_model_missing_is_not_declared_usable(network):
    _, response = network
    response.json.return_value = {'data': [{'id': 'another'}]}
    result = probe(config(protocol='toapis'))
    assert result['ok'] is False
    assert result['status'] == 'model_not_listed'
    assert '未找到' in result['message']


@pytest.mark.parametrize('status, expected', [(301, 'redirect_blocked'), (302, 'redirect_blocked'),
                                           (401, 'auth_failed'), (403, 'auth_failed'),
                                           (404, 'unsupported'), (405, 'unsupported'),
                                           (429, 'rate_limited'), (500, 'http_error')])
def test_http_failures_are_safe_and_actionable(network, status, expected):
    _, response = network
    response.status_code = status
    response.text = f'failed Authorization: Bearer {KEY} https://secret.example/?key={KEY}'
    response.json.return_value = {'error': {'message': response.text}}
    result = probe(config(protocol='adapter'))
    assert result['ok'] is False
    assert result['status'] == expected
    assert KEY not in str(result)
    assert 'secret.example' not in str(result)
    response.json.assert_not_called()


@pytest.mark.parametrize('exception, expected', [(requests.Timeout, 'timeout'),
                                                (requests.exceptions.SSLError, 'tls_error'),
                                                (requests.ConnectionError, 'connection_failed'),
                                                (requests.RequestException, 'connection_failed')])
def test_network_failures_do_not_leak_request_credentials(network, exception, expected):
    get, _ = network
    get.side_effect = exception(f'Failure URL /?api_key={KEY} Authorization: Bearer {KEY}')
    result = probe(config())
    assert result['ok'] is False
    assert result['status'] == expected
    assert KEY not in str(result)


@pytest.mark.parametrize('payload', [None, [], {'data': 'wrong'}, {'message': KEY}, {'data': [KEY]}])
def test_malformed_model_catalog_is_not_success(network, payload):
    _, response = network
    response.json.return_value = payload
    result = probe(config(protocol='toapis'))
    assert result['ok'] is False
    assert result['status'] == 'invalid_response'
    assert KEY not in str(result)


def test_non_json_success_response_is_not_success(network):
    _, response = network
    response.json.side_effect = ValueError(KEY)
    result = probe(config())
    assert result['status'] == 'invalid_response'
    assert KEY not in str(result)


def test_mock_mode_does_not_call_provider(network):
    get, _ = network
    result = probe(config(mode='mock'))
    assert result['ok'] is False
    assert result['status'] == 'mock'
    get.assert_not_called()


@pytest.mark.parametrize('overrides', [{'api_key': ''}, {'model': ''}, {'base_url': ''}])
def test_incomplete_config_does_not_make_request(network, overrides):
    get, _ = network
    result = probe(config(**overrides))
    assert result['ok'] is False
    assert result['status'] == 'incomplete'
    get.assert_not_called()


@pytest.mark.parametrize('base_url', ['https://provider.example/?key=' + KEY,
                                     'https://user:' + KEY + '@provider.example', 'file:///etc/passwd'])
def test_invalid_destination_rejected_without_echoing_url(network, base_url):
    get, _ = network
    result = probe(config(base_url=base_url))
    assert result['ok'] is False
    assert result['status'] == 'invalid_config'
    assert KEY not in str(result)
    get.assert_not_called()
