"""Authenticated streaming encryption and independently verified offsite backups.

No application configuration or credentials are loaded at import time.
"""
from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import tarfile
import tempfile

MAGIC = b'ARKBKUP1'
CHUNK = 1024 * 1024
SHANGHAI = timezone(timedelta(hours=8))


def sha256_file(path) -> str:
    digest = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for chunk in iter(lambda: handle.read(CHUNK), b''):
            digest.update(chunk)
    return digest.hexdigest()


def read_key(path) -> bytes:
    path = Path(path)
    if path.is_symlink() or (os.name != 'nt' and path.stat().st_mode & 0o077):
        raise ValueError('Backup encryption key must be a private regular file')
    try:
        key = base64.b64decode(path.read_bytes().strip(), altchars=b'-_', validate=True)
    except ValueError:
        raise ValueError('Backup encryption key must contain base64-encoded 32 random bytes') from None
    if len(key) != 32:
        raise ValueError('Backup encryption key must contain 32 random bytes')
    return key


def _fresh_output(path):
    path = Path(path)
    if path.exists() or path.is_symlink():
        raise ValueError('Output already exists; backups never overwrite archive files')
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path


def _publish_file(temporary, destination):
    # Link creation fails if destination appeared concurrently; unlike replace(),
    # it never overwrites a pre-existing user file. Both files share a filesystem.
    os.link(temporary, destination)
    Path(temporary).unlink()


def encrypt_file(source, destination, key: bytes) -> Path:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    if len(key) != 32:
        raise ValueError('A 256-bit backup key is required')
    destination = _fresh_output(destination)
    nonce = os.urandom(12)
    encryptor = Cipher(algorithms.AES(key), modes.GCM(nonce)).encryptor()
    encryptor.authenticate_additional_data(MAGIC)
    with tempfile.TemporaryDirectory(prefix='.encrypt-', dir=destination.parent) as temp:
        temporary = Path(temp)/'encrypted'
        with Path(source).open('rb') as reader, temporary.open('xb') as writer:
            writer.write(MAGIC + nonce)
            for chunk in iter(lambda: reader.read(CHUNK), b''):
                writer.write(encryptor.update(chunk))
            writer.write(encryptor.finalize())
            writer.write(encryptor.tag)
            writer.flush()
            os.fsync(writer.fileno())
        if os.name != 'nt':
            temporary.chmod(0o600)
        _publish_file(temporary, destination)
    return destination


def decrypt_file(source, destination, key: bytes) -> Path:
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    source, destination = Path(source), _fresh_output(destination)
    if len(key) != 32 or source.stat().st_size < len(MAGIC) + 12 + 16:
        raise ValueError('Invalid encrypted backup or key')
    with source.open('rb') as reader:
        if reader.read(len(MAGIC)) != MAGIC:
            raise ValueError('Unsupported encrypted backup format')
        nonce = reader.read(12)
        reader.seek(-16, os.SEEK_END)
        tag = reader.read(16)
        reader.seek(len(MAGIC) + 12)
        remaining = source.stat().st_size - len(MAGIC) - 12 - 16
        decryptor = Cipher(algorithms.AES(key), modes.GCM(nonce, tag)).decryptor()
        decryptor.authenticate_additional_data(MAGIC)
        with tempfile.TemporaryDirectory(prefix='.decrypt-', dir=destination.parent) as temp:
            temporary = Path(temp)/'plaintext'
            with temporary.open('xb') as writer:
                if os.name != 'nt':
                    temporary.chmod(0o600)
                while remaining:
                    chunk = reader.read(min(CHUNK, remaining))
                    if not chunk:
                        raise ValueError('Truncated encrypted backup')
                    writer.write(decryptor.update(chunk))
                    remaining -= len(chunk)
                try:
                    writer.write(decryptor.finalize())
                except InvalidTag:
                    raise ValueError('Backup authentication failed: wrong key or corrupted archive') from None
                writer.flush()
                os.fsync(writer.fileno())
            _publish_file(temporary, destination)
    return destination


def pack_snapshot(snapshot, destination) -> Path:
    from .backup import verify_snapshot
    snapshot, destination = Path(snapshot), _fresh_output(destination)
    verify_snapshot(snapshot)
    with tempfile.TemporaryDirectory(prefix='.pack-', dir=destination.parent) as temp:
        output = Path(temp)/'snapshot.tar.gz'
        with tarfile.open(output, 'w:gz') as archive:
            for path in sorted(snapshot.rglob('*')):
                if path.is_symlink():
                    raise ValueError('Snapshot contains a symlink')
                if path.is_file() or path.is_dir():
                    archive.add(path, arcname=path.relative_to(snapshot).as_posix(), recursive=False)
        if os.name != 'nt':
            output.chmod(0o600)
        _publish_file(output, destination)
    return destination


def unpack_snapshot(archive, destination) -> Path:
    from .backup import _no_links, _publish
    destination = _fresh_output(_no_links(destination))
    with tempfile.TemporaryDirectory(prefix='.unpack-', dir=destination.parent) as temp:
        stage = Path(temp)/'snapshot'
        stage.mkdir(mode=0o700)
        seen = set()
        with tarfile.open(archive, 'r:gz') as source:
            for member in source:
                name = PurePosixPath(member.name)
                if (not (member.isfile() or member.isdir()) or name.is_absolute() or '..' in name.parts
                        or '\\' in member.name or ':' in member.name or not name.parts
                        or name.as_posix() in seen):
                    raise ValueError('Unsafe or duplicate archive member')
                seen.add(name.as_posix())
                target = stage.joinpath(*name.parts)
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True, mode=0o700)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                content = source.extractfile(member)
                if content is None:
                    raise ValueError('Missing archive member bytes')
                with content, target.open('xb') as output:
                    shutil.copyfileobj(content, output, CHUNK)
                if os.name != 'nt':
                    target.chmod(0o600)
        from .backup import verify_snapshot
        verify_snapshot(stage)
        _publish(stage,destination)
    return destination


def _json_write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.TemporaryDirectory(prefix='.receipt-', dir=path.parent) as temp:
        temporary = Path(temp)/'receipt.json'
        with temporary.open('x', encoding='utf-8') as output:
            json.dump(value, output, ensure_ascii=False, indent=2)
            output.flush()
            os.fsync(output.fileno())
        if os.name != 'nt':
            temporary.chmod(0o600)
        temporary.replace(path)


def _prefix(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_/-]+/', value) or any(p in {'', '.', '..'} for p in value[:-1].split('/')):
        raise ValueError('A dedicated, nonempty backup object prefix is required')
    return value


def _read_remote(client, bucket, key, output=None):
    result = client.get_object(bucket, key)
    digest, size = hashlib.sha256(), 0
    try:
        while True:
            chunk = result.read(CHUNK)
            if not chunk:
                break
            digest.update(chunk)
            size += len(chunk)
            if output is not None:
                output.write(chunk)
    finally:
        close = getattr(result, 'close', None)
        if close:
            close()
    return digest.hexdigest(), size


def upload_verified(archive, client, bucket, prefix, metadata, receipt_path) -> dict:
    prefix = _prefix(prefix)
    snapshot_id = metadata['snapshot_id']
    if not isinstance(snapshot_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', snapshot_id):
        raise ValueError('Invalid snapshot identifier')
    created = _timestamp(metadata['created_at'])
    archive = Path(archive)
    digest, size = sha256_file(archive), archive.stat().st_size
    key = prefix + snapshot_id + '.arkb'
    client.put_object_from_file(bucket, key, str(archive), forbid_overwrite=True,
                                content_type='application/octet-stream', meta={'sha256':digest})
    if _read_remote(client, bucket, key) != (digest, size):
        raise ValueError('Offsite backup failed full readback verification')
    receipt = {'schema':'ark-offsite-backup', 'version':1, 'snapshot_id':snapshot_id,
               'created_at':created.isoformat(), 'verified_at':datetime.now(timezone.utc).isoformat(),
               'verified':True, 'bucket':bucket, 'key':key, 'sha256':digest, 'size':size}
    with tempfile.TemporaryDirectory(prefix='.remote-receipt-', dir=archive.parent) as temp:
        receipt_file = Path(temp)/'receipt.json'
        _json_write(receipt_file, receipt)
        receipt_key = prefix + snapshot_id + '.receipt.json'
        client.put_object_from_file(bucket, receipt_key, str(receipt_file), forbid_overwrite=True,
                                    content_type='application/json')
        if _read_remote(client, bucket, receipt_key) != (sha256_file(receipt_file), receipt_file.stat().st_size):
            raise ValueError('Offsite success receipt failed readback verification')
    _json_write(receipt_path, receipt)
    return receipt


def download_verified(receipt, client, destination) -> Path:
    destination = _fresh_output(destination)
    if receipt.get('schema') != 'ark-offsite-backup' or not receipt.get('verified'):
        raise ValueError('A verified offsite receipt is required')
    with tempfile.TemporaryDirectory(prefix='.download-', dir=destination.parent) as temp:
        temporary = Path(temp)/'archive'
        with temporary.open('xb') as output:
            observed = _read_remote(client, receipt['bucket'], receipt['key'], output)
            output.flush()
            os.fsync(output.fileno())
        if observed != (receipt['sha256'], receipt['size']):
            raise ValueError('Downloaded backup checksum/size does not match its receipt')
        if os.name != 'nt':
            temporary.chmod(0o600)
        _publish_file(temporary, destination)
    return destination


def _timestamp(value):
    date = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if date.tzinfo is None:
        raise ValueError('Backup timestamps require a timezone')
    return date.astimezone(timezone.utc)


def backup_health(receipt, *, now=None, max_age_hours=24) -> dict:
    now = now or datetime.now(timezone.utc)
    try:
        age = (now - _timestamp(receipt['created_at'])).total_seconds()
        ok = receipt.get('verified') is True and 0 <= age <= max_age_hours * 3600
        return {'ok':ok, 'snapshot_age_seconds':age, 'max_age_hours':max_age_hours}
    except (ValueError, KeyError, TypeError, AttributeError):
        return {'ok':False, 'reason':'No valid verified offsite backup receipt'}


def retention_candidates(receipts) -> list[dict]:
    valid = [item for item in receipts if item.get('verified') is True]
    dated = sorted(((item, _timestamp(item['created_at']).astimezone(SHANGHAI)) for item in valid),
                   key=lambda pair: pair[1], reverse=True)
    keep = set()
    for count, group in ((7, lambda d:d.date()), (4, lambda d:d.isocalendar()[:2]), (3, lambda d:(d.year,d.month))):
        groups = set()
        for item, stamp in dated:
            category = group(stamp)
            if category not in groups and len(groups) < count:
                groups.add(category)
                keep.add(item['snapshot_id'])
    return [item for item, _ in dated if item['snapshot_id'] not in keep]
