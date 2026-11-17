"""Recordings: Intake (step 1), the recording page (step 2) and where the reading is called from (step 3)
(docs/decisions/recordings.md, RECORDINGS; requirement, 2026-11-12).

CAPTURE runs beside the Inbox worker loop (`recordings-capture`), under its own lock (never the execution lock), every ten minutes
and whenever the instructor asks (Check now, or filing a recording by hand). One round:

  0. ASK THE INTAKE how it stands (`status`), and on a round the instructor asked for (Check now, or a recording filed by hand) have
     it make a round of its own against Plaud (`check-now`) first. Faculty asks Plaud nothing (recordings/intake.py).
  1. LIST what the Plaud intake's archive holds that began since three days ago, and never less than back to a day before the last
     round that succeeded; the first round of each campus day and every Check now list thirty days, so a recording synced late or
     made while capture was down is still found. A recording Plaud no longer has but the archive holds is listed like any other. A
     recording seen for the first time gets its list row and nothing else: its ID, title, start and length. One that is NOT KEPT is
     passed over: nothing of it is recorded again.
  2. FILE each recording that is not filed yet by where its start falls on the calendar (recordings/filing.py), without a card: a
     student's appointment, a section's class meeting, or an entry naming a current term's course. One the calendar shows to be
     nothing of Faculty's is NOT KEPT: its list row leaves the catalog and only Plaud's ID for it stays (`recording_not_kept`).
     Anything else WAITS for the instructor's Keep or Don't keep, and is tried again each round while the archive lists it.
  3. FETCH each recording filed as Faculty's that is not kept yet: the archive's answer for that one recording (`show`), then its
     transcript, outline and marks as archived and Plaud's notes rebuilt in Faculty's form, each checked against the archive's
     checksum and each only when it differs from what Faculty keeps; all into one bundle of verified storage (recordings/storage.py)
     and one new revision. NO AUDIO IS COPIED: it plays from the archive (`audio`). A recording that waits or is not kept is NEVER
     read from the archive. A recording the archive does not hold whole (no transcript or no audio yet) is asked about again, less
     and less often; one already kept is read again about 1, 6, 24 and 72 hours later and then no more, and a transcript the
     archive kept a newer version of becomes a new revision.

STEP 2 (the recording page): a kept transcript's lines are read into the catalog when it is kept (`recording_lines`; one kept
before that is read by the round, from verified storage), so a recording opens on its transcript (`recording`), plays from any
line (`audio`: the archive's file, checked against the intake's checksum on every read; or the file Faculty kept before it played
from the archive), is found by its words (`search`, which writes nothing), and a speaker may
be named in Faculty (`name_speaker`: a revision here, never written to Plaud).

STEP 3 (the reading: recordings/readings.py, `self.reading`): after a round has said what it says of Plaud, the recordings filed
under a term or a course whose transcript is read into lines are READ by the Assistant's own engine, on their own, at most two a
round; Read again is made by the worker within seconds (`run_due`). A recording's page then opens on what came out of it
(`recording` answers `reading`, `items`, `counts`, `coordinator`), and the list counts what each needs of the instructor.

STEP 4 (a student's meeting: recordings/meetings.py, recordings/readings.py): a recording filed under a student is read too;
`set_students` gives each of several students their part of one recording; a recording that began inside an appointment
recorded as cancelled or a no-show is marked, and nothing of the appointment is changed.

STEP 6 (memos, training, search by meaning): a recording filed as a MEMO is read too (recordings/readings.py), and each thing
it asks for is put to the Assistant as a request (recordings/memos.py); a TRAINING is filed under a course and kept for
reference, not read (`trainings`); and a kept transcript's passages are embedded on this machine, beside the capture loop and
under no lock, so `search` also finds what was said by DESCRIBING it (recordings/meaning.py, `self.meaning`).

ONLY WHAT IS FACULTY'S IS KEPT (requirement, 2026-11-14).
`discard` is their Don't keep: of a recording that waits, or of one Faculty had kept, which is then ERASED: every row of it
leaves the catalog (`_erase_rows`: the one way out of tables that are otherwise retained, migration 005) and its files leave
both drives through the storage engine (`_remove_erased`). The recording is hidden at once (a `not_faculty` link) and erased
under the capture lock, by the press itself or by the next round, so nothing is fetched or read for it meanwhile. `ask_again`
has what was not kept listed again, to be asked about. What the instructor approved out of a recording stands.

Nothing is ever sent to Plaud: the Plaud intake is the one program that talks to it. When the intake says the sign-in needs making
again, capture says "Sign in to Plaud again" (and still files and copies what the archive holds); anything else that goes wrong
waits for the next round and says why.

Owners: see recordings/migrations/001_recordings.sql. Lock order: the capture lock (one round at a time, across the web and worker
processes), catalog and calendar reads, network reads with no lock held, then for each write the roster lifecycle lock, the new
bundle's owner lock and ONE catalog transaction.
"""
import hashlib
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from werkzeug.exceptions import Conflict, NotFound

import rounds
from canonical_json import encoded
from catalog_migrations import apply_sql, migration_transaction
from course_storage import StorageError, exclusive, timestamp
from request_ids import valid_request_id
from rosters import connect
from scheduling.clock import ZONE_NAME

from . import filing, intake, transcript
from .storage import RecordingFiles

LOG = logging.getLogger(__name__)
LOCK = "8f2b6c1e-3d47-4a09-b6e5-0c9a7d21f4b3"   # one capture round at a time, across the web and worker processes
EVERY = 600                 # seconds between rounds
STALE = timedelta(minutes=30)   # the intake's last listing of Plaud older than this is said to be old
TICK = 20                   # how often the worker looks whether a round is due or asked for
WINDOW = timedelta(days=3)  # how far back a round lists at least
OVERLAP = timedelta(days=1) # ... and always back to this long before the last round that succeeded (capture was down, a late sync)
DEEP = timedelta(days=30)   # ... and this far back in the first round of each campus day and on Check now
# A kept recording is read again about 1 h, 6 h, 24 h and 72 h after it was kept (a transcript Plaud changed), then no more:
# the seconds from each read to the next. A read that finds a change starts the count again.
REREADS = (3600, 5 * 3600, 18 * 3600, 48 * 3600)
RETRY = 600                 # a recording the archive does not hold whole is asked about again after this long, doubling to a day
FETCHES = 12                # recordings copied from the archive in one round (a round is short); the rest follow a minute later
SHOWN = 300                 # the list page's most recent recordings (every one that needs filing is always shown)
READS = 5                   # transcripts kept before their lines were read (step 2), read into lines in one round
FOUND = 20                  # the lines one search answers unless asked for more, and
FOUND_MOST = 100            # ... the most it answers
KINDS = ("student", "term", "course", "training", "class_day", "memo")   # `training` (step 6): under a course, kept for reference
# A recording that is leaving: the instructor said Don't keep (or an earlier version marked it Not Faculty's), and it is erased
# under the capture lock. Until then it is shown nowhere and nothing of it is served.
GOING = "EXISTS (SELECT 1 FROM recording_links g WHERE g.recording_id=r.id AND g.kind='not_faculty' AND g.state='filed' " \
        "AND g.revision=(SELECT MAX(revision) FROM recording_links WHERE recording_id=g.recording_id AND owner_key=g.owner_key))"
ASKED_AGAIN = "You asked to be asked about this one again."
ERASES = 20                 # recordings erased in one round (each is a few statements and a folder; the rest follow a minute later)
ASK_BACK = timedelta(days=730)   # how far back the archive is listed once, to find what the instructor asked to be asked about again
FILE_ROLES = ("transcript", "outline", "marks", "notes", "audio")
LATEST = """SELECT r.id, r.plaud_id, r.captured_at, v.revision, v.source, v.title, v.started_at, v.start_known, v.duration_ms,
       v.transcript_file_id, v.outline_file_id, v.marks_file_id, v.notes_file_id, v.audio_file_id, v.speakers_json
    FROM recordings r JOIN recording_revisions v ON v.recording_id=r.id
     AND v.revision=(SELECT MAX(revision) FROM recording_revisions WHERE recording_id=r.id)"""
LINKS = """SELECT l.* FROM recording_links l WHERE l.state='filed'
     AND l.revision=(SELECT MAX(revision) FROM recording_links WHERE recording_id=l.recording_id AND owner_key=l.owner_key)"""


def iso(moment):
    """One format for every time the capture state holds, so they compare as text."""
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def stamp(moment):
    """A recording's start as its revision keeps it: the instant, in UTC, to the second."""
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse(value):
    try:
        found = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    return found if found.tzinfo else found.replace(tzinfo=timezone.utc)


def owner_key(target):
    kind = target["kind"]
    if kind == "enrollment":
        return f"enrollment:{target['enrollment_id']}"
    if kind == "term":
        return f"term:{target['term_id']}"
    if kind == "course":
        return f"course:{target['definition_id']}"
    if kind == "class_day":
        return f"class_day:{target['section_id']}:{target['class_date']}"
    return kind


def _digest(fields):
    return hashlib.sha256(encoded(fields).encode("utf-8")).hexdigest()


class RecordingService:
    def __init__(self, rosters, calendar=None, google_calendar=None, students=None, client=None):
        self.rosters, self.path, self.storage = rosters, rosters.path, rosters.storage
        self.calendar, self.google, self.students = calendar, google_calendar, students
        with self.rosters.lock(), connect(self.path) as db, migration_transaction(db):
            for migration in sorted((Path(__file__).parent / "migrations").glob("*.sql")):
                apply_sql(db, "recordings", migration.stem, migration.read_text(encoding="utf-8"))
        self.files = RecordingFiles(rosters)
        self.client = client or intake.IntakeClient()
        self.now = lambda: datetime.now(timezone.utc)   # the one clock of capture (tests set it)
        from .readings import Readings
        self.reading = Readings(self)   # step 3: the reading of a kept transcript, its items, and what they may become
        self.meaning = None             # step 6: search by meaning (recordings/meaning.py), set by create_app with its index folder

    # -- State -------------------------------------------------------------------------------------------------------------
    def _state(self, db=None):
        if db is not None:
            return dict(db.execute("SELECT * FROM recording_capture_state WHERE id=1").fetchone())
        with connect(self.path) as own:
            return self._state(own)

    def _set(self, **values):
        values["updated_at"] = timestamp()
        with connect(self.path) as db:
            db.execute(f"UPDATE recording_capture_state SET {', '.join(k + '=?' for k in values)} WHERE id=1", tuple(values.values()))

    def _fetch_state(self, db, recording_id, **values):
        values["updated_at"] = timestamp()
        db.execute("INSERT OR IGNORE INTO recording_fetch_state(recording_id) VALUES (?)", (recording_id,))
        db.execute(f"UPDATE recording_fetch_state SET {', '.join(k + '=?' for k in values)} WHERE recording_id=?",
                   (*values.values(), recording_id))

    def status(self, state=None):
        """Where capture stands, from the catalog and configuration alone (nothing is contacted)."""
        state = state or self._state()
        setup = self.client.setup()
        return {"status": state["status"], "detail": state["detail"], "counts": json.loads(state["counts_json"] or "{}"),
                "last_round_at": state["last_round_at"] or None, "last_success_at": state["last_success_at"] or None,
                "next_due_at": state["next_due_at"] or None, "every_seconds": EVERY,
                "requested": bool(state["requested_at"] and state["requested_at"] > (state["last_round_at"] or "")),
                "sign_in": state["status"] == "sign_in", "ready": not setup, "setup": setup}

    def attention(self):
        """What needs the instructor here: recordings to file, signing in to Plaud again, (step 3) the items of read meetings
        that are open for them, and (step 4) recordings that began inside an appointment recorded as one that did not happen.
        What Home may show (nothing is added to /api/state)."""
        with connect(self.path) as db:
            waiting = db.execute(f"SELECT COUNT(*) FROM recordings r WHERE NOT EXISTS ({LINKS} AND l.recording_id=r.id)").fetchone()[0]
            sign_in = self._state(db)["status"] == "sign_in"
            # Step 4: recordings that began inside an appointment recorded as cancelled or a no-show.
            from student_support.service import appointments
            inside = [(row["recording_id"], row["appointment_id"]) for row in db.execute(LINKS + " AND l.appointment_id IS NOT NULL")]
            booked = appointments(db, [appointment for _, appointment in inside])
            off = len({recording for recording, appointment in inside if (booked.get(appointment) or {}).get("state") in ("cancelled", "no_show")})
        for_you = sum(self.reading.for_you().values())
        return {"needs_filing": waiting, "sign_in": sign_in, "for_you": for_you, "appointments_off": off,
                "count": waiting + int(sign_in) + for_you + off}

    def unsure(self):
        """How many recordings wait for the instructor's Keep or Don't keep (Home's card and the sidebar's count: one query)."""
        with connect(self.path) as db:
            return db.execute(f"SELECT COUNT(*) FROM recordings r WHERE NOT EXISTS ({LINKS} AND l.recording_id=r.id)").fetchone()[0]

    def request(self, data=None):
        """Check now: marks a round wanted; the worker runs it within seconds. Nothing else."""
        self._set(requested_at=iso(self.now()))
        return self.status()

    # -- Rounds ------------------------------------------------------------------------------------------------------------
    def lock(self):
        return exclusive(self.storage, LOCK, Path(self.path).parent / ".recordings.lock", "A recordings round is running now.")

    def due(self, now=None):
        now = now or self.now()
        state = self._state()
        wanted = state["requested_at"] and state["requested_at"] > (state["last_round_at"] or "")
        next_due = parse(state["next_due_at"])
        return bool(wanted or next_due is None or next_due <= now)

    def run_due(self, now=None):
        with self.lock():
            if self.due(now):
                self.round()
            elif self.reading.wanted():
                self.reading.read_due()   # Read again: made within seconds, without asking Plaud anything

    def round(self):
        """One capture round. Its outcome is the capture state; nothing raises out of it but the lock's Conflict."""
        started = self.now()
        state = self._state()
        asked = bool(state["requested_at"] and state["requested_at"] > (state["last_round_at"] or ""))
        values = {"last_round_at": iso(started), "next_due_at": iso(started + timedelta(seconds=EVERY))}
        try:
            # ONLY WHAT IS FACULTY'S IS KEPT: what the instructor said not to keep is erased before anything is fetched or read.
            # More than one round's worth follows a minute later; files a drive that is away still holds, at the next round.
            if self.finish_erasing():
                values["next_due_at"] = iso(started + timedelta(seconds=60))
        except Exception:  # noqa: BLE001 - it stays hidden, and the next round erases it
            LOG.warning("Recordings that are not kept could not be erased", exc_info=True)
        try:
            self._read_kept()
        except Exception:  # noqa: BLE001 - local work beside the round: it never decides what the round says of Plaud
            LOG.warning("Kept transcripts could not be read into lines", exc_info=True)
        try:
            counts, detail, notice = self._capture(started, state, asked)
            # What the round says of itself is what the intake says of how it stands: the sign-in to be made again, or an
            # archive that is not ready, a command that failed, or a listing of Plaud that is old. What the archive already
            # held was listed, filed and copied all the same. Such a round does not count as a success (the next one looks back
            # as far as the last that was).
            values.update(status=notice[0] if notice else "ok", detail=(notice[1] if notice else detail)[:2000],
                          counts_json=encoded(counts), refused_file="")
            if not notice:
                values["last_success_at"] = iso(started)
            if counts.get("backlog"):
                values["next_due_at"] = iso(started + timedelta(seconds=60))
        except intake.IntakeError as waiting:
            values.update(status="waiting", detail=str(waiting)[:2000], refused_file="")
        except StorageError as away:
            # A drive that is away is not Plaud's doing: what was listed and filed stands, and the next round fetches.
            LOG.warning("Recordings capture is waiting for storage: %s", away)
            values.update(status="waiting", detail=f"Faculty's storage is not ready, so nothing was fetched. {away}"[:2000])
        except Exception as error:  # noqa: BLE001 - the reason is shown and the next round tries again
            LOG.exception("A recordings capture round failed")
            values.update(status="failed", detail=(str(error) or type(error).__name__)[:2000])
        self._set(**values)
        try:
            # Step 3, after the round has said what it says of Plaud: what is filed and kept is read (never part of that outcome).
            self.reading.read_due()
        except Exception:  # noqa: BLE001 - tried again next round
            LOG.exception("Kept recordings could not be read")
        try:
            # Step 5: a class's move card that waits on a class schedule changed since is made again as the move stands now.
            self.reading.refresh_moves()
        except Exception:  # noqa: BLE001 - looked at again next round
            LOG.exception("Class recordings' move cards could not be looked at again")
        return values

    def worker(self, stop):
        """Beside the Inbox worker loop: every TICK seconds, a round when one is due or asked for."""
        try:
            self.files.sweep()
        except Exception:  # noqa: BLE001 - a folder that cannot be swept is left for the next start
            LOG.warning("Unrecorded recording bundles could not be swept", exc_info=True)
        def tick():
            try:
                self.run_due()
            finally:
                self.index_turn()
        rounds.repeat(stop, tick, TICK, LOG, "Recordings capture is waiting for storage", "Recordings capture stopped unexpectedly")

    def index_turn(self):
        """Step 6: a few passages of kept transcripts are embedded for search by meaning, beside the round and under no lock
        (the embedding worker is on this machine; a round never waits for it and never fails for it)."""
        if self.meaning is None:
            return 0
        try:
            return self.meaning.turn()
        except Exception:  # noqa: BLE001 - a rebuildable index that could not be filled is filled later
            LOG.warning("Kept transcripts could not be read for search by meaning", exc_info=True)
            return 0

    @staticmethod
    def since(started, state, asked):
        """How far back this round lists (Plaud filters by when a recording BEGAN, so one synced from the recorder late, or made
        while capture was down, is found only by looking back): three days at least; back to a day before the last round that
        succeeded, however long ago that was; and thirty days in the first round of each campus day, on Check now, and when no
        round has succeeded yet."""
        last = parse(state["last_success_at"])
        since = started - WINDOW
        if last is not None:
            since = min(since, last - OVERLAP)
        if asked or last is None or filing.campus(last).date() != filing.campus(started).date():
            since = min(since, started - DEEP)
        return since

    def _how_it_stands(self, started, asked, detail):
        """Ask the intake how it stands (`status`), and on a round the instructor asked for have it make one of its own against
        Plaud first and say how it stands again. Returns `status`'s answer; `detail` gains what Check now could not do. A command
        that fails or an archive that is not ready is raised: nothing can be read."""
        state = self.client.status()
        if not state["ready"]:
            raise intake.IntakeError(f"The Plaud archive is not ready: {intake.said(state, 'problem') or 'the intake says nothing more'}.")
        if asked and state["sign_in"].get("state") == "ok":
            # CHECK NOW: the intake's own round against Plaud comes first, so the listing is as new as Plaud's. If it cannot
            # be made, that is said and the round goes on with what the archive holds.
            try:
                self.client.check_now()
            except intake.IntakeError as problem:
                detail.append(f"Check now could not ask Plaud ({str(problem).rstrip('.')}).")
            state = self.client.status()
        return state

    def _notice(self, started, state):
        """What the intake's `status` makes of this round, or None: ("sign_in", words) when the sign-in is to be made again,
        ("waiting", one sentence) when its listing of Plaud is more than STALE old."""
        sign_in = state["sign_in"]
        if sign_in.get("state") != "ok":
            why = intake.said(sign_in, "why", "says")
            return ("sign_in", f"{intake.SIGN_IN}. The Plaud intake says: {why or 'the sign-in is not ok'}. "
                               "`plaud-intake status` on this machine says what it needs.")
        listing = state.get("listing") if isinstance(state.get("listing"), dict) else {}
        last = intake.instant(listing.get("at"))
        if last is None:
            return ("waiting", "The Plaud intake has not listed Plaud yet; `systemctl --user status plaud-intake.timer` says why.")
        if started - last > STALE:
            when = filing.campus(last)
            return ("waiting", f"The Plaud intake has not listed Plaud since {when:%a}, {when:%b} {when.day} at {when:%H:%M}; "
                               "`systemctl --user status plaud-intake.timer` says why.")
        return None

    def _capture(self, started, state, asked):
        setup = self.client.setup()
        if setup:
            raise intake.NotSetUp(setup)
        counts = {"listed": 0, "new": 0, "filed": 0, "needs_filing": 0, "fetched": 0, "waiting": 0, "backlog": 0, "not_kept": 0}
        said_first = []
        stands = self._how_it_stands(started, asked, said_first)
        archive = stands["archive"] if isinstance(stands.get("archive"), str) else ""
        since = usual = self.since(started, state, asked)
        with connect(self.path) as db:
            # ASK AGAIN: what was not kept and is to be asked about again has no row, and Faculty holds no date of it (only
            # when it was decided, which may be long after it was made). So this round lists the archive ASK_BACK back, and takes
            # up from before its usual window ONLY the recordings that were asked about: nothing else that old.
            again = {row["plaud_id"] for row in db.execute("""SELECT n.plaud_id FROM recording_not_kept n WHERE n.state='ask'
                AND NOT EXISTS (SELECT 1 FROM recordings r WHERE r.plaud_id=n.plaud_id)""")}
        if again:
            since = min(since, started - ASK_BACK)
        listing = self.client.recordings(since, started + timedelta(days=1))
        counts["listed"] = sum(1 for entry in listing if entry["started_at"] >= usual)   # what a round lists, however far this one looked
        listed = self._record_listing(listing, counts, usual, again)
        if again:
            with self.rosters.lock(), connect(self.path) as db:
                # One the archive does not list cannot be asked about: it is not kept, as it was (one Plaud no longer has but
                # the archive holds is listed, and can be). Only those this round looked for: one asked about while the round
                # was running is looked for by the next.
                db.executemany("""UPDATE recording_not_kept SET state='not_kept', updated_at=? WHERE plaud_id=? AND state='ask'
                    AND NOT EXISTS (SELECT 1 FROM recordings r WHERE r.plaud_id=recording_not_kept.plaud_id)""",
                               [(timestamp(), plaud_id) for plaud_id in sorted(again)])
        since = usual
        self._file_by_calendar(listed, counts)
        self._fetch_due(started, asked, counts, archive, said_first)
        with connect(self.path) as db:
            counts["needs_filing"] = db.execute(f"SELECT COUNT(*) FROM recordings r WHERE NOT EXISTS ({LINKS} AND l.recording_id=r.id)").fetchone()[0]
        day = filing.campus(since)
        said = [f"{counts['listed']} recording{'s' if counts['listed'] != 1 else ''} at Plaud since {day:%a}, {day:%b} {day.day}"]
        if counts["new"]:
            said.append(f"{counts['new']} new")
        if counts["fetched"]:
            said.append(f"{counts['fetched']} fetched")
        if counts["waiting"]:
            said.append(f"{counts['waiting']} not ready at Plaud yet")
        return counts, " ".join([", ".join(said) + ".", *said_first]), self._notice(started, stands)

    # -- 1. What Plaud lists -------------------------------------------------------------------------------------------------
    def _revise(self, db, recording_id, latest, source, request_id="", actor="automatic", **changes):
        """Write the next revision of a recording when `changes` change anything; returns whether one was written."""
        fields = {key: (latest or {}).get(key) for key in ("title", "started_at", "start_known", "duration_ms", "speakers_json",
                                                           *(f"{role}_file_id" for role in FILE_ROLES))}
        fields.update(changes)
        fields["speakers_json"] = fields["speakers_json"] or "{}"
        digest = _digest(fields)
        if latest and latest.get("content_sha256") == digest:
            return False
        db.execute("""INSERT INTO recording_revisions(id, recording_id, revision, source, title, started_at, start_known, duration_ms,
                          transcript_file_id, outline_file_id, marks_file_id, notes_file_id, audio_file_id, speakers_json,
                          content_sha256, captured_at, request_id, actor) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                   (str(uuid.uuid4()), recording_id, (latest["revision"] if latest else 0) + 1, source, fields["title"], fields["started_at"],
                    int(fields["start_known"]), fields["duration_ms"], fields["transcript_file_id"], fields["outline_file_id"],
                    fields["marks_file_id"], fields["notes_file_id"], fields["audio_file_id"], fields["speakers_json"], digest, timestamp(),
                    request_id, actor))
        return True

    @staticmethod
    def _latest(db, recording_id):
        row = db.execute("""SELECT * FROM recording_revisions WHERE recording_id=? ORDER BY revision DESC LIMIT 1""", (recording_id,)).fetchone()
        return dict(row) if row else None

    def _record_listing(self, listing, counts, usual=None, asked=frozenset()):
        """Record what Plaud lists (a new recording's list row; a changed title, start or length as a new revision) in one
        transaction. Returns the listed recordings' IDs in Faculty. `usual`: where this round's usual window begins, when it
        listed further back to find the recordings `asked` about again (their Plaud IDs): of what began before it, only
        those are taken up."""
        found = []
        if not listing:
            return found
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                passed = {row["plaud_id"] for row in db.execute("SELECT plaud_id FROM recording_not_kept WHERE state='not_kept'")}
                for entry in listing:
                    if entry["plaud_id"] in passed:
                        counts["not_kept"] += 1   # NOT KEPT: nothing of it is recorded again
                        continue
                    row = db.execute("SELECT id FROM recordings WHERE plaud_id=?", (entry["plaud_id"],)).fetchone()
                    if not row and usual is not None and entry["started_at"] < usual and entry["plaud_id"] not in asked:
                        continue   # older than a round lists, and nobody asked about it: Ask again brings back only what was not kept
                    if row:
                        recording_id = row["id"]
                    else:
                        recording_id = str(uuid.uuid4())
                        db.execute("INSERT INTO recordings(id, plaud_id, captured_at) VALUES (?,?,?)", (recording_id, entry["plaud_id"], timestamp()))
                        db.execute("INSERT INTO recording_fetch_state(recording_id, updated_at) VALUES (?,?)", (recording_id, timestamp()))
                        counts["new"] += 1
                    self._revise(db, recording_id, self._latest(db, recording_id), "list", title=entry["title"],
                                 started_at=stamp(entry["started_at"]), start_known=int(entry["start_known"]), duration_ms=entry["duration_ms"])
                    found.append(recording_id)
                db.commit()
            except BaseException:
                db.rollback()
                raise
        return found

    # -- 2. Filing -----------------------------------------------------------------------------------------------------------
    @staticmethod
    def _links(db, recording_id):
        return [dict(row) for row in db.execute(LINKS + " AND l.recording_id=? ORDER BY l.created_at, l.id", (recording_id,))]

    def _write_filing(self, db, recording_id, target, filed_by, basis, actor, request_id=""):
        """File a recording under `target` in the caller's transaction: every other filed link is withdrawn and the target's next
        revision is filed (RE-FILING is a new link revision). Returns whether anything was written."""
        key, now = owner_key(target), timestamp()
        current = self._links(db, recording_id)
        same = [link for link in current if link["owner_key"] == key]
        # Filed there already: no change. Naming no appointment (a filing by hand) never takes away the one it was filed in.
        # A meeting filed under a course and a TRAINING of that course (step 6) are one owner: which it is, is the link's purpose.
        if same and len(current) == 1 and target.get("appointment_id") in (None, same[0]["appointment_id"]) \
                and (same[0].get("purpose") or "") == (target.get("purpose") or ""):
            return False

        def write(kind, key, state, owner, by, why, part=(None, None)):
            number = db.execute("SELECT COALESCE(MAX(revision), 0) FROM recording_links WHERE recording_id=? AND owner_key=?",
                                (recording_id, key)).fetchone()[0] + 1
            db.execute("""INSERT INTO recording_links(id, recording_id, kind, owner_key, revision, state, enrollment_id, term_id,
                              definition_id, section_id, class_date, appointment_id, part_start_ms, part_end_ms, filed_by, basis_json,
                              request_id, actor, created_at, purpose)
                          VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                       (str(uuid.uuid4()), recording_id, kind, key, number, state, owner.get("enrollment_id"), owner.get("term_id"),
                        owner.get("definition_id"), owner.get("section_id"), owner.get("class_date") or "", owner.get("appointment_id"),
                        part[0], part[1], by, encoded(why), request_id, actor, now, owner.get("purpose") or ""))

        for link in current:
            if link["owner_key"] != key:
                write(link["kind"], link["owner_key"], "withdrawn", link, filed_by, {})
        # Step 4: a student who had a PART of this recording keeps it when the recording is filed under them again (the other
        # students are withdrawn; their parts become nobody's, never this student's). The whole recording is given to one
        # student only where the instructor says so, in Students and parts.
        held = (same[0]["part_start_ms"], same[0]["part_end_ms"]) if same else (None, None)
        write(target["kind"], key, "filed", {**target, "appointment_id": target.get("appointment_id") or (same[0]["appointment_id"] if same else None)},
              filed_by, basis or {}, held)
        self._fetch_state(db, recording_id, filing_detail="")
        # Filed elsewhere, it is read again there (step 3): an earlier filing's failed tries do not hold that back.
        db.execute("UPDATE recording_reading_state SET tries=0, next_try_at='', detail='', updated_at=? WHERE recording_id=?", (now, recording_id))
        return True

    def _feed(self, day):
        """(events, unread): the calendar around one campus date, as the one feed assembles it. Google's copy is left out when
        it cannot be read, and `unread` then says so."""
        from scheduling.feed import assemble
        first, last, failed = (day - timedelta(days=1)).isoformat(), day.isoformat(), []
        from student_support.service import cancelled_events
        with connect(self.path) as db:
            db.execute("BEGIN")
            events = assemble(db, first, last, calendar=self.calendar, google=self.google, on_google_error=lambda: failed.append(True))
            # Step 4: an appointment recorded as cancelled is not on the calendar; filing reads it by reference, as the weakest
            # evidence (recordings/filing.py), so a recording that began in one is filed under that student and marked.
            events = events + cancelled_events(db, first, last)
        if failed:
            LOG.warning("Google's calendar could not be read while filing a recording")
        return events, "Faculty could not read its copy of your Google Calendar" if failed else None

    def _file_by_calendar(self, listed, counts):
        """BY THE CALENDAR, WITHOUT A CARD: file each listed recording that has no link yet by where its start falls."""
        if not listed:
            return
        from scheduling.feed import google_gap
        marks = ",".join("?" * len(listed))
        with connect(self.path) as db:
            waiting = [dict(row) for row in db.execute(
                f"{LATEST} WHERE r.id IN ({marks}) AND NOT EXISTS ({LINKS} AND l.recording_id=r.id) ORDER BY v.started_at", listed)]
            terms = [dict(row) for row in db.execute("SELECT id, code, definition_id FROM course_terms WHERE is_current=1 AND deleted_at IS NULL")]
            asked_again = {row["plaud_id"] for row in db.execute("SELECT plaud_id FROM recording_not_kept WHERE state='ask'")}
        if not waiting:
            return
        feeds, decisions, vouched = {}, [], []

        def gap(unread):
            """Why Google's calendar cannot vouch for a time now (SCHEDULING "GOOGLE CHECKED"), or None when it can."""
            if not vouched:
                vouched.append(google_gap(self.google))
            return unread or vouched[0]

        for recording in waiting:
            if not recording["start_known"]:
                decisions.append((recording, {"target": None, "detail": filing.NO_START, "basis": {}}))
                continue
            moment = filing.campus(parse(recording["started_at"]))
            if moment.date() not in feeds:
                feeds[moment.date()] = self._feed(moment.date())
            events, unread = feeds[moment.date()]
            wanted = sorted({str(event.get("id") or "").removeprefix(filing.APPOINTMENT) for event in events if event.get("source") == "office_hours"})
            appointments = {}
            if wanted:
                with connect(self.path) as db:
                    appointments = {row["id"]: {"enrollment_id": row["enrollment_id"], "term_id": row["term_id"]} for row in db.execute(
                        f"SELECT id, enrollment_id, term_id FROM student_support_items WHERE kind='appointment' AND id IN ({','.join('?' * len(wanted))})", wanted)}
            decision = filing.decide(moment, events, terms, appointments)
            if recording["plaud_id"] in asked_again:
                # ASK AGAIN: the instructor asked to be asked, so the calendar decides nothing for this one, either way: it is
                # neither dropped a second time nor kept by itself (one they chose not to keep is theirs to bring back).
                why = filing.NOT_OURS if decision["target"] and decision["target"]["kind"] == "not_faculty" \
                    else "" if decision["target"] else decision["detail"]
                decision = {"target": None, "detail": " ".join(part for part in (why, ASKED_AGAIN) if part), "basis": {}}
            elif decision["target"] is None and decision["detail"] == filing.NO_ENTRY:
                if gap(unread):
                    decision["detail"] = f"{filing.NO_ENTRY} {gap(unread)}."
            elif decision["target"] and decision["target"]["kind"] == "not_faculty" and gap(unread):
                # An automatic Not kept is final (nothing of the recording is held after it), and the entry that names the
                # course may be one Faculty has not read: it waits, and is decided again each round while Plaud lists it.
                decision = {"target": None, "detail": f"{filing.UNSEEN} {gap(unread)}.", "basis": {}}
            elif decision.get("weak") and gap(unread):
                # A cancelled appointment files only where NOTHING ELSE was on the calendar, and Google's copy cannot say so now.
                decision = {"target": None, "detail": f"{filing.CANCELLED} {gap(unread)}.", "basis": {}}
            decisions.append((recording, decision))
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                for recording, decision in decisions:
                    if self._links(db, recording["id"]):
                        continue   # filed by hand since this round read it
                    if decision["target"] is None:
                        self._fetch_state(db, recording["id"], filing_detail=decision["detail"][:2000])
                    elif decision["target"]["kind"] == "not_faculty":
                        # NOT KEPT: the calendar shows it to be nothing of Faculty's. Its list row leaves the catalog here (it
                        # was never fetched) and only Plaud's ID for it stays, so it is neither listed nor asked about again.
                        self._erase_rows(db, recording, "calendar")
                        counts["not_kept"] += 1
                    elif self._write_filing(db, recording["id"], decision["target"], "calendar", decision["basis"], "automatic"):
                        counts["filed"] += 1
                db.commit()
            except BaseException:
                db.rollback()
                raise

    def file(self, recording_id, data, actor="workspace"):
        """KEEP a recording, filed by hand (the instructor's choice, or the Assistant's on an Approve card): under a student, a
        term, a course, a section's class day or as a Memo. A repeated request ID returns what it did. It is fetched in the next
        round, which is asked for at once. Not keeping one is `discard`, the instructor's own press."""
        data = data if isinstance(data, dict) else {}
        request_id = valid_request_id(data.get("request_id"))
        kind = data.get("kind")
        if kind not in KINDS:
            raise ValueError("Choose what this recording is kept under: a student, a term, a course, a course's training, a class day or Memo.")
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                recording = db.execute(f"{LATEST} WHERE r.id=? AND NOT {GOING}", (str(recording_id),)).fetchone()
                if not recording:
                    raise NotFound("This recording no longer exists.")
                earlier = db.execute("SELECT recording_id FROM recording_links WHERE request_id=? LIMIT 1", (request_id,)).fetchone()
                if earlier and earlier["recording_id"] != recording["id"]:
                    raise Conflict("This request ID already filed another recording.")
                if not earlier:
                    target = self._target(db, recording, kind, data)
                    self._write_filing(db, recording["id"], target, "instructor", {}, actor, request_id)
                    # Kept: one the instructor had asked to be asked about again is no longer marked as not kept.
                    db.execute("DELETE FROM recording_not_kept WHERE plaud_id=?", (recording["plaud_id"],))
                    # Fetched in the round this asks for. One already kept keeps its own schedule of later reads.
                    db.execute("UPDATE recording_fetch_state SET next_check_at='', tries=0, updated_at=? WHERE recording_id=? AND status!='kept'",
                               (timestamp(), recording["id"]))
                    db.execute("UPDATE recording_capture_state SET requested_at=?, updated_at=? WHERE id=1",
                               (iso(self.now()), timestamp()))
                db.commit()
            except BaseException:
                db.rollback()
                raise
        if not earlier:
            self.reading.refiled(recording["id"])   # step 3: cards of what was read where it was filed before are taken back
        return self.listing(only=recording["id"])["recordings"][0]

    # -- ONLY WHAT IS FACULTY'S IS KEPT ------------------------------------------------------------------------------------
    def discard(self, recording_id, data, actor="workspace"):
        """`POST /api/recordings/<id>/discard`: the instructor's DON'T KEEP, of a recording that waits or of one Faculty has
        kept. It is hidden at once (nothing of it is shown, served, fetched or read from here on); its Approve cards that still
        wait are taken back and a memo's runs withdrawn (what was approved stands); then it is ERASED, here when no round is
        running, else by the round this asks for. Only Plaud's ID for it stays, so it is never listed or asked about again
        (until `ask_again`). A repeated request ID answers as the first did."""
        data = data if isinstance(data, dict) else {}
        request_id = valid_request_id(data.get("request_id"))
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                recording = db.execute("SELECT id, plaud_id FROM recordings WHERE id=?", (str(recording_id),)).fetchone()
                # What this request ID did before: it erased a recording (Plaud's ID of it is all that is left to tell which),
                # or it hid one that is not erased yet, or it filed one.
                erased = db.execute("SELECT plaud_id FROM recording_not_kept WHERE request_id=?", (request_id,)).fetchone()
                used = db.execute("SELECT recording_id, kind FROM recording_links WHERE request_id=? LIMIT 1", (request_id,)).fetchone()
                if not recording:
                    if not erased:
                        raise NotFound("This recording no longer exists.")
                    repeated = True   # the same press again, of a recording that is gone: it answers as the first did
                else:
                    repeated = bool(used and used["recording_id"] == recording["id"] and used["kind"] == "not_faculty")
                    if (used and not repeated) or erased:
                        raise Conflict("This request ID was already used for another recording.")
                    if not repeated:
                        self._write_filing(db, recording["id"], {"kind": "not_faculty"}, "instructor", {}, actor, request_id)
                        db.execute("UPDATE recording_capture_state SET requested_at=?, updated_at=? WHERE id=1", (iso(self.now()), timestamp()))
                db.commit()
            except BaseException:
                db.rollback()
                raise
        if recording:
            self.reading.gone(recording["id"])
            try:
                with self.lock():
                    self.finish_erasing()
            except Conflict:
                pass   # a round is running: it is erased by the round asked for above
            except Exception:  # noqa: BLE001 - hidden already; the next round erases it
                LOG.warning("A recording that is not kept could not be erased yet", exc_info=True)
        return {"not_kept": True, **self.kept_summary()}

    def ask_again(self, data=None, actor="workspace"):
        """`POST /api/recordings/ask-again`: the instructor asks to be asked about what was NOT KEPT. Plaud is listed again far
        enough back (a round is asked for at once), and each one it still holds gets its list row back and WAITS for their Keep
        or Don't keep: the calendar decides nothing for one they asked about, neither that it is not theirs nor that it is.
        Nothing is fetched for one that waits."""
        data = data if isinstance(data, dict) else {}
        valid_request_id(data.get("request_id"))
        now = timestamp()
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                asked = db.execute("UPDATE recording_not_kept SET state='ask', updated_at=? WHERE state='not_kept'", (now,)).rowcount
                if asked:
                    db.execute("UPDATE recording_capture_state SET requested_at=?, updated_at=? WHERE id=1", (iso(self.now()), now))
                db.commit()
            except BaseException:
                db.rollback()
                raise
        return {"asked": asked, **self.kept_summary()}

    def kept_summary(self):
        """How many recordings at Plaud are not kept (nothing else is known of them), and what needs the instructor."""
        with connect(self.path) as db:
            not_kept = db.execute("SELECT COUNT(*) FROM recording_not_kept WHERE state='not_kept'").fetchone()[0]
        return {"not_kept_count": not_kept, "attention": self.attention(), "capture": self.status()}

    def _erase_rows(self, db, recording, decided_by, request_id="", actor="automatic"):
        """ERASED, in the caller's transaction (under the roster lifecycle lock): every row of one recording leaves the
        catalog, the bundles its files were in are named for removal from both drives, and Plaud's ID for it is kept as NOT
        KEPT. This is the one way out of tables that are otherwise retained: the recording is named in `recording_erasing`
        for the length of the deletes and the name is cleared before the caller commits (migration 005). Returns the bundles."""
        recording_id = recording["id"]
        columns = " UNION ".join(f"SELECT {role}_file_id AS f FROM recording_revisions WHERE recording_id=:r" for role in FILE_ROLES)
        files = sorted(row["f"] for row in db.execute(
            f"SELECT f FROM ({columns} UNION SELECT transcript_file_id AS f FROM recording_readings WHERE recording_id=:r) WHERE f IS NOT NULL",
            {"r": recording_id}))
        marks = ",".join("?" * len(files))
        bundles = sorted(row[0] for row in db.execute(f"SELECT DISTINCT bundle_id FROM recording_files WHERE id IN ({marks})", files)) if files else []
        if bundles:
            # A bundle is the files ONE fetch of ONE recording kept: everything in it goes, and nothing of another recording may be in it.
            held = ",".join("?" * len(bundles))
            files = sorted(row[0] for row in db.execute(f"SELECT id FROM recording_files WHERE bundle_id IN ({held})", bundles))
            marks = ",".join("?" * len(files))
            named = " OR ".join(f"{role}_file_id IN ({marks})" for role in FILE_ROLES)
            if db.execute(f"SELECT 1 FROM recording_revisions WHERE recording_id!=? AND ({named}) LIMIT 1",
                          (recording_id, *(files * len(FILE_ROLES)))).fetchone():
                raise RuntimeError("A kept file of this recording is named by another recording, so nothing was erased.")
        db.execute("UPDATE recording_erasing SET recording_id=? WHERE id=1", (recording_id,))
        for statement in (
                "DELETE FROM recording_item_events WHERE item_id IN (SELECT id FROM recording_items WHERE recording_id=:r)",
                """DELETE FROM recording_item_readings WHERE item_id IN (SELECT id FROM recording_items WHERE recording_id=:r)
                       OR reading_id IN (SELECT id FROM recording_readings WHERE recording_id=:r)""",
                "DELETE FROM recording_items WHERE recording_id=:r",
                "DELETE FROM recording_readings WHERE recording_id=:r",
                "DELETE FROM recording_reading_state WHERE recording_id=:r",
                "DELETE FROM recording_fetch_state WHERE recording_id=:r",
                "DELETE FROM recording_links WHERE recording_id=:r",
                "DELETE FROM recording_revisions WHERE recording_id=:r"):
            db.execute(statement, {"r": recording_id})
        if files:
            db.execute(f"DELETE FROM recording_lines WHERE file_id IN ({marks})", files)
            db.execute(f"DELETE FROM recording_transcripts WHERE file_id IN ({marks})", files)
            db.execute(f"DELETE FROM recording_files WHERE id IN ({marks})", files)
        now = timestamp()
        for bundle_id in bundles:
            db.execute("DELETE FROM recording_bundles WHERE id=?", (bundle_id,))
            db.execute("INSERT OR IGNORE INTO recording_erased_bundles(bundle_id, erased_at) VALUES (?,?)", (bundle_id, now))
        db.execute("DELETE FROM recordings WHERE id=?", (recording_id,))
        db.execute("UPDATE recording_erasing SET recording_id=NULL WHERE id=1")
        db.execute("""INSERT INTO recording_not_kept(plaud_id, state, decided_by, request_id, actor, decided_at, updated_at)
                      VALUES (?,?,?,?,?,?,?)
                      ON CONFLICT(plaud_id) DO UPDATE SET state='not_kept', decided_by=excluded.decided_by, request_id=excluded.request_id,
                          actor=excluded.actor, decided_at=excluded.decided_at, updated_at=excluded.updated_at""",
                   (recording["plaud_id"], "not_kept", decided_by, request_id, actor, iso(self.now()), now))
        return bundles

    def finish_erasing(self):
        """Erase every recording that is leaving (the instructor's Don't keep; one an earlier version marked Not Faculty's),
        then remove from both drives the files of what was erased. The caller holds the capture lock, so nothing is being
        fetched or read meanwhile. At most ERASES a call; returns whether more wait. One that cannot be erased (its
        transaction is rolled back whole, the erasure's name with it) stays hidden, is said in the log, and does not hold back
        the others; the next round tries it again."""
        with connect(self.path) as db:
            going = [dict(row) for row in db.execute(f"""SELECT r.id, r.plaud_id,
                    (SELECT l.request_id FROM recording_links l WHERE l.recording_id=r.id AND l.kind='not_faculty' ORDER BY l.revision DESC LIMIT 1) AS request_id,
                    (SELECT l.actor FROM recording_links l WHERE l.recording_id=r.id AND l.kind='not_faculty' ORDER BY l.revision DESC LIMIT 1) AS actor,
                    (SELECT l.filed_by FROM recording_links l WHERE l.recording_id=r.id AND l.kind='not_faculty' ORDER BY l.revision DESC LIMIT 1) AS filed_by
                FROM recordings r WHERE {GOING} ORDER BY r.captured_at, r.id LIMIT ?""", (ERASES + 1,))]
        for recording in going[:ERASES]:
            self.reading.gone(recording["id"])   # again: a reading that ended after the press may have made a card since
            try:
                with self.rosters.lock(), connect(self.path) as db:
                    db.execute("BEGIN IMMEDIATE")
                    try:
                        if db.execute(f"SELECT 1 FROM recordings r WHERE r.id=? AND {GOING}", (recording["id"],)).fetchone():
                            self._erase_rows(db, recording, recording["filed_by"] or "instructor", recording["request_id"] or "",
                                             recording["actor"] or "automatic")
                        db.commit()
                    except BaseException:
                        db.rollback()
                        raise
            except Exception:  # noqa: BLE001 - this one stays hidden and is tried again; the others are erased
                LOG.exception("A recording that is not kept could not be erased")
        self._remove_erased()
        return len(going) > ERASES

    def _remove_erased(self):
        """The files of erased recordings leave both drives, through the storage engine and under its locks. A drive that is
        away, or a folder that is not where it should be, leaves the bundle named and says why; it is tried again each round."""
        with connect(self.path) as db:
            waiting = [row[0] for row in db.execute("SELECT bundle_id FROM recording_erased_bundles ORDER BY erased_at, bundle_id")]
        for bundle_id in waiting:
            try:
                with self.rosters.lock():
                    self.files.erase(bundle_id)
                    with connect(self.path) as db:
                        db.execute("DELETE FROM recording_erased_bundles WHERE bundle_id=?", (bundle_id,))
            except (StorageError, OSError) as away:
                LOG.warning("The files of an erased recording could not be removed yet: %s", away)
                with connect(self.path) as db:
                    db.execute("UPDATE recording_erased_bundles SET detail=? WHERE bundle_id=?", (str(away)[:2000], bundle_id))

    STUDENTS = 12   # the most students one recording is filed under (an afternoon of office hours left running)

    def set_students(self, recording_id, data, actor="workspace"):
        """`POST /api/recordings/<id>/students` (step 4, SEVERAL STUDENTS IN ONE RECORDING): the students a recording is of, each
        with their PART, set by the instructor. `students` is the whole list, [{enrollment_id, start_ms, end_ms}], times in
        milliseconds from the start of the recording. ONE student may have no part (the whole recording is theirs); with
        several, every student has a part and no two overlap, so that no student's timeline, follow-up or note is given
        another student's words. Each student's link is a new revision where it changed (the appointment it was filed by
        stays, by reference); a student left out is withdrawn, and so is a filing under anything else. A repeated request ID
        returns what it did."""
        data = data if isinstance(data, dict) else {}
        request_id, given = valid_request_id(data.get("request_id")), data.get("students")
        if not isinstance(given, list) or not 1 <= len(given) <= self.STUDENTS or not all(isinstance(entry, dict) for entry in given):
            raise ValueError("Say which students this recording is of, each with the part that is theirs.")
        parts = []
        for entry in given:
            start, end = entry.get("start_ms"), entry.get("end_ms")
            if start is None and end is None:
                parts.append(None)
            elif type(start) is int and type(end) is int and 0 <= start < end:
                parts.append((start, end))
            else:
                raise ValueError("Give each part as where it starts and where it ends in the recording, the end after the start.")
        if len(given) > 1 and None in parts:
            raise ValueError("With several students in one recording, each needs the part that is theirs.")
        ordered = sorted(part for part in parts if part)
        if any(later[0] < earlier[1] for earlier, later in zip(ordered, ordered[1:])):
            raise ValueError("Two students' parts overlap. Each moment of a recording is one student's.")
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                recording = db.execute(f"{LATEST} WHERE r.id=?", (str(recording_id),)).fetchone()
                if not recording:
                    raise NotFound("This recording no longer exists.")
                earlier = db.execute("SELECT recording_id FROM recording_links WHERE request_id=? LIMIT 1", (request_id,)).fetchone()
                if earlier and earlier["recording_id"] != recording["id"]:
                    raise Conflict("This request ID already filed another recording.")
                changed = False
                if not earlier:
                    length = recording["duration_ms"] or 0
                    if length and any(part and part[1] > length + 1000 for part in parts):
                        raise ValueError("A part ends after the recording does.")
                    targets = [{**self._target(db, recording, "student", entry), "part": part} for entry, part in zip(given, parts)]
                    if len({target["enrollment_id"] for target in targets}) != len(targets):
                        raise ValueError("A student is listed twice. Give each student one part.")
                    current, now = self._links(db, recording["id"]), timestamp()

                    def write(link, state, part, by="instructor"):
                        number = db.execute("SELECT COALESCE(MAX(revision), 0) FROM recording_links WHERE recording_id=? AND owner_key=?",
                                            (recording["id"], link["owner_key"])).fetchone()[0] + 1
                        db.execute("""INSERT INTO recording_links(id, recording_id, kind, owner_key, revision, state, enrollment_id, term_id,
                                          definition_id, section_id, class_date, appointment_id, part_start_ms, part_end_ms, filed_by, basis_json,
                                          request_id, actor, created_at, purpose) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                                   (str(uuid.uuid4()), recording["id"], link["kind"], link["owner_key"], number, state, link.get("enrollment_id"),
                                    link.get("term_id"), link.get("definition_id"), link.get("section_id"), link.get("class_date") or "",
                                    link.get("appointment_id"), part[0] if part else None, part[1] if part else None, by,
                                    link.get("basis_json") or "{}", request_id, actor, now, link.get("purpose") or ""))

                    keys = {owner_key(target) for target in targets}
                    for link in current:
                        if link["owner_key"] not in keys:
                            write(link, "withdrawn", None)
                            changed = True
                    for target in targets:
                        key = owner_key(target)
                        held = next((link for link in current if link["owner_key"] == key), None)
                        if held and (held["part_start_ms"], held["part_end_ms"]) == (target["part"] or (None, None)):
                            continue
                        # The appointment a student's link was filed by stays with it, by reference, whatever their part becomes.
                        write({**target, "owner_key": key, "appointment_id": held["appointment_id"] if held else None,
                               "basis_json": held["basis_json"] if held else "{}"}, "filed", target["part"])
                        changed = True
                    if changed:
                        self._fetch_state(db, recording["id"], filing_detail="")
                        db.execute("UPDATE recording_reading_state SET tries=0, next_try_at='', detail='', updated_at=? WHERE recording_id=?",
                                   (now, recording["id"]))
                        db.execute("UPDATE recording_fetch_state SET next_check_at='', tries=0, updated_at=? WHERE recording_id=? AND status!='kept'",
                                   (now, recording["id"]))
                        db.execute("UPDATE recording_capture_state SET requested_at=?, updated_at=? WHERE id=1", (iso(self.now()), now))
                db.commit()
            except BaseException:
                db.rollback()
                raise
        if changed:
            self.reading.parts_changed(recording["id"])   # cards made for the parts as they were are taken back, and made again
        return self.recording(recording["id"])

    def _target(self, db, recording, kind, data):
        def identifier(name, message):
            value = data.get(name)
            if not isinstance(value, str) or not value or len(value) > 100:
                raise ValueError(message)
            return value

        if kind == "student":
            enrollment = db.execute("""SELECT e.id, e.term_id FROM student_enrollments e JOIN course_terms t ON t.id=e.term_id
                                       WHERE e.id=? AND t.deleted_at IS NULL""", (identifier("enrollment_id", "Choose the student."),)).fetchone()
            if not enrollment:
                raise ValueError("Choose a student of one of your terms.")
            return {"kind": "enrollment", "enrollment_id": enrollment["id"], "term_id": enrollment["term_id"]}
        if kind == "term":
            term = db.execute("SELECT id FROM course_terms WHERE id=? AND deleted_at IS NULL", (identifier("term_id", "Choose the term."),)).fetchone()
            if not term:
                raise ValueError("Choose one of your terms.")
            return {"kind": "term", "term_id": term["id"]}
        if kind in ("course", "training"):
            course = db.execute("SELECT id FROM course_definitions WHERE id=?", (identifier("definition_id", "Choose the course."),)).fetchone()
            if not course:
                raise ValueError("Choose one of your courses.")
            # A TRAINING (step 6) is the course's own, kept for reference in every term: the same owner as a meeting filed
            # under the course, told apart by the link's purpose.
            return {"kind": "course", "definition_id": course["id"], **({"purpose": "training"} if kind == "training" else {})}
        if kind == "class_day":
            section = db.execute("""SELECT s.id, s.term_id FROM course_sections s JOIN course_terms t ON t.id=s.term_id
                                    WHERE s.id=? AND t.deleted_at IS NULL""", (identifier("section_id", "Choose the section."),)).fetchone()
            if not section:
                raise ValueError("Choose one of your sections.")
            day = data.get("date") or filing.campus(parse(recording["started_at"])).date().isoformat()
            try:
                day = datetime.strptime(str(day), "%Y-%m-%d").date().isoformat()
            except ValueError:
                raise ValueError("Give the class day as a date, like 2026-11-11.") from None
            return {"kind": "class_day", "section_id": section["id"], "term_id": section["term_id"], "class_date": day}
        return {"kind": kind}

    # -- 3. Fetching what is filed as Faculty's -----------------------------------------------------------------------------
    def _fetch_due(self, started, asked, counts, archive, refusals):
        now = iso(started)
        with connect(self.path) as db:
            rows = [dict(row) for row in db.execute(f"""{LATEST} JOIN recording_fetch_state f ON f.recording_id=r.id
                WHERE EXISTS ({LINKS} AND l.recording_id=r.id AND l.kind!='not_faculty') ORDER BY v.started_at DESC""")]
            states = {row["recording_id"]: dict(row) for row in db.execute("SELECT * FROM recording_fetch_state")}
        due = []
        for recording in rows:
            state = states[recording["id"]]
            if state["status"] == "kept":
                # `tries` counts the reads since it was kept or last changed; after the last of REREADS it is read no more.
                if state["tries"] < len(REREADS) and state["next_check_at"] and state["next_check_at"] <= now:
                    due.append((1, recording))
            elif state["status"] != "failed" or asked:
                if asked or not state["next_check_at"] or state["next_check_at"] <= now:
                    due.append((0, recording))
        due.sort(key=lambda pair: pair[0])   # what is not kept yet first; newest first within each
        counts["backlog"] = max(0, len(due) - FETCHES)
        for again, recording in due[:FETCHES]:
            before = states[recording["id"]]
            try:
                outcome = self._fetch(recording, started, archive)
            except StorageError:
                raise   # a drive is away: nothing else can be fetched this round either
            except intake.Refused as refused:
                # A file outside the archive, or one whose checksum differs: nothing of the recording was kept, and the round says so.
                refusals.append(str(refused))
                outcome = {"status": "kept", "detail": "", "retry": True} if again else {"status": "waiting", "detail": str(refused)}
            except intake.Gone as gone:
                # The archive does not know it. What Faculty kept stays kept, and it is not asked about again.
                outcome = {"status": "kept", "detail": "", "gone": True} if again else {"status": "failed", "detail": str(gone)}
            except intake.IntakeError as waiting:
                outcome = {"status": "kept", "detail": "", "retry": True} if again else {"status": "waiting", "detail": str(waiting)}
            except Exception as error:  # noqa: BLE001 - one recording's failure must not stop the others; the reason is shown
                LOG.exception("Fetching a recording failed")
                outcome = {"status": "kept", "detail": "", "retry": True} if again else \
                    {"status": "waiting", "detail": (str(error) or type(error).__name__)}
            if outcome["status"] != "kept":
                tries = before["tries"] + 1
                due_next = iso(started + timedelta(seconds=min(RETRY * 2 ** min(tries - 1, 8), 86400)))
            elif outcome.get("retry"):       # a kept recording Plaud could not be asked about: the same read, soon
                tries, due_next = before["tries"], iso(started + timedelta(seconds=RETRY))
            else:
                # Kept now, or read again: the next read by REREADS, counted from the last change; none after the last.
                tries = len(REREADS) if outcome.get("gone") else before["tries"] + 1 if again and not outcome.get("kept") else 0
                due_next = iso(started + timedelta(seconds=REREADS[tries])) if tries < len(REREADS) else ""
            with connect(self.path) as db:
                self._fetch_state(db, recording["id"], status=outcome["status"], detail=outcome["detail"][:2000], tries=tries,
                                  checked_at=now, next_check_at=due_next)
            counts["fetched"] += int(bool(outcome.get("kept")))
            counts["waiting"] += int(outcome["status"] == "waiting")

    def _kept(self, db, recording):
        return {role: dict(row) for role in FILE_ROLES if recording.get(f"{role}_file_id")
                for row in [db.execute("SELECT * FROM recording_files WHERE id=?", (recording[f"{role}_file_id"],)).fetchone()] if row}

    def _fetch(self, recording, started, archive):
        """Copy what the archive holds of one recording filed as Faculty's: its `show`, then its transcript, outline and marks as
        archived and its notes rebuilt in Faculty's form, each checked against the archive's checksum and each only when it
        differs from what Faculty keeps. What is new or changed goes into one bundle and one revision. NO AUDIO IS COPIED.
        Returns {status: kept | waiting, detail, kept: whether anything was kept this time}: kept is Faculty holding the
        transcript while the archive holds the audio."""
        with connect(self.path) as db:
            kept = self._kept(db, recording)
        shown = self.client.show(recording["plaud_id"])
        files = shown["files"]
        bundle, missing, said_now = self.files.bundle(), [], None
        try:
            for role in ("transcript", "outline", "marks"):
                entry = files.get(role)
                if not isinstance(entry, dict):
                    if role == "transcript" and "transcript" not in kept:
                        missing.append("The archive has no transcript of it yet.")
                    continue
                content = self.client.read(entry, archive, intake.JSON_LIMIT)
                if hashlib.sha256(content).hexdigest() != (kept.get(role) or {}).get("sha256"):
                    bundle.add(role, content, self._kind(content))
                    if role == "transcript":
                        said_now = content
            said = self._notes(files.get("notes"), archive)
            if said:
                content = encoded(said).encode("utf-8")
                if hashlib.sha256(content).hexdigest() != (kept.get("notes") or {}).get("sha256"):
                    bundle.add("notes", content, "application/json")
            if not isinstance(files.get("audio"), dict):
                missing.append("The archive has no audio of it yet.")
            facts = intake.listed(shown)
            wrote = self._keep(recording, bundle, facts, said_now)
        except OSError as error:
            # Every failure to reach the archive is an IntakeError by now, so this is a file Faculty could not write here (scratch
            # or staging full or away). It is storage that is not ready: the round says so and stops fetching.
            bundle.discard()
            raise StorageError(f"A recording's files could not be written here ({error.strerror or error or type(error).__name__}).") from None
        except BaseException:
            bundle.discard()
            raise
        have = set(kept) | set(wrote)
        complete = "transcript" in have and isinstance(files.get("audio"), dict)
        return {"status": "kept" if complete else "waiting", "detail": " ".join(dict.fromkeys(missing)), "kept": bool(wrote)}

    def _notes(self, entry, archive):
        """Plaud's notes in Faculty's form, [{type, tab, content}] in the archive's order, rebuilt from the archive's notes file (a
        list of {content, tab, title, type}) so that notes that did not change hash exactly as the ones already kept."""
        if not isinstance(entry, dict):
            return []
        try:
            found = json.loads(self.client.read(entry, archive, intake.JSON_LIMIT).decode("utf-8"))
        except ValueError:
            raise intake.IntakeError("The archive's notes were not in a shape Faculty reads.") from None
        if not isinstance(found, list):
            raise intake.IntakeError("The archive's notes were not in a shape Faculty reads.")
        return [{"type": str(note.get("type") or "")[:100], "tab": str(note.get("tab") or "")[:200], "content": note["content"]}
                for note in found if isinstance(note, dict) and isinstance(note.get("content"), str) and note["content"].strip()][:50]

    @staticmethod
    def _kind(content):
        try:
            json.loads(content.decode("utf-8"))
        except ValueError:
            return "text/plain"
        return "application/json"

    def _keep(self, recording, bundle, facts, said=None):
        """Publish a fetch's bundle and record its revision: the roster lifecycle lock, the bundle's owner lock, ONE transaction.
        A transcript kept now (`said`: its bytes) is read into lines in that transaction. Nothing is kept for a recording that
        stopped being Faculty's while it was fetched. Returns the roles kept."""
        roles = list(bundle.records)
        changes = {f"{role}_file_id": record["id"] for role, record in bundle.records.items()}
        if facts and facts["plaud_id"] == recording["plaud_id"]:
            changes.update(title=facts["title"], started_at=stamp(facts["started_at"]), start_known=int(facts["start_known"]),
                           duration_ms=facts["duration_ms"] or recording["duration_ms"])
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                if not any(link["kind"] != "not_faculty" for link in self._links(db, recording["id"])):
                    db.rollback()
                    bundle.discard()
                    return []
                self.files.seal(bundle, db)
                if said is not None and "transcript" in bundle.records:
                    self._read(db, bundle.records["transcript"]["id"], said)
                self._revise(db, recording["id"], self._latest(db, recording["id"]), "fetch" if roles else "list", **changes)
                db.commit()
            except BaseException:
                db.rollback()
                raise
        return roles

    # -- A kept transcript's lines (step 2) -----------------------------------------------------------------------------------
    @staticmethod
    def _read(db, file_id, content):
        """Read a kept transcript file into lines, in the caller's transaction, once: a file's lines never change."""
        if db.execute("SELECT 1 FROM recording_transcripts WHERE file_id=?", (file_id,)).fetchone():
            return
        readable, found = transcript.lines(content)
        db.execute("INSERT INTO recording_transcripts(file_id, lines, readable, created_at) VALUES (?,?,?,?)",
                   (file_id, len(found), int(readable), timestamp()))
        db.executemany("""INSERT INTO recording_lines(file_id, position, start_ms, end_ms, speaker, speaker_key, text)
                          VALUES (:file_id, :position, :start_ms, :end_ms, :speaker, :speaker_key, :text)""",
                       [{**line, "file_id": file_id} for line in found])

    def _read_kept(self):
        """Transcripts kept before their lines were read (the recordings step 1 fetched): at most READS a round, each read from
        verified storage against its checksum. One that cannot be read now is left for a later round."""
        with connect(self.path) as db:
            waiting = [dict(row) for row in db.execute(f"""SELECT f.* FROM ({LATEST}) v JOIN recording_files f ON f.id=v.transcript_file_id
                WHERE NOT EXISTS (SELECT 1 FROM recording_transcripts t WHERE t.file_id=f.id) ORDER BY v.started_at DESC LIMIT ?""", (READS,))]
        for file in waiting:
            content = self.files.read(file)
            with self.rosters.lock(), connect(self.path) as db:
                db.execute("BEGIN IMMEDIATE")
                try:
                    self._read(db, file["id"], content)
                    db.commit()
                except BaseException:
                    db.rollback()
                    raise

    # -- The list --------------------------------------------------------------------------------------------------------------
    def _people(self, wanted):
        """{enrollment ID: {name, sections}} for the students some recording is filed under (names and section numbers come
        from the verified roster files; a student no longer on a current roster is not here). Takes the roster lifecycle lock:
        call it with no catalog connection open."""
        if not wanted or self.students is None:
            return {}
        try:
            groups = self.students.students()["groups"]
        except Exception:  # noqa: BLE001 - a roster that cannot be read leaves the names off, never the list
            LOG.warning("Student names could not be read for the recordings list", exc_info=True)
            return {}
        return {student["enrollment_id"]: {"name": student["name"], "sections": list(student["sections"])}
                for group in groups for student in group["students"] if student["enrollment_id"] in wanted}

    def _names(self, wanted):
        """{enrollment ID: roster name} for the students some recording is filed under."""
        return {enrollment: person["name"] for enrollment, person in self._people(wanted).items()}

    def listing(self, only=None, course=None):
        """`GET /api/recordings`: where capture stands, what needs the instructor, and the recordings: those that need filing
        first, then newest first, each named by what it is filed under and its campus day. With `course` (step 6:
        `?course=<course>`), the recordings filed as that course's TRAINING alone, every one of them."""
        from seating import term_label
        if course is not None and (not isinstance(course, str) or not course or len(course) > 100):
            raise ValueError("Name the course by its ID.")
        with connect(self.path) as db:
            state = self._state(db)
            if course:
                rows = [dict(row) for row in db.execute(f"""{LATEST} WHERE EXISTS ({LINKS} AND l.recording_id=r.id AND l.kind='course'
                    AND l.purpose='training' AND l.definition_id=?) ORDER BY v.started_at DESC, r.id""", (course,))]
            else:
                # One that is leaving (Don't keep, not erased yet) is shown nowhere.
                rows = [dict(row) for row in db.execute(f"""{LATEST} WHERE NOT {GOING} {'AND r.id=?' if only else ''} ORDER BY v.started_at DESC, r.id""",
                                                        (only,) if only else ())]
            not_kept = db.execute("SELECT COUNT(*) FROM recording_not_kept WHERE state='not_kept'").fetchone()[0]
            erase_waiting = [row[0] for row in db.execute("SELECT detail FROM recording_erased_bundles ORDER BY erased_at, bundle_id")]
            links = {}
            for link in db.execute(LINKS + (" AND l.recording_id=?" if only else "") + " ORDER BY l.created_at, l.id", (only,) if only else ()):
                links.setdefault(link["recording_id"], []).append(dict(link))
            states = {row["recording_id"]: dict(row) for row in db.execute("SELECT * FROM recording_fetch_state")}
            for found in links.values():
                found.sort(key=lambda link: link["part_start_ms"] or 0)   # several students: in the order their parts come (stable)
            terms = {row["id"]: dict(row) for row in db.execute("SELECT id, code, term, term_other, year, is_current, definition_id FROM course_terms WHERE deleted_at IS NULL")}
            # Step 4: the appointment a student's recording was filed by, read by reference: one recorded as cancelled or a
            # no-show is marked (the recording says someone came; Faculty changes neither).
            from student_support.service import appointments
            booked = appointments(db, [link["appointment_id"] for found in links.values() for link in found])
            sections = {row["id"]: dict(row) for row in db.execute("SELECT id, term_id, number FROM course_sections")}
            courses = {row["id"]: row["code"] for row in db.execute("SELECT id, code FROM course_definitions")}
        names = self._names({link["enrollment_id"] for found in links.values() for link in found if link["enrollment_id"]})
        for_you = {} if only else self.reading.for_you()   # step 3: each recording's open items for the instructor

        def term_name(term_id):
            term = terms.get(term_id)
            return f"{term['code']} · {term_label(term)}" if term else "A term no longer in Faculty"

        def shown(link):
            kind = link["kind"]
            if kind == "enrollment":
                label = f"{names.get(link['enrollment_id']) or 'A student'} · {term_name(link['term_id'])}"
            elif kind == "term":
                label = term_name(link["term_id"])
            elif kind == "course":
                label = courses.get(link["definition_id"]) or "A course no longer in Faculty"
                if link.get("purpose") == "training":   # step 6: kept for reference under the course, not read
                    kind, label = "training", f"Training · {label}"
            elif kind == "class_day":
                section = sections.get(link["section_id"])
                label = f"Class · {(terms.get(link['term_id']) or {}).get('code', '')} Section {section['number'] if section else ''}".strip()
            else:
                label = "Memo"
            state = (booked.get(link["appointment_id"]) or {}).get("state", "")
            return {"kind": "student" if kind == "enrollment" else kind, "label": label, "filed_by": link["filed_by"],
                    "appointment": bool(link["appointment_id"]),
                    **({"appointment_off": state} if state in ("cancelled", "no_show") else {}),
                    **({"part": {"start_ms": link["part_start_ms"], "end_ms": link["part_end_ms"]}} if link["part_start_ms"] is not None else {}),
                    **({"date": link["class_date"]} if kind == "class_day" else {}),
                    **({"definition_id": link["definition_id"]} if kind == "training" else {})}

        def named(found, filed):
            """What a recording is called: what it is filed under; several students of one term by their names, the term once."""
            if len(found) > 1 and all(link["kind"] == "enrollment" for link in found) and len({link["term_id"] for link in found}) == 1:
                return f"{' + '.join(names.get(link['enrollment_id']) or 'A student' for link in found)} · {term_name(found[0]['term_id'])}"
            return " + ".join(link["label"] for link in filed)

        recordings = []
        for row in rows:
            found, state_of = links.get(row["id"], []), states.get(row["id"]) or {}
            local = filing.campus(parse(row["started_at"]))
            filed = [shown(link) for link in found]
            ours = bool(found)   # every filed link is Faculty's: one that is not kept has no row to list
            kept = [role for role in FILE_ROLES if row[f"{role}_file_id"]]
            recordings.append({
                "id": row["id"], "started_at": row["started_at"], "start_known": bool(row["start_known"]),
                "date": local.date().isoformat(), "time": local.strftime("%H:%M"), "duration_ms": row["duration_ms"],
                "plaud_title": row["title"], "revision": row["revision"],
                "filing": "filed" if ours else "needs_filing",
                "filing_detail": state_of.get("filing_detail", "") if not found else "",
                "links": filed, "name": named(found, filed) if filed else "Needs filing",
                "kept": kept, "for_you": for_you.get(row["id"], 0),
                "fetch": {"status": state_of.get("status", "idle") if ours else "idle", "detail": state_of.get("detail", "") if ours else ""}})
        recordings.sort(key=lambda item: item["filing"] != "needs_filing")   # stable: newest first within each
        waiting = sum(1 for item in recordings if item["filing"] == "needs_filing")
        if not course:
            recordings = [item for index, item in enumerate(recordings) if item["filing"] == "needs_filing" or index < waiting + SHOWN]
        sign_in = state["status"] == "sign_in"
        choices = {
            "terms": sorted(({"id": term["id"], "label": term_name(term["id"])} for term in terms.values() if term["is_current"]),
                            key=lambda item: item["label"].casefold()),
            "courses": sorted(({"id": definition, "label": code} for definition, code in courses.items()
                               if any(term["definition_id"] == definition for term in terms.values())), key=lambda item: item["label"].casefold()),
            "sections": sorted(({"id": section["id"], "label": f"{terms[section['term_id']]['code']} Section {section['number']} · {term_label(terms[section['term_id']])}"}
                                for section in sections.values() if section["term_id"] in terms and terms[section["term_id"]]["is_current"]),
                               key=lambda item: item["label"].casefold())}
        mine = sum(for_you.values())
        off = sum(1 for item in recordings if any(link.get("appointment_off") for link in item["links"]))
        return {"capture": self.status(state), "attention": {"needs_filing": waiting, "sign_in": sign_in, "for_you": mine,
                                                             "appointments_off": off, "count": waiting + int(sign_in) + mine + off},
                # NOT KEPT: how many recordings at Plaud Faculty holds nothing of (their number is all it can say of them).
                "not_kept_count": not_kept,
                # ... and, of what was erased, how many bundles of files a drive still holds, with the engine's reason: said
                # on the page, since "erased" is not yet true of them.
                "erase_waiting": {"count": len(erase_waiting), "detail": next((detail for detail in erase_waiting if detail), "")},
                "time_zone": ZONE_NAME, "recordings": recordings, "choices": choices}

    # -- One recording (step 2) ------------------------------------------------------------------------------------------------
    def _one(self, db, recording_id):
        row = db.execute(f"{LATEST} WHERE r.id=?", (str(recording_id),)).fetchone()
        if not row:
            raise NotFound("This recording no longer exists.")
        return dict(row)

    @staticmethod
    def _speakers(db, row):
        """(the names given in Faculty by Plaud's first label, the speakers of the kept transcript in the order they first speak:
        [{key, plaud, name, shown, lines}]). A speaker reads as the name given here, else as Plaud names them."""
        given = {key: name for key, name in json.loads(row["speakers_json"] or "{}").items() if isinstance(name, str) and name}
        found = []
        if row["transcript_file_id"]:
            found = [{"key": line["speaker_key"], "plaud": line["speaker"], "name": given.get(line["speaker_key"]),
                      "shown": given.get(line["speaker_key"]) or line["speaker"] or "Unnamed speaker", "lines": line["n"]}
                     for line in db.execute("""SELECT speaker_key, speaker, COUNT(*) n, MIN(position) first FROM recording_lines
                                               WHERE file_id=? GROUP BY speaker_key ORDER BY first""", (row["transcript_file_id"],))]
        return given, found

    def recording(self, recording_id, start=None, end=None):
        """`GET /api/recordings/<id>`: one recording as its page shows it: what it is filed under and when it was, its transcript's
        lines (each with who spoke and when), its speakers, and whether its audio is kept. With `start`/`end` (milliseconds into
        the recording) only the lines of that stretch, and neither Plaud's notes nor its outline: what a reader of one moment
        asks for. Plaud's notes and outline are read from verified storage, checked, and are Plaud's words."""
        window = start is not None or end is not None
        for value in (start, end):
            if value is not None and (not isinstance(value, int) or value < 0):
                raise ValueError("Give the stretch in milliseconds from the start of the recording.")
        found = self.listing(only=str(recording_id))["recordings"]
        if not found:
            raise NotFound("This recording no longer exists.")
        answer = found[0]
        with connect(self.path) as db:
            row = self._one(db, recording_id)
            _, speakers = self._speakers(db, row)
            shown = {speaker["key"]: speaker["shown"] for speaker in speakers}
            read = db.execute("SELECT * FROM recording_transcripts WHERE file_id=?", (row["transcript_file_id"],)).fetchone() \
                if row["transcript_file_id"] else None
            lines = []
            if read:
                lines = [{"line": line["position"], "start_ms": line["start_ms"], "end_ms": line["end_ms"], "at": transcript.offset(line["start_ms"]),
                          "speaker_key": line["speaker_key"], "speaker": shown.get(line["speaker_key"], line["speaker"]), "text": line["text"]}
                         for line in db.execute("""SELECT * FROM recording_lines WHERE file_id=? AND end_ms>=? AND start_ms<=? ORDER BY position""",
                                                (row["transcript_file_id"], start or 0, end if end is not None else 2 ** 62))]
            files = {role: dict(file) for role in ("notes", "outline", "audio") if row[f"{role}_file_id"]
                     for file in [db.execute("SELECT * FROM recording_files WHERE id=?", (row[f"{role}_file_id"],)).fetchone()] if file}
        state = "none" if not row["transcript_file_id"] else "waiting" if not read else "ready" if read["readable"] else "unreadable"
        answer.update(transcript={"state": state, "lines": read["lines"] if read else 0}, speakers=speakers, lines=lines,
                      audio=self._audio_named(row, files.get("audio"), answer["filing"] == "filed"))
        if window:
            answer["window"] = {"start_ms": start or 0, "end_ms": end}
            return answer
        answer.update(self.reading.page(row["id"]))   # step 3: what came out of it
        kept = self.files.read_many([files[role] for role in ("notes", "outline") if role in files])
        answer.update(notes=transcript.notes(kept[files["notes"]["id"]]) if "notes" in files else [],
                      outline=transcript.outline(kept[files["outline"]["id"]]) if "outline" in files else [])
        return answer

    def _audio_named(self, row, own, filed):
        """What a recording's page says of its audio: {type, size, href} when the archive holds it (asked of the intake; only for
        a recording filed as Faculty's) or Faculty still holds its own older file, else None. Nothing is read."""
        named = None
        if filed:
            try:
                found = self.client.audio(row["plaud_id"], wait=intake.PAGE_ASK)
                named = {"type": found["content_type"], "size": found["size"]}
            except intake.IntakeError:
                named = None   # the page then says what it can of the audio Faculty holds, or that the archive has none yet
        if named is None and own:
            named = {"type": own["content_type"], "size": own["size"]}
        return {**named, "href": f"/api/recordings/{row['id']}/audio"} if named else None

    def audio(self, recording_id):
        """(content type, bytes) of a recording's audio. FROM THE ARCHIVE: the file the intake names for it, inside the archive,
        checked against the intake's checksum on every read; only for a recording filed as Faculty's. If the intake cannot give
        it and Faculty still holds its own older file (kept before audio played from the archive), that file, checked against its
        checksum. Otherwise NotFound, saying the archive has none."""
        with connect(self.path) as db:
            row = self._one(db, recording_id)
            if db.execute(f"SELECT 1 FROM recordings r WHERE r.id=? AND {GOING}", (row["id"],)).fetchone():
                raise NotFound("This recording no longer exists.")   # Don't keep: nothing of it is served while it is erased
            filed = any(link["kind"] != "not_faculty" for link in self._links(db, row["id"]))
            file = db.execute("SELECT * FROM recording_files WHERE id=?", (row["audio_file_id"],)).fetchone() if row["audio_file_id"] else None
        why = "The Plaud archive has no audio of this recording."
        if filed:
            try:
                stands = self.client.status()
                found = self.client.audio(row["plaud_id"])
                return found["content_type"], self.client.read(found, stands["archive"], intake.AUDIO_LIMIT)
            except intake.IntakeError as problem:
                why = f"The Plaud archive has no audio of this recording that can be played now ({str(problem).rstrip('.')})."
        if not file:
            raise NotFound(why)
        return file["content_type"], self.files.read(dict(file))

    def search(self, data):
        """`POST /api/recordings/search`: the lines of kept transcripts that hold every word asked for, newest recording first, each
        with its recording, who spoke, when, the words, and the line before and after. Only recordings filed as Faculty's now, and
        only each one's latest transcript. Writes nothing.

        With `meaning` true (step 6) it also answers `nearest`: the passages of those same transcripts nearest what was typed
        as a DESCRIPTION, found by the local embedding worker (recordings/meaning.py), and `meaning`, where that search stands.
        A passage a keyword result already shows is not repeated. Without `meaning` the answer is what it always was."""
        data = data if isinstance(data, dict) else {}
        asked = transcript.words(data.get("query"))
        if not asked:
            raise ValueError("Type a word that was said.")
        limit = data.get("limit", FOUND)
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= FOUND_MOST:
            raise ValueError(f"Ask for 1 to {FOUND_MOST} lines.")
        only = data.get("recording_id")
        if only is not None and (not isinstance(only, str) or not only or len(only) > 100):
            raise ValueError("Name the recording by its ID.")
        where = " AND ".join("l.text LIKE ? ESCAPE '\\'" for _ in asked)
        with connect(self.path) as db:
            hits = [dict(row) for row in db.execute(f"""SELECT v.id recording_id, v.speakers_json, v.transcript_file_id file_id, l.position,
                    l.start_ms, l.end_ms, l.speaker, l.speaker_key, l.text
                FROM ({LATEST}) v JOIN recording_lines l ON l.file_id=v.transcript_file_id
                WHERE EXISTS ({LINKS} AND l.recording_id=v.id AND l.kind!='not_faculty') AND {where} {'AND v.id=?' if only else ''}
                ORDER BY v.started_at DESC, v.id, l.position LIMIT ?""",
                (*[transcript.like(word) for word in asked], *([only] if only else []), limit + 1))]
            more, hits = len(hits) > limit, hits[:limit]
            beside = {}
            for hit in hits:
                for place in (hit["position"] - 1, hit["position"] + 1):
                    if place >= 0 and (hit["file_id"], place) not in beside:
                        near = db.execute("SELECT speaker, speaker_key, text, start_ms FROM recording_lines WHERE file_id=? AND position=?",
                                          (hit["file_id"], place)).fetchone()
                        beside[hit["file_id"], place] = dict(near) if near else None
        about = {identifier: self.listing(only=identifier)["recordings"][0] for identifier in dict.fromkeys(hit["recording_id"] for hit in hits)}

        def said(line, given):
            return {"speaker": given.get(line["speaker_key"]) or line["speaker"] or "Unnamed speaker", "text": line["text"],
                    "start_ms": line["start_ms"], "at": transcript.offset(line["start_ms"])}

        results = []
        for hit in hits:
            given = {key: name for key, name in json.loads(hit["speakers_json"] or "{}").items() if isinstance(name, str) and name}
            recording = about[hit["recording_id"]]
            before, after = beside.get((hit["file_id"], hit["position"] - 1)), beside.get((hit["file_id"], hit["position"] + 1))
            results.append({"recording_id": hit["recording_id"], "name": recording["name"], "date": recording["date"], "time": recording["time"],
                            "line": hit["position"], "end_ms": hit["end_ms"], **said(hit, given),
                            "before": said(before, given) if before else None, "after": said(after, given) if after else None,
                            "href": f"#recordings/{hit['recording_id']}?t={hit['start_ms']}"})
        answer = {"query": " ".join(asked), "words": asked, "results": results, "more": more}
        if data.get("meaning") is True:
            answer["nearest"], answer["meaning"] = self._nearest(" ".join(str(data.get("query") or "").split())[:500], results, only)
        return answer

    def _nearest(self, described, results, only):
        """(the passages nearest a description, where search by meaning stands): each passage with its recording and day, where
        it begins, and its lines with who spoke; it opens the recording at that moment. Who may be shown is decided in
        `Meaning.search`, from the catalog."""
        from . import meaning
        if self.meaning is None:
            return [], {"state": "off", "detail": meaning.OFF, "indexed": 0, "waiting": 0}
        found, state = self.meaning.search(described, shown={(hit["recording_id"], hit["line"]) for hit in results}, only=only)
        about = {identifier: self.listing(only=identifier)["recordings"][0] for identifier in dict.fromkeys(passage["recording_id"] for passage in found)}
        with connect(self.path) as db:
            given = {identifier: {key: name for key, name in json.loads(self._one(db, identifier)["speakers_json"] or "{}").items()
                                  if isinstance(name, str) and name} for identifier in about}
        nearest = []
        for passage in found:
            recording, lines, names = about[passage["recording_id"]], passage["lines"], given[passage["recording_id"]]
            nearest.append({"recording_id": passage["recording_id"], "name": recording["name"], "date": recording["date"], "time": recording["time"],
                            "line": lines[0]["position"], "start_ms": lines[0]["start_ms"], "end_ms": lines[-1]["end_ms"],
                            "at": transcript.offset(lines[0]["start_ms"]),
                            "lines": [{"speaker": names.get(line["speaker_key"]) or line["speaker"] or "Unnamed speaker", "text": line["text"],
                                       "start_ms": line["start_ms"], "at": transcript.offset(line["start_ms"])} for line in lines],
                            "href": f"#recordings/{passage['recording_id']}?t={lines[0]['start_ms']}"})
        return nearest, state

    def trainings(self, definition_id):
        """`GET /api/recordings/trainings?definition_id=<course>` (step 6; COURSE-NOTES "TRAINING RECORDINGS"): the recordings
        filed as that course's TRAINING, newest first, as its course page lists them. Kept for reference in every term of the
        course; never read for items. Writes nothing."""
        if not isinstance(definition_id, str) or not definition_id or len(definition_id) > 100:
            raise ValueError("Name the course by its ID.")
        with connect(self.path) as db:
            course = db.execute("SELECT code FROM course_definitions WHERE id=?", (definition_id,)).fetchone()
            if not course:
                raise NotFound("This course is no longer in Faculty.")
            rows = [dict(row) for row in db.execute(f"""{LATEST} WHERE EXISTS ({LINKS} AND l.recording_id=r.id AND l.kind='course'
                AND l.purpose='training' AND l.definition_id=?) ORDER BY v.started_at DESC, r.id""", (definition_id,))]
        found = []
        for row in rows:
            local = filing.campus(parse(row["started_at"]))
            found.append({"id": row["id"], "date": local.date().isoformat(), "time": local.strftime("%H:%M"), "start_known": bool(row["start_known"]),
                          "duration_ms": row["duration_ms"], "kept": [role for role in FILE_ROLES if row[f"{role}_file_id"]]})
        return {"definition_id": definition_id, "code": course["code"], "count": len(found), "recordings": found}

    def name_speaker(self, recording_id, data, actor="workspace"):
        """`POST /api/recordings/<id>/speakers`: the name the instructor gives one speaker of a recording (an empty name takes it
        back). A revision of the recording, kept by Plaud's first label for that voice; never written to Plaud. A repeated request
        ID returns what it did."""
        data = data if isinstance(data, dict) else {}
        request_id = valid_request_id(data.get("request_id"))
        key, name = data.get("speaker"), data.get("name", "")
        if not isinstance(key, str) or not key or not isinstance(name, str):
            raise ValueError("Say which speaker, and the name.")
        name = " ".join(name.split())
        if len(name) > 100:
            raise ValueError("A speaker's name is at most 100 characters.")
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                row = self._one(db, recording_id)
                earlier = db.execute("SELECT recording_id FROM recording_revisions WHERE request_id=? LIMIT 1", (request_id,)).fetchone()
                if earlier and earlier["recording_id"] != row["id"]:
                    raise Conflict("This request ID already named a speaker of another recording.")
                if not earlier:
                    given, speakers = self._speakers(db, row)
                    if key not in {speaker["key"] for speaker in speakers}:
                        raise ValueError("That is not a speaker of this recording.")
                    if name:
                        given[key] = name
                    else:
                        given.pop(key, None)
                    self._revise(db, row["id"], self._latest(db, row["id"]), "instructor", request_id=request_id, actor=actor,
                                 speakers_json=encoded(given))
                db.commit()
            except BaseException:
                db.rollback()
                raise
        if not earlier:
            self.reading.voice_named(row["id"])   # step 6: a memo whose one voice is now the instructor's has its requests asked
        return self.recording(recording_id)
