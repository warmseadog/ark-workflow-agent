from dataclasses import replace

import pytest

from app.config import settings
from app import generation_settings, local_preferences
from app.production_store import ProductionStore


def test_user_storage_and_idempotency_are_isolated(tmp_path):
    from app.tenancy import user_settings
    base = replace(settings, storage_dir=tmp_path)
    a = user_settings(base, {'id': 'a'*32, 'legacy_owner': False})
    b = user_settings(base, {'id': 'b'*32, 'legacy_owner': False})
    assert a.storage_dir == tmp_path/'users'/('a'*32)
    assert b.storage_dir == tmp_path/'users'/('b'*32)
    one, two = ProductionStore(a.storage_dir), ProductionStore(b.storage_dir)
    draft = one.create_draft({'name': 'only A'})
    assert two.list_drafts() == []
    with pytest.raises(LookupError):
        two.get_draft(draft['id'])
    local_preferences.save_template(a, 'private', 'private text')
    assert 'private' not in [x['name'] for x in local_preferences.list_templates(b)]


def test_legacy_admin_keeps_data_and_global_configs(tmp_path):
    from app.tenancy import user_settings, root_settings
    base = replace(settings, storage_dir=tmp_path)
    admin = user_settings(base, {'id': 'a'*32, 'legacy_owner': True})
    ordinary = user_settings(base, {'id': 'b'*32, 'legacy_owner': False})
    assert admin.storage_dir == tmp_path
    assert root_settings(ordinary).storage_dir == tmp_path
    assert generation_settings.config_path(ordinary) == tmp_path/'private'/'generation-settings.json'
    local_preferences.save_tikhub_key(base, 'secret-global-key')
    assert local_preferences.get_tikhub_key(ordinary) == 'secret-global-key'


def test_tenant_id_cannot_escape_storage(tmp_path):
    from app.tenancy import user_settings
    with pytest.raises(ValueError):
        user_settings(replace(settings, storage_dir=tmp_path), {'id':'../../escape','legacy_owner':False})


def test_shared_templates_are_read_only_for_users(tmp_path):
    from app.tenancy import user_settings
    base=replace(settings,storage_dir=tmp_path)
    published=local_preferences.save_template(base,'Shared','Admin content')
    user=user_settings(base,{'id':'c'*32,'legacy_owner':False})
    items=local_preferences.list_templates(user)
    shared=next((item for item in items if item['name']=='Shared'),None)
    assert shared and shared['read_only'] is True
    with pytest.raises(PermissionError):
        local_preferences.save_template(user,'Hacked','Other',shared['id'])
    with pytest.raises(PermissionError):
        local_preferences.delete_template(user,shared['id'])
    assert next(x for x in local_preferences.list_templates(base) if x['id']==published['id'])['content']=='Admin content'


def test_tenant_portrait_revalidation_uses_shared_config_not_empty_user_config(tmp_path):
    from dataclasses import asdict
    from app import portrait_generation,portrait_service
    from app.tenancy import user_settings
    base=replace(settings,storage_dir=tmp_path)
    portrait_service.save_config(base,{'access_key':'test-ak','secret_key':'test-sk'})
    tenant=user_settings(base,{'id':'d'*32,'legacy_owner':False})
    snapshot={'config':asdict(portrait_service.load_config(base)),'bindings':{}}
    assert portrait_generation.verify(snapshot,ProductionStore(tenant.storage_dir),settings=tenant)=={}
    portrait_service.save_config(base,{'access_key':'changed-ak','secret_key':'changed-sk'})
    with pytest.raises(ValueError):
        portrait_generation.verify(snapshot,ProductionStore(tenant.storage_dir),settings=tenant)
