"""Durable allocation and private provenance; no teaching action dispatch."""
import base64
import fcntl
import json
import re
import os
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager, ExitStack
from pathlib import PurePosixPath

from werkzeug.exceptions import Conflict, NotFound

from certified_archive import certified, facts as archive_facts, pair_activity
from course_storage import timestamp, StorageError
from rosters import connect
from teach_inbox.service import encoded, sha, PRESENT
from . import tokens, process
from .media import PROFILE, LAYOUT, MAX_OUTPUT_PAGES, DEFAULT_MEDIA, label_media
from .layouts import get_layout
from .origins import REGISTRATION_PREFIX, batch_origin, is_registration
from .quiz_latex import unversioned
from .storage import DocumentFiles, initialize, publish, read_verified

WORKER_LOCK = "7a0ad900-a521-4667-8e2c-7638463f842d"
# QUIZZES "A COPY BY EMAIL": a student's own copy that Mail sent them is handed out; its pages are never printed again.
EMAILED_COPY = "This is a student's own copy, already emailed to them, so it is never printed again. Print a new copy instead."


def seat_order(label):
    """Natural order of seat labels (A2 before A10), the same rule the seating chart uses."""
    return [int(part) if part.isdigit() else part.casefold() for part in re.split(r"(\d+)", str(label or ""))]


def identifier():
    return str(uuid.uuid4())


def text_field(value, label, maximum=160):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f"{label} requires 1–{maximum} characters.")
    return value.strip()


class DocumentService:
    def __init__(self, rosters, inbox):
        self.rosters, self.inbox, self.path = rosters, inbox, rosters.path
        self.seating = None  # Set by the app once seating charts exist; print plans come from it.
        self.exams = None  # Set by the app: a new print job extends its term's exam record to its sections (EXAMS).
        with rosters.lock():
            initialize(self.path)
        self.files = DocumentFiles(rosters)
        # The last interleaved stack served (`section_stack`), as (key, bytes): ONE entry, keyed by each job's id and verified output
        # digest, both immutable for a ready job (PERFORMANCE "PROCESS-WIDE CACHES"; about 75 MB at most).
        self._stack_memo = None
        self._stack_lock = threading.Lock()

    @contextmanager
    def worker_lock(self):
        if self.rosters.storage:
            with self.rosters.storage.lock(WORKER_LOCK, blocking=False):
                yield
        else:
            fd = os.open(self.path.parent / ".document-worker.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            try:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError as error:
                    raise Conflict("The document worker is already processing a job.") from error
                yield
            finally:
                os.close(fd)

    @staticmethod
    def event(db, kind, actor, detail, assignment_id=None, batch_id=None, copy_id=None):
        db.execute("""INSERT INTO document_events(assignment_id,batch_id,copy_id,actor,kind,detail_json,created_at)
            VALUES(?,?,?,?,?,?,?)""", (assignment_id, batch_id, copy_id, actor, kind, encoded(detail), timestamp()))

    def create_assignment(self, definition_id, key, title, actor="workspace"):
        key, title = text_field(key, "Assignment key"), text_field(title, "Title", 240)
        with self.rosters.lock(), self.files.owner_lock(definition_id), connect(self.path) as db:
            return self._create_assignment(db, definition_id, key, title, actor)

    def _create_assignment(self, db, definition_id, key, title, actor="workspace"):
        """Catalog part, for workflows already holding the lifecycle and definition locks."""
        key, title = text_field(key, "Assignment key"), text_field(title, "Title", 240)
        if not db.execute("SELECT 1 FROM course_definitions WHERE id=?", (definition_id,)).fetchone():
            raise ValueError("Choose a retained course definition.")
        existing = db.execute("SELECT * FROM assignments WHERE definition_id=? AND assignment_key=?", (definition_id, key)).fetchone()
        if existing:
            if existing["title"] != title:
                raise Conflict("That assignment key is already used by another title.")
            return dict(existing)
        result = {"id": identifier(), "definition_id": definition_id, "assignment_key": key, "title": title, "created_at": timestamp()}
        db.execute("INSERT INTO assignments VALUES(?,?,?,?,?)", tuple(result.values()))
        self.event(db, "assignment_created", actor, {"key": key}, assignment_id=result["id"])
        return result

    def rename_assignment(self, assignment_id, data, actor="workspace"):
        """Gives an assignment a new title (requirement, 2026-09-27). The assignment stays the same
        retained identity (id, course, key); the title changes only through a recorded `assignment_names` row keeping the earlier
        title, which the catalog trigger requires. Everything that shows the assignment shows the new title from then on; what was
        printed, sent or recorded keeps its text. An exam record still titled like the assignment (never given a title of its own)
        takes the new title too, with an entry in its history. A request ID makes a repeat harmless."""
        data = data if isinstance(data, dict) else {}
        title = text_field(data.get("title"), "Title", 240)
        request_id = data.get("request_id")
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128 or any(ord(c) < 32 for c in request_id):
            raise ValueError("A request ID is required.")
        with connect(self.path) as db:
            found = db.execute("SELECT * FROM assignments WHERE id=?", (str(assignment_id),)).fetchone()
        if not found:
            raise NotFound("Assignment not found.")
        with self.rosters.lock(), self.files.owner_lock(found["definition_id"]), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            earlier = db.execute("SELECT * FROM assignment_names WHERE request_id=?", (request_id,)).fetchone()
            if earlier:
                if earlier["assignment_id"] != found["id"] or earlier["title"] != title:
                    raise Conflict("That request ID was already used for another rename.")
                return self._assignment_row(db, found["id"])
            current = db.execute("SELECT * FROM assignments WHERE id=?", (found["id"],)).fetchone()
            if current["title"] == title:
                return self._assignment_row(db, found["id"])
            if db.execute("SELECT 1 FROM assignments WHERE definition_id=? AND id!=? AND lower(trim(title))=lower(trim(?))",
                          (current["definition_id"], current["id"], title)).fetchone():
                raise Conflict("Another assignment of this course already has that title.")
            when = timestamp()
            db.execute("INSERT INTO assignment_names VALUES(?,?,?,?,?,?,?)", (identifier(), current["id"], title, current["title"], request_id, actor, when))
            db.execute("UPDATE assignments SET title=? WHERE id=?", (title, current["id"]))
            self.event(db, "assignment_renamed", actor, {"before": current["title"], "title": title}, assignment_id=current["id"])
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='exam_events'").fetchone():
                for event in db.execute("SELECT id,title FROM exam_events WHERE assignment_id=? AND title=?", (current["id"], current["title"])).fetchall():
                    db.execute("UPDATE exam_events SET title=?,version=version+1,updated_at=? WHERE id=?", (title, when, event["id"]))
                    db.execute("INSERT INTO exam_history(event_id,kind,detail_json,created_at) VALUES(?,?,?,?)",
                               (event["id"], "settings", encoded({"before": event["title"], "title": title, "source": "assignment_renamed"}), when))
            return self._assignment_row(db, current["id"])

    @staticmethod
    def _assignment_row(db, assignment_id):
        row = dict(db.execute("SELECT * FROM assignments WHERE id=?", (assignment_id,)).fetchone())
        row["names"] = [dict(r) for r in db.execute("SELECT title,previous,actor,created_at FROM assignment_names WHERE assignment_id=? ORDER BY rowid",
                                                    (assignment_id,))]
        return row

    def register_version(self, assignment_id, label, pdf, sources, compiler, actor="workspace"):
        label = text_field(label, "Version label")
        # The compiler retains the supplied class and bounded build log in
        # addition to its maximum 100 uploaded inputs (25 MB combined).
        if not isinstance(sources, dict) or not sources or len(sources) > 102:
            raise ValueError("Retain at most 100 source inputs plus the class and build log.")
        total = 0
        for name, content in sources.items():
            if not isinstance(name, str):
                raise ValueError("Source inputs need safe relative filenames and bytes.")
            path = PurePosixPath(name)
            if (not name or len(name) > 200 or path.is_absolute() or
                    any(part in (".", "..") for part in name.split("/")) or "\\" in name or str(path) != name or not isinstance(content, bytes)):
                raise ValueError("Source inputs need safe relative filenames and bytes.")
            if any(parent.as_posix() in sources for parent in path.parents if parent != PurePosixPath('.')):
                raise ValueError("A source filename cannot also be another source's directory.")
            total += len(content)
        if total > 56 * 1024**2:
            raise ValueError("Retained source inputs and build records exceed 56 MB.")
        info = process.run("inspect", pdf)
        manifest = encoded([{"path": name, "sha256": sha(content)} for name, content in sorted(sources.items())]).encode()
        version_id = identifier()
        base = f"versions/{version_id}"
        with self.rosters.lock():
            with connect(self.path) as db:
                assignment = db.execute("SELECT * FROM assignments WHERE id=?", (assignment_id,)).fetchone()
                if not assignment:
                    raise NotFound("Assignment not found.")
                if db.execute("SELECT 1 FROM assignment_versions WHERE assignment_id=? AND label=?", (assignment_id, label)).fetchone():
                    raise Conflict("Version labels are immutable. Create a new version label.")
            with self.files.files("definition", assignment["definition_id"], write=True) as root:
                for name, content in sources.items():
                    publish(root / base / "sources" / name, content)
                publish(root / base / "sources.json", manifest)
                publish(root / base / "canonical.pdf", pdf)
                result = {"id": version_id, "assignment_id": assignment_id, "label": label,
                          "source_path": base + "/sources.json", "source_manifest_sha256": sha(manifest),
                          "canonical_path": base + "/canonical.pdf", "canonical_sha256": sha(pdf), "page_count": info["page_count"],
                          "compiler_json": encoded(compiler), "layout_profile": info["layout_profile"], "created_at": timestamp()}
                with connect(self.path) as db:
                    db.execute("INSERT INTO assignment_versions VALUES(?,?,?,?,?,?,?,?,?,?,?)", tuple(result.values()))
                    self.event(db, "version_registered", actor, {"version_id": version_id, "canonical_sha256": sha(pdf)}, assignment_id=assignment_id)
        return result

    @staticmethod
    def owner(batch):
        """Whose files and lock a print job uses: its section, or its term when it names none (migration 013)."""
        return ("section", batch["section_id"]) if batch["section_id"] else ("term", batch["term_id"])

    @staticmethod
    def _copies(quantity, students):
        if type(quantity) is not int or not 1 <= quantity <= 500:
            raise ValueError("Quantity is the number of copies, between 1 and 500.")
        students = [None] * quantity if students is None else students
        if not isinstance(students, list) or len(students) != quantity or any(s is not None and (not isinstance(s, str) or len(s) != 10 or not s.isascii() or not s.isdigit()) for s in students):
            raise ValueError("Supply one enrollment ID or null per requested copy.")
        return quantity, students

    def register_stamp_pages(self, assignment_id, page_count, actor="workspace"):
        """Reuse or register the assignment's stamps-only version: N blank pages with the layout contract."""
        from .media import blank_pages, MAX_PAGES
        if type(page_count) is not int or not 1 <= page_count <= MAX_PAGES:
            raise ValueError("Stamps-only exports need between 1 and 100 pages per copy.")
        label = f"Stamps only · {page_count} page{'s' if page_count != 1 else ''}"
        with connect(self.path) as db:
            existing = db.execute("SELECT * FROM assignment_versions WHERE assignment_id=? AND label=?", (assignment_id, label)).fetchone()
        if existing:
            return dict(existing)
        details = {"generator": "faculty-stamps-only", "page_count": page_count, "paper": "letter", "layout_profile": PROFILE}
        sources = {"stamps-only.json": encoded(details).encode()}
        return self.register_version(assignment_id, label, blank_pages(page_count), sources, details, actor)

    def register_dr_copy(self, assignment_id, version_id, template_id, pages, evidence, actor="automatic"):
        """Reuse or register the document the DR center's copy of one version is scanned under (GRADING "DR COPIES"): a label-only
        document like the stamps-only one (the copy is printed elsewhere), which also names the version it copies, the blank made
        from its Word file and its sides. A copy printed WITHOUT page codes has one label, for page 1, and its other sides follow by
        position (`faculty-stamps-only`). A copy whose Word file carries its page codes (`evidence["stamps"]`, DR COPIES > STAMPS) is
        a document of that many pages, each with its own codes at the two corners the file prints them at (`faculty-dr-copy`), so
        its papers are whole when every page is there, as any paper stamped on every page. Its canonical is blank: the paper is the
        Word file, which only the DR center prints (`create_batch` refuses any job but the copy's own). Owner: the course-wide
        assignment, as every version; it refers to the version and the template, and copies neither."""
        from .media import blank_pages
        from .layouts import CORNERS_PROFILE
        if type(pages) is not int or not 1 <= pages <= 100:
            raise ValueError("A DR copy has between 1 and 100 sides.")
        stamped = bool(evidence.get("stamps"))
        with connect(self.path) as db:
            source = db.execute("SELECT label FROM assignment_versions WHERE id=? AND assignment_id=?", (version_id, assignment_id)).fetchone()
            if not source:
                raise NotFound("That version is not part of this assignment.")
            for row in db.execute("""SELECT * FROM assignment_versions WHERE assignment_id=? AND json_extract(compiler_json,'$.dr_copy.version_id')=?
                                     ORDER BY created_at DESC""", (assignment_id, version_id)):
                copy = json.loads(row["compiler_json"])["dr_copy"]
                if (copy["template_id"] == template_id and bool(copy.get("stamps")) == stamped
                        and copy.get("revision_id") == evidence.get("revision_id") and copy.get("stamps") == evidence.get("stamps")):
                    return dict(row)
            taken = {row[0] for row in db.execute("SELECT label FROM assignment_versions WHERE assignment_id=?", (assignment_id,))}
        label = base = f"DR copy · {source['label'][:130]}" + (" · page codes" if stamped else "")
        number = 2
        while label in taken:  # a copy made again from a changed Word file: versions are immutable, so it is a new document
            label, number = f"{base} ({number})", number + 1
        labels, profile = (pages, CORNERS_PROFILE) if stamped else (1, PROFILE)
        details = {"generator": "faculty-dr-copy" if stamped else "faculty-stamps-only", "page_count": labels, "paper": "letter",
                   "layout_profile": profile, "dr_copy": {"version_id": version_id, "template_id": template_id, "pages": pages, **evidence}}
        return self.register_version(assignment_id, label, blank_pages(labels, layout_profile=profile),
                                     {"dr-copy.json": encoded(details).encode()}, details, actor)

    def create_batch(self, version_id, section_id, quantity, request_id, students=None, reprint_of=None, actor="workspace",
                     seating_chart_id=None, extras=0, seat_marks=False, caption=None, term_id=None, section_ids=None, sheet_labels=False,
                     fill_names=False):
        """Allocate a print job's copies and page identities.

        A job names its section (`section_id`), or, printed without a seating chart for the term as one stack, only its term
        (`section_id` None and `term_id`; migration 013, requirement, 2026-10-01): its copies then belong to no section, and a copy's
        section is its student's own once the copy is named. Such a job lists the sections it is for (`section_ids`), by reference.
        With `sheet_labels` every copy also gets one label for its student's bubble sheet (migration 015), printed with the peel-off
        labels only, never on a page.
        """
        request_id = text_field(request_id, "Request ID")
        if section_id is None:
            if not isinstance(term_id, str) or not term_id:
                raise ValueError("Choose a permanent course section or term.")
            if seating_chart_id is not None:
                raise ValueError("A seating chart prints one job per section.")
            if (not isinstance(section_ids, list) or not section_ids or len(section_ids) > 50
                    or any(not isinstance(item, str) for item in section_ids) or len(set(section_ids)) != len(section_ids)):
                raise ValueError("Choose the distinct sections this print job is for.")
            section_ids = sorted(section_ids)
        elif section_ids is not None:
            raise ValueError("A section's print job is for that section only.")
        if seat_marks not in (True, False):
            raise ValueError("Say whether to print a visible seat label beside each stamp.")
        if type(sheet_labels) is not bool:
            raise ValueError("Say whether to print a Scantron label for each copy.")
        if type(fill_names) is not bool:
            raise ValueError("Say whether to fill in the students' names.")
        if caption is not None:
            # Printed beside every copy's stamp, followed by its copy number. Never the version: nothing stamped on the page may say
            # which version a paper is (requirement, 2026-10-01; drafted quizzes print no caption at all). Label-maker labels keep it.
            caption = unversioned(" ".join(text_field(caption, "Caption", 80).split()), "The caption")
            if seating_chart_id is not None:
                raise ValueError("A seating chart writes each copy's own caption.")
        if seating_chart_id is not None:
            if not isinstance(seating_chart_id, str) or not seating_chart_id or self.seating is None:
                raise ValueError("Choose a retained seating chart.")
            if type(extras) is not int or not 0 <= extras <= 500:
                raise ValueError("Extra unassigned copies must be a whole number between 0 and 500.")
        else:
            if seat_marks:
                raise ValueError("Seat labels need a seating chart; unassigned copies have no seat.")
            quantity, students = self._copies(quantity, students)
        # NAME FILLED IN (migration 017; requirement, 2026-10-18): each named copy also prints its student's name on the page's "Name"
        # line, read from the roster now and kept, so the copy prints the same words whatever the roster says later.
        names = {}
        if fill_names:
            if seating_chart_id is not None or not any(students):
                raise ValueError("Filling in names needs copies printed for named students.")
            roster = self.section_students(section_id) if section_id is not None else self.term_students(term_id)
            names = {item["enrollment_id"]: " ".join(item["name"].split())[:120] for item in roster["students"]
                     if item.get("name") and item["name"] != item["enrollment_id"]}
        plan = None
        with self.rosters.lock():
            # Establish the permanent section (or term) owner lock before any transaction.
            with connect(self.path) as db:
                if section_id is not None:
                    section = db.execute("SELECT term_id FROM course_sections WHERE id=?", (section_id,)).fetchone()
                    if not section:
                        raise ValueError("Choose a permanent course section.")
                    if term_id is not None and term_id != section["term_id"]:
                        raise ValueError("The section belongs to another term.")
                    term_id = section["term_id"]
                elif not db.execute("SELECT 1 FROM course_terms WHERE id=?", (term_id,)).fetchone():
                    raise ValueError("Choose a permanent course term.")
                elif db.execute(f"SELECT COUNT(*) FROM course_sections WHERE term_id=? AND id IN ({','.join('?' * len(section_ids))})",
                                (term_id, *section_ids)).fetchone()[0] != len(section_ids):
                    raise ValueError("Every section must belong to the print job's term.")
                version = db.execute("SELECT * FROM assignment_versions WHERE id=?", (version_id,)).fetchone()
                if not version:
                    raise ValueError("Choose a registered assignment version.")
                if json.loads(version["compiler_json"]).get("dr_copy") and not is_registration(request_id):
                    # GRADING "DR COPIES": the copy's paper is its Word file; its only jobs give its papers their identities.
                    raise ValueError("The DR center prints this copy from its Word file. Print one of the quiz's own versions.")
                if seating_chart_id is not None:
                    # One copy per seated student of this section, in seat order, plus unassigned extras.
                    plan = self.seating.print_plan(db, seating_chart_id, section_id, version["assignment_id"])
                    if not plan["copies"]:
                        raise ValueError("The seating chart has nobody seated in this section.")
                    quantity, students = self._copies(len(plan["copies"]) + extras, [copy["enrollment_id"] for copy in plan["copies"]] + [None] * extras)
                layout = get_layout(version["layout_profile"])
                hashed = [version_id, section_id, quantity, students, reprint_of, layout]
                if seating_chart_id is not None:
                    hashed += [seating_chart_id, extras]
                if seat_marks:
                    hashed.append({"seat_marks": True})  # Earlier requests without the option keep their hash.
                if caption is not None:
                    hashed.append({"caption": caption})  # Likewise.
                if section_id is None:
                    hashed.append({"term_id": term_id, "section_ids": section_ids})  # Likewise: a section's job keeps its hash.
                if sheet_labels:
                    hashed.append({"sheet_labels": True})  # Likewise.
                if fill_names:
                    hashed.append({"fill_names": True})  # Likewise.
                request_hash = sha(encoded(hashed).encode())
            with self.files.files(*self.owner({"section_id": section_id, "term_id": term_id}), write=True), connect(self.path) as db:
                db.execute("BEGIN IMMEDIATE")
                existing = db.execute("SELECT id,request_hash FROM print_batches WHERE request_id=?", (request_id,)).fetchone()
                if existing:
                    if existing["request_hash"] != request_hash:
                        raise Conflict("The request ID already identifies different print instructions.")
                    batch_id = existing["id"]
                else:
                    if quantity * version["page_count"] > MAX_OUTPUT_PAGES:
                        raise ValueError("Split this request into batches of at most 1500 printed pages.")
                    batch_id, created = identifier(), timestamp()
                    captions = {copy["enrollment_id"]: copy for copy in (plan["copies"] if plan else [])}
                    try:
                        db.execute("""INSERT INTO print_batches(id,assignment_version_id,section_id,quantity,status,creator,request_id,request_hash,
                            configuration_json,reprint_of_batch_id,created_at,updated_at,seating_chart_id,seat_marks,term_id) VALUES(?,?,?,?,'pending',?,?,?,?,?,?,?,?,?,?)""",
                            (batch_id, version_id, section_id, quantity, actor, request_id, request_hash, encoded(layout), reprint_of, created, created, seating_chart_id, int(seat_marks), term_id))
                        db.executemany("INSERT INTO print_batch_sections(print_batch_id,section_id) VALUES(?,?)",
                                       [(batch_id, item) for item in section_ids or []])
                        for number, enrollment in enumerate(students, 1):
                            copy_id = identifier()
                            info = captions.get(enrollment) if enrollment else None
                            db.execute("""INSERT INTO printed_copies(id,print_batch_id,copy_number,enrollment_id,created_at,updated_at,seat_label,caption)
                                          VALUES(?,?,?,?,?,?,?,?)""", (copy_id, batch_id, number, enrollment, created, created,
                                                                        info["seat_label"] if info else "",
                                                                        info["caption"] if info else f"{caption} · Copy {number}" if caption else ""))
                            if names.get(enrollment):
                                db.execute("INSERT INTO printed_copy_name_fills(printed_copy_id,text,created_at) VALUES(?,?,?)",
                                           (copy_id, names[enrollment], created))
                            for page in range(1, version["page_count"] + 1):
                                self._allocate_stamp(db, copy_id, page, created, version["layout_profile"])
                            if sheet_labels:
                                self._allocate_sheet_label(db, copy_id, created)
                    except sqlite3.IntegrityError as error:
                        raise ValueError("Print instructions violate course, section, or roster ownership.") from error
                    # EXAMS (requirement, 2026-10-03): printing for a section the term's exam record lacks extends the record, in this
                    # transaction. A registration for an unlabeled scan is not printing; its association keeps the section check.
                    extended = self.exams.extend_for_print(db, term_id, version["assignment_id"], section_ids or [section_id], batch_id) \
                        if self.exams is not None and not is_registration(request_id) else []
                    self.event(db, "batch_allocated", actor, {"quantity": quantity, "reprint_of": reprint_of, "seating_chart_id": seating_chart_id,
                                                              "seated_copies": len(captions), "extras": extras if plan else None, "seat_marks": seat_marks,
                                                              **({"sheet_labels": True} if sheet_labels else {}),
                                                              **({"term_id": term_id, "section_ids": section_ids} if section_id is None else {}),
                                                              **({"exam_sections_added": extended} if extended else {})}, batch_id=batch_id)
        return self.get_batch(batch_id)

    @staticmethod
    def _allocate_stamp(db, copy_id, page, created, layout_profile=PROFILE, symbology="DataMatrix", state="allocated"):
        layout = get_layout(layout_profile)
        for _ in range(100):
            token, alias = tokens.generate_token(), tokens.generate_alias()
            tokens.parse_token(token)
            stamp_id = identifier()
            try:
                db.execute("""INSERT INTO page_stamps VALUES(?,?,?,?,1,?,?,?,?,?,?)""",
                           (stamp_id, copy_id, page, token, symbology, alias, encoded(layout), state, created, created))
                return stamp_id
            except sqlite3.IntegrityError as error:
                if "page_stamps.token" not in str(error) and "page_stamps.recovery_alias" not in str(error):
                    raise
        raise Conflict("Could not reserve a unique page identity. No tokens were reassigned.")

    @staticmethod
    def _allocate_sheet_label(db, copy_id, created):
        """The copy's Scantron label: a token no page stamp holds (migration 015)."""
        for _ in range(100):
            token = tokens.generate_token()
            if db.execute("SELECT 1 FROM page_stamps WHERE token=?", (token,)).fetchone():
                continue
            try:
                db.execute("INSERT INTO sheet_labels(id,printed_copy_id,token,created_at) VALUES(?,?,?,?)", (identifier(), copy_id, token, created))
                return
            except sqlite3.IntegrityError as error:
                if "sheet_labels.token" not in str(error):
                    raise
        raise Conflict("Could not reserve a unique Scantron label. No tokens were reassigned.")

    @staticmethod
    def sheet_label(db, token):
        """The printed copy a Scantron label names, with its job's state, or None."""
        row = db.execute("""SELECT l.id label_id,c.id copy_id,c.enrollment_id,b.id batch_id,b.status batch_status,b.superseded_at,v.assignment_id
            FROM sheet_labels l JOIN printed_copies c ON c.id=l.printed_copy_id JOIN print_batches b ON b.id=c.print_batch_id
            JOIN assignment_versions v ON v.id=b.assignment_version_id WHERE l.token=?""", (token,)).fetchone()
        return dict(row) if row else None

    def issue_sequence_stamp(self, db, copy_id, page, created):
        """The identity of a page that never carried a label, behind an issued first-page label (migration 008).

        The caller holds the roster lock and an open transaction, and has already
        checked the scan: the page 1 label, an even cadence and this side's position.
        The token is reserved like any other and never printed or reassigned.
        """
        stamp_id = self._allocate_stamp(db, copy_id, page, created, symbology="sequence", state="issued")
        batch_id = db.execute("SELECT print_batch_id FROM printed_copies WHERE id=?", (copy_id,)).fetchone()[0]
        self.event(db, "sequence_identity_issued", "sequence", {"page_number": page, "stamp_id": stamp_id}, batch_id=batch_id, copy_id=copy_id)
        return stamp_id

    def get_batch(self, batch_id):
        with connect(self.path) as db:
            row = db.execute("""SELECT b.*,v.label version_label,v.page_count,v.assignment_id,a.title,a.definition_id,s.number section_number,
                t.term,t.term_other,t.year,t.code FROM print_batches b JOIN assignment_versions v ON v.id=b.assignment_version_id
                JOIN assignments a ON a.id=v.assignment_id LEFT JOIN course_sections s ON s.id=b.section_id JOIN course_terms t ON t.id=b.term_id
                WHERE b.id=?""", (batch_id,)).fetchone()
            if not row:
                raise NotFound("Print batch not found.")
            result = dict(row)
            result["origin"] = batch_origin(result["request_id"], result["seating_chart_id"])
            room = db.execute("SELECT r.name FROM seating_charts c JOIN rooms r ON r.id=c.room_id WHERE c.id=?",
                              (result["seating_chart_id"],)).fetchone() if result["seating_chart_id"] else None
            result["room_name"] = room[0] if room else ""
            result["copies"] = [dict(r) for r in db.execute("SELECT * FROM printed_copies WHERE print_batch_id=? ORDER BY copy_number", (batch_id,))]
            result["events"] = [dict(r) for r in db.execute("SELECT actor,kind,detail_json,created_at FROM document_events WHERE batch_id=? ORDER BY id", (batch_id,))]
            result["labels"] = [dict(r) for r in db.execute("SELECT media,path,sha256,created_at FROM print_batch_labels WHERE print_batch_id=? ORDER BY created_at", (batch_id,))]
            result["scanned"] = self._scanned(db, batch_id)
            if not result["section_id"]:
                result["sections"] = self.job_sections(db, batch_id)
            return result

    @staticmethod
    def job_sections(db, batch_id):
        """The sections a job printed for the whole term is for (migration 013), in section order: [{id, number}]."""
        return [dict(row) for row in db.execute("""SELECT s.id,s.number FROM print_batch_sections x JOIN course_sections s ON s.id=x.section_id
            WHERE x.print_batch_id=? ORDER BY s.number,s.id""", (batch_id,))]

    @staticmethod
    def _scanned(db, batch_id):
        """Which copies of a job came back in scans (read-only, UX overhaul 2026-09-26).

        A copy counts as scanned when any page of it was observed in a scan that is still in the Inbox (PRESENT: a deleted
        submission counts nowhere), the rule a scan's Assignment page uses for "papers not seen in any scan yet". It says
        nothing about completeness, identity or grading; `scans` names the Inbox jobs that hold its pages.
        """
        present = PRESENT.format("sp.message_id")
        seen = {row[0] for row in db.execute(f"""SELECT DISTINCT p.printed_copy_id FROM printed_copies c JOIN page_stamps p ON p.printed_copy_id=c.id
            JOIN scan_pages sp ON sp.page_stamp_id=p.id WHERE c.print_batch_id=? AND {present}""", (batch_id,))}
        copies = db.execute("SELECT id,copy_number,caption,enrollment_id FROM printed_copies WHERE print_batch_id=? ORDER BY copy_number", (batch_id,)).fetchall()
        scans = [dict(row) for row in db.execute(f"""SELECT j.id job_id,m.received_at,COUNT(DISTINCT p.printed_copy_id) copies
            FROM printed_copies c JOIN page_stamps p ON p.printed_copy_id=c.id JOIN scan_pages sp ON sp.page_stamp_id=p.id
            JOIN inbox_messages m ON m.id=sp.message_id JOIN inbox_jobs j ON j.message_id=m.id
            WHERE c.print_batch_id=? AND {present} GROUP BY j.id ORDER BY m.received_at,j.id""", (batch_id,))]
        return {"copies": len(seen), "total": len(copies), "scans": scans,
                "unseen": [{"copy_number": row["copy_number"], "caption": row["caption"], "enrollment_id": row["enrollment_id"]}
                           for row in copies if row["id"] not in seen]}

    @staticmethod
    def manifest(db, batch_id):
        rows = db.execute("""SELECT p.id stamp_id,p.token,p.page_number,p.recovery_alias,p.symbology,c.id copy_id,c.copy_number,c.seat_label,c.caption
            FROM page_stamps p JOIN printed_copies c ON c.id=p.printed_copy_id
            WHERE c.print_batch_id=? AND p.symbology!='sequence' ORDER BY c.copy_number,p.page_number""", (batch_id,)).fetchall()
        seat_marks = bool(db.execute("SELECT seat_marks FROM print_batches WHERE id=?", (batch_id,)).fetchone()[0])
        # NAME FILLED IN (migration 017): a copy's kept name, printed on any of its pages with a "Name" line; copies without one keep
        # their original manifest shape.
        fills = {r[0]: r[1] for r in db.execute("""SELECT f.printed_copy_id,f.text FROM printed_copy_name_fills f JOIN printed_copies c
            ON c.id=f.printed_copy_id WHERE c.print_batch_id=?""", (batch_id,))}
        result = []
        for index, row in enumerate(rows, 1):
            entry = {"output_page": index, **dict(row)}
            if fills.get(entry["copy_id"]):
                entry["name_fill"] = fills[entry["copy_id"]]
            if not entry.get("caption"):
                # Batches without seating data keep their original manifest shape.
                entry.pop("caption", None)
                entry.pop("seat_label", None)
            elif seat_marks and entry.get("seat_label"):
                # The persisted manifest records exactly what was printed beside the mark.
                entry["seat_mark"] = entry["seat_label"]
            result.append(entry)
        return result

    @classmethod
    def label_manifest(cls, db, batch_id):
        """The peel-off labels: the page stamps of `manifest`, each copy's Scantron label after its pages (migration 015).

        A job without Scantron labels has exactly its print manifest, so labels kept before keep verifying."""
        pages = cls.manifest(db, batch_id)
        sheets = {row["copy_id"]: dict(row) for row in db.execute(
            """SELECT l.token,c.id copy_id,c.copy_number,c.seat_label,c.caption FROM sheet_labels l JOIN printed_copies c ON c.id=l.printed_copy_id
               WHERE c.print_batch_id=?""", (batch_id,))}
        if not sheets:
            return pages
        result, entries = [], iter(pages)
        entry = next(entries, None)
        for copy in db.execute("SELECT id FROM printed_copies WHERE print_batch_id=? ORDER BY copy_number", (batch_id,)).fetchall():
            while entry is not None and entry["copy_id"] == copy["id"]:
                result.append(entry)
                entry = next(entries, None)
            if copy["id"] in sheets:
                sheet = sheets[copy["id"]]
                label = {"token": sheet["token"], "sheet": True, "symbology": "DataMatrix", "recovery_alias": None, "copy_id": sheet["copy_id"],
                         "copy_number": sheet["copy_number"], "page_number": None}
                if sheet["caption"]:
                    label.update(caption=sheet["caption"], seat_label=sheet["seat_label"])
                result.append(label)
        return [{**item, "output_page": index} for index, item in enumerate(result, 1)]

    @staticmethod
    def label_text(db, batch, entries):
        """What each peel-off label says beyond its manifest: its own job's section, and on a seated copy the version the seating chart
        gave the student's seat, as grading reads it (DOCUMENTS "SEAT LABELS NAME THE STUDENT'S VERSION AND SECTION").

        Kept apart from `label_manifest`, which is what a label file is checked against."""
        section = f"Section {batch['section_number']}" if batch["section_id"] else "All sections"
        versions = {row[0]: row[1] for row in db.execute(
            """SELECT c.id,v.exam_version FROM printed_copies c JOIN seating_assignments a ON a.chart_id=? AND a.enrollment_id=c.enrollment_id
               JOIN seating_exam_versions v ON v.chart_id=a.chart_id AND v.seat_id=a.seat_id WHERE c.print_batch_id=?""",
            (batch["seating_chart_id"], batch["id"]))} if batch.get("seating_chart_id") else {}
        return [{**entry, "section": section, **({"exam_version": versions[entry["copy_id"]]} if entry.get("caption") and entry["copy_id"] in versions else {})}
                for entry in entries]

    def resolve_token(self, token, *, authenticated=False, alias=False):
        if not authenticated:
            return None
        try:
            if alias:
                if not tokens.valid_alias(token):
                    return None
            else:
                tokens.parse_token(token)
        except ValueError:
            return None
        with connect(self.path) as db:
            row = db.execute(f"""SELECT p.id stamp_id,p.page_number,p.state,c.id copy_id,c.copy_number,c.enrollment_id,
                b.id batch_id,b.status batch_status,b.superseded_at,b.reprint_of_batch_id,b.section_id,s.number section_number,
                t.id term_id,t.term,t.term_other,t.year,a.definition_id,t.code,a.id assignment_id,a.assignment_key,a.title,
                v.id version_id,v.label version_label,v.page_count FROM page_stamps p
                JOIN printed_copies c ON c.id=p.printed_copy_id JOIN print_batches b ON b.id=c.print_batch_id
                LEFT JOIN course_sections s ON s.id=b.section_id JOIN course_terms t ON t.id=b.term_id
                JOIN assignment_versions v ON v.id=b.assignment_version_id JOIN assignments a ON a.id=v.assignment_id
                WHERE p.{'recovery_alias' if alias else 'token'}=?""", (token,)).fetchone()
            return dict(row) if row else None

    @contextmanager
    def batch_lock(self, batch_id=None, copy_id=None):
        with self.rosters.lock():
            with connect(self.path) as db:
                row = db.execute("SELECT COALESCE(section_id,term_id) FROM print_batches WHERE id=?", (batch_id,)).fetchone() if batch_id else db.execute(
                    "SELECT COALESCE(b.section_id,b.term_id) FROM printed_copies c JOIN print_batches b ON b.id=c.print_batch_id WHERE c.id=?", (copy_id,)).fetchone()
                if not row:
                    raise NotFound("Printed batch or copy not found.")
            with self.files.owner_lock(row[0]):
                yield

    def transition(self, batch_id, operation, actor="workspace"):
        with self.batch_lock(batch_id), connect(self.path) as db:
            row = db.execute("SELECT * FROM print_batches WHERE id=?", (batch_id,)).fetchone()
            if not row:
                raise NotFound("Print batch not found.")
            if operation == "cancel":
                if row["status"] == "ready":
                    raise Conflict("An issued batch cannot be cancelled. Supersede it instead.")
                db.execute("UPDATE print_batches SET status='cancelled',updated_at=? WHERE id=?", (timestamp(), batch_id))
                state = "cancelled"
            elif operation == "supersede":
                if row["status"] != "ready":
                    raise Conflict("Only an issued batch can be superseded.")
                db.execute("UPDATE print_batches SET superseded_at=?,updated_at=? WHERE id=?", (timestamp(), timestamp(), batch_id))
                state = "superseded"
            elif operation == "retry":
                if row["status"] != "failed":
                    raise Conflict("Only failed, unissued jobs can retry their allocations.")
                db.execute("UPDATE print_batches SET status='pending',error='',updated_at=? WHERE id=?", (timestamp(), batch_id))
                state = "allocated"
            else:
                raise ValueError("Unknown print job operation.")
            db.execute("UPDATE page_stamps SET state=?,updated_at=? WHERE printed_copy_id IN (SELECT id FROM printed_copies WHERE print_batch_id=?)", (state, timestamp(), batch_id))
            self.event(db, operation, actor, {}, batch_id=batch_id)
        return self.get_batch(batch_id)

    def set_label_mode(self, batch_id, mode, actor="workspace", evidence=None):
        """Say how a print job's paper is labeled: a Data Matrix on `every_page`, or on the `first_page` of each copy only.

        Issued tokens, pages and files never change. The mode is what scan review
        and exam collection expect to find: with `first_page`, only page 1 of a
        copy is expected to carry a label and the rest of the exam follows it in
        scan order. Either direction is the instructor's explicit, audited choice.
        """
        if mode not in ("every_page", "first_page"):
            raise ValueError("Choose whether every page or only the first page of each copy carries a label.")
        with self.batch_lock(batch_id), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM print_batches WHERE id=?", (batch_id,)).fetchone()
            if not row:
                raise NotFound("Print batch not found.")
            if row["label_mode"] != mode:
                db.execute("UPDATE print_batches SET label_mode=?,updated_at=? WHERE id=?", (mode, timestamp(), batch_id))
                self.event(db, "label_mode", actor, {"from": row["label_mode"], "to": mode, **(evidence or {})}, batch_id=batch_id)
        return self.get_batch(batch_id)

    def set_seat_marks(self, batch_id, enabled, actor="workspace"):
        """Turn the visible seat label on or off for a job still waiting in the print queue.

        Only a pending or failed job qualifies: the worker reads the manifest when it
        renders, and an issued job's pages are already what they are. The creation
        request keeps its hash; the change is its own audited event.
        """
        if enabled not in (True, False):
            raise ValueError("Say whether to print a visible seat label beside each stamp.")
        with self.batch_lock(batch_id), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM print_batches WHERE id=?", (batch_id,)).fetchone()
            if not row:
                raise NotFound("Print batch not found.")
            if not row["seating_chart_id"]:
                raise ValueError("Seat labels need a seating chart; this job's copies have no seats.")
            if row["status"] == "ready":
                raise Conflict("This job is already issued; its pages cannot change. Create a new print batch instead.")
            if row["status"] not in ("pending", "failed"):
                raise Conflict("This job is being rendered right now. Try again once it is ready or has failed.")
            if bool(row["seat_marks"]) != enabled:
                db.execute("UPDATE print_batches SET seat_marks=?,updated_at=? WHERE id=?", (int(enabled), timestamp(), batch_id))
                self.event(db, "seat_marks", actor, {"seat_marks": enabled}, batch_id=batch_id)
        return self.get_batch(batch_id)

    def assign_copy(self, copy_id, enrollment_id, reason, actor="workspace"):
        reason = text_field(reason, "Assignment change reason", 1000)
        with self.batch_lock(copy_id=copy_id), connect(self.path) as db:
            row = db.execute("SELECT * FROM printed_copies WHERE id=?", (copy_id,)).fetchone()
            if not row:
                raise NotFound("Printed copy not found.")
            try:
                db.execute("UPDATE printed_copies SET enrollment_id=?,updated_at=? WHERE id=?", (enrollment_id, timestamp(), copy_id))
            except sqlite3.IntegrityError as error:
                if db.execute("SELECT section_id FROM print_batches WHERE id=?", (row["print_batch_id"],)).fetchone()[0] is None:
                    raise ValueError("The enrollment must belong to a section of this term.") from error
                raise ValueError("The enrollment must belong to the section roster.") from error
            self.event(db, "copy_assignment", actor, {"previous": row["enrollment_id"], "enrollment_id": enrollment_id, "reason": reason}, batch_id=row["print_batch_id"], copy_id=copy_id)
        return self.get_batch(row["print_batch_id"])

    STUDENT_COPY = "student_copy"  # the document event that marks a job as one student's own copy

    def student_copy(self, version_id, enrollment_id, request_id, actor="workspace"):
        """ONE STUDENT'S OWN COPY (QUIZZES "A COPY BY EMAIL"; requirement, 2026-11-08): a print job of one copy of `version_id` named for `enrollment_id`, printed for
        their term and their current sections, and made now, so its PDF can be attached to mail to them. A copy is handed out
        once: their standing copy of this version is given again while it was never sent to a printer or emailed by Export by
        email, else a new one is made. The request ID makes a retry answer the same job."""
        request_id = text_field(request_id, "Request ID")
        if not isinstance(enrollment_id, str) or not enrollment_id:
            raise ValueError("Choose the student the copy is for.")
        with self.rosters.lock():
            with connect(self.path) as db:
                version = db.execute("""SELECT v.id,v.assignment_id,a.definition_id FROM assignment_versions v
                    JOIN assignments a ON a.id=v.assignment_id WHERE v.id=?""", (version_id,)).fetchone()
                if not version:
                    raise NotFound("Assignment version not found.")
                term = db.execute("""SELECT e.term_id FROM student_enrollments e JOIN course_terms t ON t.id=e.term_id
                    WHERE e.id=? AND t.definition_id=?""", (enrollment_id, version["definition_id"])).fetchone()
                if not term:
                    raise ValueError("This student is not in a term of this assignment's course.")
                sections = sorted(row[0] for row in db.execute("""SELECT m.section_id FROM roster_current m
                    JOIN course_sections s ON s.id=m.section_id WHERE m.enrollment_id=? AND s.term_id=?""", (enrollment_id, term[0])))
                if not sections:
                    raise ValueError("This student is on none of the course's current rosters.")
                known = db.execute("SELECT id FROM print_batches WHERE request_id=?", (request_id,)).fetchone()
                standing = None if known else next((row[0] for row in db.execute("""SELECT b.id FROM print_batches b
                    JOIN printed_copies c ON c.print_batch_id=b.id
                    WHERE b.assignment_version_id=? AND c.enrollment_id=? AND b.quantity=1 AND b.superseded_at IS NULL
                    AND b.status IN ('pending','rendering','validating','ready')
                    AND EXISTS(SELECT 1 FROM document_events e WHERE e.batch_id=b.id AND e.kind=?)
                    ORDER BY b.created_at DESC,b.id""", (version_id, enrollment_id, self.STUDENT_COPY))
                                                    if not self.handed_out(db, row[0])), None)
        if standing:
            batch_id, reused = standing, True
        else:
            batch_id, reused = self.create_batch(version_id, None, 1, request_id, [enrollment_id], actor=actor, term_id=term[0],
                                                 section_ids=sections)["id"], False
            with self.batch_lock(batch_id), connect(self.path) as db:
                if not db.execute("SELECT 1 FROM document_events WHERE batch_id=? AND kind=?", (batch_id, self.STUDENT_COPY)).fetchone():
                    self.event(db, self.STUDENT_COPY, actor, {"enrollment_id": enrollment_id, "version_id": version_id}, batch_id=batch_id)
        self.build_now(batch_id)
        batch = self.get_batch(batch_id)
        return {"batch_id": batch_id, "version_id": version_id, "assignment_id": batch["assignment_id"], "title": batch["title"],
                "version_label": batch["version_label"], "enrollment_id": enrollment_id, "reused": reused,
                "route": f"/api/documents/batches/{batch_id}/download", "name": f"{batch['title']}.pdf"}  # the title alone: a student is never told the version

    @staticmethod
    def handed_out(db, batch_id):
        """Whether a print job's copies left Faculty as paper or by Export by email (a failed attempt aside)."""
        return bool(db.execute("""SELECT 1 FROM print_printer_send_batches x JOIN print_printer_sends s ON s.id=x.send_id
            WHERE x.batch_id=? AND s.status!='failed' UNION ALL
            SELECT 1 FROM print_email_exports WHERE batch_id=? AND status!='failed'""", (batch_id, batch_id)).fetchone())

    @classmethod
    def emailed_copy(cls, db, batch_id):
        """Whether a job is a student's own copy that Mail sent them (its PDF attached to a recorded send, uncertain ones included)."""
        if not db.execute("SELECT 1 FROM document_events WHERE batch_id=? AND kind=?", (batch_id, cls.STUDENT_COPY)).fetchone():
            return False
        try:
            return bool(db.execute("SELECT 1 FROM gmail_mail_send_attachments WHERE route=?",
                                   (f"/api/documents/batches/{batch_id}/download",)).fetchone())
        except sqlite3.OperationalError:  # no Mail in this catalog
            return False

    def build_now(self, batch_id, wait=60):
        """Render one job now rather than on the worker's next turn: claimed here when the worker is free, else waited for."""
        deadline = time.monotonic() + wait
        while True:
            batch = self.get_batch(batch_id)
            if batch["status"] == "ready":
                return
            if batch["status"] in ("failed", "cancelled"):
                raise Conflict("The copy could not be made: " + (batch["error"] or batch["status"]))
            claimed = False
            if batch["status"] == "pending":
                try:
                    with self.worker_lock():
                        with self.rosters.lock(), self.files.owner_lock(self.owner(batch)[1]), connect(self.path) as db:
                            claimed = db.execute("UPDATE print_batches SET status='rendering',attempts=attempts+1,updated_at=? WHERE id=? AND status='pending'",
                                                 (timestamp(), batch_id)).rowcount == 1
                            if claimed:
                                self.event(db, "rendering", "worker", {}, batch_id=batch_id)
                        if claimed:
                            try:
                                self._build(batch_id)
                            except Exception:
                                with self.batch_lock(batch_id), connect(self.path) as db:
                                    db.execute("UPDATE print_batches SET status='failed',error='Rendering or verification failed. Inspect source and retry the unissued job.',updated_at=? WHERE id=? AND status NOT IN ('ready','cancelled')", (timestamp(), batch_id))
                                    self.event(db, "build_failed", "worker", {}, batch_id=batch_id)
                except (Conflict, StorageError):
                    pass  # the worker is rendering: it takes this job on its next turn
            if claimed:
                continue
            if time.monotonic() > deadline:
                raise Conflict("The copy is still being made. Try again in a minute.")
            time.sleep(.25)

    def section_students(self, section_id):
        """A section's roster for choosing a copy's student: enrollment IDs, and each student's exported name.

        Names come from the term's verified roster payload, read under the roster lifecycle lock and outside any catalog
        transaction (the storage manager may need its own writer). An unknown section has no students, as before. When the
        payload cannot be read (an archived term whose copy is on an unavailable drive), the enrollment IDs are still given
        with no names, so a copy can still be reassigned.
        """
        from seating import student_name  # seating imports this module; import at call time.
        with self.rosters.lock():
            with connect(self.path) as db:
                term = db.execute("SELECT t.* FROM course_sections s JOIN course_terms t ON t.id=s.term_id WHERE s.id=?", (section_id,)).fetchone()
                ids = [r[0] for r in db.execute("SELECT enrollment_id FROM roster_memberships WHERE section_id=? ORDER BY enrollment_id", (section_id,))]
            if term is None or not ids:
                return {"enrollments": ids, "students": []}
            try:
                with self.rosters.files(dict(term)) as root:
                    payload = self.rosters.read_payload(root, term["roster_initialized"])
            except StorageError:
                return {"enrollments": ids, "students": []}
        records = payload.get("sections", {}).get(section_id, {}).get("students", {})
        students = [{"enrollment_id": enrollment_id, "name": student_name({**records.get(enrollment_id, {}), "id": enrollment_id})} for enrollment_id in ids]
        students.sort(key=lambda item: (item["name"].casefold(), item["enrollment_id"]))
        return {"enrollments": ids, "students": students}

    def term_students(self, term_id):
        """Every section's roster of a term, for naming a copy of a job printed for the whole term (migration 013).

        The same shape as `section_students`, each student also carrying their own section (`section_id`, `section_number`):
        such a copy belongs to whichever section its student is in. Names come from the term's verified roster payload; when it
        cannot be read, the enrollment IDs are still given with no names.
        """
        from seating import student_name  # seating imports this module; import at call time.
        with self.rosters.lock():
            with connect(self.path) as db:
                term = db.execute("SELECT * FROM course_terms WHERE id=?", (term_id,)).fetchone()
                members = db.execute("""SELECT m.enrollment_id,m.section_id,s.number section_number FROM roster_memberships m
                    JOIN course_sections s ON s.id=m.section_id WHERE s.term_id=? ORDER BY m.enrollment_id,s.number""", (term_id,)).fetchall()
            ids = sorted({row["enrollment_id"] for row in members})
            if term is None or not ids:
                return {"enrollments": ids, "students": []}
            try:
                with self.rosters.files(dict(term)) as root:
                    payload = self.rosters.read_payload(root, term["roster_initialized"])
            except StorageError:
                return {"enrollments": ids, "students": []}
        sections = payload.get("sections", {})
        students = [{"enrollment_id": row["enrollment_id"], "section_id": row["section_id"], "section_number": row["section_number"],
                     "name": student_name({**sections.get(row["section_id"], {}).get("students", {}).get(row["enrollment_id"], {}),
                                           "id": row["enrollment_id"]})} for row in members]
        students.sort(key=lambda item: (item["name"].casefold(), item["section_number"], item["enrollment_id"]))
        return {"enrollments": ids, "students": students}

    def build_one(self):
        with self.worker_lock():
            with self.rosters.lock(), ExitStack() as owners:
                with connect(self.path) as db:
                    sections = [r[0] for r in db.execute("SELECT DISTINCT COALESCE(section_id,term_id) owner FROM print_batches WHERE status IN ('pending','rendering','validating') ORDER BY owner")]
                for section_id in sections:
                    owners.enter_context(self.files.owner_lock(section_id))
                with connect(self.path) as db:
                    batch_id = self._claim_batch(db)
                if batch_id is None:
                    return False
            try:
                self._build(batch_id)
            except Exception:
                with self.batch_lock(batch_id), connect(self.path) as db:
                    db.execute("UPDATE print_batches SET status='failed',error='Rendering or verification failed. Inspect source and retry the unissued job.',updated_at=? WHERE id=? AND status NOT IN ('ready','cancelled')", (timestamp(), batch_id))
                    self.event(db, "build_failed", "worker", {}, batch_id=batch_id)
            return True

    def _claim_batch(self, db):
        for row in db.execute("SELECT id FROM print_batches WHERE status IN ('rendering','validating')").fetchall():
            db.execute("UPDATE print_batches SET status='failed',error='Worker interrupted. Explicit retry retains the same tokens.',updated_at=? WHERE id=?", (timestamp(), row[0]))
            self.event(db, "interrupted", "worker", {}, batch_id=row[0])
        row = db.execute("SELECT id FROM print_batches WHERE status='pending' ORDER BY created_at,id LIMIT 1").fetchone()
        if not row:
            return None
        batch_id = row[0]
        db.execute("UPDATE print_batches SET status='rendering',attempts=attempts+1,updated_at=? WHERE id=?", (timestamp(), batch_id))
        self.event(db, "rendering", "worker", {}, batch_id=batch_id)
        return batch_id

    def _build(self, batch_id):
        with self.rosters.lock():
            with connect(self.path) as db:
                batch = dict(db.execute("SELECT * FROM print_batches WHERE id=?", (batch_id,)).fetchone())
                version = dict(db.execute("SELECT v.*,a.definition_id FROM assignment_versions v JOIN assignments a ON a.id=v.assignment_id WHERE v.id=?", (batch["assignment_version_id"],)).fetchone())
                layout = self._batch_layout(db, batch)
                manifest = self.manifest(db, batch_id)
            with self.files.files("definition", version["definition_id"]) as root:
                canonical = read_verified(root, version["canonical_path"], version["canonical_sha256"])
            with self.files.owner_lock(self.owner(batch)[1]), connect(self.path) as db:
                db.execute("UPDATE print_batches SET status='validating',updated_at=? WHERE id=? AND status='rendering'", (timestamp(), batch_id))
        output, checks = process.run("stamp", canonical, manifest, layout_profile=layout["id"])
        manifest_bytes = encoded({"batch_id": batch_id, "canonical_sha256": version["canonical_sha256"], "layout": layout,
                                  "pages": manifest, "output_sha256": sha(output), "validation": checks}).encode()
        # Attempt-specific output permits recovery even if a renderer upgrade
        # changes PDF bytes. Prior failed files remain auditable and unissued.
        base = f"batches/{batch_id}/attempt-{batch['attempts']}"
        with self.rosters.lock():
            with connect(self.path) as db:
                if db.execute("SELECT status FROM print_batches WHERE id=?", (batch_id,)).fetchone()[0] == "cancelled":
                    return
                if self.manifest(db, batch_id) != manifest:
                    raise StorageError("Persisted print mappings changed during rendering.")
            with self.files.files(*self.owner(batch), write=True) as root:
                publish(root / base / "print.pdf", output)
                publish(root / base / "manifest.json", manifest_bytes)
                with connect(self.path) as db:
                    db.execute("BEGIN IMMEDIATE")
                    db.execute("""UPDATE print_batches SET status='ready',output_path=?,output_sha256=?,manifest_path=?,manifest_sha256=?,error='',updated_at=? WHERE id=?""",
                               (base + "/print.pdf", sha(output), base + "/manifest.json", sha(manifest_bytes), timestamp(), batch_id))
                    db.execute("UPDATE page_stamps SET state='issued',updated_at=? WHERE printed_copy_id IN(SELECT id FROM printed_copies WHERE print_batch_id=?)", (timestamp(), batch_id))
                    self.event(db, "issued", "worker", checks, batch_id=batch_id)

    def version_preview(self, version_id):
        """DOCUMENTS: the immutable course-wide canonical, before any printed identities exist."""
        with self.rosters.lock():
            with connect(self.path) as db:
                row = db.execute("""SELECT v.*,a.definition_id FROM assignment_versions v
                    JOIN assignments a ON a.id=v.assignment_id WHERE v.id=?""", (version_id,)).fetchone()
            if row is None:
                raise NotFound("Assignment version not found.")
            with self.files.files("definition", row["definition_id"]) as root:
                return read_verified(root, row["canonical_path"], row["canonical_sha256"])

    def download(self, batch_id, manifest=False):
        with self.rosters.lock():
            batch = self.get_batch(batch_id)
            if batch["status"] != "ready" or batch["superseded_at"]:
                raise Conflict("Only a validated, non-superseded batch is downloadable.")
            with self.files.files(*self.owner(batch)) as root:
                content = read_verified(root, batch["output_path"], batch["output_sha256"])
                receipt = read_verified(root, batch["manifest_path"], batch["manifest_sha256"])
                value = json.loads(receipt)
                with connect(self.path) as db:
                    expected = self.manifest(db, batch_id)
                    layout = self._batch_layout(db, batch)
                if value["pages"] != expected or value["output_sha256"] != sha(content) or value["layout"] != layout:
                    raise StorageError("The output manifest disagrees with the retained catalog.")
                return receipt if manifest else content

    def labels(self, batch_id, media=DEFAULT_MEDIA, actor="workspace"):
        """Peel-off stamps for a ready batch: the same tokens on the chosen label media, built once per media and verified after."""
        label_media(media)
        with self.rosters.lock():
            batch = self.get_batch(batch_id)
            if batch["status"] != "ready" or batch["superseded_at"]:
                raise Conflict("Only a validated, non-superseded batch has printable labels.")
            with self.files.files(*self.owner(batch), write=True) as root:
                kept = next((item for item in batch["labels"] if item["media"] == media), None)
                if kept:
                    return read_verified(root, kept["path"], kept["sha256"])
                with connect(self.path) as db:
                    pages = self.label_manifest(db, batch_id)
                    layout = self._batch_layout(db, batch)
                    shown = self.label_text(db, batch, pages)
                    room = db.execute("SELECT r.name FROM seating_charts c JOIN rooms r ON r.id = c.room_id WHERE c.id = ?",
                                      (batch["seating_chart_id"],)).fetchone() if batch.get("seating_chart_id") else None
                header = {"exam": batch["title"], "version": f"Version {batch['version_label']}",
                          "section": f"Section {batch['section_number']}" if batch["section_id"] else "All sections",
                          "term": f"{batch['term_other'] if batch['term'] == 'Other' else batch['term']} {batch['year']}",
                          "room": room[0] if room else "", "page_count": batch["page_count"]}
                output, checks = process.run("labels", b"", {"pages": shown, "header": header, "media": media}, timeout=300, layout_profile=layout["id"])
                base = batch["output_path"].rsplit("/", 1)[0]
                name = "labels.pdf" if media == "avery-5160" else f"labels-{media}.pdf"
                publish(root / base / name, output)
                with connect(self.path) as db:
                    db.execute("BEGIN IMMEDIATE")
                    if self.label_manifest(db, batch_id) != pages:
                        raise StorageError("Persisted print mappings changed while rendering labels.")
                    db.execute("INSERT INTO print_batch_labels(print_batch_id,media,path,sha256,created_at) VALUES(?,?,?,?,?)",
                               (batch_id, media, f"{base}/{name}", sha(output), timestamp()))
                    self.event(db, "labels_issued", actor, {**checks, "media": media}, batch_id=batch_id)
                return output

    def section_stack(self, batch_ids):
        """One print file for paper that comes in several versions (a drafted quiz, 2026-09-28): the ready, current jobs of one
        assignment and owner (a section, or the term for jobs printed as one stack, migration 013), one per version, their copies
        interleaved (copy 1 of Version A, of B, of C ..., then copy 2 ...), so a stack handed out from the top alternates the
        versions. Like the room file it only re-orders already-verified pages of the retained per-version files; nothing is
        allocated or issued, and those files remain the record. The Print page's one Print PDF of such a stack (GET
        `/api/documents/stack`) and the quiz draft's page both serve it."""
        if (not isinstance(batch_ids, list) or not 1 <= len(batch_ids) <= 26
                or any(not isinstance(item, str) for item in batch_ids) or len(set(batch_ids)) != len(batch_ids)):
            raise ValueError("Choose between 1 and 26 print jobs of one stack.")
        batches = sorted((self.get_batch(batch_id) for batch_id in batch_ids), key=lambda b: (b["version_label"], b["id"]))
        if len({self.owner(b) for b in batches}) != 1 or len({b["assignment_id"] for b in batches}) != 1 or len({b["assignment_version_id"] for b in batches}) != len(batches):
            raise ValueError("A stack needs one print job per version of one assignment, all printed for the same section or term.")
        waiting = [f"Version {b['version_label']}" for b in batches if b["status"] != "ready" or b["superseded_at"]]
        if waiting:
            raise Conflict(f"Every version's print job must be ready and current first; waiting on {', '.join(waiting)}.")
        # Checked only after the ready/current check above, so a superseded job is refused even while its stack is remembered. A ready
        # job's verified output never changes (a retry keeps its tokens), so the same jobs and digests are the same file: the file
        # viewer's describe and its PDF, then Download, read it once instead of re-cutting 70 MB three times.
        key = tuple((b["id"], b["output_sha256"]) for b in batches)
        with self._stack_lock:
            memo = self._stack_memo
        if memo and memo[0] == key:
            return memo[1]
        entries, sources = [], []
        for index, batch in enumerate(batches):
            with connect(self.path) as db:
                manifest = self.manifest(db, batch["id"])
            for position, entry in enumerate(manifest):
                entries.append(((entry["copy_number"], index, entry["page_number"]), index, position))
            sources.append(self.download(batch["id"]))
        entries.sort(key=lambda item: item[0])
        order = [[index, position] for _, index, position in entries]
        content = process.run("reorder", b"", {"sources": [base64.b64encode(content).decode() for content in sources], "pages": order}, timeout=300)[0]
        with self._stack_lock:
            self._stack_memo = (key, content)
        return content

    def room_export(self, batch_ids, kind="print", media=DEFAULT_MEDIA):
        """One file for a whole room in seat order, cut from the retained per-section outputs of one chart export.

        Print jobs stay owned by their sections, with their tokens and validation
        untouched; this only re-orders already-verified pages (the print PDF, and
        roll labels, which are one label per page) so every row's papers and
        labels sit together across sections. Sheet labels are laid out afresh from
        the same manifests and validated like any label file. Nothing is allocated
        or issued, and the same stamps also remain in the per-section files.

        `page-labels` and `scantron-labels` split those labels for jobs printed with Scantron labels (DOCUMENTS "SCANTRON
        LABELS"): the page stamps alone, and each student's bubble sheet label alone, so each prints on its own.
        """
        if kind not in ("print", "labels", "page-labels", "scantron-labels"):
            raise ValueError("Choose the print PDF or labels for the room.")
        part = {"page-labels": False, "scantron-labels": True}.get(kind)
        kind = "print" if kind == "print" else "labels"
        spec = label_media(media) if kind == "labels" else None
        if (not isinstance(batch_ids, list) or not 1 <= len(batch_ids) <= 20
                or any(not isinstance(item, str) for item in batch_ids) or len(set(batch_ids)) != len(batch_ids)):
            raise ValueError("Choose between 1 and 20 print jobs from one chart export.")
        batches = sorted((self.get_batch(batch_id) for batch_id in batch_ids), key=lambda b: (b["section_number"], b["id"]))
        if len({b["assignment_version_id"] for b in batches}) != 1 or len({b["seating_chart_id"] for b in batches}) != 1 or not batches[0]["seating_chart_id"]:
            raise ValueError("Room output needs print jobs of one seating chart and one exam version.")
        waiting = [f"Section {b['section_number']}" for b in batches if b["status"] != "ready" or b["superseded_at"]]
        if waiting:
            raise Conflict(f"Every section's print job must be ready and current before the room file can be cut; waiting on {', '.join(waiting)}.")
        entries, sources = [], []
        for index, batch in enumerate(batches):
            with connect(self.path) as db:
                manifest = self.manifest(db, batch["id"]) if kind == "print" else self.label_text(db, batch, self.label_manifest(db, batch["id"]))
            for position, entry in enumerate(manifest):
                if part is not None and bool(entry.get("sheet")) != part:
                    continue
                # Seated copies first, row by row across sections; unassigned extras last, section by section; pages in order within a copy,
                # then its Scantron label.
                key = ((0, seat_order(entry["seat_label"]), 0, entry["copy_number"]) if entry.get("seat_label") else (1, [], index, entry["copy_number"])) \
                    + ((1, 0) if entry.get("sheet") else (0, entry["page_number"]))
                entries.append((key, index, position, entry))
            if kind == "print":
                sources.append(self.download(batch["id"]))
            elif spec["kind"] == "roll":
                sources.append(self.labels(batch["id"], media))
        entries.sort(key=lambda item: item[0])
        if not entries:
            raise Conflict("These print jobs have no Scantron labels." if part else "These print jobs have no page labels.")
        if kind == "labels" and spec["kind"] == "sheet":
            first = batches[0]
            with connect(self.path) as db:
                layout = self._batch_layout(db, first)
                room = db.execute("SELECT r.name FROM seating_charts c JOIN rooms r ON r.id = c.room_id WHERE c.id = ?", (first["seating_chart_id"],)).fetchone()
            numbers = sorted({b["section_number"] for b in batches})
            header = {"exam": first["title"], "version": f"Version {first['version_label']}", "section": f"Section{'s' if len(numbers) != 1 else ''} {', '.join(map(str, numbers))}",
                      "term": f"{first['term_other'] if first['term'] == 'Other' else first['term']} {first['year']}", "room": room[0] if room else "", "page_count": first["page_count"]}
            pages = [{**entry, "output_page": number} for number, (_, _, _, entry) in enumerate(entries, 1)]
            return process.run("labels", b"", {"pages": pages, "header": header, "media": media}, timeout=300, layout_profile=layout["id"])[0]
        order = [[index, position] for _, index, position, _ in entries]
        return process.run("reorder", b"", {"sources": [base64.b64encode(content).decode() for content in sources], "pages": order}, timeout=300)[0]

    @staticmethod
    def _batch_layout(db, batch):
        profile = db.execute("SELECT layout_profile FROM assignment_versions WHERE id=?", (batch["assignment_version_id"],)).fetchone()[0]
        layout = get_layout(profile)
        if json.loads(batch["configuration_json"]) != layout or db.execute("""SELECT 1 FROM page_stamps p
            JOIN printed_copies c ON c.id=p.printed_copy_id WHERE c.print_batch_id=?
            AND p.symbology!='sequence' AND (p.placement_json!=? OR p.symbology!=?) LIMIT 1""", (batch["id"], encoded(layout), layout["symbology"])).fetchone():
            raise StorageError("The retained page placements disagree with the version and batch layout.")
        return layout

    # The jobs the Print page lists: every print job, and the newest 100 jobs first-page registration issued (never printed; one
    # scan can issue dozens). An older print job never drops off the list, so a wizard's "Already printed" still sees it.
    LISTED = f"""WITH listed(id) AS (SELECT id FROM print_batches WHERE request_id NOT LIKE '{REGISTRATION_PREFIX}%'
        UNION ALL SELECT id FROM (SELECT id FROM print_batches WHERE request_id LIKE '{REGISTRATION_PREFIX}%' ORDER BY created_at DESC LIMIT 100))"""

    def catalog(self):
        with connect(self.path) as db:
            batches = [self._listed_batch(r) for r in db.execute(f"""{self.LISTED} SELECT b.id,b.assignment_version_id,b.section_id,b.term_id,b.quantity,b.status,
                b.superseded_at,b.error,b.created_at,b.seating_chart_id,b.label_mode,b.seat_marks,b.reprint_of_batch_id,b.request_id,
                (SELECT r.name FROM seating_charts c JOIN rooms r ON r.id=c.room_id WHERE c.id=b.seating_chart_id) room_name
                FROM print_batches b WHERE b.id IN (SELECT id FROM listed) ORDER BY b.created_at DESC""")]
            # Copies of each listed job seen in a scan still in the Inbox (PRESENT), for "Scanned 161 of 164".
            scanned = dict(db.execute(f"""{self.LISTED} SELECT c.print_batch_id,COUNT(*) FROM printed_copies c
                WHERE c.print_batch_id IN (SELECT id FROM listed) AND EXISTS(
                  SELECT 1 FROM page_stamps p JOIN scan_pages sp ON sp.page_stamp_id=p.id WHERE p.printed_copy_id=c.id AND {PRESENT.format("sp.message_id")})
                GROUP BY c.print_batch_id""").fetchall())
            # Archived (a view, certified_archive.py): a job of an assessment whose grades are certified.
            archive = certified(db)
            owners = {r["id"]: (r["assignment_id"], r["term_id"]) for r in db.execute(f"""{self.LISTED} SELECT b.id,v.assignment_id,b.term_id FROM print_batches b
                JOIN assignment_versions v ON v.id=b.assignment_version_id
                WHERE b.id IN (SELECT id FROM listed)""")}
            for batch in batches:
                batch["scanned"] = scanned.get(batch["id"], 0)
                batch["archived"] = archive_facts(archive.get(owners.get(batch["id"])), batch["created_at"])
                if not batch["section_id"]:
                    batch["sections"] = self.job_sections(db, batch["id"])  # a job printed for the whole term lists its sections
            return {"assignments": [dict(r) for r in db.execute("SELECT a.*,d.code FROM assignments a JOIN course_definitions d ON d.id=a.definition_id ORDER BY d.code,a.assignment_key")],
                    "versions": [dict(r) for r in db.execute("SELECT * FROM assignment_versions ORDER BY created_at")],
                    "definitions": [dict(r) for r in db.execute("SELECT id,code FROM course_definitions ORDER BY code")],
                    "sections": [dict(r) for r in db.execute("SELECT s.id,s.number,t.id term_id,t.definition_id,t.code,t.term,t.term_other,t.year,t.is_current FROM course_sections s JOIN course_terms t ON t.id=s.term_id ORDER BY t.code,t.year DESC,s.number")],
                    "batches": batches,
                    # A lifecycle row is archived only while nothing of it (chart, print job, scan) was made after the certification.
                    "lifecycle": [{**row, "archived": archive_facts(entry := archive.get((row["assignment_id"], row["term_id"])),
                                                                     pair_activity(db, row["assignment_id"], row["term_id"], entry["plan_id"]) if entry else None)}
                                  for row in self._lifecycle(db)],
                    "layout": LAYOUT}

    @staticmethod
    def _lifecycle(db):
        """Each assignment in each CURRENT term of its course, as its Assignments card shows it (read-only, UX overhaul 2026-09-26).

        Paper, Seating and Print follow the one shared rule of amendment 15 (`exam_workflow.overview.assignment_stages`), so
        the card and the exam hub never disagree. Also: the term's exam event (for the Exams link), its grading plan once it
        holds responses (`grading_plan_id`; scan association alone never puts an assignment on Grading, and this read must stay
        cheap), or once an approved quiz or the instructor's Set up grading now set it up before any paper (`grading_entry_plan_id`,
        QUIZZES, GRADING: on Grading from then on), whether the term's exam record has a grading plan at all (`has_grading_plan`:
        the card offers Set up grading now only without one), the
        approved quiz draft it came from (`quiz_draft_id`), the exam date and time the seating charts or the main sitting
        record (`exam_date_chart_id` names the chart when a chart's date is the one shown, 2026-10-02), and what was printed:
        when last, how many copies of which versions, and whether as one term stack in several versions (counted over current
        print jobs only: never registration, superseded or cancelled ones, the stage rule's jobs). Counts, dates and record IDs
        only; nothing is decided or stored.
        """
        from exam_workflow.overview import assignment_stages, campus_today  # imports seating, which imports this module
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        rows = db.execute("""SELECT a.id assignment_id,t.id term_id,t.term,t.term_other,t.year,
            (SELECT COUNT(*) FROM assignment_versions v WHERE v.assignment_id=a.id) versions
            FROM assignments a JOIN course_terms t ON t.definition_id=a.definition_id WHERE t.is_current=1
            ORDER BY a.id,t.year DESC,t.id""").fetchall()
        result = []
        for row in rows:
            aid, tid = row["assignment_id"], row["term_id"]
            charts = db.execute("SELECT id,confirmed_at,exam_date,exam_time FROM seating_charts WHERE term_id=? AND assignment_id=? ORDER BY created_at",
                                (tid, aid)).fetchall()
            batches = [dict(b) for b in db.execute("""SELECT b.status,b.superseded_at,b.request_id,b.created_at,b.quantity,b.section_id,
                b.assignment_version_id,v.label version_label FROM print_batches b
                JOIN assignment_versions v ON v.id=b.assignment_version_id
                WHERE v.assignment_id=? AND b.term_id=?""", (aid, tid))]
            stages = assignment_stages(row["versions"], [{"confirmed": bool(c["confirmed_at"])} for c in charts], batches)
            event = db.execute("SELECT id FROM exam_events WHERE term_id=? AND assignment_id=?", (tid, aid)).fetchone() if "exam_events" in tables else None
            plan = found_plan = None
            if event and "frq_assignments" in tables:
                found = db.execute("SELECT id FROM frq_assignments WHERE event_id=?", (event[0],)).fetchone()
                found_plan = found[0] if found else None
                if found and "frq_responses" in tables and db.execute("SELECT 1 FROM frq_responses WHERE assignment_id=? LIMIT 1", (found[0],)).fetchone():
                    plan = found[0]
            # An approved quiz is on Grading before any paper exists (its rubrics are finalized while it is written), and its draft's
            # page holds the interleaved file, the rebuild and Put it on Canvas. So is a plan the instructor set up before the papers
            # (Set up grading now, 2026-10-02: its `assignment_created` event marked `before_papers`; one indexed read).
            quiz = "quiz_draft_approvals" in tables
            quiz_plan = found_plan if quiz and found_plan and not plan and db.execute("""SELECT 1 FROM quiz_draft_approvals WHERE status='complete'
                AND term_id=? AND json_extract(result_json,'$.plan_id')=? LIMIT 1""", (tid, found_plan)).fetchone() else None
            early_plan = found_plan if found_plan and not plan and not quiz_plan and "frq_events" in tables and db.execute("""SELECT 1 FROM frq_events
                WHERE assignment_id=? AND kind='assignment_created' AND json_extract(detail_json,'$.before_papers')=1 LIMIT 1""",
                (found_plan,)).fetchone() else None
            draft = db.execute("""SELECT draft_id FROM quiz_draft_approvals WHERE status='complete' AND term_id=? AND json_extract(result_json,'$.assignment_id')=?
                ORDER BY created_at DESC,rowid DESC LIMIT 1""", (tid, aid)).fetchone() if quiz else None
            # Papers came back: a present scan is linked to this term's grading plan (the Inbox's association), whether or not
            # its Inbox checks are finished. Papers printed without labels never have print jobs, so this is the only sign.
            scanned = bool(plan) or bool(found_plan and "frq_scan_links" in tables and db.execute(f"""SELECT 1 FROM frq_scan_links l WHERE l.assignment_id=?
                AND l.id=(SELECT MAX(n.id) FROM frq_scan_links n WHERE n.message_id=l.message_id) AND {PRESENT.format("l.message_id")} LIMIT 1""",
                (found_plan,)).fetchone())
            dated = next((c for c in charts if c["exam_date"]), None)
            dated_chart = dated["id"] if dated else None  # where the date is changed: that chart, else the main sitting (Exams page)
            if dated is None and event and "exam_sittings" in tables:
                dated = db.execute("SELECT date exam_date,time exam_time FROM exam_sittings WHERE event_id=? AND kind='main' AND date!='' ORDER BY date,id LIMIT 1",
                                   (event[0],)).fetchone()
            current = [b for b in batches if not is_registration(b["request_id"]) and not b["superseded_at"] and b["status"] != "cancelled"]
            printed = [b["created_at"] for b in current]
            result.append({"assignment_id": aid, "term_id": tid, "term_label": f"{row['term_other'] if row['term'] == 'Other' else row['term']} {row['year']}",
                           "stages": stages, "charts": len(charts), "confirmed_charts": sum(1 for c in charts if c["confirmed_at"]),
                           "exam_event_id": event[0] if event else None, "grading_plan_id": plan,
                           "grading_entry_plan_id": plan or quiz_plan or early_plan, "has_grading_plan": bool(found_plan),
                           "quiz_draft_id": draft[0] if draft else None,
                           "exam_date": dated["exam_date"] if dated else "", "exam_time": dated["exam_time"] if dated else "",
                           "exam_date_chart_id": dated_chart,
                           "last_printed_at": max(printed) if printed else None,
                           "printed_copies": sum(b["quantity"] for b in current),
                           # One stack for the term in several versions (DOCUMENTS ONE STACK, EVERY VERSION): no job names a section.
                           "printed_stack": bool(current) and all(not b["section_id"] for b in current) and len({b["assignment_version_id"] for b in current}) > 1,
                           "printed_versions": sorted({b["version_label"] for b in current}),
                           # Collected: scanned papers are linked, and the exam date (if any) is not still ahead.
                           "collected": bool(scanned and not (dated and dated["exam_date"] and dated["exam_date"] > campus_today())),
                           # Labels issued for scanned papers (registration) are not printing, so they do not make it active.
                           "active": bool(charts or event or any(not is_registration(b["request_id"]) for b in batches))})
        return result

    @staticmethod
    def _listed_batch(row):
        """A print job as the Assignments & printing list shows it (read-only, UX overhaul 2026-09-26).

        `export` is the request prefix that one confirmation or one Create print jobs click shares ("<request>:<section>",
        the rule seating.py uses to group a room's jobs); `origin` says whether first-page registration issued the job
        (never printed), a seating chart export made it, or it is an ordinary print job (a section's, or with no
        `section_id` the term's, migration 013). The request ID itself stays private to the catalog.
        """
        batch = dict(row)
        request_id = batch.pop("request_id") or ""
        batch["export"] = request_id.rsplit(":", 1)[0] if ":" in request_id else request_id
        batch["origin"] = batch_origin(request_id, batch["seating_chart_id"])
        batch["seat_marks"] = bool(batch["seat_marks"])
        return batch
