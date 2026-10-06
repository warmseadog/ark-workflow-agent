"""Preview/apply only the 2026-10-06 saved-template rollback; no app imports."""
import argparse
import json
import sqlite3
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--storage-dir', type=Path, required=True)
    parser.add_argument('--apply', action='store_true', help='Without this flag, only report candidates.')
    args = parser.parse_args()
    path = (args.storage_dir/'local-preferences.db').resolve()
    if not path.is_file():
        parser.error('local-preferences.db does not exist in the supplied directory')
    with sqlite3.connect(path.as_uri()+'?mode=rw', uri=True, timeout=15) as db:
        db.execute('BEGIN IMMEDIATE' if args.apply else 'BEGIN')
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='prompt_scarf_hand_backups'").fetchone():
            print('No scarf/hand prompt backup found; nothing changed.')
            return
        rows = db.execute('SELECT b.id,b.before_content,b.after_content,p.content FROM prompt_scarf_hand_backups b LEFT JOIN prompt_templates p ON p.id=b.id').fetchall()
        restored, skipped = [], []
        for ident, before, after, current in rows:
            if current != after:
                skipped.append(ident)
                continue
            restored.append(ident)
            if args.apply:
                db.execute('UPDATE prompt_templates SET content=? WHERE id=? AND content=?', (before, ident, after))
        # Retain migration marker and backup. Re-reading templates must not re-apply.
        print(json.dumps({'applied': args.apply, 'restore': restored, 'skip_changed_or_deleted': skipped}))


if __name__ == '__main__':
    main()
