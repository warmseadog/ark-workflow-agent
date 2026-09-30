"""Bounded local media preparation; never stretch time or pad still frames."""
from pathlib import Path
import math
import subprocess
from .person_video import probe


def ending_frames(video, directory):
    import cv2
    info = probe(video)
    directory = Path(directory)
    directory.mkdir(parents=True,exist_ok=True)
    count = int(round(info['duration']*info['fps']))
    first = max(0,count-round(3*info['fps']))
    indices = [round(first+(count-1-first)*i/7) for i in range(8)]
    capture = cv2.VideoCapture(str(video))
    frames = []
    try:
        for i,index in enumerate(indices):
            capture.set(cv2.CAP_PROP_POS_FRAMES,index)
            ok,frame = capture.read()
            if not ok:
                raise ValueError('基础视频结尾画面无法读取，已保留基础片，请恢复任务重试。')
            h,w = frame.shape[:2]
            if max(h,w)>960:
                scale=960/max(h,w)
                frame=cv2.resize(frame,(round(w*scale),round(h*scale)))
            path=directory/f'ending-{i}.jpg'
            ok,encoded=cv2.imencode('.jpg',frame,[cv2.IMWRITE_JPEG_QUALITY,85])
            if not ok:
                raise ValueError('无法保存续写分析画面。')
            path.write_bytes(encoded.tobytes())
            frames.append({'timestamp':index/info['fps'],'path':path})
    finally:
        capture.release()
    return frames


def finalize(source, target, target_duration):
    """Expect a complete extend result, not an ambiguous tail-only clip."""
    import imageio_ffmpeg
    info = probe(source)
    if info['duration'] < target_duration-1/info['fps']-.001 or info['duration'] > math.ceil(target_duration)+.2:
        raise ValueError('续写结果总时长与目标不符，已保留基础片和远端结果，请核对服务商任务。')
    target=Path(target)
    temporary=target.with_name(target.stem+'.continuation.partial.mp4')
    try:
        subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(),'-nostdin','-y','-v','error','-i',str(source),
            '-t',str(target_duration),'-map','0:v:0','-map','0:a?','-c:v','libx264','-threads','1',
            '-preset','fast','-crf','18','-pix_fmt','yuv420p','-c:a','aac','-movflags','+faststart',str(temporary)],
            check=True,capture_output=True,timeout=600)
        final=probe(temporary)
        if abs(final['duration']-target_duration)>1/final['fps']+.01:
            raise ValueError('续写成片时长校验未通过，已保留基础片。')
        temporary.replace(target)
        return target
    except (OSError,subprocess.SubprocessError):
        raise ValueError('续写成片处理失败，已保留基础片，请检查磁盘空间并恢复任务。') from None
    finally:
        temporary.unlink(missing_ok=True)
