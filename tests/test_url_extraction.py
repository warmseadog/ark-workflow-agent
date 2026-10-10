import pytest

from app.media import extract_video_url


@pytest.mark.parametrize(
    "value, expected",
    [
        (
            "https://v.douyin.com/abc123/",
            "https://v.douyin.com/abc123/",
        ),
        (
            "3.07 复制打开抖音，看看作品 https://v.douyin.com/abc123/。",
            "https://v.douyin.com/abc123/",
        ),
        (
            "复制打开小红书看看 https://www.xiaohongshu.com/explore/abc-123/】",
            "https://www.xiaohongshu.com/explore/abc-123/",
        ),
        (
            "https://xhslink.com/a/abc123/，欢迎查看",
            "https://xhslink.com/a/abc123/",
        ),
    ],
)
def test_extract_video_url_accepts_direct_and_embedded_platform_urls(value, expected):
    assert extract_video_url(value) == expected


def test_extract_video_url_keeps_other_direct_http_urls_for_yt_dlp():
    value = "https://example.com/video.mp4"

    assert extract_video_url(value) == value


def test_extract_video_url_rejects_share_text_without_a_url():
    with pytest.raises(ValueError, match="没有识别到有效的视频链接"):
        extract_video_url("3.07 复制打开抖音，看看这个作品")


@pytest.mark.parametrize('link', [
    'https://v.douyin.com/ZiN3vrgiA8E/',
    '[https://v.douyin.com/ZiN3vrgiA8E/](https://v.douyin.com/ZiN3vrgiA8E/)',
    '<https://v.douyin.com/ZiN3vrgiA8E/>',
])
def test_douyin_share_with_timestamp_and_contact(link):
    text = f'1.00 复制打开抖音，看看【宇宙來的的图文作品】你总是盘旋 在我脑海里面  {link} :6pm 12/06 cNj:/ [s@R.Xz](mailto:s@R.Xz)'
    assert extract_video_url(text) == 'https://v.douyin.com/ZiN3vrgiA8E/'


def test_markdown_share_preserves_signed_query_and_detects_distinct_links():
    url = 'https://www.xiaohongshu.com/explore/abc?xsec_token=a%2Bb%3D&xsec_source=pc'
    assert extract_video_url(f'[{url}]({url})') == url
    with pytest.raises(ValueError, match='多个视频链接'):
        extract_video_url('[A](https://v.douyin.com/a/) [B](https://v.douyin.com/b/)')

