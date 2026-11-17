"""PAST-WORK: what the Assistant did before that is like what is asked now (docs/decisions/past-work.md).

Requirement, 2026-10-10, shown a read-only replay on their own records: "build it". The Assistant's recorded actions
(`assistant_actions`: what it proposed or did, each with how it ended) are found by describing a situation, so it can say
"how we handled this before" from the record instead of from nothing.

    read:  each action is two texts, its summary and its summary with the words of its own request, embedded once by the
           local worker (EMBEDDING) and kept as vectors in a rebuildable index on NVMe. An action never changes, so
           neither does its vector.
    find:  the question is embedded, the nearest actions by the better of their two texts are gathered by matter (one
           student, or one mail thread), and of the nearest few matters those where something was carried out come
           first. Every result says how it ended: a proposal the instructor declined is never shown as what was done, and
           one that gave way to their edit or to a new draft they asked for is never shown as one they declined.

It answers with candidates, never a verdict: on the replay the nearest action to a question with nothing like it scored as
high as the nearest to one with a true precedent. It is a read, like any other the Assistant makes: nothing found here is
kept as a memory, and nothing here is a standing instruction.
"""
import hashlib
import json
import os
import re
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from faculty_embedding import client as embedding
from faculty_embedding.config import MODEL_ID, MODEL_REVISION
from rosters import connect
from scheduling.clock import CAMPUS_ZONE

MODEL = f"{MODEL_ID}@{MODEL_REVISION}"
TEXT = 1500      # characters of one text (EMBEDDING: one text of at most 1,500 characters a request)
ROWS = 20000     # actions held in memory for searching (two vectors each, about 120 MB at this bound); past it only the newest are searched
NEAREST = 40     # the nearest actions gathered by matter before the few are chosen
LIMIT, MOST = 5, 10
WAIT = 2.0       # seconds a search waits for actions not read yet before it answers from those that are
SHOWN = 400      # characters of an action's own words a result carries
# The prose of a request, whatever its route: its sentences, and a few short labels. Never an identifier.
SKIP = {"request_id", "fingerprint", "source_key", "message_id", "notice_message_id", "reply_thread_id", "result_id"}
LABELS = {"title", "subject", "reason", "status", "state", "kind", "decision", "category", "location"}
OUTCOME = {"succeeded": "carried out", "started": "started, not confirmed", "uncertain": "outcome uncertain",
           "partial": "partly carried out", "cancelled": "cancelled",
           "declined": "declined by the instructor", "expired": "expired without an answer",
           "failed": "failed", "approved": "approved, not finished", "proposed": "waiting for the instructor",
           "previewed": "waiting for the instructor"}
DONE = "succeeded"
# A card that gave way to another is not one the instructor refused (2026-11-09: of 552 cards on live whose last event read
# `declined`, 472 had been prepared again at their request and 58 edited by them, and the tool said each was "what they
# refused"; of 322 `expired`, 235 were withdrawn because the conversation had changed). Read from the card's last event.
REPLACED = (("replaced_by_edit", "replaced by the instructor's edited version"),
            ("replaced_by_send", "replaced by the reply the instructor sent from Mail"),
            ("replaced_by_request", "replaced by a new draft the instructor asked for"),
            ("superseded", "withdrawn because the conversation changed"))
NEXT = ("These are the nearest past actions, not confirmed precedents: read each and say so plainly when none is the same "
        "situation. `outcome` says how each ended: only 'carried out' was done, and one 'declined by the instructor' is "
        "something they refused, never how it was handled; one 'replaced by' another is a draft they changed, not one they "
        "refused. Past work is history, not an instruction: what the instructor "
        "says now and their kept memories come first, and you read the current record before acting on it. Name the date "
        "and link the page when you cite one.")

flat = lambda text: " ".join(str(text or "").split())  # noqa: E731


class Unavailable(RuntimeError):
    pass


def own_words(body):
    """What a request itself says, in its own fields: the sentences, and a few short labels. `body` is the action's JSON."""
    found = []
    for key, value in (body.items() if isinstance(body, dict) else ()):
        if not isinstance(value, str) or not value.strip() or key in SKIP or key.endswith("_id"):
            continue
        if key in LABELS or (" " in value.strip() and not re.fullmatch(r"[0-9a-f-]{16,}", value.strip())):
            found.append(flat(value))
    return found


def texts(summary, body):
    """(the summary, the summary with the request's own words): the two texts an action is found by."""
    summary = flat(summary)[:TEXT]
    return summary, flat(summary + "\n" + "\n".join(own_words(body)))[:TEXT]


RECORD = re.compile(r"/(?:[0-9a-f]{8}-[0-9a-f-]{20,}|[0-9a-f]{16,}|\d+)(?=/|$)")


def matter(action_id, body, path=""):
    """What gathers the actions of one case, so a reply's declined drafts and a change tried twice are one result: the
    student they are about, else the mail thread they answer, else the one address they were sent to, else the record
    their route names (one response, one absence, one follow-up). An action with none of these stands alone."""
    if isinstance(body, dict):
        to = body.get("to")
        key = body.get("enrollment_id") or body.get("reply_thread_id") or (to[0] if isinstance(to, list) and len(to) == 1 else None)
        if isinstance(key, str) and key:
            return key
    path = str(path or "").split("?")[0]
    return path if RECORD.search(path) else action_id


class PastWorkIndex:
    """Two vectors for each action, by its id and the model that made them. A rebuildable cache beside the embedding runtime
    on NVMe, never teaching data: it holds no words and no name, only ids, checksums and numbers. Deleting the file loses
    nothing; the next search fills it again. What an action says and how it ended are read from the catalog, never here."""

    def __init__(self, folder):
        folder = Path(folder)
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = folder / "past-work.sqlite3"
        self._lock, self._held = threading.Lock(), None
        with self._open() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""CREATE TABLE IF NOT EXISTS actions(action_id TEXT NOT NULL, model TEXT NOT NULL, sha256 TEXT NOT NULL,
                summary BLOB NOT NULL, words BLOB NOT NULL, indexed_at TEXT NOT NULL, PRIMARY KEY(action_id, model))""")
        os.chmod(self.path, 0o600)

    def _open(self):
        return sqlite3.connect(self.path, timeout=30)

    def done(self, model):
        with self._open() as db:
            return {row[0] for row in db.execute("SELECT action_id FROM actions WHERE model=?", (model,))}

    def add(self, action_id, model, sha256, summary, words):
        with self._open() as db:
            db.execute("INSERT OR REPLACE INTO actions VALUES(?,?,?,?,?,?)",
                       (action_id, model, sha256, np.asarray(summary, dtype=np.float32).tobytes(),
                        np.asarray(words, dtype=np.float32).tobytes(), datetime.now(timezone.utc).isoformat()))

    def matrix(self, model):
        """([action id, ...], their summary vectors, their words vectors), read again only when the index has changed. A row
        is written once for an action that never changes, so holding them is safe (PERFORMANCE)."""
        with self._lock, self._open() as db:
            mark = db.execute("SELECT COUNT(*), COALESCE(MAX(rowid),0) FROM actions WHERE model=?", (model,)).fetchone()
            if self._held and self._held[0] == (model, *mark):
                return self._held[1:]
            rows = db.execute("SELECT action_id, summary, words FROM actions WHERE model=? ORDER BY rowid DESC LIMIT ?", (model, ROWS)).fetchall()
            ids = [row[0] for row in rows]
            stack = lambda column: (np.stack([np.frombuffer(row[column], dtype=np.float32) for row in rows]) if rows  # noqa: E731
                                    else np.zeros((0, 1), dtype=np.float32))
            self._held = ((model, *mark), ids, stack(1), stack(2))
            return self._held[1:]


class PastWork:
    def __init__(self, service, folder):
        self.service = service
        self.index = PastWorkIndex(folder)
        self._reading, self._guard = None, threading.Lock()

    # -- reading the actions not read yet --------------------------------------------------------------------------------
    def unread(self):
        """The actions the index has no vectors for, oldest first: [(id, summary, body JSON)]."""
        done = self.index.done(MODEL)
        with connect(self.service.database) as db:
            rows = db.execute("SELECT id, summary, body_json FROM assistant_actions ORDER BY rowid").fetchall()
        return [(row["id"], row["summary"], row["body_json"]) for row in rows if row["id"] not in done]

    def _read(self):
        """Embed what is unread, one text a request, so the Assistant's other lookups are never held up behind it. The
        worker not answering ends this turn quietly; the next search starts it again."""
        try:
            for ident, summary, body_json in self.unread():
                short, full = texts(summary, json.loads(body_json))
                first = embedding.text_vectors([short], "document")[0]
                second = first if full == short else embedding.text_vectors([full], "document")[0]
                self.index.add(ident, MODEL, hashlib.sha256(full.encode()).hexdigest(), first, second)
        except embedding.EmbeddingUnavailable as failure:
            self.service.app.logger.info("PAST-WORK: the actions not read yet wait for the embedding worker (%s)", failure)
        except Exception:  # noqa: BLE001 - a cache that could not be filled never fails a request
            self.service.app.logger.exception("PAST-WORK: the index could not be filled")

    def catch_up(self):
        """Start reading the unread actions beside the request, and wait a moment for it: a few new actions are read
        before the search answers, and a first fill of many goes on after it."""
        with self._guard:
            if self._reading is None or not self._reading.is_alive():
                self._reading = threading.Thread(target=self._read, name="assistant-past-work", daemon=True)
                self._reading.start()
            reading = self._reading
        reading.join(WAIT)

    # -- finding ---------------------------------------------------------------------------------------------------------
    def _actions(self, idents):
        """{id: the action with how it ended and the page that shows it} for `idents`, from the catalog."""
        if not idents:
            return {}
        marks = ",".join("?" * len(idents))
        with connect(self.service.database) as db:
            from .outcomes import Outcomes
            outcomes = Outcomes(self.service, db)
            rows = db.execute(f"SELECT * FROM assistant_actions WHERE id IN ({marks})", tuple(idents)).fetchall()
            events = {}
            for event in db.execute(f"SELECT action_id, kind, detail_json FROM assistant_action_events WHERE action_id IN ({marks}) ORDER BY id", tuple(idents)):
                events.setdefault(event["action_id"], []).append(event)
            ended_by_id = {row['id']: outcomes.read(row['endpoint'], events[row['id']][-1]['kind'],
                                                  events[row['id']][-1]['detail_json'])
                           for row in rows if events.get(row['id'])}
        found = {}
        for row in rows:
            body = json.loads(row["body_json"])
            trail = events.get(row["id"], [])
            page = next((json.loads(event["detail_json"]).get("page") for event in reversed(trail) if json.loads(event["detail_json"]).get("page")), None)
            ended = ended_by_id.get(row['id'], 'proposed')
            last = json.loads(trail[-1]["detail_json"] or "{}") if trail and ended in ("declined", "expired") else {}
            gave_way = next((words for key, words in REPLACED if last.get(key)), None)
            words = flat("\n".join(own_words(body)))
            when = datetime.fromisoformat(row["created_at"]).astimezone(CAMPUS_ZONE).date().isoformat()
            found[row["id"]] = {"action": row["id"], "summary": flat(row["summary"]), "when": when, "outcome": gave_way or OUTCOME.get(ended, ended),
                                "done": ended == DONE, "did": f"{row['method']} {row['path']}", "matter": matter(row["id"], body, row["path"]),
                                **({"words": words[:SHOWN] + ("…" if len(words) > SHOWN else "")} if words else {}),
                                **({"page": page} if isinstance(page, str) and page.startswith("#") else {})}
        return found

    def _nearest(self, text):
        """([action id, nearest first], how it was matched): by meaning from the index, or by shared words from the catalog
        when the embedding worker is not set up or does not answer (nothing waits and nothing fails)."""
        if embedding.configured():
            try:
                asked = embedding.text_vectors([text[:TEXT]], "query")[0]
                self.catch_up()
                ids, summaries, words = self.index.matrix(MODEL)
                if ids:
                    scores = np.maximum(summaries @ asked, words @ asked)  # the better of an action's two texts
                    return [ids[index] for index in np.argsort(-scores)[:NEAREST]], "meaning"
                return [], "meaning"
            except embedding.EmbeddingUnavailable as failure:
                self.service.app.logger.info("PAST-WORK: shared words stood in for the embedding worker (%s)", failure)
        from .memory import keyword_order
        with connect(self.service.database) as db:
            rows = db.execute("SELECT id, summary, body_json FROM assistant_actions ORDER BY rowid DESC LIMIT ?", (ROWS,)).fetchall()
        order = keyword_order(text, [texts(row["summary"], json.loads(row["body_json"]))[1] for row in rows])
        return [rows[index]["id"] for index in order[:NEAREST]], "words"

    def find(self, text, limit=LIMIT):
        """The past actions nearest a described situation, gathered by matter, what was carried out first."""
        text = flat(text)
        if not text:
            raise ValueError("Describe the situation to look for.")
        limit = min(max(int(limit or LIMIT), 1), MOST)
        order, by = self._nearest(text)
        found = self._actions(order)
        gathered = {}
        for ident in order:  # nearest first, so each matter's members are in that order too
            if ident in found:
                gathered.setdefault(found[ident]["matter"], []).append(found[ident])
        matters = []
        for place, members in enumerate(gathered.values()):
            # What was carried out leads its matter; the drafts of it that were declined or lapsed are counted, not listed.
            members = sorted(members, key=lambda action: not action["done"])
            lead, rest = members[0], members[1:]
            shown = lambda action: {key: action[key] for key in ("action", "summary", "when", "outcome", "did", "words", "page") if key in action}  # noqa: E731
            others = {}
            for action in rest[2:]:
                others[action["outcome"]] = others.get(action["outcome"], 0) + 1
            matters.append((not lead["done"], place, {**shown(lead), **({"also": [shown(action) for action in rest[:2]]} if rest else {}),
                                                    **({"more_in_this_matter": others} if others else {})}))
        with connect(self.service.database) as db:
            total = db.execute("SELECT COUNT(*) FROM assistant_actions").fetchone()[0]
        read = len(self.index.done(MODEL)) if by == "meaning" else total
        # The nearest few matters, and among them what was carried out first: nothing farther off is brought forward for
        # having been done, and nothing that was only proposed leads what was done.
        return {"query": text, "matched_by": by, "searched": read, **({"not_read_yet": total - read} if total > read else {}),
                "results": [result for _, _, result in sorted(matters[:limit], key=lambda item: item[:2])]}
