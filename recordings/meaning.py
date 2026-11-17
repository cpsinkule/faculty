"""Recordings, step 6: SEARCH BY MEANING (docs/decisions/recordings.md, RECORDINGS "Memos, training, search by meaning (step 6)";
EMBEDDING "WHAT IS SENT"). What was said is found by DESCRIBING it, where the keyword search needs the words themselves.

    read:  a kept transcript's lines are gathered into PASSAGES (consecutive lines, about a thousand characters), and each
           passage's words are embedded once by the local worker (EMBEDDING: one text of at most 1,500 characters a request,
           on this machine, nothing kept by the worker) and kept as a vector in a rebuildable index on NVMe. The capture
           thread gives it a turn after each of its ticks, outside the capture lock: at most BATCH passages. Only the latest
           transcript of a recording filed as Faculty's NOW is read: one that needs filing or is Not Faculty's has no kept
           transcript, and one re-filed as Not Faculty's is no longer read and its vectors are dropped.
    find:  the description is embedded as a query and compared with the kept vectors. WHO MAY BE SHOWN IS DECIDED AT SEARCH
           TIME FROM THE CATALOG, never from the index: a passage is answered only while its transcript is the latest one of
           a recording filed as Faculty's now, and its words are read from the catalog's own lines, never from here.

The index is the workspace's rebuildable computing, as the picture index and the past-work index are: it holds no word, no
name and no student, only which lines of which kept transcript file a vector was made from, the checksum of those words and
the numbers. Deleting the file loses nothing; the worker's turns fill it again. NEAREST IS NEVER A FINDING: a score orders
the passages and nothing else, and is not shown.
"""
import hashlib
import logging
import os
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from faculty_embedding import client as embedding
from faculty_embedding.config import MODEL_ID, MODEL_REVISION
from rosters import connect

from . import transcript
from .service import LATEST, LINKS

LOG = logging.getLogger(__name__)
MODEL = f"{MODEL_ID}@{MODEL_REVISION}"
PASSAGE = 1000       # characters a passage is gathered to (its last line may carry it a little past)
TEXT = 1500          # ... and the most of one that is embedded (EMBEDDING: one text of at most 1,500 characters a request)
FEW_WORDS = 5        # a passage of fewer words says too little to be found by its meaning, and is near anything vague
BATCH = 8            # passages embedded in one turn of the worker (each one request of at most 1.5 seconds)
ROWS = 40000         # vectors held in memory for searching (about 120 MB at this bound); past it the newest are searched
NEAREST, MOST = 6, 12
TRIES = 3            # a passage the worker would not embed this many times in a row is set aside
ASIDE = timedelta(days=7)   # ... and tried again after this long
QUIET = 30           # seconds between looks while nothing waits
OFF = "Search by meaning is not set up on this Faculty instance."
SILENT = "Search by meaning is not answering now. The words you typed were still searched."


def gathered(lines):
    """A transcript's lines gathered into passages, in order: [{first, last, text}], `first` and `last` the places of its
    lines and `text` their words alone (no speaker, no time). A new voice does not end a passage; about PASSAGE characters
    do. A passage of fewer than FEW_WORDS words is left out."""
    found, held, size = [], [], 0
    for line in lines:
        held.append(line)
        size += len(line["text"]) + 1
        if size >= PASSAGE:
            found.append(held)
            held, size = [], 0
    if held:
        found.append(held)
    passages = [{"first": group[0]["position"], "last": group[-1]["position"], "text": " ".join(line["text"] for line in group)[:TEXT]}
                for group in found]
    return [passage for passage in passages if len(passage["text"].split()) >= FEW_WORDS]


def mark(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class PassageIndex:
    """One vector for each passage, by the kept transcript file and the place of its first line. It holds no words."""

    def __init__(self, folder):
        folder = Path(folder)
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = folder / "passages.sqlite3"
        self._lock, self._held = threading.Lock(), None
        with self._open() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""CREATE TABLE IF NOT EXISTS passages(file_id TEXT NOT NULL, first INTEGER NOT NULL, last INTEGER NOT NULL,
                sha256 TEXT NOT NULL, model TEXT NOT NULL, vector BLOB NOT NULL, indexed_at TEXT NOT NULL, PRIMARY KEY(file_id, first, model))""")
            # A transcript file never changes, so one whose every passage is in is never gathered again.
            db.execute("""CREATE TABLE IF NOT EXISTS files(file_id TEXT NOT NULL, model TEXT NOT NULL, passages INTEGER NOT NULL,
                at TEXT NOT NULL, PRIMARY KEY(file_id, model))""")
            db.execute("""CREATE TABLE IF NOT EXISTS skipped(file_id TEXT NOT NULL, first INTEGER NOT NULL, model TEXT NOT NULL,
                reason TEXT NOT NULL, at TEXT NOT NULL, PRIMARY KEY(file_id, first, model))""")
            # Rows are deleted and replaced (a recording re-filed, another model revision), so a count and a largest rowid do
            # not say what a held matrix holds: a counter every change advances does (as PICTURE-SEARCH's index keeps one).
            db.execute("CREATE TABLE IF NOT EXISTS cache_generations(name TEXT PRIMARY KEY, version INTEGER NOT NULL)")
            db.execute("INSERT OR IGNORE INTO cache_generations VALUES('passages',0)")
            for operation in ("INSERT", "UPDATE", "DELETE"):
                db.execute(f"""CREATE TRIGGER IF NOT EXISTS passages_{operation.lower()}_generation AFTER {operation} ON passages BEGIN
                    UPDATE cache_generations SET version=version+1 WHERE name='passages'; END""")
        os.chmod(self.path, 0o600)

    def _open(self):
        return sqlite3.connect(self.path, timeout=30)

    @staticmethod
    def _now():
        return datetime.now(timezone.utc).isoformat()

    def finished(self, model):
        """The transcript files whose every passage `model` has embedded or set aside."""
        with self._open() as db:
            return {row[0] for row in db.execute("SELECT file_id FROM files WHERE model=?", (model,))}

    def have(self, file_id, model):
        """The first lines of the passages of one file that are in, or set aside lately (older ones are due another try)."""
        since = (datetime.now(timezone.utc) - ASIDE).isoformat()
        with self._open() as db:
            found = {row[0] for row in db.execute("SELECT first FROM passages WHERE file_id=? AND model=?", (file_id, model))}
            found.update(row[0] for row in db.execute("SELECT first FROM skipped WHERE file_id=? AND model=? AND at>=?", (file_id, model, since)))
            return found

    def add(self, file_id, first, last, sha256, model, vector):
        with self._open() as db:
            db.execute("DELETE FROM skipped WHERE file_id=? AND first=? AND model=?", (file_id, first, model))
            db.execute("INSERT OR REPLACE INTO passages VALUES(?,?,?,?,?,?,?)",
                       (file_id, first, last, sha256, model, np.asarray(vector, dtype=np.float32).tobytes(), self._now()))

    def aside(self, file_id, model):
        """Whether a passage of this file is set aside: the file is then looked at again, so that passage gets its later try."""
        with self._open() as db:
            return bool(db.execute("SELECT 1 FROM skipped WHERE file_id=? AND model=? LIMIT 1", (file_id, model)).fetchone())

    def skip(self, file_id, first, model, reason):
        with self._open() as db:
            db.execute("INSERT OR REPLACE INTO skipped VALUES(?,?,?,?,?)", (file_id, first, model, reason[:200], self._now()))

    def finish(self, file_id, model, passages):
        with self._open() as db:
            db.execute("INSERT OR REPLACE INTO files VALUES(?,?,?,?)", (file_id, model, passages, self._now()))

    def keep_only(self, model, files):
        """Forget what another model revision made, and every file that is not one of `files` (a recording filed as Not
        Faculty's since, a transcript Plaud changed): a cache keeps nothing it may not show. Returns how many passages went."""
        with self._open() as db:
            gone = db.execute("DELETE FROM passages WHERE model!=?", (model,)).rowcount
            db.execute("DELETE FROM files WHERE model!=?", (model,))
            db.execute("DELETE FROM skipped WHERE model!=?", (model,))
            held = {row[0] for table in ("passages", "files", "skipped") for row in db.execute(f"SELECT DISTINCT file_id FROM {table}")}
            for file_id in held - set(files):
                gone += db.execute("DELETE FROM passages WHERE file_id=?", (file_id,)).rowcount
                db.execute("DELETE FROM files WHERE file_id=?", (file_id,))
                db.execute("DELETE FROM skipped WHERE file_id=?", (file_id,))
            return gone

    def strays(self, model, files):
        """Whether the index holds anything of a file that is not one of `files`, or made under another mark than `model`."""
        with self._open() as db:
            held = {row[0] for table in ("passages", "files", "skipped") for row in db.execute(f"SELECT DISTINCT file_id FROM {table}")}
            other = any(db.execute(f"SELECT 1 FROM {table} WHERE model!=? LIMIT 1", (model,)).fetchone() for table in ("passages", "files", "skipped"))
        return other or bool(held - set(files))

    def count(self, model):
        with self._open() as db:
            return db.execute("SELECT COUNT(*) FROM passages WHERE model=?", (model,)).fetchone()[0]

    def matrix(self, model):
        """([(file id, first, last, sha256), ...], unit float32 rows), read again only when the index has changed."""
        with self._lock, self._open() as db:
            db.execute("BEGIN")
            version = db.execute("SELECT version FROM cache_generations WHERE name='passages'").fetchone()
            if self._held and self._held[0] == (model, *version):
                return self._held[1], self._held[2]
            rows = db.execute("SELECT file_id, first, last, sha256, vector FROM passages WHERE model=? ORDER BY rowid DESC LIMIT ?", (model, ROWS)).fetchall()
            refs = [tuple(row[:4]) for row in rows]
            matrix = np.stack([np.frombuffer(row[4], dtype=np.float32) for row in rows]) if rows else np.zeros((0, 1), dtype=np.float32)
            self._held = ((model, *version), refs, matrix)
            return refs, matrix


class Meaning:
    def __init__(self, record, folder):
        self.record, self.path = record, record.path
        self.index = PassageIndex(folder)
        self._failed, self._next = {}, 0.0

    # -- What may be read, and shown ---------------------------------------------------------------------------------------
    @staticmethod
    def current(db):
        """{kept transcript file: its recording} for the latest readable transcript of every recording filed as Faculty's
        NOW, newest recording first. A recording that needs filing or is Not Faculty's is not here, whatever it once kept."""
        return {row["file_id"]: row["id"] for row in db.execute(f"""SELECT v.id, v.transcript_file_id file_id FROM ({LATEST}) v
            JOIN recording_transcripts t ON t.file_id=v.transcript_file_id AND t.readable=1 AND t.lines>0
            WHERE EXISTS ({LINKS} AND l.recording_id=v.id AND l.kind!='not_faculty') ORDER BY v.started_at DESC, v.id""")}

    @staticmethod
    def _lines(db, file_id, first=0, last=2 ** 62):
        return [dict(row) for row in db.execute("""SELECT position, start_ms, end_ms, speaker, speaker_key, text FROM recording_lines
            WHERE file_id=? AND position BETWEEN ? AND ? ORDER BY position""", (file_id, first, last))]

    # -- Reading: the worker's turn -----------------------------------------------------------------------------------------
    def turn(self, now=None):
        """One turn beside the capture loop, under no lock: at most BATCH passages of the newest recording that is not all in
        are embedded, one a request. A worker that does not answer (down, slow, or lending the GPU) ends the turn and blames
        no passage; one that refuses the same passage TRIES times in a row while it answers has that passage set aside.
        With nothing waiting, what may no longer be shown is dropped. Returns how many passages were embedded."""
        if not embedding.configured():
            return 0
        clock = datetime.now(timezone.utc).timestamp() if now is None else now
        if clock < self._next:
            return 0
        with connect(self.path) as db:
            current = self.current(db)
        finished = self.index.finished(MODEL)
        waiting = [file_id for file_id in current if file_id not in finished]
        if self.index.strays(MODEL, current):
            # What may no longer be shown goes at this turn (a recording filed as Not Faculty's since, a transcript Plaud
            # changed, another model revision), whether or not anything else waits.
            self.index.keep_only(MODEL, current)
        made = 0
        for file_id in waiting:   # newest recording first; one whose passage is set aside holds nothing behind it
            with connect(self.path) as db:
                passages = gathered(self._lines(db, file_id))
            have, aside = self.index.have(file_id, MODEL), False
            for passage in passages:
                if passage["first"] in have:
                    continue
                if made >= BATCH:
                    return made   # the rest in the next turn
                key = (file_id, passage["first"])
                try:
                    vector = embedding.text_vectors([passage["text"]], "document")[0]
                except embedding.CardLent:
                    self._next = clock + QUIET
                    return made   # the GPU is lent to another model: nobody's fault, and it comes back
                except embedding.EmbeddingUnavailable as failure:
                    self._failed[key] = self._failed.get(key, 0) + 1
                    if self._failed[key] >= TRIES and self._answers():
                        # The worker is up and will not take this one: set it aside so it holds nothing behind it.
                        self.index.skip(file_id, passage["first"], MODEL, str(failure))
                        self._failed.pop(key, None)
                        aside = True
                        continue
                    self._next = clock + QUIET
                    return made
                self._failed.pop(key, None)
                self.index.add(file_id, passage["first"], passage["last"], mark(passage["text"]), MODEL, vector)
                made += 1
            if not aside and not self.index.aside(file_id, MODEL):
                self.index.finish(file_id, MODEL, len(passages))   # every passage is in: never gathered again
        if not made:
            self._next = clock + QUIET
        return made

    @staticmethod
    def _answers():
        """Whether the worker answers a status request and holds the GPU: only then is a refusal about the passage."""
        try:
            return embedding.status().get("lent") is not True
        except embedding.EmbeddingUnavailable:
            return False

    # -- Finding ---------------------------------------------------------------------------------------------------------------
    def status(self):
        """Where the index stands, from the catalog and the index alone (the worker is not asked)."""
        if not embedding.configured():
            return {"state": "off", "detail": OFF, "indexed": 0, "waiting": 0}
        with connect(self.path) as db:
            current = self.current(db)
        finished = self.index.finished(MODEL)
        return {"state": "ok", "detail": "", "indexed": self.index.count(MODEL), "waiting": sum(1 for file_id in current if file_id not in finished)}

    def search(self, text, limit=NEAREST, shown=(), only=None):
        """(the passages nearest a description, where the search stands). Each passage is its recording, where it begins and
        ends, and its lines with who spoke, read from the catalog now. `shown`: (recording, line) pairs a keyword search
        already answers; a passage holding one is left out. `only`: one recording's passages alone. Without the worker
        nothing is answered and the state says why: nothing falls back to a hosted service."""
        text = " ".join(str(text or "").split())
        state = self.status()
        if state["state"] == "off" or not text:
            return [], state
        try:
            asked = embedding.text_vectors([text[:TEXT]], "query")[0]
        except embedding.EmbeddingUnavailable as failure:
            LOG.info("RECORDINGS: search by meaning was not answered (%s)", failure)
            return [], {**state, "state": "unavailable", "detail": SILENT}
        refs, matrix = self.index.matrix(MODEL)
        if not refs:
            return [], state
        order = np.argsort(-(matrix @ asked))
        limit, found, taken = min(max(int(limit or NEAREST), 1), MOST), [], set(shown)
        with connect(self.path) as db:
            current = self.current(db)   # WHO MAY BE SHOWN: decided now, from the catalog, never from the index
            for place in order:
                file_id, first, last, sha256 = refs[int(place)]
                recording = current.get(file_id)
                if recording is None or (only and recording != only):
                    continue   # no longer Faculty's, or no longer its latest transcript: dropped at the next tidy
                lines = self._lines(db, file_id, first, last)
                if not lines or mark(" ".join(line["text"] for line in lines)[:TEXT]) != sha256:
                    continue   # not the words this vector was made from: never shown as if it were
                if any((recording, line["position"]) in taken for line in lines):
                    continue
                found.append({"recording_id": recording, "file_id": file_id, "lines": lines})
                if len(found) >= limit:
                    break
        return found, state
