"""Dice previews are short, varied, persisted separately, and never submit video."""
import json

import httpx
import pytest

from app import main, variation_settings
from app.production_store import ProductionStore
from tests.test_production_api import client, complete_draft, asset
from tests.test_access_control import protected, accounts_clients

TEXT = '人物自然向前走几步后停下，轻轻侧身展示穿搭，镜头保持中景，整体节奏舒缓自然。'


def provider(monkeypatch, texts=None):
    calls = []
    values = iter(texts or [TEXT])

    async def send(self, request, **kwargs):
        calls.append(json.loads(request.content))
        return httpx.Response(200, request=request, json={'choices': [
            {'finish_reason': 'stop', 'message': {'content': next(values)}}]})

    monkeypatch.setattr(httpx.AsyncClient, 'send', send)
    variation_settings.save_config(main.settings, {'api_key': 'secret-test'})
    return calls


def preview(client, draft):
    return client.post('/api/production/inspiration-random', json={
        'draft_id': draft['id'], 'revision': draft['revision']})


def test_preview_uses_materials_without_creating_run_or_changing_draft(client, monkeypatch):
    draft = complete_draft(client)
    calls = provider(monkeypatch)
    response = preview(client, draft)
    assert response.status_code == 200, response.text
    assert response.json()['inspiration'] == TEXT
    assert response.json()['style']
    assert not ProductionStore(main.settings.storage_dir).list_runs()
    assert client.get('/api/production/drafts/' + draft['id']).json() == draft
    content = calls[0]['messages'][1]['content']
    assert any(x['type'] == 'image_url' for x in content)
    assert '20～100' in calls[0]['messages'][0]['content']
    assert 'secret-test' not in response.text


def test_dice_rotates_styles_across_requests_and_rejects_repetition(client, monkeypatch):
    from app.inspiration_preview import STYLES
    draft = complete_draft(client)
    # Different real content; the second response repeats and must be rejected.
    texts = [TEXT, TEXT, '以固定全身构图表现简洁利落的穿搭，人物站定后轻轻调整重心，在自然停顿中展示整体轮廓。']
    calls = provider(monkeypatch, texts)
    first, second = preview(client, draft), preview(client, draft)
    assert first.status_code == second.status_code == 200
    assert first.json()['style'] != second.json()['style']
    assert first.json()['inspiration'] != second.json()['inspiration']
    assert len(calls) == 3
    store = ProductionStore(main.settings.storage_dir)
    assert len(store.inspiration_history()) == 2
    assert len(STYLES) >= 6


@pytest.mark.parametrize('text', ['太短', '字' * 101, '1. ' + TEXT, TEXT + '\n' + TEXT])
def test_invalid_preview_text_preserves_draft(client, monkeypatch, text):
    draft = complete_draft(client)
    provider(monkeypatch, [text, text])
    response = preview(client, draft)
    assert response.status_code == 502
    assert client.get('/api/production/drafts/' + draft['id']).json() == draft
    assert not ProductionStore(main.settings.storage_dir).list_runs()


def test_missing_or_stale_draft_rejected_before_provider(client, monkeypatch):
    calls = provider(monkeypatch)
    draft = complete_draft(client)
    assert preview(client, {**draft, 'revision': 1}).status_code == 409
    assert preview(client, {**draft, 'id': 'missing'}).status_code == 404
    assert not calls


def test_inspiration_draft_roundtrip_and_guided_snapshot(client, monkeypatch):
    provider(monkeypatch)
    draft = complete_draft(client)
    result = client.put('/api/production/drafts/' + draft['id'], json={
        'revision': draft['revision'], 'inspiration': TEXT})
    assert result.status_code == 200, result.text
    saved = result.json()
    assert client.get('/api/production/drafts/' + draft['id']).json()['inspiration'] == TEXT
    run = client.post('/api/production/runs', json={'draft_id': saved['id'],
        'revision': saved['revision'], 'idempotency_key': 'confirmed-preview',
        'variation': {'inspiration': TEXT, 'creation_mode': 'guided'}})
    assert run.status_code == 200, run.text
    assert run.json()['confirmed_inspiration'] == TEXT
    assert run.json()['variation']['creation_mode'] == 'guided'


def test_preview_requires_authentication(protected):
    assert protected.post('/api/production/inspiration-random', json={
        'draft_id': 'anything', 'revision': 1}).status_code in (401, 403)


def test_user_cannot_preview_another_users_draft(accounts_clients, monkeypatch):
    _, _, (admin, alice, bob) = accounts_clients
    admin.put('/api/variation-settings', json={'api_key': 'private-key'})
    draft = complete_draft(alice)
    assert preview(bob, draft).status_code == 404


def test_random_uses_visual_provider_and_real_duration_with_optional_references(client, monkeypatch):
    draft = complete_draft(client)
    shoes = asset(client, 'shoes', 'shoes.png')
    scene = asset(client, 'scene', 'scene.png')
    draft = client.put('/api/production/drafts/' + draft['id'], json={
        'revision':draft['revision'], 'shoes_asset_ids':[shoes['id']], 'shoes_enabled':True,
        'scene_asset_ids':[scene['id']], 'scene_enabled':True}).json()
    calls = provider(monkeypatch)
    client.put('/api/inspiration-settings', json={'inherit_provider':False,
        'model':'text-only', 'api_key':'text-key'})
    assert preview(client, draft).status_code == 200
    assert calls[0]['model'] != 'text-only'
    content = calls[0]['messages'][1]['content']
    context = json.loads(content[0]['text'])
    assert context['视频时长（秒）'] > 0
    labels = [item['text'] for item in content if item['type']=='text']
    assert any('独立鞋子参考' in label for label in labels)
    assert any('独立场景参考' in label for label in labels)


def test_valid_gif_clothing_is_supported(client, monkeypatch):
    import io
    from PIL import Image
    draft = complete_draft(client)
    buffer = io.BytesIO()
    Image.new('RGB',(80,80),'red').save(buffer,format='GIF')
    response = client.post('/api/production/assets',data={'kind':'clothing'},files={
        'file':('dress.gif',buffer.getvalue(),'image/gif')})
    assert response.status_code == 200, response.text
    draft = client.put('/api/production/drafts/' + draft['id'], json={
        'revision':draft['revision'], 'clothing_asset_ids':[response.json()['id']]}).json()
    provider(monkeypatch)
    assert preview(client, draft).status_code == 200


def test_shared_workspace_lease_prevents_parallel_style_reuse(client, monkeypatch):
    from app.queue_admission import _lease
    draft = complete_draft(client)
    calls = provider(monkeypatch)
    lease = _lease(main.settings.storage_dir / '.inspiration-preview.lock')
    try:
        assert preview(client, draft).status_code == 429
        assert not calls
    finally:
        lease.close()
    assert preview(client, draft).status_code == 200


def test_disabled_visual_planner_prevents_random_calls(client, monkeypatch):
    draft = complete_draft(client)
    calls = provider(monkeypatch)
    variation_settings.save_config(main.settings, {'enabled':False})
    assert preview(client,draft).status_code == 503
    assert not calls


def test_eight_clicks_cover_different_styles_and_history_survives_new_store(client, monkeypatch):
    draft = complete_draft(client)
    texts = [
        TEXT,
        '采用简洁的固定全身构图，人物站定并轻轻调整重心，用干净停顿表现穿搭轮廓。',
        '以轻快的步伐穿过画面，镜头小幅跟随移动，在利落的停步中结束展示。',
        '选取衣服上清楚可见的一处细节，用近景配合轻微手部调整，表现面料的层次。',
        '利用画面留白突出人物姿态，全程保持固定机位，只安排一次自然的视线变化。',
        '镜头像偶然捕捉到一个生活瞬间，人物放松手臂自然停留，不刻意面向镜头摆拍。',
        '通过舒展的肩背与连贯的手臂姿态表现优雅气质，动作平缓克制，自然收尾。',
        '人物以自信稳健的站姿面对镜头，下颌微抬后目光落定，以有力量的短暂停顿结束。',
    ]
    calls = provider(monkeypatch,texts)
    results = [preview(client,draft) for _ in texts]
    assert all(result.status_code == 200 for result in results)
    assert len({result.json()['style'] for result in results}) == 8
    assert len(calls) == 8
    assert len(ProductionStore(main.settings.storage_dir).inspiration_history()) == 8
    assert not ProductionStore(main.settings.storage_dir).list_runs()
