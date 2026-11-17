"""The picture index: one vector per picture Faculty already holds, by the picture's SHA-256 and the model that made it,
and one more for the words Faculty stores about it (a question's text, a section's title), by those words' SHA-256.

A rebuildable cache beside the embedding runtime on NVMe, never teaching data: it holds no picture, no text and no name,
only where a picture is (a source and its row's id), hashes and 768 numbers. Deleting the file loses nothing; the worker's
turn fills it again. Who may see a picture is decided at search time from the catalog, never from here (PICTURE-SEARCH).
"""
import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROWS = 40000  # vectors held in memory for searching (about 120 MB at this bound); past it the newest are searched
WORDS = ":words"  # what `skipped.source` ends with when it is a picture's words, not the picture, that were set aside
# Set aside for good, not for a fault that may pass: true of the file, or of the words, until they change (another hash). A
# page with nothing on it is not a picture that could not be read, and is not tried again in a week.
BLANK = "Nothing is on this page."
NO_WORDS = "Nothing is stored about this picture."
SETTLED = (BLANK, NO_WORDS)
ASIDE = f"(at>=? OR reason IN ({','.join('?' * len(SETTLED))}))"


class PictureIndex:
    def __init__(self, folder):
        folder = Path(folder)
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = folder / "pictures.sqlite3"
        self._lock, self._held = threading.Lock(), None
        with self._open() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""CREATE TABLE IF NOT EXISTS pictures(source TEXT NOT NULL, ref TEXT NOT NULL, sha256 TEXT NOT NULL,
                model TEXT NOT NULL, vector BLOB NOT NULL, indexed_at TEXT NOT NULL, PRIMARY KEY(source, ref, model))""")
            db.execute("""CREATE TABLE IF NOT EXISTS skipped(source TEXT NOT NULL, ref TEXT NOT NULL, sha256 TEXT NOT NULL,
                model TEXT NOT NULL, reason TEXT NOT NULL, at TEXT NOT NULL, PRIMARY KEY(source, ref, model))""")
            # The words stored about a picture, embedded as a document: `sha256` is of the words, which are not kept here.
            db.execute("""CREATE TABLE IF NOT EXISTS texts(source TEXT NOT NULL, ref TEXT NOT NULL, sha256 TEXT NOT NULL,
                model TEXT NOT NULL, vector BLOB NOT NULL, indexed_at TEXT NOT NULL, PRIMARY KEY(source, ref, model))""")
            # PICTURE-SEARCH: workspace-owned rebuildable cache metadata. Counts
            # and maximum rowids can repeat after deletion and replacement, so
            # they cannot identify the contents cached by another process.
            db.execute("CREATE TABLE IF NOT EXISTS cache_generations(name TEXT PRIMARY KEY, version INTEGER NOT NULL)")
            for table in ("pictures", "texts"):
                db.execute("INSERT OR IGNORE INTO cache_generations VALUES(?,0)", (table,))
                for operation in ("INSERT", "UPDATE", "DELETE"):
                    db.execute(f"""CREATE TRIGGER IF NOT EXISTS {table}_{operation.lower()}_generation
                        AFTER {operation} ON {table} BEGIN
                        UPDATE cache_generations SET version=version+1 WHERE name='{table}'; END""")
        os.chmod(self.path, 0o600)
        self._held_texts = None

    def _open(self):
        return sqlite3.connect(self.path, timeout=30)

    @staticmethod
    def _now():
        return datetime.now(timezone.utc).isoformat()

    def done(self, source, model, aside_since=""):
        """{id: the picture's SHA-256} of what `model` has embedded of `source`, or set aside since `aside_since` (an ISO
        time; what was set aside earlier is due another try, unless it was set aside for good: `SETTLED`). A picture drawn
        again under the same id (a scan started over) has another hash, and so waits again."""
        with self._open() as db:
            found = dict(db.execute(f"SELECT ref, sha256 FROM skipped WHERE source=? AND model=? AND {ASIDE}", (source, model, aside_since, *SETTLED)).fetchall())
            found.update(db.execute("SELECT ref, sha256 FROM pictures WHERE source=? AND model=?", (source, model)).fetchall())
            return found

    def texts_done(self, source, model, aside_since=""):
        """{id: the SHA-256 of the words} `model` has embedded for `source`, or set aside since `aside_since`."""
        with self._open() as db:
            found = dict(db.execute(f"SELECT ref, sha256 FROM skipped WHERE source=? AND model=? AND {ASIDE}", (source + WORDS, model, aside_since, *SETTLED)).fetchall())
            found.update(db.execute("SELECT ref, sha256 FROM texts WHERE source=? AND model=?", (source, model)).fetchall())
            return found

    def refs(self, source):
        with self._open() as db:
            return {row[0] for table, name in (("pictures", source), ("skipped", source), ("skipped", source + WORDS), ("texts", source))
                    for row in db.execute(f"SELECT ref FROM {table} WHERE source=?", (name,))}

    def drop(self, source, refs):
        with self._open() as db:
            for table, name in (("pictures", source), ("skipped", source), ("skipped", source + WORDS), ("texts", source)):
                db.executemany(f"DELETE FROM {table} WHERE source=? AND ref=?", [(name, ref) for ref in refs])

    def keep_only(self, pictures, texts):
        """Forget every vector, and everything set aside, that was made under another mark than these two (another model
        revision, or pictures seen at another setting): they are never searched, and a cache keeps nothing it cannot use."""
        with self._open() as db:
            db.execute("DELETE FROM pictures WHERE model!=?", (pictures,))
            db.execute("DELETE FROM texts WHERE model!=?", (texts,))
            db.execute("DELETE FROM skipped WHERE (source LIKE ? AND model!=?) OR (source NOT LIKE ? AND model!=?)",
                       ("%" + WORDS, texts, "%" + WORDS, pictures))

    def counts(self, model):
        with self._open() as db:
            return {"indexed": db.execute("SELECT COUNT(*) FROM pictures WHERE model=?", (model,)).fetchone()[0],
                    "unreadable": db.execute("SELECT COUNT(*) FROM skipped WHERE model=? AND source NOT LIKE ? AND reason!=?", (model, "%" + WORDS, BLANK)).fetchone()[0],
                    "by_source": dict(db.execute("SELECT source, COUNT(*) FROM pictures WHERE model=? GROUP BY source ORDER BY source", (model,)).fetchall())}

    def add(self, source, ref, sha256, model, vector):
        """Keep a picture's vector, advancing the matrix generation even if SQLite reuses its rowid.
        One row per picture and model; what was set aside under that id is forgotten."""
        with self._open() as db:
            db.execute("DELETE FROM skipped WHERE source=? AND ref=? AND model=?", (source, ref, model))
            db.execute("INSERT OR REPLACE INTO pictures VALUES(?,?,?,?,?,?)",
                       (source, ref, sha256, model, np.asarray(vector, dtype=np.float32).tobytes(), self._now()))

    def skip(self, source, ref, sha256, model, reason):
        with self._open() as db:
            db.execute("DELETE FROM pictures WHERE source=? AND ref=? AND model=?", (source, ref, model))
            db.execute("INSERT OR REPLACE INTO skipped VALUES(?,?,?,?,?,?)", (source, ref, sha256, model, reason[:200], self._now()))

    def set_text(self, source, ref, sha256, model, vector):
        """Keep the vector of the words stored about a picture, or forget it (`vector` None: the words are gone)."""
        with self._open() as db:
            db.execute("DELETE FROM skipped WHERE source=? AND ref=? AND model=?", (source + WORDS, ref, model))
            if vector is None:
                db.execute("DELETE FROM texts WHERE source=? AND ref=? AND model=?", (source, ref, model))
            else:
                db.execute("INSERT OR REPLACE INTO texts VALUES(?,?,?,?,?,?)",
                           (source, ref, sha256, model, np.asarray(vector, dtype=np.float32).tobytes(), self._now()))

    def skip_text(self, source, ref, sha256, model, reason):
        """Set aside words the worker would not embed; the picture's own vector stays."""
        with self._open() as db:
            db.execute("DELETE FROM texts WHERE source=? AND ref=? AND model=?", (source, ref, model))
            db.execute("INSERT OR REPLACE INTO skipped VALUES(?,?,?,?,?,?)", (source + WORDS, ref, sha256, model, reason[:200], self._now()))

    def _matrix(self, table, held, model):
        with self._lock, self._open() as db:
            db.execute("BEGIN")
            mark = db.execute("SELECT version FROM cache_generations WHERE name=?", (table,)).fetchone()
            kept = getattr(self, held)
            if kept and kept[0] == (model, *mark):
                return kept[1], kept[2]
            rows = db.execute(f"SELECT source,ref,vector FROM {table} WHERE model=? ORDER BY rowid DESC LIMIT ?", (model, ROWS)).fetchall()
            refs = [(row[0], row[1]) for row in rows]
            matrix = np.stack([np.frombuffer(row[2], dtype=np.float32) for row in rows]) if rows else np.zeros((0, 1), dtype=np.float32)
            setattr(self, held, ((model, *mark), refs, matrix))
            return refs, matrix

    def matrix(self, model):
        """([(source, ref), ...], unit float32 rows) of every picture vector of `model`, read again only when the index has
        changed. A row is written once and never altered (a replacement is a new row), so holding them is safe (PERFORMANCE)."""
        return self._matrix("pictures", "_held", model)

    def text_matrix(self, model):
        """The same for the vectors of the words stored about pictures."""
        return self._matrix("texts", "_held_texts", model)
