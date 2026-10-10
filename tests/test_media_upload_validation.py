from dataclasses import replace
from io import BytesIO
import pytest
from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.testclient import TestClient
from app import main
from app.workflow_router import get_router
from app.workflow_store import WorkflowStore
from tests.media_fixtures import image_bytes, video_bytes

@pytest.fixture
def client(tmp_path,monkeypatch):
    monkeypatch.setattr(main,'settings',replace(main.settings,storage_dir=tmp_path))
    return TestClient(main.app)

@pytest.mark.parametrize('kind,name,data',[
    ('face','face.png',b'fixture'),('video','video.mp4',b'fixture'),
    ('video','image.mp4',image_bytes()),('face','face.jpg',image_bytes()),
    ('face','large.png',image_bytes(size=(17000,1)))])
def test_production_rejects_invalid_media_without_persisting(client,tmp_path,kind,name,data):
    response=client.post('/api/production/assets',data={'kind':kind},files={'file':(name,data)})
    assert response.status_code==422
    assert not list((tmp_path/'assets').glob('*'))

@pytest.mark.parametrize('kind,name,data',[('face','real.png',image_bytes()),('video','real.mp4',video_bytes())])
def test_real_upload_is_preserved(client,kind,name,data):
    response=client.post('/api/production/assets',data={'kind':kind},files={'file':(name,data)})
    assert response.status_code==200,response.text
    assert client.get(response.json()['url']).content==data

def test_import_rejects_text_disguised_as_video(client,tmp_path,monkeypatch):
    def download(text,path,settings):
        path.write_bytes(b'fixture')
        return path
    monkeypatch.setattr(main.media,'download_video',download)
    response=client.post('/api/production/assets/import',json={'text':'https://example.test/video.mp4'})
    assert response.status_code==422
    assert not list((tmp_path/'assets').glob('*'))

@pytest.mark.parametrize('route,field,name,mime',[('source-upload','video','source.mp4','video/mp4'),('source-upload','video','source.txt','video/mp4'),('material-upload','material','face.png','image/png')])
@pytest.mark.parametrize('data',[b'fixture',b''])
def test_workflow_rejects_invalid_media(tmp_path,monkeypatch,route,field,name,mime,data):
    monkeypatch.setenv('WORKFLOW_STORAGE',str(tmp_path/'files'))
    api=FastAPI()
    api.include_router(get_router(WorkflowStore(tmp_path/'workflow.sqlite3')))
    client=TestClient(api)
    project=client.post('/api/workflow/projects',json={'name':'upload'}).json()['id']
    response=client.post(f'/api/workflow/projects/{project}/{route}',data={'kind':'face'},files={field:(name,data,mime)})
    assert response.status_code in {415,422}
    assert not [p for p in (tmp_path/'files').rglob('*') if p.is_file()]

@pytest.mark.parametrize('data,limit',[(b'fixture',100),(b'',100),(image_bytes(),1)])
def test_legacy_rejects_invalid_upload_and_removes_file(tmp_path,data,limit):
    target=tmp_path/'face.png'
    with pytest.raises(HTTPException):main._save_upload(UploadFile(filename='face.png',file=BytesIO(data)),target,limit)
    assert not target.exists()


def test_registration_failure_cleans_up_copied_media(client, tmp_path, monkeypatch):
    from app.production_store import ProductionStore
    def fail(*args, **kwargs):
        raise ValueError('registration failed')
    monkeypatch.setattr(ProductionStore, 'add_asset', fail)
    response = client.post('/api/production/assets', data={'kind':'face'}, files={'file':('face.png',image_bytes())})
    assert response.status_code == 422
    assert not list((tmp_path/'assets').glob('*'))


def test_legacy_stream_error_removes_partial_file_and_hides_internal_message(tmp_path):
    class BrokenStream:
        def read(self, size):
            raise OSError('secret token and internal storage path')
    target = tmp_path/'face.png'
    with pytest.raises(HTTPException) as caught:
        main._save_upload(UploadFile(filename='face.png',file=BrokenStream()),target,100)
    assert caught.value.status_code == 500
    assert 'secret' not in caught.value.detail
    assert not target.exists()


@pytest.mark.parametrize('extension,format', [('png','PNG'),('jpg','JPEG'),('webp','WEBP'),('gif','GIF'),('bmp','BMP'),('tiff','TIFF')])
def test_supported_decodable_image_formats(client, extension, format):
    response=client.post('/api/production/assets',data={'kind':'face'},files={'file':('face.'+extension,image_bytes(format))})
    assert response.status_code==200,response.text


@pytest.mark.parametrize('name,data', [('broken.png', image_bytes()[:50]),('broken.mp4', video_bytes()[:64])])
def test_truncated_container_is_rejected(client,name,data):
    response=client.post('/api/production/assets',data={'kind':'video' if name.endswith('.mp4') else 'face'},files={'file':(name,data)})
    assert response.status_code==422


def test_multipage_tiff_cumulative_dimensions_are_bounded(client):
    from PIL import Image
    stream=BytesIO()
    first=Image.new('L',(1,1))
    large=Image.new('L',(6000,6000))
    first.save(stream,format='TIFF',save_all=True,append_images=[large]*3,compression='tiff_deflate')
    response=client.post('/api/production/assets',data={'kind':'face'},files={'file':('pages.tiff',stream.getvalue())})
    assert response.status_code==422
