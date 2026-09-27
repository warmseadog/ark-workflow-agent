"""TikHub video resolution; credentials only go to the fixed official API host."""
from __future__ import annotations

import ipaddress
import re
import socket
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlsplit

import imageio_ffmpeg
import requests
import urllib3

from .local_preferences import get_tikhub_key
from .media_errors import MediaPipelineError
from .video_links import platform_for_url

API_BASE = 'https://api.tikhub.io'
ENDPOINTS = {
    'douyin': ('/api/v1/douyin/app/v3/fetch_one_video_by_share_url', 'share_url'),
    'xiaohongshu': ('/api/v1/xiaohongshu/app_v2/get_video_note_detail', 'share_text'),
    'kuaishou': ('/api/v1/kuaishou/web/fetch_one_video_by_url', 'url'),
}

@dataclass
class MediaPlan:
    videos: list[str]
    audio: str | None = None

def api_get(path, params, key):
    try:
        response = requests.get(API_BASE + path, params=params,
                                headers={'Authorization': f'Bearer {key}'},
                                timeout=(10, 60), allow_redirects=False)
        if response.status_code in {401, 403}:
            raise MediaPipelineError('TikHub 鉴权失败，请检查链接解析设置中的 API Key。')
        if response.status_code == 402:
            raise MediaPipelineError('TikHub 余额不足，请在 TikHub 控制台检查余额。')
        if response.status_code == 429:
            raise MediaPipelineError('TikHub 请求过于频繁，请稍后重试。')
        if response.status_code != 200:
            raise MediaPipelineError(f'TikHub 服务暂时不可用（HTTP {response.status_code}），请稍后重试。')
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError()
        code = payload.get('code', 200)
        if str(code) not in {'0', '200'}:
            raise MediaPipelineError('TikHub 未能解析该作品，请检查密钥、余额以及作品是否公开可访问。')
        data = payload.get('data')
        if not isinstance(data, (dict, list)) or not data:
            raise MediaPipelineError('TikHub 没有返回作品数据，请确认链接有效且作品公开。')
        return data
    except requests.RequestException:
        raise MediaPipelineError('连接 TikHub 超时或网络异常，请稍后重试。') from None
    except (ValueError, TypeError):
        raise MediaPipelineError('TikHub 返回了无法识别的数据，请稍后重试。') from None

def nodes(data):
    """Walk response wrappers but never mistake music, covers or avatars for video."""
    if isinstance(data, dict):
        yield data
        for key, value in data.items():
            if key.lower() not in {'music', 'cover', 'covers', 'origin_cover', 'dynamic_cover', 'author', 'user', 'images', 'image_list'}:
                yield from nodes(value)
    elif isinstance(data, list):
        for value in data:
            yield from nodes(value)

def http_url(value):
    if isinstance(value, str):
        if value.startswith('//'):
            value = 'https:' + value
        if urlsplit(value).scheme in {'http', 'https'}:
            return value
    return None

def address(value):
    if isinstance(value, str):
        return http_url(value)
    if isinstance(value, list):
        return next((result for item in value if (result := address(item))), None)
    if isinstance(value, dict):
        for key in ('url_list', 'master_url', 'main_url', 'baseUrl', 'base_url', 'url'):
            result = address(value.get(key))
            if result:
                return result
    return None

def parse_media(platform, data):
    if platform == 'bilibili':
        for node in nodes(data):
            dash = node.get('dash')
            if isinstance(dash, dict) and dash.get('video'):
                video = sorted(dash['video'], key=lambda x: (x.get('id', 0), x.get('bandwidth', 0)), reverse=True)
                audio = sorted(dash.get('audio') or [], key=lambda x: x.get('bandwidth', 0), reverse=True)
                url = address(video)
                if url:
                    return MediaPlan([url], address(audio))
            if node.get('durl'):
                urls = [address(item) for item in sorted(node['durl'], key=lambda x: x.get('order', 0))]
                if urls and all(urls):
                    return MediaPlan(urls)
    else:
        # Prefer explicit video nodes, then only explicit video-playback field names.
        for node in nodes(data):
            for key in ('video', 'video_info', 'video_info_v2', 'videoInfo', 'photo'):
                video = node.get(key)
                if not isinstance(video, dict):
                    continue
                for part in nodes(video):
                    for field in ('play_addr', 'playAddr', 'play_url', 'playUrl', 'photoUrl',
                                  'videoUrl', 'video_url', 'mp4Url', 'master_url', 'main_url'):
                        url = address(part.get(field))
                        if url:
                            return MediaPlan([url])
        for node in nodes(data):
            for field in ('photoUrl', 'mp4Url', 'video_url', 'videoUrl'):
                url = address(node.get(field))
                if url:
                    return MediaPlan([url])
    raise MediaPipelineError('未找到可下载的视频。请确认这是公开视频，而非图文笔记、直播或已删除的作品。')

_FAKE_IP_RANGE = ipaddress.ip_network('198.18.0.0/15')


def _resolve_fake_ip(hostname):
    """Resolve through a fixed HTTPS resolver; never trust a virtual IP as a destination."""
    try:
        with requests.get('https://dns.alidns.com/resolve',
                          params={'name': hostname, 'type': 'A'},
                          timeout=(5, 10), allow_redirects=False) as response:
            if response.status_code != 200:
                raise ValueError()
            data = response.json()
        if not isinstance(data, dict) or data.get('Status') != 0:
            raise ValueError()
        answers = data.get('Answer')
        if not isinstance(answers, list):
            raise ValueError()
        addresses = tuple(dict.fromkeys(str(ipaddress.ip_address(item['data']))
                          for item in answers if isinstance(item, dict) and item.get('type') == 1))
        if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
            raise ValueError()
        return addresses
    except (requests.RequestException, ValueError, TypeError, KeyError):
        raise MediaPipelineError('检测到代理 Fake-IP，但未能取得安全的公网视频地址。请检查网络或稍后重试，无需关闭虚拟 IP。') from None


def validate_public_url(url):
    try:
        parsed = urlsplit(url)
        if parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password or parsed.port not in {None, 80, 443}:
            raise ValueError()
        try:
            literal = ipaddress.ip_address(parsed.hostname)
        except ValueError:
            literal = None
        if literal is not None and not literal.is_global:
            raise ValueError()
        resolved = socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == 'https' else 80), type=socket.SOCK_STREAM)
        addresses = [ipaddress.ip_address(item[4][0]) for item in resolved]
        if not addresses or any(not address.is_global and address not in _FAKE_IP_RANGE for address in addresses):
            raise ValueError()
    except (ValueError, OSError):
        raise MediaPipelineError('视频地址无效，或指向不允许访问的本地网络。') from None
    if any(address in _FAKE_IP_RANGE for address in addresses):
        return _resolve_fake_ip(parsed.hostname)
    return None


class _PinnedResponse(requests.Response):
    def __init__(self, pool):
        super().__init__()
        self._media_pool = pool

    def close(self):
        try:
            super().close()
        finally:
            self._media_pool.close()


def _get_pinned_video(url, addresses, headers):
    """Connect only to verified public IPs, retaining Host, SNI and certificate checks."""
    parsed = urlsplit(url)
    hostname = parsed.hostname.encode('idna').decode('ascii')
    target = parsed.path or '/'
    if parsed.query:
        target += '?' + parsed.query
    outgoing = dict(headers or {})
    outgoing['Host'] = parsed.netloc
    for address in addresses[:4]:
        if not ipaddress.ip_address(address).is_global:
            raise MediaPipelineError('视频地址指向不允许访问的本地网络。')
        if parsed.scheme == 'https':
            pool = urllib3.HTTPSConnectionPool(
                address, port=parsed.port or 443, server_hostname=hostname,
                assert_hostname=hostname, cert_reqs='CERT_REQUIRED', ca_certs=requests.certs.where())
        else:
            pool = urllib3.HTTPConnectionPool(address, port=parsed.port or 80)
        try:
            raw = pool.urlopen('GET', target, headers=outgoing, preload_content=False,
                               redirect=False, retries=False, timeout=urllib3.Timeout(connect=10, read=45))
            response = _PinnedResponse(pool)
            response.status_code = raw.status
            response.headers = requests.structures.CaseInsensitiveDict(raw.headers)
            response.raw = raw
            response.url = url
            return response
        except (urllib3.exceptions.HTTPError, OSError):
            pool.close()
    raise MediaPipelineError('已识别代理 Fake-IP，但视频公网连接失败，请稍后重试或更换作品。')


def public_get(url, headers=None, platform=None):
    for _ in range(6):
        if platform and platform_for_url(url) != platform:
            raise MediaPipelineError('分享短链接跳转到了不支持的网站，请使用作品详情页链接。')
        public_addresses = validate_public_url(url)
        response = (_get_pinned_video(url, public_addresses, headers) if public_addresses else
                    requests.get(url, headers=headers or {}, timeout=(10, 45), stream=True, allow_redirects=False))
        if response.status_code in {301, 302, 303, 307, 308}:
            location = response.headers.get('Location', '')
            response.close()
            if not location:
                raise MediaPipelineError('分享链接跳转地址为空。')
            url = urljoin(url, location)
            continue
        if response.status_code != 200:
            response.close()
            raise MediaPipelineError('视频资源无法访问，请检查作品是否公开，或稍后重新解析链接。')
        return response, url
    raise MediaPipelineError('分享链接跳转次数过多，请复制作品详情页链接。')

def bilibili_identity(url):
    match = re.search(r'/video/(BV[0-9A-Za-z]+)', urlsplit(url).path)
    if not match:
        response, url = public_get(url, {'User-Agent': 'Mozilla/5.0'}, platform='bilibili')
        try:
            match = re.search(r'/video/(BV[0-9A-Za-z]+)', urlsplit(url).path)
            if not match:
                # Older av links can expose the canonical BV id in page metadata.
                sample = next(response.iter_content(256 * 1024), b'').decode('utf-8', errors='ignore')
                match = re.search(r'"bvid"\s*:\s*"(BV[0-9A-Za-z]+)"', sample)
        finally:
            response.close()
    if not match:
        raise MediaPipelineError('未识别到 B 站视频 BV 号，请复制视频详情页链接。')
    try:
        page = int(parse_qs(urlsplit(url).query).get('p', ['1'])[0])
        if page < 1:
            raise ValueError()
    except ValueError:
        raise MediaPipelineError('B 站分 P 参数无效。') from None
    return match.group(1), page

def resolve_media(url, key):
    platform = platform_for_url(url)
    if platform in ENDPOINTS:
        path, param = ENDPOINTS[platform]
        data = api_get(path, {param: url}, key)
        return parse_media(platform, data)
    if platform == 'bilibili':
        bv_id, page = bilibili_identity(url)
        detail = api_get('/api/v1/bilibili/web/fetch_one_video', {'bv_id': bv_id}, key)
        cid = None
        for node in nodes(detail):
            if isinstance(node.get('pages'), list):
                selected = next((item for item in node['pages'] if item.get('page') == page), None)
                if selected:
                    cid = selected.get('cid')
                break
            if page == 1 and node.get('cid'):
                cid = node['cid']
        if not cid:
            raise MediaPipelineError('未找到 B 站视频对应分 P，请检查链接中的 p 参数。')
        data = api_get('/api/v1/bilibili/web/fetch_video_playurl', {'bv_id': bv_id, 'cid': str(cid)}, key)
        return parse_media(platform, data)
    raise MediaPipelineError('TikHub 链接解析支持小红书、抖音、快手和 B 站。')

def download_tikhub_video(url, destination, settings):
    key = get_tikhub_key(settings)
    if not key:
        raise MediaPipelineError('请先打开「链接解析设置」保存 TikHub API Key，再导入平台链接。')
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        plan = resolve_media(url, key)
        headers = {'User-Agent': 'Mozilla/5.0'}
        if platform_for_url(url) == 'bilibili':
            headers['Referer'] = 'https://www.bilibili.com/'
        total = 0
        deadline = time.monotonic() + 900
        with tempfile.TemporaryDirectory(prefix='tikhub-', dir=destination.parent) as directory:
            root = Path(directory)
            paths = []
            for index, media_url in enumerate(plan.videos + ([plan.audio] if plan.audio else [])):
                path = root / f'{index}.media'
                response, _ = public_get(media_url, headers)
                with response:
                    content_type = response.headers.get('Content-Type', '').lower()
                    if any(kind in content_type for kind in ('text/', 'json', 'image/')):
                        raise MediaPipelineError('视频地址返回了非视频内容，请重新解析链接。')
                    with path.open('wb') as target:
                        for chunk in response.iter_content(1024 * 1024):
                            total += len(chunk)
                            if total > settings.max_upload_mb * 1024 * 1024:
                                raise MediaPipelineError('下载视频超过项目大小限制，请选择更短的视频。')
                            if time.monotonic() > deadline:
                                raise MediaPipelineError('视频下载超时，请稍后重试。')
                            target.write(chunk)
                if path.stat().st_size == 0:
                    raise MediaPipelineError('下载到的视频为空。')
                paths.append(path)
            command = [imageio_ffmpeg.get_ffmpeg_exe(), '-y', '-v', 'error']
            if len(plan.videos) > 1:
                manifest = root / 'segments.txt'
                manifest.write_text(''.join(f"file '{path.name}'\n" for path in paths[:len(plan.videos)]), encoding='utf-8')
                command += ['-f', 'concat', '-safe', '0', '-i', str(manifest)]
            else:
                command += ['-i', str(paths[0])]
            if plan.audio:
                command += ['-i', str(paths[-1]), '-map', '0:v:0', '-map', '1:a:0']
            else:
                command += ['-map', '0:v:0', '-map', '0:a:0?']
            output = root / 'complete.mp4'
            command += ['-c', 'copy', '-movflags', '+faststart', str(output)]
            result = subprocess.run(command, capture_output=True, timeout=300)
            if result.returncode or not output.exists() or not output.stat().st_size:
                raise MediaPipelineError('视频封装失败，请尝试其他作品或上传本地视频。')
            output.replace(destination)
        return destination
    except requests.RequestException:
        raise MediaPipelineError('视频下载网络异常，请稍后重试。') from None
    except (OSError, subprocess.TimeoutExpired):
        raise MediaPipelineError('视频下载或合并失败，请检查磁盘空间和 FFmpeg 环境。') from None
