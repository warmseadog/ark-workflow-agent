import pytest
from app.production_store import ProductionStore
from app.reference_media import publish_video, get_video


def test_only_durable_base_artifact_can_be_shared(tmp_path):
    store=ProductionStore(tmp_path);draft=store.create_draft({})
    run=store.create_run(draft['id'],1,'x',{})
    work=tmp_path/'work'/run['id'];work.mkdir(parents=True)
    base=work/'base.mp4';base.write_bytes(b'generated')
    with pytest.raises(ValueError):publish_video(base,tmp_path,'https://studio.example')
    store.update_continuation(run['id'],base_ready=True)
    url=publish_video(base,tmp_path,'https://studio.example')
    assert get_video(url.rsplit('/',1)[-1],tmp_path)==base.resolve()
    raw=work/'source.mp4';raw.write_bytes(b'raw')
    with pytest.raises(ValueError):publish_video(raw,tmp_path,'https://studio.example')
    nested=work/'other';nested.mkdir();(nested/'base.mp4').write_bytes(b'raw')
    with pytest.raises(ValueError):publish_video(nested/'base.mp4',tmp_path,'https://studio.example')


def test_tos_upload_accepts_only_recorded_base(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from app import storage_settings
    from app.continuation import publish_base
    store=ProductionStore(tmp_path);draft=store.create_draft({})
    run=store.create_run(draft['id'],1,'x',{})
    work=tmp_path/'work'/run['id'];work.mkdir(parents=True)
    base=work/'base.mp4';base.write_bytes(b'generated')
    calls=[]
    class Tos:
        def put_object_from_file(self,bucket,key,path,**kwargs):calls.append((key,path))
        def pre_signed_url(self,*a,**kw):return SimpleNamespace(signed_url='https://tos/signed')
        def close(self):pass
    monkeypatch.setattr(storage_settings,'make_client',lambda *a:Tos())
    cfg=storage_settings.StorageConfig(enabled=True,access_key='ak',secret_key='sk',bucket='bucket')
    with pytest.raises(ValueError):publish_base(base,SimpleNamespace(storage_dir=tmp_path),None,cfg)
    store.update_continuation(run['id'],base_ready=True)
    assert publish_base(base,SimpleNamespace(storage_dir=tmp_path),None,cfg)=='https://tos/signed'
    assert calls[0][0].endswith('/base.mp4')
    assert calls[0][1]==str(base.resolve())
