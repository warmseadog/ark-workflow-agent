"""Extract share URLs without discarding signed query parameters."""
import html
import re
from urllib.parse import urlsplit

PLATFORMS = {
    'douyin': ('douyin.com', 'iesdouyin.com'),
    'xiaohongshu': ('xiaohongshu.com', 'xhslink.com', 'xhslink.cn'),
    'kuaishou': ('kuaishou.com', 'gifshow.com', 'chenzhongtech.com'),
    'bilibili': ('bilibili.com', 'b23.tv', 'bili2233.cn'),
}
LABELS = {'douyin': '抖音', 'xiaohongshu': '小红书', 'kuaishou': '快手', 'bilibili': 'B 站'}
URL_RE = re.compile(r'https?://[^\s<>\[\]\u4e00-\u9fff，。！？；：、【】（）《》「」『』“”‘’\x00-\x1f]+', re.I)

def platform_for_url(url):
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or '').lower().rstrip('.')
        if parsed.scheme not in {'http', 'https'} or parsed.username or parsed.password or parsed.port not in {None, 80, 443}:
            return None
        for platform, domains in PLATFORMS.items():
            if any(host == domain or host.endswith('.' + domain) for domain in domains):
                return platform
    except ValueError:
        pass
    return None

def extract_video_url(raw):
    candidates = []
    for match in URL_RE.findall(html.unescape(raw.strip())):
        candidate = match.rstrip(".,!?;:)]}'\"")
        try:
            parsed = urlsplit(candidate)
            if parsed.hostname and not parsed.username and not parsed.password:
                candidates.append(candidate)
        except ValueError:
            continue
    supported = list(dict.fromkeys(url for url in candidates if platform_for_url(url)))
    if len(supported) > 1:
        raise ValueError('识别到多个视频链接，请每次只粘贴一个作品的分享文案。')
    if supported:
        return supported[0]
    if candidates:
        return candidates[0]
    raise ValueError('没有识别到有效的视频链接，请粘贴小红书、抖音、快手或 B 站分享文案，或上传本地视频。')
