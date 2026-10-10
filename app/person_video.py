"""Person video validation and active media selection for Seedance workflows."""
import hashlib
import math
from pathlib import Path
from . import input_limits as limits


def is_video(draft):
    return draft.get('person_reference_mode','image') == 'video'


def person_ids(draft):
    return ([draft['person_video_asset_id']] if draft.get('person_video_asset_id') else []) if is_video(draft) else draft.get('face_asset_ids',[])


def probe(path):
    import cv2
    capture=cv2.VideoCapture(str(path))
    try:
        fps=capture.get(cv2.CAP_PROP_FPS)
        frames=capture.get(cv2.CAP_PROP_FRAME_COUNT)
        width=capture.get(cv2.CAP_PROP_FRAME_WIDTH)
        height=capture.get(cv2.CAP_PROP_FRAME_HEIGHT)
        if not capture.isOpened() or not all(math.isfinite(v) and v>0 for v in (fps,frames,width,height)) or not capture.read()[0]:
            raise ValueError('视频无法读取，请使用有效的 MP4 或 MOV 视频。')
        return {'duration':frames/fps,'fps':fps,'width':width,'height':height}
    finally:capture.release()


def validate_file(path, *, person=True, max_seconds=limits.PERSON_VIDEO_MAX_SECONDS):
    path=Path(path)
    if not path.is_file() or not 0<path.stat().st_size<=limits.PERSON_VIDEO_MAX_BYTES:
        raise ValueError('单段参考视频需大于 0、小于等于 50 MB。')
    if person and path.suffix.lower() not in limits.PERSON_VIDEO_EXTENSIONS:
        raise ValueError('人物视频请使用 MP4 或 MOV 格式。')
    info=probe(path)
    if not limits.PERSON_VIDEO_MIN_SECONDS<=info['duration']<=max_seconds:
        raise ValueError(f'参考视频时长需在 2–{max_seconds} 秒之间。')
    if person:
        w,h=info['width'],info['height']
        if not (limits.PORTRAIT_MIN_DIMENSION<w<limits.PORTRAIT_MAX_DIMENSION and limits.PORTRAIT_MIN_DIMENSION<h<limits.PORTRAIT_MAX_DIMENSION
                and limits.PORTRAIT_MIN_ASPECT<w/h<limits.PORTRAIT_MAX_ASPECT and limits.PERSON_VIDEO_MIN_PIXELS<=w*h<=limits.PERSON_VIDEO_MAX_PIXELS):
            raise ValueError('人物视频尺寸不符合素材库要求，请使用 720p 或 1080p 的横屏或竖屏视频。')
        if not limits.PERSON_VIDEO_MIN_FPS<=info['fps']<=limits.PERSON_VIDEO_MAX_FPS:
            raise ValueError('人物视频帧率需为 24–60 fps。')
    return info


def validate_asset(settings, asset):
    path=Path(asset['path']).resolve()
    if asset['kind']!='person_video' or not path.is_relative_to((settings.storage_dir/'assets').resolve()):
        raise ValueError('请选择已上传的人物视频。')
    validate_file(path)
    if hashlib.sha256(path.read_bytes()).hexdigest()!=asset['sha256']:
        raise ValueError('人物视频内容已变化，请重新上传。')
    return path


def validate_pair(source, person, *, max_seconds=15, source_clip=None):
    from .source_clip import validate_source
    total=validate_source(source,source_clip,max_seconds=max_seconds)['duration']+validate_file(person,max_seconds=max_seconds)['duration']
    if total>max_seconds:
        raise ValueError(f'动作视频和人物视频合计 {total:.1f} 秒，超过 {max_seconds} 秒；请缩短其中一段再提交。')
    return total
