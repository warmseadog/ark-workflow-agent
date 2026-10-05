import pytest


def test_maintenance_lock_excludes_other_operations_and_releases_after_error(tmp_path):
    from app.release_lock import maintenance_lock
    root = tmp_path / 'backup'
    with pytest.raises(ValueError):
        with maintenance_lock(root):
            assert (root / '.maintenance-operation.lock').is_file()
            with pytest.raises(RuntimeError):
                with maintenance_lock(root):
                    pytest.fail('Second writer acquired maintenance lock')
            assert (root / '.maintenance-operation.lock').is_file()
            raise ValueError('synthetic failure')
    with maintenance_lock(root):
        pass


def test_maintenance_lock_is_released_when_owning_process_crashes(tmp_path):
    import subprocess
    import sys
    import os
    from app.release_lock import maintenance_lock
    code = ('from app.release_lock import maintenance_lock; import sys, os\n'
            'with maintenance_lock(sys.argv[1]):\n'
            ' print("LOCKED", flush=True)\n'
            ' sys.stdin.read(1)\n'
            ' os._exit(91)\n')
    process = subprocess.Popen([sys.executable, '-c', code, str(tmp_path)], stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                               creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    try:
        assert process.stdout.readline().strip() == 'LOCKED'
        with pytest.raises(RuntimeError):
            with maintenance_lock(tmp_path):
                pytest.fail('Other process holds lock')
        process.communicate(input='x', timeout=10)
        assert process.returncode == 91
        with maintenance_lock(tmp_path):
            pass
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)


def test_backup_cannot_restart_a_service_while_release_is_in_maintenance(tmp_path):
    from app.backup_operations import run_backup
    from tests.test_backup_operations import config, Service
    from tests.test_backup_transport import ObjectStore
    from pathlib import Path
    cfg = config(tmp_path)
    root = Path(cfg['snapshot_root'])
    root.mkdir()
    (root / '.release-maintenance.json').write_text('{}')
    service = Service()
    with pytest.raises(RuntimeError):
        run_backup(cfg, service=service, client=ObjectStore())
    assert service.events == []


def test_backup_respects_the_same_lock_as_release(tmp_path):
    from app.backup_operations import run_backup
    from app.release_lock import maintenance_lock
    from tests.test_backup_operations import config, Service
    from tests.test_backup_transport import ObjectStore
    cfg = config(tmp_path)
    service = Service()
    with maintenance_lock(cfg['snapshot_root']):
        with pytest.raises(RuntimeError):
            run_backup(cfg, service=service, client=ObjectStore())
    assert service.events == []
