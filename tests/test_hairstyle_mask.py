from dataclasses import replace
from pathlib import Path
import hashlib
import numpy as np
import pytest
from PIL import Image
from app.config import settings
from app import hairstyle_mask as hair


def asset_at(tmp_path):
    path=tmp_path/'assets'/'hair.png';path.parent.mkdir()
    frame=np.indices((100,100)).sum(axis=0).astype(np.uint8)
    rgb=np.stack([frame*2,frame,255-frame],axis=2)
    Image.fromarray(rgb).save(path)
    return {'kind':'hairstyle','path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()},rgb


def test_default_mask_preserves_pixels_outside_face_and_ellipse_corners():
    original=np.random.default_rng(7).integers(0,256,(100,100,3),dtype=np.uint8)
    processed=hair.mask_faces(original.copy(),[[30,40,70,80,.9]],1.0)
    assert np.array_equal(processed[:40],original[:40])
    assert np.array_equal(processed[:, :30],original[:, :30])
    assert np.array_equal(processed[40,30],original[40,30])
    assert not np.array_equal(processed[50:70,40:60],original[50:70,40:60])
    expanded=hair.mask_faces(original.copy(),[[30,40,70,80,.9]],1.4)
    assert not np.array_equal(expanded[30:40,45:55],original[30:40,45:55])


def test_preview_cache_keeps_original_and_keys_both_settings(tmp_path,monkeypatch):
    asset,original=asset_at(tmp_path);cfg=replace(settings,storage_dir=tmp_path)
    seen=[]
    monkeypatch.setattr(hair,'_detect_faces',lambda frame,threshold:seen.append(threshold) or [[30,40,70,80,.9]])
    first=hair.process_hairstyle(cfg,asset)
    again=hair.process_hairstyle(cfg,asset,{'mask_scale':1,'threshold':.2})
    assert first['path']!=again['path'] and seen==[.2,.2]
    assert first['settings']=={'mask_scale':1.,'threshold':.2} and first['faces_detected']==1
    assert np.array_equal(np.array(Image.open(asset['path'])),original)
    changed=hair.process_hairstyle(cfg,asset,{'mask_scale':1.05,'threshold':.15})
    assert changed['path']!=first['path'] and seen==[.2,.2,.15]
    Path(asset['path']).write_bytes(b'changed')
    with pytest.raises(ValueError):hair.process_hairstyle(cfg,asset)


def test_no_face_and_detection_failure_are_distinct(tmp_path,monkeypatch):
    asset,original=asset_at(tmp_path);cfg=replace(settings,storage_dir=tmp_path)
    monkeypatch.setattr(hair,'_detect_faces',lambda *args:[])
    result=hair.process_hairstyle(cfg,asset)
    assert result['faces_detected']==0
    assert np.array_equal(np.array(Image.open(result['path'])),original)
    def failed(*args):raise RuntimeError('detector failed')
    monkeypatch.setattr(hair,'_detect_faces',failed)
    with pytest.raises(hair.MediaPipelineError):hair.process_hairstyle(cfg,asset,{'mask_scale':1.05})


@pytest.mark.parametrize('damage', ['bytes', 'legacy_metadata'])
def test_mask_cache_verifies_output_and_rebuilds_unverified_entries(tmp_path, monkeypatch, damage):
    import json
    asset, _ = asset_at(tmp_path)
    asset['id'] = 'hair-source'
    cfg = replace(settings, storage_dir=tmp_path)
    monkeypatch.setattr(hair, '_detect_faces', lambda *args: [[30,40,70,80,.9]])
    first = hair.process_hairstyle(cfg, asset)
    expected = first['path'].read_bytes()
    if damage == 'bytes':
        first['path'].write_bytes(Path(asset['path']).read_bytes())
    else:
        first['path'].with_suffix('.json').write_text(json.dumps({'faces_detected': 1, 'settings': first['settings']}))
    rebuilt = hair.process_hairstyle(cfg, asset)
    assert rebuilt['path'].read_bytes() == expected
    assert rebuilt['output_sha256'] == hashlib.sha256(expected).hexdigest()
    assert rebuilt['source_sha256'] == asset['sha256']
    assert rebuilt['output_sha256'] != asset['sha256']
    assert rebuilt['source_asset_id'] == 'hair-source'
    # An independent upload is processed independently without inheriting an ID.
    again = hair.process_hairstyle(cfg, {**asset, 'id': 'second-upload'})
    assert again['source_asset_id'] == 'second-upload'


def test_preview_api_validates_kind_and_returns_local_image(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from app import main
    from app.production_store import ProductionStore
    asset,_=asset_at(tmp_path);monkeypatch.setattr(main,'settings',replace(settings,storage_dir=tmp_path))
    store=ProductionStore(tmp_path);path=Path(asset['path'])
    store.add_asset('hair','hair.png','hairstyle',path,path.stat().st_size,'image/png',asset['sha256'])
    store.add_asset('face','face.png','face',path,path.stat().st_size,'image/png',asset['sha256'])
    monkeypatch.setattr(hair,'_detect_faces',lambda *args:[[30,40,70,80,.9]])
    client=TestClient(main.app)
    data=client.post('/api/production/hairstyle/preview',json={'asset_id':'hair'}).json()
    assert data['faces_detected']==1 and data['settings']['mask_scale']==1
    assert client.get(data['url']).headers['content-type']=='image/png'
    assert client.get(data['url']).headers['cache-control']=='private, no-store'
    assert 'path' not in data
    assert client.post('/api/production/hairstyle/preview',json={'asset_id':'face'}).status_code==422
    assert client.get('/api/production/hairstyle/preview/not-a-key').status_code==404
