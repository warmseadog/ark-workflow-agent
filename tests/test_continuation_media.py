import subprocess
import imageio_ffmpeg
import pytest
from app import continuation_media as media
from app.person_video import probe


def video(path, seconds):
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(),'-v','error','-y','-f','lavfi','-i',f'testsrc2=size=160x120:rate=24:duration={seconds}',
                    '-c:v','libx264','-threads','1',str(path)],check=True,capture_output=True)
    return path


def test_end_frames_have_ordered_real_timestamps(tmp_path):
    source=video(tmp_path/'base.mp4',5)
    frames=media.ending_frames(source,tmp_path/'frames')
    assert len(frames)==8
    assert 2 <= frames[0]['timestamp'] < frames[-1]['timestamp'] < 5
    assert all(x['path'].is_file() for x in frames)
    assert len({x['timestamp'] for x in frames})==8


def test_finalize_trims_fractional_total_without_slowing(tmp_path):
    source=video(tmp_path/'extension.mp4',9)
    out=media.finalize(source,tmp_path/'final.mp4',8.7)
    assert abs(probe(out)['duration']-8.7)<1/24+.001


def test_short_or_tail_only_result_does_not_become_success(tmp_path):
    source=video(tmp_path/'extension.mp4',4)
    with pytest.raises(ValueError):media.finalize(source,tmp_path/'final.mp4',9)
    assert not (tmp_path/'final.mp4').exists()
