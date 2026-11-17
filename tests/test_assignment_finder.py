"""A scan without labels finds its assignment (requirement, 2026-09-27): papers by their blank template's look AND printed title,
bubble sheets by the one exam on the calendar. Synthetic pages; the local OCR runtime and the templates' files are scripted."""
import io
import json
from pathlib import Path
import random
import uuid
from datetime import datetime, timezone
from unittest.mock import patch
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw

from rosters import connect
from teach_inbox.service import sha
from document_identity import assignment_finder
from bubble_fixture import other_page, pdf, sheet
import test_unlabeled_exams
from test_unlabeled_exams import HEADERS, cover_text
import unittest

QUIZ_TITLE = "MATH 140 Fall 2026 Chapter 2 Quiz"
TEST_TITLE = "Common Exam I, September 17th, Fall 2026"


def blank(seed, width=1700, height=2200):
    """A blank first page: blocks of print in its top half, placed by `seed`, so two assignments' blanks look different."""
    image = Image.new("L", (width, height), 255)
    draw, rng = ImageDraw.Draw(image), random.Random(seed)
    for _ in range(14):
        x, y = rng.randrange(100, width - 500), rng.randrange(80, height // 2 - 120)
        draw.rectangle((x, y, x + rng.randrange(150, 450), y + rng.randrange(20, 90)), fill=0)
    return image


def written(image, text):
    """The blank as a student hands it in: a name written at the top."""
    image = image.copy()
    ImageDraw.Draw(image).text((200, 60), text, fill=40)
    return image


def png(image):
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


class AssignmentFinderTests(unittest.TestCase):
    def test_template_ocr_cache_is_bounded_and_keeps_recently_used_entries(self):
        finder = self.scans.assignment_finder
        read = {'pages': [{'reads': [{'kind': 'band', 'rotation': 0, 'text': TEST_TITLE}]}]}
        with patch.object(self.app.extensions['frq_grading'].templates, 'read', return_value=(b'', 'text/plain')), \
                patch('faculty_ocr.recognize_covers', return_value=read) as ocr:
            for number in range(129):
                self.assertEqual(finder._template_titles(str(number)), [TEST_TITLE.lower()])
            finder._template_titles('1')
            finder._template_titles('129')
            self.assertEqual(ocr.call_count, 130)
            self.assertEqual(len(finder._template_text), 128)
            self.assertIn('1', finder._template_text)
            self.assertNotIn('2', finder._template_text)
            finder._template_titles('2')
            self.assertEqual(ocr.call_count, 131)

    # The unlabeled-exam fixture's course, roster, Test 1 and its exam; imported by module so its own tests are not collected here.
    setUp = test_unlabeled_exams.UnlabeledExams.setUp
    submit = test_unlabeled_exams.UnlabeledExams.submit
    page_index = test_unlabeled_exams.UnlabeledExams.page_index  # Scan pages count from 1.

    def with_templates(self):
        """Test 1 (a scanned blank: no text layer, its title read by OCR) and Quiz 2 (a digital blank with its text), each one template."""
        self.quiz = self.client.post("/api/documents/assignments", json={"definition_id": self.client.get("/api/state").json["course_definitions"][0]["id"],
                                                                         "key": "quiz-2", "title": "Quiz 2"}, headers=HEADERS).json
        self.images = {"test": blank(1), "quiz": blank(2)}
        self.files, self.templates = {}, {}
        with connect(self.path) as db:
            for key, assignment, pages, text in (("test", self.assignment["id"], 3, ""),
                                                 ("quiz", self.quiz["id"], 2, f"Name: UIN:\n{QUIZ_TITLE}\nSections 2.1-2.4 Version A 10 points\nDirections: PRINT your name")):
                template = str(uuid.uuid4())
                self.templates[template] = key
                self.files[(template, "image")] = png(self.images[key])
                self.files[(template, "text")] = text.encode()
                db.execute("""INSERT INTO assignment_templates(id,assignment_id,label,request_id,request_sha256,page_count,source_format,
                              source_names_json,manifest_path,manifest_sha256,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                           (template, assignment, "Version A", str(uuid.uuid4()), "0" * 64, pages, "pdf", "[]", "x", "0" * 64, "2026-09-26T00:00:00+00:00"))
        self.template_shas = {sha(self.files[(template, "image")]): key for template, key in self.templates.items()}

    def read_template(self, identifier, kind, page=None):
        self.assertEqual(page, 1)
        return self.files[(identifier, kind)], "image/png" if kind == "image" else "text/plain"

    def covers(self, pages, title_box, words, title_unless=None):
        self.assertEqual((list(title_box), list(words), title_unless), ([.12, .17, .88, .36], ["name", "UIN"], "(?s)."))
        self.read.append(len(pages))
        result = []
        for content in pages:
            key = self.template_shas.get(sha(content))
            text = f"Math 140\n{TEST_TITLE}\n\nFirst and Last Name (printed):\nUIN:" if key == "test" else self.texts.get(self.page_index(content), "")
            result.append({"reads": [{"kind": "band", "rotation": 0, "text": text}], "source_sha256": sha(content)})
        return {"pages": result, "model_id": "fixture"}

    def find(self, message):
        self.read = []
        finder = self.scans.assignment_finder
        with patch.object(self.app.extensions["frq_grading"].templates, "read", side_effect=self.read_template), \
                patch("faculty_ocr.recognize_covers", side_effect=self.covers), patch("faculty_ocr.available", return_value=True):
            self.assertTrue(finder.process_one())
            self.assertFalse(finder.process_one())  # Each scan is looked at once.
        with connect(self.path) as db:
            job = db.execute("SELECT id FROM inbox_jobs WHERE message_id=?", (message,)).fetchone()[0]
            return finder.latest(db, job)

    def test_papers_whose_first_pages_look_like_one_blank_and_print_its_title_are_filed_under_it(self):
        self.with_templates()
        people = (("Alice Example", "123456789"), ("Bob Example", "223456780"), ("Carol Nguyen", "987650001"))
        pages = []
        for name, _ in people:
            pages += [written(self.images["quiz"], name), other_page(f"back {name}")]
        message = self.submit(pdf(pages))["message_id"]
        self.texts = {2 * index + 1: f"Name: {name}\nUIN: {uin}\n{QUIZ_TITLE}\nSections 2.1-2.4 Version B 10 points" for index, (name, uin) in enumerate(people)}
        found = self.find(message)
        self.assertIsNotNone(found["chosen"], found)
        self.assertEqual(found["chosen"]["title"], "Quiz 2", found)
        self.assertEqual((found["chosen"]["basis"], found["chosen"]["agreeing"]), ("template", 3))
        self.assertEqual(self.read, [1, 3])  # Test 1's scanned blank is read once for its title, then the three first pages at once.
        # The ordinary association, by the worker, over the term's sections; then the first pages are looked for at the blank's length.
        state = self.client.get(f"/api/grading/scans/{message}").json
        self.assertEqual(state["plan"]["title"], "Quiz 2")
        with connect(self.path) as db:
            self.assertEqual(db.execute("SELECT source FROM frq_scan_links WHERE message_id=?", (message,)).fetchone()[0], "automatic")
            run = db.execute("SELECT sides,register_confident,actor FROM scan_unlabeled_runs WHERE message_id=?", (message,)).fetchone()
            self.assertEqual((run["sides"], run["register_confident"]), (2, 1))
            self.assertEqual(db.execute("SELECT instructor_review_required FROM inbox_messages WHERE id=?", (message,)).fetchone()[0], 1)
        info = [step for step in self.scans.review_item(message)["overview"]["steps"] if step["kind"] == "info" and "chosen automatically" in step["text"]]
        self.assertEqual(len(info), 1)
        self.assertIn("Quiz 2 was chosen automatically: 3 of its first pages look like that assignment's blank template and print its title", info[0]["text"])

    def test_an_assignment_set_up_before_its_papers_is_found_and_its_plan_reused(self):
        """Set up grading now (requirement, 2026-10-02) imports the registered PDF as the assignment's blank template, so the finder can
        match an unlabeled scan of it, and the automatic association reuses the exam record and plan the instructor set up."""
        definition = self.client.get("/api/state").json["course_definitions"][0]["id"]
        quiz = self.client.post("/api/documents/assignments", json={"definition_id": definition, "key": "quiz-2", "title": "Quiz 2"}, headers=HEADERS).json
        self.images = {"quiz": blank(2)}
        fixtures = Path(__file__).resolve().parents[1] / "document_identity/fixtures"  # a PDF in Faculty's page layout (the conversion is scripted)
        version = self.documents.register_version(quiz["id"], "A", (fixtures / "letter.pdf").read_bytes(), {"main.tex": (fixtures / "letter.tex").read_bytes()},
                                                  {"fixture": "letter"})
        page = {"number": 1, "width_pt": 612, "height_pt": 792, "width_px": 1700, "height_px": 2200, "png": png(self.images["quiz"]),
                "text": f"Name: UIN:\n{QUIZ_TITLE}\nSections 2.1-2.4 Version A 10 points\nDirections: PRINT your name"}
        prepared = lambda files, entrypoint=None: {"pdf": b"%PDF-reference", "sources": files, "conversion": {"engine": "fixture"}, "pages": [page]}
        with patch("document_identity.template_conversion.convert_template", side_effect=prepared):
            response = self.client.post("/api/grading/assignments", json={"assignment_id": quiz["id"], "term_id": self.section["term_id"]}, headers=HEADERS)
        self.assertEqual(response.status_code, 201, response.json)
        plan = response.json
        self.assertTrue(plan["entry"]["ready"])
        [imported] = plan["set_up"]["templates"]
        self.assertEqual(imported["version_id"], version["id"])
        self.assertEqual([t["source_version_id"] for t in plan["templates"]], [version["id"]])
        self.files = {(imported["template_id"], "image"): page["png"], (imported["template_id"], "text"): page["text"].encode()}
        self.template_shas = {}
        people = (("Alice Example", "123456789"), ("Bob Example", "223456780"), ("Carol Nguyen", "987650001"))
        pages = []
        for name, _ in people:
            pages += [written(self.images["quiz"], name), other_page(f"back {name}")]
        message = self.submit(pdf(pages))["message_id"]
        self.texts = {2 * index + 1: f"Name: {name}\nUIN: {uin}\n{QUIZ_TITLE}\nSections 2.1-2.4 Version B 10 points" for index, (name, uin) in enumerate(people)}
        found = self.find(message)
        self.assertEqual((found["chosen"] or {}).get("title"), "Quiz 2", found)
        state = self.client.get(f"/api/grading/scans/{message}").json
        self.assertEqual(state["plan"]["id"], plan["id"])
        with connect(self.path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM exam_events WHERE assignment_id=?", (quiz["id"],)).fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM frq_assignments WHERE event_id=?", (plan["event_id"],)).fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT source FROM frq_scan_links WHERE message_id=?", (message,)).fetchone()[0], "automatic")

    def test_pages_that_look_like_one_blank_but_print_another_title_are_left_to_the_instructor(self):
        self.with_templates()
        pages = [written(self.images["quiz"], f"student {n}") for n in range(3)]
        message = self.submit(pdf(pages))["message_id"]
        self.texts = {n + 1: f"Math 140\n{TEST_TITLE}\nFirst and Last Name (printed): x\nUIN: 1" for n in range(3)}
        found = self.find(message)
        self.assertIsNone(found["chosen"])
        self.assertIn("more than one assignment", found["papers"]["reason"])
        self.assertIsNone(self.client.get(f"/api/grading/scans/{message}").json["plan"])
        first = self.scans.review_item(message)["overview"]["steps"][0]
        self.assertEqual(first["action"], "grading_associate")
        self.assertIn("It was not chosen automatically: its first pages point to more than one assignment", first["text"])

    def test_a_title_alone_or_a_look_alone_is_not_enough(self):
        self.with_templates()
        # Pages that print the quiz's title but look like neither blank (a reprint in another layout).
        message = self.submit(pdf([other_page(f"quiz page {n}") for n in range(3)]))["message_id"]
        self.texts = {n + 1: f"Name: x\nUIN: 1\n{QUIZ_TITLE}" for n in range(3)}
        found = self.find(message)
        self.assertIsNone(found["chosen"])
        self.assertIsNone(self.client.get(f"/api/grading/scans/{message}").json["plan"])
        # Pages that look like the quiz's blank but whose title could not be read.
        message = self.submit(pdf([written(self.images["quiz"], f"s{n}") for n in range(3)]))["message_id"]
        self.texts = {}
        found = self.find(message)
        self.assertIsNone(found["chosen"])
        self.assertEqual(found["papers"]["reason"], "Only 0 of 3 pages both look like Quiz 2's blank template and print its title.")

    def with_look_alike_quiz(self):
        """Quiz 3, printed with Quiz 2's header: its blank is Quiz 2's with one more block, so the two look almost the same."""
        self.with_templates()
        self.quiz3 = self.client.post("/api/documents/assignments", json={"definition_id": self.client.get("/api/state").json["course_definitions"][0]["id"],
                                                                          "key": "quiz-3", "title": "Quiz 3"}, headers=HEADERS).json
        image = self.images["quiz"].copy()
        ImageDraw.Draw(image).rectangle((300, 700, 900, 760), fill=0)
        self.images["quiz3"] = image
        template = str(uuid.uuid4())
        self.templates[template] = "quiz3"
        self.files[(template, "image")] = png(image)
        self.files[(template, "text")] = f"Name: UIN:\n{QUIZ_TITLE.replace('2', '3')}\nSections 3.1-3.3 Version A 10 points".encode()
        with connect(self.path) as db:
            db.execute("""INSERT INTO assignment_templates(id,assignment_id,label,request_id,request_sha256,page_count,source_format,
                          source_names_json,manifest_path,manifest_sha256,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                       (template, self.quiz3["id"], "Version A", str(uuid.uuid4()), "0" * 64, 2, "pdf", "[]", "x", "0" * 64, "2026-09-26T00:00:00+00:00"))
        self.template_shas[sha(png(image))] = "quiz3"

    def scores(self, image):
        """The look scores the finder gives a page against each quiz's blank."""
        finder = self.scans.assignment_finder
        views = finder._views(png(image))
        return {key: float((views @ finder._signature(png(self.images[key]))).max()) for key in ("quiz", "quiz3")}

    def test_a_makeup_paper_of_one_of_two_look_alike_quizzes_is_filed_by_its_printed_title(self):
        # v3 (2026-09-29): one makeup Quiz 3 paper, front and back; the look alone cannot tell Quiz 3 from Quiz 2, the title can.
        self.with_look_alike_quiz()
        page = written(self.images["quiz3"], "Dana Example")
        scores = self.scores(page)
        self.assertGreater(scores["quiz3"], scores["quiz"])
        self.assertLess(scores["quiz3"] - scores["quiz"], assignment_finder.LEAD)  # The look alone would choose nothing (v2).
        message = self.submit(pdf([page, other_page("back")]))["message_id"]
        self.texts = {1: f"Name: Dana Example\nUIN: 123456789\n{QUIZ_TITLE.replace('2', '3')}\nSections 3.1-3.3 Version B 10 points"}
        found = self.find(message)
        self.assertIsNotNone(found["chosen"], found)
        self.assertEqual((found["rule"], found["chosen"]["title"], found["chosen"]["agreeing"]), ("assignment-from-template-v4", "Quiz 3", 1))
        self.assertEqual(self.client.get(f"/api/grading/scans/{message}").json["plan"]["title"], "Quiz 3")

    def test_a_look_alike_page_that_prints_the_other_quizs_title_chooses_nothing(self):
        # The page looks most like Quiz 2's blank but prints Quiz 3's title: the title never outvotes the look.
        self.with_look_alike_quiz()
        page = written(self.images["quiz"], "Eve Example")
        scores = self.scores(page)
        self.assertGreater(scores["quiz"], scores["quiz3"])
        message = self.submit(pdf([page, other_page("back")]))["message_id"]
        self.texts = {1: f"Name: Eve Example\nUIN: 123456789\n{QUIZ_TITLE.replace('2', '3')}"}
        found = self.find(message)
        self.assertIsNone(found["chosen"], found)
        self.assertEqual(found["papers"]["reason"], "Only 0 of 1 pages both look like Quiz 3's blank template and print its title.")
        self.assertIsNone(self.client.get(f"/api/grading/scans/{message}").json["plan"])

    def test_no_blank_templates_means_nothing_is_read(self):
        message = self.submit(pdf([other_page(f"page {n}") for n in range(4)]))["message_id"]
        self.template_shas, self.texts = {}, {}
        found = self.find(message)
        self.assertEqual(self.read, [])
        self.assertIn("No current assignment has a blank template", found["papers"]["reason"])

    def test_a_scan_waits_for_alias_reads_only_until_a_sample_finds_no_printed_alias(self):
        # 2026-10-31: a 98-page scan waited on 60 alias reads of about 18 s each and was linked by hand meanwhile. Once
        # `ALIAS_SAMPLE` reads are done and none saw a printed alias, the scan is looked at; a read that saw one keeps the wait.
        message = self.submit(pdf([other_page(f"page {n}") for n in range(12)]))["message_id"]
        finder, sample = self.scans.assignment_finder, assignment_finder.ALIAS_SAMPLE
        with connect(self.path) as db:
            pages = [row[0] for row in db.execute("SELECT id FROM scan_pages WHERE message_id=? ORDER BY source_page_index", (message,))]
            self.assertGreater(len(pages), sample)
            db.executemany("INSERT INTO scan_alias_ocr(scan_page_id,message_id,status,created_at,updated_at) VALUES(?,?,'pending','t','t')",
                           [(page, message) for page in pages])

            def done(page, outcome="no_match", aliases=()):
                db.execute("UPDATE scan_alias_ocr SET status='complete',result_json=? WHERE scan_page_id=?",
                           (json.dumps({"outcome": outcome, "aliases": list(aliases), "needs_review": True}), page))

            def waiting():
                return (finder._next(db) or {}).get("id") != message

            for page in pages[:sample - 1]:
                done(page)
            self.assertTrue(waiting())                       # one read short of the sample
            done(pages[sample - 1])
            self.assertFalse(waiting())                      # the sample saw no alias: no more waiting
            done(pages[sample], "conflict", ["A1-0000000000", "A1-1111111111"])
            self.assertTrue(waiting())                       # a read that saw an alias: wait for every read, as before
            for page in pages[sample + 1:]:
                done(page)
            self.assertFalse(waiting())

    def test_bubble_sheets_take_the_one_exam_on_the_calendar(self):
        today = datetime.now(timezone.utc).astimezone(ZoneInfo("America/Chicago")).date().isoformat()
        with connect(self.path) as db:
            db.execute("INSERT INTO exam_sittings(id,event_id,label,kind,date,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                       (str(uuid.uuid4()), self.event["id"], "Main", "main", today, today, today))
        self.template_shas, self.texts = {}, {}
        message = self.submit(pdf([sheet({1: "A"}), sheet({1: "B"})]))["message_id"]
        found = self.find(message)
        self.assertEqual((found["chosen"]["title"], found["chosen"]["basis"], found["chosen"]["dates"]), ("Test 1", "calendar", [today]))
        with connect(self.path) as db:
            run = db.execute("SELECT assignment_id,actor FROM bubble_runs WHERE message_id=?", (message,)).fetchone()
        self.assertEqual((run["assignment_id"], run["actor"]), (self.assignment["id"], "automatic"))
        self.assertEqual(self.client.get(f"/api/grading/scans/{message}").json["plan"]["title"], "Test 1")
        info = [step for step in self.scans.review_item(message)["overview"]["steps"] if "chosen automatically" in step["text"]]
        self.assertIn(f"it is the only exam on the calendar ({today})", info[0]["text"])
        # A second exam in the same two weeks: nothing is chosen.
        quiz = self.client.post("/api/documents/assignments", json={"definition_id": self.client.get("/api/state").json["course_definitions"][0]["id"],
                                                                    "key": "quiz-3", "title": "Quiz 3"}, headers=HEADERS).json
        event = self.exams.create({"term_id": self.section["term_id"], "assignment_id": quiz["id"], "section_ids": [self.section["id"]]})
        with connect(self.path) as db:
            db.execute("INSERT INTO exam_sittings(id,event_id,label,kind,date,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                       (str(uuid.uuid4()), event["id"], "Main", "main", today, today, today))
        message = self.submit(pdf([sheet({1: "C"})]))["message_id"]
        found = self.find(message)
        self.assertIsNone(found["chosen"])
        self.assertIn("More than one exam is on the calendar", found["sheets"]["reason"])
        with connect(self.path) as db:
            self.assertIsNone(db.execute("SELECT 1 FROM bubble_runs WHERE message_id=?", (message,)).fetchone())

    def test_bubble_sheets_take_the_one_exam_on_the_calendar_whose_answer_key_fits(self):
        # 2026-10-26: Exam 2's sheets chose nothing because quizzes without a key had makeup sittings in the same two weeks. An exam
        # with a key is theirs only when its key has as many questions as the sheets answer.
        today = datetime.now(timezone.utc).astimezone(ZoneInfo("America/Chicago")).date().isoformat()
        quiz = self.client.post("/api/documents/assignments", json={"definition_id": self.client.get("/api/state").json["course_definitions"][0]["id"],
                                                                    "key": "quiz-3", "title": "Quiz 3"}, headers=HEADERS).json
        quiz_event = self.exams.create({"term_id": self.section["term_id"], "assignment_id": quiz["id"], "section_ids": [self.section["id"]]})

        def key(event_id, questions):
            versions = [{"letter": letter, "questions": [{"number": n, "answer": "A"} for n in range(1, questions + 1)]} for letter in "AB"]
            with connect(self.path) as db:
                plan = str(uuid.uuid4())
                db.execute("INSERT INTO frq_assignments VALUES(?,?,1,?,?)", (plan, event_id, today, today))
                db.execute("INSERT INTO frq_bubble_keys(id,assignment_id,revision,key_json,sha256,request_id,request_sha256,actor,created_at) VALUES(?,?,1,?,'x',?,'x','instructor',?)",
                           (str(uuid.uuid4()), plan, json.dumps({"versions": versions}), str(uuid.uuid4()), today))

        with connect(self.path) as db:
            for event_id, kind in ((self.event["id"], "main"), (quiz_event["id"], "makeup")):
                db.execute("INSERT INTO exam_sittings(id,event_id,label,kind,date,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                           (str(uuid.uuid4()), event_id, kind.title(), kind, today, today, today))
        key(self.event["id"], 3)
        self.template_shas, self.texts = {}, {}
        message = self.submit(pdf([sheet({1: "A", 2: "B", 3: "C"}), sheet({1: "B", 2: "B", 3: "D"})]))["message_id"]
        found = self.find(message)
        self.assertEqual((found["chosen"]["title"], found["chosen"]["basis"], found["chosen"]["answer_key"], found["chosen"]["questions"]),
                         ("Test 1", "calendar", True, 3))
        self.assertEqual(sorted((exam["title"], exam["answer_key"], exam["key_questions"]) for exam in found["sheets"]["exams"]),
                         [("Quiz 3", False, []), ("Test 1", True, [3])])
        self.assertEqual(self.client.get(f"/api/grading/scans/{message}").json["plan"]["title"], "Test 1")
        info = [step for step in self.scans.review_item(message)["overview"]["steps"] if "chosen automatically" in step["text"]]
        self.assertIn(f"it is the only exam with a bubble-sheet answer key on the calendar ({today}) in the two weeks before these bubble sheets "
                      "arrived, and the sheets answer its key's 3 questions", info[0]["text"])
        # Sheets answering two questions do not fit Test 1's key: nothing is chosen.
        message = self.submit(pdf([sheet({1: "C", 2: "A"}), sheet({1: "D", 2: "E"})]))["message_id"]
        found = self.find(message)
        self.assertIsNone(found["chosen"])
        self.assertEqual(found["sheets"]["questions"], 2)
        self.assertIn("The sheets answer 2 questions, and no answer key", found["sheets"]["reason"])
        with connect(self.path) as db:
            self.assertIsNone(db.execute("SELECT 1 FROM bubble_runs WHERE message_id=?", (message,)).fetchone())
        # Both keys fit: nothing is chosen.
        key(quiz_event["id"], 3)
        message = self.submit(pdf([sheet({1: "C", 2: "C", 3: "E"})]))["message_id"]
        found = self.find(message)
        self.assertIsNone(found["chosen"])
        self.assertIn("More than one exam with a bubble-sheet answer key", found["sheets"]["reason"])

    def jev_reads(self, message, answers):
        """`find` with Jev set up and reading each scan page (by its number) as `answers` says: an assignment id, None or "none"."""
        asked = []

        def which(jev, owner, picture, assignments):
            index = self.page_index_of(owner)
            asked.append(index)
            answer = answers.get(index, "none")
            return {"status": "complete", "assignment_id": None if answer == "none" else answer, "none": answer == "none"}

        with patch.object(assignment_finder.AssignmentFinder, "_jev", return_value=object()), \
                patch("document_identity.jev_papers.which_assignment", side_effect=which):
            return self.find(message), asked

    page_index_of = test_unlabeled_exams.UnlabeledExams.page_index_of

    def test_jev_proposes_the_assignment_the_templates_leave_undecided_and_it_is_chosen_when_the_pages_fit(self):
        # Quiz 2's blank has 2 pages; these pages look like no blank and print no title.
        self.with_templates()
        self.texts = {}
        message = self.submit(pdf([other_page(f"quiz page {n}") for n in range(4)]))["message_id"]
        found, asked = self.jev_reads(message, {1: self.quiz["id"], 3: self.quiz["id"]})
        self.assertEqual(sorted(asked), [1, 2, 3, 4])
        self.assertEqual((found["chosen"]["title"], found["chosen"]["basis"], found["chosen"]["pages"], found["chosen"]["sides"]), ("Quiz 2", "jev", 4, 2))
        self.assertEqual(found["papers"]["jev"]["agreeing"], 2)
        self.assertEqual(self.client.get(f"/api/grading/scans/{message}").json["plan"]["title"], "Quiz 2")
        info = [step for step in self.scans.review_item(message)["overview"]["steps"] if "chosen automatically" in step["text"]]
        self.assertIn("Quiz 2 was chosen automatically: Jev read 2 of its pages as that assignment, and its 4 pages are whole papers of 2.", info[0]["text"])

    def test_jev_proposal_whose_page_count_does_not_fit_chooses_nothing(self):
        self.with_templates()
        self.texts = {}
        message = self.submit(pdf([other_page(f"odd page {n}") for n in range(5)]))["message_id"]
        found, _ = self.jev_reads(message, {1: self.quiz["id"], 3: self.quiz["id"], 5: self.quiz["id"]})
        self.assertIsNone(found["chosen"])
        self.assertIn("Jev reads it as Quiz 2, but its 5 pages are not whole papers of 2 pages; choose its assignment.", found["reason"])
        self.assertIsNone(self.client.get(f"/api/grading/scans/{message}").json["plan"])

    def test_jev_reading_two_assignments_chooses_nothing(self):
        self.with_templates()
        self.texts = {}
        message = self.submit(pdf([other_page(f"mixed page {n}") for n in range(4)]))["message_id"]
        found, _ = self.jev_reads(message, {1: self.quiz["id"], 3: self.assignment["id"]})
        self.assertIsNone(found["chosen"])
        self.assertIn("Jev read its pages as more than one assignment (Quiz 2, Test 1)", found["reason"])
        self.assertIsNone(self.client.get(f"/api/grading/scans/{message}").json["plan"])

    def test_title_lines_leave_out_fields_versions_and_course_names(self):
        self.assertEqual(assignment_finder.title_lines("Name: UIN:\nMATH 140 Fall 2026 Chapter 2 Quiz\nSections 2.1–2.4 Version A 10 points\nMath 140"),
                         ["math 140 fall 2026 chapter 2 quiz"])
        self.assertEqual(assignment_finder.title_lines(cover_text("Jane", "1")), ["common exam i, fall 2026"])


if __name__ == "__main__":
    unittest.main()
