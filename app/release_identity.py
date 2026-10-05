"""Read the immutable release identity shared by deployment, runtime and backup."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
import re


CONTENT_FIELDS = ('schema', 'version', 'source_commit', 'capabilities', 'files',
                  'dependencies', 'source_dirty', 'untracked_files')


class ReleaseIdentityError(ValueError):
    pass


def manifest_content_sha256(manifest):
    """Hash exactly the portable build inputs, excluding derived identity fields."""
    try:
        content = {key: manifest[key] for key in CONTENT_FIELDS}
        return hashlib.sha256(json.dumps(content, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    except (KeyError, TypeError, ValueError) as error:
        raise ReleaseIdentityError('Incomplete release manifest') from error


def _relative(value):
    return (isinstance(value, str) and bool(value) and '\\' not in value and ':' not in value
            and not PurePosixPath(value).is_absolute()
            and all(part not in {'', '.', '..'} for part in value.split('/')))


def validate_release_identity(identity, *, code_revision=None):
    """Validate the compact identity embedded in a snapshot or runtime response."""
    fields = {'schema', 'version', 'revision', 'source_commit', 'content_sha256', 'capabilities', 'dependencies'}
    if not isinstance(identity, dict) or set(identity) != fields:
        raise ReleaseIdentityError('Incomplete release identity')
    commit, digest = identity['source_commit'], identity['content_sha256']
    if (identity['schema'] != 'ark-release' or type(identity['version']) is not int or identity['version'] != 1
            or not isinstance(commit, str) or not re.fullmatch(r'[a-f0-9]{40}|[a-f0-9]{64}', commit)
            or not isinstance(digest, str) or not re.fullmatch(r'[a-f0-9]{64}', digest)
            or identity['revision'] != commit + '+' + digest[:16]
            or (code_revision is not None and commit != code_revision)):
        raise ReleaseIdentityError('Inconsistent release identity')
    capabilities, dependencies = identity['capabilities'], identity['dependencies']
    if (not isinstance(capabilities, list) or len(capabilities) > 100
            or any(not isinstance(item, str) or not re.fullmatch(r'[a-z0-9-]{1,80}', item) for item in capabilities)
            or len(set(capabilities)) != len(capabilities)):
        raise ReleaseIdentityError('Invalid release capabilities')
    if (not isinstance(dependencies, dict) or len(dependencies) > 1000
            or any(not isinstance(name, str) or not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', name)
                   or not isinstance(version, str) or not re.fullmatch(r'[A-Za-z0-9.!+_-]{1,120}', version)
                   for name, version in dependencies.items())):
        raise ReleaseIdentityError('Invalid release dependency identity')
    return identity


def read_release_identity(release_root=None):
    """Return public identity, None for an unpackaged checkout, or fail closed.

    File integrity and installed dependencies are checked by the deployer. Reading
    identity verifies the manifest digest and REVISION, without hashing media or
    all application files on every request.
    """
    root = Path(release_root) if release_root is not None else Path(__file__).resolve().parent.parent
    path = root / 'release-manifest.json'
    if not path.exists():
        if path.is_symlink() or (root / 'release-receipt.json').exists():
            raise ReleaseIdentityError('Packaged release manifest is missing')
        revision_path = root / 'REVISION'
        if revision_path.exists():
            try:
                revision = revision_path.read_text(encoding='utf-8').strip()
            except (OSError, UnicodeError) as error:
                raise ReleaseIdentityError('Cannot inspect release identity') from error
            if re.fullmatch(r'(?:[a-f0-9]{40}|[a-f0-9]{64})\+[a-f0-9]{16}', revision):
                raise ReleaseIdentityError('Packaged release manifest is missing')
        return None
    try:
        if path.stat().st_size > 4 * 1024 * 1024:
            raise ReleaseIdentityError('Release manifest is too large')
        manifest = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(manifest, dict):
            raise ReleaseIdentityError('Invalid release manifest')
        if (manifest.get('schema') != 'ark-release' or type(manifest.get('version')) is not int
                or manifest['version'] != 1
                or not re.fullmatch(r'[a-f0-9]{40}|[a-f0-9]{64}', str(manifest.get('source_commit', '')))
                or not re.fullmatch(r'[a-f0-9]{64}', str(manifest.get('content_sha256', '')))
                or type(manifest.get('source_dirty')) is not bool):
            raise ReleaseIdentityError('Unsupported release identity')
        capabilities = manifest.get('capabilities')
        if (not isinstance(capabilities, list) or len(capabilities) > 100
                or any(not isinstance(item, str) or not re.fullmatch(r'[a-z0-9-]{1,80}', item)
                       for item in capabilities) or len(set(capabilities)) != len(capabilities)):
            raise ReleaseIdentityError('Invalid release capabilities')
        dependencies = manifest.get('dependencies')
        if (not isinstance(dependencies, dict) or len(dependencies) > 1000
                or any(not isinstance(name, str) or not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', name)
                       or not isinstance(version, str) or not re.fullmatch(r'[A-Za-z0-9.!+_-]{1,120}', version)
                       for name, version in dependencies.items())):
            raise ReleaseIdentityError('Invalid release dependency identity')
        files = manifest.get('files')
        if not isinstance(files, dict) or not files:
            raise ReleaseIdentityError('Release file inventory is missing')
        for name, entry in files.items():
            if (not _relative(name) or not isinstance(entry, dict)
                    or not re.fullmatch(r'[a-f0-9]{64}', str(entry.get('sha256', '')))
                    or type(entry.get('size')) is not int or entry['size'] < 0
                    or type(entry.get('mode')) is not int or entry['mode'] not in {0o644, 0o755}):
                raise ReleaseIdentityError('Invalid release file inventory')
        untracked = manifest.get('untracked_files')
        if (not isinstance(untracked, list) or any(not _relative(name) or name not in files for name in untracked)
                or len(set(untracked)) != len(untracked)):
            raise ReleaseIdentityError('Invalid release source inventory')
        digest = manifest_content_sha256(manifest)
        revision = manifest['source_commit'] + '+' + digest[:16]
        if (digest != manifest['content_sha256'] or manifest.get('revision') != revision
                or (root / 'REVISION').stat().st_size > 256
                or (root / 'REVISION').read_text(encoding='utf-8').strip() != revision):
            raise ReleaseIdentityError('Release identity disagrees with its content or REVISION')
        return validate_release_identity({key: manifest[key] for key in ('schema', 'version', 'revision', 'source_commit',
                                               'content_sha256', 'capabilities', 'dependencies')})
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ReleaseIdentityError('Cannot read a consistent release identity') from error
