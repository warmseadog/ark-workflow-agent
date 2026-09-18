from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


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

    @classmethod
    def from_env(cls) -> "Settings":
        storage_dir = Path(os.getenv("STORAGE_DIR", "storage")).resolve()
        for child in ("uploads", "work", "outputs"):
            (storage_dir / child).mkdir(parents=True, exist_ok=True)
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
        )


settings = Settings.from_env()
