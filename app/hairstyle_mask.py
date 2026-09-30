"""Independent face-only masking of hairstyle references; source files stay intact."""
from __future__ import annotations
import hashlib
import json
import math
from pathlib import Path
import threading
import uuid

import cv2
import numpy as np
from PIL import Image, ImageOps, UnidentifiedImageError
from .media_errors import MediaPipelineError

_lock = threading.Lock()
_detector = None
DEFAULTS = {'mask_scale':1.0, 'threshold':0.2}

def options(values=None):
    values = {} if values is None else values
    if not isinstance(values,dict) or set(values)-set(DEFAULTS):
        raise ValueError('发型打码参数不正确。')
    result = {**DEFAULTS, **values}
    for key,(low,high) in {'mask_scale':(1,2),'threshold':(0.05,0.95)}.items():
        value = result[key]
        if type(value) not in (int,float) or not math.isfinite(value) or not low <= value <= high:
            raise ValueError('发型遮罩倍数应为 1–2，检测阈值应为 0.05–0.95。')
        result[key] = float(value)
    return result

def _detect_faces(frame, threshold):
    global _detector
    from deface.centerface import CenterFace
    if _detector is None: _detector = CenterFace(backend='opencv')
    height,width = frame.shape[:2]
    scale = min(1,1280/max(width,height))
    _detector.in_shape = (max(1,round(width*scale)),max(1,round(height*scale)))
    detections,_ = _detector(frame, threshold=threshold)
    return detections

def mask_faces(frame, detections, mask_scale):
    """Use deface-compatible expansion, clipped to an ellipse to spare surrounding hair."""
    height,width = frame.shape[:2]
    for detection in detections:
        x1,y1,x2,y2 = map(float,detection[:4])
        dx,dy = (x2-x1)*(mask_scale-1),(y2-y1)*(mask_scale-1)
        x1,y1,x2,y2 = round(x1-dx),round(y1-dy),round(x2+dx),round(y2+dy)
        x1,y1,x2,y2 = max(0,x1),max(0,y1),min(width,x2),min(height,y2)
        if x2<=x1 or y2<=y1:continue
        roi = frame[y1:y2,x1:x2]
        small = cv2.resize(roi,(max(1,(x2-x1)//20),max(1,(y2-y1)//20)),interpolation=cv2.INTER_AREA)
        pixelated = cv2.resize(small,(x2-x1,y2-y1),interpolation=cv2.INTER_NEAREST)
        mask = np.zeros(roi.shape[:2],dtype=np.uint8)
        cv2.ellipse(mask,((x2-x1)//2,(y2-y1)//2),(max(1,(x2-x1)//2),max(1,(y2-y1)//2)),0,0,360,255,-1)
        roi[mask>0] = pixelated[mask>0]
    return frame

def process_hairstyle(settings, asset, values=None):
    params = options(values)
    if asset['kind']!='hairstyle':raise ValueError('请选择发型参考图。')
    source = Path(asset['path']).resolve()
    if not source.is_relative_to((settings.storage_dir/'assets').resolve()) or not source.is_file():
        raise ValueError('发型参考图不存在，请重新上传。')
    from .artifacts import sha256_file
    digest = sha256_file(source)
    if digest != asset['sha256']:raise ValueError('发型参考图已变化，请重新上传。')
    key = hashlib.sha256((digest+json.dumps(params,sort_keys=True)+':hair-face-v1').encode()).hexdigest()
    root = settings.storage_dir/'cache'/'hairstyle-mask';root.mkdir(parents=True,exist_ok=True)
    output,metadata = root/(key+'.png'),root/(key+'.json')
    with _lock:
        if output.is_file() and metadata.is_file():
            try:
                details = json.loads(metadata.read_text(encoding='utf-8'))
                if (isinstance(details,dict) and details.get('source_sha256')==digest
                        and details.get('settings')==params
                        and details.get('output_sha256')==sha256_file(output)
                        and type(details.get('faces_detected')) is int):
                    return {**details,'path':output,'key':key,'source_asset_id':asset.get('id')}
            except (ValueError,OSError):pass
        try:
            with Image.open(source) as image:
                if image.width*image.height>16000000:raise ValueError('发型参考图过大，请缩小到 1600 万像素以内。')
                if getattr(image,'n_frames',1)>1:raise ValueError('发型打码请使用静态图片。')
                frame = np.array(ImageOps.exif_transpose(image).convert('RGB'))
            detections = _detect_faces(frame,params['threshold'])
            result = mask_faces(frame,detections,params['mask_scale'])
            temporary = root/(uuid.uuid4().hex+'.tmp.png')
            try:
                Image.fromarray(result).save(temporary,format='PNG')
                temporary.replace(output)
            finally:temporary.unlink(missing_ok=True)
            details = {'faces_detected':len(detections),'settings':params,
                       'source_sha256':digest,'output_sha256':sha256_file(output)}
            metadata.write_text(json.dumps(details),encoding='utf-8')
            return {**details,'path':output,'key':key,'source_asset_id':asset.get('id')}
        except (ValueError,UnidentifiedImageError):raise ValueError('发型图无法处理，请使用有效静态图片（最多 1600 万像素）。') from None
        except Exception:raise MediaPipelineError('发型参考图打码失败，请检查图片后重试。') from None
