"""Recordings, step 1: Intake (docs/decisions/recordings.md, RECORDINGS) offline, against the stand-in of the Plaud intake
(tests/fake_intake.py: a real archive folder and an executable stand-in command): Faculty asks Plaud nothing, only the five commands
list, show, audio, status and check-now are ever run (check-now only on a round the instructor asked for), what the intake says
of the sign-in and of its last listing is what the round says of itself, a file is read only inside the archive and checked against
the intake's checksum, a recording is filed by where its start falls on the calendar, one that waits for the instructor is never
read from the archive, and what is kept is a verified bundle of words (no audio: it plays from the archive). ONLY WHAT IS FACULTY'S
IS KEPT (requirement, 2026-11-14): one the calendar shows to be nothing of Faculty's leaves no row, Don't keep erases a recording
with its files, neither is listed or asked about again, and Ask me again brings what was not kept back to be decided. Nothing here
reaches Plaud or holds a real recording; the people are invented."""
import ast
import hashlib
import io
import json
import os
import sqlite3
import stat
import tempfile
import threading
import time
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from browser_course_pages import seed_course
from fake_intake import OGG, FakeIntake
from test_rosters import csv_bytes
from test_storage import HEADERS, temporary_storage

from app import create_app
from course_storage import StorageError
from canonical_json import encoded
from recordings import filing, intake, service as recording_service
from rosters import connect
from scheduling.clock import CAMPUS_ZONE

ALICE, BOB, CAROL = "001234567", "123456789", "222333444"
ROWS = {507: [["", "Example, Alice", ALICE, "Registered", "MATH", "U1", "", "Y", "", "", "", "alice@example.test"],
              ["", "Example, Bob", BOB, "Registered", "MATH", "U2", "", "N", "", "", "", "bob@example.test"]],
        508: [["", "Example, Carol", CAROL, "Registered", "MATH", "U1", "", "Y", "", "", "", "carol@example.test"]]}
NOW = datetime(2026, 11, 11, 22, 0, tzinfo=timezone.utc)   # Wednesday, November 11, 4:00 pm on campus
MON, TUE, WED = "2026-11-09", "2026-11-10", "2026-11-11"
TALK = [{"start_time": 0, "end_time": 4200, "speaker": "Speaker 1", "content": "Calculators stay in the bag for Exam 3."},
        {"start_time": 4200, "end_time": 9000, "speaker": "Avery Quill", "content": "And the seating charts go out Thursday."}]
SUMMARY = [{"type": "auto_sum_note", "tab": "Summary", "content": "A meeting about Exam 3."}]


def campus(day, clock):
    """The instant of a campus wall-clock time."""
    return datetime.fromisoformat(f"{day}T{clock}").replace(tzinfo=CAMPUS_ZONE).astimezone(timezone.utc)


class FakeGoogle:
    """The instructor's Google calendar as Faculty's copy of it is fed to the calendar (google_calendar/service.py `feed`)."""

    def __init__(self):
        self.rows, self.gap, self.fail = [], None, False

    def add(self, title, day, start, end, all_day=False):
        self.rows.append({"id": f"google:primary:{len(self.rows)}", "source": "google", "title": title, "date": day, "end_date": day,
                          "time": "" if all_day else start, "end_time": "" if all_day else end, "all_day": all_day, "location": "",
                          "notes": "", "definition_id": None, "term_id": None, "section_id": None, "code": "", "term_label": "",
                          "sections": [], "href": "", "editable": False, "version": None, "time_basis": "campus", "category": "event"})

    def feed(self, start, end):
        if self.fail:
            raise RuntimeError("catalog busy")
        return [dict(row) for row in self.rows if start <= row["date"] <= end]

    def unchecked(self, now=None):
        return self.gap


class Base(unittest.TestCase):
    storage = False

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.clock = NOW
        self.fake = FakeIntake(self.temp.name).start(clock=lambda: self.clock)
        self.addCleanup(self.fake.stop)
        clean = {k: v for k, v in os.environ.items() if not k.startswith("FACULTY_")}
        with mock.patch.dict(os.environ, {**clean, "FACULTY_PLAUD_INTAKE": str(self.fake.command)}, clear=True):
            if self.storage:
                os.makedirs(os.path.join(self.temp.name, "drives"))
                self.drives = temporary_storage(os.path.join(self.temp.name, "drives"))
                self.app = create_app(storage=self.drives)
            else:
                self.app = create_app(os.path.join(self.temp.name, "data"))
        self.app.testing = True
        self.client = self.app.test_client()
        self.record = self.app.extensions["recordings"]
        self.record.now = self.tick
        self.google = self.record.google = FakeGoogle()
        course = seed_course(self.client, code="MATH 140", sections=[507, 508], multiple_sections=True)
        self.definition = course["definition_id"]
        self.group = self.client.get("/api/state").json["course_groups"][0]["id"]
        for number, rows in ROWS.items():
            answer = self.client.post(f"/api/course-groups/{self.group}/sections/{number}/roster",
                                      data={"file": (io.BytesIO(csv_bytes(rows)), "roster.csv")}, headers=HEADERS)
            self.assertEqual(answer.status_code, 201, answer.json)
        with self.db() as db:
            self.sections = {r["number"]: r["id"] for r in db.execute("SELECT id, number FROM course_sections")}
            self.term = db.execute("SELECT id FROM course_terms").fetchone()[0]
            self.students = {r["uin"]: r["id"] for r in db.execute("SELECT id, uin FROM student_enrollments")}

    # -- helpers --
    def tick(self):
        """Capture's clock: a second later each time it is read, so what happens later is later."""
        self.clock += timedelta(seconds=1)
        return self.clock

    def db(self):
        return connect(self.app.config["DATABASE"])

    def round(self, later=0):
        self.clock += timedelta(seconds=later)
        with self.record.lock():
            return self.record.round()

    def listing(self):
        answer = self.client.get("/api/recordings")
        self.assertEqual(answer.status_code, 200, answer.json)
        return answer.json

    def item(self, plaud_id):
        with self.db() as db:
            found = db.execute("SELECT id FROM recordings WHERE plaud_id=?", (plaud_id,)).fetchone()
        self.assertIsNotNone(found, f"{plaud_id} was never listed")
        return next(row for row in self.listing()["recordings"] if row["id"] == found["id"])

    def links(self, plaud_id):
        with self.db() as db:
            return [dict(row) for row in db.execute(recording_service.LINKS + """ AND l.recording_id=(SELECT id FROM recordings
                                                    WHERE plaud_id=?) ORDER BY l.created_at""", (plaud_id,))]

    def event(self, title, day, start, end, **owner):
        answer = self.client.post("/api/calendar/events", headers=HEADERS, json={
            "request_id": str(uuid.uuid4()), "title": title, "date": day, "time": start, "end_time": end, "all_day": False, **owner})
        self.assertEqual(answer.status_code, 201, answer.json)
        return answer.json

    def appointment(self, uin, day, start, end, **more):
        answer = self.client.post("/api/student-support", headers=HEADERS, json={
            "request_id": str(uuid.uuid4()), "kind": "appointment", "enrollment_id": self.students[uin], "title": "Exam 2 questions",
            "date": day, "time": start, "end_time": end, **more})
        self.assertIn(answer.status_code, (200, 201), answer.json)
        return answer.json

    def schedule(self, *entries):
        """The term's weekly schedule, in use: what the Calendar then shows as class meetings and office hours."""
        saved = self.client.post(f"/api/scheduling/terms/{self.term}", headers=HEADERS, json={
            "request_id": str(uuid.uuid4()), "base_revision": 0, "first_day": "2026-08-24", "last_day": "2026-12-03", "entries": list(entries)})
        self.assertEqual(saved.status_code, 200, saved.json)
        url = f"/api/scheduling/terms/{self.term}/use"
        used = self.client.post(url, headers=HEADERS, json={"request_id": str(uuid.uuid4()), "confirmation": "use",
                                                            "fingerprint": self.client.get(url).json["fingerprint"]})
        self.assertEqual(used.status_code, 200, used.json)

    def file(self, plaud_id, request=None, **body):
        return self.client.post(f"/api/recordings/{self.item(plaud_id)['id']}/file", headers=HEADERS,
                                json={"request_id": request or str(uuid.uuid4()), **body})

    def discard(self, plaud_id, request=None):
        """The instructor's Don't keep."""
        return self.client.post(f"/api/recordings/{self.item(plaud_id)['id']}/discard", headers=HEADERS,
                                json={"request_id": request or str(uuid.uuid4())})

    def held(self, plaud_id):
        """Whether Faculty holds anything of a recording Plaud has (its list row is the least it can hold)."""
        with self.db() as db:
            return bool(db.execute("SELECT 1 FROM recordings WHERE plaud_id=?", (plaud_id,)).fetchone())

    def not_kept(self):
        """{Plaud's ID: (state, who decided)} of the recordings that are not kept: all Faculty holds of them."""
        with self.db() as db:
            return {row["plaud_id"]: (row["state"], row["decided_by"]) for row in db.execute("SELECT * FROM recording_not_kept")}

    def folders(self, bundle_id):
        """Every place the files of one bundle may be."""
        if self.storage:
            return [self.drives.nvme / "recordings" / bundle_id, self.drives.hdd / "recordings" / bundle_id]
        return [Path(self.app.config["DATABASE"]).parent / "recordings" / bundle_id]

    def rows(self):
        """How many rows each table of a recording holds."""
        with self.db() as db:
            return {table: db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in (
                "recordings", "recording_revisions", "recording_links", "recording_fetch_state", "recording_reading_state",
                "recording_bundles", "recording_files", "recording_transcripts", "recording_lines", "recording_readings",
                "recording_items", "recording_item_readings", "recording_item_events", "recording_erased_bundles")}

    def meeting(self, plaud_id="rec-meeting", **more):
        """A recording that began inside a calendar entry naming MATH 140: filed under the term."""
        self.google.add("MATH 140 - all instructors", TUE, "10:00", "11:15")
        return self.fake.add(plaud_id, campus(TUE, "10:02"), minutes=71, title="11-10 Meeting", **{"transcript": TALK, "notes": SUMMARY, **more})


ALLOWED = {"list", "show", "audio", "status", "check-now"}


def own_audio(test, plaud_id, content):
    """What a recording kept before audio played from the archive holds: its own audio file, in a bundle of its own."""
    with test.db() as db:
        recording = dict(db.execute(recording_service.LATEST + " WHERE r.plaud_id=?", (plaud_id,)).fetchone())
    bundle = test.record.files.bundle()
    bundle.add("audio", content, "audio/ogg")
    test.assertEqual(test.record._keep(recording, bundle, None), ["audio"])
    return content


class IntakeTests(Base):
    """FACULTY ASKS PLAUD NOTHING (RECORDINGS "Plaud"): the intake is asked, only five commands are ever run, and what it says of
    the sign-in and of its listing is what a round says of itself."""

    def test_only_the_five_commands_are_run_and_check_now_only_on_a_round_the_instructor_asked_for(self):
        self.meeting()
        with mock.patch.dict(os.environ, {"FACULTY_SECRET_PROBE": "x", "FACULTY_PLAUD_PROBE": "x"}):
            self.assertEqual(self.round()["status"], "ok")
            # An ordinary round: how the intake stands, what it lists, and the one recording filed as Faculty's.
            self.assertEqual(self.fake.commands(), ["status", "list", "show"])
            self.fake.clear()
            self.round(later=600)
            self.assertEqual(self.fake.commands(), ["status", "list"])
            # Check now: the intake makes a round of its own against Plaud first, and says how it stands after it.
            self.fake.clear()
            self.assertEqual(self.client.post("/api/recordings/check", json={}, headers=HEADERS).status_code, 200)
            self.assertEqual(self.fake.commands(), [])   # the press only marks a round wanted
            self.record.run_due()
            self.assertEqual(self.fake.commands(), ["status", "check-now", "status", "list"])
            # A recording filed by hand asks for a round too.
            self.fake.clear()
            self.assertEqual(self.file("rec-meeting", kind="memo").status_code, 200)
            self.record.run_due()
            self.assertEqual(self.fake.commands()[:3], ["status", "check-now", "status"])
        self.assertTrue(set(self.fake.commands()) <= ALLOWED, self.fake.commands())
        self.fake.clear()
        self.round(later=600)
        # Whatever was run, never the intake's own round, its copy or its renewal; and never with a FACULTY_* variable.
        for call in self.fake.calls():
            self.assertIn(call[0], ALLOWED)
        self.assertEqual(self.fake.leaked(), [])
        with self.assertRaises(ValueError):
            self.record.client.run("round")
        with self.assertRaises(ValueError):
            self.record.client.run("renew")
        self.assertEqual(self.fake.leaked(), [])

    def test_both_drop_ins_name_the_intakes_command_and_nothing_of_the_old_token_file_or_address_remains(self):
        root = Path(recording_service.__file__).parent.parent
        for conf in ("deploy/faculty.service.d/plaud.conf", "deploy/faculty-inbox.service.d/plaud.conf"):
            lines = [line for line in (root / conf).read_text(encoding="utf-8").splitlines() if line.startswith("Environment=")]
            self.assertEqual(lines, ["Environment=FACULTY_PLAUD_INTAKE=%h/code/plaud-intake/bin/plaud-intake"], conf)
        old = ("FACULTY_PLAUD_" + "TOKENS_FILE", "FACULTY_PLAUD_" + "URL")
        for path in [*sorted((root / "recordings").glob("*.py")), *sorted((root / "tests").glob("*recording*.py")), root / "tests" / "fake_intake.py",
                     *sorted((root / "deploy").glob("*/plaud.conf"))]:
            text = path.read_text(encoding="utf-8")
            for name in old:
                self.assertNotIn(name, text, f"{path.name} still names {name}")

    def test_the_environment_the_command_is_given_has_no_faculty_variable(self):
        with mock.patch.dict(os.environ, {"FACULTY_SECRET_PROBE": "x", "KEEP_ME": "y"}):
            seen = intake.IntakeClient.environment()
        self.assertNotIn("FACULTY_SECRET_PROBE", seen)
        self.assertEqual(seen.get("KEEP_ME"), "y")
        self.assertFalse(any(name.startswith("FACULTY_") for name in seen))

    def test_nothing_under_recordings_reaches_the_network(self):
        banned = {"urllib", "http", "socket", "ssl", "requests", "ftplib", "smtplib", "xmlrpc", "asyncio", "aiohttp", "httpx", "websocket"}
        folder = Path(recording_service.__file__).parent
        self.assertFalse((folder / "plaud.py").exists())
        for path in sorted(folder.glob("*.py")):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                names = [alias.name for alias in node.names] if isinstance(node, ast.Import) else [node.module or ""] if isinstance(node, ast.ImportFrom) else []
                for name in names:
                    self.assertNotIn(name.split(".")[0], banned, f"{path.name} imports {name}")
        # The module that runs the command runs it as an argument list, never through a shell.
        source = Path(intake.__file__).read_text(encoding="utf-8")
        self.assertIn("shell=False", source)
        self.assertNotIn("shell=True", source)
        self.assertNotIn("os.system", source)

    def test_an_unset_command_is_not_set_up_and_a_missing_failing_slow_or_garbled_one_waits_and_says_why(self):
        self.meeting()
        self.assertEqual(self.round()["status"], "ok")
        before = self.rows()
        # Missing: no file where the command should be.
        self.record.client.command = str(Path(self.temp.name) / "nowhere" / "plaud-intake")
        outcome = self.round(later=600)
        self.assertEqual(outcome["status"], "waiting", outcome)
        self.assertIn("could not be run", outcome["detail"])
        self.assertEqual(self.rows(), before)
        # Unset: this Faculty instance has no Plaud intake to ask.
        self.record.client.command = ""
        self.assertEqual(self.listing()["capture"]["setup"], "Plaud is not set up for this Faculty instance.")
        self.assertFalse(self.listing()["capture"]["ready"])
        outcome = self.round(later=600)
        self.assertEqual((outcome["status"], outcome["detail"]), ("waiting", "Plaud is not set up for this Faculty instance."))
        self.record.client.command = str(self.fake.command)
        # A command that answers something that is not one JSON object.
        self.fake.behave(garbage=True)
        outcome = self.round(later=600)
        self.assertEqual(outcome["status"], "waiting", outcome)
        self.assertIn("did not answer status in a shape Faculty reads", outcome["detail"])
        # A command that never answers: given up after its time.
        self.fake.behave(hang=True)
        with mock.patch.object(intake, "ASK", 1):
            outcome = self.round(later=600)
        self.assertEqual(outcome["status"], "waiting", outcome)
        self.assertIn("did not answer status in time", outcome["detail"])
        # A command that exits 1 with an error, and one that does not have the command Faculty asked it.
        self.fake.behave()
        self.fake.not_ready("The archive folder is not there.")
        outcome = self.round(later=600)
        self.assertEqual(outcome["status"], "waiting", outcome)
        self.assertIn("The Plaud archive is not ready: The archive folder is not there.", outcome["detail"])
        self.assertEqual(self.fake.commands()[-1], "status")   # nothing was listed from an archive that is not ready
        self.assertEqual(self.rows(), before)
        self.assertEqual(self.item("rec-meeting")["kept"], ["transcript", "notes"])   # what Faculty kept stays
        self.fake.is_ready()
        self.assertEqual(self.round(later=600)["status"], "ok")
        with self.assertRaises(intake.IntakeError):
            self.record.client.run("status", wait=5, accept=())   # an exit status not accepted is an error, not an answer

    def test_the_sign_in_to_be_made_again_is_what_the_intake_says_and_what_the_archive_holds_is_still_filed_and_copied(self):
        self.fake.sign_in_again("Plaud refused the sign-in. Run plaud login.")
        self.meeting()
        outcome = self.round()
        self.assertEqual(outcome["status"], "sign_in", outcome)
        self.assertTrue(outcome["detail"].startswith("Sign in to Plaud again"), outcome["detail"])
        self.assertIn("Plaud refused the sign-in. Run plaud login.", outcome["detail"])
        self.assertIn("plaud-intake status", outcome["detail"])
        # Still listed, filed and copied from what the archive holds; the intake is not asked to go to Plaud.
        row = self.item("rec-meeting")
        self.assertEqual((row["filing"], row["kept"], row["fetch"]["status"]), ("filed", ["transcript", "notes"], "kept"))
        self.assertNotIn("check-now", self.fake.commands())
        self.client.post("/api/recordings/check", json={}, headers=HEADERS)
        self.record.run_due()
        self.assertNotIn("check-now", self.fake.commands())
        page = self.listing()
        self.assertEqual((page["capture"]["sign_in"], page["attention"]["sign_in"], page["attention"]["count"]), (True, True, 1))
        # It was not a success: the next round looks back as far as the last that was.
        self.assertEqual(self.record.status()["last_success_at"], None)
        self.fake.signed_in()
        outcome = self.round(later=600)
        self.assertEqual(outcome["status"], "ok", outcome)
        self.assertEqual((self.listing()["attention"]["sign_in"], self.listing()["capture"]["sign_in"]), (False, False))
        self.assertIsNotNone(self.record.status()["last_success_at"])

    def test_a_listing_of_plaud_more_than_thirty_minutes_old_waits_and_says_which(self):
        self.meeting()
        self.fake.listed_at(self.clock - timedelta(minutes=29))
        self.assertEqual(self.round()["status"], "ok")
        self.fake.listed_at(self.clock - timedelta(minutes=31))
        outcome = self.round(later=600)
        self.assertEqual(outcome["status"], "waiting", outcome)
        self.assertIn("The Plaud intake has not listed Plaud since", outcome["detail"])
        self.assertIn("systemctl --user status plaud-intake.timer", outcome["detail"])
        # What the archive already holds is still listed, filed and copied.
        self.fake.add("rec-new", campus(TUE, "10:20"), minutes=5, title="Another", transcript=TALK)
        self.round(later=60)
        self.assertEqual(self.item("rec-new")["kept"], ["transcript"])
        # An intake whose listing is very old is stale all the same.
        self.fake.listed_at(datetime(1970, 1, 1, tzinfo=timezone.utc))
        self.assertEqual(self.round(later=600)["status"], "waiting")
        self.fake.listed_at(None)
        self.assertEqual(self.round(later=600)["status"], "ok")

    def test_check_now_that_cannot_ask_plaud_says_so_and_goes_on_with_what_the_archive_holds(self):
        self.meeting()
        self.round()
        self.fake.fails_check_now(1, "The archive was busy.")
        self.client.post("/api/recordings/check", json={}, headers=HEADERS)
        self.fake.clear()
        self.record.run_due()
        self.assertEqual(self.fake.commands(), ["status", "check-now", "status", "list"])
        capture = self.listing()["capture"]
        self.assertEqual(capture["status"], "ok")
        self.assertIn("Check now could not ask Plaud (The archive was busy)", capture["detail"])
        self.assertEqual(self.item("rec-meeting")["filing"], "filed")

    def test_a_recording_the_intake_has_not_archived_yet_appears_after_check_now_only(self):
        self.google.add("MATH 140 - all instructors", TUE, "10:00", "11:15")
        self.fake.add("rec-just-made", campus(TUE, "10:02"), minutes=5, title="Just made", transcript=TALK, pending=True)
        self.round()
        self.assertFalse(self.held("rec-just-made"))
        self.client.post("/api/recordings/check", json={}, headers=HEADERS)
        self.record.run_due()
        self.assertEqual(self.item("rec-just-made")["filing"], "filed")

    def test_a_file_outside_the_archive_a_wrong_checksum_and_a_symlink_are_refused_and_nothing_of_them_is_kept(self):
        self.google.add("MATH 140 - all instructors", TUE, "10:00", "11:15")
        for refusal, said in (("outside", "outside its archive"), ("corrupt", "did not match its checksum"), ("symlink", "could not be opened")):
            with self.subTest(refusal=refusal):
                name = f"rec-{refusal}"
                self.fake.add(name, campus(TUE, "10:02"), minutes=5, title=name, transcript=TALK, notes=SUMMARY)
                getattr(self.fake, refusal)(name, "transcript")
                before = self.rows()
                outcome = self.round(later=600)
                self.assertEqual(outcome["status"], "ok", outcome)
                self.assertIn(said, outcome["detail"])                    # said in the round's detail ...
                row = self.item(name)
                self.assertEqual((row["kept"], row["fetch"]["status"]), ([], "waiting"))   # ... and nothing of it was kept
                self.assertIn(said, row["fetch"]["detail"])
                after = self.rows()
                self.assertEqual((after["recording_files"], after["recording_bundles"]), (before["recording_files"], before["recording_bundles"]))
                self.assertNotIn("calculators", outcome["detail"].lower())   # no file's contents in what is said
                self.fake.forget(name)

    def test_the_file_reader_alone(self):
        archive = self.fake.archive
        self.fake.add("rec-a", campus(TUE, "10:02"), title="A", transcript=TALK, notes=SUMMARY)
        entry = self.fake.versions["rec-a", "transcript"][-1]
        read = intake.IntakeClient.read
        self.assertEqual(json.loads(read(entry, archive, intake.JSON_LIMIT)), TALK)
        for bad in ({**entry, "sha256": "0" * 64}, {**entry, "sha256": "short"}, {**entry, "path": "relative/transcript.json"},
                    {**entry, "path": str(archive.parent / "state.json")}, {**entry, "path": str(archive / ".." / "state.json")},
                    {**entry, "path": str(archive / "recordings")}, {"sha256": entry["sha256"]}):
            with self.subTest(bad=bad), self.assertRaises(intake.Refused):
                read(bad, archive, intake.JSON_LIMIT)
        with self.assertRaises(intake.Refused):
            read(entry, archive, 10)                                       # larger than Faculty keeps
        with self.assertRaises(intake.Refused):
            read(entry, "", intake.JSON_LIMIT)                             # no archive named
        with self.assertRaises(intake.Refused):
            read(entry, self.fake.base / "other", intake.JSON_LIMIT)       # an archive it is not inside

    def test_nothing_waiting_or_not_kept_is_read_from_the_archive(self):
        self.google.add("MATH 664 lecture", TUE, "12:40", "13:55")
        self.fake.add("rec-664", campus(TUE, "12:41"), title="Lecture", transcript=TALK)
        self.fake.add("rec-unknown", campus(TUE, "18:30"), title="Evening", transcript=TALK)
        for later in (0, 600, 600):
            self.assertEqual(self.round(later=later)["status"], "ok")
        self.assertEqual(self.fake.asked("show"), [])
        self.assertEqual(self.fake.asked("audio"), [])
        waiting = self.item("rec-unknown")
        self.assertEqual(self.client.get(f"/api/recordings/{waiting['id']}/audio").status_code, 404)
        self.assertEqual(self.client.get(f"/api/recordings/{waiting['id']}").json["audio"], None)
        self.assertEqual(self.fake.asked("audio"), [])


class CaptureTests(Base):
    def test_a_new_recording_is_filed_by_the_calendar_and_its_words_copied_from_the_archive_within_one_round(self):
        self.meeting(marks=[{"start_time": 61000}], outline=[{"start_time": 0, "topic": "Exam 3"}])
        outcome = self.round()
        self.assertEqual((outcome["status"], json.loads(outcome["counts_json"])["fetched"]), ("ok", 1), outcome)
        row = self.item("rec-meeting")
        self.assertEqual((row["filing"], row["name"], row["links"][0]["filed_by"]), ("filed", "MATH 140 · Fall 2026", "calendar"))
        self.assertEqual((row["date"], row["time"], row["duration_ms"], row["plaud_title"]), (TUE, "10:02", 71 * 60000, "11-10 Meeting"))
        # NO AUDIO IS COPIED: it plays from the archive. The fetch is complete: Faculty keeps the transcript and the archive has the audio.
        self.assertEqual((row["kept"], row["fetch"]["status"]), (["transcript", "outline", "marks", "notes"], "kept"))
        # How the intake was asked: how it stands, what it lists, and `show` of the one recording filed as Faculty's. Nothing else.
        self.assertEqual(self.fake.asked(), [("status", None), ("list", None), ("show", "rec-meeting")])
        # What is kept is the files, each with its checksum, read back and checked: byte for byte what the archive holds.
        with self.db() as db:
            files = {r["role"]: dict(r) for r in db.execute("SELECT * FROM recording_files")}
            revisions = [dict(r) for r in db.execute("SELECT * FROM recording_revisions ORDER BY revision")]
        self.assertEqual([(r["revision"], r["source"], bool(r["audio_file_id"])) for r in revisions], [(1, "list", False), (2, "fetch", False)])
        self.assertEqual(sorted(files), ["marks", "notes", "outline", "transcript"])
        for role in ("transcript", "outline", "marks"):
            self.assertEqual(self.record.files.read(files[role]), Path(self.fake.versions["rec-meeting", role][-1]["path"]).read_bytes())
        self.assertEqual(json.loads(self.record.files.read(files["transcript"])), TALK)
        self.assertEqual(json.loads(self.record.files.read(files["notes"])), SUMMARY)
        # The start is kept as the instant and shown as campus wall-clock time.
        self.assertEqual(revisions[0]["started_at"], "2026-11-10T16:02:00Z")
        # The audio is the archive's, served on request and never copied.
        audio = self.client.get(f"/api/recordings/{row['id']}/audio")
        self.assertEqual((audio.status_code, audio.data, audio.mimetype), (200, OGG, "audio/ogg"))
        self.assertEqual(self.client.get(f"/api/recordings/{row['id']}").json["audio"], {"type": "audio/ogg", "size": len(OGG), "href": f"/api/recordings/{row['id']}/audio"})
        with self.db() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM recording_files WHERE role='audio'").fetchone()[0], 0)
        # Nothing more is asked of a kept recording in the next round but how the intake stands and its list.
        self.fake.clear()
        self.round(later=600)
        self.assertEqual(self.fake.asked(), [("status", None), ("list", None)])

    def test_unchanged_notes_rebuilt_from_the_archives_hash_exactly_as_the_ones_already_kept(self):
        notes = [{"type": "auto_sum_note", "tab": "Summary", "content": "A meeting about Exam 3."},
                 {"type": "note", "tab": "Next steps", "content": "Send the seating charts."}]
        self.meeting(notes=notes)
        self.round()
        with self.db() as db:
            kept = dict(db.execute("SELECT * FROM recording_files WHERE role='notes'").fetchone())
        # Faculty's own form (type, tab, content, in the archive's order), through the same `encoded`.
        self.assertEqual(kept["sha256"], hashlib.sha256(encoded(notes).encode("utf-8")).hexdigest())
        # The archive's notes file is another form ({content, tab, title, type}): it is not what is kept, and it is not read again
        # as a change when nothing changed.
        self.assertNotEqual(kept["sha256"], self.fake.versions["rec-meeting", "notes"][-1]["sha256"])
        self.round(later=3700)
        with self.db() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM recording_files WHERE role='notes'").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM recording_revisions").fetchone()[0], 2)
        self.fake.recordings["rec-meeting"]["notes"] = [*notes, {"type": "note", "tab": "Later", "content": "One more."}]
        self.round(later=6 * 3600)
        with self.db() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM recording_files WHERE role='notes'").fetchone()[0], 2)

    def test_the_audio_route_serves_the_archives_file_checks_it_on_every_read_falls_back_to_the_older_own_file_and_is_404_after_dont_keep(self):
        self.meeting()
        self.round()
        recording = self.item("rec-meeting")["id"]
        route = f"/api/recordings/{recording}/audio"
        self.assertEqual(self.client.get(route).data, OGG)
        self.assertEqual(self.client.get(route, headers={"Range": "bytes=0-3"}).data, b"OggS")   # a player starts anywhere
        self.assertEqual(self.client.get(route).headers["Cache-Control"], "no-store")
        # Checked against the intake's checksum on EVERY read: a file changed after the first is refused, nothing is served.
        self.fake.corrupt("rec-meeting", "audio")
        refused = self.client.get(route)
        self.assertEqual(refused.status_code, 404)
        self.assertIn("did not match its checksum", refused.json["error"])
        # A recording kept before audio played from the archive still holds its own file: served when the archive cannot give it.
        older = b"OggS\x00\x02" + b"older own copy"
        own_audio(self, "rec-meeting", older)
        self.assertEqual(self.client.get(route).data, older)
        self.assertEqual(self.client.get(f"/api/recordings/{recording}").json["audio"]["size"], len(OGG))   # the page names the archive's file
        self.assertIn("audio", self.item("rec-meeting")["kept"])
        # The archive is asked first: where it can give the file, that is what plays.
        self.fake.recordings["rec-meeting"]["audio"] = OGG + b"later"
        self.assertEqual(self.client.get(route).data, OGG + b"later")
        # When the intake cannot be asked at all, the older own file still plays.
        self.fake.behave(garbage=True)
        self.assertEqual(self.client.get(route).data, older)
        self.fake.behave()
        # Don't keep: nothing of it is served, the archive is untouched, and the route answers 404.
        archived = Path(self.fake.versions["rec-meeting", "audio"][-1]["path"])
        self.fake.clear()
        self.assertEqual(self.discard("rec-meeting").status_code, 200)
        self.assertEqual(self.client.get(route).status_code, 404)
        self.assertTrue(archived.exists())
        self.assertEqual(self.fake.asked("audio"), [])

    def test_a_recording_with_no_audio_in_the_archive_has_none_to_play_and_says_so_and_waits_for_it(self):
        self.meeting(audio=None)
        self.round()
        row = self.item("rec-meeting")
        self.assertEqual((row["kept"], row["fetch"]["status"], row["fetch"]["detail"]), (["transcript", "notes"], "waiting", "The archive has no audio of it yet."))
        page = self.client.get(f"/api/recordings/{row['id']}")
        self.assertEqual(page.json["audio"], None)
        missing = self.client.get(f"/api/recordings/{row['id']}/audio")
        self.assertEqual(missing.status_code, 404)
        self.assertIn("The Plaud archive has no audio of this recording", missing.json["error"])
        # The intake archives it; the next ask of the archive completes the fetch without copying anything more.
        self.fake.recordings["rec-meeting"]["audio"] = OGG
        self.round(later=600)
        row = self.item("rec-meeting")
        self.assertEqual((row["kept"], row["fetch"]["status"]), (["transcript", "notes"], "kept"))
        self.assertEqual(self.client.get(f"/api/recordings/{row['id']}/audio").data, OGG)

    def test_a_round_looks_back_far_enough_to_find_what_was_synced_late_or_made_while_capture_was_down(self):
        windows = []
        listed = self.record.client.recordings

        def asking(start, end):
            windows.append(start)
            return listed(start, end)

        patcher = mock.patch.object(self.record.client, "recordings", asking)
        patcher.start()
        self.addCleanup(patcher.stop)

        def since():
            """How far back the last listing asked, as hours before the clock."""
            return round((self.clock - windows[-1]).total_seconds() / 3600)

        self.meeting()
        self.fake.add("rec-late-sync", campus("2026-11-01", "09:00"), title="Synced ten days later", transcript=TALK)
        outcome = self.round()
        self.assertEqual(since(), 30 * 24)                      # no round has succeeded yet: thirty days
        self.assertEqual(outcome["detail"], "2 recordings at Plaud since Mon, Oct 12, 2 new, 1 fetched.")
        self.assertEqual(self.item("rec-late-sync")["filing"], "needs_filing")
        self.round(later=600)
        self.assertEqual(since(), 3 * 24)                       # the same campus day: three days
        self.assertIn("since Sun, Nov 8", self.listing()["capture"]["detail"])
        # One that began five days ago reaches Plaud only now: an ordinary round does not see it, Check now does.
        self.fake.add("rec-old", campus("2026-11-06", "09:00"), title="From Friday", transcript=TALK)
        self.round(later=600)
        with self.db() as db:
            self.assertIsNone(db.execute("SELECT 1 FROM recordings WHERE plaud_id='rec-old'").fetchone())
        self.client.post("/api/recordings/check", json={}, headers=HEADERS)
        self.record.run_due()
        self.assertEqual(since(), 30 * 24)
        self.assertEqual(self.item("rec-old")["filing"], "needs_filing")
        # The first round of the next campus day looks back thirty days by itself.
        self.round(later=24 * 3600)
        self.assertEqual(since(), 30 * 24)
        # Capture down for longer than that (the sign-in lapsed, the machine off): back to a day before the last success.
        state = {"last_success_at": recording_service.iso(NOW - timedelta(days=40))}
        self.assertEqual(self.record.since(NOW, state, False), NOW - timedelta(days=41))
        same_day = {"last_success_at": recording_service.iso(NOW - timedelta(hours=2))}
        self.assertEqual(self.record.since(NOW, same_day, False), NOW - timedelta(days=3))
        self.assertEqual(self.record.since(NOW, same_day, True), NOW - timedelta(days=30))
        self.assertEqual(self.record.since(NOW, {"last_success_at": ""}, False), NOW - timedelta(days=30))

    def test_a_kept_recording_is_read_again_at_widening_intervals_and_then_no_more(self):
        self.meeting()
        self.round()
        one = ("show", "rec-meeting")
        reads = lambda: self.fake.asked().count(one)  # noqa: E731
        self.assertEqual(reads(), 1)
        # About 1 h, 6 h, 24 h and 72 h after it was kept: not just before each, once just after.
        for hours, expected in ((1, 2), (6, 3), (24, 4), (72, 5)):
            due = NOW + timedelta(hours=hours)
            self.round(later=(due - self.clock).total_seconds() - 900)
            self.assertEqual(reads(), expected - 1, f"read again before {hours} h")
            self.round(later=1200)
            self.assertEqual(reads(), expected, f"not read again at {hours} h")
        for later in (3600, 86400, 7 * 86400, 30 * 86400):
            self.round(later=later)
        self.assertEqual(reads(), 5)                             # five reads in all, where there were 72
        self.assertEqual(self.fake.asked("audio"), [])           # and the audio never asked for by a round: it plays from the archive
        self.assertEqual(self.item("rec-meeting")["fetch"]["status"], "kept")

    def test_the_intake_failing_on_a_later_read_leaves_a_recording_kept_and_uses_up_no_read(self):
        self.meeting()
        self.round()
        one = ("show", "rec-meeting")
        self.assertEqual(self.file("rec-meeting", kind="memo").status_code, 200)   # re-filed: its later reads go on as they were
        with mock.patch.object(self.record.client, "show", side_effect=intake.IntakeError("The Plaud intake could not be run.")):
            self.round(later=3700)
        row = self.item("rec-meeting")
        self.assertEqual((row["fetch"], row["kept"]), ({"status": "kept", "detail": ""}, ["transcript", "notes"]))
        self.round(later=700)                                    # asked again ten minutes later
        self.assertEqual(self.fake.asked().count(one), 2)
        self.round(later=5 * 3600)                               # and the 6 h read still comes
        self.assertEqual(self.fake.asked().count(one), 3)
        # The archive not knowing it leaves what Faculty kept, and ends the reads.
        self.fake.forget("rec-meeting")
        self.round(later=18 * 3600)
        self.assertEqual(self.fake.asked().count(one), 4)
        self.round(later=48 * 3600)
        self.assertEqual(self.fake.asked().count(one), 4)
        row = self.item("rec-meeting")
        self.assertEqual((row["fetch"], row["kept"]), ({"status": "kept", "detail": ""}, ["transcript", "notes"]))

    def test_a_recording_gone_at_plaud_but_held_by_the_archive_is_read_and_kept_like_any_other(self):
        self.meeting()
        self.fake.remove("rec-meeting")   # the instructor deleted it at Plaud: the archive keeps it, marked gone
        self.assertEqual(self.round()["status"], "ok")
        self.assertEqual((self.item("rec-meeting")["filing"], self.item("rec-meeting")["kept"]), ("filed", ["transcript", "notes"]))
        self.assertEqual(self.client.get(f"/api/recordings/{self.item('rec-meeting')['id']}/audio").data, OGG)

    def test_a_kept_recording_the_instructor_does_not_keep_is_erased_with_its_files_and_is_asked_about_no_more(self):
        self.meeting()
        self.fake.add("rec-other", campus(TUE, "10:20"), minutes=5, title="Another", transcript=TALK)   # kept, and untouched by this
        self.round()
        kept = self.item("rec-meeting")
        self.assertEqual(kept["kept"], ["transcript", "notes"])
        with self.db() as db:
            bundles = [row[0] for row in db.execute("""SELECT DISTINCT f.bundle_id FROM recording_files f JOIN recording_revisions v
                ON f.id IN (v.transcript_file_id, v.notes_file_id, v.audio_file_id) WHERE v.recording_id=?""", (kept["id"],))]
            others = db.execute("SELECT COUNT(*) FROM recording_files").fetchone()[0] - 2
        self.assertEqual(len(bundles), 1)
        self.assertTrue(any(path.exists() for path in self.folders(bundles[0])))
        before = self.rows()
        answer = self.discard("rec-meeting", request="req-not-kept")
        self.assertEqual((answer.status_code, answer.json["not_kept"], answer.json["not_kept_count"]), (200, True, 1), answer.json)
        # Nothing of it is left: no row of it in any table, no file of it on any drive, and neither its page nor its audio.
        after = self.rows()
        self.assertEqual((after["recordings"], after["recording_bundles"], after["recording_files"], after["recording_erased_bundles"]),
                         (1, before["recording_bundles"] - 1, others, 0))
        with self.db() as db:
            for table in ("recording_revisions", "recording_links", "recording_fetch_state", "recording_readings", "recording_items"):
                self.assertEqual(db.execute(f"SELECT COUNT(*) FROM {table} WHERE recording_id=?", (kept["id"],)).fetchone()[0], 0, table)
            self.assertIsNone(db.execute("SELECT recording_id FROM recording_erasing").fetchone()[0])
            said = " ".join(str(value) for row in db.execute("SELECT * FROM recording_not_kept") for value in tuple(row))
        self.assertFalse(any(path.exists() for path in self.folders(bundles[0])))
        self.assertEqual([row["id"] for row in self.listing()["recordings"]], [self.item("rec-other")["id"]])
        for address in (f"/api/recordings/{kept['id']}", f"/api/recordings/{kept['id']}/audio"):
            self.assertEqual(self.client.get(address).status_code, 404, address)
        # All Faculty holds of it is Plaud's ID and who decided: no title, no time, no length.
        self.assertEqual(self.not_kept(), {"rec-meeting": ("not_kept", "instructor")})
        self.assertNotIn("11-10 Meeting", said)
        # The other recording is whole, and still served.
        self.assertEqual(self.item("rec-other")["kept"], ["transcript"])
        self.assertEqual(self.client.get(f"/api/recordings/{self.item('rec-other')['id']}/audio").data, OGG)
        # What was erased was Faculty's copy of the words: the recording is whole in the archive, and nothing was asked to change it.
        self.assertTrue(Path(self.fake.versions["rec-meeting", "audio"][-1]["path"]).exists())
        self.assertTrue(Path(self.fake.versions["rec-meeting", "transcript"][-1]["path"]).exists())
        # A repeated press answers as the first did; the recording itself is gone.
        again = self.client.post(f"/api/recordings/{kept['id']}/discard", headers=HEADERS, json={"request_id": "req-not-kept"})
        self.assertEqual((again.status_code, again.json["not_kept_count"]), (200, 1))
        self.assertEqual(self.client.post(f"/api/recordings/{kept['id']}/discard", headers=HEADERS, json={"request_id": str(uuid.uuid4())}).status_code, 404)
        # Plaud still lists it: nothing of it is recorded again, it is never fetched, and it is never asked about.
        self.fake.clear()
        for later in (600, 3600, 6 * 3600):
            self.assertEqual(self.round(later=later)["status"], "ok")
        self.assertNotIn(("show", "rec-meeting"), self.fake.asked())
        self.assertFalse(self.held("rec-meeting"))
        self.assertEqual(self.listing()["attention"]["needs_filing"], 0)
        self.assertEqual(self.listing()["not_kept_count"], 1)
        # Asked about again, it WAITS for the instructor: the calendar that filed it once does not keep it by itself.
        self.assertEqual(self.client.post("/api/recordings/ask-again", headers=HEADERS, json={"request_id": str(uuid.uuid4())}).json["asked"], 1)
        self.record.run_due()
        row = self.item("rec-meeting")
        self.assertEqual((row["filing"], row["filing_detail"], row["kept"]), ("needs_filing", "You asked to be asked about this one again.", []))
        self.round(later=600)
        self.assertEqual(self.item("rec-meeting")["filing"], "needs_filing")
        self.assertNotIn(("show", "rec-meeting"), self.fake.asked())

    def test_a_recording_that_is_leaving_is_hidden_at_once_and_erased_by_the_next_round_when_one_was_running(self):
        self.meeting()
        self.round()
        kept = self.item("rec-meeting")
        with self.record.lock():   # a round is running: the press cannot erase, and hides
            answer = self.client.post(f"/api/recordings/{kept['id']}/discard", headers=HEADERS, json={"request_id": str(uuid.uuid4())})
        self.assertEqual((answer.status_code, answer.json["not_kept"]), (200, True))
        self.assertTrue(self.held("rec-meeting"))                      # not erased yet
        self.assertEqual(self.listing()["recordings"], [])            # and shown nowhere
        self.assertTrue(self.listing()["capture"]["requested"])       # a round was asked for
        for address in (f"/api/recordings/{kept['id']}", f"/api/recordings/{kept['id']}/audio"):
            self.assertEqual(self.client.get(address).status_code, 404, address)
        found = self.client.post("/api/recordings/search", headers=HEADERS, json={"query": "calculators"})
        self.assertEqual(found.json["results"], [])
        refused = self.client.post(f"/api/recordings/{kept['id']}/file", headers=HEADERS, json={"request_id": str(uuid.uuid4()), "kind": "memo"})
        self.assertEqual(refused.status_code, 404)                     # it cannot be filed back while it leaves
        self.fake.clear()
        self.round(later=5)
        self.assertFalse(self.held("rec-meeting"))
        self.assertEqual(self.not_kept(), {"rec-meeting": ("not_kept", "instructor")})
        self.assertNotIn(("show", "rec-meeting"), self.fake.asked())
        self.assertEqual(self.rows()["recording_files"], 0)

    def test_a_recording_marked_not_facultys_before_this_rule_is_erased_by_the_first_round(self):
        """What the catalog held before 2026-11-14: a `not_faculty` link beside the list row (and, for one fetched first, its
        files). The first round after the change erases it as the rule now says."""
        self.meeting()
        self.fake.add("rec-664", campus(TUE, "12:41"), title="Lecture", transcript=TALK)
        self.round()
        with self.record.rosters.lock(), self.db() as db:
            for plaud_id, by in (("rec-meeting", "instructor"), ("rec-664", "calendar")):
                recording = db.execute("SELECT id FROM recordings WHERE plaud_id=?", (plaud_id,)).fetchone()[0]
                self.record._write_filing(db, recording, {"kind": "not_faculty"}, by, {}, "workspace" if by == "instructor" else "automatic")
        self.assertEqual(self.listing()["recordings"], [])
        self.round(later=600)
        self.assertEqual(self.not_kept(), {"rec-meeting": ("not_kept", "instructor"), "rec-664": ("not_kept", "calendar")})
        rows = self.rows()
        self.assertEqual({table: count for table, count in rows.items() if count}, {})

    def test_a_file_that_cannot_be_written_here_is_storage_not_ready_and_never_the_intakes_fault(self):
        self.meeting()
        with mock.patch("recordings.storage.Bundle.add", side_effect=OSError(28, "No space left on device")), \
                self.assertLogs("recordings.service", "WARNING") as logs:
            outcome = self.round()
        self.assertEqual(outcome["status"], "waiting", outcome)   # not "ok" with the recording waiting on the archive for ever
        self.assertIn("storage is not ready", outcome["detail"])
        self.assertIn("No space left on device", outcome["detail"])
        self.assertNotIn("Plaud intake", outcome["detail"])
        self.assertFalse(any("Traceback" in line for line in logs.output))
        row = self.item("rec-meeting")
        self.assertEqual((row["kept"], row["fetch"]["detail"]), ([], ""))   # nothing blamed on the intake for this recording
        self.assertEqual(self.round(later=600)["status"], "ok")
        self.assertEqual(self.item("rec-meeting")["kept"], ["transcript", "notes"])

    def test_a_recording_the_archive_does_not_hold_whole_is_asked_about_again_and_what_it_lacks_is_said(self):
        self.meeting()
        self.fake.recordings["rec-meeting"]["audio"] = None
        self.fake.recordings["rec-meeting"]["transcript"] = None
        self.round()
        row = self.item("rec-meeting")
        self.assertEqual((row["kept"], row["fetch"]["status"]), (["notes"], "waiting"))
        self.assertEqual(row["fetch"]["detail"], "The archive has no transcript of it yet. The archive has no audio of it yet.")
        self.assertEqual(self.round(later=60)["status"], "ok")
        self.assertEqual(self.fake.asked("show"), [("show", "rec-meeting")] * 1)   # not asked about again before ten minutes
        self.fake.recordings["rec-meeting"]["transcript"] = TALK
        self.round(later=600)
        row = self.item("rec-meeting")
        self.assertEqual((row["kept"], row["fetch"]["detail"]), (["transcript", "notes"], "The archive has no audio of it yet."))
        self.fake.recordings["rec-meeting"]["audio"] = OGG
        self.round(later=1200)
        self.assertEqual((self.item("rec-meeting")["fetch"]["status"], self.item("rec-meeting")["fetch"]["detail"]), ("kept", ""))

    def test_what_is_not_facultys_leaves_no_row_what_waits_is_never_fetched_and_ask_again_brings_both_back(self):
        self.google.add("MATH 664 lecture", TUE, "12:40", "13:55")
        self.fake.add("rec-664", campus(TUE, "12:41"), title="Lecture", transcript=TALK)
        self.fake.add("rec-unknown", campus(TUE, "18:30"), title="Evening", transcript=TALK)
        for later in (0, 600, 600):
            self.assertEqual(self.round(later=later)["status"], "ok")
        self.assertEqual({command for command, _ in self.fake.asked()}, {"status", "list"})   # how it stands and the list, and nothing else
        # NOT KEPT by the calendar: nothing of it is held but Plaud's ID, in any of the three rounds that listed it.
        self.assertFalse(self.held("rec-664"))
        self.assertEqual(self.not_kept(), {"rec-664": ("not_kept", "calendar")})
        with self.db() as db:
            everything = " ".join(str(value) for table in ("recordings", "recording_revisions", "recording_links", "recording_fetch_state",
                                                           "recording_not_kept") for row in db.execute(f"SELECT * FROM {table}") for value in tuple(row))
        self.assertNotIn("Lecture", everything)
        unknown = self.item("rec-unknown")
        self.assertEqual((unknown["filing"], unknown["filing_detail"], unknown["kept"]), ("needs_filing", "No calendar entry at that time.", []))
        page = self.listing()
        self.assertEqual(page["attention"], {"needs_filing": 1, "sign_in": False, "for_you": 0, "appointments_off": 0, "count": 1})
        self.assertEqual(([row["id"] for row in page["recordings"]], page["not_kept_count"]), ([unknown["id"]], 1))
        self.assertEqual(self.record.unsure(), 1)   # what Home's card and the sidebar count
        with self.db() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM recording_files").fetchone()[0], 0)
            self.assertEqual([r["source"] for r in db.execute("SELECT source FROM recording_revisions")], ["list"])
        # Don't keep, of one that waits: gone, never fetched, and not asked about again.
        self.assertEqual(self.discard("rec-unknown").status_code, 200)
        self.round(later=600)
        self.assertEqual({command for command, _ in self.fake.asked()} - {"check-now"}, {"status", "list"})   # Don't keep asked for a round
        self.assertEqual((self.listing()["recordings"], self.listing()["not_kept_count"], self.record.unsure()), ([], 2, 0))
        self.assertEqual({table: count for table, count in self.rows().items() if count}, {})
        # `file` no longer takes Not Faculty's: not keeping is the instructor's own press.
        # ASK AGAIN: both are listed again and wait. The calendar does not decide a second time that one is not theirs.
        asked = self.client.post("/api/recordings/ask-again", headers=HEADERS, json={"request_id": str(uuid.uuid4())})
        self.assertEqual((asked.status_code, asked.json["asked"], asked.json["not_kept_count"], asked.json["capture"]["requested"]), (200, 2, 0, True))
        self.record.run_due()
        again = {name: self.item(name) for name in ("rec-664", "rec-unknown")}
        self.assertEqual({name: row["filing"] for name, row in again.items()}, {"rec-664": "needs_filing", "rec-unknown": "needs_filing"})
        self.assertEqual(again["rec-664"]["filing_detail"], "The calendar entry at that time names no course of yours. You asked to be asked about this one again.")
        self.assertEqual(again["rec-unknown"]["filing_detail"], "No calendar entry at that time. You asked to be asked about this one again.")
        self.assertEqual(self.not_kept(), {"rec-664": ("ask", "calendar"), "rec-unknown": ("ask", "instructor")})
        self.round(later=600)
        self.assertTrue(self.held("rec-664"))    # still waiting a round later
        self.assertEqual({command for command, _ in self.fake.asked() if command not in ("status", "check-now")}, {"list"})   # nothing was read for either
        # Keep one: it is fetched in the next round and is no longer marked. Don't keep the other: not kept, as it was.
        filed = self.file("rec-664", kind="term", term_id=self.term)
        self.assertEqual((filed.status_code, filed.json["filing"], filed.json["links"][0]["filed_by"]), (200, "filed", "instructor"))
        self.assertTrue(self.listing()["capture"]["requested"])
        self.record.run_due()
        self.assertIn(("show", "rec-664"), self.fake.asked())
        self.assertNotIn(("show", "rec-unknown"), self.fake.asked())
        self.assertEqual(self.item("rec-664")["kept"], ["transcript"])
        self.assertEqual(self.discard("rec-unknown").status_code, 200)
        self.assertEqual(self.not_kept(), {"rec-unknown": ("not_kept", "instructor")})
        refused = self.client.post(f"/api/recordings/{self.item('rec-664')['id']}/file", headers=HEADERS,
                                   json={"request_id": str(uuid.uuid4()), "kind": "not_faculty"})
        self.assertEqual(refused.status_code, 400)

    def test_ask_again_finds_one_made_long_before_it_was_decided_and_takes_up_nothing_else_that_old(self):
        """Faculty holds no date of a recording that is not kept, only when that was decided, which may be months after it
        was made: Ask again lists Plaud far back, and takes up only what was asked about."""
        self.clock = datetime(2026, 6, 2, 15, 0, tzinfo=timezone.utc)
        self.fake.add("rec-old", campus("2026-06-01", "18:30"), title="Evening", transcript=TALK)
        self.round()
        self.assertEqual(self.file("rec-old", kind="term", term_id=self.term).status_code, 200)
        self.record.run_due()
        self.assertEqual(self.item("rec-old")["kept"], ["transcript"])
        # Five months later: another recording as old, which Faculty never saw, is at Plaud too; and they say Don't keep.
        self.clock = NOW
        self.fake.add("rec-ancient", campus("2026-05-20", "18:30"), title="Older still", transcript=TALK)
        self.round()
        self.assertFalse(self.held("rec-ancient"))   # older than a round lists
        self.assertEqual(self.discard("rec-old").status_code, 200)
        self.assertFalse(self.held("rec-old"))
        self.assertEqual(self.client.post("/api/recordings/ask-again", headers=HEADERS, json={"request_id": str(uuid.uuid4())}).json["asked"], 1)
        self.record.run_due()
        row = self.item("rec-old")
        self.assertEqual((row["filing"], row["kept"], row["date"]), ("needs_filing", [], "2026-06-01"))
        self.assertFalse(self.held("rec-ancient"))   # Ask again brings back only what was not kept
        self.assertEqual(self.not_kept(), {"rec-old": ("ask", "instructor")})
        # The round still says what a round lists (the last thirty days), not how far this one looked.
        self.assertTrue(self.listing()["capture"]["detail"].startswith("0 recordings at Plaud since Mon, Oct 12"), self.listing()["capture"]["detail"])

    def test_a_recording_fetched_twice_is_erased_with_both_bundles_and_every_transcripts_lines(self):
        self.meeting()
        self.round()
        self.fake.recordings["rec-meeting"]["transcript"] = [TALK[0], {**TALK[1], "content": "And the seating charts go out Friday."}]
        self.round(later=3700)   # Plaud changed the transcript: a second bundle, a second file, its own lines
        with self.db() as db:
            bundles = [row[0] for row in db.execute("SELECT id FROM recording_bundles ORDER BY created_at")]
            self.assertEqual(db.execute("SELECT COUNT(*) FROM recording_transcripts").fetchone()[0], 2)
        self.assertEqual(len(bundles), 2)
        self.assertTrue(all(any(path.exists() for path in self.folders(bundle)) for bundle in bundles))
        self.assertEqual(self.discard("rec-meeting").status_code, 200)
        self.assertEqual({table: count for table, count in self.rows().items() if count}, {})
        for bundle in bundles:
            self.assertFalse(any(path.exists() for path in self.folders(bundle)), bundle)

    def test_one_that_cannot_be_erased_stays_hidden_is_rolled_back_whole_and_does_not_hold_back_the_others(self):
        self.meeting()
        self.fake.add("rec-other", campus(TUE, "10:20"), minutes=5, title="Another", transcript=TALK)
        self.round()
        first = self.item("rec-meeting")["id"]
        real = self.record._erase_rows

        def fails(db, recording, *args, **kwargs):
            bundles = real(db, recording, *args, **kwargs)   # every delete is made and the erasure's name cleared ...
            if recording["id"] == first:
                raise sqlite3.OperationalError("disk I/O error")   # ... and then its transaction fails
            return bundles

        with self.record.lock():   # a round is running: both are hidden, and left for the next round to erase
            for name in ("rec-meeting", "rec-other"):
                self.assertEqual(self.discard(name).status_code, 200)
        with mock.patch.object(self.record, "_erase_rows", side_effect=fails), self.assertLogs("recordings.service", "ERROR"):
            self.round(later=5)
        self.assertEqual((self.held("rec-meeting"), self.held("rec-other")), (True, False))
        self.assertEqual(self.listing()["recordings"], [])   # the one that failed is still hidden
        with self.db() as db:
            self.assertIsNone(db.execute("SELECT recording_id FROM recording_erasing").fetchone()[0])   # no erasure is left named
            self.assertEqual(db.execute("SELECT COUNT(*) FROM recording_files").fetchone()[0], 2)       # its rows are whole again
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("DELETE FROM recording_files")                                               # and retained as ever
        self.round(later=600)
        self.assertFalse(self.held("rec-meeting"))
        self.assertEqual({table: count for table, count in self.rows().items() if count}, {})

    def test_a_request_id_is_one_recordings(self):
        self.fake.add("rec-a", campus(TUE, "18:30"), title="A", transcript=TALK)
        self.fake.add("rec-b", campus(TUE, "19:30"), title="B", transcript=TALK)
        self.round()
        other = self.item("rec-b")["id"]
        self.assertEqual(self.discard("rec-a", request="one").status_code, 200)
        # The ID that erased one recording does nothing to another, and neither does one that filed it.
        again = self.client.post(f"/api/recordings/{other}/discard", headers=HEADERS, json={"request_id": "one"})
        self.assertEqual(again.status_code, 409, again.json)
        self.assertEqual(self.file("rec-b", request="two", kind="term", term_id=self.term).status_code, 200)
        self.assertEqual(self.client.post(f"/api/recordings/{other}/discard", headers=HEADERS, json={"request_id": "two"}).status_code, 409)
        self.assertTrue(self.held("rec-b"))
        self.assertEqual(self.not_kept(), {"rec-a": ("not_kept", "instructor")})

    def test_one_the_archive_does_not_list_cannot_be_asked_about_and_stays_not_kept_but_one_gone_at_plaud_can(self):
        """BEHAVIOUR CHANGED by the archive (RECORDINGS "Plaud"): a recording Plaud no longer has but the archive holds is listed
        like any other, so it CAN be asked about again and kept; one the archive does not list stays not kept, as it was."""
        self.fake.add("rec-old", campus(TUE, "18:30"), title="Evening", transcript=TALK)
        self.fake.add("rec-gone", campus(TUE, "19:30"), title="Later", transcript=TALK)
        self.round()
        for name in ("rec-old", "rec-gone"):
            self.assertEqual(self.discard(name).status_code, 200)
        self.fake.forget("rec-old")   # the archive lists it no more
        self.fake.remove("rec-gone")  # Plaud no longer has it; the archive does
        self.assertEqual(self.client.post("/api/recordings/ask-again", headers=HEADERS, json={"request_id": str(uuid.uuid4())}).json["asked"], 2)
        self.record.run_due()
        self.assertEqual(self.not_kept(), {"rec-old": ("not_kept", "instructor"), "rec-gone": ("ask", "instructor")})
        self.assertEqual([row["plaud_title"] for row in self.listing()["recordings"]], ["Later"])
        self.assertEqual(self.item("rec-gone")["filing"], "needs_filing")
        # Kept by the instructor, it is read from the archive and plays from it.
        self.assertEqual(self.file("rec-gone", kind="term", term_id=self.term).status_code, 200)
        self.record.run_due()
        row = self.item("rec-gone")
        self.assertEqual((row["kept"], row["fetch"]["status"]), (["transcript"], "kept"))
        self.assertEqual(self.client.get(f"/api/recordings/{row['id']}/audio").data, OGG)
        self.assertEqual(self.not_kept(), {"rec-old": ("not_kept", "instructor")})

    def test_a_recording_plaud_has_not_finished_is_asked_about_again_and_a_changed_transcript_is_a_new_revision(self):
        self.meeting(transcript=None, notes=None)
        self.round()
        row = self.item("rec-meeting")
        self.assertEqual((row["kept"], row["fetch"]["status"], row["fetch"]["detail"]), ([], "waiting", "The archive has no transcript of it yet."))
        self.fake.recordings["rec-meeting"]["transcript"] = TALK
        self.round(later=60)            # not due yet: the archive is asked again after ten minutes
        self.assertEqual(self.item("rec-meeting")["kept"], [])
        self.round(later=600)
        self.assertEqual((self.item("rec-meeting")["kept"], self.item("rec-meeting")["fetch"]["status"]), (["transcript"], "kept"))
        # Plaud changes the transcript (a speaker named in its app): read again within the hour, kept as a new revision.
        named = [{**TALK[0], "speaker": "Chris"}, TALK[1]]
        self.fake.recordings["rec-meeting"]["transcript"] = named
        self.round(later=600)
        self.round(later=3600)
        with self.db() as db:
            revisions = [dict(r) for r in db.execute("SELECT * FROM recording_revisions ORDER BY revision")]
            transcript = dict(db.execute("SELECT * FROM recording_files WHERE id=?", (revisions[-1]["transcript_file_id"],)).fetchone())
        self.assertEqual([r["source"] for r in revisions], ["list", "fetch", "fetch"])
        self.assertEqual(json.loads(self.record.files.read(transcript)), named)
        self.assertEqual(revisions[-1]["audio_file_id"], None)   # no audio is ever copied
        self.assertEqual(self.fake.asked("audio"), [])
        # The archive kept the earlier transcript beside the newer one; Faculty copied the newer, as archived.
        self.assertEqual(len(self.fake.versions["rec-meeting", "transcript"]), 2)
        self.assertEqual(transcript["sha256"], self.fake.versions["rec-meeting", "transcript"][-1]["sha256"])

    def test_a_renamed_recording_is_a_new_revision_and_one_plaud_gave_no_start_for_waits(self):
        self.meeting()
        self.fake.add("rec-late", campus(TUE, "10:05"), title="Synced later", transcript=TALK, start_at=False)
        self.round()
        late = self.item("rec-late")
        self.assertEqual((late["filing"], late["filing_detail"], late["start_known"]), ("needs_filing", "Plaud did not say when it began.", False))
        self.fake.recordings["rec-meeting"]["name"] = "Instructors meeting"
        self.round(later=600)
        self.assertEqual(self.item("rec-meeting")["plaud_title"], "Instructors meeting")
        with self.db() as db:
            last = db.execute("""SELECT * FROM recording_revisions WHERE recording_id=(SELECT id FROM recordings WHERE plaud_id='rec-meeting')
                                 ORDER BY revision DESC LIMIT 1""").fetchone()
        self.assertEqual((last["source"], bool(last["audio_file_id"]), bool(last["transcript_file_id"])), ("list", False, True))

    def test_recordings_are_append_only(self):
        self.meeting()
        self.round()
        with self.db() as db:
            for statement in ("UPDATE recordings SET plaud_id='x'", "DELETE FROM recordings", "UPDATE recording_revisions SET title='x'",
                              "DELETE FROM recording_revisions", "UPDATE recording_links SET state='withdrawn'", "DELETE FROM recording_links",
                              "UPDATE recording_files SET sha256='x'", "DELETE FROM recording_files", "DELETE FROM recording_bundles",
                              "UPDATE recording_bundles SET id='x'"):
                with self.subTest(statement=statement), self.assertRaises(sqlite3.IntegrityError):
                    db.execute(statement)
            # The one way out (migration 005) names ONE recording for the length of its erasure: while another is named, this
            # recording's own rows are retained as ever.
            db.execute("UPDATE recording_erasing SET recording_id='another-recording' WHERE id=1")
            for statement in ("DELETE FROM recordings", "DELETE FROM recording_revisions", "DELETE FROM recording_links"):
                with self.subTest(named=statement), self.assertRaises(sqlite3.IntegrityError):
                    db.execute(statement)
            db.execute("UPDATE recording_erasing SET recording_id=NULL WHERE id=1")
            for statement in ("DELETE FROM recording_files", "DELETE FROM recording_bundles", "DELETE FROM recording_lines",
                              "DELETE FROM recording_transcripts"):
                with self.subTest(cleared=statement), self.assertRaises(sqlite3.IntegrityError):
                    db.execute(statement)


class FilingTests(Base):
    """BY THE CALENDAR, WITHOUT A CARD, and by hand."""

    def add(self, plaud_id, day, clock):
        return self.fake.add(plaud_id, campus(day, clock), title=f"Recording {plaud_id}", transcript=TALK)

    def filed(self, plaud_id):
        row = self.item(plaud_id)
        return row["filing"], row["name"], row["filing_detail"]

    def test_inside_a_students_appointment_it_is_filed_under_that_student(self):
        meeting = self.appointment(ALICE, TUE, "14:00", "14:30")
        self.appointment(BOB, TUE, "15:00", "15:30", state="cancelled")
        self.add("in-grace", TUE, "13:52")      # eight minutes before the start
        self.add("too-early", TUE, "13:45")     # fifteen minutes before
        self.add("cancelled", TUE, "15:05")
        self.add("after", TUE, "14:30")         # it had ended
        self.round()
        self.assertEqual(self.filed("in-grace"), ("filed", "Example, Alice · MATH 140 · Fall 2026", ""))
        link, = self.links("in-grace")
        self.assertEqual((link["kind"], link["enrollment_id"], link["term_id"], link["appointment_id"], link["filed_by"], link["actor"]),
                         ("enrollment", self.students[ALICE], self.term, meeting["id"], "calendar", "automatic"))
        self.assertNotIn("Exam 2 questions", link["basis_json"])   # what the student came about is not copied
        for other in ("too-early", "after"):
            self.assertEqual(self.filed(other), ("needs_filing", "Needs filing", "No calendar entry at that time."), other)
        # Step 4: with nothing else on the calendar, a recording that began inside an appointment recorded as cancelled is
        # filed under that student and marked (tests/test_recordings_student_meetings.py).
        self.assertEqual(self.filed("cancelled"), ("filed", "Example, Bob · MATH 140 · Fall 2026", ""))
        self.assertEqual(self.item("cancelled")["links"][0]["appointment_off"], "cancelled")
        self.assertEqual(self.item("in-grace")["links"][0], {"kind": "student", "label": "Example, Alice · MATH 140 · Fall 2026",
                                                             "filed_by": "calendar", "appointment": True})
        # Filed by hand under the student it is already filed under: no change, and the appointment by reference stays.
        with self.db() as db:
            before = db.execute("SELECT COUNT(*) FROM recording_links").fetchone()[0]
        again = self.file("in-grace", kind="student", enrollment_id=self.students[ALICE])
        self.assertEqual((again.status_code, again.json["links"][0]["appointment"], again.json["links"][0]["filed_by"]), (200, True, "calendar"))
        with self.db() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM recording_links").fetchone()[0], before)
        self.assertEqual(self.links("in-grace")[0]["appointment_id"], meeting["id"])

    def test_inside_a_class_meeting_it_is_filed_under_that_sections_class_day(self):
        self.schedule({"kind": "class", "weekdays": [0, 2, 4], "start": "08:00", "end": "08:50", "room": "HELD 107", "section_id": self.sections[507]})
        self.event("MATH 140 · Section 508", TUE, "09:35", "10:50", scope="section", definition_id=self.definition, term_id=self.term,
                   section_id=self.sections[508])
        self.google.add("MATH 140 lecture", WED, "08:00", "08:50")   # the same term beside its class meeting: they agree
        self.add("class-507", WED, "07:55")
        self.add("class-508", TUE, "09:36")
        self.add("no-class", TUE, "08:05")      # section 507 does not meet on Tuesday
        self.round()
        self.assertEqual(self.filed("class-507"), ("filed", "Class · MATH 140 Section 507", ""))
        self.assertEqual(self.filed("class-508"), ("filed", "Class · MATH 140 Section 508", ""))
        self.assertEqual(self.filed("no-class")[0], "needs_filing")
        link, = self.links("class-507")
        self.assertEqual((link["kind"], link["section_id"], link["class_date"], link["term_id"], link["enrollment_id"]),
                         ("class_day", self.sections[507], WED, self.term, None))
        self.assertEqual(self.item("class-508")["links"][0]["date"], TUE)

    def test_inside_an_entry_naming_a_current_terms_course_it_is_filed_under_that_term(self):
        self.google.add("math140 coordinators", MON, "13:00", "14:00")
        self.event("Exam 3 writing session", TUE, "13:00", "14:00", scope="term", definition_id=self.definition, term_id=self.term)
        self.google.add("MATH 140 review day", WED, "", "", all_day=True)   # an all-day entry never counts
        self.add("by-title", MON, "13:10")
        self.add("by-owner", TUE, "12:55")
        self.add("all-day", WED, "11:00")
        self.round()
        self.assertEqual(self.filed("by-title"), ("filed", "MATH 140 · Fall 2026", ""))
        self.assertEqual(self.filed("by-owner"), ("filed", "MATH 140 · Fall 2026", ""))
        self.assertEqual(self.filed("all-day")[0], "needs_filing")
        link, = self.links("by-title")
        self.assertEqual((link["kind"], link["term_id"], json.loads(link["basis_json"])["entries"][0]["title"]),
                         ("term", self.term, "math140 coordinators"))

    def test_inside_an_entry_naming_no_course_of_facultys_it_is_not_kept_and_nothing_of_it_is_held(self):
        self.google.add("MATH 664 lecture", TUE, "12:40", "13:55")
        self.event("Dentist", WED, "09:00", "10:00")
        self.add("other-course", TUE, "12:45")
        self.add("errand", WED, "09:10")
        outcome = self.round()
        self.assertEqual(json.loads(outcome["counts_json"])["not_kept"], 2)
        self.assertEqual(self.not_kept(), {"other-course": ("not_kept", "calendar"), "errand": ("not_kept", "calendar")})
        self.assertEqual((self.held("other-course"), self.held("errand")), (False, False))
        page = self.listing()
        self.assertEqual((page["recordings"], page["attention"]["count"], page["not_kept_count"]), ([], 0, 2))
        # Nothing of either recording, nor of the entry that showed it to be nothing of Faculty's: no title, no time, no link.
        self.assertEqual({table: count for table, count in self.rows().items() if count}, {})
        with self.db() as db:
            held = " ".join(str(value) for row in db.execute("SELECT * FROM recording_not_kept") for value in tuple(row))
            columns = [row[1] for row in db.execute("PRAGMA table_info(recording_not_kept)")]
        for word in ("664", "Dentist", "Recording", "12:45", "09:10"):
            self.assertNotIn(word, held)
        # The row has no place for a title, a start or a length: Plaud's ID, who decided, and when they did.
        self.assertEqual(columns, ["plaud_id", "state", "decided_by", "request_id", "actor", "decided_at", "updated_at"])
        # Listed again by a later round, they are passed over: counted, and nothing of them recorded.
        outcome = self.round(later=600)
        self.assertEqual((json.loads(outcome["counts_json"])["not_kept"], json.loads(outcome["counts_json"])["new"]), (2, 0))
        self.assertEqual((self.held("other-course"), self.held("errand")), (False, False))

    def test_not_facultys_waits_while_google_cannot_vouch_and_an_away_entry_files_nothing(self):
        self.google.add("MATH 664 lecture", TUE, "12:40", "13:55")
        self.google.add("Unavailable", TUE, "15:00", "17:00")           # says where the instructor is not
        self.event("Out of office", WED, "09:00", "10:00")
        self.event("Dentist", WED, "13:00", "14:00")
        self.add("other-course", TUE, "12:45")
        self.add("away-google", TUE, "15:30")
        self.add("away-own", WED, "09:10")
        self.add("errand", WED, "13:05")
        self.google.gap = "Faculty has not read your Google Calendar since Mon, Nov 9 at 8:00 AM"
        self.round()
        unseen = ("needs_filing", "Needs filing", "The only calendar entry at that time names no course of yours, but Faculty has "
                                                  "not read your Google Calendar since Mon, Nov 9 at 8:00 AM.")
        self.assertEqual(self.filed("other-course"), unseen)
        self.assertEqual(self.filed("errand"), unseen)                  # Faculty's own entry too: Google may hold the meeting
        for plaud_id in ("away-google", "away-own"):
            self.assertEqual(self.filed(plaud_id)[0], "needs_filing", plaud_id)
            self.assertTrue(self.filed(plaud_id)[2].startswith("No calendar entry at that time."), plaud_id)
        self.assertEqual(self.listing()["attention"]["needs_filing"], 4)
        # Google's copy that cannot be read at all vouches for nothing either.
        self.google.gap, self.google.fail = None, True
        with self.assertLogs("recordings.service", "WARNING"):
            self.round(later=600)
        self.assertEqual(self.filed("errand"), ("needs_filing", "Needs filing", "The only calendar entry at that time names no course "
                                                "of yours, but Faculty could not read its copy of your Google Calendar."))
        # Read again, Google vouches: what names no course is not kept, and an away entry alone still files nothing.
        self.google.fail = False
        self.round(later=600)
        self.assertEqual(self.not_kept(), {"other-course": ("not_kept", "calendar"), "errand": ("not_kept", "calendar")})
        self.assertEqual((self.held("other-course"), self.held("errand")), (False, False))
        self.assertEqual(self.filed("away-google"), ("needs_filing", "Needs filing", "No calendar entry at that time."))
        self.assertEqual(self.filed("away-own"), ("needs_filing", "Needs filing", "No calendar entry at that time."))

    def test_home_asks_about_what_waits_for_keep_or_dont_keep_with_a_count_and_a_link_only(self):
        """where: "Home and sidebar too"."""
        def card():
            overview = self.client.get("/api/workspace/overview")
            self.assertEqual(overview.status_code, 200, overview.json)
            return [entry for entry in overview.json["attention"] if entry["key"] == "recordings:keep"], json.dumps(overview.json)

        self.assertEqual(card()[0], [])   # nothing waits: no card
        self.google.add("MATH 664 lecture", TUE, "12:40", "13:55")
        self.add("other-course", TUE, "12:45")   # not Faculty's by the calendar: not kept, and never asked about
        self.add("nothing", TUE, "20:00")
        self.add("later", WED, "20:00")
        self.round()
        (waiting,), shown = card()
        self.assertEqual({key: waiting[key] for key in ("kind", "label", "count", "href", "verb")},
                         {"kind": "review", "label": "Recordings to keep or not", "count": 2, "href": "#recordings", "verb": "Decide"})
        self.assertEqual(waiting["detail"], "Faculty could not tell whether 2 recordings belong to your teaching. Nothing of them is fetched until you say.")
        self.assertNotIn("Recording nothing", shown)   # a count and a link: never what Plaud calls one, or when it was
        # Kept, or not kept: it no longer asks.
        self.assertEqual(self.file("nothing", kind="memo").status_code, 200)
        (waiting,), _ = card()
        self.assertEqual((waiting["count"], waiting["detail"]),
                         (1, "Faculty could not tell whether 1 recording belongs to your teaching. Nothing of it is fetched until you say."))
        self.assertEqual(self.discard("later").status_code, 200)
        self.assertEqual(card()[0], [])

    def test_no_entry_or_two_that_disagree_wait_for_the_instructor(self):
        self.appointment(ALICE, TUE, "14:00", "14:30")
        self.appointment(BOB, TUE, "14:15", "14:45")
        self.schedule({"kind": "office_hours", "weekdays": [0], "start": "15:00", "end": "17:00", "room": "BLOC 309", "seats_makeup": False})
        self.appointment(CAROL, MON, "15:30", "16:00")
        self.add("two-students", TUE, "14:20")
        self.add("drop-in", MON, "16:20")       # office hours, nobody booked
        self.add("booked", MON, "15:31")        # office hours, Carol's appointment
        self.add("nothing", TUE, "20:00")
        self.google.gap = "Faculty has not read your Google Calendar since Mon, Nov 9 at 8:00 AM"
        self.round()
        self.assertEqual(self.filed("two-students"), ("needs_filing", "Needs filing",
                                                      "Two calendar entries at that time disagree: an appointment and an appointment."))
        self.assertEqual(self.filed("drop-in"), ("needs_filing", "Needs filing", "During office hours with no appointment. Pick the student."))
        self.assertEqual(self.filed("booked"), ("filed", "Example, Carol · MATH 140 · Fall 2026", ""))
        self.assertEqual(self.filed("nothing")[2], "No calendar entry at that time. Faculty has not read your Google Calendar since "
                                                   "Mon, Nov 9 at 8:00 AM.")
        # An entry that arrives later files it in a later round, while Plaud still lists it.
        self.google.gap = None
        self.google.add("MATH 140 evening review", TUE, "19:30", "21:00")
        self.round(later=600)
        self.assertEqual(self.filed("nothing"), ("filed", "MATH 140 · Fall 2026", ""))

    def test_the_rules_alone(self):
        terms = [{"id": "t140", "code": "MATH 140", "definition_id": "d140"}, {"id": "t151", "code": "MATH 151", "definition_id": "d151"}]
        self.assertEqual(filing.named_terms("Math-140 / MATH151 joint meeting, room 140", terms), ["t140", "t151"])
        self.assertEqual(filing.named_terms("MATH 1400, MATH 14, AMATH 140x, 140", terms), [])
        moment = datetime(2026, 11, 10, 10, 5)
        google = FakeGoogle()
        google.add("MATH 140 and MATH 151 coordinators", TUE, "10:00", "11:00")
        found = filing.decide(moment, google.rows, terms, {})
        self.assertEqual((found["target"], "more than one current term" in found["detail"]), (None, True))
        self.assertEqual(filing.campus(datetime(2026, 7, 1, 15, 0, tzinfo=timezone.utc)), datetime(2026, 7, 1, 10, 0))   # daylight time
        self.assertEqual(intake.instant("2026-11-10T16:02:11Z"), datetime(2026, 11, 10, 16, 2, 11, tzinfo=timezone.utc))
        self.assertEqual(intake.instant("2026-11-10T10:02:11-06:00"), datetime(2026, 11, 10, 16, 2, 11, tzinfo=timezone.utc))
        self.assertIsNone(intake.instant("yesterday"))

    def test_filing_by_hand(self):
        self.add("rec", TUE, "20:00")
        self.round()
        recording = self.item("rec")["id"]
        choices = self.listing()["choices"]
        self.assertEqual(([t["label"] for t in choices["terms"]], [c["label"] for c in choices["courses"]],
                          [s["label"] for s in choices["sections"]]),
                         (["MATH 140 · Fall 2026"], ["MATH 140"], ["MATH 140 Section 507 · Fall 2026", "MATH 140 Section 508 · Fall 2026"]))
        cases = [({"kind": "student", "enrollment_id": self.students[BOB]}, "Example, Bob · MATH 140 · Fall 2026", "enrollment"),
                 ({"kind": "term", "term_id": self.term}, "MATH 140 · Fall 2026", "term"),
                 ({"kind": "course", "definition_id": self.definition}, "MATH 140", "course"),
                 ({"kind": "class_day", "section_id": self.sections[508]}, "Class · MATH 140 Section 508", "class_day"),
                 ({"kind": "memo"}, "Memo", "memo")]
        for body, name, kind in cases:
            with self.subTest(kind=kind):
                answer = self.file("rec", **body)
                self.assertEqual((answer.status_code, answer.json["name"]), (200, name), answer.json)
                link, = self.links("rec")   # re-filing withdrew what it was filed under before
                self.assertEqual((link["kind"], link["filed_by"], link["actor"], link["state"]), (kind, "instructor", "workspace", "filed"))
        self.assertEqual(self.links("rec")[0]["class_date"], "")
        with self.db() as db:
            day = db.execute("SELECT class_date, state FROM recording_links WHERE kind='class_day' ORDER BY revision").fetchall()
            states = [tuple(r) for r in db.execute("SELECT kind, state FROM recording_links WHERE recording_id=? ORDER BY created_at, rowid", (recording,))]
        self.assertEqual([tuple(r) for r in day], [(TUE, "filed"), (TUE, "withdrawn")])   # the class day is the recording's campus day
        self.assertEqual(states[:3], [("enrollment", "filed"), ("enrollment", "withdrawn"), ("term", "filed")])
        # The same request again changes nothing; its ID on another recording, a missing ID and what is not there are refused.
        again = self.file("rec", request="one", kind="term", term_id=self.term)
        def count():
            with self.db() as db:
                return db.execute("SELECT COUNT(*) FROM recording_links").fetchone()[0]

        before = count()
        self.assertEqual(self.file("rec", request="one", kind="term", term_id=self.term).json, again.json)
        self.assertEqual(count(), before)
        self.add("second", TUE, "21:00")
        self.round(later=600)
        self.assertEqual(self.file("second", request="one", kind="memo").status_code, 409)
        route = f"/api/recordings/{recording}/file"
        self.assertEqual(self.client.post(route, headers=HEADERS, json={"kind": "memo"}).status_code, 400)
        self.assertEqual(self.client.post(route, headers=HEADERS, json={"request_id": "x1", "kind": "everyone"}).status_code, 400)
        self.assertEqual(self.client.post(route, headers=HEADERS, json={"request_id": "x2", "kind": "student", "enrollment_id": "0000000000"}).status_code, 400)
        self.assertEqual(self.client.post("/api/recordings/none/file", headers=HEADERS, json={"request_id": "x3", "kind": "memo"}).status_code, 404)
        self.assertEqual(self.client.post(route, json={"request_id": "x4", "kind": "memo"}).status_code, 403)   # same-origin writes only
        # Don't keep and Ask me again are writes like any other: same-origin only, with a request ID, and not for what is not there.
        self.assertEqual(self.client.post(f"/api/recordings/{recording}/discard", json={"request_id": "x5"}).status_code, 403)
        self.assertEqual(self.client.post(f"/api/recordings/{recording}/discard", headers=HEADERS, json={}).status_code, 400)
        self.assertEqual(self.client.post("/api/recordings/none/discard", headers=HEADERS, json={"request_id": "x6"}).status_code, 404)
        self.assertEqual(self.client.post("/api/recordings/ask-again", json={"request_id": "x7"}).status_code, 403)
        self.assertEqual(self.client.post("/api/recordings/ask-again", headers=HEADERS, json={}).status_code, 400)
        self.assertTrue(self.held("rec"))
        # A filing by hand is not undone by the calendar.
        self.google.add("MATH 664 lecture", TUE, "19:55", "21:00")
        self.round(later=600)
        self.assertEqual(self.item("rec")["name"], "MATH 140 · Fall 2026")


class StorageTests(CaptureTests):
    storage = True

    def test_what_is_kept_is_a_verified_bundle_backed_up_then_kept_on_the_hdd_and_checked_on_every_read(self):
        self.meeting()
        self.round()
        with self.db() as db:
            bundle = db.execute("SELECT * FROM recording_bundles").fetchone()
            files = {r["role"]: dict(r) for r in db.execute("SELECT * FROM recording_files")}
        self.assertEqual((bundle["storage_status"], sorted(files)), ("staged", ["notes", "transcript"]))   # no audio: it plays from the archive
        hot = self.drives.nvme / "recordings" / bundle["id"] / "data"
        self.assertEqual(sorted(p.name for p in hot.iterdir()), ["notes.json", "transcript.json"])
        self.assertIn(self.record.files.manager, self.drives.owner_managers())
        state = self.drives.backup_all()
        self.assertEqual(state["status"], "complete", state)
        with self.db() as db:
            after = db.execute("SELECT * FROM recording_bundles").fetchone()
        self.assertEqual(after["storage_status"], "archived")
        self.assertFalse(hot.exists())
        self.assertEqual(json.loads(self.record.files.read(files["transcript"])), TALK)
        self.assertEqual(self.drives.retention_plan()["snapshots"]["delete"], [])
        # A kept file changed on the HDD is refused, never served.
        snapshot = self.drives.hdd / "recordings" / bundle["id"] / "snapshots" / after["storage_snapshot"] / "data"
        os.chmod(snapshot / "transcript.json", 0o600)
        (snapshot / "transcript.json").write_bytes(b"tampered")
        with self.assertRaises(StorageError):
            self.record.files.read(files["transcript"])

    def test_files_a_drive_still_holds_are_said_on_the_page_and_removed_when_it_is_back(self):
        self.meeting()
        self.round()
        self.assertEqual(self.drives.backup_all()["status"], "complete")   # kept on the HDD alone now
        with self.db() as db:
            bundle = db.execute("SELECT id FROM recording_bundles").fetchone()[0]
        cold = self.drives.hdd / "recordings" / bundle
        self.assertTrue(cold.exists())
        with mock.patch.object(self.record.files.manager, "check_hdd", side_effect=StorageError("The expected hdd drive is not mounted.")), \
                self.assertLogs("recordings.service", "WARNING"):
            self.assertEqual(self.discard("rec-meeting").status_code, 200)
        # Its rows are gone and it is asked about no more. Its files are still on the drive that was away, and the page says
        # so with the engine's reason: "erased" is not yet true of them.
        self.assertFalse(self.held("rec-meeting"))
        self.assertEqual({table: count for table, count in self.rows().items() if count}, {"recording_erased_bundles": 1})
        self.assertTrue(cold.exists())
        waiting = self.listing()["erase_waiting"]
        self.assertEqual((waiting["count"], waiting["detail"]),
                         (1, "A drive is away and may still hold this recording's files. The expected hdd drive is not mounted."))
        self.round(later=600)   # the drive is back: the next round removes them
        self.assertFalse(cold.exists())
        self.assertEqual(self.listing()["erase_waiting"], {"count": 0, "detail": ""})
        self.assertEqual({table: count for table, count in self.rows().items() if count}, {})
        # Storage is whole without it: nothing to prune, and the next nightly backup is complete.
        self.assertEqual(self.drives.retention_plan()["snapshots"]["delete"], [])
        self.assertEqual(self.drives.backup_all()["status"], "complete")

    def test_what_is_on_the_drive_that_is_present_is_removed_at_once(self):
        self.meeting()
        self.round()
        with self.db() as db:
            bundle = db.execute("SELECT id FROM recording_bundles").fetchone()[0]
        hot = self.drives.nvme / "recordings" / bundle   # staged on NVMe: not backed up yet
        self.assertTrue(hot.exists())
        with mock.patch.object(self.record.files.manager, "check_hdd", side_effect=StorageError("The expected hdd drive is not mounted.")), \
                self.assertLogs("recordings.service", "WARNING"):
            self.assertEqual(self.discard("rec-meeting").status_code, 200)
        self.assertFalse(hot.exists())                                # gone from the drive that is here
        self.assertEqual(self.listing()["erase_waiting"]["count"], 1)   # the other could not be looked at, so it stays named
        self.round(later=600)
        self.assertEqual(self.listing()["erase_waiting"]["count"], 0)

    def test_nothing_is_removed_while_the_nightly_backup_runs(self):
        """The backup's first pass copies a bundle to the HDD under no lock of the bundle's: a folder removed meanwhile could
        be written back. It holds the nightly lock from start to end, and nothing is removed while it does."""
        self.meeting()
        self.round()
        with self.db() as db:
            bundle = db.execute("SELECT id FROM recording_bundles").fetchone()[0]
        hot = self.drives.nvme / "recordings" / bundle
        with self.drives.lock("nightly-backup"), self.assertLogs("recordings.service", "WARNING"):
            self.assertEqual(self.discard("rec-meeting").status_code, 200)
        self.assertFalse(self.held("rec-meeting"))
        self.assertTrue(hot.exists())
        self.assertEqual(self.listing()["erase_waiting"], {"count": 1, "detail": "The nightly backup is running. The files are removed once it has finished."})
        self.round(later=600)
        self.assertFalse(hot.exists())
        self.assertEqual(self.listing()["erase_waiting"]["count"], 0)
        self.assertEqual(self.drives.backup_all()["status"], "complete")

    def test_a_drive_that_is_away_waits_without_blaming_plaud_and_keeps_what_was_filed(self):
        self.meeting()
        with mock.patch.object(self.record.files, "stage_path", side_effect=StorageError("The expected nvme drive is not mounted.")), \
                self.assertLogs("recordings.service", "WARNING") as logs:
            outcome = self.round()
        self.assertEqual(outcome["status"], "waiting", outcome)
        self.assertIn("storage is not ready", outcome["detail"])
        self.assertFalse(any("Traceback" in line for line in logs.output))
        row = self.item("rec-meeting")
        self.assertEqual((row["filing"], row["kept"]), ("filed", []))
        self.assertEqual(self.round(later=600)["status"], "ok")
        self.assertEqual(self.item("rec-meeting")["kept"], ["transcript", "notes"])

    def test_an_interrupted_round_leaves_no_record_and_its_folder_is_swept(self):
        self.meeting()
        real, calls = self.record._revise, []

        def revise(*args, **kwargs):
            calls.append(args)
            if len(calls) == 2:   # the list row was recorded; the fetch's revision is where it stops
                raise sqlite3.OperationalError("interrupted")
            return real(*args, **kwargs)

        with mock.patch.object(self.record, "_revise", side_effect=revise), self.assertLogs("recordings.service", "ERROR"):
            self.round()
        with self.db() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM recording_bundles").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM recording_files").fetchone()[0], 0)
        left = list((self.drives.nvme / "recordings").iterdir())
        self.assertEqual(len(left), 1)
        self.record.files.sweep()
        self.assertEqual(len(list((self.drives.nvme / "recordings").iterdir())), 1)   # not an hour old yet
        old = time.time() - 2 * 3600
        os.utime(left[0], (old, old))
        self.record.files.sweep()
        self.assertEqual(list((self.drives.nvme / "recordings").iterdir()), [])
        # The next round keeps it after all.
        self.round(later=600)
        self.assertEqual(self.item("rec-meeting")["kept"], ["transcript", "notes"])


if __name__ == "__main__":
    unittest.main()
