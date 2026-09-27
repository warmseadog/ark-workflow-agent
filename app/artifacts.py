"""Small, deterministic helpers for immutable workflow artifacts."""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import BinaryIO


def sha256_file(path: str | Path, chunk_size: int = 1024 * 1024) -> str:
    """Return the SHA-256 digest without loading a media file into memory."""
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        while chunk := stream.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_publish(source: str | Path, destination: str | Path) -> Path:
    """Atomically publish a completed artifact after its producer closes it."""
    src, dst = Path(source), Path(destination)
    dst.parent.mkdir(parents=True, exist_ok=True)
    src.replace(dst)
    return dst

