"""Validate uploaded bytes before they become persisted media assets."""
from pathlib import Path
import math
import warnings

import cv2
from fastapi import HTTPException
from PIL import Image

IMAGE_FORMATS = {
    '.png': {'PNG'}, '.jpg': {'JPEG'}, '.jpeg': {'JPEG'}, '.webp': {'WEBP'},
    '.gif': {'GIF'}, '.bmp': {'BMP'}, '.tif': {'TIFF'}, '.tiff': {'TIFF'},
    '.heic': {'HEIF', 'HEIC'}, '.heif': {'HEIF', 'HEIC'}, '.avif': {'AVIF'},
}
VIDEO_SUFFIXES = {'.mp4', '.mov', '.m4v', '.webm', '.mkv', '.avi'}
MAX_DIMENSION = 16384
MAX_PIXELS = 40_000_000


def _dimensions(width, height):
    if not all(math.isfinite(value) and 0 < value <= MAX_DIMENSION for value in (width, height)) or width * height > MAX_PIXELS:
        raise ValueError('invalid media dimensions')


def validate_media(path: Path, kind: str, max_bytes: int | None = None) -> None:
    """Require an allowed extension, matching container, and decodable pixels."""
    try:
        size = path.stat().st_size
        if not size:
            raise HTTPException(422, '素材为空，请重新选择。')
        if max_bytes is not None and size > max_bytes:
            raise HTTPException(413, '素材超过大小限制。')
        suffix = path.suffix.lower()
        if kind == 'image':
            if suffix not in IMAGE_FORMATS:
                raise ValueError('unsupported image extension')
            with warnings.catch_warnings():
                warnings.simplefilter('error', Image.DecompressionBombWarning)
                with Image.open(path) as picture:
                    if picture.format not in IMAGE_FORMATS[suffix]:
                        raise ValueError('image format mismatch')
                    _dimensions(*picture.size)
                    picture.verify()
                with Image.open(path) as picture:
                    frames = getattr(picture, 'n_frames', 1)
                    if frames > 1000 or picture.width * picture.height * frames > 100_000_000:
                        raise ValueError('image decoding limit exceeded')
                    decoded_pixels = 0
                    for index in range(frames):
                        picture.seek(index)
                        _dimensions(*picture.size)
                        decoded_pixels += picture.width * picture.height
                        if decoded_pixels > 100_000_000:
                            raise ValueError('image decoding limit exceeded')
                        picture.load()
        elif kind == 'video':
            with path.open('rb') as stream:
                header = stream.read(64)
            if suffix in {'.mp4', '.mov', '.m4v'}:
                container_ok = header[4:8] in {b'ftyp', b'moov', b'mdat', b'wide', b'free'}
                if header[4:8] == b'ftyp' and header[8:12] in {b'avif', b'avis', b'heic', b'heix', b'mif1', b'msf1'}:
                    container_ok = False
            elif suffix in {'.webm', '.mkv'}:
                container_ok = header.startswith(b'\x1aE\xdf\xa3')
            elif suffix == '.avi':
                container_ok = header.startswith(b'RIFF') and header[8:12] == b'AVI '
            else:
                container_ok = False
            if not container_ok:
                raise ValueError('video container mismatch')
            capture = cv2.VideoCapture(str(path), cv2.CAP_FFMPEG)
            try:
                if not capture.isOpened():
                    raise ValueError('invalid video')
                _dimensions(capture.get(cv2.CAP_PROP_FRAME_WIDTH), capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
                ok, frame = capture.read()
                if not ok or frame is None:
                    raise ValueError('undecodable video')
                _dimensions(frame.shape[1], frame.shape[0])
            finally:
                capture.release()
        else:
            raise ValueError('unsupported media kind')
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(422, '素材格式无效、已损坏或尺寸超出限制，请重新选择有效的图片或视频。') from exc


def save_upload(upload, destination: Path, max_bytes: int, kind: str) -> Path:
    """Bound writes and remove incomplete or invalid uploads after closing them."""
    try:
        expected = VIDEO_SUFFIXES if kind == 'video' else IMAGE_FORMATS
        if Path(upload.filename or '').suffix.lower() not in expected:
            raise HTTPException(422, '请选择有效的视频或图片文件。')
        destination.parent.mkdir(parents=True, exist_ok=True)
        total = 0
        with destination.open('wb') as target:
            while chunk := upload.file.read(1024 * 1024):
                total += len(chunk)
                if total > max_bytes:
                    raise HTTPException(413, '上传文件超过大小限制。')
                target.write(chunk)
        validate_media(destination, kind, max_bytes)
        return destination
    except Exception as exc:
        destination.unlink(missing_ok=True)
        if isinstance(exc, HTTPException):
            raise
        raise HTTPException(500, '素材保存失败，请稍后重试。') from exc
