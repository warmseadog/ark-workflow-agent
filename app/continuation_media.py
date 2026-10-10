"""Bounded local media preparation; never stretch time or pad still frames."""
from pathlib import Path
import math
import subprocess
import tempfile
from fractions import Fraction
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


class ContinuationQualityError(ValueError):
    error_kind = 'continuation_seam_mismatch'


class _Frames(list):
    def __init__(self, values, time_base):
        super().__init__(values)
        self.time_base = time_base


def _ffmpeg(arguments):
    import imageio_ffmpeg
    return subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-nostdin', '-v', 'error',
        '-threads', '1', *arguments], check=True, capture_output=True, timeout=600).stdout


def _frame_signatures(path, *, original_timing=True):
    """Decoded frames AND presentation timing; no perceptual tolerance for the prefix."""
    data = _ffmpeg(['-copyts', '-i', str(path), '-map', '0:v:0', '-an', '-fps_mode', 'passthrough',
        *(['-enc_time_base', 'demux'] if original_timing else ['-frames:v', '1']),
        '-threads', '1', '-f', 'framemd5', '-']).decode('ascii')
    time_base = None
    result = []
    for line in data.splitlines():
        if line.startswith('#tb 0:'):
            time_base = Fraction(line.split(':', 1)[1].strip())
        elif line and not line.startswith('#'):
            if time_base is None:
                raise ValueError('无法校验基础片的播放时间。')
            _, _, pts, duration, size, digest = [part.strip() for part in line.split(',')]
            result.append((int(pts)*time_base, int(duration)*time_base, size, digest))
    return _Frames(result, time_base)


def _check_seam(base, tail):
    """Conservative cut detector, not a guarantee of identity or motion quality."""
    import cv2
    import numpy as np

    def samples(path, at_end):
        capture = cv2.VideoCapture(str(path))
        try:
            count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
            start = max(0, count - 6) if at_end else 0
            capture.set(cv2.CAP_PROP_POS_FRAMES, start)
            result = []
            for _ in range(min(6, count)):
                ok, frame = capture.read()
                if not ok:
                    raise ValueError('接续点画面无法读取，基础片已保留。')
                result.append(cv2.resize(frame, (96, 96), interpolation=cv2.INTER_AREA).astype(np.float32))
            if not result:
                raise ValueError('接续点缺少有效画面，基础片已保留。')
            return result
        finally:
            capture.release()

    left, right = samples(base, True), samples(tail, False)
    distance = lambda a, b: float(np.abs(a - b).mean())
    motion = [distance(a, b) for frames in (left, right) for a, b in zip(frames, frames[1:])]
    threshold = max(16.0, min(30.0, float(np.median(motion or [0])) * 4 + 8))
    if distance(left[-1], right[0]) > threshold:
        raise ContinuationQualityError('续写接续处画面变化过大，未发布成片；基础片已保留，可重新生成尾段。')


def _has_audio(path):
    # Include video so a silent input still has an output stream.
    data = _ffmpeg(['-i', str(path), '-map', '0:v:0', '-map', '0:a:0?', '-c', 'copy',
        '-f', 'streamhash', '-'])
    return b',a,' in data


def finalize(source, target, target_duration, base):
    """Publish original base packets + generated tail, only after exact prefix verification."""
    info, original = probe(source), probe(base)
    if info['duration'] < target_duration-1/info['fps']-.001 or info['duration'] > math.ceil(target_duration)+.2:
        raise ValueError('续写结果总时长与目标不符，已保留基础片和远端结果，请核对服务商任务。')
    duration = original['duration']
    if not math.isfinite(target_duration) or target_duration <= duration:
        raise ValueError('目标时长必须长于完整基础片，不能裁剪已有内容。')
    target = Path(target)
    try:
        expected = _frame_signatures(base)
        if not expected:
            raise ValueError('基础片没有可校验的视频帧。')
        period = _frame_signatures(base, original_timing=False)[0][1]
        timescale = expected.time_base.denominator
        tick = expected.time_base
        if period <= 0 or expected[0][0] != 0 or any(abs(frame[0]-index*period)>tick or abs(frame[1]-period)>tick for index, frame in enumerate(expected)):
            raise ValueError('基础片时间轴不是从零开始的恒定帧率，无法保证无损续接；原片已保留。')
        # OpenCV reports rounded average rates for some MP4 track timescales.
        # Derive the cut and encoding rate from decoded presentation timestamps.
        rate = 1 / period
        # Keep the base container's exact end; allow one track tick of rounding
        # when selecting the first generated tail frame.
        cut = duration - float(tick)
        timescale = str(timescale)
        with tempfile.TemporaryDirectory(prefix='.continuation-', dir=target.parent) as directory:
            work = Path(directory)
            clean_base, tail, joined, ready = [work/name for name in ('base.mp4', 'tail.mp4', 'joined.mp4', 'ready.mp4')]
            # The original video is never sent through an encoder or a filter.
            _ffmpeg(['-i', str(base), '-map', '0:v:0', '-an', '-c:v', 'copy',
                '-video_track_timescale', timescale, str(clean_base)])
            _ffmpeg(['-i', str(source), '-map', '0:v:0', '-an', '-vf',
                f'trim=start={cut},setpts=PTS-STARTPTS,scale={int(original["width"])}:{int(original["height"])},fps={rate}',
                '-t', str(target_duration-duration), '-c:v', 'libx264', '-threads', '1',
                '-preset', 'fast', '-crf', '18', '-pix_fmt', 'yuv420p', '-video_track_timescale', timescale, str(tail)])
            _check_seam(base, tail)
            listing = work/'concat.txt'
            listing.write_text(f"file 'base.mp4'\nduration {duration}\nfile 'tail.mp4'\n", encoding='ascii')
            _ffmpeg(['-f', 'concat', '-safe', '1', '-i', str(listing), '-map', '0:v:0',
                '-c:v', 'copy', '-video_track_timescale', timescale, '-movflags', '+faststart', str(joined)])
            base_audio, tail_audio = _has_audio(base), _has_audio(source)
            if base_audio or tail_audio:
                # Keep the original audio content in the prefix. Only audio is encoded;
                # video packets, including the entire base, remain untouched.
                inputs = []
                for path, present in ((base, base_audio), (source, tail_audio)):
                    inputs += ['-i', str(path)] if present else ['-f', 'lavfi', '-i', 'anullsrc=r=48000:cl=stereo']
                tail_start = duration if tail_audio else 0
                filters = (f'[0:a:0]atrim=duration={duration},aresample=48000:first_pts=0,'
                    f'aformat=channel_layouts=stereo,apad,atrim=duration={duration}[a];'
                    f'[1:a:0]atrim=start={tail_start}:duration={target_duration-duration},asetpts=PTS-{tail_start}/TB,'
                    f'aresample=48000:first_pts=0,aformat=channel_layouts=stereo,apad,atrim=duration={target_duration-duration}[b];'
                    '[a][b]concat=n=2:v=0:a=1[out]')
                audio = work/'audio.m4a'
                _ffmpeg([*inputs, '-filter_complex_threads', '1', '-filter_complex', filters,
                    '-map', '[out]', '-c:a', 'aac', '-threads', '1', str(audio)])
                _ffmpeg(['-i', str(joined), '-i', str(audio), '-map', '0:v:0', '-map', '1:a:0',
                    '-c', 'copy', '-video_track_timescale', timescale, '-movflags', '+faststart', str(ready)])
            else:
                joined.replace(ready)
            actual = _frame_signatures(ready)
            if actual[:len(expected)] != expected or len(actual) <= len(expected):
                raise ValueError('成片前段逐帧一致性校验失败，未发布；基础片已保留。')
            final = probe(ready)
            if abs(final['duration']-target_duration)>1/final['fps']+.01:
                raise ValueError('续写成片时长校验未通过，已保留基础片。')
            ready.replace(target)
            return target
    except (OSError,subprocess.SubprocessError):
        raise ValueError('续写成片处理失败，已保留基础片，请检查磁盘空间并恢复任务。') from None
