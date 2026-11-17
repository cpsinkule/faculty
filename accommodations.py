"""Section/enrollment accommodation records; workspace Inbox import provenance.

Structured records and immutable revisions live in the NVMe catalog and its
verified nightly snapshots. The uploaded workbook remains one Inbox artifact,
even when its rows span several courses. No roster payloads are copied/rewritten.
"""
from contextlib import ExitStack
import json
import re
import uuid

from flask import Blueprint, jsonify, request
from werkzeug.exceptions import Conflict

from accommodation_import import display_name, parse_accommodations, MAX_UPLOAD
from course_groups import group_courses
from course_storage import live_offerings, timestamp
from dr_quiz_takers.service import offered as dr_quiz_taker_offered, states as dr_quiz_taker_states
from rosters import connect
from teach_inbox.service import PRESENT, encoded, sha


def validate_metadata(value):
    if not isinstance(value, dict) or set(value) != {"term", "preview_hash"}:
        raise ValueError("Preview the accommodation export before importing it.")
    if not isinstance(value["preview_hash"], str) or not re.fullmatch(r"[a-f0-9]{64}", value["preview_hash"]):
        raise ValueError("The accommodation preview is missing or invalid.")
    term = value["term"]
    if term is not None and (not isinstance(term, dict) or set(term) != {"term", "term_other", "year"}
        or term["term"] not in ("Fall", "Spring", "Summer I", "Summer II", "Summer (Full)", "Other")
        or not isinstance(term["term_other"], str) or len(term["term_other"]) > 100
        or type(term["year"]) is not int or not 1900 <= term["year"] <= 2200):
        raise ValueError("Choose a current teaching term or all current terms.")


class AccommodationService:
    def __init__(self, rosters, inbox):
        self.rosters, self.inbox, self.path = rosters, inbox, rosters.path
        with rosters.lock(), connect(self.path) as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS accommodation_imports (
                    job_id TEXT PRIMARY KEY REFERENCES inbox_jobs(id),
                    source_artifact_id TEXT NOT NULL REFERENCES inbox_artifacts(id),
                    source_sha256 TEXT NOT NULL, result_json TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS student_accommodations (
                    section_id TEXT NOT NULL, enrollment_id TEXT NOT NULL,
                    revision_id TEXT NOT NULL REFERENCES accommodation_revisions(id),
                    PRIMARY KEY(section_id,enrollment_id),
                    FOREIGN KEY(section_id,enrollment_id) REFERENCES roster_memberships(section_id,enrollment_id)
                );
                CREATE TABLE IF NOT EXISTS accommodation_revisions (
                    id TEXT PRIMARY KEY, section_id TEXT NOT NULL, enrollment_id TEXT NOT NULL,
                    job_id TEXT NOT NULL REFERENCES accommodation_imports(job_id),
                    source_row INTEGER NOT NULL, source_updated_at TEXT NOT NULL,
                    snapshot_json TEXT NOT NULL, snapshot_sha256 TEXT NOT NULL, created_at TEXT NOT NULL,
                    FOREIGN KEY(section_id,enrollment_id) REFERENCES roster_memberships(section_id,enrollment_id)
                );
                CREATE INDEX IF NOT EXISTS accommodation_history ON accommodation_revisions(section_id,enrollment_id,created_at);
                CREATE TRIGGER IF NOT EXISTS accommodation_revision_owner BEFORE INSERT ON student_accommodations
                    WHEN NOT EXISTS(SELECT 1 FROM accommodation_revisions WHERE id=NEW.revision_id AND section_id=NEW.section_id AND enrollment_id=NEW.enrollment_id)
                    BEGIN SELECT RAISE(ABORT,'Accommodation revision owner mismatch'); END;
                CREATE TRIGGER IF NOT EXISTS accommodation_update_owner BEFORE UPDATE ON student_accommodations
                    WHEN OLD.section_id IS NOT NEW.section_id OR OLD.enrollment_id IS NOT NEW.enrollment_id OR
                    NOT EXISTS(SELECT 1 FROM accommodation_revisions WHERE id=NEW.revision_id AND section_id=NEW.section_id AND enrollment_id=NEW.enrollment_id)
                    BEGIN SELECT RAISE(ABORT,'Accommodation identity is retained'); END;
                CREATE TRIGGER IF NOT EXISTS accommodation_revision_immutable BEFORE UPDATE ON accommodation_revisions
                    BEGIN SELECT RAISE(ABORT,'Accommodation history is immutable'); END;
                CREATE TRIGGER IF NOT EXISTS accommodation_revision_retained BEFORE DELETE ON accommodation_revisions
                    BEGIN SELECT RAISE(ABORT,'Accommodation history is retained'); END;
                CREATE TRIGGER IF NOT EXISTS accommodation_import_immutable BEFORE UPDATE ON accommodation_imports
                    BEGIN SELECT RAISE(ABORT,'Accommodation import history is immutable'); END;
                CREATE TRIGGER IF NOT EXISTS accommodation_import_retained BEFORE DELETE ON accommodation_imports
                    BEGIN SELECT RAISE(ABORT,'Accommodation import history is retained'); END;
                CREATE TRIGGER IF NOT EXISTS accommodation_record_retained BEFORE DELETE ON student_accommodations
                    BEGIN SELECT RAISE(ABORT,'Student accommodation records are retained'); END;
            """)

    def groups(self, current_only=True):
        with connect(self.path) as db:
            return [g for g in group_courses(live_offerings(db)) if not current_only or g["is_current"]]

    def options(self):
        with self.rosters.lock():
            terms = {encoded({k:g[k] for k in ("term", "term_other", "year")}) for g in self.groups()}
        return {"terms": [json.loads(t) for t in sorted(terms)]}

    def preview(self, parsed, term=None):
        validate_metadata({"term": term, "preview_hash": "0" * 64})
        with self.rosters.lock():
            plan = self._plan(parsed, term)
        # Display only, added after the preview hash: the name as rosters show it ('Last, First') and what an updated
        # student had before (the dialog's Previously → Now).
        for source, row in zip(parsed["rows"], plan["rows"], strict=True):
            row["display_name"] = display_name(source)
        with connect(self.path) as db:
            for row in plan["rows"]:
                if row["status"] == "updated" and row.get("previous_revision"):
                    previous = db.execute("SELECT snapshot_json FROM accommodation_revisions WHERE id=?", (row["previous_revision"],)).fetchone()
                    if previous:
                        row["previous_accommodations"] = self.active(json.loads(previous["snapshot_json"]))
        return plan

    def match_rows(self, parsed, term):
        """Resolve source rows to existing memberships while holding the roster lock."""
        destinations = {}
        requested = {(r["code"], r["section"]) for r in parsed["rows"]}
        for group in self.groups():
            if term is not None and any(group[k] != term[k] for k in term):
                continue
            for number in group["current_sections"]:
                if (group["code"], number) not in requested:
                    continue
                owner, section = self.rosters.resolve(group["id"], number)
                with self.rosters.files(owner) as root:
                    roster = self.rosters.response(owner, section, self.rosters.read_payload(root, owner["roster_initialized"]))
                students = {s["uin"]:s for s in roster["students"]}
                destinations.setdefault((group["code"], number), []).append({"term_id": owner["id"], "section_id": section["id"],
                    "label": f"{group['code']} · {group['term_other'] if group['term']=='Other' else group['term']} {group['year']} · Section {number}",
                    "students": students, "course_url": f"#course/{group['id']}/section/{number}/roster"})
        plan = []
        with connect(self.path) as db:
            for row in parsed["rows"]:
                item = {"row": row["row"], "uin": row["uin"], "name": row["name"], "code": row["code"], "section": row["section"],
                        "status": "unmatched", "reason": "No matching current course and section."}
                candidates = destinations.get((row["code"], row["section"]), [])
                if len(candidates) > 1:
                    item.update(status="ambiguous", reason="More than one current term matches. Choose a teaching term.")
                elif candidates:
                    destination = candidates[0]; student = destination["students"].get(row["uin"])
                    item.update(label=destination["label"], course_url=destination["course_url"])
                    if not student or not db.execute("SELECT 1 FROM roster_memberships m JOIN student_enrollments e ON e.id=m.enrollment_id WHERE m.section_id=? AND m.enrollment_id=? AND e.uin=?",
                                                    (destination["section_id"], student["id"], row["uin"])).fetchone():
                        item["reason"] = "UIN is not in this section's saved roster. Import the roster first."
                    else:
                        item.update(status="matched", reason="", section_id=destination["section_id"], enrollment_id=student["id"])
                plan.append(item)
        return plan

    def _plan(self, parsed, term):
        plan = self.match_rows(parsed, term)
        with connect(self.path) as db:
            for row, item in zip(parsed["rows"], plan, strict=True):
                if item["status"] != "matched":
                    continue
                previous = db.execute("SELECT r.* FROM student_accommodations a JOIN accommodation_revisions r ON r.id=a.revision_id WHERE a.section_id=? AND a.enrollment_id=?",
                                      (item["section_id"],item["enrollment_id"])).fetchone()
                old = json.loads(previous["snapshot_json"]) if previous else {"values": {}, "metadata": {}}
                snapshot = {}
                for kind in ("values", "metadata"):
                    incoming = {key.casefold() for key in row[kind]}
                    snapshot[kind] = {**{k:v for k,v in old[kind].items() if k.casefold() not in incoming}, **row[kind]}
                digest = sha(encoded(snapshot).encode())
                item.update(status="unchanged" if previous and previous["snapshot_sha256"] == digest else "updated" if previous else "added",
                    previous_revision=previous["id"] if previous else None, snapshot=snapshot, snapshot_sha256=digest, source_updated_at=row["updated"])
                if previous and previous["source_updated_at"] and (not row["updated"] or row["updated"] < previous["source_updated_at"]):
                    item.update(status="stale", reason="A newer accommodation record is already saved.")
                item["accommodations"] = self.active(snapshot)
        counts = {status: sum(r["status"] == status for r in plan) for status in ("added", "updated", "unchanged", "unmatched", "ambiguous", "stale")}
        return {"rows": plan, "counts": counts, "total": len(plan), "preview_hash": sha(encoded({"source":parsed,"term":term,"plan":plan}).encode())}

    @staticmethod
    def active(snapshot):
        return [{"name": name, "value": value} for name,value in snapshot["values"].items()
                if value.strip().casefold() not in ("", "no", "false", "0", "n")]

    def import_job(self, job, file):
        metadata = job["parameters"]["metadata"]; validate_metadata(metadata)
        parsed = parse_accommodations(file["content"], file["filename"])
        with self.rosters.lock(), ExitStack() as locks:
            with connect(self.path) as db:
                prior = db.execute("SELECT * FROM accommodation_imports WHERE job_id=?", (job["id"],)).fetchone()
                if prior:
                    if prior["source_artifact_id"] != file["id"] or prior["source_sha256"] != sha(file["content"]):
                        raise ValueError("The saved accommodation import belongs to different input.")
                    return json.loads(prior["result_json"])
            plan = self._plan(parsed, metadata["term"])
            if plan["preview_hash"] != metadata["preview_hash"]:
                raise ValueError("Rosters or accommodations changed after the preview. Preview and import the file again.")
            changes = [r for r in plan["rows"] if r["status"] in ("added", "updated")]
            if self.rosters.storage:
                for owner in sorted({r["section_id"] for r in changes}):
                    locks.enter_context(self.rosters.storage.lock(owner))
            result = {"message": f"Accommodations imported: {plan['counts']['added']} added, {plan['counts']['updated']} updated, {plan['counts']['unchanged']} unchanged.",
                      "counts":plan["counts"], "total":plan["total"], "rows":[{k:v for k,v in r.items() if k not in ("snapshot","snapshot_sha256","previous_revision")} for r in plan["rows"]]}
            with connect(self.path) as db:
                db.execute("BEGIN IMMEDIATE")
                db.execute("INSERT INTO accommodation_imports VALUES(?,?,?,?,?)", (job["id"],file["id"],sha(file["content"]),encoded(result),timestamp()))
                for row in changes:
                    revision = str(uuid.uuid4())
                    db.execute("INSERT INTO accommodation_revisions VALUES(?,?,?,?,?,?,?,?,?)", (revision,row["section_id"],row["enrollment_id"],job["id"],
                        row["row"],row["source_updated_at"],encoded(row["snapshot"]),row["snapshot_sha256"],timestamp()))
                    db.execute("INSERT INTO student_accommodations VALUES(?,?,?) ON CONFLICT(section_id,enrollment_id) DO UPDATE SET revision_id=excluded.revision_id",
                        (row["section_id"],row["enrollment_id"],revision))
            return result

    def attach(self, roster, db=None):
        if db is None:
            with connect(self.path) as db:
                return self.attach(roster, db)
        records = {r["enrollment_id"]:r for r in db.execute("SELECT r.* FROM student_accommodations a JOIN accommodation_revisions r ON r.id=a.revision_id WHERE a.section_id=?", (roster["section_id"],))}
        for student in roster["students"]:
            row = records.get(student["id"])
            if row:
                snapshot = json.loads(row["snapshot_json"])
                student["accommodations"] = {"items":self.active(snapshot),"metadata":snapshot["metadata"],"updated_at":row["source_updated_at"],"imported_at":row["created_at"],"job_id":row["job_id"]}
        roster["accommodation_count"] = sum(bool(s.get("accommodations",{}).get("items")) for s in roster["students"])
        return self.dr_exams.attach(roster, db) if hasattr(self, 'dr_exams') else roster

    def overview(self):
        with self.rosters.lock(), connect(self.path) as db:
            sections = {r[0] for r in db.execute('SELECT DISTINCT section_id FROM student_accommodations UNION SELECT DISTINCT section_id FROM dr_exam_bookings')}
            students, terms = [], {}
            for group in self.groups(current_only=False):
                if not group['definition_id'] or not group['term'] or not group['year']:
                    continue
                term = {k:group[k] for k in ('term','term_other','year')}
                key = encoded(term)
                terms[key] = {**term,'is_current':group['is_current'] or terms.get(key,{}).get('is_current',False)}
                for number in group['sections']:
                    owner, section = self.rosters.resolve(group['id'], number)
                    if section['id'] not in sections:
                        continue
                    with self.rosters.files(owner) as root:
                        roster = self.attach(self.rosters.response(owner,section,self.rosters.read_payload(root,owner['roster_initialized'])),db)
                    for student in roster['students']:
                        if not student.get('accommodations') and not student.get('dr_exams'):
                            continue
                        name = next((v for k,v in student['fields'].items() if k.casefold()=='name'),student['uin'])
                        students.append({'id':student['id'],'uin':student['uin'],'name':name,'code':group['code'],
                            **term,'term_id':owner['id'],'section':number,'section_id':section['id'],'is_current':number in group['current_sections'],
                            'course_url':f"#course/{group['id']}/section/{number}/roster",
                            'accommodations':student.get('accommodations'),'dr_exams':student.get('dr_exams',[])})
            flags = dr_quiz_taker_states(db, [s['id'] for s in students])  # ACCOMMODATIONS "DR QUIZ TAKERS"
            offered = dr_quiz_taker_offered(db, [s['id'] for s in students])
            students = [{**s, 'dr_quiz_taker': flags[s['id']], 'dr_quiz_taker_offered': s['id'] in offered} for s in students]
            return {'terms':list(terms.values()),'students':students,'last_imports':self.last_imports(db)}

    @staticmethod
    def last_imports(db):
        """The latest import of each export (read only): when, its Inbox record and how many rows could not be imported."""
        present = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='inbox_deletions'").fetchone()
        latest = {}
        for kind, table in (('accommodations', 'accommodation_imports'), ('dr_exams', 'dr_exam_imports')):
            if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
                latest[kind] = None
                continue
            row = db.execute(f"""SELECT i.job_id,i.created_at,i.result_json FROM {table} i JOIN inbox_jobs j ON j.id=i.job_id
                WHERE {PRESENT.format('j.message_id') if present else '1'} ORDER BY i.created_at DESC,i.rowid DESC LIMIT 1""").fetchone()
            if not row:
                latest[kind] = None
                continue
            counts = json.loads(row['result_json']).get('counts', {})
            latest[kind] = {'job_id': row['job_id'], 'imported_at': row['created_at'],
                            'counts': {k: counts.get(k, 0) for k in ('added', 'updated', 'unchanged')},
                            'not_imported': sum(counts.get(k, 0) for k in ('unmatched', 'ambiguous', 'stale'))}
        return latest


def register(app, service):
    api = Blueprint("accommodations", __name__, url_prefix="/api/accommodations")

    @api.get("/options")
    def options():
        return jsonify(service.options())

    @api.get('/overview')
    def overview():
        return jsonify(service.overview())

    @api.get('/exams/<booking_id>/history')
    def exam_history(booking_id):
        return jsonify(service.dr_exams.history(booking_id))

    def importer():
        if '/exams/' in request.path:
            from dr_exam_import import parse_dr_exams
            return service.dr_exams, parse_dr_exams, 'accommodations.exams.sync'
        return service, parse_accommodations, 'accommodations.sync'

    def upload():
        if set(request.files) != {"file"} or len(request.files.getlist("file")) != 1:
            raise ValueError("Choose one accommodation export.")
        file = request.files["file"]; content = file.read(MAX_UPLOAD + 1)
        try:
            term = json.loads(request.form.get("term", "null"))
        except ValueError:
            raise ValueError("Choose a valid teaching term.")
        return file.filename, content, term

    @api.post("/preview")
    @api.post("/exams/preview")
    def preview():
        filename, content, term = upload()
        processor, parse, _ = importer()
        return jsonify(processor.preview(parse(content, filename), term))

    @api.post("/import")
    @api.post("/exams/import")
    def submit():
        filename, content, term = upload()
        metadata = {"term":term, "preview_hash":request.form.get("preview_hash")}; validate_metadata(metadata)
        key = request.form.get("idempotency_key")
        processor, parse, action = importer()
        command = {"action":action,"metadata":metadata}
        files = [{"filename":filename,"content":content}]
        # A lost HTTP response may be retried after the worker has changed the
        # roster preview. Let Inbox verify the original content fingerprint first.
        if key:
            with service.rosters.lock(), connect(service.path) as db:
                existing = db.execute("SELECT 1 FROM inbox_messages WHERE dedupe_key=?", (encoded(["web","workspace",key]),)).fetchone()
            if existing:
                return jsonify(service.inbox.submit(command, files, idempotency_key=key)), 202
        plan = processor.preview(parse(content, filename), term)
        if plan["preview_hash"] != metadata["preview_hash"]:
            raise Conflict("The import preview changed. Preview the file again.")
        if not any(plan["counts"][k] for k in ("added","updated","unchanged")):
            raise ValueError("No rows match a current roster. Resolve the preview issues first.")
        job = service.inbox.submit(command, files, idempotency_key=key)
        return jsonify(job), 202

    # Import from AIM: the instructor signs in to a private browser here; its exports go to the previews above.
    from aim_import import register as register_aim
    register_aim(app, api)
    app.register_blueprint(api)
