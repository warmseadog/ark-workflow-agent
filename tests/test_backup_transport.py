from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path

import pytest


def test_encryption_roundtrip_and_wrong_key_never_publish_plaintext(tmp_path):
    from app.backup_transport import encrypt_file, decrypt_file
    source = tmp_path/'source.tar'
    source.write_bytes(b'private configuration and media\x00' * 100000)
    encrypted = tmp_path/'snapshot.arkb'
    key = b'1'*32
    encrypt_file(source, encrypted, key)
    assert b'private configuration' not in encrypted.read_bytes()
    restored = tmp_path/'restored.tar'
    decrypt_file(encrypted, restored, key)
    assert restored.read_bytes() == source.read_bytes()
    rejected = tmp_path/'wrong.tar'
    with pytest.raises(ValueError):
        decrypt_file(encrypted, rejected, b'2'*32)
    assert not rejected.exists()
    data = bytearray(encrypted.read_bytes())
    data[len(data)//2] ^= 1
    encrypted.write_bytes(data)
    with pytest.raises(ValueError):
        decrypt_file(encrypted, rejected, key)
    assert not rejected.exists()


class ObjectStore:
    def __init__(self, corrupt=False):
        self.objects = {}
        self.corrupt = corrupt
    def put_object_from_file(self, bucket, key, file_path, **kwargs):
        assert kwargs['forbid_overwrite']
        assert key not in self.objects
        self.objects[key] = Path(file_path).read_bytes()
    def get_object(self, bucket, key):
        value = self.objects[key]
        if self.corrupt and key.endswith('.arkb'):
            value += b'bad'
        return io.BytesIO(value)


def test_failed_remote_readback_cannot_replace_success_receipt(tmp_path):
    from app.backup_transport import upload_verified
    archive = tmp_path/'x.arkb'
    archive.write_bytes(b'local encrypted archive')
    receipt = tmp_path/'last-success.json'
    receipt.write_text('{"previous":"verified"}')
    old = receipt.read_bytes()
    metadata = {'snapshot_id':'snapshot-123', 'created_at':'2026-10-05T00:00:00+00:00'}
    store = ObjectStore(corrupt=True)
    with pytest.raises(ValueError):
        upload_verified(archive, store, 'separate-backups', 'ark-backups/', metadata, receipt)
    assert receipt.read_bytes() == old
    assert not any(key.endswith('.receipt.json') for key in store.objects)
    store = ObjectStore()
    result = upload_verified(archive, store, 'separate-backups', 'ark-backups/', metadata, receipt)
    assert result['verified'] is True
    assert result['size'] == archive.stat().st_size
    assert json.loads(receipt.read_text())['snapshot_id'] == 'snapshot-123'
    assert len(store.objects) == 2


def test_freshness_uses_snapshot_time_not_upload_time_and_rejects_future():
    from app.backup_transport import backup_health
    now = datetime(2026,10,5,tzinfo=timezone.utc)
    old = {'verified':True, 'created_at':(now-timedelta(hours=25)).isoformat(), 'verified_at':now.isoformat()}
    assert backup_health(old, now=now)['ok'] is False
    assert backup_health({'verified':False, 'created_at':now.isoformat()}, now=now)['ok'] is False
    assert backup_health({'verified':True, 'created_at':now.isoformat()}, now=now)['ok'] is True
    assert backup_health({'verified':True, 'created_at':(now+timedelta(days=2)).isoformat()}, now=now)['ok'] is False


def test_retention_keeps_daily_weekly_monthly_and_never_unverified():
    from app.backup_transport import retention_candidates
    now = datetime(2026,10,5,tzinfo=timezone.utc)
    receipts = [{'snapshot_id':f'day-{i}', 'created_at':(now-timedelta(days=i)).isoformat(), 'verified':True}
                for i in range(100)]
    receipts.append({'snapshot_id':'incomplete', 'created_at':(now-timedelta(days=200)).isoformat(), 'verified':False})
    delete = {item['snapshot_id'] for item in retention_candidates(receipts)}
    assert not delete.intersection({f'day-{i}' for i in range(7)})
    assert 'incomplete' not in delete
    assert 'day-99' in delete
    kept = [item for item in receipts if item['snapshot_id'] not in delete and item['verified']]
    assert len({item['created_at'][:7] for item in kept}) >= 3


def test_archive_paths_and_existing_destination_are_rejected(tmp_path):
    from app.backup_transport import unpack_snapshot
    import tarfile
    archive = tmp_path/'malicious.tar.gz'
    with tarfile.open(archive,'w:gz') as tar:
        item = tarfile.TarInfo('../escape')
        item.size=3
        tar.addfile(item,io.BytesIO(b'bad'))
    with pytest.raises(ValueError):
        unpack_snapshot(archive,tmp_path/'destination')
    assert not (tmp_path/'escape').exists()
    assert not (tmp_path/'destination').exists()


def test_encrypted_archive_roundtrip_preserves_empty_dirs_and_database(tmp_path, monkeypatch):
    from app.backup import create_snapshot, restore_snapshot
    from app.backup_transport import pack_snapshot, unpack_snapshot, encrypt_file, decrypt_file
    import sqlite3
    for key in ('DATABASE_URL','WORKFLOW_DB','WORKFLOW_STORAGE'):
        monkeypatch.delenv(key,raising=False)
    source=tmp_path/'data';source.mkdir();(source/'workflow').mkdir()
    with sqlite3.connect(source/'studio.db') as db:
        db.execute('CREATE TABLE example(value TEXT)')
        db.execute("INSERT INTO example VALUES ('restored')")
    snapshot=tmp_path/'snapshot'
    create_snapshot(source,snapshot,quiesced=True)
    archive=pack_snapshot(snapshot,tmp_path/'archive.tar.gz')
    encrypted=encrypt_file(archive,tmp_path/'encrypted.arkb',b'x'*32)
    decrypted=decrypt_file(encrypted,tmp_path/'downloaded.tar.gz',b'x'*32)
    unpacked=unpack_snapshot(decrypted,tmp_path/'unpacked')
    restore_snapshot(unpacked,tmp_path/'restored')
    assert (tmp_path/'restored'/'workflow').is_dir()
    with sqlite3.connect(tmp_path/'restored'/'studio.db') as db:
        assert db.execute('SELECT value FROM example').fetchone()[0]=='restored'
    assert (tmp_path/'restored'/'.restore-hold.json').is_file()
