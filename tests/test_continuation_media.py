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
    base=video(tmp_path/'base.mp4',4)
    source=video(tmp_path/'extension.mp4',9)
    out=media.finalize(source,tmp_path/'final.mp4',8.7,base)
    assert abs(probe(out)['duration']-8.7)<1/24+.001


def test_short_or_tail_only_result_does_not_become_success(tmp_path):
    base=video(tmp_path/'base.mp4',4)
    source=video(tmp_path/'extension.mp4',4)
    with pytest.raises(ValueError):media.finalize(source,tmp_path/'final.mp4',9,base)
    assert not (tmp_path/'final.mp4').exists()


def hashes(path):
    data=subprocess.check_output([imageio_ffmpeg.get_ffmpeg_exe(),'-v','error','-i',str(path),
        '-map','0:v:0','-an','-fps_mode','passthrough','-f','framemd5','-'])
    return [line.split(b',')[-1].strip() for line in data.splitlines() if not line.startswith(b'#')]


def test_repainted_prefix_is_replaced_with_exact_base_frames(tmp_path):
    base=video(tmp_path/'base.mp4',4.041666666666667)
    source=tmp_path/'extension.mp4'
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(),'-v','error','-y','-f','lavfi','-i',
        'testsrc2=size=160x120:rate=24:duration=7', '-vf',
        "drawbox=color=red:t=fill:enable='lt(t,3)'",'-c:v','libx264','-threads','1',str(source)],check=True,capture_output=True)
    before=base.read_bytes()
    out=media.finalize(source,tmp_path/'final.mp4',6.7,base)
    original=hashes(base)
    assert hashes(out)[:len(original)]==original
    assert base.read_bytes()==before
    assert abs(probe(out)['duration']-6.7)<1/24+.001


def test_obvious_seam_mismatch_never_publishes_or_overwrites(tmp_path):
    base=video(tmp_path/'base.mp4',4)
    source=tmp_path/'extension.mp4';out=tmp_path/'final.mp4'
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(),'-v','error','-y','-f','lavfi','-i',
        'color=white:size=160x120:rate=24:duration=7','-c:v','libx264','-threads','1',str(source)],check=True,capture_output=True)
    out.write_bytes(b'previous-success')
    with pytest.raises(ValueError,match='接续'):
        media.finalize(source,out,7,base)
    assert out.read_bytes()==b'previous-success'
    assert base.exists() and source.exists()
    assert not list(tmp_path.glob('.continuation-*'))


@pytest.mark.parametrize('base_sound,extended_sound', [(True, True), (True, False), (False, True)])
def test_audio_content_preserved_and_video_prefix_exact(tmp_path,base_sound,extended_sound):
    import numpy as np
    ff=imageio_ffmpeg.get_ffmpeg_exe()
    def make(path,seconds,sound,frequency):
        args=[ff,'-v','error','-y','-f','lavfi','-i',f'testsrc2=size=160x120:rate=24:duration={seconds}']
        if sound:args+=['-f','lavfi','-i',f'sine=frequency={frequency}:sample_rate=48000:duration={seconds}']
        subprocess.run([*args,'-c:v','libx264','-threads','1','-c:a','aac',str(path)],check=True,capture_output=True)
        return path
    base=make(tmp_path/'base.mp4',4,base_sound,440)
    extended=make(tmp_path/'extended.mp4',7,extended_sound,880)
    out=media.finalize(extended,tmp_path/'final.mp4',7,base)
    assert hashes(out)[:len(hashes(base))]==hashes(base)
    for t,present,frequency in [(1,base_sound,440),(5,extended_sound,880)]:
        raw=subprocess.check_output([ff,'-v','error','-ss',str(t),'-i',str(out),'-t','0.5',
            '-map','0:a:0','-ac','1','-ar','48000','-f','f32le','-'])
        samples=np.frombuffer(raw,dtype=np.float32)
        if present:
            peak=np.fft.rfftfreq(len(samples),1/48000)[np.abs(np.fft.rfft(samples)).argmax()]
            assert abs(peak-frequency)<4
        else:assert np.abs(samples).max()<.001


def test_different_tail_size_and_fps_do_not_change_original(tmp_path):
    base=video(tmp_path/'base.mp4',4)
    source=tmp_path/'extended.mp4'
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(),'-v','error','-y','-f','lavfi','-i',
        'testsrc2=size=160x120:rate=30:duration=7','-vf','scale=320:240','-c:v','libx264','-threads','1',str(source)],check=True,capture_output=True)
    out=media.finalize(source,tmp_path/'final.mp4',7,base)
    assert hashes(out)[:len(hashes(base))]==hashes(base)
    assert probe(out)['fps']==24


def test_failed_prefix_verification_keeps_previous_output(tmp_path,monkeypatch):
    base=video(tmp_path/'base.mp4',4);source=video(tmp_path/'extended.mp4',7)
    out=tmp_path/'final.mp4';out.write_bytes(b'previous')
    real=media._frame_signatures
    def corrupted(path,**kwargs):
        values=real(path,**kwargs)
        return [('wrong-frame',),*values[1:]] if path.name=='ready.mp4' else values
    monkeypatch.setattr(media,'_frame_signatures',corrupted)
    with pytest.raises(ValueError,match='逐帧'):
        media.finalize(source,out,7,base)
    assert out.read_bytes()==b'previous'


@pytest.mark.parametrize('rate',['24','30000/1001','24000/1001'])
def test_track_timebase_and_fractional_fps_preserve_prefix(tmp_path,rate):
    def make(path,seconds):
        subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(),'-v','error','-y','-f','lavfi','-i',
            f'color=blue:size=160x120:rate={rate}:duration={seconds}','-c:v','libx264','-threads','1',
            '-video_track_timescale','90000',str(path)],check=True,capture_output=True)
        return path
    base=make(tmp_path/'base.mp4',4);source=make(tmp_path/'extended.mp4',7)
    out=media.finalize(source,tmp_path/'final.mp4',7,base)
    assert hashes(out)[:len(hashes(base))]==hashes(base)


def test_signatures_detect_changed_playback_timing(tmp_path):
    base=video(tmp_path/'base.mp4',4);slow=tmp_path/'slow.mp4'
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(),'-v','error','-y','-itsscale','2','-i',str(base),
        '-c','copy',str(slow)],check=True,capture_output=True)
    assert hashes(base)==hashes(slow)
    assert media._frame_signatures(base)!=media._frame_signatures(slow)


def test_delayed_audio_is_not_shifted_to_start(tmp_path):
    import numpy as np
    ff=imageio_ffmpeg.get_ffmpeg_exe();silent=video(tmp_path/'silent.mp4',4)
    base=tmp_path/'base.mp4';source=video(tmp_path/'extended.mp4',7)
    subprocess.run([ff,'-v','error','-y','-i',str(silent),'-itsoffset','0.6','-f','lavfi',
        '-i','sine=frequency=440:sample_rate=48000:duration=3.4','-c:v','copy','-c:a','aac',str(base)],check=True,capture_output=True)
    out=media.finalize(source,tmp_path/'final.mp4',7,base)
    raw=subprocess.check_output([ff,'-v','error','-i',str(out),'-t','1','-map','0:a:0','-ac','1','-ar','48000','-f','f32le','-'])
    samples=np.frombuffer(raw,dtype=np.float32)
    assert np.abs(samples[:24000]).max()<.001
    assert np.abs(samples[33600:]).max()>.01


def test_subframe_timing_jitter_is_detected_and_not_published(tmp_path):
    base=tmp_path/'base.mp4';jitter=tmp_path/'jitter.mp4'
    ff=imageio_ffmpeg.get_ffmpeg_exe()
    subprocess.run([ff,'-v','error','-y','-f','lavfi','-i','testsrc2=size=160x120:rate=24:duration=4',
        '-c:v','libx264','-threads','1','-bf','0',str(base)],check=True,capture_output=True)
    subprocess.run([ff,'-v','error','-y','-i',str(base),'-c','copy','-bsf:v',
        r'setts=pts=PTS+if(mod(N\,2)\,100\,0)',str(jitter)],check=True,capture_output=True)
    assert hashes(base)==hashes(jitter)
    assert media._frame_signatures(base)!=media._frame_signatures(jitter)
    source=video(tmp_path/'extended.mp4',7)
    with pytest.raises(ValueError,match='时间轴'):
        media.finalize(source,tmp_path/'final.mp4',7,jitter)
