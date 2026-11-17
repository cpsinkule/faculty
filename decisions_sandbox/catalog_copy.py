"""A read-only copy of the live catalog, made as course_storage.capture_catalog makes one: SQLite's online backup from a
read-only connection (the live catalog is in WAL mode; a file copy could miss committed pages), then integrity-checked, set to
DELETE journal mode and made 0400 so any stray write fails."""
import os
import sqlite3

from .safety import LIVE_CATALOG, refuse_live


def capture(destination, source=LIVE_CATALOG):
    refuse_live(destination)
    if destination.exists():
        raise ValueError('The copy already exists; each run takes its own.')
    live = sqlite3.connect(f'file:{source}?mode=ro', uri=True)
    try:
        copy = sqlite3.connect(destination)
        try:
            live.backup(copy)
            copy.execute('PRAGMA journal_mode=DELETE')
            result = copy.execute('PRAGMA integrity_check').fetchone()[0]
            if result != 'ok':
                raise ValueError('The catalog copy failed its integrity check.')
            counts = {name: copy.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
                      for name in ('comm_messages', 'student_support_items', 'assistant_actions', 'jev_calls', 'schema_migrations')}
        finally:
            copy.close()
    finally:
        live.close()
    os.chmod(destination, 0o400)
    return counts


def open_copy(path):
    """A read-only connection to the copy (rows by name; temporary tables in memory for as-of views)."""
    db = sqlite3.connect(f'file:{path}?mode=ro', uri=True, check_same_thread=False)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA temp_store=MEMORY')
    return db
