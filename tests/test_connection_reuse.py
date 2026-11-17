"""rosters.connect reuses a catalog connection only when it is exactly what a new one would be."""
from pathlib import Path
import os
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest

import rosters
from rosters import connect


class ConnectionReuseTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.addCleanup(rosters._close_pool)
        rosters._close_pool()
        self.path = Path(folder.name) / "faculty.sqlite3"
        with connect(self.path) as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("CREATE TABLE item(value INTEGER)")
            db.executemany("INSERT INTO item VALUES(?)", [(value,) for value in range(10)])
        rosters._close_pool()

    def other_writer(self, value):
        with sqlite3.connect(self.path) as db:  # another process's connection, as far as SQLite knows
            db.execute("INSERT INTO item VALUES(?)", (value,))
        db.close()

    def idle(self):
        return [db for _, db in rosters._idle]

    def assertClosed(self, db):
        with self.assertRaisesRegex(sqlite3.ProgrammingError, "closed database"):
            sqlite3.Connection.execute(db, "SELECT 1")  # past the thread check: the connection itself is closed

    def test_a_clean_block_returns_its_connection_and_the_next_block_sees_later_commits(self):
        with connect(self.path) as db:
            first = db
        self.assertEqual(self.idle(), [first])
        self.other_writer(10)
        with connect(self.path) as db:
            self.assertIs(db, first)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM item").fetchone()[0], 11)
            self.assertIs(db.row_factory, sqlite3.Row)
            self.assertEqual(db.execute("PRAGMA foreign_keys").fetchone()[0], 1)
            self.assertEqual(db.execute("PRAGMA journal_size_limit").fetchone()[0], 64 * 1024 * 1024)

    def test_a_connection_used_after_its_block_behaves_as_a_closed_one(self):
        with connect(self.path) as db:
            pass
        self.assertEqual(self.idle(), [db])
        for use in (lambda: db.execute("INSERT INTO item VALUES(10)"), lambda: db.executemany("INSERT INTO item VALUES(?)", [(10,)]),
                    lambda: db.executescript("INSERT INTO item VALUES(10);"), lambda: db.cursor(), db.commit, db.rollback,
                    lambda: db.set_authorizer(None), lambda: db.create_function("f", 1, abs), lambda: db.set_trace_callback(None),
                    lambda: list(db.iterdump()), db.serialize, lambda: db.getlimit(sqlite3.SQLITE_LIMIT_LENGTH), db.interrupt,
                    lambda: db.in_transaction, lambda: db.total_changes, lambda: db.autocommit, db.__enter__,
                    lambda: db.__exit__(None, None, None)):
            with self.assertRaisesRegex(sqlite3.ProgrammingError, "closed database"):
                use()
        db.close()  # closing it again changes nothing
        db.row_factory = None  # nor does a setting: it changed only a closed connection before
        with connect(self.path) as again:
            self.assertIs(again, db)
            self.assertEqual(again.execute("SELECT COUNT(*) FROM item").fetchone()[0], 10)
        self.assertEqual(self.idle(), [db])

    def test_a_cursor_left_partly_read_closes_its_connection_so_no_old_snapshot_is_served(self):
        with connect(self.path) as db:
            unfinished = db.execute("SELECT value FROM item")
            unfinished.fetchone()
            held = db
        self.assertEqual(self.idle(), [])  # an unfinished statement keeps a read snapshot: never pooled
        self.assertClosed(held)  # closed as before
        self.other_writer(10)
        with connect(self.path) as db:
            self.assertIsNot(db, held)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM item").fetchone()[0], 11)
            db.execute("BEGIN IMMEDIATE")
            db.execute("INSERT INTO item VALUES(11)")
        del unfinished

    def test_any_cursor_still_referenced_keeps_its_connection_out_of_the_pool(self):
        for use in ("cursor", "execute", "executemany", "executescript"):
            with self.subTest(use), connect(self.path) as db:
                kept = {"cursor": lambda: db.cursor(), "execute": lambda: db.execute("SELECT 1"),
                        "executemany": lambda: db.executemany("INSERT INTO item VALUES(?)", [(20,)]),
                        "executescript": lambda: db.executescript("SELECT 1;")}[use]()
            self.assertEqual(self.idle(), [])
            del kept

    def test_reusing_a_shortcut_cursor_cannot_hide_connection_state(self):
        # PERFORMANCE: every cursor must track later statements too, including the shortcuts' returned cursors.
        for shortcut in ("execute", "executemany", "executescript"):
            for runner in ("execute", "executescript"):
                with self.subTest(shortcut=shortcut, runner=runner):
                    rosters._close_pool()
                    with connect(self.path) as db:
                        cursor = {"execute": lambda: db.execute("SELECT 1"),
                                  "executemany": lambda: db.executemany("INSERT INTO item VALUES(?)", []),
                                  "executescript": lambda: db.executescript("SELECT 1;")}[shortcut]()
                        getattr(cursor, runner)("PRAGMA query_only=ON;")
                        del cursor
                    self.assertEqual(self.idle(), [])
                    with connect(self.path) as fresh:
                        fresh.execute("INSERT INTO item VALUES(10)")
                        fresh.execute("DELETE FROM item WHERE value=10")

    def test_a_shortcut_cursor_keeps_the_connections_thread_guard(self):
        # PERFORMANCE: check_same_thread=False is only for sequential pool handoffs, never concurrent cursor use.
        with connect(self.path) as db:
            cursor = db.execute("SELECT value FROM item")
            errors = []

            def use():
                for operation in (lambda: cursor.execute("SELECT 2"), cursor.fetchone, cursor.fetchmany,
                                  cursor.fetchall, lambda: next(cursor), lambda: iter(cursor), cursor.close):
                    try:
                        operation()
                    except sqlite3.ProgrammingError as error:
                        errors.append(str(error))

            worker = threading.Thread(target=use)
            worker.start()
            worker.join()
            self.assertEqual(len(errors), 7)
            self.assertTrue(all("same thread" in error for error in errors))

    def test_nested_blocks_get_distinct_connections_and_keep_their_own_transactions(self):
        with connect(self.path) as outer:
            outer.execute("BEGIN IMMEDIATE")
            outer.execute("INSERT INTO item VALUES(10)")
            with connect(self.path) as inner:
                self.assertIsNot(inner, outer)
                self.assertEqual(inner.execute("SELECT COUNT(*) FROM item").fetchone()[0], 10)  # the outer write is not committed yet
        self.assertEqual(len(self.idle()), 2)
        with connect(self.path) as first, connect(self.path) as second:
            self.assertEqual({id(first), id(second)}, {id(outer), id(inner)})
            self.assertEqual(second.execute("SELECT COUNT(*) FROM item").fetchone()[0], 11)

    def test_an_exception_rolls_back_and_closes_the_connection(self):
        with self.assertRaises(RuntimeError):
            with connect(self.path) as db:
                db.execute("INSERT INTO item VALUES(10)")
                failed = db
                raise RuntimeError("stop")
        self.assertEqual(self.idle(), [])
        self.assertClosed(failed)
        with connect(self.path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM item").fetchone()[0], 10)

    def test_a_connection_left_in_a_transaction_or_changed_is_closed(self):
        db = rosters._checkout(self.path)
        db.execute("BEGIN")
        self.assertFalse(rosters._checkin(db))
        db.rollback()
        db.close()
        for change in (lambda db: setattr(db, "isolation_level", None), lambda db: setattr(db, "text_factory", bytes),
                       lambda db: setattr(db, "autocommit", True), lambda db: db.set_authorizer(lambda *_: sqlite3.SQLITE_OK),
                       lambda db: db.set_progress_handler(lambda: 0, 1000), lambda db: db.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 10 ** 6),
                       lambda db: db.create_collation("reverse", lambda a, b: (a < b) - (a > b)),
                       lambda db: db.execute("CREATE TABLE IF NOT EXISTS blobs(data BLOB)") and db.execute("INSERT INTO blobs VALUES(zeroblob(4))")
                       and db.blobopen("blobs", "data", 1, readonly=True).close()):
            with connect(self.path) as db:
                change(db)
            self.assertEqual(self.idle(), [])

    def test_a_statement_that_could_leave_state_keeps_its_connection_out_of_the_pool(self):
        attached = self.path.with_name("attached.sqlite3")
        for statement in ("CREATE TEMP TABLE item(value INTEGER)", "CREATE TEMPORARY VIEW recent AS SELECT 1",
                          "CREATE TABLE temp.scratch(value)", "PRAGMA query_only=1", "PRAGMA query_only(1)", "PRAGMA busy_timeout=0",
                          "PRAGMA main.cache_size = 10", "PRAGMA case_sensitive_like=ON", "PRAGMA recursive_triggers=1",
                          "PRAGMA busy_timeout /* ms */ = 0", "PRAGMA\n  query_only\n  = 1",
                          f"ATTACH '{attached}' AS other"):
            for runner in ("execute", "cursor", "executescript"):
                with self.subTest(statement=statement, runner=runner):
                    with connect(self.path) as db:
                        if runner == "execute":
                            db.execute(statement)
                        elif runner == "cursor":
                            db.cursor().execute(statement)
                        else:
                            db.executescript(statement + ";")
                    self.assertEqual(self.idle(), [])
                    with connect(self.path) as db:  # a new connection: the main table, a working write, the promised timeout
                        self.assertEqual(db.execute("SELECT COUNT(*) FROM item").fetchone()[0], 10)
                        self.assertEqual(db.execute("SELECT 'QUIZ:1' LIKE 'quiz:%'").fetchone()[0], 1)
                        self.assertEqual(db.execute("PRAGMA busy_timeout").fetchone()[0], 15000)
                        db.execute("INSERT INTO item VALUES(10)")
                        db.execute("DELETE FROM item WHERE value=10")
        for statement in ("PRAGMA table_info(item)", "PRAGMA index_list(item)", "PRAGMA foreign_keys", "PRAGMA foreign_keys=OFF",
                          "PRAGMA foreign_key_check", "PRAGMA main.user_version;", "PRAGMA busy_timeout\n",
                          "SELECT template_id FROM (SELECT 1 AS template_id)"):
            with self.subTest(statement=statement):
                rosters._close_pool()
                with connect(self.path) as db:
                    db.execute(statement).fetchall()
                self.assertEqual(self.idle(), [db])

    def test_state_a_block_changed_is_reset_for_the_next(self):
        traced = []
        with connect(self.path) as db:
            db.execute("PRAGMA foreign_keys=OFF")
            db.row_factory = None
            db.set_trace_callback(traced.append)
            db.create_function("twice", 1, lambda value: value * 2, deterministic=True)
        with connect(self.path) as again:
            self.assertIs(again, db)
            traced.clear()
            self.assertEqual(again.execute("PRAGMA foreign_keys").fetchone()[0], 1)
            self.assertIs(again.row_factory, sqlite3.Row)
            self.assertEqual(traced, [])
            # The Inbox search registers its deterministic functions on every connection it uses; doing so again is harmless.
            again.create_function("twice", 1, lambda value: value * 2, deterministic=True)
            self.assertEqual(again.execute("SELECT twice(21)").fetchone()[0], 42)

    def test_a_replaced_catalog_file_is_never_read_through_the_old_connection(self):
        with connect(self.path) as old:
            pass
        replacement = self.path.with_name("replacement.sqlite3")
        with sqlite3.connect(replacement) as db:
            db.execute("CREATE TABLE item(value INTEGER)")
            db.execute("INSERT INTO item VALUES(99)")
        db.close()
        os.replace(replacement, self.path)
        with connect(self.path) as db:
            self.assertIsNot(db, old)
            self.assertEqual([tuple(row) for row in db.execute("SELECT value FROM item")], [(99,)])
        self.assertClosed(old)  # the stale connection was closed, not kept
        self.assertEqual(self.idle(), [db])

    def test_closing_a_stale_connection_leaves_the_new_databases_wal_alone(self):
        with connect(self.path) as old:
            old.execute("SELECT COUNT(*) FROM item").fetchone()  # opens its WAL
        for name in ("faculty.sqlite3", "faculty.sqlite3-wal", "faculty.sqlite3-shm"):
            (self.path.parent / name).unlink(missing_ok=True)
        writer = sqlite3.connect(self.path)
        self.addCleanup(writer.close)
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("CREATE TABLE item(value INTEGER)")
        writer.execute("INSERT INTO item VALUES(7)")
        writer.commit()
        rosters._close_pool()  # closes the old connection, whose file is gone
        self.assertClosed(old)
        self.assertTrue(self.path.with_name("faculty.sqlite3-wal").exists())
        reader = sqlite3.connect(self.path)
        self.addCleanup(reader.close)
        self.assertEqual(reader.execute("SELECT value FROM item").fetchall(), [(7,)])

    def test_closing_the_pool_leaves_no_wal_as_closing_every_connection_did(self):
        with connect(self.path) as db:
            db.execute("INSERT INTO item VALUES(10)")
        self.assertTrue(self.path.with_name("faculty.sqlite3-wal").exists())  # kept open by the idle connection
        rosters._close_pool()
        self.assertFalse(self.path.with_name("faculty.sqlite3-wal").exists())
        copy = self.path.with_name("copy.sqlite3")
        shutil.copy(self.path, copy)
        with sqlite3.connect(copy) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM item").fetchone()[0], 11)
        db.close()

    def test_memory_databases_are_never_pooled(self):
        with connect(":memory:") as db:
            db.execute("CREATE TABLE scratch(value)")
        with connect(":memory:") as again:
            self.assertIsNot(again, db)
            self.assertEqual(again.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()[0], 0)
        self.assertEqual(self.idle(), [])

    def test_the_pool_is_bounded_per_file_and_overall_oldest_first(self):
        blocks = [connect(self.path) for _ in range(rosters.POOL_PER_FILE + 2)]
        opened = [block.__enter__() for block in blocks]
        for block in blocks:
            block.__exit__(None, None, None)
        self.assertEqual(self.idle(), opened[2:])
        self.assertClosed(opened[0])
        self.assertClosed(opened[1])
        others = []
        for index in range(rosters.POOL_TOTAL):
            path = self.path.with_name(f"other-{index}.sqlite3")
            with connect(path) as db:
                others.append(db)
        self.assertEqual(len(self.idle()), rosters.POOL_TOTAL)
        self.assertEqual(self.idle(), others)

    def test_a_returned_connection_serves_another_thread_and_the_first_thread_cannot_use_it_then(self):
        with connect(self.path) as db:
            first = db
        seen, errors = {}, []

        def use():
            try:
                with connect(self.path) as db:
                    seen["db"] = db
                    seen["count"] = db.execute("SELECT COUNT(*) FROM item").fetchone()[0]
                    db.execute("BEGIN IMMEDIATE")
                    db.execute("INSERT INTO item VALUES(10)")
                    first_thread_use.wait(5)
            except Exception as error:  # noqa: BLE001 - reported below
                errors.append(error)

        first_thread_use = threading.Event()
        worker = threading.Thread(target=use)
        worker.start()
        while "db" not in seen and worker.is_alive():
            worker.join(0.01)
        with self.assertRaisesRegex(sqlite3.ProgrammingError, "same thread"):
            first.execute("SELECT 1")  # checked out by the other thread now
        first_thread_use.set()
        worker.join()
        self.assertEqual(errors, [])
        self.assertIs(seen["db"], first)
        self.assertEqual(seen["count"], 10)
        with connect(self.path) as db:
            self.assertIs(db, first)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM item").fetchone()[0], 11)

    def test_a_stale_reference_in_another_thread_cannot_close_or_change_the_block_holding_its_connection(self):
        stale = {}

        def finish():
            with connect(self.path) as db:
                db.execute("SELECT 1").fetchone()
            stale["db"] = db
        worker = threading.Thread(target=finish)
        worker.start()
        worker.join()
        with connect(self.path) as db:
            self.assertIs(db, stale["db"])
            db.execute("BEGIN IMMEDIATE")
            db.execute("INSERT INTO item VALUES(10)")
            outcome = []

            def meddle():
                for use in (stale["db"].close, lambda: stale["db"].__exit__(None, None, None), lambda: stale["db"].autocommit):
                    try:
                        use()
                        outcome.append("returned")
                    except sqlite3.ProgrammingError as error:
                        outcome.append(str(error))
                stale["db"].row_factory = None
            worker = threading.Thread(target=meddle)
            worker.start()
            worker.join()
            self.assertEqual(len(outcome), 3)
            for text in outcome:
                self.assertIn("same thread", text)
            self.assertIs(db.row_factory, sqlite3.Row)
            db.execute("INSERT INTO item VALUES(11)")
        with connect(self.path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM item").fetchone()[0], 12)

    def test_another_thread_may_evict_and_close_an_idle_connection(self):
        opened = []

        def fill():
            with connect(self.path) as db:
                opened.append(db)
        worker = threading.Thread(target=fill)
        worker.start()
        worker.join()
        self.assertEqual(self.idle(), opened)
        for index in range(rosters.POOL_TOTAL):
            with connect(self.path.with_name(f"other-{index}.sqlite3")):
                pass
        self.assertNotIn(opened[0], self.idle())
        self.assertClosed(opened[0])  # closed by this thread, without an error
        with connect(self.path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM item").fetchone()[0], 10)


CHILD = """
import os, signal, sys, threading
sys.path.insert(0, {root!r})
from rosters import connect
if {own!r}:
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(3))
with connect({path!r}) as db:
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("CREATE TABLE item(value INTEGER)")
for value in range(50):
    with connect({path!r}) as db:
        db.execute("INSERT INTO item VALUES(?)", (value,))
os.kill(os.getpid(), {signum})
threading.Event().wait(10)
"""


class TerminationTests(unittest.TestCase):
    """A process ended by a signal closes its idle pooled connections first, so its catalog needs no WAL, as before."""

    def run_child(self, signum, own=False):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        path = Path(folder.name) / "faculty.sqlite3"
        root = str(Path(rosters.__file__).resolve().parent)
        child = subprocess.run([sys.executable, "-c", CHILD.format(root=root, path=str(path), signum=int(signum), own=own)],
                               capture_output=True, text=True, timeout=60)
        return path, child

    def test_a_signal_that_ends_the_process_first_closes_the_pool(self):
        for signum in (signal.SIGTERM, signal.SIGHUP):
            with self.subTest(signal=signum.name):
                path, child = self.run_child(signum)
                self.assertEqual(child.returncode, -signum, child.stderr)  # ended by the same signal as before
                self.assertFalse(path.with_name("faculty.sqlite3-wal").exists())
                copy = path.with_name("copy.sqlite3")
                shutil.copy(path, copy)  # the stopped catalog on its own holds every commit
                db = sqlite3.connect(copy)
                self.addCleanup(db.close)
                self.assertEqual(db.execute("SELECT COUNT(*) FROM item").fetchone()[0], 50)

    def test_a_process_with_its_own_handler_keeps_it(self):
        path, child = self.run_child(signal.SIGTERM, own=True)
        self.assertEqual(child.returncode, 3, child.stderr)  # its handler ran: sys.exit, then atexit closed the pool
        self.assertFalse(path.with_name("faculty.sqlite3-wal").exists())


if __name__ == "__main__":
    unittest.main()
