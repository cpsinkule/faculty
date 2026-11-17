"""Recordings, step 2: the recording page (docs/decisions/recordings.md, RECORDINGS) offline, against the loopback stand-in of
the stand-in of the Plaud intake (tests/fake_intake.py): a kept transcript is read into lines when it is kept, a recording opens on them, its audio is the kept
file served with ranges, what was said is found by its words without anything being written, and a speaker named in Faculty is a
revision here that Plaud never hears of. The transcripts have the shape of Plaud's own (seen on real ones, structure only); the
people and their words are invented."""
import json
import os
import sqlite3
import unittest
from pathlib import Path
from unittest import mock

from fake_intake import OGG
from test_assistant import settle
from test_recordings import BOB, HEADERS, MON, TUE, WED, Base, campus, own_audio

from assistant import policy
from recordings import service as recording_service, transcript

SAID = [{"start_time": 500, "end_time": 3200, "speaker": "Chris", "original_speaker": "Speaker 1", "segment_uid": "u1",
         "content": "Calculators stay in the bag for Exam 3."},
        {"start_time": 3200, "end_time": 6100, "speaker": "Speaker 2", "original_speaker": "Speaker 2", "segment_uid": "u2",
         "content": "And the seating charts go out Thursday, 100% of them."},
        {"start_time": 6100, "end_time": 8800, "speaker": "Chris", "original_speaker": "Speaker 1", "segment_uid": "u3",
         "content": "Good. The makeup room is BLOC 166."}]
OUTLINE = [{"start_time": 500, "end_time": 6100, "topic": "Exam 3 rules"}, {"start_time": 6100, "end_time": 8800, "topic": "Rooms"}]
SUMMARY = [{"type": "auto_sum_note", "tab": "Summary", "content": "## Exam 3\n\nCalculators are not allowed."}]
TABLES = ("recordings", "recording_revisions", "recording_links", "recording_files", "recording_bundles", "recording_lines",
          "recording_transcripts")


class PageBase(Base):
    def kept(self, plaud_id="rec-meeting", **more):
        """A recording filed under the term and kept, with a transcript in Plaud's own shape."""
        self.meeting(plaud_id, **{"transcript": SAID, "outline": OUTLINE, "notes": SUMMARY, **more})
        self.assertEqual(self.round()["status"], "ok")
        return self.item(plaud_id)["id"]

    def one(self, recording, **query):
        answer = self.client.get(f"/api/recordings/{recording}", query_string=query)
        self.assertEqual(answer.status_code, 200, answer.json)
        return answer.json

    def search(self, query, **more):
        answer = self.client.post("/api/recordings/search", json={"query": query, **more}, headers=HEADERS)
        self.assertEqual(answer.status_code, 200, answer.json)
        return answer.json

    def name(self, recording, speaker, name, request="n1"):
        return self.client.post(f"/api/recordings/{recording}/speakers", headers=HEADERS,
                                json={"request_id": request, "speaker": speaker, "name": name})

    def counts(self):
        with self.db() as db:
            return {table: db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in TABLES}


class TranscriptTests(PageBase):
    def test_a_kept_transcript_is_read_into_lines_and_the_recording_opens_on_them(self):
        recording = self.kept()
        page = self.one(recording)
        self.assertEqual((page["id"], page["name"], page["date"], page["time"], page["filing"]),
                         (recording, "MATH 140 · Fall 2026", TUE, "10:02", "filed"))
        self.assertEqual(page["transcript"], {"state": "ready", "lines": 3})
        self.assertEqual([(l["line"], l["at"], l["start_ms"], l["end_ms"], l["speaker"], l["speaker_key"]) for l in page["lines"]],
                         [(0, "0:00", 500, 3200, "Chris", "Speaker 1"), (1, "0:03", 3200, 6100, "Speaker 2", "Speaker 2"),
                          (2, "0:06", 6100, 8800, "Chris", "Speaker 1")])
        self.assertEqual(page["lines"][1]["text"], "And the seating charts go out Thursday, 100% of them.")
        self.assertEqual(page["speakers"], [{"key": "Speaker 1", "plaud": "Chris", "name": None, "shown": "Chris", "lines": 2},
                                            {"key": "Speaker 2", "plaud": "Speaker 2", "name": None, "shown": "Speaker 2", "lines": 1}])
        self.assertEqual(page["audio"], {"type": "audio/ogg", "size": len(OGG), "href": f"/api/recordings/{recording}/audio"})
        # Plaud's own words come from the kept files, checked: its summary and its outline.
        self.assertEqual(page["notes"], [{"tab": "Summary", "content": "## Exam 3\n\nCalculators are not allowed."}])
        self.assertEqual(page["outline"], [{"start_ms": 500, "topic": "Exam 3 rules"}, {"start_ms": 6100, "topic": "Rooms"}])
        # One stretch of it, for a reader of one moment: the lines that touch it, and neither Plaud's notes nor its outline.
        part = self.one(recording, start_ms=3000, end_ms=6000)
        self.assertEqual(([l["line"] for l in part["lines"]], part["window"], "notes" in part, "outline" in part),
                         ([0, 1], {"start_ms": 3000, "end_ms": 6000}, False, False))
        self.assertEqual([l["line"] for l in self.one(recording, start_ms=6100)["lines"]], [1, 2])
        self.assertEqual(self.client.get(f"/api/recordings/{recording}?start_ms=soon").status_code, 400)
        self.assertEqual(self.client.get("/api/recordings/none").status_code, 404)
        # The lines are a copy of the kept file, read once, and never change.
        with self.db() as db:
            read = db.execute("SELECT * FROM recording_transcripts").fetchone()
            self.assertEqual((read["lines"], read["readable"], db.execute("SELECT COUNT(*) FROM recording_lines").fetchone()[0]), (3, 1, 3))
            for statement in ("UPDATE recording_lines SET text='x'", "DELETE FROM recording_lines", "UPDATE recording_transcripts SET lines=0",
                              "DELETE FROM recording_transcripts"):
                with self.subTest(statement=statement), self.assertRaises(sqlite3.IntegrityError):
                    db.execute(statement)
        # Opening it asked the intake only where its audio is: nothing of a recording was read from the archive again.
        self.fake.clear()
        self.one(recording)
        self.assertEqual(self.fake.asked(), [("audio", "rec-meeting")])

    def test_a_recording_with_nothing_kept_and_a_transcript_faculty_cannot_read_say_so(self):
        self.fake.add("rec-unfiled", campus(TUE, "20:00"), title="Evening", transcript=SAID)
        self.meeting(transcript={"version": 2, "words": "not a list of lines"})
        self.round()
        unfiled = self.one(self.item("rec-unfiled")["id"])
        self.assertEqual((unfiled["transcript"], unfiled["lines"], unfiled["speakers"], unfiled["audio"], unfiled["notes"]),
                         ({"state": "none", "lines": 0}, [], [], None, []))
        odd = self.one(self.item("rec-meeting")["id"])
        self.assertEqual((odd["transcript"], odd["lines"], odd["kept"]), ({"state": "unreadable", "lines": 0}, [], ["transcript", "notes"]))

    def test_the_shapes_alone(self):
        readable, lines = transcript.lines(json.dumps(SAID + [{"start_time": 9000, "content": "   "}, {"content": "no time"},
                                                               {"start_time": 9500, "end_time": 9000, "speaker": "", "content": "ends  before\nit starts"}]))
        self.assertEqual((readable, len(lines), lines[3]), (True, 4, {"position": 3, "start_ms": 9500, "end_ms": 9500, "speaker": "",
                                                                      "speaker_key": "", "text": "ends before it starts"}))
        self.assertEqual(transcript.lines(b"[]"), (True, []))
        for odd in (b"{}", b"not json", b'["a line"]', json.dumps([{"start_time": "soon", "content": "x"}]).encode(), b"\xff\xfe"):
            self.assertEqual(transcript.lines(odd), (False, []), odd)
        self.assertEqual(transcript.words('  "Calculators,"  calculators Exam   3?  '), ["calculators", "exam", "3"])
        self.assertEqual(len(transcript.words(" ".join(f"w{n}" for n in range(20)))), transcript.MAX_WORDS)
        self.assertEqual(transcript.like("100%_a\\b"), "%100\\%\\_a\\\\b%")
        self.assertEqual([transcript.offset(ms) for ms in (0, 59999, 754000, 3723000)], ["0:00", "0:59", "12:34", "1:02:03"])
        self.assertEqual(transcript.outline(b'{"topic": "x"}'), [])
        self.assertEqual(transcript.notes(b'[{"tab": "", "content": "words"}, {"tab": "Empty", "content": " "}, "odd"]'), [{"tab": "Notes", "content": "words"}])

    def test_a_transcript_kept_before_its_lines_were_read_is_read_by_a_later_round_from_storage(self):
        # What step 1 kept on the live machine: the file, and no lines yet.
        with mock.patch.object(self.record, "_read"):
            recording = self.kept()
        self.assertEqual(self.one(recording)["transcript"], {"state": "waiting", "lines": 0})
        self.assertEqual(self.search("calculators")["results"], [])
        if self.storage:   # the nightly backup has moved the bundle to the HDD since: it is read from there, checked
            self.assertEqual(self.drives.backup_all()["status"], "complete")
        self.fake.clear()
        self.round(later=600)
        self.assertEqual(self.one(recording)["transcript"], {"state": "ready", "lines": 3})
        self.assertEqual(len(self.search("calculators")["results"]), 1)
        self.assertEqual({command for command, _ in self.fake.asked()} - {"audio"}, {"status", "list"})   # read from storage, not from the archive
        # It is read whatever the intake says: a round that says "Sign in to Plaud again" still reads what Faculty already holds.
        with mock.patch.object(self.record, "_read"):
            self.fake.add("rec-two", campus(WED, "09:00"), title="Second", transcript=SAID)
            self.google.add("MATH 140 review", WED, "08:55", "10:00")
            self.round(later=600)
        self.fake.sign_in_again()
        self.assertEqual(self.round(later=600)["status"], "sign_in")
        self.assertEqual(self.one(self.item("rec-two")["id"])["transcript"], {"state": "ready", "lines": 3})


class AudioTests(PageBase):
    def test_the_audio_is_the_archives_file_with_ranges_no_store_and_nosniff(self):
        recording = self.kept()
        if self.storage:
            self.assertEqual(self.drives.backup_all()["status"], "complete")
        route = f"/api/recordings/{recording}/audio"
        whole = self.client.get(route)
        self.assertEqual((whole.status_code, whole.data, whole.mimetype), (200, OGG, "audio/ogg"))
        self.assertEqual((whole.headers["Cache-Control"], whole.headers["X-Content-Type-Options"], whole.headers["Accept-Ranges"],
                          whole.headers["Content-Disposition"]), ("no-store", "nosniff", "bytes", "inline"))
        self.assertNotIn("sandbox", whole.headers["Content-Security-Policy"])   # the page's own player may play it
        for asked, status, expected, said in (("bytes=0-99", 206, OGG[:100], f"bytes 0-99/{len(OGG)}"),
                                              ("bytes=1000-", 206, OGG[1000:], f"bytes 1000-{len(OGG) - 1}/{len(OGG)}"),
                                              ("bytes=-10", 206, OGG[-10:], f"bytes {len(OGG) - 10}-{len(OGG) - 1}/{len(OGG)}")):
            with self.subTest(asked=asked):
                part = self.client.get(route, headers={"Range": asked})
                self.assertEqual((part.status_code, part.data, part.headers["Content-Range"], part.headers["Cache-Control"]),
                                 (status, expected, said, "no-store"))
        self.assertEqual(self.client.get(route, headers={"Range": f"bytes={len(OGG) + 5}-"}).status_code, 416)
        self.assertEqual(self.client.post(route, json={}, headers=HEADERS).status_code, 405)
        self.assertEqual(self.client.get("/api/recordings/none/audio").status_code, 404)
        # It is never read as data nor fetched as a file by the Assistant, which answers with the page and the moment.
        self.assertIn("recordings.audio", policy.GET_BLOCKED)
        self.assertIn("recordings.audio", policy.FILE_BLOCKED)
        # Playing it asked the intake where the file is, and nothing of Plaud; the file is read from the archive and checked.
        self.fake.clear()
        self.client.get(route, headers={"Range": "bytes=0-9"})
        self.assertEqual(self.fake.asked(), [("status", None), ("audio", "rec-meeting")])

    def test_a_recording_with_no_audio_in_the_archive_answers_404_and_a_changed_file_is_refused(self):
        self.fake.add("rec-silent", campus(WED, "09:00"), title="No audio yet", transcript=SAID, audio=None)
        self.google.add("MATH 140 review", WED, "08:55", "10:00")
        recording = self.kept()
        silent = self.client.get(f"/api/recordings/{self.item('rec-silent')['id']}/audio")
        self.assertEqual(silent.status_code, 404)
        self.assertIn("The Plaud archive has no audio of this recording", silent.json["error"])
        self.assertEqual(self.client.get(f"/api/recordings/{self.item('rec-silent')['id']}").json["audio"], None)
        # The archive's file changed since the intake gave its checksum: never served.
        self.fake.corrupt("rec-meeting", "audio")
        self.assertEqual(self.client.get(f"/api/recordings/{recording}/audio").status_code, 404)
        # Faculty's own older file (kept before audio played from the archive) is checked on every read too: changed, it is refused.
        own_audio(self, "rec-meeting", OGG[:-1] + b"?")
        self.assertEqual(self.client.get(f"/api/recordings/{recording}/audio").data, OGG[:-1] + b"?")
        with self.db() as db:
            bundle = db.execute("SELECT bundle_id FROM recording_files WHERE role='audio'").fetchone()[0]
        root = self.drives.nvme / "recordings" if self.storage else Path(self.app.config["DATABASE"]).parent / "recordings"
        kept = root / bundle / "data" / "audio.ogg"
        os.chmod(kept, 0o600)
        kept.write_bytes(OGG[:-1] + b"!")
        self.assertEqual(self.client.get(f"/api/recordings/{recording}/audio").status_code, 503)   # never served unchecked


class SearchTests(PageBase):
    def test_what_was_said_is_found_by_its_words_and_a_search_writes_nothing(self):
        first = self.kept()
        self.google.add("MATH 140 review", WED, "08:55", "10:00")
        self.fake.add("rec-review", campus(WED, "09:00"), title="Review", transcript=[
            {"start_time": 754000, "end_time": 760000, "speaker": "Chris", "original_speaker": "Speaker 1", "content": "No calculators on the review day either."}])
        self.google.add("MATH 664 lecture", MON, "12:40", "13:55")
        self.fake.add("rec-other", campus(MON, "12:45"), title="Lecture", transcript=SAID)
        self.round(later=600)
        second = self.item("rec-review")["id"]
        before = self.counts()
        self.fake.clear()
        found = self.search("Calculators")
        self.assertEqual((found["query"], found["words"], found["more"]), ("calculators", ["calculators"], False))
        self.assertEqual([(r["recording_id"], r["name"], r["date"], r["time"], r["line"], r["at"], r["start_ms"], r["speaker"]) for r in found["results"]],
                         [(second, "MATH 140 · Fall 2026", WED, "09:00", 0, "12:34", 754000, "Chris"),      # the newest recording first
                          (first, "MATH 140 · Fall 2026", TUE, "10:02", 0, "0:00", 500, "Chris")])
        self.assertEqual(found["results"][0]["href"], f"#recordings/{second}?t=754000")
        # Every word must be in the line, whatever its case; the line before and after come with it.
        hit, = self.search("SEATING charts thursday")["results"]
        self.assertEqual((hit["line"], hit["text"], hit["before"]["text"], hit["before"]["at"], hit["after"]["speaker"], hit["after"]["start_ms"]),
                         (1, "And the seating charts go out Thursday, 100% of them.", "Calculators stay in the bag for Exam 3.", "0:00", "Chris", 6100))
        self.assertEqual(self.search("seating calculators")["results"], [])
        # What was typed means itself: a % or an _ is not "anything".
        self.assertEqual(len(self.search("100%")["results"]), 1)
        self.assertEqual(self.search("1%0")["results"], [])
        self.assertEqual(self.search("BLOC_166")["results"], [])
        # One recording alone, and no more lines than asked for.
        self.assertEqual([r["recording_id"] for r in self.search("calculators", recording_id=first)["results"]], [first])
        few = self.search("calculators", limit=1)
        self.assertEqual((len(few["results"]), few["more"]), (1, True))
        for body in ({"query": "  "}, {"query": "?!"}, {"query": "x", "limit": 0}, {"query": "x", "limit": 1000}, {"query": "x", "recording_id": 7}, {}):
            self.assertEqual(self.client.post("/api/recordings/search", json=body, headers=HEADERS).status_code, 400, body)
        # It wrote nothing, and asked the intake nothing. A recording that is not Faculty's is not searched: nothing of it is held.
        self.assertEqual((self.counts(), self.fake.asked()), (before, []))
        self.assertEqual((self.held("rec-other"), self.not_kept()), (False, {"rec-other": ("not_kept", "calendar")}))
        # The Assistant runs it as a read; it is the app's own route, same-origin like every write.
        self.assertEqual(policy.base_rule("recordings.search").tier, "read")
        self.assertEqual(self.client.post("/api/recordings/search", json={"query": "calculators"}).status_code, 403)

    def test_only_what_is_facultys_now_and_only_the_latest_transcript_is_searched(self):
        recording = self.kept()
        self.assertEqual(len(self.search("thursday")["results"]), 1)
        # Plaud changes the transcript: the words it no longer holds are not found, the new ones are.
        self.fake.recordings["rec-meeting"]["transcript"] = [SAID[0], {**SAID[1], "content": "And the seating charts go out Friday."}, SAID[2]]
        self.round(later=3700)
        self.assertEqual(self.search("thursday")["results"], [])
        self.assertEqual([r["line"] for r in self.search("friday")["results"]], [1])
        with self.db() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM recording_lines").fetchone()[0], 6)   # the earlier file's lines stay
        # Don't keep: erased with both transcripts' lines, no longer searched, and its page is gone.
        self.assertEqual(self.discard("rec-meeting").status_code, 200)
        self.assertEqual(self.search("friday")["results"], [])
        with self.db() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM recording_lines").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM recording_transcripts").fetchone()[0], 0)
        self.assertEqual(self.client.get(f"/api/recordings/{recording}").status_code, 404)


class SpeakerTests(PageBase):
    def test_a_speaker_named_in_faculty_is_a_revision_kept_by_plauds_first_label_and_never_sent_to_plaud(self):
        recording = self.kept()
        self.fake.clear()
        named = self.name(recording, "Speaker 2", "  Avery   Quill ")
        self.assertEqual(named.status_code, 200, named.json)
        self.assertEqual(named.json["speakers"][1], {"key": "Speaker 2", "plaud": "Speaker 2", "name": "Avery Quill", "shown": "Avery Quill", "lines": 1})
        self.assertEqual([l["speaker"] for l in named.json["lines"]], ["Chris", "Avery Quill", "Chris"])
        self.assertEqual(self.search("seating")["results"][0]["speaker"], "Avery Quill")
        self.assertEqual(self.search("calculators")["results"][0]["speaker"], "Chris")
        with self.db() as db:
            last = dict(db.execute("SELECT * FROM recording_revisions ORDER BY revision DESC LIMIT 1").fetchone())
            count = db.execute("SELECT COUNT(*) FROM recording_revisions").fetchone()[0]
            earlier = [tuple(r) for r in db.execute("SELECT source, actor, request_id FROM recording_revisions ORDER BY revision LIMIT 2")]
        self.assertEqual((last["source"], last["actor"], last["request_id"], json.loads(last["speakers_json"])),
                         ("instructor", "workspace", "n1", {"Speaker 2": "Avery Quill"}))
        self.assertTrue(last["transcript_file_id"] and not last["audio_file_id"])   # what is kept is carried forward; no audio is ever copied
        self.assertEqual(earlier, [("list", "automatic", ""), ("fetch", "automatic", "")])
        # The same request again changes nothing; the same name under a new request writes no revision either.
        self.assertEqual(self.name(recording, "Speaker 2", "Someone Else").json["speakers"][1]["name"], "Avery Quill")
        self.assertEqual(self.name(recording, "Speaker 2", "Avery Quill", request="n2").status_code, 200)
        with self.db() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM recording_revisions").fetchone()[0], count)
        # What is refused.
        self.fake.add("rec-two", campus(WED, "09:00"), title="Second", transcript=SAID)
        self.google.add("MATH 140 review", WED, "08:55", "10:00")
        self.round(later=600)
        other = self.item("rec-two")["id"]
        self.assertEqual(self.name(other, "Speaker 2", "Avery Quill").status_code, 409)             # its request ID named another recording's
        self.assertEqual(self.name(recording, "Speaker 9", "Nobody", request="n3").status_code, 400)
        self.assertEqual(self.name(recording, "Speaker 2", "x" * 101, request="n4").status_code, 400)
        self.assertEqual(self.client.post(f"/api/recordings/{recording}/speakers", headers=HEADERS, json={"speaker": "Speaker 2", "name": "A"}).status_code, 400)
        self.assertEqual(self.client.post(f"/api/recordings/{recording}/speakers", json={"request_id": "n5", "speaker": "Speaker 2", "name": "A"}).status_code, 403)
        self.assertEqual(self.name("none", "Speaker 2", "A", request="n6").status_code, 404)
        self.assertEqual(self.one(other)["speakers"][1]["shown"], "Speaker 2")                      # a name is one recording's
        # Plaud names that voice itself later: the name given here stays, kept by Plaud's first label; taken back, Plaud's shows.
        self.fake.recordings["rec-meeting"]["transcript"] = [SAID[0], {**SAID[1], "speaker": "Dana"}, SAID[2]]
        self.round(later=3700)
        page = self.one(recording)
        self.assertEqual(page["speakers"][1], {"key": "Speaker 2", "plaud": "Dana", "name": "Avery Quill", "shown": "Avery Quill", "lines": 1})
        self.assertEqual(self.name(recording, "Speaker 2", "", request="n7").json["speakers"][1], {"key": "Speaker 2", "plaud": "Dana", "name": None,
                                                                                                    "shown": "Dana", "lines": 1})
        # No name went anywhere: the intake was only asked what it holds, and the five commands are all that Faculty ever runs.
        self.assertTrue({command for command, _ in self.fake.asked()} <= {"list", "show", "audio", "status", "check-now"})
        # By the Assistant it is an Approve card, with the request ID Faculty puts there; so is filing.
        self.assertEqual(policy.base_rule("recordings.speakers").tier, "approve")
        self.assertTrue(policy.base_rule("recordings.speakers").request_id and policy.base_rule("recordings.file").request_id)

    def test_a_recording_filed_under_a_student_opens_and_is_found_like_any_other(self):
        recording = self.kept()
        self.assertEqual(self.file("rec-meeting", kind="student", enrollment_id=self.students[BOB]).status_code, 200)
        self.assertEqual(self.one(recording)["name"], "Example, Bob · MATH 140 · Fall 2026")
        hit, = self.search("calculators")["results"]
        self.assertEqual((hit["name"], hit["href"]), ("Example, Bob · MATH 140 · Fall 2026", f"#recordings/{recording}?t=500"))   # no student in the address


class AssistantTests(PageBase):
    """THE READ TOOL (ASSISTANT "THE TIERS"): through the Assistant's own tools, no model."""

    def setUp(self):
        super().setUp()
        self.addCleanup(settle)
        self.assistant = self.app.extensions["assistant"]
        self.session = self.assistant.open_session("test")

    def tool(self, name, **args):
        text, _ = self.assistant.run_tool(self.session, name, args)
        return json.loads(text)

    def test_the_assistant_finds_what_was_said_at_once_reads_a_stretch_and_cannot_take_the_audio(self):
        recording = self.kept()
        before = self.counts()
        found = self.tool("faculty_write", method="POST", path="/api/recordings/search", body={"query": "calculators"}, summary="Find what was said")
        self.assertEqual((found["outcome"], found["status"]), ("done", 200), found)   # a read: it ran at once, no card
        hit, = found["result"]["results"]
        self.assertEqual((hit["name"], hit["date"], hit["speaker"], hit["at"], hit["text"], hit["href"]),
                         ("MATH 140 · Fall 2026", TUE, "Chris", "0:00", "Calculators stay in the bag for Exam 3.", f"#recordings/{recording}?t=500"))
        self.assertEqual(hit["after"]["text"], "And the seating charts go out Thursday, 100% of them.")
        part = self.tool("faculty_get", path=f"/api/recordings/{recording}?start_ms=3000&end_ms=6000")
        self.assertEqual([(line["at"], line["speaker"]) for line in part["lines"]], [("0:00", "Chris"), ("0:03", "Speaker 2")])
        self.assertNotIn("notes", part)
        # The audio is neither read as data nor fetched as a file.
        self.assertIn("does not read this route", self.tool("faculty_get", path=f"/api/recordings/{recording}/audio")["error"])
        self.assertIn("does not fetch this route", self.tool("fetch_files", paths=[f"/api/recordings/{recording}/audio"])["files"][0]["error"])
        # Naming a speaker and filing are the instructor's: an Approve card, and nothing done.
        named = self.tool("faculty_write", method="POST", path=f"/api/recordings/{recording}/speakers",
                          body={"speaker": "Speaker 2", "name": "Avery Quill"}, summary="Name a speaker")
        filed = self.tool("faculty_write", method="POST", path=f"/api/recordings/{recording}/file", body={"kind": "memo"}, summary="File it as a memo")
        self.assertEqual((named["outcome"], filed["outcome"]), ("waiting_for_approval", "waiting_for_approval"))
        self.assertEqual(self.counts(), before)
        self.assertEqual(self.one(recording)["speakers"][1]["shown"], "Speaker 2")
        # The guide tells it where to look and how to answer.
        guide = (Path(policy.__file__).parent / "guide.md").read_text()
        self.assertIn("POST /api/recordings/search", guide)
        self.assertIn("`#recordings/<id>?t=<milliseconds from its start>`", guide)


class StoragePageTests(TranscriptTests, AudioTests):
    storage = True


if __name__ == "__main__":
    unittest.main()
