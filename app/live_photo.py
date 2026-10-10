"""Preserve Live Photo motion/audio in browser- and model-compatible MP4 segments."""
import subprocess

import imageio_ffmpeg

from .media_errors import MediaPipelineError
from .person_video import probe


def normalize_segments(paths, directory):
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    first = probe(paths[0])
    # Use one canvas/timebase so multiple moving photos can be concatenated.
    factor = min(1, 1920/max(first['width'], first['height']))
    width = max(2, int(first['width']*factor)//2*2)
    height = max(2, int(first['height']*factor)//2*2)
    outputs = []
    for index, source in enumerate(paths):
        info = probe(source)
        target = directory/f'live-{index}.mp4'
        check = subprocess.run([ffmpeg, '-nostdin', '-v', 'error', '-protocol_whitelist', 'file,pipe',
                                '-i', str(source), '-map', '0:a:0', '-t', '0.01', '-f', 'null', '-'],
                               capture_output=True, timeout=30)
        command = [ffmpeg, '-nostdin', '-v', 'error', '-y', '-protocol_whitelist', 'file,pipe',
                   '-i', str(source)]
        if check.returncode:
            command += ['-f', 'lavfi', '-i', 'anullsrc=r=44100:cl=stereo']
        command += ['-map', '0:v:0', '-map', '1:a:0' if check.returncode else '0:a:0',
                    '-vf', f'scale={width}:{height}:force_original_aspect_ratio=decrease:force_divisible_by=2,'
                           f'pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,tpad=stop_mode=clone:stop_duration=0.1,fps=30:start_time=0',
                    '-af', f"asetpts=PTS-STARTPTS,apad,atrim=duration={info['duration']:.10f}",
                    '-frames:v', str(max(1, round(info['duration']*30))), '-c:v', 'libx264', '-preset', 'fast',
                    '-threads', '1', '-crf', '18', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-ar', '44100',
                    '-ac', '2', '-movflags', '+faststart', str(target)]
        result = subprocess.run(command, capture_output=True, timeout=300)
        if result.returncode or not target.is_file() or not target.stat().st_size:
            raise MediaPipelineError('实况照片动态视频转换失败，请稍后重试或上传本地视频。')
        outputs.append(target)
    return outputs
