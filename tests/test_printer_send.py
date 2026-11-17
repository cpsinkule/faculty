"""Send to printer (DOCUMENTS "SEND TO PRINTER") against a fake IPP printer on loopback; no real printer is reached."""
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import struct
import tempfile
import threading
import unittest
import uuid
from unittest.mock import patch

from app import create_app
from document_identity import ipp, printer as printer_module
from document_identity.printer import PrinterConfig
from rosters import connect
from test_storage import HEADERS


class FakePrinter:
    """Answers Get-Printer-Attributes, Create-Job, Send-Document and Cancel-Job like the office Brother does."""

    def __init__(self):
        self.calls, self.documents, self.mode, self.lock = [], [], "ok", threading.Lock()
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                operation = struct.unpack(">H", body[2:4])[0]
                _, groups = ipp.decode(body[:2] + b"\x00\x00" + body[4:])
                attributes = groups.get(ipp.OPERATION, {})
                start = body.find(b"%PDF")  # the document follows the attributes
                document = body[start:] if start >= 0 else b""
                with fake.lock:
                    fake.calls.append((operation, attributes, groups.get(ipp.JOB, {})))
                if operation == ipp.SEND_DOCUMENT and fake.mode == "drop":
                    self.connection.close()
                    return
                status, extra = 0, b""
                if operation == ipp.GET_PRINTER_ATTRIBUTES:
                    extra = bytes([ipp.PRINTER_GROUP]) + attr(ipp.ENUM, "printer-state", struct.pack(">i", 3)) + \
                        attr(ipp.TEXT, "printer-make-and-model", b"Brother MFC-L5915DW series") + attr(ipp.KEYWORD, "printer-state-reasons", b"none")
                elif operation == ipp.CREATE_JOB:
                    if fake.mode == "refuse":
                        status = 0x0507
                    else:
                        extra = bytes([ipp.JOB]) + attr(ipp.INTEGER, "job-id", struct.pack(">i", 41))
                elif operation == ipp.SEND_DOCUMENT:
                    with fake.lock:
                        fake.documents.append(document)
                answer = struct.pack(">BBHI", 1, 1, status, 1) + bytes([ipp.OPERATION]) + attr(ipp.CHARSET, "attributes-charset", b"utf-8") + \
                    attr(ipp.LANGUAGE, "attributes-natural-language", b"en") + extra + b"\x03"
                if operation == ipp.SEND_DOCUMENT and fake.mode == 'malformed':
                    answer = answer[:-1]
                self.send_response(200)
                self.send_header("Content-Type", "application/ipp")
                self.send_header("Content-Length", str(len(answer)))
                self.end_headers()
                self.wfile.write(answer)

        def attr(tag, name, value):
            return struct.pack(">BH", tag, len(name)) + name.encode() + struct.pack(">H", len(value)) + value

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.uri = f"ipp://127.0.0.1:{self.server.server_port}/ipp/print"

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    def operations(self):
        return [call[0] for call in self.calls if call[0] != ipp.GET_PRINTER_ATTRIBUTES]


class IppParsingTests(unittest.TestCase):
    def test_every_truncated_packet_is_a_protocol_error(self):
        packet = ipp.encode(0, [('printer-state', ipp.ENUM, 3)])
        for end in range(len(packet)):
            with self.subTest(end=end), self.assertRaises(ipp.IppError):
                ipp.decode(packet[:end])
        self.assertEqual(ipp.decode(packet)[0], 0)


class PrinterSendTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app = create_app(self.temp.name)
        self.client = self.app.test_client()
        self.client.get("/").close()
        response = self.client.post("/api/courses", json={"name": "Calculus", "code": "MATH 140", "term": "Fall", "year": 2026, "sections": [507], "is_current": True}, headers=HEADERS)
        self.assertEqual(response.status_code, 201)
        self.fake = FakePrinter()
        self.addCleanup(self.fake.close)
        self.documents = self.app.extensions["documents"]
        self.sends = self.app.extensions["printer_sends"]
        self.sends.config = PrinterConfig(self.fake.uri)
        self.path = self.app.config["DATABASE"]
        catalog = self.documents.catalog()
        assignment = self.documents.create_assignment(catalog["definitions"][0]["id"], "printer-test", "Printer test")
        version = self.client.post("/api/documents/versions/fixture", json={"assignment_id": assignment["id"], "label": "A", "paper": "letter"}, headers=HEADERS).json
        self.batch = self.documents.create_batch(version["id"], catalog["sections"][0]["id"], 3, str(uuid.uuid4()))
        self.documents.build_one()
        self.batch = self.documents.get_batch(self.batch["id"])
        self.assertEqual(self.batch["status"], "ready")
        self.query = f"/api/documents/printer?kind=job&batches={self.batch['id']}"

    def body(self, sides="one-sided", request_id=None, outputs=None):
        return {"kind": "job", "batch_ids": [self.batch["id"]], "sides": sides, "request_id": request_id or str(uuid.uuid4()),
                "outputs": outputs or [{"id": self.batch["id"], "output_sha256": self.batch["output_sha256"]}]}

    def row(self, send_id):
        with connect(self.path) as db:
            return dict(db.execute("SELECT * FROM print_printer_sends WHERE id=?", (send_id,)).fetchone())

    def test_options_read_the_printer_and_the_file(self):
        options = self.client.get(self.query).json
        self.assertTrue(options["configured"])
        self.assertEqual(options["printer"], {"reachable": True, "name": "Brother MFC-L5915DW series", "state": "idle", "reasons": [], "accepting": True})
        self.assertEqual(options["pages"], 3 * self.batch["page_count"])
        self.assertEqual(options["copies"], 3)
        self.assertEqual(options["two_sided_allowed"], self.batch["page_count"] % 2 == 0)
        self.assertEqual(options["outputs"], [{"id": self.batch["id"], "output_sha256": self.batch["output_sha256"]}])
        self.assertEqual(self.fake.operations(), [])  # opening the window prints nothing

    def test_send_prints_the_verified_pdf_once_with_a_receipt(self):
        request_id = str(uuid.uuid4())
        with ThreadPoolExecutor(max_workers=4) as pool:
            answers = list(pool.map(lambda _: self.client.post("/api/documents/printer", json=self.body(request_id=request_id), headers=HEADERS), range(4)))
        self.assertEqual({a.status_code for a in answers}, {202})
        self.assertEqual(len({a.json["id"] for a in answers}), 1)
        send_id = answers[0].json["id"]
        self.assertTrue(self.sends.process_one())
        self.assertFalse(self.sends.process_one())
        self.assertEqual(self.fake.operations(), [ipp.CREATE_JOB, ipp.SEND_DOCUMENT])
        self.assertEqual(self.fake.documents, [self.documents.download(self.batch["id"])])
        create = next(call for call in self.fake.calls if call[0] == ipp.CREATE_JOB)
        self.assertEqual(create[2]["sides"], ["one-sided"])
        self.assertEqual(create[2]["copies"], [1])
        row = self.row(send_id)
        self.assertEqual(row["status"], "accepted")
        self.assertIn('"job_id":41', row["printer_receipt_json"])
        # The same request ID returns the same send; a different command under it is refused.
        self.assertEqual(self.client.post("/api/documents/printer", json=self.body(request_id=request_id), headers=HEADERS).json["id"], send_id)
        self.assertEqual(self.client.post("/api/documents/printer", json=self.body("two-sided-long-edge", request_id), headers=HEADERS).status_code, 409)
        history = self.client.get(self.query).json["sends"]
        self.assertEqual([item["status"] for item in history], ["accepted"])

    def test_writes_need_the_workspace_origin_and_current_outputs(self):
        fresh = self.app.test_client()
        self.assertEqual(fresh.post("/api/documents/printer", json=self.body()).status_code, 403)
        stale = self.client.post("/api/documents/printer", json=self.body(outputs=[{"id": self.batch["id"], "output_sha256": "0" * 64}]), headers=HEADERS)
        self.assertEqual(stale.status_code, 409)
        if self.batch["page_count"] % 2:
            self.assertEqual(self.client.post("/api/documents/printer", json=self.body("two-sided-long-edge"), headers=HEADERS).status_code, 400)
        self.assertEqual(self.client.post("/api/documents/printer", json={**self.body(), "sides": "two-sided-short-edge"}, headers=HEADERS).status_code, 400)
        with connect(self.path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM print_printer_sends").fetchone()[0], 0)

    def test_unconfigured_or_public_printer_never_queues(self):
        for uri in ("", "ipp://8.8.8.8/ipp/print", "ipps://192.0.2.201/ipp/print", "http://192.0.2.201:631/ipp/print", "ipp://user:pw@192.0.2.201/ipp/print"):
            with self.subTest(uri=uri):
                self.sends.config = PrinterConfig(uri)
                self.assertFalse(self.client.get(self.query).json["configured"])
                self.assertEqual(self.client.post("/api/documents/printer", json=self.body(), headers=HEADERS).status_code, 503)
        self.assertTrue(PrinterConfig("ipp://192.0.2.201/ipp/print").printer())
        self.assertTrue(PrinterConfig("ipp://BRWC413755DFB0C.local/ipp/print").printer())

    def test_refused_job_fails_and_dropped_send_is_never_resent(self):
        self.fake.mode = "refuse"
        first = self.client.post("/api/documents/printer", json=self.body(), headers=HEADERS).json
        self.assertTrue(self.sends.process_one())
        self.assertEqual(self.row(first["id"])["status"], "failed")
        self.assertIn("Nothing was printed", self.row(first["id"])["error"])
        self.fake.mode = "drop"
        second = self.client.post("/api/documents/printer", json=self.body(), headers=HEADERS).json
        self.assertTrue(self.sends.process_one())
        row = self.row(second["id"])
        self.assertEqual(row["status"], "uncertain")
        self.assertIn("check the printer", row["error"])
        self.assertIn(ipp.CANCEL_JOB, self.fake.operations())  # a half-sent job is cancelled, best effort
        self.fake.calls.clear()
        self.assertFalse(self.sends.process_one())
        self.assertEqual(self.fake.operations(), [])

    def test_malformed_receipt_after_sending_is_uncertain_and_never_retried(self):
        # DOCUMENTS: receiving an unreadable receipt never proves that nothing printed.
        self.fake.mode = 'malformed'
        send = self.client.post('/api/documents/printer', json=self.body(), headers=HEADERS).json
        self.assertTrue(self.sends.process_one())
        self.assertEqual(self.row(send['id'])['status'], 'uncertain')
        self.fake.calls.clear()
        self.assertFalse(self.sends.process_one())
        self.assertEqual(self.fake.operations(), [])

    def test_nested_or_duplicate_print_identifiers_are_rejected_before_queuing(self):
        output = self.body()['outputs'][0]
        for changes in ({'batch_ids': [{}]}, {'batch_ids': None}, {'outputs': [{'id': [], 'output_sha256': 'a'}]},
                        {'outputs': [output, output]}):
            with self.subTest(changes=changes):
                response = self.client.post('/api/documents/printer', json={**self.body(), **changes}, headers=HEADERS)
                self.assertEqual(response.status_code, 400, response.json)
        with connect(self.path) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM print_printer_sends').fetchone()[0], 0)

    def test_interrupted_send_becomes_unconfirmed_without_resending(self):
        send = self.client.post("/api/documents/printer", json=self.body(), headers=HEADERS).json
        with connect(self.path) as db:
            db.execute("UPDATE print_printer_sends SET status='sending' WHERE id=?", (send["id"],))
        self.assertTrue(self.sends.process_one())
        self.assertEqual(self.row(send["id"])["status"], "uncertain")
        self.assertEqual(self.fake.operations(), [])

    def test_a_large_file_goes_as_parts_cut_between_copies(self):
        content = self.documents.download(self.batch["id"])
        send = self.client.post("/api/documents/printer", json=self.body(), headers=HEADERS).json
        with patch.object(printer_module, "DOCUMENT_BYTES", len(content) // 2):
            self.assertTrue(self.sends.process_one())
        self.assertEqual(self.row(send["id"])["status"], "accepted")
        self.assertEqual(self.fake.operations(), [ipp.CREATE_JOB] + [ipp.SEND_DOCUMENT] * len(self.fake.documents))
        self.assertGreater(len(self.fake.documents), 1)
        from pypdf import PdfReader
        import io
        counts = [len(PdfReader(io.BytesIO(part)).pages) for part in self.fake.documents]
        self.assertEqual(sum(counts), 3 * self.batch["page_count"])
        self.assertTrue(all(count % self.batch["page_count"] == 0 for count in counts))
        last = [call[1]["last-document"] for call in self.fake.calls if call[0] == ipp.SEND_DOCUMENT]
        self.assertEqual(last, [[False]] * (len(last) - 1) + [[True]])

    def test_records_are_retained_and_immutable(self):
        send = self.client.post("/api/documents/printer", json=self.body(), headers=HEADERS).json
        with connect(self.path) as db:
            for statement in ("DELETE FROM print_printer_sends", "UPDATE print_printer_sends SET sides='two-sided-long-edge'",
                              "DELETE FROM print_printer_send_batches", "UPDATE print_printer_send_batches SET output_sha256='x'"):
                with self.subTest(statement=statement), self.assertRaises(Exception):
                    db.execute(statement)
        self.assertEqual(self.row(send["id"])["approved_by"], "workspace")


class StudentCopyTests(unittest.TestCase):
    """QUIZZES "A COPY BY EMAIL" (requirement, 2026-11-08): one student's own stamped copy, made at once for a reply to attach, handed out once."""
    setUp = PrinterSendTests.setUp

    def students(self):
        import io
        from test_exam_workflow import student
        from test_rosters import csv_bytes
        group = self.client.get("/api/state").json["course_groups"][0]
        made = self.client.post(f"/api/course-groups/{group['id']}/sections/507/roster", headers=HEADERS, data={"file": (
            io.BytesIO(csv_bytes([student("Example, Alice", "001234567"), student("Example, Bob", "123456789")])), "roster.csv")})
        self.assertEqual(made.status_code, 201, made.json)
        with connect(self.path) as db:
            return dict(db.execute("SELECT uin,id FROM student_enrollments").fetchall())

    def copy(self, enrollment_id, request_id=None):
        return self.client.post(f"/api/documents/versions/{self.batch['assignment_version_id']}/student-copy", headers=HEADERS,
                                json={"enrollment_id": enrollment_id, "request_id": request_id or str(uuid.uuid4())})

    def send(self, batch):
        return self.client.post("/api/documents/printer", headers=HEADERS, json={"kind": "job", "batch_ids": [batch["id"]], "sides": "one-sided",
            "request_id": str(uuid.uuid4()), "outputs": [{"id": batch["id"], "output_sha256": batch["output_sha256"]}]})

    def test_a_students_own_copy_is_made_at_once_given_again_and_never_printed_once_emailed(self):
        people = self.students()
        alice, bob = people["001234567"], people["123456789"]
        made = self.copy(alice, "copy-alice")
        self.assertEqual(made.status_code, 200, made.json)
        copy = self.documents.get_batch(made.json["batch_id"])  # rendered now, not on the worker's next turn
        self.assertEqual((copy["status"], copy["quantity"], copy["section_id"], [c["enrollment_id"] for c in copy["copies"]]),
                         ("ready", 1, None, [alice]))
        self.assertEqual(made.json["route"], f"/api/documents/batches/{copy['id']}/download")
        self.assertEqual(made.json["name"], "Printer test.pdf")  # the title alone, never the version
        self.assertFalse(made.json["reused"])
        pdf = self.client.get(made.json["route"])
        self.assertEqual(pdf.status_code, 200)
        self.assertTrue(pdf.data.startswith(b"%PDF"))
        # Asked again, the same copy while it never left Faculty; a retry of the request answers the same job; Bob gets his own.
        self.assertEqual((self.copy(alice).json["batch_id"], self.copy(alice).json["reused"]), (copy["id"], True))
        self.assertEqual(self.copy(alice, "copy-alice").json["batch_id"], copy["id"])
        self.assertNotEqual(self.copy(bob).json["batch_id"], copy["id"])
        self.assertEqual(self.copy("1999999999").status_code, 400)
        # Printed, it is handed out: the next copy is a new one.
        self.assertEqual(self.send(copy).status_code, 202)
        self.assertTrue(self.sends.process_one())
        fresh = self.copy(alice).json
        self.assertNotEqual(fresh["batch_id"], copy["id"])
        self.assertFalse(fresh["reused"])
        # Emailed (Mail recorded a send with its PDF), it is never printed or exported again.
        with connect(self.path) as db:
            db.execute("""INSERT INTO gmail_mail_sends(id,request_id,request_hash,sender,to_json,cc_json,bcc_json,subject,body_text,actor,created_at)
                VALUES('mail-1','mail-1',?,'instructor@example.test','["alice@example.test"]','[]','[]','Take-home','Attached.','workspace','2026-11-08T00:00:00Z')""", ("0" * 64,))
            db.execute("""INSERT INTO gmail_mail_send_attachments VALUES('mail-1',0,?,'Printer test.pdf','application/pdf',1,?)""",
                       (fresh["route"], "0" * 64))
        emailed = self.documents.get_batch(fresh["batch_id"])
        refused = self.send(emailed)
        self.assertEqual(refused.status_code, 409)
        self.assertIn("never printed again", refused.json["error"])
        self.assertEqual(self.send(self.batch).status_code, 202)  # the class's own job prints as before
        self.assertEqual(self.copy(alice).json["batch_id"], fresh["batch_id"])  # still hers to be sent again


if __name__ == "__main__":
    unittest.main()
