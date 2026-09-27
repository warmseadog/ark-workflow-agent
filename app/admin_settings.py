"""Configuration overview and account verification, without returning secrets."""
import requests
from . import local_preferences, storage_settings
from .generation_settings import load_config


def public_model(settings, config=None):
    config = config or load_config(settings)
    result = config.public()
    storage = storage_settings.load_config(settings)
    result['generation_message'] = generation_problem(config, storage)
    result['video_source'] = 'tos' if storage.enabled else 'workbench'
    return result


def generation_problem(config, storage):
    if config.mode == 'http' and config.protocol == 'ark' and storage.enabled:
        return config.problem() or storage.problem()
    return config.generation_problem()


def overview(settings):
    return {
        'model': public_model(settings), 'storage': storage_settings.load_config(settings).public(),
        'tikhub': {'has_api_key': bool(local_preferences.get_tikhub_key(settings)), 'base_url': 'https://api.tikhub.io'},
        'system': {'storage_dir': str(settings.storage_dir), 'max_upload_mb': settings.max_upload_mb,
                   'seedance_poll_seconds': settings.seedance_poll_seconds, 'access': '仅本机访问',
                   'cookie_configured': bool(settings.ytdlp_cookie_file or settings.ytdlp_cookies_from_browser)},
        'redaction': {'blur_style': settings.deface_replacewith, 'mosaic_size': settings.deface_mosaic_size,
                       'mask_scale': settings.deface_mask_scale},
    }


def test_tikhub(settings, api_key='', clear_api_key=False):
    key = '' if clear_api_key else (api_key.strip() or local_preferences.get_tikhub_key(settings))
    if not key:
        return {'ok': False, 'message': '请填写 TikHub API Key，或先保存密钥。'}
    if any(ord(c) < 33 or ord(c) > 126 for c in key):
        return {'ok': False, 'message': 'TikHub API Key 格式不正确。'}
    try:
        response = requests.get('https://api.tikhub.io/api/v1/tikhub/user/get_user_info',
                                headers={'Authorization': f'Bearer {key}'}, timeout=(10, 15), allow_redirects=False)
        if response.status_code in {401, 403}:
            return {'ok': False, 'message': 'TikHub 鉴权失败，请检查 API Key 是否有效或已过期。'}
        if response.status_code != 200:
            return {'ok': False, 'message': f'TikHub 连接失败（HTTP {response.status_code}），请检查网络或稍后重试。'}
        data = response.json()
        if not isinstance(data, dict) or str(data.get('code')) not in {'0', '200'} or not isinstance(data.get('user_data'), dict):
            return {'ok': False, 'message': 'TikHub 返回了异常账户信息，请检查密钥权限。'}
        user = data['user_data']
        if user.get('account_disabled') is True or user.get('is_active') is False:
            return {'ok': False, 'message': 'TikHub 账户未激活或已禁用，请在服务商控制台检查。'}
        return {'ok': True, 'message': 'TikHub 账户鉴权通过。未抓取视频；具体平台权限和可用额度以 TikHub 控制台为准。'}
    except (requests.RequestException, ValueError):
        return {'ok': False, 'message': 'TikHub 连接超时或响应异常，请检查网络后重试。'}
