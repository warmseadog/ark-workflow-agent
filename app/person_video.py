"""Person video validation and active media selection for Seedance 2.0 workflows."""
import hashlib
import math
from pathlib import Path


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


def validate_file(path, *, person=True):
    path=Path(path)
    if not path.is_file() or not 0<path.stat().st_size<=50*1024*1024:
        raise ValueError('单段参考视频需大于 0、小于等于 50 MB。')
    if person and path.suffix.lower() not in {'.mp4','.mov'}:
        raise ValueError('人物视频请使用 MP4 或 MOV 格式。')
    info=probe(path)
    if not 2<=info['duration']<=15:
        raise ValueError('参考视频时长需在 2–15 秒之间。')
    if person:
        w,h=info['width'],info['height']
        if not (300<w<6000 and 300<h<6000 and .4<w/h<2.5 and 407696<=w*h<=8295044):
            raise ValueError('人物视频尺寸不符合素材库要求，请使用 720p 或 1080p 的横屏或竖屏视频。')
        if not 23.9<=info['fps']<=60.1:
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


def validate_pair(source, person):
    total=validate_file(source,person=False)['duration']+validate_file(person)['duration']
    if total>15:
        raise ValueError(f'动作视频和人物视频合计 {total:.1f} 秒，超过 15 秒；请缩短其中一段再提交。')
    return total
