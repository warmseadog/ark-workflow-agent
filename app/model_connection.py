"""Read-only provider checks: never create a generation or upload media."""
from __future__ import annotations

from time import perf_counter

import requests

from .generation_settings import GenerationConfig, validate_url


def test_connection(config: GenerationConfig) -> dict:
    """Check the saved destination, returning only bounded, credential-free text.

    A successful task-list request proves Ark API authentication. A model-list
    request additionally proves catalog visibility, not generation entitlement.
    Provider bodies and exception strings must never be included in the result.
    """
    started = perf_counter()

    def result(ok: bool, status: str, message: str) -> dict:
        return {'ok': ok, 'status': status, 'message': message,
                'latency_ms': max(0, round((perf_counter() - started) * 1000))}

    if config.mode == 'mock':
        return result(False, 'mock', '当前为本地演示模式，未调用外部接口；切换真实生成后可测试连通。')
    if not config.base_url or not config.api_key or not config.model:
        return result(False, 'incomplete', '请先填写接口地址、API Key 和模型 ID，再测试连通。')
    if config.mode != 'http' or config.protocol not in {'ark', 'toapis', 'adapter'}:
        return result(False, 'invalid_config', '接口模式或格式不受支持，请检查模型配置。')
    try:
        base_url = validate_url(config.base_url)
    except ValueError:
        return result(False, 'invalid_config', '接口地址格式不正确，请检查模型配置。')

    if config.protocol == 'ark':
        endpoint = '/contents/generations/tasks'
        params = {'page_size': 1}
    else:
        endpoint = '/models'
        # ToAPIs defaults to text models unless a media type is specified.
        params = {'type': 'video'} if config.protocol == 'toapis' else {}
    try:
        response = requests.get(
            base_url + endpoint,
            params=params,
            headers={'Authorization': f'Bearer {config.api_key}', 'Accept': 'application/json'},
            timeout=15,
            allow_redirects=False,
        )
    except requests.exceptions.SSLError:
        return result(False, 'tls_error', 'TLS 证书验证失败，请检查接口域名和证书或本机代理设置。')
    except requests.Timeout:
        return result(False, 'timeout', '连接测试超时，请检查接口地址、端口和网络后重试。')
    except requests.RequestException:
        return result(False, 'connection_failed', '无法连接服务商，请检查接口地址、端口、DNS 和网络设置。')

    code = response.status_code
    if 300 <= code < 400:
        return result(False, 'redirect_blocked', '接口返回了跳转，测试未跟随跳转；请填写最终 API 地址后重试。')
    if code in {401, 403}:
        return result(False, 'auth_failed', '接口鉴权失败，请检查 API Key、账户权限以及所选服务商。')
    if code in {404, 405}:
        return result(False, 'unsupported', '当前地址不支持此只读测试接口，暂时无法验证；请核对 API 地址和接口格式。')
    if code == 429:
        return result(False, 'rate_limited', '服务商限制了当前请求，请稍后重试并检查账户额度。')
    if not 200 <= code < 300:
        return result(False, 'http_error', f'服务商返回 HTTP {code}，请检查服务状态和账户设置后重试。')
    try:
        payload = response.json()
    except ValueError:
        return result(False, 'invalid_response', '接口未返回有效的 JSON 数据，无法确认连通；请核对 API 地址和接口格式。')
    if not isinstance(payload, dict) or payload.get('error') or payload.get('success') is False:
        return result(False, 'invalid_response', '接口响应格式不符合预期，无法确认连通；请核对 API 地址和接口格式。')
    if config.protocol == 'ark':
        if not isinstance(payload.get('items'), list):
            return result(False, 'invalid_response', '接口未返回有效的视频任务列表，无法确认连通。')
        return result(True, 'connected', '视频任务接口连通且鉴权通过；尚未验证所选模型的生成权限，未创建生成任务。')

    models = payload.get('data')
    if not isinstance(models, list) or any(
        not isinstance(model, dict) or not isinstance(model.get('id'), str) for model in models
    ):
        return result(False, 'invalid_response', '接口未返回有效的模型目录，无法确认当前模型是否可用。')
    if not any(model['id'] == config.model for model in models):
        return result(False, 'model_not_listed', '接口可访问，但模型目录中未找到所填模型 ID；请检查拼写和账户模型权限。')
    return result(True, 'model_visible', '接口可访问，模型目录中已找到所选模型；目录可见不保证生成权限，未创建生成任务。')
