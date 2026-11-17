"""Course-wide immutable configuration and explicit term-local selections."""
import json
import logging
from pathlib import Path

from werkzeug.exceptions import Conflict, NotFound
from course_storage import StorageError, timestamp
from rosters import connect

from . import answers, config, rubrics, locations
from .models import box, digest, encoded, exam_letter, identifier, items, number, text, uid


LOG = logging.getLogger(__name__)


def version(data, current):
    if type(data.get("version")) is not int or data["version"] != current:
        raise Conflict("Grading changed. Refresh and inspect the current revision before saving.")


class AssignmentsMixin:
    def settings(self):
        with connect(self.path) as db:
            row = db.execute("SELECT * FROM frq_settings WHERE id=1").fetchone()
        return {"version": row["version"] if row else 0, "settings": config.validate(json.loads(row["settings_json"]) if row else {})}

    def save_settings(self, data):
        clean = config.validate(data.get("settings"), saving=True)  # A key bound to another host is refused only when the instructor saves.
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            current = db.execute("SELECT version FROM frq_settings WHERE id=1").fetchone()
            version(data, current[0] if current else 0)
            db.execute("INSERT INTO frq_settings VALUES(1,1,?,?) ON CONFLICT(id) DO UPDATE SET version=version+1,settings_json=excluded.settings_json,updated_at=excluded.updated_at",
                       (encoded(clean), timestamp()))
            self.event(db, None, "settings_saved", clean)
        self._health_cache = None
        return self.settings()

    def create_assignment(self, data):
        with self.rosters.lock(), connect(self.path) as db:
            event = self.exams._event(db, data.get("event_id"))
            current = db.execute("SELECT id FROM frq_assignments WHERE event_id=?", (event["id"],)).fetchone()
            if current:
                return self.assignment(current[0])
            identifier = uid()
            db.execute("INSERT INTO frq_assignments VALUES(?,?,1,?,?)", (identifier, event["id"], timestamp(), timestamp()))
            self.event(db, identifier, "assignment_created", {"event_id": event["id"]})
        return self.assignment(identifier)

    def set_up_grading(self, data, actor="workspace"):
        """SET UP GRADING NOW (requirement, 2026-10-02): the instructor's explicit action makes the
        grading plan before any paper, so its rubrics, blank templates, answer areas and answer key are finished before scan day.
        `{event_id}` names the term's exam record; `{assignment_id, term_id, section_ids?}` names the assessment, whose exam record
        is reused or made here by the Exams page's own creation (`ExamService._create`) for `section_ids`, else the sections its
        seating charts and print jobs name in that term, else every section of the term (an automatic association naming a section
        outside the record is refused, intake.py). The record and the plan are written in ONE catalog transaction under the
        lifecycle lock, the plan marked `before_papers` in its `assignment_created` event (`grading_entry` then lists it, as it does a
        plan an approved quiz set up). Owners: the plan is the term's exam record's (course_terms); the templates the course-wide
        assignment's (HIERARCHY). An exam record that already has a plan returns it (`created` false) and makes nothing new.

        Then, only when the plan was made now and outside the lock (a template's pages are rendered in the conversion sandbox), each
        current registered PDF version is imported as its blank template by `templates.import_version`, which reuses a version's
        template, so the assignment finder can match an unlabeled scan of it and step 1 has its blanks. Current: not stamps-only, and
        the versions the term's current print jobs hand out (never superseded, cancelled or registration jobs), or, with nothing
        printed in the term yet, every version not retired there (every print job of it superseded or cancelled). A version that
        cannot be imported, whatever the failure, is named in the result; the plan stands and step 1 imports it.
        Returns (the plan read with an additive `set_up: {created, templates}`, created)."""
        from document_identity.origins import is_registration
        with self.rosters.lock():
            with connect(self.path) as db:
                db.execute("BEGIN IMMEDIATE")
                if data.get("event_id") is not None:
                    event = self.exams._event(db, data.get("event_id"))
                else:
                    term_id, assignment_id = data.get("term_id"), data.get("assignment_id")
                    term = db.execute("SELECT definition_id FROM course_terms WHERE id=? AND deleted_at IS NULL", (term_id,)).fetchone() \
                        if isinstance(term_id, str) else None
                    assignment = db.execute("SELECT definition_id FROM assignments WHERE id=?", (assignment_id,)).fetchone() \
                        if isinstance(assignment_id, str) else None
                    if not term or not assignment or term[0] != assignment[0]:
                        raise ValueError("Choose an assignment and a teaching term of the same course to set up grading for.")
                    found = db.execute("SELECT id FROM exam_events WHERE term_id=? AND assignment_id=?", (term_id, assignment_id)).fetchone()
                    if not found:
                        sections = data.get("section_ids")
                        if sections is None:
                            named = """SELECT x.section_id FROM seating_chart_sections x JOIN seating_charts c ON c.id=x.chart_id
                                    WHERE c.term_id=:term AND c.assignment_id=:assignment
                                UNION SELECT b.section_id FROM print_batches b JOIN assignment_versions v ON v.id=b.assignment_version_id
                                    WHERE v.assignment_id=:assignment AND b.term_id=:term AND b.section_id IS NOT NULL AND b.status!='cancelled'
                                    AND b.request_id NOT LIKE 'unlabeled:%'
                                UNION SELECT x.section_id FROM print_batch_sections x JOIN print_batches b ON b.id=x.print_batch_id
                                    JOIN assignment_versions v ON v.id=b.assignment_version_id
                                    WHERE v.assignment_id=:assignment AND b.term_id=:term AND b.status!='cancelled'
                                    AND b.request_id NOT LIKE 'unlabeled:%'"""
                            ids = {row[0] for row in db.execute(named, {"term": term_id, "assignment": assignment_id})}
                            rows = db.execute("SELECT id FROM course_sections WHERE term_id=? ORDER BY number,id", (term_id,)).fetchall()
                            sections = [row[0] for row in rows if not ids or row[0] in ids]
                            if not sections:
                                raise ValueError("Add this term's sections before setting up its grading.")
                        found = [self.exams._create(db, {"term_id": term_id, "assignment_id": assignment_id, "section_ids": sections})]
                    event = self.exams._event(db, found[0])
                current = db.execute("SELECT id FROM frq_assignments WHERE event_id=?", (event["id"],)).fetchone()
                created = not current
                aid = current[0] if current else uid()
                if created:
                    db.execute("INSERT INTO frq_assignments VALUES(?,?,1,?,?)", (aid, event["id"], timestamp(), timestamp()))
                    self.event(db, aid, "assignment_created", {"event_id": event["id"], "before_papers": True}, actor)
                # Only a plan made now imports anything: an existing one is returned as it is (a repeated click, a stale page).
                candidates, printed = [], set()
                for row in (db.execute("SELECT id,label,compiler_json FROM assignment_versions WHERE assignment_id=? ORDER BY created_at,id",
                                       (event["assignment_id"],)).fetchall() if created else []):
                    if json.loads(row["compiler_json"]).get("generator") == "faculty-stamps-only" or json.loads(row["compiler_json"]).get("dr_copy"):
                        continue  # labels, and the DR center's copy of a version (DR COPIES), hold no questions of their own
                    jobs = [job for job in db.execute("SELECT request_id,status,superseded_at FROM print_batches WHERE assignment_version_id=? AND term_id=?",
                                                      (row["id"], event["term_id"])) if not is_registration(job["request_id"])]
                    current = [job for job in jobs if not job["superseded_at"] and job["status"] != "cancelled"]
                    if jobs and not current:
                        continue  # retired: an earlier layout whose every print job was replaced
                    if current:
                        printed.add(row["id"])
                    candidates.append({"version_id": row["id"], "label": row["label"]})
                # Printed this term: the versions its current print jobs hand out, never another term's paper of the course-wide
                # assignment; nothing printed yet: every registered PDF version.
                versions = [item for item in candidates if item["version_id"] in printed] if printed else candidates
        imported = []
        for version_ in versions:
            try:
                imported.append({**version_, "template_id": self.templates.import_version(version_["version_id"])["id"]})
            except (ValueError, Conflict, NotFound, StorageError) as failure:
                imported.append({**version_, "error": getattr(failure, "description", None) or str(failure)})
            except Exception:  # noqa: BLE001 - the plan and its exam record are committed: name the version, never fail the set-up
                LOG.exception("Set up grading now: importing version %s as a blank template failed", version_["version_id"])
                imported.append({**version_, "error": "Its blank template could not be prepared; prepare it in Rubrics & Templates."})
        result = self.assignment(aid)
        result["set_up"] = {"created": created, "templates": imported}
        return result, created

    def _stood(self, db, aid, qid, rubric_id):
        """Whether the plan's confirmed finalization stood for this question's rubric revision: only then does replacing it end the
        question's automatic acceptance (a second reinterpretation of an unconfirmed revision suspends nothing)."""
        return (self.rubric_quality_status(db, aid)["standing"].get(qid) or {}).get("rubric_id") == rubric_id

    @staticmethod
    def plan(db, aid):
        row = db.execute("""SELECT g.*,e.assignment_id source_assignment_id,e.term_id,e.title,t.code,t.term,t.year,t.term_other
            FROM frq_assignments g JOIN exam_events e ON e.id=g.event_id JOIN course_terms t ON t.id=e.term_id WHERE g.id=?""", (aid,)).fetchone()
        if not row:
            raise NotFound("Grading assignment not found.")
        return dict(row)

    @staticmethod
    def question(db, aid, qid):
        row = db.execute("""SELECT p.*,q.question_key,q.assignment_version_id,q.exam_version,r.content_json,r.sha256,r.revision
            FROM frq_assignment_questions p JOIN frq_questions q ON q.id=p.question_id
            JOIN frq_question_revisions r ON r.id=p.question_revision_id
            WHERE p.assignment_id=? AND p.question_id=?""", (aid, qid)).fetchone()
        if not row:
            raise NotFound("Question not found in this grading assignment.")
        result = dict(row)
        result["content"] = json.loads(result.pop("content_json"))
        result["rubric"] = AssignmentsMixin.rubric(db, result["rubric_id"]) if result["rubric_id"] else None
        return result

    @staticmethod
    def rubric(db, rid):
        row = db.execute("SELECT r.*,a.created_at approved_at FROM frq_rubric_revisions r LEFT JOIN frq_rubric_approvals a ON a.rubric_id=r.id WHERE r.id=?", (rid,)).fetchone()
        if not row:
            raise NotFound("Rubric revision not found.")
        result = dict(row)
        result["rules"] = json.loads(result.pop("rules_json"))
        result["compiler"] = json.loads(result.pop("compiler_json"))
        return result

    @staticmethod
    def insert_rubric_revision(db, qid, rid, source, artifact, rules, maximum, compiler, now=None):
        """The question's next immutable rubric revision, numbered and checksummed in one place; returns its checksum, the `sha256`
        approval and the editors compare against."""
        revision = db.execute("SELECT COALESCE(MAX(revision),0)+1 FROM frq_rubric_revisions WHERE question_id=?", (qid,)).fetchone()[0]
        checksum = digest({"source": source, "rules": rules, "max_points": maximum})
        db.execute("INSERT INTO frq_rubric_revisions VALUES(?,?,?,?,?,?,?,?,?,?)", (rid, qid, revision, source, artifact,
            encoded(rules), checksum, rubrics.semantic_hash(rules), encoded(compiler), now if now is not None else timestamp()))
        return checksum

    @staticmethod
    def new_question_revision(db, qid, clean, now=None):
        """The question's next immutable revision of `clean` (its content and that content's checksum); returns its id."""
        revision = db.execute("SELECT COALESCE(MAX(revision),0)+1 FROM frq_question_revisions WHERE question_id=?", (qid,)).fetchone()[0]
        rid = uid()
        db.execute("INSERT INTO frq_question_revisions VALUES(?,?,?,?,?,?)", (rid, qid, revision, encoded(clean), digest(clean), now or timestamp()))
        return rid

    @staticmethod
    def pin_question_revision(db, aid, qid, rid):
        """The assignment's plan now reads the question at revision `rid`; its rubric pin stays."""
        db.execute("UPDATE frq_assignment_questions SET question_revision_id=?,version=version+1 WHERE assignment_id=? AND question_id=?", (rid, aid, qid))

    def assignment(self, aid, *, include_activity=False):
        with self.rosters.lock(), connect(self.path) as db:
            result = self.plan(db, aid)
            result['entry'] = self.grading_entry(db, result)
            result["questions"] = [self.question(db, aid, row[0]) for row in db.execute("SELECT question_id FROM frq_assignment_questions WHERE assignment_id=? ORDER BY rowid", (aid,))]
            readings, memo = self.answer_key_readings(db, aid), {}  # memo: this read's questions share their documents' paper sides
            for question in result["questions"]:
                question["location_check"] = self.location_status(db, aid, question, memo=memo)
                # What the approved-or-draft rubric itself says about answers, and a key for the instructor to confirm; where the rubric
                # gives none, the latest reading of it from the blank exam (2026-09-25), shown but never saved unconfirmed.
                rules = question["rubric"]["rules"] if question["rubric"] else None
                question["answer"] = {"key": question["content"].get("answer_key", []), "roles": answers.rubric_roles(rules) if rules else None,
                                      "proposed_key": answers.key_from_rubric(rules) if rules else [],
                                      **({"read_key": readings[question["question_id"]]} if question["question_id"] in readings else {})}
                # GRADING MULTIPLE-CHOICE READER (AUTOMATIC CONFIRMATION): the grading in force (the exact rule, or a confirmation saved
                # before it), whether the instructor set the question to written work, and whether it has grades.
                from . import choice
                written = bool((question["content"].get("choice_grading") or {}).get("written"))
                found = choice.resolved(question["content"], rules, question["rubric_id"]) if rules and not written else None
                if found or (written and rules and choice.propose(question["content"], rules, question["rubric_id"])):
                    question["answer"]["choice"] = {"grading": found, "written": written, "locked": self.graded_yet(db, aid, question["question_id"])}
            result["document_versions"] = [dict(row) for row in db.execute("SELECT id,label,page_count FROM assignment_versions WHERE assignment_id=? ORDER BY created_at", (result["source_assignment_id"],))]
            result["templates"] = self.templates.listing(result["source_assignment_id"])
            settings = self.settings()["settings"]
            result["default_tier"] = settings["default_tier"]
            result["auto_grade"] = self.auto_grade_state(db, aid, settings)  # Whether the next batch starts by itself, and why not.
            # Exam versions behind one label sheet: what the seating charts handed out, what was declared for a plan made from a scan,
            # what has questions, and each version's blank exam.
            result["exam_versions"] = {"graded": self.plan_versions(db, aid), "seated": self.chart_versions(db, result),
                "declared": self.declared_versions(db, result), "added": self.added_versions(db, result),
                "blanks": {k: {"template_id": v["id"], "label": v["label"]} for k, v in self.version_blanks(db, aid).items()}}
            result["exam_versions"]["letters"] = sorted(set(result["exam_versions"]["graded"]) | set(result["exam_versions"]["seated"])
                                                        | set(result["exam_versions"]["declared"]) | set(result["exam_versions"]["added"]))
            result['rubric_quality'] = self.rubric_quality_status(db, aid)
            # ACCEPTING A DRAFT READIES IT (requirement, 2026-10-11): what the rubric check of accepted drafts still waits for before it
            # starts by itself, said only while such a plan waits, so every other plan's body is exactly as it was. Read only, from
            # what this read already has (`rubric_generation.rubric_review_wait`); a plan no acceptance readied costs one indexed read.
            state, sentence, _ = self.rubric_review_wait(db, aid, result['rubric_quality'], result['questions'])
            if state == 'waiting':
                result['rubric_review_waits'] = sentence
            result['rubric_upload_documents'] = self.rubric_upload_documents(db, result)
            result['template_setup'] = self.template_setup(db, aid)
            result["workflow"] = self.workflow_summary(db, result)
            result["bubble"] = self.bubble_brief(db, result)  # Bubble sheets read for this plan, and its answer key's revision.
            # Three cheap counts, so the guide can say why no paper is gradable yet instead of an empty batch preview.
            # Names and pages stay in Exams; this only counts retained records of this exam's assessment.
            sections = "SELECT section_id FROM exam_event_sections WHERE event_id=?"
            versions = "SELECT id FROM assignment_versions WHERE assignment_id=?"
            # A job printed for the whole term (no section, document_identity migration 013) counts for its term's exam.
            mine = f"(b.section_id IN ({sections}) OR (b.section_id IS NULL AND b.term_id=(SELECT term_id FROM exam_events WHERE id=?)))"
            scope = (result["source_assignment_id"], result["event_id"], result["event_id"])
            result["papers"] = {
                "print_jobs_attached": db.execute(  # current jobs only, as the Exams page's Print tile counts them
                    "SELECT COUNT(*) FROM exam_event_batches eb JOIN print_batches b ON b.id=eb.batch_id WHERE eb.event_id=? "
                    "AND b.superseded_at IS NULL AND b.status!='cancelled'", (result["event_id"],)).fetchone()[0],
                "print_jobs_available": db.execute(f"""SELECT COUNT(*) FROM print_batches b WHERE b.status='ready' AND b.superseded_at IS NULL
                    AND b.assignment_version_id IN ({versions}) AND {mine}
                    AND NOT EXISTS(SELECT 1 FROM exam_event_batches eb WHERE eb.batch_id=b.id)""", scope).fetchone()[0],
                "scans_awaiting_review": db.execute(f"""SELECT COUNT(DISTINCT m.id) FROM inbox_messages m JOIN scan_packets k ON k.message_id=m.id
                    JOIN scan_sequence_runs r ON r.id=k.run_id AND r.undone_at IS NULL JOIN printed_copies c ON c.id=k.printed_copy_id
                    JOIN print_batches b ON b.id=c.print_batch_id WHERE m.instructor_review_required=1 AND b.assignment_version_id IN ({versions})
                    AND {mine}
                    AND NOT EXISTS(SELECT 1 FROM inbox_deletions gone WHERE gone.message_id=m.id AND gone.restored_at IS NULL)""", scope).fetchone()[0],
                "reviewed_papers": db.execute(f"""SELECT COUNT(DISTINCT ar.printed_copy_id) FROM assembly_revisions ar JOIN printed_copies c ON c.id=ar.printed_copy_id
                    JOIN print_batches b ON b.id=c.print_batch_id WHERE b.assignment_version_id IN ({versions}) AND {mine}""", scope).fetchone()[0]}
            result["papers"]["scans_awaiting_review"] = result["workflow"]["awaiting_review"]
            if include_activity:
                result['activity'] = self.activity(db, aid)
        return result

    def save_question(self, aid, data):
        with self.rosters.lock(), connect(self.path) as db:
            plan = self.plan(db, aid)
            version(data, plan["version"])
            document_id = data.get("assignment_version_id")
            document = db.execute("SELECT * FROM assignment_versions WHERE id=? AND assignment_id=?", (document_id, plan["source_assignment_id"])).fetchone() if isinstance(document_id, str) else None
            if not document:
                raise ValueError("Choose a document version of this assessment.")
            key = identifier(data.get("question_key"))
            exam_version = exam_letter(data.get("exam_version"))
            content = data.get("content")
            if not isinstance(content, dict):
                raise ValueError("Supply question details.")
            template_id = content.get("template_id") or None
            if template_id is not None and not isinstance(template_id, str):
                raise ValueError("Choose a blank template of this assessment.")
            page_count = document["page_count"]
            if template_id:
                template = db.execute("SELECT page_count FROM assignment_templates WHERE id=? AND assignment_id=?",
                    (template_id, plan["source_assignment_id"])).fetchone()
                stamps_only = json.loads(document["compiler_json"]).get("generator") == "faculty-stamps-only"
                if not template or (not stamps_only and template["page_count"] != page_count):
                    raise ValueError("Template must belong to this assignment and match the printed exam's page count; stamps-only labels use the template's pages.")
                page_count = template["page_count"]
            maximum = number(content.get("max_points"), "Maximum points", .01, 10000)
            regions = []
            for region in items(content.get("regions", []), "Answer areas", 20):
                if not isinstance(region, dict):
                    raise ValueError("An answer area must describe a page and rectangle.")
                page = region.get("page")
                if type(page) is not int or not 1 <= page <= page_count:
                    raise ValueError("Question page is outside the selected document.")
                rect = region.get("region", {})
                if not isinstance(rect, dict) or set(rect) != {"x", "y", "width", "height"}:
                    raise ValueError("Answer areas need x, y, width and height.")
                rect = {k: number(v, "Region", 0, 1) for k, v in rect.items()}
                box([rect["x"], rect["y"], rect["x"] + rect["width"], rect["y"] + rect["height"]])
                regions.append({"page": page, "region": rect, "privacy_masks": [box(m) for m in items(region.get("privacy_masks", []), "Privacy masks", 30)]})
                if None in regions[-1]["privacy_masks"]:
                    raise ValueError("A privacy mask must be a rectangle.")
            if not regions:
                raise ValueError("Define at least one answer area.")
            clean = {key: text(content.get(key, ""), key, 20000, key != "title") for key in
                ("title", "problem", "expected_answer", "coordinator_solution", "instructor_solution", "worked_solution", "acceptable_methods", "notes")}
            examples = []
            for example in items(content.get("examples", []), "Calibration examples", 20):
                if not isinstance(example, dict):
                    raise ValueError("A calibration example must describe the work and selected rubric lines.")
                examples.append({"work": text(example.get("work", "")), "rubric_item_ids": [identifier(i) for i in items(example.get("rubric_item_ids", []), "Example selections")],
                    "notes": text(example.get("notes", "")), "source": text(example.get("source", "Coordinator example"))})
            clean.update(max_points=maximum, regions=regions, template_id=template_id, examples=examples,
                         answer_key=answers.clean_key(content.get("answer_key")),
                         printed_number=locations.printed_number(content.get("printed_number", "")),
                         final_answer_region=box(content.get("final_answer_region")), problem_region=box(content.get("problem_region")))
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT id,exam_version FROM frq_questions WHERE assignment_version_id=? AND question_key=?", (document["id"], key)).fetchone()
            if existing and "exam_version" in data and existing["exam_version"] != exam_version:
                raise Conflict(f"This question key belongs to {'Version ' + existing['exam_version'] if existing['exam_version'] else 'the exam without versions'}. Use another key for another exam version.")
            qid = existing[0] if existing else uid()
            if not existing:
                db.execute("INSERT INTO frq_questions(id,assignment_version_id,question_key,created_at,exam_version) VALUES(?,?,?,?,?)", (qid, document["id"], key, timestamp(), exam_version))
            rid = self.new_question_revision(db, qid, clean)
            # A new question/solution revision invalidates the term's rubric pin;
            # other terms and historical proposals retain their exact revisions.
            db.execute("""INSERT INTO frq_assignment_questions VALUES(?,?,?,NULL,1)
                ON CONFLICT(assignment_id,question_id) DO UPDATE SET question_revision_id=excluded.question_revision_id,rubric_id=NULL,version=version+1""", (aid, qid, rid))
            self.touch(db, aid)
            self.event(db, aid, "question_saved", {"question_id": qid, "revision_id": rid})
        return self.assignment(aid)

    def import_rubric(self, aid, qid, data):
        with self.rosters.lock(), connect(self.path) as db:
            question = self.question(db, aid, qid)
            version(data, question["version"])
            source = text(data.get("source_text"), "Original rubric", 200000, False)
            maximum = question["content"]["max_points"]
            compiler = {"name": "instructor-edited" if "rules" in data else "rule-parser-v5"}  # v5: "not both" too anchored to other lines.
            if data.get("preparation_id"):
                prepared = self.preparation(data["preparation_id"])
                if prepared["assignment_id"] != aid or prepared["kind"] != "compile" or prepared["status"] != "complete" or prepared["payload"]["question_id"] != qid or prepared["payload"]["source_text"] != source or prepared["payload"]["question_version"] != question["version"]:
                    raise Conflict("Choose the completed interpretation for this question and original source.")
                rules = rubrics.validate(data.get("rules", prepared["result"]["rules"]), maximum)
                compiler = {**prepared["result"]["compiler"], "preparation_id": prepared["id"], "instructor_edited": rules != prepared["result"]["rules"]}
            else:
                rules = rubrics.validate(data["rules"], maximum) if "rules" in data else rubrics.parse(source, maximum, data.get("format", "text"), data.get("mode", "negative"))
            source_artifact = data.get("source_artifact_id") or None
            if source_artifact:
                # Originals may only be course-wide source materials of this course.
                plan = self.plan(db, aid)
                row = db.execute("""SELECT 1 FROM inbox_artifacts a JOIN inbox_messages m ON m.id=a.message_id
                    JOIN assignments d ON d.definition_id=m.definition_id WHERE a.id=? AND d.id=?
                    AND m.term_id IS NULL AND m.section_id IS NULL""", (source_artifact, plan["source_assignment_id"])).fetchone()
                if not row:
                    raise ValueError("Rubric source must be retained for this course.")
            # A revision that replaces the rubric a confirmed finalization covered suspends automatic acceptance, as the line editor
            # (`edit_rubric`) does: both editors follow the same rules. Only THIS question's (requirement, 2026-09-25).
            if question["rubric"] and self._stood(db, aid, qid, question["rubric"]["id"]):
                self.suspend_acceptance(db, aid, "A new rubric revision replaced one the rubric finalization confirmed.", question_ids=[qid])
            rid = uid()
            self.insert_rubric_revision(db, qid, rid, source, source_artifact, rules, maximum, compiler)
            db.execute("UPDATE frq_assignment_questions SET rubric_id=?,version=version+1 WHERE assignment_id=? AND question_id=?", (rid, aid, qid))
            # A confirmed answer area is kept through a re-import or an edited interpretation (requirement, 2026-10-11: confirming it again
            # repeated what was already confirmed). The template, pages, areas and printed number live in the question revision, which
            # this never touches; the one thing of the rubric the confirmation covers is its maximum, which the printed points were
            # compared with, so a rubric with another maximum is asked again. Only the area: the finalization is not carried.
            carried = bool(question["rubric"]) and rules["ceiling"] == question["rubric"]["rules"]["ceiling"] and self.carry_location(
                db, aid, qid, question["question_revision_id"], question["rubric"]["id"],
                "A new revision of the rubric with the same maximum; the template, pages, areas and printed number are the same.", to_rubric=rid)
            self.touch(db, aid)
            self.event(db, aid, "rubric_imported", {"rubric_id": rid, "question_id": qid, "location_carried_forward": carried})
        return self.assignment(aid)

    def correct_rubric_line(self, aid, qid, data):
        """The instructor's own correction of ONE line's wording (2026-09-21: a coordinator's full-credit line said y = -4 where the
        printed problem gives y = -5; 11 of 15 students answered -5, and every grader argued with the rubric).

        Revisions are immutable, so the correction is a NEW revision: the same lines, IDs, points and rules with this line's wording
        replaced, the original file and each line's `original_source_text` kept, and the compiler record naming what it corrects.
        Nothing here is an AI's clarification (those stay an overlay beside the rubric); it is the instructor fixing their rubric,
        and Faculty changes nothing on Gradescope. What a confirmed finalization decided (built-in rules, exclusive pairs, the
        points) does not depend on a line's wording, so it is carried to the corrected revision and that revision is approved with
        it, unless the wording changes what the built-in rules find: then the finalization is asked for again as after any change.
        The corrected line's own clarification is the one exception: it explained the OLD wording, so it is dropped, not carried.
        Earlier proposals of the question were made under the old wording and are stale, as after any rubric change.
        """
        import copy
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            question = self.question(db, aid, qid)
            version(data, question["version"])
            rubric = question["rubric"]
            if not rubric or data.get("rubric_id") != rubric["id"] or data.get("sha256") != rubric["sha256"]:
                raise Conflict("The rubric changed. Refresh and correct the current revision.")
            iid, wording = data.get("item_id"), text(data.get("description"), "Corrected wording", 4000, False).strip()
            rules = copy.deepcopy(rubric["rules"])
            item = next((i for i in rules["items"] if i["id"] == iid), None)
            if not item:
                raise ValueError("Choose a line of this rubric.")
            previous = item["description"]
            if not wording or wording == previous.strip():
                raise ValueError("The wording is unchanged.")
            if (item.get("exceptions") or "").strip() == previous.strip():
                item["exceptions"] = wording  # The importer's own mark is the line's wording: it follows the correction.
            item["description"] = wording
            maximum = question["content"]["max_points"]
            rules = rubrics.validate(rules, maximum)
            before = self.rubric_quality_status(db, aid, details=True)
            rid = uid()
            compiler = {"name": "instructor-corrected", "corrected_from": rubric["id"], "item_id": iid, "previous_description": previous, "based_on": rubric["compiler"]}
            sha = self.insert_rubric_revision(db, qid, rid, rubric["source_text"], rubric["source_artifact_id"], rules, maximum, compiler)
            db.execute("UPDATE frq_assignment_questions SET rubric_id=?,version=version+1 WHERE assignment_id=? AND question_id=?", (rid, aid, qid))
            # The confirmed answer areas are pinned to the rubric revision too. No page, area, template or maximum changed: the
            # confirmation is carried by an append-only receipt, exactly as a corrected answer key carries it.
            located = self.carry_location(db, aid, qid, question["question_revision_id"], rubric["id"],
                "Only the wording of one rubric line changed; the template, pages, areas and the rubric maximum are the same.", to_rubric=rid)
            carried = None
            stood = (before["standing"].get(qid) or {}).get("rubric_id") == rubric["id"]
            if before["review"]:
                from .rubric_quality import builtin_exclusions
                policy = copy.deepcopy(before["review"]["policy"])
                entry = next((q for q in policy["questions"] if q["question_id"] == qid and q["rubric_id"] == rubric["id"]), None)
                if entry and entry.get("builtin", []) == builtin_exclusions(rules, maximum):
                    entry["rubric_id"] = rid
                    # A clarification of THIS line was written against the wording just corrected, and graders read the two side by
                    # side: it is dropped, and the receipt says so. The other lines' clarifications, the pairs and the built-in rules carry.
                    dropped = (entry.get("clarifications") or {}).pop(iid, None)
                    receipt = {"from_review": before["review"]["id"], "question_id": qid, "item_id": iid, "rubric_id": rid}
                    if dropped is not None:
                        receipt["clarification_dropped"] = dropped
                    policy["carried"] = [*policy.get("carried", []), receipt]
                    after = self.rubric_quality_status(db, aid, details=True)
                    from .rubric_quality import question_inputs
                    policy["inputs"] = question_inputs(after["context"])  # what the carried confirmation covers, question by question
                    carried = uid()
                    db.execute("INSERT INTO frq_rubric_quality_reviews VALUES(?,?,?,?,?,?,?)", (carried, aid, before["review"]["check_id"], after["input_sha256"], encoded(policy), "workspace", timestamp()))
                    db.execute("UPDATE frq_rubric_quality_options SET review_id=?,version=version+1,updated_at=? WHERE assignment_id=?", (carried, timestamp(), aid))
                    db.execute("INSERT OR IGNORE INTO frq_rubric_approvals VALUES(?,?,?,?,?)", (rid, sha, "workspace", "The instructor's correction of one line; approved with the finalization it carries.", timestamp()))
                    self.event(db, aid, "rubric_quality_carried", {"review_id": carried, "from_review": before["review"]["id"], "question_id": qid, "rubric_id": rid,
                                                                   **({"clarification_dropped": dropped} if dropped is not None else {})})
            if stood:
                self.suspend_acceptance(db, aid, "The instructor corrected a rubric line.", question_ids=[qid])  # only this question's
            self.touch(db, aid)
            self.event(db, aid, "rubric_line_corrected", {"question_id": qid, "rubric_id": rid, "corrected_from": rubric["id"], "item_id": iid, "previous": previous, "corrected": wording,
                                                          "location_carried_forward": bool(located)})
        return {**self.assignment(aid), "correction": {"rubric_id": rid, "item_id": iid, "finalization_carried": bool(carried)}}

    def edit_rubric(self, aid, qid, data):
        """The instructor's rubric, prepared before the rubric reviewer reads it (requirement, 2026-09-24).

        Wording and points: `lines` [{item_id, description?, points?}] make a NEW immutable revision, like `correct_rubric_line` but for
        any lines' wording and points; the IDs, groups and rules stay, the uploaded file and each line's `original_source_text` are
        kept, and the compiler record (`instructor-edited`) lists every change. Graders read and scoring uses the new revision, and
        Faculty changes nothing on Gradescope. Nothing of a confirmed finalization is carried: changed wording or points need the
        reviewer and the instructor again, and earlier proposals of the question are stale, as after any rubric change.
        Comments: `comments` {item_id: text} are the instructor's guidance about lines for the reviewer, who folds each into that
        line's clarification. They belong to the term grading plan, append-only; an empty comment clears one.
        Either change ends the question's confirmation for review, which names the exact revision and comments it confirmed.
        """
        import copy
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            question = self.question(db, aid, qid)
            version(data, question["version"])
            rubric = question["rubric"]
            if not rubric or data.get("rubric_id") != rubric["id"] or data.get("sha256") != rubric["sha256"]:
                raise Conflict("The rubric changed. Refresh and edit the current revision.")
            maximum = question["content"]["max_points"]
            rules = copy.deepcopy(rubric["rules"])
            lines = {item["id"]: item for item in rules["items"]}
            changes, seen = [], set()
            for line in items(data.get("lines", []), "Rubric lines", 1000):
                if not isinstance(line, dict) or not isinstance(line.get("item_id"), str) or line["item_id"] not in lines or line["item_id"] in seen:
                    raise ValueError("Edit each line of this rubric at most once.")
                seen.add(line["item_id"])
                item, change = lines[line["item_id"]], {"item_id": line["item_id"]}
                if "description" in line:
                    wording = text(line["description"], "Rubric line wording", 4000, False).strip()
                    if not wording:
                        raise ValueError("Every rubric line needs its wording.")
                    if wording != item["description"].strip():
                        if (item.get("exceptions") or "").strip() == item["description"].strip():
                            item["exceptions"] = wording  # The importer's own mark is the line's wording: it follows the edit.
                        change.update(previous_description=item["description"], description=wording)
                        item["description"] = wording
                if "points" in line:
                    points = number(line["points"], "Points", -maximum, maximum)
                    if float(points) != float(item["points"]):
                        change.update(previous_points=item["points"], points=points)
                        item["points"] = points
                if len(change) > 1:
                    changes.append(change)
            current = self.line_comments(db, aid, qid).get(qid, {})
            asked = data.get("comments", {})
            if not isinstance(asked, dict) or set(asked) - set(lines):
                raise ValueError("Comment on lines of this rubric.")
            comments = {}
            for iid, value in asked.items():
                note = text(value, "Comment", 2000).strip()
                if note != current.get(iid, ""):
                    comments[iid] = note
            if not changes and not comments:
                raise ValueError("Nothing changed.")
            rid, located = rubric["id"], None
            if changes:
                rules = rubrics.validate(rules, maximum)
                if self._stood(db, aid, qid, rubric["id"]):
                    self.suspend_acceptance(db, aid, "The instructor edited rubric lines.", question_ids=[qid])  # only this question's
                rid = uid()
                compiler = {"name": "instructor-edited", "edited_from": rubric["id"], "changes": changes, "based_on": rubric["compiler"]}
                self.insert_rubric_revision(db, qid, rid, rubric["source_text"], rubric["source_artifact_id"], rules, maximum, compiler)
                db.execute("UPDATE frq_assignment_questions SET rubric_id=?,version=version+1 WHERE assignment_id=? AND question_id=?", (rid, aid, qid))
                # The answer areas do not depend on a line's wording or points: the template, pages, areas and the question's maximum are
                # the same, so a confirmed location is carried by an append-only receipt, exactly as a corrected line carries it.
                located = self.carry_location(db, aid, qid, question["question_revision_id"], rubric["id"],
                    "Only rubric lines' wording or points changed; the template, pages, areas and the question's maximum are the same.", to_rubric=rid)
                self.event(db, aid, "rubric_lines_edited", {"question_id": qid, "rubric_id": rid, "edited_from": rubric["id"], "changes": changes,
                                                            "location_carried_forward": bool(located)})
            for iid, note in comments.items():
                db.execute("INSERT INTO frq_rubric_line_comments VALUES(?,?,?,?,?,?,?,?)", (uid(), aid, qid, iid, rid, note, "workspace", timestamp()))
            if comments:
                self.event(db, aid, "rubric_line_comments_saved", {"question_id": qid, "rubric_id": rid, "items": sorted(comments)})
            if changes:  # A comment alone is the finalization's input, with its own fingerprint, not a change to the plan.
                self.touch(db, aid)
        return {**self.assignment(aid), "edit": {"rubric_id": rid, "lines_changed": len(changes), "comments_changed": len(comments)}}

    def approve_rubric(self, aid, qid, data):
        with self.rosters.lock(), connect(self.path) as db:
            question = self.question(db, aid, qid)
            version(data, question["version"])
            rubric = question["rubric"]
            if not rubric or data.get("sha256") != rubric["sha256"] or data.get("rubric_id") != rubric["id"]:
                raise Conflict("Review the exact current rubric before approval.")
            if rubric["rules"]["warnings"] and data.get("acknowledge_rules") is not True:
                raise ValueError("Confirm that the flagged rubric interpretations have been reviewed.")
            db.execute("INSERT OR IGNORE INTO frq_rubric_approvals VALUES(?,?,?,?,?)", (rubric["id"], rubric["sha256"], "workspace", text(data.get("notes", "")), timestamp()))
            self.event(db, aid, "rubric_approved", {"rubric_id": rubric["id"], "sha256": rubric["sha256"]})
        return self.assignment(aid)

    def rubric_history(self, aid, qid):
        with self.rosters.lock(), connect(self.path) as db:
            self.question(db, aid, qid)
            return [self.rubric(db, r[0]) for r in db.execute("SELECT id FROM frq_rubric_revisions WHERE question_id=? ORDER BY revision DESC", (qid,))]

    def upload_source(self, aid, filename, content, request_id):
        """Retain original course material with the existing Inbox storage lifecycle."""
        extension = Path(filename).suffix.lower()
        if extension not in (".txt", ".csv", ".json", ".pdf", ".png", ".jpg", ".jpeg"):
            raise ValueError("Upload UTF-8 text, CSV, JSON, PDF, PNG or JPEG rubric material.")
        with self.rosters.lock(), connect(self.path) as db:
            plan = self.plan(db, aid)
            definition = db.execute("SELECT definition_id FROM assignments WHERE id=?", (plan["source_assignment_id"],)).fetchone()[0]
        job = self.inbox.submit({"action": "document.archive", "target": {"level": "course", "id": definition},
            "metadata": {"purpose": "grading_rubric_source"}}, [{"filename": filename, "content": content}],
            source="web", idempotency_key=request_id, authenticated=True)
        # This explicit workspace upload archives course material only. The
        # scanner-only gate would skip owner resolution and leave it unclassified;
        # rubric interpretation and approval remain separate operations below.
        detail = self.inbox.get(job["id"])
        artifact = next(a for a in detail["artifacts"] if a["role"] == "input")
        if extension in (".txt", ".csv", ".json"):
            extracted = content.decode("utf-8-sig")
            provenance = {"method": "utf-8"}
        else:
            return self.queue_preparation(aid, "source", {"source_artifact_id": artifact["id"], "format": extension}, "source:" + request_id)
        result = {"source_artifact_id": artifact["id"], "source_text": extracted, "provenance": provenance}
        if extension == ".csv":
            from .gradescope_import import summary
            try:
                result["rubric_source"] = summary(extracted)
            except ValueError as error:
                result["rubric_source_error"] = str(error)
        return result
