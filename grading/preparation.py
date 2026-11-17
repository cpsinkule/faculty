"""Bounded source reading and optional rubric compilation on the Inbox worker."""
import json

from werkzeug.exceptions import Conflict, NotFound
from course_storage import timestamp
from rosters import connect

from . import config, rubrics
from .models import digest, encoded, text, uid
from .providers.base import ProviderError


class PreparationMixin:
    def queue_preparation(self, aid, kind, payload, request_id):
        text(request_id, "Request ID", 128, False)
        checksum = digest({"kind": kind, "payload": payload, "assignment_id": aid})
        with self.rosters.lock(), connect(self.path) as db:
            self.plan(db, aid)
            existing = db.execute("SELECT * FROM frq_preparations WHERE request_id=?", (request_id,)).fetchone()
            if existing:
                if existing["request_sha256"] != checksum:
                    raise Conflict("This preparation request ID already names different material.")
                return self.preparation(existing["id"])
            if db.execute("SELECT COUNT(*) FROM frq_preparations WHERE status IN ('pending','running')").fetchone()[0] >= 20:
                raise Conflict("Too many rubric preparations are waiting. Finish or inspect them first.")
            identifier, now = uid(), timestamp()
            db.execute("INSERT INTO frq_preparations VALUES(?,?,?,?,?,?,'pending',0,NULL,'',?,?)", (identifier, aid, kind, request_id, checksum, encoded(payload), now, now))
            self.event(db, aid, "rubric_preparation_queued", {"preparation_id": identifier, "kind": kind})
        self.inbox.wake.set()
        return self.preparation(identifier)

    def preparation(self, identifier):
        with connect(self.path) as db:
            row = db.execute("SELECT * FROM frq_preparations WHERE id=?", (identifier,)).fetchone()
            if not row:
                raise NotFound("Rubric preparation not found.")
            result = self.decode_result(row)
            result["completed_parts"] = db.execute("SELECT COUNT(*) FROM frq_preparation_parts WHERE preparation_id=?", (identifier,)).fetchone()[0]
            return result

    def compile_rubric(self, aid, qid, data):
        with self.rosters.lock(), connect(self.path) as db:
            question = self.question(db, aid, qid)
            original = text(data.get("source_text"), "Original rubric", 200000, False)
            payload = {"question_id": qid, "question_version": question["version"], "source_text": original,
                "max_points": question["content"]["max_points"], "settings": self.settings()["settings"],
                "endpoint_sha256": digest(config.endpoint("local_grader").encode())}
            format_ = data.get("format", "text")
            if format_ not in ("text", "copied", "csv", "json"):
                raise ValueError("Choose text, CSV or JSON for rubric interpretation.")
            if format_ in ("csv", "json"):
                payload["imported_rules"] = rubrics.parse(original, payload["max_points"], format_, data.get("mode", "negative"))
        return self.queue_preparation(aid, "compile", payload, data.get("request_id"))

    def retry_preparation(self, identifier):
        # Not under the worker's execution lock, which a busy worker holds (see retry_location).
        with self.rosters.lock(), connect(self.path) as db:
            row = db.execute("SELECT * FROM frq_preparations WHERE id=?", (identifier,)).fetchone()
            if not row or row["status"] != "failed" or row["attempt"] >= 2:
                raise Conflict("Only failed preparations with fewer than two retries can be retried.")
            db.execute("UPDATE frq_preparations SET status='pending',error='',attempt=attempt+1,updated_at=? WHERE id=?", (timestamp(), identifier))
        self.inbox.wake.set()
        return self.preparation(identifier)

    def process_preparation_one(self):
        with self.inbox.worker_lock():
            with self.rosters.lock(), connect(self.path) as db:
                if db.execute("UPDATE frq_preparations SET status='failed',error='Worker interrupted. Retry explicitly.',updated_at=? WHERE status='running'", (timestamp(),)).rowcount:
                    return True
                row = db.execute("SELECT * FROM frq_preparations WHERE status='pending' ORDER BY updated_at,rowid LIMIT 1").fetchone()
                if not row:
                    return False
                task = dict(row)
                payload = json.loads(task["payload_json"])
                parts = {r["part"]: json.loads(r["result_json"]) for r in db.execute("SELECT * FROM frq_preparation_parts WHERE preparation_id=?", (task["id"],))}
                db.execute("UPDATE frq_preparations SET status='running',updated_at=? WHERE id=?", (timestamp(), task["id"]))
            try:
                part, result, metadata, finished = 0, None, {}, False
                if task["kind"] == "compile":
                    if payload["endpoint_sha256"] != digest(config.endpoint("local_grader").encode()):
                        raise ProviderError("The local compiler endpoint changed. Request a new interpretation.")
                    imported = payload.get("imported_rules")
                    # Evaluation exports can contain student rows. Only parsed
                    # rubric metadata reaches the compiler, even on localhost.
                    source = encoded(imported) if imported else payload["source_text"]
                    answer = self.provider("local_grader", payload["settings"]).compile(source, payload["max_points"],
                        **({"rubric": imported} if imported else {}))
                    try:
                        rules = rubrics.compiled(answer["output"], imported, payload["max_points"]) if imported else rubrics.validate(answer["output"], payload["max_points"])
                    except ValueError as error:
                        raise ProviderError("Rubric interpretation was refused: " + str(error)) from None
                    result = {"source_text": payload["source_text"], "rules": rules, "raw": answer["raw"], "compiler": answer["metadata"]}
                    metadata, finished = answer["metadata"], True
                else:
                    from faculty_ocr import recognize_artifact
                    _, content = self.inbox.read_artifact(payload["source_artifact_id"])
                    if 0 not in parts:
                        if payload["format"] == ".pdf":
                            from document_identity.process import run
                            count = run("pages", content)["total"]
                        else:
                            count = 1
                        if not 1 <= count <= 20:
                            raise ProviderError("Rubric source must have at most 20 pages.")
                        result = {"page_count": count}
                    else:
                        count = parts[0]["page_count"]
                        part = next((i for i in range(1, count+1) if i not in parts), None)
                        if part is None:
                            result = {"source_artifact_id": payload["source_artifact_id"],
                                "source_text": "\n\n".join(parts[i]["text"] for i in range(1, count+1)),
                                "provenance": {"method": "local_glm", "pages": count, "needs_review": True}}
                            finished = True
                        else:
                            result = recognize_artifact(self.inbox, payload["source_artifact_id"], task="text", page=part)
                            if result.get("truncated"):
                                raise ProviderError("Rubric OCR was truncated. Upload a smaller source page or paste the complete text.")
                            metadata = {k: v for k, v in result.items() if k != "text"}
                with self.rosters.lock(), connect(self.path) as db:
                    if part is not None:
                        db.execute("INSERT INTO frq_preparation_parts(preparation_id,part,result_json,provider_json,created_at) VALUES(?,?,?,?,?)", (task["id"], part, encoded(result), encoded(metadata), timestamp()))
                    db.execute("UPDATE frq_preparations SET status=?,result_json=?,updated_at=? WHERE id=?",
                        ("complete" if finished else "pending", encoded(result) if finished else None, timestamp(), task["id"]))
                    if finished:
                        self.event(db, task["assignment_id"], "rubric_preparation_complete", {"preparation_id": task["id"]}, "worker")
            except Exception as error:
                safe = str(error) if isinstance(error, ProviderError) else "Rubric preparation failed validation or processing. Original material and completed pages are retained."
                with self.rosters.lock(), connect(self.path) as db:
                    db.execute("UPDATE frq_preparations SET status='failed',error=?,updated_at=? WHERE id=?", (safe, timestamp(), task["id"]))
            return True
