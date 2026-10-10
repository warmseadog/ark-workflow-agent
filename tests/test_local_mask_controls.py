from pathlib import Path
import json
import pytest
from app import main, media, local_mosaic, redaction_test
from tests.test_redaction_settings import client


@pytest.mark.parametrize('mode',['face','face_hair_primary','hair_primary','face_hair_all'])
def test_local_modes_persist_and_reach_new_draft(client,mode):
    old=client.post('/api/production/drafts',json={}).json()
    result=client.put('/api/redaction-settings',json={'profile':'local','values':{
        'mask_mode':mode,'blur_style':'mosaic','mosaic_size':48,'keep_audio':False,
        'local_options':{'hair_mosaic_size':36,'detection_width':640,'tracking_width':1280,
                         'hair_update_hz':12.0,'primary_confidence':0.3,'score_threshold':0.7,'hold_frames':18,'encoder':'libx264'}}})
    assert result.status_code==200,result.text
    saved=result.json()['config']
    assert saved['mask_mode']==mode and saved['local_options']['hair_mosaic_size']==36
    assert client.get('/api/redaction-settings').json()['profiles']['local']==saved
    assert client.post('/api/production/drafts',json={}).json()['mask']==saved
    assert client.get('/api/production/drafts/'+old['id']).json()['mask']==old['mask']
    _,_,options=redaction_test._configuration(main.settings,'local',json.dumps(saved),'{}')
    assert options.mask_mode==mode
    if mode!='face':
        command=local_mosaic.build_local_mosaic_command(Path('source.mp4'),Path('out.mp4'),options)
        assert command[command.index('--mosaic-size')+1]=='48'
        assert command[command.index('--hair-mosaic-size')+1]=='36'
        assert command[command.index('--detection-width')+1]=='640'
        assert '--no-audio' in command
        flag='--score-threshold' if mode=='face_hair_all' else '--primary-confidence'
        assert float(command[command.index(flag)+1])==(.7 if mode=='face_hair_all' else .3)


@pytest.mark.parametrize('values',[{'hair_update_hz':0},{'hair_mosaic_size':101},{'tracking_width':True},
    {'encoder':'arbitrary-command'},{'hold_frames':-1},{'primary_confidence':2},{'unknown':1}])
def test_bad_local_parameters_never_change_defaults(client,values):
    before=client.get('/api/redaction-settings').json()
    result=client.put('/api/redaction-settings',json={'profile':'local','values':{'local_options':values}})
    assert result.status_code==422
    assert client.get('/api/redaction-settings').json()==before


def test_old_task_keeps_original_hair_command():
    options=media.BlurOptions(mask_mode='hair_primary',robust_tracking=True)
    command=local_mosaic.build_local_mosaic_command(Path('in.mp4'),Path('out.mp4'),options)
    assert '--robust' in command and '--hair-only' in command
    assert '--mosaic-size' not in command


def test_http_profile_does_not_enable_local_hair_modes(client):
    result=client.put('/api/redaction-settings',json={'profile':'http','values':{'mask_mode':'face_hair_all'}})
    assert result.status_code==422


def test_pixel_granularity_changes_both_face_and_hair_output(monkeypatch):
    import numpy as np
    monkeypatch.syspath_prepend(str(local_mosaic.SCRIPT_DIR))
    from process_primary_face_mosaic import mosaic
    from hair_mosaic import mosaic_hair
    source=np.random.default_rng(42).integers(0,255,(120,120,3),dtype=np.uint8)
    polygon=np.array([[20,20],[100,20],[100,100],[20,100]],dtype=np.int32)
    fine=mosaic(source.copy(),polygon,4)
    coarse=mosaic(source.copy(),polygon,60)
    assert not np.array_equal(fine,coarse)
    assert np.array_equal(coarse[:8],source[:8])
    assert coarse[40:80,40:80].var()<fine[40:80,40:80].var()
    mask=np.zeros((120,120),dtype=np.uint8);mask[20:100,20:100]=1
    fine=mosaic_hair(source.copy(),mask,mosaic_size=4)
    coarse=mosaic_hair(source.copy(),mask,mosaic_size=60)
    assert not np.array_equal(fine,coarse)
    assert coarse[40:80,40:80].var()<fine[40:80,40:80].var()


@pytest.mark.parametrize('mode',['face_hair_primary','hair_primary','face_hair_all'])
@pytest.mark.parametrize('keep_audio',[False,True])
def test_real_local_processor_creates_decodable_video_with_audio_choice(tmp_path,mode,keep_audio):
    import subprocess, imageio_ffmpeg, cv2
    import sys, mediapipe
    if mode!='face_hair_all' and sys.platform=='win32' and not mediapipe.__file__.isascii():
        pytest.skip('MediaPipe legacy graph loader cannot read a Unicode Windows installation path; exercised on Linux release host')
    ffmpeg=imageio_ffmpeg.get_ffmpeg_exe()
    import skimage
    portrait=tmp_path/'portrait.png'
    portrait.write_bytes((Path(skimage.__file__).parent/'data/astronaut.png').read_bytes())
    source=tmp_path/'input.mp4'
    subprocess.run([ffmpeg,'-y','-loglevel','error','-loop','1','-framerate','10','-i',str(portrait),
                    '-f','lavfi','-i','sine=frequency=500:duration=0.4','-t','0.4','-pix_fmt','yuv420p','-c:v','libx264','-c:a','aac','-shortest',str(source)],check=True,capture_output=True)
    output=tmp_path/'output.mp4'
    options=media.BlurOptions(mask_mode=mode,mosaic_size=36,keep_audio=keep_audio,robust_tracking=True,
                             local_options=media.LocalMosaicOptions(encoder='libx264',detection_width=540))
    processed=subprocess.run(local_mosaic.build_local_mosaic_command(source,output,options),cwd=local_mosaic.SCRIPT_DIR,capture_output=True,text=True,timeout=60)
    assert processed.returncode==0,processed.stderr
    details=json.loads(processed.stdout.strip().splitlines()[-1])
    assert details.get('detected',details.get('direct_face_detections',0))>0
    assert details['hair_segmentation_refreshes']>0
    cap=cv2.VideoCapture(str(output));ok,frame=cap.read();frames=cap.get(cv2.CAP_PROP_FRAME_COUNT);cap.release()
    assert ok and frame.shape[:2]==(512,512) and frames==4
    audio=subprocess.run([ffmpeg,'-v','error','-i',str(output),'-map','0:a:0','-f','null','-'],capture_output=True)
    assert (audio.returncode==0)==keep_audio
