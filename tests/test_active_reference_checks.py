import pytest

from app import production_worker as worker, shared_portraits
from tests.test_production_worker import setup


@pytest.mark.parametrize('kind', ['hairstyle', 'scene', 'bag', 'hat'])
def test_disabled_reference_does_not_block_generation(setup, monkeypatch, kind):
    cfg, store, draft, private = setup
    checked=[]
    def check(settings, ident):
        checked.append(ident)
        if ident == 'removed-reference':
            raise LookupError('人物或照片已从素材库移除。')
    monkeypatch.setattr(shared_portraits, 'authorize_asset', check)
    draft=store.save_draft(draft['id'], draft['revision'], {kind+'_enabled':False, kind+'_asset_ids':['removed-reference']})
    run=store.create_run(draft['id'], draft['revision'], 'disabled-'+kind, private)
    worker.execute_run(cfg,store,store.claim_next())
    assert store.get_run(run['id'])['status']=='succeeded'
    assert 'removed-reference' not in checked


def test_inactive_person_video_does_not_block_image_reference(setup, monkeypatch):
    cfg, store, draft, private = setup
    def check(settings, ident):
        if ident == 'old-video': raise LookupError('旧视频已移除。')
    monkeypatch.setattr(shared_portraits, 'authorize_asset', check)
    draft=store.save_draft(draft['id'],draft['revision'],{'person_reference_mode':'image','person_video_asset_id':'old-video'})
    run=store.create_run(draft['id'],draft['revision'],'inactive-video',private)
    worker.execute_run(cfg,store,store.claim_next())
    assert store.get_run(run['id'])['status']=='succeeded'


def test_enabled_bad_reference_names_file_and_stops_before_processing(setup, monkeypatch):
    cfg, store, draft, private = setup
    path=cfg.storage_dir/'hair.jpg';path.write_bytes(b'hair')
    store.add_asset('hair','yoyo发型1.jpg','hairstyle',path,4,'image/jpeg','test-hash')
    def check(settings, ident):
        if ident=='hair':raise LookupError('人物或照片已从素材库移除。')
    monkeypatch.setattr(shared_portraits,'authorize_asset',check)
    monkeypatch.setattr(worker,'process_hairstyle',lambda *a:pytest.fail('Rejected image must not be processed'))
    monkeypatch.setattr(worker,'run_deface',lambda *a:pytest.fail('Rejected run must not preprocess video'))
    draft=store.save_draft(draft['id'],draft['revision'],{'hairstyle_enabled':True,'hairstyle_asset_ids':['hair']})
    run=store.create_run(draft['id'],draft['revision'],'rejected-hair',private)
    worker.execute_run(cfg,store,store.claim_next())
    result=store.get_run(run['id'])
    assert result['status']=='failed'
    assert '发型参考图' in result['error'] and 'yoyo发型1.jpg' in result['error'] and '已从素材库移除' in result['error']
    assert not result['provider_task_id']
