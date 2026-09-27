from pathlib import Path

from app.config import Settings
from app.media import build_download_command


def _settings(**changes):
    values = dict(
        host="127.0.0.1",
        port=8000,
        storage_dir=Path("storage"),
        deface_bin="deface",
        deface_replacewith="mosaic",
        deface_mosaic_size=20,
        deface_mask_scale=1.4,
        seedance_mode="mock",
        seedance_api_url="",
        seedance_api_key="",
        seedance_poll_seconds=5,
        max_upload_mb=512,
        ytdlp_cookies_from_browser="",
        ytdlp_cookie_file=None,
    )
    values.update(changes)
    return Settings(**values)


def test_download_command_uses_browser_cookies_when_configured(tmp_path):
    command = build_download_command(
        "https://v.douyin.com/example/",
        tmp_path / "source.mp4",
        _settings(ytdlp_cookies_from_browser="chrome"),
    )

    assert command[command.index("--cookies-from-browser") + 1] == "chrome"
    assert "--cookies" not in command


def test_download_command_uses_cookie_file_when_configured(tmp_path):
    cookie_file = tmp_path / "cookies.txt"
    command = build_download_command(
        "https://v.douyin.com/example/",
        tmp_path / "source.mp4",
        _settings(ytdlp_cookie_file=cookie_file),
    )

    assert command[command.index("--cookies") + 1] == str(cookie_file)
    assert "--cookies-from-browser" not in command


def test_browser_cookie_setting_takes_precedence_over_cookie_file(tmp_path):
    cookie_file = tmp_path / "cookies.txt"
    command = build_download_command(
        "https://v.douyin.com/example/",
        tmp_path / "source.mp4",
        _settings(ytdlp_cookies_from_browser="edge", ytdlp_cookie_file=cookie_file),
    )

    assert command[command.index("--cookies-from-browser") + 1] == "edge"
    assert "--cookies" not in command
