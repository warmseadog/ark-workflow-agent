import pytest
from app.source_clip import normalize_clip, validate_source


@pytest.mark.parametrize('value', [{}, {'start':-1,'duration':8}, {'start':float('nan'),'duration':8}, {'start':0,'duration':1}, {'start':0,'duration':31}, {'start':True,'duration':8}, {'start':0,'duration':True}, {'start':0,'duration':8,'path':'x'}])
def test_reject_invalid_clip(value):
    with pytest.raises(ValueError): normalize_clip(value)


def test_clip_bounds_use_selected_duration(tmp_path, monkeypatch):
    from app import source_clip
    video=tmp_path/'source.mp4';video.write_bytes(b'fixture')
    monkeypatch.setattr(source_clip,'probe',lambda p:{'duration':60,'fps':25,'width':1280,'height':720})
    result=validate_source(video,{'start':50,'duration':8},max_seconds=30)
    assert result['duration']==8 and result['source_duration']==60
    with pytest.raises(ValueError,match='超出'):
        validate_source(video,{'start':55,'duration':8},max_seconds=30)
    with pytest.raises(ValueError):
        validate_source(video,{'start':0,'duration':20},max_seconds=15)


def test_old_draft_keeps_full_source():
    assert normalize_clip(None) is None
    assert normalize_clip({'start':2.5,'duration':8}) == {'start':2.5,'duration':8}


def test_fractional_duration_and_explicit_slow_extension(tmp_path,monkeypatch):
    from app import source_clip
    path=tmp_path/'video.mp4';path.write_bytes(b'video')
    monkeypatch.setattr(source_clip,'probe',lambda p:{'duration':7.7,'fps':30,'width':720,'height':1280})
    assert normalize_clip({'start':0,'duration':7.7})['duration']==7.7
    assert validate_source(path,{'start':0,'duration':9,'retime':'slow'})['duration']==9
    with pytest.raises(ValueError):validate_source(path,{'start':0,'duration':9})


def test_real_slow_extension_preserves_original_and_audio(tmp_path):
    import subprocess, imageio_ffmpeg
    from app.source_clip import clip_video
    binary=imageio_ffmpeg.get_ffmpeg_exe();source=tmp_path/'source.mp4';target=tmp_path/'slow.mp4'
    subprocess.run([binary,'-y','-v','error','-f','lavfi','-i','testsrc2=s=320x240:r=30:d=3',
                    '-f','lavfi','-i','sine=frequency=440:duration=3','-c:v','libx264','-c:a','aac',str(source)],check=True,capture_output=True)
    original=source.read_bytes()
    clip_video(source,target,{'start':0,'duration':5,'retime':'slow'})
    assert abs(validate_source(target)['duration']-5)<.15
    assert b'Audio:' in subprocess.run([binary,'-i',str(target)],capture_output=True).stderr
    assert source.read_bytes()==original


def test_slow_silent_short_video_reaches_maximum_duration(tmp_path):
    import subprocess, imageio_ffmpeg
    from app.source_clip import clip_video
    source=tmp_path/'short.mp4';target=tmp_path/'long.mp4'
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(),'-y','-v','error','-f','lavfi','-i',
                    'color=blue:s=320x240:r=24:d=2','-c:v','libx264',str(source)],check=True,capture_output=True)
    clip_video(source,target,{'start':0,'duration':30,'retime':'slow'})
    assert abs(validate_source(target)['duration']-30)<.1


def test_real_cut_selects_requested_frames_and_preserves_audio(tmp_path):
    import subprocess
    import cv2
    import imageio_ffmpeg
    from app.source_clip import clip_video
    binary=imageio_ffmpeg.get_ffmpeg_exe()
    source=tmp_path/'source.mp4';target=tmp_path/'clip.mp4'
    subprocess.run([binary,'-y','-v','error','-f','lavfi','-i','color=red:s=320x240:r=25:d=3',
                    '-f','lavfi','-i','color=blue:s=320x240:r=25:d=7','-f','lavfi','-i','sine=frequency=440:duration=10',
                    '-filter_complex','[0:v][1:v]concat=n=2:v=1:a=0[v]','-map','[v]','-map','2:a',
                    '-c:v','libx264','-c:a','aac',str(source)],check=True,capture_output=True)
    original=source.read_bytes()
    clip_video(source,target,{'start':4,'duration':4})
    assert abs(validate_source(target)['duration']-4)<.1
    capture=cv2.VideoCapture(str(target));ok,frame=capture.read();capture.release()
    assert ok and frame[:,:,0].mean()>200 and frame[:,:,2].mean()<30
    info=subprocess.run([binary,'-i',str(target)],capture_output=True).stderr
    assert b'Audio:' in info and source.read_bytes()==original
