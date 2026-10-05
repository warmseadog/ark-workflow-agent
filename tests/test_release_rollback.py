"""Faults at the real release handler's systemd/proxy boundaries stay closed."""
import ast
import json
from pathlib import Path
import pytest

SCRIPT = Path(__file__).resolve().parents[1] / 'deploy/ecs/release-account-policy-remote.py'


def rollback(*, active=False, start_fails=False, ready_fails=False):
    tree = ast.parse(SCRIPT.read_text(encoding='utf-8'))
    handler = [node for node in tree.body if isinstance(node, ast.Try)][-1]
    handler.body = ast.parse("raise RuntimeError('new release verification failed')").body
    events = []
    def run(*args, **kwargs):
        events.append(args[1])
        if args[1] == 'start' and start_fails:
            raise RuntimeError('rollback service failed')
    def ready():
        events.append('ready')
        if ready_fails:
            raise RuntimeError('rollback health failed')
    scope = dict(switched=True, stopped=True, public_paused=True, reopen_allowed=False,
                 queues=lambda: {'tasks': int(active)}, run=run, SERVICE='test',
                 switch=lambda target: events.append('switch'), PREVIOUS='previous',
                 wait_ready=ready, json=json, print=lambda value: None,
                 restore_public_requests=lambda: events.append('reopened'))
    with pytest.raises(RuntimeError):
        exec(compile(ast.fix_missing_locations(ast.Module(body=[handler], type_ignores=[])),
                     str(SCRIPT), 'exec'), scope)
    return events


def test_failed_old_service_start_keeps_maintenance():
    assert 'reopened' not in rollback(start_fails=True)


def test_failed_old_service_readiness_keeps_maintenance():
    assert 'reopened' not in rollback(ready_fails=True)


def test_active_tasks_defer_rollback_and_keep_new_submissions_closed():
    assert rollback(active=True) == []


def test_successful_rollback_checks_readiness_before_reopening():
    assert rollback() == ['stop', 'switch', 'start', 'ready', 'reopened']
