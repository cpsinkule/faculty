"""Versioned, transactional schema changes in the workspace-owned catalog.

Callers retain their existing roster lifecycle lock. A migration transaction
contains both the schema change and its ledger entry; scripts never implicitly
commit the caller's work. Existing idempotent SQL can be adopted once on upgrade.
Future changes get a new version instead of editing an already recorded script.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import re
import sqlite3


class MigrationError(RuntimeError):
    """The recorded migration history cannot safely be applied."""


@contextmanager
def migration_transaction(db, *, rebuild=()):
    """Own one immediate transaction; never commit another caller's transaction.

    `rebuild` names tables a migration recreates, which SQLite cannot change in place (a NOT NULL dropped, for one): its own
    procedure turns foreign-key enforcement off for that transaction, since dropping a referenced table would otherwise delete
    through its children. The pragma is set before BEGIN (it is a no-op inside a transaction) and read back; before commit every
    foreign key of those tables, and of every table that references them, must hold, or the whole migration rolls back.
    Enforcement is turned back on afterwards, as each pooled checkout also does.
    """
    if db.in_transaction:
        raise MigrationError("Start migrations before opening another catalog transaction.")
    if rebuild:
        db.execute("PRAGMA foreign_keys=OFF")
        if db.execute("PRAGMA foreign_keys").fetchone()[0]:
            raise MigrationError("Foreign-key enforcement could not be suspended for a table rebuild.")
    try:
        db.execute("BEGIN IMMEDIATE")
        try:
            db.execute("""CREATE TABLE IF NOT EXISTS schema_migrations (
                namespace TEXT NOT NULL, version TEXT NOT NULL, sha256 TEXT NOT NULL,
                applied_at TEXT NOT NULL, adoption TEXT NOT NULL CHECK(adoption IN ('applied','adopted')),
                PRIMARY KEY(namespace,version))""")
            yield
            if rebuild:
                _check_rebuilt(db, rebuild)
            db.commit()
        except BaseException:
            db.rollback()
            raise
    finally:
        if rebuild:
            db.execute("PRAGMA foreign_keys=ON")


def _check_rebuilt(db, tables):
    names = [row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    rebuilt = set(tables)
    checked = rebuilt | {name for name in names
                         if any(row[2] in rebuilt for row in db.execute(f'PRAGMA foreign_key_list("{name}")'))}
    for name in sorted(checked):
        violation = db.execute(f'PRAGMA foreign_key_check("{name}")').fetchone()
        if violation:
            raise MigrationError(f"A rebuilt table left a broken reference ({violation[0]} row {violation[1]} -> {violation[2]}).")


def execute_statements(db, script):
    """Execute trusted SQL without executescript's implicit pre-script commit.

    SQLite's own completeness parser keeps triggers and quoted semicolons whole.
    Transaction control belongs to migration_transaction, never to a SQL file.
    """
    pending = ""
    for character in script:
        pending += character
        if character == ";" and sqlite3.complete_statement(pending):
            _execute(db, pending)
            pending = ""
    if pending.strip():
        # A final statement need not have a semicolon; execute also accepts a
        # trailing comment. Incomplete statements still raise and roll back.
        _execute(db, pending)


def _execute(db, statement):
    leading = re.sub(r"\A(?:\s+|--[^\n]*(?:\n|$)|/\*.*?\*/)*", "", statement, flags=re.S)
    token = re.match(r"[a-zA-Z]+", leading)
    keyword = token[0].upper() if token else ""
    if keyword in {"BEGIN", "COMMIT", "END", "ROLLBACK", "SAVEPOINT", "RELEASE", "VACUUM", "ATTACH", "DETACH", "PRAGMA"}:
        raise MigrationError(f"Migration SQL cannot contain {keyword}; the runner owns the transaction.")
    db.execute(statement)


def apply_sql(db, namespace, version, script, *, adopt=False):
    """Run a new script once, or verify its recorded checksum on later starts.

    adopt=True identifies an existing module's first ledger enrollment. Its
    pre-ledger history is unknown, so current idempotent SQL is reconciled before
    recording the baseline; no claim is made about earlier file checksums.
    """
    if not db.in_transaction:
        raise MigrationError("Apply migrations inside migration_transaction.")
    if not all(isinstance(value, str) and re.fullmatch(r"[a-zA-Z0-9_.-]{1,100}", value) for value in (namespace, version)):
        raise MigrationError("Migration namespace and version need stable identifiers.")
    checksum = hashlib.sha256(script.encode("utf-8")).hexdigest()
    previous = db.execute("SELECT sha256 FROM schema_migrations WHERE namespace=? AND version=?", (namespace, version)).fetchone()
    if previous:
        if previous[0] != checksum:
            raise MigrationError(f"Applied migration {namespace}/{version} changed. Restore its original SQL and add a new migration.")
        return False
    execute_statements(db, script)
    db.execute("INSERT INTO schema_migrations(namespace,version,sha256,applied_at,adoption) VALUES(?,?,?,?,?)",
               (namespace, version, checksum, datetime.now(timezone.utc).isoformat(timespec="milliseconds"), "adopted" if adopt else "applied"))
    return True
