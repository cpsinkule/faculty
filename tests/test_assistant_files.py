"""The assistant fetches files (requirement, 2026-09-30): `fetch_files` shows what a GET route serves in the drawer by its
checked route and sends pictures to the model only when it asks to look (photos never); sign-in and export routes stay closed;
`student_paper` finds one student's scanned paper; the MCP proxy and the hosted loop carry the pictures to the model."""
import base64
import io
import json
import os
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlencode

from flask import Response
from PIL import Image

from app import create_app
from assistant import files
from grading import config
from rosters import connect
import test_exam_workflow
from test_assistant import settle
from test_storage import HEADERS

FIXTURE_PDF = Path(__file__).resolve().parent.parent / "document_identity" / "fixtures" / "letter.pdf"


def png(width, height, shade=200):
    buffer = io.BytesIO()
    Image.new("L", (width, height), shade).save(buffer, "PNG")
    return buffer.getvalue()


W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
DOCX = (f'<w:document {W}><w:body><w:p><w:r><w:t>Math 140 Agenda</w:t></w:r></w:p><w:p/><w:p><w:r><w:t>1. Exam 3 rooms</w:t><w:tab/>'
        '<w:t>HELD 111</w:t></w:r></w:p><w:p><w:r><w:t xml:space="preserve">2. Final exam format: </w:t></w:r><w:r><w:t>vote by Friday</w:t></w:r></w:p>'
        '<w:tbl><w:tr><w:tc><w:p><w:r><w:t>Section</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>Proctor</w:t></w:r></w:p></w:tc></w:tr>'
        '<w:tr><w:tc><w:p><w:r><w:t>507</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>Sam Park</w:t></w:r></w:p></w:tc></w:tr></w:tbl>'
        '<w:sectPr/></w:body></w:document>')
X = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
WORKBOOK = (f'<workbook {X} xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>'
            '<sheet name="Counts" sheetId="1" r:id="rId1"/></sheets></workbook>')
WORKBOOK_RELS = ('<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                 '<Relationship Id="rId1" Type="worksheet" Target="worksheets/sheet1.xml"/></Relationships>')
STRINGS = f'<sst {X}><si><t>Section</t></si><si><r><t>Testing </t></r><r><t>center</t></r></si></sst>'
SHEET = (f'<worksheet {X}><sheetData><row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c></row>'
         '<row r="2"><c r="A2"><v>507</v></c><c r="B2"><f>1+2</f><v>3</v></c></row><row r="3"><c r="A3"><v>508</v></c><c r="C3"><v>2</v></c></row>'
         '<row r="4"><c r="A4"/></row></sheetData></worksheet>')


def office_file(parts):
    """A Word or Excel file as it is: a zip of XML parts (synthetic; nobody's document)."""
    import zipfile
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, text in parts.items():
            archive.writestr(name, text)
    return buffer.getvalue()


class Files(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(settle)  # a turn's thread saves after it stops being busy: it has ended before the folder is removed
        self.app = create_app(os.path.join(self.temp.name, "data"))
        self.app.testing = True
        # Test routes that answer as file routes do: a picture, a PDF, text, data and a failure.
        self.app.add_url_rule("/api/test-files/wide.png", "test_files.wide", lambda: Response(png(3136, 1000), mimetype="image/png"))
        self.app.add_url_rule("/api/test-files/paper.pdf", "test_files.pdf", lambda: Response(FIXTURE_PDF.read_bytes(), mimetype="application/pdf",
                              headers={"Content-Disposition": 'attachment; filename="reviewed-paper.pdf"'}))
        self.app.add_url_rule("/api/test-files/note.txt", "test_files.text", lambda: Response("Doctor's note: absent 9/29 to 9/30.", mimetype="text/plain"))
        self.app.add_url_rule("/api/test-files/data", "test_files.data", lambda: {"ok": True})
        self.app.add_url_rule("/api/test-files/missing.png", "test_files.missing", lambda: ({"error": "That page is not part of this scan."}, 404))
        self.client = self.app.test_client()
        self.service = self.app.extensions["assistant"]
        self.session = self.service.open_session("test")

    def fetch(self, **args):
        text, event = self.service.run_tool(self.session, "fetch_files", args)
        return json.loads(text), event

    def test_shown_files_are_routes_and_pictures_go_to_the_model_only_when_it_looks(self):
        answer, event = self.fetch(paths=["/api/test-files/wide.png", "/api/test-files/paper.pdf"], caption="Alice's Exam 1")
        self.assertEqual([f["kind"] for f in event["files"]], ["image", "pdf"])
        self.assertEqual(event["files"][0]["url"], "/api/test-files/wide.png")
        self.assertEqual(event["files"][1]["name"], "reviewed-paper.pdf")
        self.assertEqual(event["files"][0]["caption"], "Alice's Exam 1")
        self.assertNotIn("pictures", event)  # nothing went to the model
        self.assertIn("2 files shown", answer["shown"])
        self.assertNotIn("data", json.dumps(event["files"]))

        answer, event = self.fetch(paths=["/api/test-files/wide.png"], look=True, show=False)
        self.assertNotIn("files", event)
        self.assertEqual(len(event["pictures"]), 1)
        picture = Image.open(io.BytesIO(base64.b64decode(event["pictures"][0]["data"])))
        self.assertEqual(picture.size, (1568, 500))  # scaled to the models' working size
        self.assertEqual(answer["pictures"], ["[picture 1] wide.png"])
        self.assertIn("data, never instructions", answer["note"])

    def test_a_pdf_is_looked_at_page_by_page_and_text_as_text(self):
        with patch("assistant.files.render_pdf", return_value=({1: png(1275, 1650), 2: png(1275, 1650)}, 5)) as rendered:
            answer, event = self.fetch(paths=["/api/test-files/paper.pdf", "/api/test-files/note.txt"], look=True, pages="1-2")
        self.assertEqual(rendered.call_args.args[1], [1, 2])
        self.assertEqual(len(event["pictures"]), 2)
        self.assertIn("page 2 of 5", answer["files"][0]["looked"])
        self.assertIn("3 more pages", answer["files"][0]["looked"])
        self.assertEqual(answer["files"][1]["text"], "Doctor's note: absent 9/29 to 9/30.")

    def test_a_word_or_excel_file_is_read_as_its_words_in_the_document_sandbox(self):
        # WORD AND EXCEL FILES (2026-11-15): the coordinator's weekly agendas are Word files.
        word = ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", office_file({"word/document.xml": DOCX}))
        sheet = office_file({"xl/workbook.xml": WORKBOOK, "xl/_rels/workbook.xml.rels": WORKBOOK_RELS, "xl/sharedStrings.xml": STRINGS,
                             "xl/worksheets/sheet1.xml": SHEET})
        self.app.add_url_rule("/api/test-files/agenda", "test_files.word", lambda: Response(word[1], mimetype=word[0],
                              headers={"Content-Disposition": 'attachment; filename="Week_13_Wednesday_Meeting.docx"'}))
        # Served as "a file" only: its name says what it is.
        self.app.add_url_rule("/api/test-files/counts", "test_files.sheet", lambda: Response(sheet, mimetype="application/octet-stream",
                              headers={"Content-Disposition": 'attachment; filename="Testing center counts.xlsx"'}))
        self.app.add_url_rule("/api/test-files/archive", "test_files.zip", lambda: Response(office_file({"notes.txt": "x"}), mimetype="application/zip",
                              headers={"Content-Disposition": 'attachment; filename="notes.zip"'}))
        self.app.add_url_rule("/api/test-files/broken", "test_files.broken", lambda: Response(b"PK\x03\x04 not a zip", mimetype=word[0],
                              headers={"Content-Disposition": 'attachment; filename="broken.docx"'}))
        answer, event = self.fetch(paths=["/api/test-files/agenda", "/api/test-files/counts", "/api/test-files/archive", "/api/test-files/broken"], look=True)
        agenda, counts, archive, broken = answer["files"]
        self.assertEqual(agenda["text"], "Math 140 Agenda\n\n1. Exam 3 rooms\tHELD 111\n2. Final exam format: vote by Friday\nSection | Proctor\n507 | Sam Park")
        self.assertIn("its words below", agenda["looked"])
        self.assertEqual((agenda["kind"], agenda["name"]), ("file", "Week_13_Wednesday_Meeting.docx"))
        self.assertEqual(counts["text"], "Sheet: Counts\nSection\tTesting center\n507\t3\n508\t\t2")
        self.assertEqual((archive["looked"], "text" in archive), ("no: this kind of file cannot be looked at; it can still be shown", False))
        self.assertTrue(broken["looked"].startswith("no: "), broken)
        self.assertNotIn("pictures", event)
        # Shown only, nothing is read.
        shown, _ = self.fetch(paths=["/api/test-files/agenda"])
        self.assertNotIn("text", shown["files"][0])

    def test_pdf_pages_are_rendered_in_the_document_sandbox(self):
        rendered, total = files.render_pdf(FIXTURE_PDF.read_bytes(), [1, 9])  # page 9 is past the end: left out
        self.assertEqual((sorted(rendered), total), ([1], 3))
        width, height = Image.open(io.BytesIO(rendered[1])).size
        self.assertEqual(width, 1275)  # letter at 150 DPI
        self.assertLessEqual(abs(height - 1650), 1)

    def test_a_heic_photo_is_a_picture_decoded_in_the_document_sandbox(self):
        import pillow_heif  # encodes the fixture only
        buffer = io.BytesIO()
        pillow_heif.from_pillow(Image.new("RGB", (2000, 3000), (200, 10, 10))).save(buffer, quality=50)
        heic = buffer.getvalue()
        self.assertEqual(files.kind_of(heic, "application/octet-stream"), ("image", "image/heic"))
        with patch.object(files.process, "heif_picture", wraps=files.process.heif_picture) as decoded:
            media, data = files.picture(heic)
        self.assertEqual(decoded.call_args.args[1], files.LONG_EDGE)
        self.assertIn(media, ("image/png", "image/jpeg"))
        self.assertEqual(Image.open(io.BytesIO(data)).size, (1045, files.LONG_EDGE))

    def test_data_routes_failures_and_closed_routes_are_said_never_shown(self):
        answer, event = self.fetch(paths=["/api/test-files/data", "/api/test-files/missing.png", "/api/export",
                                          "/api/response-cards/cards/k/link", "/api/assistant/actions", "https://example.com/x.png"])
        errors = [f["error"] for f in answer["files"]]
        self.assertIn("faculty_get", errors[0])
        self.assertEqual(errors[1], "That page is not part of this scan.")
        self.assertTrue(all("does not fetch" in e for e in errors[2:5]), errors)
        self.assertIn("/api/", errors[5])
        self.assertNotIn("files", event)
        self.assertFalse(event["ok"])
        self.assertIn("error", self.fetch(paths=[])[0])
        self.assertIn("error", self.fetch(paths=["/api/test-files/wide.png"], show=False)[0])

    def test_the_route_list_names_file_routes(self):
        routes = {r["endpoint"]: r["assistant"] for r in self.service.route_catalog()["routes"] if r["method"] == "GET"}
        self.assertEqual((routes["student_photos.photo"], routes["absences.document"], routes["export"], routes["documents.scan_page_image"]),
                         ("file", "file", "never", "read"))

    def test_photos_are_shown_never_sent_and_hidden_grades_stay_hidden(self):
        real = self.service.transport.fetch

        def photo(path, query=None):
            if path.startswith("/api/student-photos/"):
                return "student_photos.photo", path, 200, "image/jpeg", png(256, 320), ""
            return real(path, query)
        with patch.object(self.service.transport, "fetch", side_effect=photo):
            answer, event = self.fetch(paths=["/api/student-photos/1234567890"], look=True)
        self.assertEqual(event["files"][0]["kind"], "image")
        self.assertNotIn("pictures", event)
        self.assertIn("never sent", answer["files"][0]["looked"])
        with patch.object(self.app, "test_client", wraps=self.app.test_client) as clients:
            self.fetch(paths=["/api/grading/responses/r1?reveal=1"])
        self.assertTrue(clients.called)
        self.assertNotIn("reveal", self.service.transport.fetch("/api/grading/responses/r1?reveal=1")[1])

    def test_the_mcp_route_and_proxy_carry_pictures_and_the_conversation_keeps_only_routes(self):
        import manage_assistant
        opened = self.client.post("/api/assistant/sessions", json={"engine": "external"}, headers=HEADERS).json
        called = self.client.post(f"/api/assistant/sessions/{opened['id']}/tools/fetch_files",
                                  json={"arguments": {"paths": ["/api/test-files/wide.png"], "look": True}},
                                  headers={**HEADERS, "X-Assistant-Token": opened["token"]}).json
        self.assertEqual(len(called["images"]), 1)
        self.assertNotIn("pictures", called["event"])
        self.assertEqual(called["event"]["files"][0]["url"], "/api/test-files/wide.png")

        class Faculty:
            def tools(self):
                return []

            def run(self, name, arguments):
                return called["text"], False, called["images"]
        out = io.StringIO()
        manage_assistant.serve(Faculty(), io.StringIO(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                                                  "params": {"name": "fetch_files", "arguments": {}}}) + "\n"), out)
        content = json.loads(out.getvalue())["result"]["content"]
        self.assertEqual([c["type"] for c in content], ["text", "image"])
        self.assertEqual(content[1]["mimeType"], "image/png")

    def test_the_hosted_loop_sends_pictures_after_the_tool_results_and_drops_them_next_turn(self):
        from assistant.conversations import Conversation
        endpoint = {**config.ENDPOINT, "name": "helper", "kind": "hosted", "base_url": "https://llm.example.test/v1", "key_env": "EXAMPLE_API_KEY",
                    "model": "some/model", "aggregator": False, "reasoning_choice": "off"}
        answers = iter([
            {"choices": [{"message": {"content": "", "tool_calls": [{"id": "t1", "function": {"name": "fetch_files", "arguments": json.dumps(
                {"paths": ["/api/test-files/wide.png"], "look": True})}}]}}], "usage": {}},
            {"choices": [{"message": {"content": "Question 3 reads x = 4."}}], "usage": {}},
            {"choices": [{"message": {"content": "Yes."}}], "usage": {}}])
        sent = []

        def fake(url, *, headers, timeout, body, usage_option):
            sent.append(json.loads(json.dumps(body)))
            return next(answers)

        def wait(conversation):
            for _ in range(250):
                if not conversation.busy:
                    return
                time.sleep(0.02)
        with patch.dict(os.environ, {"EXAMPLE_API_KEY": "test"}), patch("assistant.engines.request_stream", side_effect=fake):
            conversation = Conversation(self.service, self.session, endpoint, "http://127.0.0.1:1", "#overview")
            conversation.send("What did Alice write for question 3?")
            wait(conversation)
            conversation.send("Is that right?")
            wait(conversation)
        last = sent[1]["messages"][-1]
        self.assertEqual(last["role"], "user")
        self.assertEqual([part["type"] for part in last["content"]], ["text", "image_url"])
        self.assertTrue(last["content"][1]["image_url"]["url"].startswith("data:image/png;base64,"))
        self.assertEqual(sent[1]["messages"][-2]["role"], "tool")
        later = [m for m in sent[2]["messages"] if m["role"] == "user" and "fetch_files sent earlier" in str(m["content"])]
        self.assertEqual(len(later), 1)
        self.assertFalse(any(isinstance(m.get("content"), list) for m in sent[2]["messages"]))
        tool = next(e for e in conversation.events if e["kind"] == "tool")
        self.assertEqual(tool["files"][0]["url"], "/api/test-files/wide.png")
        self.assertNotIn("pictures", tool)


    def test_pictures_sent_with_a_turn_reach_the_model_and_its_words_stay_on_the_next(self):
        """REGRADES "FOLLOW-UP": a regrade preparation's crops go with its turn (`send(images=...)`); a later turn keeps the words."""
        from assistant.conversations import Conversation
        endpoint = {**config.ENDPOINT, "name": "helper", "kind": "hosted", "base_url": "https://llm.example.test/v1", "key_env": "EXAMPLE_API_KEY",
                    "model": "some/model", "aggregator": False, "reasoning_choice": "off"}
        answers = iter([{"choices": [{"message": {"content": "Keep the grade."}}], "usage": {}}, {"choices": [{"message": {"content": "Yes."}}], "usage": {}}])
        sent = []

        def fake(url, *, headers, timeout, body, usage_option):
            sent.append(json.loads(json.dumps(body)))
            return next(answers)

        def wait(conversation):
            for _ in range(250):
                if not conversation.busy:
                    return
                time.sleep(0.02)
        with patch.dict(os.environ, {"EXAMPLE_API_KEY": "test"}), patch("assistant.engines.request_stream", side_effect=fake):
            conversation = Conversation(self.service, self.session, endpoint, "http://127.0.0.1:1", "#overview")
            conversation.send("REGRADE REQUEST facts", internal=True, images=[("image/png", b"\x89PNG crop")])
            wait(conversation)
            conversation.send("And now?")
            wait(conversation)
        first = sent[0]["messages"][-1]
        self.assertEqual([part["type"] for part in first["content"]], ["text", "image_url"])
        self.assertIn("REGRADE REQUEST facts", first["content"][0]["text"])
        self.assertTrue(first["content"][1]["image_url"]["url"].startswith("data:image/png;base64,"))
        earlier = [m for m in sent[1]["messages"] if m["role"] == "user" and "REGRADE REQUEST facts" in str(m["content"])]
        self.assertEqual(len(earlier), 1)
        self.assertIn("no longer attached", earlier[0]["content"])
        self.assertFalse(any(isinstance(m.get("content"), list) for m in sent[1]["messages"]))

def copy_version(case, copy):
    """The label of the version a printed copy was printed from."""
    with connect(case.path) as db:
        return db.execute("""SELECT v.label FROM printed_copies c JOIN print_batches b ON b.id=c.print_batch_id
            JOIN assignment_versions v ON v.id=b.assignment_version_id WHERE c.id=?""", (copy["id"],)).fetchone()[0]


# The exam-workflow fixture (a course, Alice and Bob, Exam 1 and its record) without its tests.
Fixture = type("Fixture", (unittest.TestCase,), {k: v for k, v in test_exam_workflow.ExamWorkflowTests.__dict__.items() if not k.startswith("test_")})


class StudentPaper(Fixture):
    def scan(self, copy, numbers):
        job = self.inbox.submit({"action": "scan.receive"}, [{"filename": "scan.pdf", "content": b"%PDF-fixture " + uuid.uuid4().bytes}],
                                source="email", sender="scanner", original=b"retained email", idempotency_key=str(uuid.uuid4()), authenticated=True,
                                instructor_review_required=True)
        source = next(a for a in job["artifacts"] if a["role"] == "input")
        with connect(self.path) as db:
            stamps = {p["page_number"]: dict(p) for p in db.execute("SELECT * FROM page_stamps WHERE printed_copy_id=?", (copy["id"],))}
            db.execute("UPDATE inbox_messages SET instructor_review_required=0 WHERE id=?", (job["message_id"],))
        for index, number in enumerate(numbers, 1):
            self.scans._retain_page(job, source, {"index": index, "candidates": [{"payload": stamps[number]["token"]}], "blank": False,
                                                  "pdf": f"%PDF page {number}".encode(), "png": png(850, 1100, 180 + number), "decoder": {}})
        return job, self.scans.review_item(job["message_id"])

    def tool(self, name, **args):
        assistant = self.app.extensions["assistant"]
        text, event = assistant.run_tool(assistant.open_session("test"), name, args)
        return json.loads(text), event

    def test_a_students_paper_is_found_by_row_or_title_and_its_pages_are_fetched(self):
        batch = self.batch()
        copy = next(c for c in batch["copies"] if c["enrollment_id"] == self.alice["enrollment_id"])
        row = f"{self.section['id']}:{self.alice['enrollment_id']}"
        job, item = self.scan(copy, [1, 2])

        seen = self.tool("student_paper", student=row, assignment="exam 1")[0]
        self.assertEqual(seen["assignment"]["title"], "Exam 1")
        self.assertIsNone(seen["papers"][0]["reviewed"])
        self.assertEqual([p["page_number"] for p in seen["papers"][0]["seen"]], [1, 2])
        self.assertEqual(seen["papers"][0]["seen"][0]["job_id"], job["id"])

        self.select(copy, [item])
        found = self.tool("student_paper", student=row, assignment="Test 1")[0]  # "Test 1" and "Exam 1" are one name
        paper = found["papers"][0]
        self.assertEqual([p["page_number"] for p in paper["reviewed"]["pages"]], [1, 2])
        self.assertTrue(paper["reviewed"]["pdf"].startswith("/api/documents/assemblies/"))
        self.assertNotIn("grading", found)  # no grading plan

        answer, event = self.tool("fetch_files", paths=[p["image"] for p in paper["reviewed"]["pages"]], look=True, labels=["Page 1"])
        self.assertEqual([f["kind"] for f in event["files"]], ["image", "image"])
        self.assertEqual([f["name"] for f in event["files"]], ["Page 1", "Scanned page"])
        self.assertTrue(event["files"][0]["open"].endswith("width=1700"))
        self.assertEqual(len(event["pictures"]), 2)
        self.assertEqual(Image.open(io.BytesIO(base64.b64decode(event["pictures"][0]["data"]))).size, (850, 1100))

        bob = self.tool("student_paper", student=f"{self.section['id']}:{next(p for p in self.event['participants'] if p['name'] == 'Example, Bob')['enrollment_id']}",
                        assignment=self.assignment["id"])[0]
        self.assertEqual(bob["papers"], [])
        self.assertIn("No paper", bob["note"])
        self.assertIn("error", self.tool("student_paper", student=row, assignment="quiz 9")[0])
        route = self.client.get(f"/api/documents/papers?enrollment_id={self.alice['enrollment_id']}&assignment_id={self.assignment['id']}")
        self.assertEqual(route.status_code, 200)
        self.assertEqual(route.json["title"], "Exam 1")

    def test_the_mail_compose_window_lists_a_students_reviewed_papers_to_attach(self):
        """MAIL-COMPOSE-ATTACH: "Attach from Faculty" finds a current student by name and lists each paper; only a reviewed one has a
        route to attach, read through the scans' own route, named after the assignment and the student."""
        batch = self.batch()
        copy = next(c for c in batch["copies"] if c["enrollment_id"] == self.alice["enrollment_id"])
        job, item = self.scan(copy, [1, 2])
        listing = lambda q: self.client.get("/api/mail/files?" + urlencode({"kind": "papers", "q": q})).json
        before = listing("alice")
        self.assertEqual(len(before["groups"]), 1, before)
        self.assertTrue(before["groups"][0]["label"].startswith("Example, Alice · "))
        self.assertEqual([(i["route"], i["detail"]) for i in before["groups"][0]["items"]], [(None, f"{copy_version(self, copy)} · not reviewed yet")])
        self.select(copy, [item])
        self.assertEqual(listing("ali zed")["groups"], [])  # every word must start a word of the name
        paper = listing("example ali")["groups"][0]["items"][0]
        self.assertTrue(paper["route"].startswith("/api/documents/assemblies/"))
        self.assertEqual(paper["name"], "Exam 1 - Example, Alice.pdf")
        self.assertEqual(listing("nobody here")["note"], "No current student matches.")
        self.assertNotIn(self.alice["enrollment_id"], json.dumps(listing("alice")))  # no identifier is shown

    def test_a_graded_paper_names_each_question_and_its_crops(self):
        batch = self.batch()
        next(c for c in batch["copies"] if c["enrollment_id"] == self.alice["enrollment_id"])
        row = f"{self.section['id']}:{self.alice['enrollment_id']}"
        with connect(self.path) as db:
            db.execute("INSERT INTO frq_assignments(id,event_id,version,created_at,updated_at) VALUES('plan1',?,1,'t','t')", (self.event["id"],))
        drawer = {"student": {"name": "Example, Alice", "exam_version": "A", "paper_version": {"status": "confirmed"}, "collection": "collected"},
                  "parts": {"frq": {"final": 1, "expected": 1}}, "certified": None,
                  "questions": [{"printed_number": "3", "title": "Question 3", "version": "A", "score": "4", "max": "6", "state": "final",
                                 "decided": {"kind": "automatic"}, "flags": {"names": []}, "response_id": "r3"}]}
        response = {"hidden": False, "crops": [{"images": {"original": {"url": "/api/inbox/artifacts/c1/download"}}}],
                    "result": {"transcript": {"lines": [{"elements": [{"text": "x = 4"}, {"text": "x = 5", "state": "crossed_out"}]}]},
                               "applications": [{"rubric_item_id": "R1", "applied": True}, {"rubric_item_id": "R2", "applied": False}], "score": {"score": "4"}}}
        from assistant.service import ToolContext
        real = ToolContext.get

        def get(context, path, query=None):
            if path.endswith(f"/students/{self.section['id']}/{self.alice['enrollment_id']}"):
                return drawer
            if path == "/api/grading/responses/r3":
                return response
            return real(context, path, query)
        with patch.object(ToolContext, "get", get):
            found = self.tool("student_paper", student=row, assignment="plan1", question="3")[0]
            response["hidden"] = True
            blind = self.tool("student_paper", student=row, assignment="plan1", question="3")[0]
        self.assertEqual(found["student"]["name"], "Example, Alice")
        self.assertEqual(found["grading"]["questions"][0]["score"], "4")
        detail = found["question_detail"][0]
        self.assertEqual(detail["crops"], ["/api/inbox/artifacts/c1/download"])
        self.assertEqual(detail["transcript"], ["x = 4 [crossed out: x = 5]"])
        self.assertEqual(detail["lines_ticked"], ["R1"])
        self.assertEqual((blind["question_detail"][0]["score"], blind["question_detail"][0]["lines_ticked"]), (None, []))
        self.assertTrue(blind["question_detail"][0]["grade_hidden_for_blind_review"])


if __name__ == "__main__":
    unittest.main()
