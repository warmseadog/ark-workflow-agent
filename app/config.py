from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

if TYPE_CHECKING:
    from .redaction_service import ServiceConfig


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def local_demo_enabled() -> bool:
    """Only the explicit loopback developer entry point enables offline fixtures."""
    return (os.getenv('APP_LOCAL_DEV_PROFILE') == 'demo'
            and os.getenv('APP_HOST') == '127.0.0.1'
            and not _as_bool(os.getenv('APP_AUTH_ENABLED')))


@dataclass(frozen=True)
class Settings:
    host: str
    port: int
    storage_dir: Path
    deface_bin: str
    deface_replacewith: str
    deface_mosaic_size: int
    deface_mask_scale: float
    seedance_mode: str
    seedance_api_url: str
    seedance_api_key: str
    seedance_poll_seconds: int
    max_upload_mb: int
    ytdlp_cookies_from_browser: str
    ytdlp_cookie_file: Path | None
    config_root: Path | None = None
    user_id: str = ''
    redaction_service: ServiceConfig | None = field(default=None, repr=False)

    @classmethod
    def from_env(cls) -> "Settings":
        storage_dir = Path(os.getenv("STORAGE_DIR", "storage")).resolve()
        for child in ("uploads", "work", "outputs"):
            (storage_dir / child).mkdir(parents=True, exist_ok=True)
        cookie_file = os.getenv("YTDLP_COOKIE_FILE", "").strip()
        return cls(
            host=os.getenv("APP_HOST", "127.0.0.1"),
            port=int(os.getenv("APP_PORT", "8000")),
            storage_dir=storage_dir,
            deface_bin=os.getenv("DEFACE_BIN", "deface"),
            deface_replacewith=os.getenv("DEFACE_REPLACEWITH", "mosaic"),
            deface_mosaic_size=int(os.getenv("DEFACE_MOSAIC_SIZE", "20")),
            deface_mask_scale=float(os.getenv("DEFACE_MASK_SCALE", "1.4")),
            seedance_mode=os.getenv("SEEDANCE_MODE", "mock").strip().lower(),
            seedance_api_url=os.getenv("SEEDANCE_API_URL", "").rstrip("/"),
            seedance_api_key=os.getenv("SEEDANCE_API_KEY", ""),
            seedance_poll_seconds=int(os.getenv("SEEDANCE_POLL_SECONDS", "5")),
            max_upload_mb=int(os.getenv("MAX_UPLOAD_MB", "512")),
            ytdlp_cookies_from_browser=os.getenv("YTDLP_COOKIES_FROM_BROWSER", "").strip().lower(),
            ytdlp_cookie_file=Path(cookie_file).expanduser().resolve() if cookie_file else None,
        )


settings = Settings.from_env()
