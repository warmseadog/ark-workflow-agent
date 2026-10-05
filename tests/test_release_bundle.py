import importlib
import json
from pathlib import Path
import subprocess
import tarfile
import pytest


def api():
    assert importlib.util.find_spec('app.release_bundle'), 'Release bundle implementation missing'
    return importlib.import_module('app.release_bundle')


@pytest.fixture
def repo(tmp_path):
    path = tmp_path/'repo'; path.mkdir()
    for name, data in {'app/__init__.py':'', 'app/main.py':'answer = 42\n',
                       'requirements.txt':'example>=1\n',
                       'deploy/ecs/requirements-linux.lock.txt':'example==1.2\n',
                       'deploy/ecs/app.service':'[Service]\n'}.items():
        target=path/name; target.parent.mkdir(parents=True,exist_ok=True); target.write_text(data)
    subprocess.run(['git','init','-q',str(path)],check=True)
    subprocess.run(['git','-C',str(path),'add','.'],check=True)
    subprocess.run(['git','-C',str(path),'-c','user.name=Test','-c','user.email=test@example.invalid',
                    'commit','-qm','fixture'],check=True)
    return path


def test_full_bundle_reproducible_and_covers_dependency_and_deploy_files(repo,tmp_path):
    release=api()
    first=release.build_bundle(repo,tmp_path/'first.tar.gz')
    second=release.build_bundle(repo,tmp_path/'second.tar.gz')
    assert first==second
    assert (tmp_path/'first.tar.gz').read_bytes()==(tmp_path/'second.tar.gz').read_bytes()
    with tarfile.open(tmp_path/'first.tar.gz') as archive:
        assert {'requirements.txt','deploy/ecs/requirements-linux.lock.txt','deploy/ecs/app.service',
                'app/main.py','REVISION','release-manifest.json'} <= set(archive.getnames())
    assert first['dependencies']=={'example':'1.2'}
    assert first['source_dirty'] is False


def test_untracked_source_requires_explicit_selection(repo,tmp_path):
    release=api(); (repo/'app/new.py').write_text('new=True')
    with pytest.raises(ValueError,match='[Uu]ntracked'):
        release.build_bundle(repo,tmp_path/'blocked.tar.gz')
    manifest=release.build_bundle(repo,tmp_path/'accepted.tar.gz',include_untracked=['app/new.py'])
    assert manifest['untracked_files']==['app/new.py']
    assert 'app/new.py' in manifest['files']
    assert manifest['source_dirty'] is True


def test_working_tree_content_changes_release_identity(repo,tmp_path):
    release=api(); old=release.build_bundle(repo,tmp_path/'old.tar.gz')
    (repo/'app/main.py').write_text('answer = 43\n')
    new=release.build_bundle(repo,tmp_path/'new.tar.gz')
    assert old['source_commit']==new['source_commit']
    assert old['revision']!=new['revision']


def test_tracked_bundled_mosaic_scripts_and_models_survive_full_deployment(repo,tmp_path):
    release=api();base='storage/attached_local_face_mosaic_v3/local-face-mosaic-tracking/'
    names=[base+'scripts/process_primary_face_mosaic.py',base+'scripts/process_all_faces_mosaic.py',
           base+'models/selfie_multiclass_256x256.tflite',base+'models/face_detection_yunet_2023mar.onnx']
    for name in names:
        path=repo/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'bundled-resource')
    subprocess.run(['git','-C',str(repo),'add',base],check=True)
    (repo/'storage'/'live-business.db').write_bytes(b'must-not-read-or-package')
    manifest=release.build_bundle(repo,tmp_path/'bundle.tar.gz')
    assert set(names)<=set(manifest['files'])
    destination=tmp_path/'release';release.unpack_bundle(tmp_path/'bundle.tar.gz',destination)
    assert (destination/names[0]).is_file()
    assert 'storage/live-business.db' not in manifest['files']


@pytest.mark.parametrize('declaration',['argon2-cffi==25.1.0','example>=2'])
def test_requirements_missing_or_incompatible_lock_cannot_build(repo,tmp_path,declaration):
    release=api();(repo/'requirements.txt').write_text(declaration+'\n')
    with pytest.raises(ValueError,match='[Dd]eclar|[Ll]ock'):
        release.build_bundle(repo,tmp_path/'bundle.tar.gz')


def test_deleted_tracked_file_is_absent_from_full_release(repo,tmp_path):
    release=api(); (repo/'app/main.py').unlink()
    manifest=release.build_bundle(repo,tmp_path/'new.tar.gz')
    assert 'app/main.py' not in manifest['files']


@pytest.mark.parametrize('name',['app/.env','deploy/ecs/server.key','app/private.pem'])
def test_secret_files_cannot_be_approved_into_bundle(repo,tmp_path,name):
    release=api(); (repo/name).write_text('do-not-package')
    with pytest.raises(ValueError,match='[Ss]ecret'):
        release.build_bundle(repo,tmp_path/'bad.tar.gz',include_untracked=[name])


def test_unpack_verifies_files_and_revision_before_publication(repo,tmp_path):
    release=api(); manifest=release.build_bundle(repo,tmp_path/'bundle.tar.gz')
    destination=tmp_path/'release'; release.unpack_bundle(tmp_path/'bundle.tar.gz',destination)
    assert (destination/'REVISION').read_text().strip()==manifest['revision']
    assert release.verify_release(destination)['revision']==manifest['revision']
    (destination/'app/main.py').write_text('tampered')
    with pytest.raises(ValueError,match='[Hh]ash|[Cc]ontent'):
        release.verify_release(destination)


def test_dependencies_missing_or_mismatched_block_activation(repo,tmp_path):
    release=api(); manifest=release.build_bundle(repo,tmp_path/'bundle.tar.gz')
    for installed in ({},{'example':'2.0'}):
        with pytest.raises(ValueError,match='[Dd]ependenc'):
            release.assert_dependencies(manifest,installed)
    release.assert_dependencies(manifest,{'example':'1.2'})


def test_unlisted_top_level_python_cannot_hide_from_release_verification(repo,tmp_path):
    release=api();release.build_bundle(repo,tmp_path/'bundle.tar.gz')
    destination=tmp_path/'release';release.unpack_bundle(tmp_path/'bundle.tar.gz',destination)
    (destination/'sitecustomize.py').write_text('unexpected=True')
    with pytest.raises(ValueError,match='file set'):
        release.verify_release(destination)


def test_manifest_dependency_inventory_must_match_packaged_lock(repo,tmp_path):
    import hashlib
    release=api();release.build_bundle(repo,tmp_path/'bundle.tar.gz')
    destination=tmp_path/'release';release.unpack_bundle(tmp_path/'bundle.tar.gz',destination)
    manifest=json.loads((destination/'release-manifest.json').read_text())
    manifest['dependencies']={'example':'9.9'}
    content={key:value for key,value in manifest.items() if key not in {'revision','content_sha256'}}
    checksum=hashlib.sha256(json.dumps(content,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    manifest['content_sha256']=checksum;manifest['revision']=manifest['source_commit']+'+'+checksum[:16]
    (destination/'release-manifest.json').write_text(json.dumps(manifest))
    (destination/'REVISION').write_text(manifest['revision']+'\n')
    with pytest.raises(ValueError,match='[Dd]ependenc|[Ll]ock'):
        release.verify_release(destination)


@pytest.mark.parametrize('mode',['deploy','probe','typo'])
@pytest.mark.parametrize('script',['release-account-policy-remote.py','release-multiuser-remote.py'])
def test_retired_remote_mutation_entry_cannot_bypass_new_preflight(mode,script):
    import runpy
    root=Path(__file__).resolve().parents[1]
    # Must reject before attempting systemctl or inspecting process environments.
    with pytest.raises(ValueError,match='[Rr]etired|[Mm]ode'):
        runpy.run_path(str(root/'deploy/ecs'/script),init_globals={'mode':mode})


def test_cli_rejects_unknown_mode_without_remote_process(tmp_path):
    root=Path(__file__).resolve().parents[1]
    script=root/'deploy/ecs/release.py'
    assert script.is_file(), 'Safe tracked CLI missing'
    result=subprocess.run([__import__('sys').executable,str(script),'rollback-typo'],
                          cwd=tmp_path,capture_output=True,text=True)
    assert result.returncode==2
    assert 'invalid choice' in result.stderr
