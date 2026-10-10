"""Tiny, genuinely decodable media for upload integration tests."""
from functools import lru_cache
from io import BytesIO
from pathlib import Path
import tempfile
from PIL import Image

def image_bytes(format='PNG', size=(32,32), color=(80,120,160)):
    stream=BytesIO()
    Image.new('RGB',size,color).save(stream,format=format)
    return stream.getvalue()

@lru_cache(maxsize=1)
def video_bytes():
    import cv2
    import numpy as np
    with tempfile.TemporaryDirectory() as folder:
        path=Path(folder)/'fixture.mp4'
        writer=cv2.VideoWriter(str(path),cv2.VideoWriter_fourcc(*'mp4v'),10,(32,32))
        assert writer.isOpened()
        for _ in range(20): writer.write(np.full((32,32,3),120,dtype=np.uint8))
        writer.release()
        return path.read_bytes()

def media_bytes(name):
    suffix=Path(name).suffix.lower()
    if suffix in {'.mp4','.mov','.m4v'}: return video_bytes()
    return image_bytes('JPEG' if suffix in {'.jpg','.jpeg'} else 'WEBP' if suffix=='.webp' else 'PNG')
