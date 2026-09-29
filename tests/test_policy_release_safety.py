"""Exercise release preflight helpers against actual temporary SQLite databases."""
import ast
import json
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT/'deploy/ecs/release-account-policy-remote.py'


def helpers(data):
    tree = ast.parse(SCRIPT.read_text(encoding='utf-8'))
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                 and node.name in {'databases', 'queues', 'idle'}]
    scope = {'DATA': data, 'WORKFLOW_DB': data/'workflow.db', 'sqlite3': sqlite3, 'json': json}
    exec(compile(ast.Module(body=functions, type_ignores=[]), str(SCRIPT), 'exec'), scope)
    return scope


@pytest.mark.parametrize('state', ['queued', 'running'])
def test_release_refuses_active_legacy_workflow_tasks(tmp_path, state):
    with sqlite3.connect(tmp_path/'workflow.db') as db:
        db.execute('CREATE TABLE stage_tasks (id TEXT, status TEXT)')
        db.execute('INSERT INTO stage_tasks VALUES (?,?)', ('old-task', state))
    scope = helpers(tmp_path)
    with pytest.raises(AssertionError, match='Active tasks'):
        scope['idle']()


def test_release_allows_completed_workflow_tasks_and_backs_up_database(tmp_path):
    with sqlite3.connect(tmp_path/'workflow.db') as db:
        db.execute('CREATE TABLE stage_tasks (id TEXT, status TEXT)')
        db.execute("INSERT INTO stage_tasks VALUES ('old-task','succeeded')")
    scope = helpers(tmp_path)
    assert not any(scope['idle']().values())
    assert tmp_path/'workflow.db' in scope['databases']()


def test_release_probe_never_initializes_inherited_workflow_database(tmp_path, monkeypatch):
    outside = tmp_path/'must-not-be-touched'/'workflow.db'
    monkeypatch.setenv('WORKFLOW_DB', str(outside))
    monkeypatch.setenv('WORKFLOW_STORAGE', str(outside.parent))
    tree = ast.parse(SCRIPT.read_text(encoding='utf-8'))
    probe = next(ast.literal_eval(node.value) for node in tree.body if isinstance(node, ast.Assign)
                 and any(isinstance(target, ast.Name) and target.id == 'probe' for target in node.targets))
    result = subprocess.run([sys.executable, '-c', probe], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert not outside.exists(), 'Isolated release probe touched the inherited workflow database'
