"""Create draft questions directly from explicit Gradescope rubric uploads, or from a model's draft the instructor accepted."""
from pathlib import Path

from werkzeug.exceptions import Conflict
from course_storage import timestamp
from rosters import connect

from . import answers, gradescope_import, locations, rubrics
from .models import digest, exam_letter, identifier, text, uid


class RubricUploadsMixin:
    def upload_rubric(self, aid, filename, content, data, *, areas=None):
        """One atomic question/rubric import per file; a browser can select many. `areas` (only Import from Gradescope, GRADESCOPE
        "IMPORTING RUBRICS FROM GRADESCOPE"): the question's boxes on Gradescope's outline as answer areas on the template imported
        from it, `{"template_id", "regions"}`; a new question starts with them, still to be checked and confirmed."""
        filename = text(filename, "Filename", 255, False)
        if Path(filename).suffix.lower() != ".csv":
            raise ValueError("Choose Gradescope rubric CSV files for automatic question creation.")
        if not content or len(content) > 10 * 1024 * 1024:
            raise ValueError("Each rubric source must contain at most 10 MiB.")
        try:
            source = text(content.decode("utf-8-sig"), "Original rubric", 200000, False)
        except UnicodeDecodeError:
            raise ValueError("Upload a UTF-8 Gradescope CSV.") from None
        exported = gradescope_import.inspect(source)
        if not exported or exported["max_points"] is None or exported["mode"] is None:
            raise ValueError("Automatic import needs a Gradescope CSV with its question total and scoring method. Re-export the rubric with that metadata.")
        rules = rubrics.parse(source, exported["max_points"], "csv")
        printed, _ = locations.hint({"content": {"title": ""}, "question_key": ""}, filename)
        printed = locations.printed_number(printed)
        # The CSV's internal rubric number does NOT establish its printed number.
        # File names are the same for every exam version, so the upload says which version they describe.
        exam_version = exam_letter(data.get("exam_version"))
        key = (f"q{printed}" if printed else "rubric-" + digest(content)[:24]) + (f"-{exam_version}" if exam_version else "")
        return self.create_draft_question(aid, filename=filename, content=content, source=source, rules=rules, max_points=exported["max_points"],
            printed=printed, exam_version=exam_version, key=key, document_id=data.get("assignment_version_id"),
            request_id=identifier(data.get("request_id")), compiler={"name": "gradescope-upload-v3"},
            title=f"Question {printed}" if printed else f"Uploaded rubric · {Path(filename).stem}", event="rubric_uploaded", areas=areas)

    def create_draft_question(self, aid, *, filename, content, source, rules, max_points, printed, exam_version, key, document_id,
                              request_id, compiler, title, event, statement="", solution="", detail=None, answer_key=None, areas=None):
        """One draft question with its rubric revision, term pin and upload receipt, from an uploaded CSV or an accepted model draft.

        Retain originals through Inbox before opening the catalog transaction.
        An interrupted publication may leave an unused source, never half a pin.
        Retrying its request reuses both source retention and the committed receipt.

        `answer_key` (requirement, 2026-10-11): the key a drafted quiz or makeup showed with its draft, saved
        with the question's first revision because the instructor approved the draft that showed it (`quiz_drafts._set_up_quiz_plan`,
        `makeup_drafts._carry_out_makeup`). Every other caller gives none and the question starts without a key, as before: a key
        read from a rubric or a blank exam stays unused until the instructor saves it. A key is evidence only; it selects no line.
        """
        answer_key = answers.clean_key(answer_key)
        with self.rosters.lock():
            with connect(self.path) as db:
                plan = self.plan(db, aid)
                versions = db.execute("SELECT id FROM assignment_versions WHERE assignment_id=? ORDER BY created_at", (plan["source_assignment_id"],)).fetchall()
                document_id = document_id or (versions[0][0] if len(versions) == 1 else None)
                if not document_id or document_id not in {row[0] for row in versions}:
                    raise ValueError("Choose the printed document version for these rubrics. Add its PDF version in Create if none exists.")
                if document_id in self.rebuilt_documents(db, aid):
                    raise Conflict("That printed version was replaced when this quiz was rebuilt; its questions are no longer on the plan. Choose its new version.")
                checksum = digest({"filename": filename, "source_sha256": digest(content), "assignment_version_id": document_id,
                                   **({"exam_version": exam_version} if exam_version else {})})
                receipt = db.execute("SELECT * FROM frq_rubric_uploads WHERE assignment_id=? AND request_id=?", (aid, request_id)).fetchone()
                if receipt and receipt["input_sha256"] != checksum:
                    raise Conflict("This upload request already belongs to a different file or document version.")
                receipt = receipt or db.execute("SELECT * FROM frq_rubric_uploads WHERE assignment_id=? AND input_sha256=?", (aid, checksum)).fetchone()
                if receipt:
                    return {"status": "already_imported", "question_id": receipt["question_id"], "rubric_id": receipt["rubric_id"]}
                graded, seated = self.plan_versions(db, aid), self.version_letters(db, plan)
                if exam_version and db.execute("""SELECT 1 FROM frq_assignment_questions p JOIN frq_questions q ON q.id=p.question_id
                        WHERE p.assignment_id=? AND q.assignment_version_id=? AND q.exam_version=''""", (aid, document_id)).fetchone():
                    raise Conflict("This assessment already has questions without an exam version. Say which exam version they describe (Rubrics → Assignment versions) before uploading another version’s rubrics.")
                if not exam_version and (graded or len(seated) > 1):
                    raise ValueError(f"This exam has versions {', '.join(graded or seated)}, and every version’s rubric files have the same names. Choose the exam version these files describe.")
                existing = db.execute("SELECT id FROM frq_questions WHERE assignment_version_id=? AND question_key=?", (document_id, key)).fetchone()
                for row in db.execute("SELECT p.question_id FROM frq_assignment_questions p JOIN frq_questions q ON q.id=p.question_id WHERE p.assignment_id=? AND q.assignment_version_id=? AND q.exam_version=?", (aid, document_id, exam_version)):
                    question = self.question(db, aid, row[0])
                    number = self.location_context(db, aid, question)["printed_number"]
                    if (existing and question["question_id"] == existing[0]) or (printed and printed == number):
                        raise Conflict(f"{question['content']['title']} already exists for {'Version ' + exam_version if exam_version else 'this document'}. Use its Import revision button to replace the rubric after review.")
                qid = existing[0] if existing else uid()
            # The lifecycle lock serializes imports, while storage owns every byte.
            retained = self.upload_source(aid, filename, content, "rubric-upload:" + digest({"assignment": aid, "request": request_id}))
            artifact = retained["source_artifact_id"]
            clean = {name: "" for name in ("problem", "expected_answer", "coordinator_solution", "instructor_solution", "worked_solution", "acceptable_methods", "notes")}
            clean.update(title=title, printed_number=printed, max_points=max_points, regions=[], template_id=None,
                examples=[], answer_key=answer_key, final_answer_region=None, problem_region=None)
            # A drafted question arrives with the statement and solution the instructor accepted; an uploaded CSV carries neither.
            clean.update(problem=statement, worked_solution=solution)
            if areas and areas.get("regions"):
                # Gradescope's boxes on the template read from Gradescope: suggested areas, checked and confirmed like any (`regions_from`).
                clean.update(template_id=areas["template_id"], regions=areas["regions"], regions_from="gradescope")
            with connect(self.path) as db:
                db.execute("BEGIN IMMEDIATE")
                now, rubric_id = timestamp(), uid()
                if not existing:
                    db.execute("INSERT INTO frq_questions(id,assignment_version_id,question_key,created_at,exam_version) VALUES(?,?,?,?,?)", (qid, document_id, key, now, exam_version))
                question_revision = self.new_question_revision(db, qid, clean, now)
                self.insert_rubric_revision(db, qid, rubric_id, source, artifact, rules, max_points, compiler, now)
                db.execute("INSERT INTO frq_assignment_questions VALUES(?,?,?,?,1)", (aid, qid, question_revision, rubric_id))
                db.execute("INSERT INTO frq_rubric_uploads VALUES(?,?,?,?,?,?,?)", (aid, request_id, checksum, qid, rubric_id, artifact, now))
                self.touch(db, aid)
                self.event(db, aid, event, {"question_id": qid, "rubric_id": rubric_id, "source_artifact_id": artifact, "printed_number": printed,
                    **({"exam_version": exam_version} if exam_version else {}), **(detail or {})})
        self.inbox.wake.set()
        return {"status": "imported", "question_id": qid, "rubric_id": rubric_id, "exam_version": exam_version,
                "printed_number": printed, "max_points": max_points}
