"""Faculty: a private, single-user home for a growing teaching stack."""

import gzip
import json
import os
import re
import sqlite3
import time
import uuid
from contextlib import contextmanager, nullcontext
from datetime import datetime, timezone
from functools import partial
from pathlib import Path
from urllib.parse import urlsplit

from flask import Flask, g, jsonify, request
from werkzeug.exceptions import HTTPException
from course_storage import CourseStorage, StorageError, PENDING
from course_codes import split_course_code, validate_course_code
from course_groups import group_courses
from roster_import import MAX_UPLOAD, parse_roster
from request_limits import json_object, protect_body
from rosters import RosterService, synchronize_owners, protect_roster_references
from rooms import RoomService, register as register_rooms
from teach_inbox.service import InboxService
from teach_inbox.api import register as register_inbox, authenticate_transport
from teach_inbox.protocol import MAX_REQUEST, MAX_SCAN_FILE

ROOT = Path(__file__).resolve().parent
CATEGORIES = ("Administration", "Teaching", "Assessment", "Resources", "Other")
STATUSES = ("planned", "building", "ready")
ICONS = ("users", "book", "check", "folder", "calendar", "spark", "grid", "chart")
COLORS = ("sage", "clay", "blue", "lavender", "sand")
TERMS = ("Fall", "Spring", "Summer I", "Summer II", "Summer (Full)", "Other")
SLOW_REQUEST = 10  # seconds: a request this long goes in the journal


def now():
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def database(path):
    connection = sqlite3.connect(path, timeout=10)
    connection.execute("PRAGMA foreign_keys=ON")
    connection.row_factory = sqlite3.Row
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def initialize(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with database(path) as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.executescript("""
            CREATE TABLE IF NOT EXISTS tools (
                id TEXT PRIMARY KEY, name TEXT NOT NULL,
                description TEXT NOT NULL, url TEXT NOT NULL,
                category TEXT NOT NULL, status TEXT NOT NULL,
                icon TEXT NOT NULL, color TEXT NOT NULL,
                pinned INTEGER NOT NULL DEFAULT 0,
                version INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS settings (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                workspace_name TEXT NOT NULL, teacher_name TEXT NOT NULL,
                version INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS courses (
                id TEXT PRIMARY KEY, name TEXT NOT NULL,
                code TEXT NOT NULL, term TEXT NOT NULL,
                is_current INTEGER NOT NULL DEFAULT 1,
                version INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                crn TEXT NOT NULL DEFAULT '',
                multiple_sections INTEGER NOT NULL DEFAULT 0,
                sections TEXT NOT NULL DEFAULT '[]',
                term_other TEXT NOT NULL DEFAULT '',
                year INTEGER
            );
            CREATE TABLE IF NOT EXISTS course_definitions (
                id TEXT PRIMARY KEY,
                code TEXT NOT NULL UNIQUE,
                prefix TEXT NOT NULL,
                number TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE (prefix, number)
            );
        """)
        # Upgrade existing course records atomically, preserving unknown legacy terms.
        db.execute("BEGIN IMMEDIATE")
        columns = {row["name"] for row in db.execute("PRAGMA table_info(courses)")}
        for column, definition in (
            ("crn", "TEXT NOT NULL DEFAULT ''"),
            ("multiple_sections", "INTEGER NOT NULL DEFAULT 0"),
            ("sections", "TEXT NOT NULL DEFAULT '[]'"),
            ("term_other", "TEXT NOT NULL DEFAULT ''"),
            ("year", "INTEGER"),
            ("storage_status", "TEXT NOT NULL DEFAULT ''"),
            ("storage_snapshot", "TEXT NOT NULL DEFAULT ''"),
            ("storage_verified_at", "TEXT NOT NULL DEFAULT ''"),
            ("storage_initialized", "INTEGER NOT NULL DEFAULT 0"),
            ("storage_hot_required", "INTEGER NOT NULL DEFAULT 0"),
            ("storage_error", "TEXT NOT NULL DEFAULT ''"),
            ("deleted_at", "TEXT"),
            ("definition_id", "TEXT REFERENCES course_definitions(id)"),
        ):
            if column not in columns:
                db.execute(f"ALTER TABLE courses ADD COLUMN {column} {definition}")
        if "year" not in columns:
            standard_terms = {term.casefold(): term for term in TERMS if term != "Other"}
            for row in db.execute("SELECT id, term FROM courses").fetchall():
                legacy_term = row["term"].strip()
                match = re.fullmatch(r"(.*?)\s+([1-9][0-9]{3})", legacy_term)
                label, year = (match[1].strip(), int(match[2])) if match else (legacy_term, None)
                term = standard_terms.get(label.casefold(), "Other" if label else "")
                db.execute(
                    "UPDATE courses SET term=?, term_other=?, year=?, version=version+1 WHERE id=?",
                    (term, label if term == "Other" else "", year, row["id"]),
                )
        if "definition_id" not in columns:
            for row in db.execute("SELECT * FROM courses").fetchall():
                parts = split_course_code(row["code"])
                if parts is None:
                    continue  # Preserve an unrecognized legacy code for explicit correction.
                values = {"code": f"{parts[0]} {parts[1]}", "code_prefix": parts[0], "code_number": parts[1]}
                definition_id = ensure_course_definition(db, values)
                db.execute("""UPDATE courses SET code=?, definition_id=?, version=version+1,
                              storage_status=CASE storage_status WHEN 'active' THEN 'sync_pending'
                              WHEN 'archived' THEN 'archive_pending' ELSE storage_status END WHERE id=?""",
                           (values["code"], definition_id, row["id"]))
        db.execute("CREATE INDEX IF NOT EXISTS courses_definition_id ON courses(definition_id)")
        # Seed only a new workspace, never repopulate a deliberately empty one.
        inserted = db.execute(
            "INSERT OR IGNORE INTO settings VALUES (1, 'My teaching workspace', '', 1)"
        ).rowcount
        if inserted:
            timestamp = now()
            db.execute(
                "INSERT INTO tools VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (str(uuid.uuid4()), "Course Rosters",
                 "A home for every course and the students in it. Your first teaching tool, ready to build.",
                 "", "Administration", "planned", "users", "sage", 1, 1,
                 timestamp, timestamp),
            )


def tool_record(row):
    result = dict(row)
    result["pinned"] = bool(result["pinned"])
    return result


def course_record(row):
    result = dict(row)
    result["is_current"] = bool(result["is_current"])
    result["multiple_sections"] = bool(result["multiple_sections"])
    result["sections"] = json.loads(result["sections"])
    result["code_prefix"], result["code_number"] = split_course_code(result["code"]) or ("", "")
    return result


def ensure_course_definition(db, values):
    """Resolve one shared definition by canonical code, retaining its identity forever."""
    db.execute("""INSERT INTO course_definitions (id, code, prefix, number, created_at)
                  VALUES (?, ?, ?, ?, ?) ON CONFLICT(code) DO NOTHING""",
               (str(uuid.uuid4()), values["code"], values["code_prefix"], values["code_number"], now()))
    return db.execute("SELECT id FROM course_definitions WHERE code=?", (values["code"],)).fetchone()["id"]


def clean_text(data, key, maximum, required=False):
    value = data.get(key, "")
    if not isinstance(value, str):
        raise ValueError(f"{key.replace('_', ' ').capitalize()} must be text.")
    value = value.strip()
    if required and not value:
        raise ValueError(f"{key.replace('_', ' ').capitalize()} is required.")
    if len(value) > maximum:
        raise ValueError(f"{key.replace('_', ' ').capitalize()} must be {maximum} characters or fewer.")
    return value


def validate_tool(data):
    result = {
        "name": clean_text(data, "name", 60, True),
        "description": clean_text(data, "description", 280),
        "url": clean_text(data, "url", 2048),
    }
    for field, choices, default in (
        ("category", CATEGORIES, "Other"), ("status", STATUSES, "planned"),
        ("icon", ICONS, "grid"), ("color", COLORS, "sage"),
    ):
        result[field] = data.get(field, default)
        if result[field] not in choices:
            raise ValueError(f"Choose a valid {field}.")
    result["pinned"] = data.get("pinned", False)
    if not isinstance(result["pinned"], bool):
        raise ValueError("Pinned must be true or false.")
    url = result["url"]
    if url:
        if any(ord(char) < 33 for char in url) or "\\" in url:
            raise ValueError("The tool URL cannot contain spaces, control characters, or backslashes.")
        try:
            parsed = urlsplit(url)
            relative = url.startswith("/") and not url.startswith("//")
            absolute = parsed.scheme in ("http", "https") and bool(parsed.hostname)
            if not (relative or absolute) or parsed.username or parsed.password:
                raise ValueError()
            _ = parsed.port
        except ValueError:
            raise ValueError("Use an http:// or https:// URL, or a path starting with a single /.") from None
    if result["status"] == "ready" and not url:
        raise ValueError("Add a URL before marking this tool ready.")
    return result


def validate_course(data):
    result = {
        "name": clean_text(data, "name", 120, True),
        **validate_course_code(data),
        "term": clean_text(data, "term", 60, True),
        "term_other": clean_text(data, "term_other", 60),
        "year": data.get("year"),
        "crn": clean_text(data, "crn", 40),
        "multiple_sections": data.get("multiple_sections", False),
        "sections": data.get("sections", []),
        "is_current": data.get("is_current", True),
    }
    if not isinstance(result["is_current"], bool):
        raise ValueError("Current course must be true or false.")
    if result["term"] not in TERMS:
        raise ValueError("Choose a term from the list, or choose Other and enter its identifier.")
    if result["term"] == "Other" and not result["term_other"]:
        raise ValueError("Enter a term identifier when choosing Other.")
    if result["term"] != "Other":
        result["term_other"] = ""
    if type(result["year"]) is not int or not 1000 <= result["year"] <= 9999:
        raise ValueError("Choose a four-digit year.")
    if not isinstance(result["multiple_sections"], bool):
        raise ValueError("Multiple sections must be true or false.")
    sections = result["sections"]
    if not isinstance(sections, list) or not sections:
        raise ValueError("Enter a section number for this course.")
    if result["multiple_sections"]:
        if len(sections) < 2:
            raise ValueError("Enter at least two section numbers for a course with multiple sections.")
    elif len(sections) != 1:
        raise ValueError("Enter one section number, or select Multiple Sections for additional sections.")
    if any(type(section) is not int or not 1 <= section <= 9007199254740991 for section in sections):
        raise ValueError("Section numbers must be positive whole numbers.")
    if len(set(sections)) != len(sections):
        raise ValueError("Each section number must be different.")
    return result


def create_app(data_dir=None, storage=None):
    app = Flask(__name__, static_folder=str(ROOT / "static"), static_url_path="/static")
    from web_assets import register as register_assets
    index_html = register_assets(app, ROOT / "static")
    # Single-user app: network access is the boundary, with no browser session.
    app.config["MAX_CONTENT_LENGTH"] = MAX_REQUEST
    app.config["MAX_FORM_PARTS"] = 24
    config_path = Path(os.environ.get("FACULTY_STORAGE_CONFIG", ROOT / ".faculty-storage.json"))
    if storage is None and data_dir is None and os.environ.get("FACULTY_REQUIRE_STORAGE") == "1" and not config_path.is_file():
        raise StorageError("The required course storage configuration is missing. No fallback workspace was opened.")
    # An explicit DATA_DIR asks for an isolated workspace. Without this, a copy started beside the deployed
    # .faculty-storage.json silently opened the live catalog instead of the folder it was given.
    isolated = bool(os.environ.get("DATA_DIR")) and os.environ.get("FACULTY_REQUIRE_STORAGE") != "1"
    if storage is None and data_dir is None and not isolated and config_path.exists():
        storage = CourseStorage(json.loads(config_path.read_text()))
    path = storage.database if storage else Path(data_dir or os.environ.get("DATA_DIR", ROOT / "data")) / "faculty.sqlite3"
    initialize(path)
    app.config["DATABASE"] = path
    app.extensions["course_storage"] = storage
    rosters = RosterService(path, storage)
    app.extensions["rosters"] = rosters
    inbox = InboxService(rosters)
    app.extensions["inbox"] = inbox
    register_inbox(app, inbox)
    # SCANNER-INTAKE "SCANNER FOLDER": the network scanners save PDFs into this folder on NVMe; the Inbox worker retains each.
    from teach_inbox.scan_folder import ScanFolder
    inbox.scan_folder = ScanFolder(inbox, os.environ.get("FACULTY_SCANNER_FOLDER") or (
        Path(storage.nvme) / "scanner-folder" if storage else Path(path).parent / "scanner-folder"))
    from accommodations import AccommodationService, register as register_accommodations
    accommodations = AccommodationService(rosters, inbox)
    rosters.accommodations = inbox.accommodations = accommodations
    app.extensions["accommodations"] = accommodations
    from dr_exams import DrExamService
    dr_exams = DrExamService(accommodations)
    accommodations.dr_exams = inbox.dr_exams = dr_exams
    app.extensions['dr_exams'] = dr_exams
    register_accommodations(app, accommodations)
    # IMPORT FROM HOWDY (CANVAS, 2026-11-01): the AIM sign-in window reads each section's class roster from Howdy (read only);
    # Import rosters sends each to the section's ordinary roster import.
    from howdy_import import register as register_howdy
    register_howdy(app, rosters)
    from document_identity.service import DocumentService
    from document_identity.scans import ScanService
    from document_identity.email_exports import PrintEmailExports
    from document_identity.printer import PrinterSends
    from document_identity.api import register as register_documents
    documents = DocumentService(rosters, inbox)
    scans = ScanService(documents)
    email_exports = PrintEmailExports(documents)
    printer_sends = PrinterSends(documents)
    documents.printer_sends = printer_sends  # AUTOMATIC MAKEUP PRINTS (absences/makeup_booking.py)
    app.extensions["documents"] = documents
    app.extensions["document_scans"] = scans
    app.extensions["print_email_exports"] = email_exports
    app.extensions["printer_sends"] = printer_sends
    inbox.documents, inbox.document_scans = documents, scans
    inbox.print_email_exports, inbox.printer_sends = email_exports, printer_sends
    register_documents(app, documents, scans, email_exports, printer_sends)
    from assignment_templates import AssignmentTemplateService
    from assignment_template_api import register as register_assignment_templates
    templates = AssignmentTemplateService(documents)
    app.extensions["assignment_templates"] = templates
    register_assignment_templates(app, templates)
    rooms = RoomService(path)
    app.extensions["rooms"] = rooms
    register_rooms(app, rooms)
    from seating import SeatingService, register as register_seating
    from exam_links import ExamLinkService
    exam_links = ExamLinkService(rosters)
    dr_exams.exam_links = exam_links
    app.extensions["exam_links"] = exam_links
    seating = SeatingService(rosters, rooms, exam_links)
    documents.seating = seating
    seating.documents = documents
    app.extensions["seating"] = seating
    register_seating(app, seating)
    from bubble_sheets.api import register as register_bubble_sheets
    from bubble_sheets.service import BubbleSheetService
    bubble_sheets = BubbleSheetService(scans, seating)
    app.extensions["bubble_sheets"] = inbox.bubble_sheets = bubble_sheets
    register_bubble_sheets(app, bubble_sheets)
    from response_cards.service import ResponseCardService
    from response_cards.api import register as register_response_cards
    response_cards = ResponseCardService(rosters, seating)
    seating.response_cards = response_cards
    seating.initialize_card_updates()
    inbox.response_cards = response_cards
    app.extensions["response_cards"] = response_cards
    register_response_cards(app, response_cards)
    email_exports.gmail = response_cards.mail.gmail  # Export by email sends from the connected Gmail (document_identity/email_exports.py)
    from seating_student_mail import SeatingStudentMail, register as register_student_mail
    student_chart_mail = SeatingStudentMail(seating)
    inbox.student_chart_mail = student_chart_mail
    app.extensions["seating_student_mail"] = student_chart_mail
    register_student_mail(app, student_chart_mail)
    from exam_workflow.service import ExamService
    from exam_workflow.grading import GradingService
    from exam_workflow.requests import ExamRequestService
    from exam_workflow.api import register as register_exams
    exams = ExamService(rosters, documents, scans, seating, response_cards)
    scans.exams = exams
    documents.exams = exams  # EXAMS: a new print job extends its term's exam record to its sections.
    grading = GradingService(exams)
    exam_requests = ExamRequestService(exams, grading)
    response_cards.exam_workflow = exam_requests
    app.extensions.update(exams=exams, exam_grading=grading, exam_requests=exam_requests)
    register_exams(app, exams, grading, exam_requests)
    from grading.service import GradingService as FrqGradingService
    from grading.api import register as register_frq_grading
    frq_grading = FrqGradingService(exams, grading, templates, inbox)
    app.extensions["frq_grading"] = inbox.frq_grading = frq_grading
    bubble_sheets.booklet_versions = frq_grading.booklet_versions  # BUBBLE-SHEETS "THE BOOKLET'S VERSION"
    scans.grading = frq_grading
    register_frq_grading(app, frq_grading)
    # GRADING "GEMINI SUBSCRIPTION": Google's Antigravity CLI keeps every conversation under its HOME, so Faculty runs it in a folder
    # of its own beside the other runtime work on NVMe, never in the instructor's home. Without configured storage there is none
    # (FACULTY_ANTIGRAVITY_HOME may name one) and the connection says so.
    from grading.providers import antigravity
    antigravity.configure(os.environ.get("FACULTY_ANTIGRAVITY_HOME") or (str(Path(storage.nvme) / "runtime" / "antigravity") if storage else ""))
    antigravity.prepare()
    # Quizzes drafted by a model from the instructor's material (grading/quiz_drafts.py): revised on request, approved into print and grading.
    from grading.quiz_api import register as register_quiz_drafts
    register_quiz_drafts(app, frq_grading)
    # Sending grades to Gradescope (AGENTS.md): inert unless FACULTY_GRADESCOPE_CREDENTIALS_FILE names the saved sign-in.
    from grading.gradescope_sync import GradescopeSender
    from grading.gradescope_api import register as register_gradescope
    gradescope = GradescopeSender(frq_grading)
    app.extensions["gradescope"] = inbox.gradescope = gradescope
    register_gradescope(app, gradescope)
    # Regrade requests on the Review step (AGENTS.md "Regrade requests"): the communications record's captured Gradescope requests,
    # placed beside their responses, and the instructor's reply posted to Gradescope from Faculty.
    from grading.regrade_requests import RegradeRequests
    from grading.regrade_api import register as register_regrades
    regrades = RegradeRequests(frq_grading, gradescope)
    app.extensions["regrades"] = regrades
    register_regrades(app, regrades)
    # Canvas (AGENTS.md "Canvas"): each section's Canvas course and what Faculty reads there with the kept Canvas session; inert
    # unless FACULTY_CANVAS_SESSION_FILE names where the sign-in window keeps it.
    from canvas.service import CanvasService
    from canvas.api import register as register_canvas
    from canvas.content_service import CanvasContentService
    canvas = CanvasService(rosters)
    app.extensions["canvas"] = canvas
    canvas_content = CanvasContentService(canvas)
    app.extensions["canvas_content"] = canvas_content
    from canvas.messages import CanvasMessages
    canvas_messages = CanvasMessages(canvas)
    app.extensions["canvas_messages"] = canvas_messages
    from canvas.announcements import CanvasAnnouncements
    canvas_announcements = CanvasAnnouncements(canvas)
    app.extensions["canvas_announcements"] = canvas_announcements
    register_canvas(app, canvas, canvas_content, canvas_messages, canvas_announcements)
    # iClicker (AGENTS.md "iClicker"): each section's iClicker course, read with the saved sign-in for the gradebook's Import from
    # iClicker; inert (never contacted) unless FACULTY_ICLICKER_CREDENTIALS_FILE names the sign-in.
    from iclicker.service import IClickerService
    from iclicker.api import register as register_iclicker
    iclicker = IClickerService(rosters)
    app.extensions["iclicker"] = iclicker
    register_iclicker(app, iclicker)
    # Student photos (AGENTS.md "Student photos"): read from a printed photo roster in the document sandbox and matched to a section
    # by name; private, append-only catalog rows shown in the roster's student window, the gradebook's record and Mail.
    from student_photos.service import StudentPhotoService
    from student_photos.api import register as register_student_photos
    student_photos = StudentPhotoService(rosters)
    rosters.photos = student_photos
    app.extensions["student_photos"] = student_photos
    register_student_photos(app, student_photos)
    # Each section's TA contacts (AGENTS.md "TA contacts"): catalog records the section page shows; its links open the
    # instructor's own mail program, and nothing here sends email.
    from teaching_assistants.service import TeachingAssistantService
    from teaching_assistants.api import register as register_teaching_assistants
    teaching_assistants = TeachingAssistantService(rosters)
    app.extensions["teaching_assistants"] = teaching_assistants
    register_teaching_assistants(app, teaching_assistants)
    # COORDINATOR: whether a course is coordinated in a term and who coordinates it, owned by the term and saved from the
    # course card with the offering it saves; nothing here sends email.
    import course_coordinators.service as coordinators
    app.extensions["course_coordinators"] = coordinators.CoordinatorService(rosters)
    # Exam-day packets (AGENTS.md "Exam-day packet"): an assessment's proctor instructions and each room's proctors for one term,
    # and one room's packet for the chart page to print with its seat chart; nothing here prints, stores a file or sends email.
    from exam_day.service import ExamDayService
    from exam_day.api import register as register_exam_day
    exam_day = ExamDayService(rosters, seating)
    app.extensions["exam_day"] = exam_day
    register_exam_day(app, exam_day)
    # A course's lecture notes (AGENTS.md "Course notes"): uploaded on the course page, split into their sections in the document
    # sandbox and kept in the course definition's verified storage; nothing is sent anywhere.
    from course_notes.service import CourseNotesService
    from course_notes.api import register as register_course_notes
    course_notes = CourseNotesService(rosters, documents)
    app.extensions["course_notes"] = course_notes
    register_course_notes(app, course_notes)
    # A course's question bank (AGENTS.md "Question bank"): questions retained from approved assessments and quizzes, written by the
    # instructor or read out of a PDF, placed in an outline that follows the lecture notes and tagged by the Rubric drafting model. Its
    # automatic pass is a turn of the Inbox worker; its model work is claimed by the grading cloud dispatcher.
    from question_bank.service import QuestionBankService
    from question_bank.api import register as register_question_bank
    question_bank = QuestionBankService(rosters, documents, course_notes, frq_grading)
    app.extensions["question_bank"] = frq_grading.question_bank = inbox.question_bank = question_bank
    register_question_bank(app, question_bank)
    # Term gradebooks (gradebook/README.md): the instructor's records; pulls read grading, never change or send it.
    from gradebook.service import GradebookService
    from gradebook.api import register as register_gradebook
    gradebook = GradebookService(rosters, frq=frq_grading, accommodations=accommodations, canvas=canvas, iclicker=iclicker)
    gradebook.photos = student_photos
    # COLUMN ASSIGNMENTS (docs/decisions/canvas.md): a column the gradebook gains gets its assignment in Faculty's Canvas copy.
    gradebook.canvas_content = canvas_content
    app.extensions["gradebook"] = gradebook
    register_gradebook(app, gradebook)
    # The morning sync (gradebook/canvas_nightly.py; docs/decisions/canvas.md NIGHTLY SYNC): each current term's Sync with Canvas by
    # itself from 05:00 campus time (only WebAssign's and Canvas quizzes' scores before the term's first sync), their Canvas due
    # dates moving the gradebook's columns and Faculty's Canvas copy first. In the web process, which registers Canvas writes so a
    # restart waits for them; started only when Faculty serves (below), never by a check. It replaces the 10 pm WebAssign import.
    from gradebook.canvas_nightly import CanvasNightly
    app.extensions["canvas_nightly"] = CanvasNightly(gradebook, storage, canvas_content)
    # iClicker after class (gradebook/iclicker_after_class.py; docs/decisions/iclicker.md AFTER CLASS): each section's Import from
    # iClicker by itself once its class ends on a class day (the Term schedule's), in the web process, which holds the iClicker
    # sign-in; started only when Faculty serves (below), never by a check.
    from gradebook.iclicker_after_class import IClickerAfterClass
    app.extensions["iclicker_after_class"] = IClickerAfterClass(gradebook, storage)
    # Certification and Export, the sixth grading stage (grading/certification.py): it reads grading, Gradescope's receipts and the
    # gradebook, and records a certification with the gradebook's values in one transaction; built after the three it uses.
    from grading.certification import CertificationService
    from grading.certification_api import register as register_certification
    certification = CertificationService(frq_grading, gradescope, gradebook)
    app.extensions["certification"] = certification
    # Late papers certify themselves (GRADING "LATE PAPERS CERTIFY THEMSELVES"): the Inbox worker's grading step reaches it here.
    frq_grading.certification = certification
    register_certification(app, certification)
    # Regrade requests answered in one panel on the Return stage (REGRADES, 2026-10-07): the reply drafted by a model (claimed by the
    # cloud dispatcher, so the Inbox worker's copy needs it too) and Apply, which reaches the certification and the gradebook above.
    from grading.regrade_apply import RegradeApply
    from grading.regrade_api import register_panel as register_regrade_panel
    from grading.regrade_drafts import RegradeDrafts
    frq_grading.regrade_drafts = regrade_drafts = RegradeDrafts(frq_grading, regrades, certification)
    app.extensions["regrade_panel"] = regrade_panel = RegradeApply(regrades, gradescope, certification, gradebook, regrade_drafts)
    register_regrade_panel(app, regrade_panel, regrade_drafts)
    # Rubric memories (GRADING "RUBRIC MEMORIES", 2026-10-14): general rules and watches for the rubric writers and the rubric check,
    # learned after a Certify and claimed by the cloud dispatcher (so the Inbox worker's copy needs it too). Every sentence it reads is
    # cleared of the current rosters' names and of the names on the roster of the plan's own term (a term that is over is in no
    # current roster); a roster that cannot be read stops the pass rather than send a name.
    from grading.rubric_memory import RubricMemory
    from grading.rubric_memory_api import register as register_rubric_memories
    frq_grading.rubric_memory = rubric_memory = RubricMemory(frq_grading)

    def rubric_memory_names(term_id=None):
        current = app.extensions["student_support"].people().values()
        names = [person.get("name", "") for person in current]
        if term_id and term_id not in {person.get("term_id") for person in current}:
            try:
                names += app.extensions["student_notes"].term_names(term_id)
            except Exception as failure:  # whatever keeps the roster from being read: the pass fails, as the contract says
                raise OSError("The roster of this assignment's term could not be read, so its sentences cannot be cleared of names.") from failure
        return names
    rubric_memory.names = rubric_memory_names
    # ASSISTANT-MEMORY "ONE PAGE": rubric learning by itself follows the instructor's switch on the Memory page.
    rubric_memory.learns = lambda: app.extensions["assistant"].memory.settings()["rubric_learning"]
    register_rubric_memories(app, rubric_memory)
    # REGRADES "FOLLOW-UP" (2026-10-07): what a Follow-up preparation is told about a regrade request, and its crops.
    from grading.regrade_followup import RegradeFacts
    app.extensions["regrade_facts"] = RegradeFacts(frq_grading, regrades, regrade_panel)
    from teaching_review import TeachingReviewService, register as register_teaching_review
    teaching_review = TeachingReviewService(certification, question_bank)
    app.extensions["teaching_review"] = teaching_review
    register_teaching_review(app, teaching_review)
    from assignment_workspace import register as register_assignment_workspace
    register_assignment_workspace(app)
    from calendar_service import CalendarService
    from workspace_calendar import register as register_calendar
    calendar = CalendarService(rosters)
    app.extensions["calendar"] = calendar
    register_calendar(app, calendar)
    # SCHEDULING: a course term's Term schedule (class meetings, office hours, meeting hours), the one clock, engine and feed.
    from scheduling.service import ScheduleService
    from scheduling.api import register as register_scheduling
    schedule = ScheduleService(rosters)
    app.extensions["scheduling"] = schedule
    register_scheduling(app, schedule)
    # Google Calendar sync (AGENTS.md "Google Calendar sync"): Faculty's events into the instructor's main Google calendar and
    # their Google events onto the Calendar, over the one Google connection Response Cards made; inert until connected.
    from google_calendar.service import GoogleCalendarService
    from google_calendar.api import register as register_google_calendar
    google_calendar = GoogleCalendarService(rosters, calendar, response_cards.mail.gmail, storage)
    app.extensions["google_calendar"] = inbox.google_calendar = google_calendar
    register_google_calendar(app, google_calendar)
    # Mail (AGENTS.md "Gmail in Faculty"): read the instructor's Gmail, a student's mail with them, and write and send from
    # Faculty, over the same Google connection; inert until connected, and Faculty keeps no copy of what it reads.
    from gmail_mail.service import GmailMailService
    from gmail_mail.api import register as register_gmail_mail
    gmail_mail = GmailMailService(rosters, response_cards.mail.gmail, teaching_assistants, student_photos)
    gmail_mail.canvas = canvas  # a reply to a Canvas message is posted in its Canvas conversation (MAIL-CANVAS-POST)
    app.extensions["gmail_mail"] = gmail_mail
    register_gmail_mail(app, gmail_mail)
    # Messages sent later (docs/decisions/mail.md MAIL-SEND-LATER, 2026-11-07): a reviewed Mail message or Canvas message to sections
    # kept as approved and sent at its time by the web process's thread (started only when Faculty serves, below); WORKSPACE-owned.
    from scheduled_sends.service import ScheduledSends
    from scheduled_sends.api import register as register_scheduled_sends
    app.extensions["scheduled_sends"] = scheduled_sends = ScheduledSends(rosters, gmail_mail, canvas_messages, canvas, storage)
    register_scheduled_sends(app, scheduled_sends)
    # DR QUIZ NOTICES (SCHEDULING, 2026-10-15): after a quiz is marked Prescheduled in AIM, each DR student of its section gets one
    # email through the Mail page's send (receipts, never resent automatically); a bounded turn of the Inbox worker.
    from scheduling.dr_quiz_notices import DrQuizNotices
    schedule.dr_quiz_notices = app.extensions["dr_quiz_notices"] = inbox.dr_quiz_notices = DrQuizNotices(schedule, gmail_mail)
    # The in-app file viewer (AGENTS.md "Faculty assistant", VIEWER): a file route's PDF pages, picture or text shown in the app,
    # read through the route itself; nothing is stored.
    from file_viewer.api import register as register_file_viewer
    register_file_viewer(app)
    # The communications record (AGENTS.md "Communications record"): every message with a student in Gmail, Canvas and Gradescope,
    # captured beside the Inbox worker loop and kept (originals in verified storage), and Faculty's own sends by reference; one
    # timeline per student for each term. Everything it does elsewhere is a read.
    from communications.service import CommunicationService
    from communications.api import register as register_communications
    communications = CommunicationService(rosters, gmail_mail, canvas)
    app.extensions["communications"] = inbox.communications = communications
    register_communications(app, communications)
    # Excused absences (AGENTS.md "Excused absences"): each student's absences in a section, their documentation (uploads in the
    # section's verified storage, or attachments kept in the communications record) and the instructor's decision. An excused
    # absence gives the iClicker sessions within its dates full credit in the gradebook (ABSENCES ICLICKER-CREDIT); it changes
    # nothing else in the gradebook or the exam records, and nothing is sent anywhere.
    from absences.service import AbsenceService
    from absences.api import register as register_absences
    absences = AbsenceService(rosters, documents, communications=communications, gradebook=gradebook, exams=exams)
    app.extensions["absences"] = absences
    inbox.absences = absences  # DECISION DEADLINE: the worker marks undocumented absences past their deadline not excused
    # MAKEUP BOOKING (requirement, 2026-10-04): a quiz makeup's version drafted by itself, the student's booking card (their times,
    # from the calendar), their pick seating them in a makeup sitting and a copy with their name; a bounded turn of the Inbox worker.
    from absences.makeup_booking import MakeupBooking
    booking = MakeupBooking(absences, exams, frq_grading, response_cards, calendar, google_calendar)
    booking.inbox = inbox
    absences.booking = response_cards.makeup_booking = inbox.makeup_booking = booking
    # DEPARTMENT MAKEUP EXAMS (requirement, 2026-10-22): the Math department's makeup exam request form, filled out by Faculty on the
    # instructor's approval of a Follow-up card; its receipts read from Gmail by a bounded turn of the Inbox worker.
    from absences.department_makeups import DepartmentMakeups
    absences.department = inbox.department_makeups = DepartmentMakeups(absences, exams, gmail_mail)
    from absences.google_signin import register as register_department_signin
    register_department_signin(app, absences.department.current_form_url)  # GOOGLE SIGN-IN: the form takes signed-in responses only
    register_absences(app, absences)
    # Student notes (AGENTS.md "Student notes"): the instructor's own notes about a student, owned by the student's term enrollment;
    # catalog only, nothing sent anywhere.
    from student_notes.service import StudentNoteService
    from student_notes.api import register as register_student_notes
    student_notes = StudentNoteService(rosters)
    app.extensions["student_notes"] = student_notes
    register_student_notes(app, student_notes)
    from dr_quiz_takers import api as dr_quiz_takers_api, service as dr_quiz_takers_service  # ACCOMMODATIONS "DR QUIZ TAKERS"
    dr_quiz_takers_api.register(app, dr_quiz_takers_service.DrQuizTakerService(rosters))
    from student_support.service import StudentSupportService
    from student_support.api import register as register_student_support
    student_support = StudentSupportService(rosters, student_notes, communications)
    app.extensions["student_support"] = student_support
    gmail_mail.followups = student_support  # MAIL-FOLLOW-UP: mail a follow-up has settled leaves the Mail page's Students list
    google_calendar.names = student_support.name_appointments  # CALENDAR OFFICE HOURS: Google's appointments name their student
    register_student_support(app, student_support)
    # Recordings (docs/decisions/recordings.md, RECORDINGS; step 1, Intake): what the instructor's Plaud recorder holds, listed
    # beside the Inbox worker loop, filed by where each recording's start falls on the calendar, and fetched into verified storage
    # only once filed as Faculty's. Everything it asks of Plaud is a read, but the renewal of the instructor's own sign-in.
    from recordings.service import RecordingService
    from recordings.api import register as register_recordings
    recordings = RecordingService(rosters, calendar, google_calendar, student_notes)
    app.extensions["recordings"] = inbox.recordings = recordings
    recordings.reading.frq = frq_grading  # RECORDINGS "The reading (step 3)": the Assistant's own engine, as Compute settings name it
    register_recordings(app, recordings)
    # RECORDINGS "Search by meaning (step 6)" / EMBEDDING: a kept transcript's passages are embedded by the local worker, beside the
    # capture loop, and kept as vectors in a rebuildable index on NVMe, as the picture index is.
    from recordings.meaning import Meaning
    recordings.meaning = Meaning(recordings, os.environ.get("FACULTY_RECORDING_INDEX_DIR") or (
        str(Path(storage.nvme) / "runtime" / "recording-index") if storage else str(Path(path).parent / "recording-index")))
    # JEV: workspace computing receipts; teaching records remain with their owners.
    from jev.service import JevService
    from jev.api import register as register_jev
    from student_support.triage import StudentTriage
    jev = JevService(rosters)
    app.extensions["jev"] = jev
    scans.jev = jev  # JEV PAPERS: Jev finds first pages without a printed Name line and reads whose paper each is
    frq_grading.jev = jev  # JEV BLANK ANSWERS: a zero for an answer both the reader and Jev saw left blank stands by itself
    student_support.triage = StudentTriage(student_support, jev, inbox)
    inbox.student_triage = student_support.triage
    register_jev(app, jev, student_support.triage)
    question_bank.jev = jev  # JEV BANK-TAGS: Jev places questions before the tagging model writes their objectives
    from jev.routing import register as register_jev_routing  # JEV ROUTING: the Ctrl K palette's typed requests
    register_jev_routing(app, jev)
    from workspace_overview import register as register_overview
    register_overview(app)
    # The Faculty assistant (AGENTS.md "Faculty assistant"): an AI agent's tools over the app's own routes, the instructor's
    # chosen categories of change made by itself and everything else as Approve cards; built last, since it reads every route.
    from assistant.service import AssistantService
    from assistant.api import register as register_assistant
    from assistant.conversations import Conversations
    assistant = AssistantService(app, rosters)
    assistant.conversations = Conversations(assistant)
    # EMBEDDING: the kept memories nearest a request come from the local worker when the service names its socket; otherwise,
    # and whenever it does not answer, keywords stand in (ASSISTANT-MEMORY).
    from faculty_embedding import client as embedding
    if embedding.configured():
        assistant.memory.embedder = embedding.Embedder()
    # PICTURE-SEARCH: scanned pages are found by describing them. Vectors come from the same local worker and are kept in a
    # rebuildable index on NVMe beside the other runtime work; the Inbox worker fills it one page a turn.
    from picture_search.service import PictureSearch
    from picture_search.api import register as register_picture_search
    def runtime_dir(variable, name):  # runtime work lives on NVMe beside the catalog's storage, never on the root disk (STORAGE)
        return os.environ.get(variable) or (
            str(Path(storage.nvme) / "runtime" / name) if storage else str(Path(path).parent / name))

    picture_index = runtime_dir("FACULTY_PICTURE_INDEX_DIR", "picture-index")
    from picture_search import sources as picture_sources
    app.extensions["picture_search"] = inbox.picture_search = PictureSearch(path, inbox, picture_index, sources=[
        picture_sources.Scans(inbox), picture_sources.TemplatePages(templates), picture_sources.BankPictures(question_bank),
        picture_sources.NotesPages(course_notes), picture_sources.AbsenceDocuments(absences),
        picture_sources.StudentAttachments(communications), picture_sources.MemoryPictures(path)])
    register_picture_search(app, app.extensions["picture_search"])
    # PAST-WORK: the Assistant's recorded actions are found by describing a situation ("how we handled this before"). Their
    # vectors come from the same local worker and are kept in a rebuildable index on NVMe, as the picture index is.
    from assistant.past_work import PastWork
    past_work_index = runtime_dir("FACULTY_PAST_WORK_INDEX_DIR", "past-work-index")
    app.extensions["past_work"] = assistant.past_work = PastWork(assistant, past_work_index)
    from assistant.coding import CodingService
    # Coding tasks work in their own clones on NVMe (runtime work, not teaching data); never in TMPDIR, which each service clears.
    code_tasks = runtime_dir("FACULTY_CODE_TASKS_DIR", "code-tasks")
    assistant.coding = CodingService(assistant, code_tasks)
    app.extensions["assistant"] = assistant
    inbox.assistant_watches = assistant.watches
    inbox.assistant_workflows = assistant.workflows
    from assistant.followups import FollowupSuggestions
    assistant.followups = FollowupSuggestions(assistant, student_support)
    regrade_panel.followups = assistant.followups  # REGRADES "FOLLOW-UP": Return shows Follow-up's one suggestion
    student_support.suggestions = assistant.followups
    inbox.followup_suggestions = assistant.followups
    absences.department.followups = assistant.followups  # ABSENCES DEPARTMENT MAKEUP EXAMS › ONCE THE EXAM IS SEATED
    absences.booking.followups = assistant.followups  # ABSENCES MAKEUP BOOKING › BACK TO FOLLOW-UP: "I need another time"
    # RECORDINGS "What an item may become": a date said in a meeting is proposed as an Approve card through the Assistant's own
    # actions, and Remember keeps a memory through the Assistant's own memory (the instructor's press; its checks apply).
    recordings.reading.assistant = assistant
    recordings.reading.support = student_support  # RECORDINGS "Student meetings" (step 4): a student's open follow-up, as its own listing answers it
    # SCHEDULING "DR REQUEST EMAILS" (2026-11-01): the DR center's request emails read from Gmail; a DR quiz's request opens the
    # student's Follow-up with the card that uploads its accessible copy to AIM (the instructor's Approve and Duo); an Inbox turn.
    from scheduling.dr_requests import DrExamRequests
    dr_requests = DrExamRequests(schedule, gmail_mail)
    dr_requests.followups, dr_requests.frq = assistant.followups, frq_grading
    app.extensions["dr_requests"] = inbox.dr_requests = dr_requests
    register_assistant(app, assistant)
    if storage:
        with storage.connect() as db:
            db.execute("""UPDATE courses SET storage_status = CASE WHEN is_current = 1
                          THEN 'restore_pending' ELSE 'archive_pending' END WHERE storage_status = ''""")

    @app.before_request
    def mark_arrival():
        g.arrived = time.monotonic()

    @app.before_request
    def protect_writes():
        if storage and request.path.startswith("/api/"):
            # Keep catalog-only diagnostics available when the backup drive is
            # offline. Payload reads and all writes retain the existing guard.
            diagnostic = request.method == "GET" and (
                request.path == "/api/workspace/overview" or
                (request.path == "/api/state" and request.args.get("include") == "core")
            )
            if diagnostic and storage.working_root == storage.nvme:
                storage.check_nvme()
            else:
                storage.check_hdd()
        if authenticate_transport(inbox):
            return None
        if request.method in ("POST", "PATCH", "PUT", "DELETE"):
            if request.headers.get("X-Teaching-Request") != "1":
                return jsonify(error="A same-origin workspace request is required."), 403
            if request.headers.get("Sec-Fetch-Site") == "cross-site":
                return jsonify(error="Cross-site writes are not allowed."), 403
            origin = request.headers.get("Origin")
            if origin:
                try:
                    parts = urlsplit(origin)
                    valid = parts.scheme in ("http", "https") and parts.netloc == request.host
                except ValueError:
                    valid = False
                if not valid:
                    return jsonify(error="The request origin does not match this workspace."), 403
            return protect_body()

    @app.after_request
    def response_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        # The app-wide policy is the default only: a view that set its own (a kept-file download's sandbox) keeps it
        # (COMMUNICATIONS "THE TIMELINE", requirement, 2026-11-06).
        response.headers.setdefault("Content-Security-Policy", (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; connect-src 'self'; object-src 'none'; "
            "base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
        ))
        if request.endpoint == "file_viewer.pdf" and response.status_code == 200 and response.mimetype == "application/pdf":
            # A PDF Faculty made opens in the browser's own PDF viewer inside the app's file viewer (AGENTS.md "Faculty assistant",
            # VIEWER): this response alone may be framed, and only by the app's own pages. No other directive, so the browser's
            # viewer runs; the route serves nothing but a checked PDF from NATIVE_PDFS.
            response.headers["X-Frame-Options"] = "SAMEORIGIN"
            response.headers["Content-Security-Policy"] = "frame-ancestors 'self'"
        if request.endpoint != "versioned_asset" or response.status_code not in {200, 304}:
            response.headers["Cache-Control"] = "no-store"
        # Large JSON reads go gzip-encoded to browsers that accept it (Tailscale serve compresses nothing). Caching is unchanged;
        # downloads, streams and file responses are left alone, and so is any ETag a view set (a 304 passes through as it is).
        if (response.status_code == 200 and request.method != "HEAD" and response.mimetype == "application/json"
                and not response.direct_passthrough and not response.is_streamed
                and "Content-Encoding" not in response.headers and "Content-Disposition" not in response.headers
                and request.accept_encodings["gzip"] > 0):
            body = response.get_data()
            if 8192 <= len(body) <= 8_000_000:
                response.set_data(gzip.compress(body, 5, mtime=0))
                response.headers["Content-Encoding"] = "gzip"
                response.vary.add("Accept-Encoding")
        # Waitress logs no requests, so a page's lost or late answer could not be traced to one (FRONTEND "A RESTART NOBODY SEES").
        took = time.monotonic() - g.get("arrived", time.monotonic())
        if took >= SLOW_REQUEST:
            app.logger.warning("%s %s took %.1f s (%s)", request.method, request.path, took, response.status_code)
        return response

    @app.errorhandler(HTTPException)
    def http_error(error):
        return jsonify(error=error.description), error.code

    @app.errorhandler(ValueError)
    def validation_error(error):
        return jsonify(error=str(error)), 400

    @app.errorhandler(sqlite3.Error)
    def database_error(error):
        app.logger.exception("Workspace database operation failed")
        return jsonify(error="The workspace could not be saved. Check server storage and try again."), 503

    @app.errorhandler(OSError)
    @app.errorhandler(StorageError)
    def storage_error(error):
        return jsonify(error=str(error)), 503

    body = partial(json_object, "The request must be a JSON object.")

    def snapshot(include=None):
        include = {"core", "rooms", "seating", "storage"} if include is None else include
        result = {}
        if "core" in include:
            with database(path) as db:
                settings = dict(db.execute("SELECT * FROM settings WHERE id = 1").fetchone())
                tools = [tool_record(row) for row in db.execute("SELECT * FROM tools ORDER BY created_at, id")]
                courses = [course_record(row) for row in db.execute("SELECT * FROM courses WHERE deleted_at IS NULL ORDER BY name COLLATE NOCASE, id")]
                definitions = [dict(row) for row in db.execute("SELECT * FROM course_definitions ORDER BY code")]
                counts = {"rooms": db.execute("SELECT COUNT(*) FROM rooms").fetchone()[0],
                          "seating": db.execute("SELECT COUNT(*) FROM seating_charts").fetchone()[0]}
            settings.pop("id")
            result.update(settings=settings, tools=tools, courses=courses,
                          course_groups=group_courses(courses), course_definitions=definitions,
                          course_prefixes=sorted({"MATH", *(item["prefix"] for item in definitions)}),
                          counts=counts)
        if "rooms" in include:
            result["rooms"] = rooms.list()
        if "seating" in include:
            result["seating_charts"] = seating.list()
        if "storage" in include:
            result["storage"] = storage.status() if storage else {"enabled": False}
        return result

    @app.get("/")
    def index():
        return app.response_class(index_html, mimetype="text/html")

    @app.get("/api/health")
    def health():
        with database(path) as db:
            db.execute("SELECT id FROM settings LIMIT 1").fetchone()
        return jsonify(status="ok")

    @app.get("/api/state")
    def state():
        include = request.args.get("include")
        if include is not None:
            include = set(include.split(","))
            if not include or not include <= {"core", "rooms", "seating", "storage"}:
                raise ValueError("Choose state sections from core, rooms, seating, and storage.")
        return jsonify(snapshot(include))

    @app.get("/api/export")
    def export():
        content = {"schema_version": 4, "exported_at": now(), **snapshot(), "rooms": rooms.export()}
        response = app.response_class(json.dumps(content, indent=2), mimetype="application/json")
        response.headers["Content-Disposition"] = 'attachment; filename="faculty-workspace.json"'
        return response

    @app.post("/api/tools")
    def add_tool():
        values = validate_tool(body())
        identifier, timestamp = str(uuid.uuid4()), now()
        with database(path) as db:
            db.execute(
                """INSERT INTO tools (id, name, description, url, category, status, icon,
                   color, pinned, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (identifier, *(values[key] for key in ("name", "description", "url", "category", "status", "icon", "color", "pinned")), timestamp, timestamp),
            )
            result = tool_record(db.execute("SELECT * FROM tools WHERE id = ?", (identifier,)).fetchone())
        return jsonify(result), 201

    @app.route("/api/tools/<identifier>", methods=["PATCH", "DELETE"])
    def change_tool(identifier):
        data = body()
        with database(path) as db:
            # Serialize the read/compare/write so concurrent devices cannot silently overwrite each other.
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM tools WHERE id = ?", (identifier,)).fetchone()
            if row is None:
                return jsonify(error="This tool no longer exists."), 404
            if type(data.get("version")) is not int or data["version"] != row["version"]:
                return jsonify(error="This tool changed on another device. Close and reopen it to get the latest version."), 409
            if request.method == "DELETE":
                db.execute("DELETE FROM tools WHERE id = ?", (identifier,))
                return "", 204
            values = validate_tool({**tool_record(row), **data})
            db.execute(
                """UPDATE tools SET name=?, description=?, url=?, category=?, status=?, icon=?,
                   color=?, pinned=?, updated_at=?, version=version+1 WHERE id=?""",
                (*(values[key] for key in ("name", "description", "url", "category", "status", "icon", "color", "pinned")), now(), identifier),
            )
            result = tool_record(db.execute("SELECT * FROM tools WHERE id = ?", (identifier,)).fetchone())
        return jsonify(result)

    @app.post("/api/courses")
    def add_course():
        data = body()
        values = validate_course(data)
        # COORDINATOR: the card's Coordinated course answer, checked before anything is saved and recorded for the offering's term.
        coordinator = coordinators.clean(data["coordinator"]) if "coordinator" in data else None
        identifier, timestamp = str(uuid.uuid4()), now()
        with rosters.lock(), database(path) as db:
            db.execute("BEGIN IMMEDIATE")
            definition_id = ensure_course_definition(db, values)
            db.execute(
                """INSERT INTO courses (id, name, code, term, is_current, crn, multiple_sections,
                   sections, term_other, year, created_at, updated_at, storage_status, definition_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (identifier, values["name"], values["code"], values["term"], values["is_current"],
                 values["crn"], values["multiple_sections"], json.dumps(values["sections"]),
                 values["term_other"], values["year"], timestamp, timestamp,
                 ("restore_pending" if values["is_current"] else "archive_pending") if storage else "", definition_id),
            )
            synchronize_owners(db, bool(storage))
            if coordinator is not None:
                save_coordinator(db, {**values, "definition_id": definition_id}, coordinator)
            result = course_record(db.execute("SELECT * FROM courses WHERE id = ?", (identifier,)).fetchone())
        if storage:
            storage.wake.set()
        return jsonify(result), 201

    def save_coordinator(db, course, coordinator):
        term_id = coordinators.term_of(db, course)
        if term_id is None:
            raise ValueError("Save the course with its code, term and year before naming its coordinator.")
        from request_actor import current as actor
        coordinators.record(db, term_id, coordinator, actor())

    @app.get("/api/courses/<identifier>/coordinator")
    def course_coordinator(identifier):
        # COORDINATOR: what the Course settings card shows; the term owns it, so every offering of the term reads the same.
        return jsonify(app.extensions["course_coordinators"].offering(identifier))

    @app.route("/api/courses/<identifier>", methods=["PATCH", "DELETE"])
    def change_course(identifier):
        data = body()
        if storage:
            try:
                storage._identifier(identifier)
            except (ValueError, StorageError):
                return jsonify(error="This course no longer exists."), 404
        with rosters.lock(), storage.lock(identifier, blocking=False) if storage else nullcontext(), database(path) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM courses WHERE id = ?", (identifier,)).fetchone()
            if row is None or row["deleted_at"]:
                return jsonify(error="This course no longer exists."), 404
            if type(data.get("version")) is not int or data["version"] != row["version"]:
                return jsonify(error="This course changed on another device. Close and reopen it to get the latest version."), 409
            if storage and row["storage_status"] in PENDING:
                return jsonify(error="Course storage is still being moved. Wait for it to finish before editing."), 409
            if request.method == "DELETE":
                protect_roster_references(db, course_record(row))
                if storage:
                    db.execute("""UPDATE courses SET deleted_at=?, updated_at=?, is_current=0,
                                  storage_status='archive_pending', storage_error='', version=version+1 WHERE id=?""",
                               (now(), now(), identifier))
                    synchronize_owners(db, True)
                    db.commit()
                    storage.wake.set()
                else:
                    db.execute("DELETE FROM courses WHERE id = ?", (identifier,))
                    synchronize_owners(db, False)
                return "", 204
            previous = course_record(row)
            if "code_prefix" in data or "code_number" in data:
                previous.pop("code")
            elif "code" in data:
                previous.pop("code_prefix")
                previous.pop("code_number")
            values = validate_course({**previous, **data})
            coordinator = coordinators.clean(data["coordinator"]) if "coordinator" in data else None
            definition_id = ensure_course_definition(db, values)
            protect_roster_references(db, course_record(row), {**values, "definition_id": definition_id})
            db.execute(
                """UPDATE courses SET name=?, code=?, term=?, is_current=?, crn=?,
                   multiple_sections=?, sections=?, term_other=?, year=?,
                   updated_at=?, storage_status=?, definition_id=?, storage_error='', version=version+1 WHERE id=?""",
                (values["name"], values["code"], values["term"], values["is_current"], values["crn"],
                 values["multiple_sections"], json.dumps(values["sections"]), values["term_other"],
                 values["year"], now(),
                 ("sync_pending" if values["is_current"] and row["is_current"] else
                  "restore_pending" if values["is_current"] else "archive_pending") if storage else "", definition_id, identifier),
            )
            synchronize_owners(db, bool(storage))
            if coordinator is not None:
                save_coordinator(db, {**values, "definition_id": definition_id}, coordinator)
            result = course_record(db.execute("SELECT * FROM courses WHERE id = ?", (identifier,)).fetchone())
        if storage:
            storage.wake.set()
        return jsonify(result)

    @app.get("/api/course-groups/summary")
    def course_group_summary():
        # Read-only counts for the course pages: students, accommodations and DR bookings per section (catalog only).
        return jsonify(rosters.summary())

    @app.route("/api/course-groups/<group_id>/sections/<int:number>/roster", methods=["GET", "POST"])
    def section_roster(group_id, number):
        if request.method == "GET":
            return jsonify(rosters.get(group_id, number))
        files = request.files.getlist("file")
        if len(files) != 1 or len(request.files) != 1 or not files[0].filename:
            raise ValueError("Choose one CSV or XLSX roster file.")
        upload = files[0]
        parsed = parse_roster(upload.read(MAX_UPLOAD + 1), upload.filename)
        return jsonify(rosters.import_roster(group_id, number, parsed, upload.filename)), 201

    # Dropping and restoring students (requirement, 2026-09-27): a recorded, reversible mark on the section membership.
    @app.post("/api/course-groups/<group_id>/sections/<int:number>/roster/drop")
    def section_roster_drop(group_id, number):
        return jsonify(rosters.set_dropped(group_id, number, body(), "drop"))

    @app.post("/api/course-groups/<group_id>/sections/<int:number>/roster/restore")
    def section_roster_restore(group_id, number):
        return jsonify(rosters.set_dropped(group_id, number, body(), "restore"))

    @app.patch("/api/settings")
    def settings():
        data = body()
        workspace = clean_text(data, "workspace_name", 60, True)
        teacher = clean_text(data, "teacher_name", 40)
        with database(path) as db:
            if type(data.get("version")) is not int:
                raise ValueError("A settings version is required.")
            changed = db.execute(
                "UPDATE settings SET workspace_name=?, teacher_name=?, version=version+1 WHERE id=1 AND version=?",
                (workspace, teacher, data["version"]),
            ).rowcount
            if not changed:
                return jsonify(error="Settings changed on another device. Close and reopen settings to try again."), 409
            saved = dict(db.execute("SELECT workspace_name,teacher_name,version FROM settings WHERE id=1").fetchone())
        return jsonify(saved)

    return app


if __name__ == "__main__":
    import logging
    from waitress import create_server
    from web_server import drain, listening_socket

    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "3000"))
    application = create_app()
    # ASSISTANT / TEACHING-WORKFLOWS: engines and MCP sessions belong to the web process, not the Inbox worker.
    application.extensions["assistant"].followups.start_dispatcher()
    if application.extensions["course_storage"]:
        application.extensions["course_storage"].start_worker()
    application.extensions["iclicker_after_class"].start()
    application.extensions["canvas"].owner = True  # this process runs Faculty's Canvas writes (canvas/service.py `owner`)
    application.extensions["canvas_nightly"].start()
    application.extensions["scheduled_sends"].start()
    logging.basicConfig()
    # The largest body any route takes is a scanner PDF (Inbox scan upload); every route still sets its own limit.
    # FRONTEND "A RESTART NOBODY SEES": the port stays open across a restart, and 8 threads keep a page's small reads from waiting
    # behind slow ones (an Approve can take a minute).
    server = create_server(application, sockets=[listening_socket(host, port)], threads=8, max_request_body_size=MAX_SCAN_FILE + 64 * 1024)
    # A restart (SIGTERM) lets Faculty's writes to Canvas that are running finish first, then what is being answered, then ends the
    # process through the handler that was there (rosters._terminate closes the catalog's idle connections): canvas/stopping.py.
    import signal
    from canvas.stopping import stop_handler
    signal.signal(signal.SIGTERM, stop_handler(application.extensions["canvas"].writes, previous=signal.getsignal(signal.SIGTERM), drain=lambda: drain(server)))
    print(f"Faculty is running at http://{host}:{port}", flush=True)
    server.run()
