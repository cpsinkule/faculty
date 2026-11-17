"""PICTURE-SEARCH part two (requirement, 2026-10-10): the printed pages of quizzes and exams,
question bank pictures, course notes pages, absence documentation and attachments on mail a student wrote are indexed beside
scanned pages. Where Faculty stores words about a picture they are matched first (the instructor searched "inequalities" and
got pages that "weren't really inequalities": the model cannot read a page); everything else is found by how it looks. Each
source applies its owner's rule for what is gone, and a result about a student's file never says whose it is.

A stand-in embedder throughout: no model is loaded and the live worker is never reached."""
import hashlib
import io
import json
import re
import sqlite3
import uuid
from unittest.mock import patch

import numpy as np
from PIL import Image

from faculty_embedding import client as embedding
from picture_search import service as picture_service, sources as picture_sources
from picture_search.index import BLANK
from rosters import connect
from test_absences import Base
from test_assistant_files import png
from test_course_notes import OUTLINE, PAGES, notes_pdf
from test_storage import HEADERS

DIMENSION = 16
KEYWORDS = {"inequalit": 1, "matri": 2, "supply": 3, "number line": 4}
NOW = "2026-10-01T15:00:00+00:00"


def unit(position):
    vector = np.zeros(DIMENSION, dtype=np.float32)
    vector[position % DIMENSION] = 1
    return vector


class StandIn:
    """A picture lands on the coordinate of its first pixel's shade (180+n on n). Words land on the coordinate of the first
    keyword they hold ("look n" on n); words with neither are equally near everything."""
    def __init__(self):
        self.pictures, self.asked = [], []

    def picture_vectors(self, pictures, timeout=20, path=None):
        self.pictures.append(len(pictures))
        return np.stack([unit(Image.open(io.BytesIO(data)).convert("L").getpixel((0, 0)) - 180) for data in pictures])

    def text_vectors(self, texts, role="query", timeout=1.5, path=None):
        self.asked.append((role, list(texts)))
        rows = []
        for text in texts:
            look = re.search(r"look (\d+)", text.lower())
            place = next((position for word, position in KEYWORDS.items() if word in text.lower()), int(look.group(1)) if look else None)
            rows.append(np.full(DIMENSION, DIMENSION ** -0.5, dtype=np.float32) if place is None else unit(place))
        return np.stack(rows)

    def status(self, timeout=1.5, path=None):
        return {"device": "test"}


def converted(files, entrypoint=None):
    """A two-page quiz as the template converter returns it: matrices on page 1 (shade 185), inequalities on page 2 (186)."""
    texts = {1: "1. Find the product of the two matrices.", 2: "2. Graph the solution set of the system of linear inequalities."}
    return {"pdf": b"Synthetic reference PDF", "sources": files, "conversion": {"engine": "synthetic-converter", "dpi": 300},
            "pages": [{"number": number, "width_pt": 612, "height_pt": 792, "width_px": 612, "height_px": 792, "text": texts[number],
                       "png": png(612, 792, 184 + number)} for number in (1, 2)]}


def pictures_only(files, entrypoint=None):
    """The same quiz uploaded as pictures: Faculty holds no text of either page, and page 2 is its empty back (white)."""
    made = converted(files, entrypoint)
    for page, shade in zip(made["pages"], (185, 255)):
        page.update(text="", png=png(612, 792, shade))
    return made


class SourcesTests(Base):
    def setUp(self):
        super().setUp()
        self.path = self.app.config["DATABASE"]
        self.search = self.app.extensions["picture_search"]
        self.model = StandIn()
        for name in ("picture_vectors", "text_vectors", "status"):
            self.enterContext(patch.object(embedding, name, getattr(self.model, name)))
        self.enterContext(patch.object(embedding, "configured", return_value=True))

    def index_all(self):
        turns = 0
        self.search._wait_until = 0
        while self.search.process_one():
            turns += 1
        return turns

    def find(self, text, **query):
        answer = self.client.get("/api/picture-search", query_string={"q": text, **query})
        self.assertEqual(answer.status_code, 200, answer.json)
        return answer.json["results"]

    def of(self, results, kind):
        return [hit for hit in results if hit["kind"] == kind]

    # -- printed pages -------------------------------------------------------------------------------------------------------
    def templates(self):
        documents, templates = self.app.extensions["documents"], self.app.extensions["assignment_templates"]
        assignment = documents.create_assignment(self.definition, "quiz-3", "Quiz 3")
        with patch("document_identity.template_conversion.convert_template", side_effect=converted):
            made = [templates.upload(assignment_id=assignment["id"], label=label, request_id=str(uuid.uuid4()), files={"quiz.pdf": label.encode()})
                    for label in ("A", "B")]
        return assignment, made

    def test_a_printed_page_is_found_by_what_is_printed_on_it_and_its_versions_are_one_result(self):
        assignment, (first, second) = self.templates()
        self.assertEqual(self.search.status()["waiting"], 4)
        self.assertEqual(self.index_all(), 4)
        self.assertEqual(self.model.pictures, [1, 1, 1, 1], "one picture a request")
        documents = [texts[0] for role, texts in self.model.asked if role == "document"]
        self.assertEqual(len(documents), 4)
        self.assertTrue(all(text.startswith("Quiz 3 ") for text in documents))
        self.assertTrue(all(len(text) <= picture_sources.WORDS_CHARS for text in documents))

        found = self.find("inequalities")
        pages = self.of(found, "Printed page")
        self.assertEqual(found[0]["label"], "Quiz 3, page 2")  # what Faculty's words say comes first
        self.assertEqual((found[0]["matched"], found[0]["source"], found[0]["score"]), ("words", "template", 1.0))
        # Its versions A and B are one result; page 1 has words of its own that say something else, so it is not guessed at.
        self.assertEqual([hit["label"] for hit in found], ["Quiz 3, page 2"])
        self.assertEqual(pages, found)
        self.assertEqual(found[0]["link"], f"#documents?assignment={assignment['id']}&step=details")
        self.assertTrue(found[0]["thumb"].endswith("/pages/2/preview.webp"))
        self.assertIn("Printed copy · MATH 140", found[0]["where"])
        self.assertNotIn("same", json.dumps(found))
        picture = self.client.get(found[0]["image"])
        self.assertEqual((picture.status_code, picture.mimetype), (200, "image/png"))
        self.assertEqual(self.client.get(found[0]["thumb"]).status_code, 200)
        # Words that are not near the description are no match: the pages are then only what looks nearest, a version each.
        self.assertEqual([(hit["label"], hit["matched"], hit["score"]) for hit in self.find("look 5")],
                         [("Quiz 3, page 1", "look", 1.0), ("Quiz 3, page 2", "look", 0.0)])

        # A question's stored text on the page is among its words: a new revision of it embeds the words again, not the picture.
        with sqlite3.connect(self.path) as db:
            db.execute("PRAGMA foreign_keys=OFF")
            db.execute("INSERT INTO frq_questions(id, assignment_version_id, question_key, created_at, exam_version) VALUES('q-1','v-1','q1',?, 'A')", (NOW,))
            content = {"template_id": first["id"], "problem": "Find the equilibrium of supply and demand.", "regions": [{"page": 1, "region": {}}]}
            db.execute("INSERT INTO frq_question_revisions(id, question_id, revision, content_json, sha256, created_at) VALUES('r-1','q-1',1,?,?,?)",
                       (json.dumps(content), "1" * 64, NOW))
        before = len(self.model.pictures)
        self.assertEqual(self.search.status()["waiting"], 1)
        self.assertEqual(self.index_all(), 1)
        self.assertEqual(len(self.model.pictures), before + 1)
        self.assertIn("supply and demand", self.model.asked[-1][1][0])
        self.assertEqual(self.find("supply")[0]["label"], "Quiz 3, page 1")
        # Only the kinds asked for are searched.
        self.assertEqual(self.find("inequalities", sources="scan,bank"), [])
        self.assertEqual({hit["source"] for hit in self.find("inequalities", sources="template")}, {"template"})
        self.assertEqual(self.client.get("/api/picture-search", query_string={"q": "x y z", "sources": "photos"}).status_code, 400)

    def test_a_label_alone_is_not_words_and_a_page_with_nothing_on_it_is_not_indexed(self):
        """
        an empty page whose only words were its label, which is near any vague description."""
        documents, templates = self.app.extensions["documents"], self.app.extensions["assignment_templates"]
        assignment = documents.create_assignment(self.definition, "quiz-3", "Quiz 3")
        with patch("document_identity.template_conversion.convert_template", side_effect=pictures_only):
            made = templates.upload(assignment_id=assignment["id"], label="A", request_id=str(uuid.uuid4()), files={"quiz.pdf": b"A"})
        self.assertEqual(self.index_all(), 2)
        self.assertEqual(self.model.asked, [], "a label is not embedded as words about a page")
        self.assertEqual(self.model.pictures, [1], "the empty page is not sent to the model")
        status = self.search.status()
        self.assertEqual((status["by_source"], status["unreadable"], status["waiting"]), ({"template": 1}, 0, 0))
        self.assertEqual(self.search.index.text_matrix(picture_service.MODEL)[0], [])
        with sqlite3.connect(self.search.index.path) as db:
            self.assertEqual(db.execute("SELECT ref, reason FROM skipped WHERE source='template'").fetchall(), [(f"{made['id']}#2", BLANK)])
            db.execute("UPDATE skipped SET at='2026-01-01T00:00:00+00:00'")  # long ago: neither is a fault that may pass
        self.search._wait_until = 0
        self.assertEqual(self.search.status()["waiting"], 0)
        # Whatever is described, the page with no words is only what looks nearest, and the empty one is never an answer.
        for description in ("Quiz 3", "syllabus", "look 11"):  # the stand-in puts a white picture where "look 11" is
            self.assertEqual([(hit["label"], hit["matched"]) for hit in self.find(description)], [("Quiz 3, page 1", "look")], description)

        # A question stored for the page gives it words: it is read again and found by them.
        with sqlite3.connect(self.path) as db:
            db.execute("PRAGMA foreign_keys=OFF")
            db.execute("INSERT INTO frq_questions(id, assignment_version_id, question_key, created_at, exam_version) VALUES('q-1','v-1','q1',?, 'A')", (NOW,))
            content = {"template_id": made["id"], "problem": "Find the equilibrium of supply and demand.", "regions": [{"page": 1, "region": {}}]}
            db.execute("INSERT INTO frq_question_revisions(id, question_id, revision, content_json, sha256, created_at) VALUES('r-1','q-1',1,?,?,?)",
                       (json.dumps(content), "1" * 64, NOW))
        self.assertEqual(self.index_all(), 1)
        self.assertEqual(self.model.asked[-1], ("document", ["Quiz 3 A, page 1 Find the equilibrium of supply and demand."]))
        self.assertEqual([(hit["label"], hit["matched"]) for hit in self.find("supply")], [("Quiz 3, page 1", "words")])

    def test_what_was_worded_another_way_is_read_again(self):
        self.templates()
        self.index_all()
        self.assertEqual(self.search.status()["waiting"], 0)
        with patch.object(picture_sources, "WORDING", "another way"):
            self.assertEqual(self.search.status()["waiting"], 4)

    # -- the question bank ---------------------------------------------------------------------------------------------------
    def test_a_bank_picture_is_found_by_its_question_until_it_is_removed_or_its_question_retired(self):
        question = self.client.post(f"/api/question-bank/definitions/{self.definition}/questions", headers=HEADERS, json={
            "request_id": "w1", "content": {"kind": "free_response", "statement": "Shade the solution of the inequality.", "points": 3}})
        self.assertEqual(question.status_code, 200, question.json)
        kept = self.client.post(f"/api/question-bank/questions/{question.json['id']}/pictures", headers=HEADERS,
                                data={"request_id": "p1", "caption": "A shaded half-plane.", "file": (io.BytesIO(png(60, 40, 187)), "graph.png")})
        self.assertEqual(kept.status_code, 200, kept.json)
        with connect(self.path) as db:
            picture = db.execute("SELECT id FROM question_bank_pictures").fetchone()["id"]
        self.assertEqual(self.index_all(), 1)
        self.assertEqual(self.model.asked[-1], ("document", ["Question bank A shaded half-plane. Shade the solution of the inequality."]))
        hit = self.find("inequalities")[0]
        self.assertEqual((hit["kind"], hit["label"], hit["matched"]), ("Question bank", "A shaded half-plane.", "words"))
        self.assertTrue(hit["where"].startswith("Question bank · MATH 140"))
        self.assertRegex(hit["link"], r"^#course/[0-9a-f-]+/home/questions$")
        self.assertEqual(self.client.get(hit["image"]).status_code, 200)

        def post(route):
            answer = self.client.post(route, json={"request_id": str(uuid.uuid4())}, headers=HEADERS)
            self.assertEqual(answer.status_code, 200, answer.json)
        post(f"/api/question-bank/pictures/{picture}/remove")
        self.assertEqual(self.find("inequalities"), [])
        self.assertEqual(self.search.status()["waiting"], 0)
        post(f"/api/question-bank/pictures/{picture}/restore")
        self.assertEqual(len(self.find("inequalities")), 1)
        post(f"/api/question-bank/questions/{question.json['id']}/retire")
        self.assertEqual(self.find("inequalities"), [])

    def test_each_kind_with_words_has_a_place_so_many_bank_pictures_cannot_crowd_out_the_printed_page(self):
        self.templates()
        question = self.client.post(f"/api/question-bank/definitions/{self.definition}/questions", headers=HEADERS, json={
            "request_id": "w1", "content": {"kind": "free_response", "statement": "Graph the inequality.", "points": 3}}).json
        for number in range(5):
            kept = self.client.post(f"/api/question-bank/questions/{question['id']}/pictures", headers=HEADERS, data={
                "request_id": f"p{number}", "caption": f"Inequality graph {number}", "file": (io.BytesIO(png(60 + number, 40, 187)), "graph.png")})
            self.assertEqual(kept.status_code, 200, kept.json)
        self.index_all()
        for limit in ("2", "4"):
            found = self.find("inequalities", limit=limit)
            self.assertEqual([hit["kind"] for hit in found], ["Printed page"] + ["Question bank"] * (int(limit) - 1), "the printed page first")
            self.assertEqual({hit["matched"] for hit in found}, {"words"})

    def test_once_words_match_a_look_alike_fills_a_place_only_when_it_is_near(self):
        question = self.client.post(f"/api/question-bank/definitions/{self.definition}/questions", headers=HEADERS, json={
            "request_id": "w1", "content": {"kind": "free_response", "statement": "Graph the inequality.", "points": 3}}).json
        kept = self.client.post(f"/api/question-bank/questions/{question['id']}/pictures", headers=HEADERS, data={
            "request_id": "p1", "caption": "A half-plane", "file": (io.BytesIO(png(60, 40, 187)), "graph.png")})
        self.assertEqual(kept.status_code, 200, kept.json)
        absence = self.record().json
        for number, shade in enumerate((181, 183)):  # the stand-in puts shade 181 where "inequalities" is, and 183 far from it
            sent = self.client.post(f"/api/absences/{absence['id']}/documents", headers=HEADERS,
                                    data={"file": (io.BytesIO(png(300 + number, 400, shade)), "note.png"), "request_id": f"u{number}"})
            self.assertEqual(sent.status_code, 200, sent.json)
        self.index_all()
        found = self.find("inequalities")
        self.assertEqual([(hit["kind"], hit["matched"], hit["score"]) for hit in found],
                         [("Question bank", "words", 1.0), ("Absence documentation", "look", 1.0)])
        # No words match: look is the only answer there is, however far (the bank picture too, by its look).
        self.assertEqual(sorted((hit["kind"], hit["matched"], hit["score"]) for hit in self.find("look 3")),
                         [("Absence documentation", "look", 0.0), ("Absence documentation", "look", 1.0), ("Question bank", "look", 0.0)])

    # -- course notes --------------------------------------------------------------------------------------------------------
    def test_course_notes_pages_are_found_by_their_section_and_text_and_only_the_current_edition(self):
        route = f"/api/course-notes/definitions/{self.definition}"
        uploaded = self.client.post(route, data={"file": (io.BytesIO(notes_pdf(PAGES, OUTLINE)), "notes.pdf"), "request_id": "u1"}, headers=HEADERS)
        self.assertEqual(uploaded.status_code, 200, uploaded.json)
        current = uploaded.json["current"]
        pages = sum(part["pages"] for part in current["parts"])
        self.index_all()
        self.assertEqual(self.search.status()["by_source"], {"notes": pages})
        self.assertTrue(all(count <= picture_sources.PDF_PAGES for count in self.model.pictures))
        found = self.of(self.find("matrix multiplication", limit="24"), "Course notes")
        labels = [hit["label"] for hit in found if hit["matched"] == "words"]
        self.assertIn("1.2 Matrix Multiplication and Its Applications to Real Scenarios, page 1", labels)
        hit = next(hit for hit in found if hit["label"].startswith("1.2 Matrix Multiplication"))
        self.assertEqual((hit["pdf_page"], hit["open_page"], hit["where"]), (1, 1, "Course notes · MATH 140"))
        self.assertTrue(hit["image"].endswith("/notes.pdf"))
        self.assertRegex(hit["link"], r"^#course/[0-9a-f-]+/home/notes$")
        self.assertEqual(self.client.get(hit["image"]).mimetype, "application/pdf")
        thumb = self.client.get(hit["thumb"])
        self.assertEqual((thumb.status_code, thumb.mimetype), (200, "image/png"))

        # The Assistant finds it too, and looks at that page of the PDF through fetch_files.
        assistant = self.app.extensions["assistant"]
        text, event = assistant.run_tool(assistant.open_session("test"), "find_pictures", {"query": "matrix multiplication", "kinds": "notes", "limit": 24})
        seen = next(found for found in json.loads(text)["pictures"] if found["label"] == hit["label"])
        self.assertEqual((seen["kind"], seen["pages"], seen["matched"], seen["image"]), ("Course notes", "1", "words", hit["image"]))
        self.assertNotIn("files", event)
        text, event = assistant.run_tool(assistant.open_session("test"), "fetch_files", {"paths": [seen["image"]], "pages": seen["pages"], "look": True})
        self.assertEqual(len(event["pictures"]), 1, text)

        removed = self.client.post(f"/api/course-notes/{current['id']}/remove", json={"request_id": "r1", "confirmation": "remove"}, headers=HEADERS)
        self.assertEqual(removed.status_code, 200, removed.json)
        self.assertEqual(self.of(self.find("matrix multiplication", limit="24"), "Course notes"), [])
        self.assertEqual(self.search.status()["waiting"], 0)

    def test_an_empty_notes_page_is_left_out_and_the_pages_after_it_are_still_read(self):
        # Front matter of two pages, the first of them empty (the first page of its turn, so the others must not wait on it).
        pages = [[], *PAGES[1:]]
        uploaded = self.client.post(f"/api/course-notes/definitions/{self.definition}", headers=HEADERS,
                                    data={"file": (io.BytesIO(notes_pdf(pages, OUTLINE)), "notes.pdf"), "request_id": "u1"})
        self.assertEqual(uploaded.status_code, 200, uploaded.json)
        current = uploaded.json["current"]
        self.index_all()
        status = self.search.status()
        self.assertEqual((status["by_source"], status["unreadable"], status["waiting"]),
                         ({"notes": sum(part["pages"] for part in current["parts"]) - 1}, 0, 0))
        with sqlite3.connect(self.search.index.path) as db:
            self.assertEqual(db.execute("SELECT ref, reason FROM skipped WHERE source='notes'").fetchall(), [(f"{current['id']}:1#1", BLANK)])
            self.assertEqual(db.execute("SELECT COUNT(*) FROM texts WHERE ref=?", (f"{current['id']}:1#1",)).fetchone()[0], 0)
        self.assertTrue(all("Course notes Front matter" != text for _, texts in self.model.asked for text in texts))
        # Every notes page here is white where the stand-in looks, so by look they are all equally near: all but the empty one.
        labels = [hit["label"] for hit in self.find("look 11", limit="24", sources="notes")]
        self.assertIn("Front matter, page 2", labels)
        self.assertNotIn("Front matter, page 1", labels)
        self.assertEqual(len(labels), status["by_source"]["notes"])

    # -- documents students send ---------------------------------------------------------------------------------------------
    def test_absence_documentation_is_found_by_how_it_looks_and_never_named(self):
        absence = self.record().json
        photo = self.client.post(f"/api/absences/{absence['id']}/documents", headers=HEADERS,
                                 data={"file": (io.BytesIO(png(300, 400, 183)), "Alice Example clinic note.png"), "request_id": "u1"})
        self.assertEqual(photo.status_code, 200, photo.json)
        letter = self.client.post(f"/api/absences/{absence['id']}/documents", headers=HEADERS,
                                  data={"file": (io.BytesIO(notes_pdf([["A letter"], ["Its second page"]], [])), "letter.pdf"), "request_id": "u2"})
        self.assertEqual(letter.status_code, 200, letter.json)
        self.assertEqual(self.index_all(), 2)
        self.assertEqual(self.search.status()["by_source"], {"absence": 3})  # the photo, and both pages of the letter
        self.assertEqual([role for role, _ in self.model.asked], [], "nothing is stored about a student's file to match by words")

        hit = self.find("look 3")[0]
        self.assertEqual((hit["kind"], hit["label"], hit["matched"], hit["link"]), ("Absence documentation", "Absence documentation", "look", "#absences"))
        self.assertEqual(hit["where"][:22], "MATH 140 · Section 507")
        self.assertTrue(hit["thumb"].startswith("/api/viewer/picture?route=%2Fapi%2Fabsences%2F"))
        self.assertEqual(self.client.get(hit["thumb"]).status_code, 200)
        pages = [found for found in self.find("look 11", limit="24") if found["kind"] == "Absence documentation" and "pdf_page" in found]
        self.assertEqual(sorted(found["label"] for found in pages), ["Absence documentation", "Absence documentation, page 2"])
        self.assertEqual(self.client.get(pages[0]["thumb"]).mimetype, "image/png")
        text = json.dumps(self.find("look 3", limit="24"))
        for absent in ("Alice", "Example", "clinic", "letter.pdf", self.alice, "illness", "Flu"):
            self.assertNotIn(absent, text)

        document = photo.json["documents"][0]
        gone = self.client.post(f"{document['url']}/remove", json={"request_id": "r1"}, headers=HEADERS)
        self.assertEqual(gone.status_code, 200, gone.json)
        self.assertNotIn(1.0, [found["score"] for found in self.find("look 3")])  # removed from its absence: no longer found
        back = self.client.post(f"{document['url']}/restore", json={"request_id": "r2"}, headers=HEADERS)
        self.assertEqual(back.status_code, 200, back.json)
        self.assertEqual((self.find("look 3")[0]["label"], self.find("look 3")[0]["score"]), ("Absence documentation", 1.0))

    def test_an_empty_picture_a_student_sent_is_not_indexed_and_an_empty_first_page_does_not_hold_back_the_rest(self):
        absence = self.record().json
        for number, (content, name) in enumerate(((png(300, 400, 255), "empty.png"), (notes_pdf([[], ["Its second page"]], []), "letter.pdf"))):
            sent = self.client.post(f"/api/absences/{absence['id']}/documents", headers=HEADERS,
                                    data={"file": (io.BytesIO(content), name), "request_id": f"u{number}"})
            self.assertEqual(sent.status_code, 200, sent.json)
        self.assertEqual(self.index_all(), 2)
        status = self.search.status()
        self.assertEqual((status["by_source"], status["unreadable"], status["waiting"]), ({"absence": 1}, 0, 0))
        self.assertEqual([hit["label"] for hit in self.find("look 11")], ["Absence documentation, page 2"])
        with sqlite3.connect(self.search.index.path) as db:
            self.assertEqual({row[0] for row in db.execute("SELECT reason FROM skipped")}, {BLANK})
            db.execute("UPDATE skipped SET at='2026-01-01T00:00:00+00:00'")
        self.search._wait_until = 0
        self.assertEqual(self.search.status()["waiting"], 0)  # empty until the file changes: not tried again in a week

    def test_every_attachment_a_student_sent_is_found_once_where_it_was_first_sent(self):
        # On the live record a reply carried
        # the pictures of the message it answered, and the rule that took a picture on two messages for a signature's logo
        # had left 19 of 104 attachments out, none of them a logo.
        photo, screenshot = png(300, 400, 184), png(300, 400, 185)
        digest = hashlib.sha256(photo).hexdigest()
        sent = [{"part": 1, "name": "Alice Example.jpg", "type": "image/jpeg", "size": 50000, "sha256": digest, "kept": True},
                {"part": 2, "name": "icon.png", "type": "image/png", "size": 3000, "sha256": "b" * 64, "kept": True},
                {"part": 3, "name": "Screenshot.png", "type": "image/png", "size": 20000, "sha256": "c" * 64, "kept": True},
                {"part": 4, "name": "clip.mov", "type": "video/quicktime", "size": 900000, "sha256": "d" * 64, "kept": True},
                {"name": "lost.jpg", "type": "image/jpeg", "size": 50000, "kept": False, "reason": "It could not be fetched."}]
        key = {"part": 1, "name": "key.png", "type": "image/png", "size": 60000, "sha256": "e" * 64, "kept": True}

        def message(ident, role, files, link, at):
            with connect(self.path) as db:
                db.execute("INSERT INTO comm_messages(id, channel, external_id, thread_key, captured_at) VALUES(?,?,?,?,?)", (ident, "gmail", ident, "t", NOW))
                db.execute("""INSERT INTO comm_revisions(id, message_id, revision, sent_at, author_role, author_json, recipients_json, subject,
                    body_text, attachments_json, context_json, content_sha256, captured_at) VALUES(?,?,1,?,?,'{}','[]','Absence','Hello',?,?,?,?)""",
                           (ident + "-r1", ident, at, role, json.dumps(files), json.dumps({"thread_id": "19abc0de"}), "0" * 64, NOW))
                db.execute("INSERT INTO comm_links(id, message_id, enrollment_id, term_id, role, matched_by, created_at) VALUES(?,?,?,?,?,?,?)",
                           (ident + "-l", ident, self.alice, self.term, link, "roster_address", NOW))
        message("m-student", "student", sent, "author", "2026-10-01T10:00:00+00:00")
        # The instructor's reply carries the screenshot along and adds a picture of its own; the student's answer carries both,
        # and the photograph once more.
        message("m-reply", "instructor", [sent[2], key], "to", "2026-10-01T10:05:00+00:00")
        message("m-again", "student", [sent[2], key, sent[0]], "author", "2026-10-01T10:10:00+00:00")
        read = lambda message, position: ("Screenshot.png", "image/png", screenshot) if position == 2 else ("Alice Example.jpg", "image/jpeg", photo)  # noqa: E731
        self.enterContext(patch.object(self.app.extensions["communications"], "attachment", side_effect=read))
        self.assertEqual(self.search.status()["waiting"], 2)  # the photograph and the screenshot, each once; not the icon or the clip
        self.assertEqual(self.index_all(), 2)
        self.assertEqual(self.search.index.refs("mail"), {"m-student:0#1", "m-student:2#1"})  # each where it was first sent
        hit = self.find("look 4")[0]
        self.assertEqual((hit["kind"], hit["label"], hit["matched"]), ("From a student", "Attachment a student sent", "look"))
        self.assertEqual((hit["where"], hit["link"]), ("Mail · sent 2026-10-01", "#mail?thread=19abc0de"))
        self.assertEqual(hit["image"], "/api/communications/messages/m-student/attachments/0")
        self.assertNotIn("Alice", json.dumps(hit))
        # On three messages, found once; and the picture the instructor sent first is not a student's, whoever carries it back.
        self.assertEqual([found["image"] for found in self.find("look 5") if found["score"] == 1.0], ["/api/communications/messages/m-student/attachments/2"])
        with connect(self.path) as db:
            listed = picture_sources.StudentAttachments(None)._attachments(db)
        self.assertEqual({row["sha256"] for row in listed}, {digest, "c" * 64})

        # The same file kept as an absence's documentation is that absence's, in every copy, and is listed once.
        absence = self.record().json
        with connect(self.path) as db:
            db.execute("""INSERT INTO absence_documents(id, absence_id, kind, name, content_type, size, sha256, path, message_id, attachment_index,
                request_id, actor, created_at) VALUES('doc-1',?,'message','Alice Example.jpg','image/jpeg',50000,?,NULL,'m-student',0,'d1','workspace',?)""",
                       (absence["id"], digest, NOW))
        self.assertEqual(self.index_all(), 1)
        self.assertEqual(self.search.status()["waiting"], 0)  # its later copy on the student's answer is not listed again
        kinds = [found["kind"] for found in self.find("look 4") if found["score"] == 1.0]
        self.assertEqual(kinds, ["Absence documentation"])


class RememberedPictures(SourcesTests):
    def test_a_remembered_picture_is_found_by_what_was_said_about_it_until_it_is_erased(self):
        memory = self.app.extensions["assistant"].memory
        from test_assistant_memory import answering  # EVERY WAY IN: what the form saves is checked as a chat message is
        with answering():
            kept = memory.write({"content": "This is the board layout I use for the inequalities review."})
        content = png(320, 240, 189)
        with connect(self.path) as db:
            db.execute("""INSERT INTO assistant_memory_pictures(id, memory_id, position, media_type, sha256, size, image, created_at)
                VALUES('pic-1', ?, 0, 'image/png', ?, ?, ?, ?)""", (kept["id"], hashlib.sha256(content).hexdigest(), len(content), content, NOW))
        self.assertEqual(self.index_all(), 1)
        self.assertEqual(self.search.status()["by_source"], {"memory": 1})
        self.assertEqual(self.model.asked[-1], ("document", ["This is the board layout I use for the inequalities review."]))
        hit = self.find("inequalities")[0]
        self.assertEqual((hit["kind"], hit["matched"], hit["label"], hit["link"], hit["memory_id"]),
                         ("Remembered picture", "words", "This is the board layout I use for the inequalities review.", "", kept["id"]))
        self.assertEqual(hit["image"], "/api/assistant/memory-pictures/pic-1")
        self.assertEqual(self.client.get(hit["thumb"]).mimetype, "image/png")
        # By a picture: the one the Assistant's memory asks with, on this source alone.
        alike = self.search.like(png(300, 200, 189), 3, ["memory"], timeout=2)
        self.assertEqual([(found["memory_id"], found["score"]) for found in alike["results"]], [(kept["id"], 1.0)])
        # Removed: no longer found. Erased: its bytes are gone, and an idle turn drops what the index held of it.
        memory.remove(kept["id"])
        self.assertEqual(self.find("inequalities"), [])
        memory.restore(kept["id"])
        self.assertEqual(len(self.find("inequalities")), 1)
        memory.erase(kept["id"])
        self.assertEqual(self.find("inequalities"), [])
        self.search._wait_until = self.search._tidy_after = 0
        self.assertFalse(self.search.process_one())
        self.assertEqual(self.search.index.refs("memory"), set())
        self.assertEqual(self.client.get("/api/assistant/memory-pictures/pic-1").status_code, 404)


for _name in [n for n in vars(SourcesTests) if n.startswith("test_") and n not in vars(RememberedPictures)]:
    setattr(RememberedPictures, _name, None)  # SourcesTests' own tests run once, above
del _name


class ConversionTests(Base):
    def test_a_picture_is_made_upright_and_flat_and_a_file_that_is_no_picture_says_so(self):
        out = io.BytesIO()
        Image.new("RGBA", (40, 30), (255, 0, 0, 0)).save(out, "WEBP")
        flat = Image.open(io.BytesIO(picture_sources.as_picture(out.getvalue())))
        self.assertEqual((flat.format, flat.mode, flat.getpixel((0, 0))), ("PNG", "RGB", (255, 255, 255)))
        for content in (b"not a picture", b"\x00\x00\x00\x14ftypqt  \x00\x00\x00\x00qt  "):
            with self.assertRaises(picture_sources.NotAPicture):
                picture_sources.as_picture(content)
        self.assertTrue(picture_sources.is_pdf(b"%PDF-1.7\n"))
        self.assertEqual(picture_sources.viewer_page("/api/x/y.pdf", 3), "/api/viewer/page.png?route=%2Fapi%2Fx%2Fy.pdf&page=3")

    def test_a_file_that_is_no_picture_is_set_aside_at_once(self):
        search = self.app.extensions["picture_search"]
        absence = self.record().json
        model = StandIn()
        for name in ("picture_vectors", "text_vectors", "status"):
            self.enterContext(patch.object(embedding, name, getattr(model, name)))
        self.enterContext(patch.object(embedding, "configured", return_value=True))
        sent = self.client.post(f"/api/absences/{absence['id']}/documents", headers=HEADERS,
                                data={"file": (io.BytesIO(png(300, 400, 183)), "note.png"), "request_id": "u1"})
        self.assertEqual(sent.status_code, 200, sent.json)
        with patch.object(self.app.extensions["absences"], "read_document", return_value=("clip.mov", "video/quicktime", b"\x00\x00\x00\x14ftypqt  ")):
            self.assertTrue(search.process_one())
        self.assertEqual((search.status()["indexed"], search.status()["unreadable"], search.status()["waiting"]), (0, 1, 0))
        self.assertEqual(model.pictures, [])


if __name__ == "__main__":
    import unittest
    unittest.main()
