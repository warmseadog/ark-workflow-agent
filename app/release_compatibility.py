"""Read-only downgrade gate. SQL shape compatibility is not worker compatibility."""
from __future__ import annotations
from contextlib import closing
from pathlib import Path
import sqlite3
from .release_bundle import ReleaseError, no_links


def discover_databases(storage_root,*,database_paths=(),extra_roots=()):
    roots=[no_links(storage_root),*(no_links(p) for p in extra_roots)]
    found=set()
    for root in roots:
        if not root.exists(): raise ReleaseError('Configured data root is missing')
        paths=[root] if root.is_file() else root.rglob('*')
        for path in paths:
            no_links(path)
            if root.is_dir() and 'backups' in path.relative_to(root).parts: continue
            if not path.is_file(): continue
            with path.open('rb') as stream: header=stream.read(16)
            if header==b'SQLite format 3\0': found.add(path)
            elif path.suffix.lower() in {'.db','.sqlite','.sqlite3'}:
                raise ReleaseError('Configured SQLite file is invalid')
    for value in database_paths:
        path=no_links(value)
        if path not in found: raise ReleaseError('Configured database missing or outside covered roots')
    return sorted(found)


def assert_rollback_compatible(target_release,storage_root,*,database_paths=(),extra_roots=()):
    roots=[no_links(storage_root),*(no_links(p) for p in extra_roots)]
    for root in roots:
        marker=root/'.restore-hold.json'
        if root.is_dir() and (marker.exists() or marker.is_symlink()):
            raise ReleaseError('Restore hold prevents release activation; reconcile through recovery procedure')
    from .release_identity import read_release_identity
    identity=read_release_identity(target_release)
    capabilities=set(identity['capabilities']) if identity else set()
    required=set()
    databases=discover_databases(storage_root,database_paths=database_paths,extra_roots=extra_roots)
    for path in databases:
        try:
            with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as db:
                tables={row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                for table in tables:
                    quote='"'+table.replace('"','""')+'"'
                    columns={row[1] for row in db.execute('PRAGMA table_info('+quote+')')}
                    if table=='production_variations' and db.execute('SELECT 1 FROM production_variations LIMIT 1').fetchone():
                        required.add('camera-variation-v1')
                    if table=='prompt_templates' and 'rule_version' in columns:
                        required.add('exclusive-prompts-v2')
                        if db.execute("SELECT 1 FROM prompt_templates WHERE rule_version='yoyo-v3' LIMIT 1").fetchone():
                            required.add('yoyo-prompts-v3')
                    if table in ('production_drafts','production_runs'):
                        field = 'data' if table == 'production_drafts' else 'snapshot'
                        if field in columns and db.execute('SELECT 1 FROM '+quote+" WHERE json_extract("+field+",'$.prompt_rule_version')='yoyo-v3' LIMIT 1").fetchone():
                            required.add('yoyo-prompts-v3')
                    if table=='users' and 'deleted_at' in columns:
                        required.add('three-tier-accounts-v1')
                    if table=='production_run_phases':
                        required.add('explicit-run-phases-v1')
                    if table=='scheduling' and 'global_concurrency' in columns:
                        required.add('global-scheduling-v1')
                    if 'status' in columns:
                        if db.execute('SELECT 1 FROM '+quote+" WHERE status='restore_held' LIMIT 1").fetchone():
                            required.add('restore-hold-v1')
                        if table=='portrait_photos' and db.execute('SELECT 1 FROM '+quote+" WHERE status IN ('stopped','uncertain') LIMIT 1").fetchone():
                            required.add('portrait-query-windows-v1')
                        if table=='stage_tasks' and db.execute('SELECT 1 FROM '+quote+" WHERE status='uncertain' LIMIT 1").fetchone():
                            required.add('workflow-provider-state-v1')
                    if table=='stage_tasks' and 'provider_state_json' in columns:
                        if db.execute("SELECT 1 FROM stage_tasks WHERE provider_state_json IS NOT NULL AND trim(provider_state_json) NOT IN ('','{}') LIMIT 1").fetchone():
                            required.add('workflow-provider-state-v1')
                    if table=='portrait_query_windows' and db.execute('SELECT 1 FROM portrait_query_windows LIMIT 1').fetchone():
                        required.add('portrait-query-windows-v1')
        except sqlite3.Error as exc: raise ReleaseError('Database compatibility inspection failed') from exc
    missing=required-capabilities
    if missing: raise ReleaseError('Downgrade compatibility denied; target lacks '+', '.join(sorted(missing)))
    return {'databases_checked':len(databases),'required_capabilities':sorted(required)}
