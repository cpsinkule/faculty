"""Legacy adoption, serialized startup, checksum drift, and atomic DDL failures."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from catalog_migrations import MigrationError, apply_sql, migration_transaction


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.addCleanup(self.db.close)

    def test_script_runs_once_with_quoted_semicolons_and_trigger_bodies(self):
        script = """CREATE TABLE example(value TEXT);
            CREATE TABLE audit(value TEXT);
            CREATE TRIGGER recorded AFTER INSERT ON example BEGIN
                INSERT INTO audit VALUES('first;second');
                INSERT INTO audit VALUES(NEW.value);
            END;
            INSERT INTO example VALUES('saved; value'); -- trailing comment
        """
        with migration_transaction(self.db):
            self.assertTrue(apply_sql(self.db, "example", "001", script))
        with migration_transaction(self.db):
            self.assertFalse(apply_sql(self.db, "example", "001", script))
        self.assertEqual(self.db.execute("SELECT value FROM audit").fetchall(), [("first;second",), ("saved; value",)])

    def test_all_schema_and_ledger_changes_roll_back_together(self):
        with self.assertRaises(sqlite3.OperationalError):
            with migration_transaction(self.db):
                apply_sql(self.db, "example", "001", "CREATE TABLE preserved(id INTEGER);")
                apply_sql(self.db, "example", "002", "CREATE TABLE partial(id INTEGER); INSERT INTO missing VALUES(1);")
        self.assertEqual(self.db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall(), [])
        with migration_transaction(self.db):
            self.assertTrue(apply_sql(self.db, "example", "001", "CREATE TABLE preserved(id INTEGER);"))

    def test_a_rebuild_suspends_foreign_keys_for_its_transaction_and_checks_every_reference(self):
        # Term print jobs (document_identity 013): SQLite cannot drop a NOT NULL in place, and dropping a referenced table with
        # enforcement on deletes through its children. The rebuild keeps rows and rowids; triggers on other tables keep working.
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript("""CREATE TABLE parent(id TEXT PRIMARY KEY, owner TEXT NOT NULL);
            CREATE TABLE child(id INTEGER PRIMARY KEY, parent_id TEXT NOT NULL REFERENCES parent(id));
            CREATE TRIGGER child_scope BEFORE INSERT ON child WHEN NOT EXISTS(SELECT 1 FROM parent WHERE id=NEW.parent_id)
              BEGIN SELECT RAISE(ABORT,'no parent'); END;
            INSERT INTO parent(rowid,id,owner) VALUES(7,'a','x'); INSERT INTO child VALUES(1,'a');""")
        rebuild = """CREATE TEMP TABLE carry AS SELECT rowid AS r,* FROM parent; DROP TABLE parent;
            CREATE TABLE parent(id TEXT PRIMARY KEY, owner TEXT); INSERT INTO parent(rowid,id,owner) SELECT r,id,owner FROM carry; DROP TABLE carry;"""
        with self.assertRaises(sqlite3.IntegrityError):
            with migration_transaction(self.db):
                apply_sql(self.db, "example", "001", rebuild)
        lossy = rebuild.replace("FROM carry;", "FROM carry WHERE id!='a';")
        with self.assertRaisesRegex(MigrationError, "broken reference"):
            with migration_transaction(self.db, rebuild=("parent",)):
                apply_sql(self.db, "example", "001", lossy)
        self.assertEqual(self.db.execute("PRAGMA foreign_keys").fetchone()[0], 1)
        self.assertEqual(self.db.execute("SELECT rowid,id FROM parent").fetchall(), [(7, "a")])
        self.assertIsNone(self.db.execute("SELECT 1 FROM sqlite_master WHERE name='schema_migrations'").fetchone())
        with migration_transaction(self.db, rebuild=("parent",)):
            self.assertTrue(apply_sql(self.db, "example", "001", rebuild))
        self.assertEqual(self.db.execute("PRAGMA foreign_keys").fetchone()[0], 1)
        self.assertEqual(self.db.execute("SELECT rowid,id,owner FROM parent").fetchall(), [(7, "a", "x")])
        self.db.execute("INSERT INTO parent VALUES('b', NULL)")
        self.db.execute("INSERT INTO child VALUES(2,'b')")
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("INSERT INTO child VALUES(3,'missing')")

    def test_the_memory_tables_rebuild_carries_every_revision_over_and_keeps_its_rules(self):
        # ASSISTANT-MEMORY "SAID IN A RECORDING" (assistant 015): `source` gains `recording`, which is a CHECK of the table. A
        # catalog that already holds memories keeps every revision as it is, in the same columns; what refers to them still does.
        folder = Path(__file__).resolve().parent.parent / "assistant" / "migrations"
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript("""CREATE TABLE course_definitions(id TEXT PRIMARY KEY); CREATE TABLE course_terms(id TEXT PRIMARY KEY);
            CREATE TABLE jev_calls(id TEXT PRIMARY KEY); CREATE TABLE student_support_suggestion_runs(id TEXT PRIMARY KEY);
            CREATE TABLE assistant_preparations(id TEXT PRIMARY KEY);
            INSERT INTO course_terms VALUES('t1'); INSERT INTO jev_calls VALUES('j1'); INSERT INTO assistant_preparations VALUES('p1');""")
        with migration_transaction(self.db):
            for name in ("010_memory", "011_memory_pictures", "012_draft_memories", "013_memory_always", "014_memory_settings"):
                apply_sql(self.db, "assistant", name, (folder / f"{name}.sql").read_text(encoding="utf-8"))
        revision = """INSERT INTO assistant_memory_revisions(memory_id,revision,status,kind,scope,term_id,title,content,source,actor,reason,
            superseded_by,capture_call,standalone,always,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"""
        self.db.executescript("INSERT INTO assistant_memory_items VALUES('m1','2026-10-10'); INSERT INTO assistant_memory_items VALUES('m2','2026-10-11');")
        self.db.execute(revision, ("m1", 1, "current", "preference", "workspace", None, "", "No em dashes.", "chat", "assistant", "kept", None, "j1", 0, 1, "2026-10-10"))
        self.db.execute(revision, ("m1", 2, "superseded", "preference", "workspace", None, "", "No em dashes.", "chat", "assistant", "replaced", "m2", None, 1, 1, "2026-10-11"))
        self.db.execute(revision, ("m2", 1, "current", "fact", "term", "t1", "Late work", "Late work loses ten percent.", "followup", "instructor", "kept", None, None, 1, 0, "2026-10-11"))
        self.db.execute("UPDATE assistant_memory_revisions SET content='',title='',erased=1 WHERE memory_id='m1' AND revision=1")   # Erase
        self.db.execute("INSERT INTO assistant_memory_uses(memory_id,revision,conversation_id,used_at) VALUES('m2',1,'c1','2026-10-12')")
        self.db.execute("INSERT INTO assistant_draft_memories(preparation_id,memory_id,revision,selection,given_at) VALUES('p1','m2',1,'chosen','2026-10-12')")
        self.db.commit()
        read = lambda: ([tuple(row) for row in self.db.execute("SELECT * FROM assistant_memory_revisions ORDER BY memory_id,revision")],
                        [row[1] for row in self.db.execute("PRAGMA table_info(assistant_memory_revisions)")],
                        [tuple(row) for row in self.db.execute("SELECT * FROM assistant_memory_latest ORDER BY memory_id")])
        before = read()
        with migration_transaction(self.db, rebuild=("assistant_memory_revisions",)):
            self.assertTrue(apply_sql(self.db, "assistant", "015_memory_said_in_a_recording",
                                      (folder / "015_memory_said_in_a_recording.sql").read_text(encoding="utf-8")))
        self.assertEqual(read(), before)
        self.assertEqual(self.db.execute("PRAGMA foreign_keys").fetchone()[0], 1)
        self.assertEqual(self.db.execute("PRAGMA foreign_key_check").fetchall(), [])
        self.assertIsNone(self.db.execute("SELECT 1 FROM sqlite_master WHERE name='assistant_memory_revisions_next' AND type='table'").fetchone())
        # The one more source is accepted, and nothing else changed about what a revision may be.
        self.db.execute("INSERT INTO assistant_memory_items VALUES('m3','2026-11-13')")
        self.db.execute(revision, ("m3", 1, "current", "fact", "term", "t1", "", "No late work after solutions are posted.", "recording", "instructor", "kept", None, None, 1, 0, "2026-11-13"))
        for refused in ((revision, ("m3", 2, "current", "fact", "term", "t1", "", "x", "mail", "instructor", "edited", None, None, 1, 0, "2026-11-13")),
                        (revision, ("m3", 3, "current", "fact", "term", "t1", "", "x", "form", "instructor", "edited", None, None, 1, 0, "2026-11-13")),
                        ("UPDATE assistant_memory_revisions SET content='Other' WHERE memory_id=?", ("m2",)),
                        ("DELETE FROM assistant_memory_revisions WHERE memory_id=?", ("m2",)),
                        ("INSERT INTO assistant_memory_uses(memory_id,revision,conversation_id,used_at) VALUES(?,9,'c2','2026-11-13')", ("m2",))):
            with self.subTest(refused=refused[0][:60], values=refused[1][:2]), self.assertRaises(sqlite3.IntegrityError):
                self.db.execute(*refused)
        self.db.execute("UPDATE assistant_memory_revisions SET content='',title='',erased=1 WHERE memory_id='m3'")   # Erase still may
        self.db.execute("INSERT INTO assistant_memory_uses(memory_id,revision,conversation_id,used_at) VALUES('m3',1,'c2','2026-11-13')")

    def test_changed_recorded_sql_refuses_startup_without_rewriting_history(self):
        with migration_transaction(self.db):
            apply_sql(self.db, "example", "001", "CREATE TABLE example(id INTEGER);")
        original = self.db.execute("SELECT * FROM schema_migrations").fetchall()
        with self.assertRaisesRegex(MigrationError, "add a new migration"):
            with migration_transaction(self.db):
                apply_sql(self.db, "example", "001", "CREATE TABLE example(id TEXT);")
        self.assertEqual(self.db.execute("SELECT * FROM schema_migrations").fetchall(), original)

    def test_never_implicitly_commits_an_existing_transaction(self):
        self.db.execute("CREATE TABLE example(id INTEGER)")
        self.db.execute("INSERT INTO example VALUES(1)")
        with self.assertRaisesRegex(MigrationError, "another catalog transaction"):
            with migration_transaction(self.db):
                pass
        self.assertTrue(self.db.in_transaction)
        self.db.rollback()
        self.assertEqual(self.db.execute("SELECT * FROM example").fetchall(), [])

    def test_sql_files_cannot_commit_or_escape_the_transaction(self):
        for command in ("COMMIT;", "-- comment\nCOMMIT;", "COMMIT/* comment */;", "PRAGMA foreign_keys=OFF;", "SAVEPOINT nested;"):
            with self.subTest(command=command), self.assertRaises(MigrationError):
                with migration_transaction(self.db):
                    apply_sql(self.db, "example", "001", "CREATE TABLE partial(id INTEGER);" + command)
            self.assertEqual(self.db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall(), [])

    def test_two_startups_apply_once_after_acquiring_database_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "catalog.sqlite3"
            def start(_):
                db = sqlite3.connect(path, timeout=5)
                try:
                    with migration_transaction(db):
                        return apply_sql(db, "example", "001", "CREATE TABLE example(value TEXT); INSERT INTO example VALUES('once');")
                finally:
                    db.close()
            with ThreadPoolExecutor(max_workers=2) as pool:
                self.assertEqual(sorted(pool.map(start, range(2))), [False, True])


class AdoptionTests(unittest.TestCase):
    def test_application_adopts_existing_rows_once_then_appends_new_versions(self):
        from app import create_app
        from rosters import connect
        from test_storage import HEADERS
        with tempfile.TemporaryDirectory() as directory:
            app = create_app(directory)
            client = app.test_client()
            course = client.post("/api/courses", json={"code": "MATH 140", "name": "Calculus", "term": "Fall", "year": 2026,
                                                       "sections": [507], "is_current": True}, headers=HEADERS)
            self.assertEqual(course.status_code, 201)
            docs = app.extensions["documents"]
            assignment = docs.create_assignment(course.json["definition_id"], "exam-1", "Exam 1")
            with connect(app.config["DATABASE"]) as db:
                # These modules were introduced after the ledger. They must be
                # absent in a pre-ledger fixture and installed as new migrations.
                for view in ('frq_all_acceptances', 'frq_all_acceptance_reviews', 'assistant_memory_latest'):
                    db.execute(f'DROP VIEW IF EXISTS {view}')
                for table in ("question_bank_picture_events", "question_bank_pictures", "question_bank_version_links", "question_bank_assessment_links", "question_bank_run_questions", "question_bank_events", "question_bank_classifications", "question_bank_revisions",
                              "question_bank_questions", "question_bank_runs", "question_bank_uploads", "question_bank_outline_imports",
                              "question_bank_node_revisions", "question_bank_nodes",
                              "frq_transcript_checks", "frq_participation", "frq_gradescope_item_wordings", "frq_gradescope_mc_labels", "frq_rubric_choices", "frq_gradescope_mc_corrections", "frq_gradescope_automatic_runs", "frq_gradescope_automatic_sends", "frq_regrade_apply_steps", "frq_regrade_applies", "frq_regrade_drafts", "frq_rubric_memory_decisions", "frq_rubric_memories", "frq_rubric_memory_runs", "frq_gradescope_mc_checks", "frq_rubric_line_links", "frq_gradescope_publication_units", "frq_gradescope_publications", "frq_regrade_marks", "frq_regrade_reply_outcomes", "frq_regrade_replies", "copy_name_reads", "quiz_draft_scraps", "quiz_draft_approvals", "quiz_draft_revisions", "quiz_drafts", "frq_flag_resolutions", "frq_team_acceptance_reinstatements", "frq_quality_audits", "frq_gradescope_created_questions", "frq_gradescope_creation_units", "frq_gradescope_creations", "frq_certification_students", "frq_certifications", "frq_training_examples", "frq_gradescope_sheet_uploads", "frq_gradescope_sheet_sends", "frq_gradescope_sheet_targets",
                              "frq_gradescope_grades", "frq_gradescope_uploads", "frq_gradescope_sends", "frq_gradescope_targets", "frq_gradescope_courses",
                              "frq_added_versions", "frq_bubble_keys", "frq_declared_versions", "frq_rubric_review_ready", "frq_rubric_line_comments", "frq_jev_routes", "frq_tag_team_acceptance_events", "frq_tag_team_acceptance_reviews", "frq_tag_team_acceptances", "frq_one_shot_acceptance_events", "frq_one_shot_acceptance_reviews", "frq_one_shot_acceptances", "frq_job_reset_restores", "frq_job_reset_responses", "frq_job_resets", "frq_template_reviews", "frq_team_acceptance_events", "frq_team_acceptance_reviews", "frq_team_acceptances",
                              "frq_rubric_generations", "frq_rubric_quality_reviews", "frq_rubric_quality_checks", "frq_rubric_quality_options",
                              "frq_scan_checks", "frq_scan_links", "frq_review_feedback", "frq_acceptance_reviews", "frq_acceptances", "frq_benchmark_reports", "frq_acceptance_permission_events",
                              "frq_acceptance_permissions", "frq_canary_baselines", "frq_canary_items", "frq_canary_runs", "frq_endpoint_probes", "frq_cloud_calls",
                              "frq_version_reviews", "frq_version_checks", "frq_rubric_uploads", "frq_location_reviews", "frq_location_checks", "frq_template_readings",
                              "frq_rejected_outputs", "frq_cloud_approvals", "frq_preparation_parts", "frq_preparations", "frq_settings", "frq_events", "frq_exports", "frq_steps",
                              "frq_tasks", "frq_reviews", "frq_results", "frq_jobs", "frq_responses", "frq_assignment_questions", "frq_rubric_approvals",
                              "frq_rubric_revisions", "frq_question_revisions", "frq_questions", "frq_assignments",
                              "exam_rubric_template_sources", "assignment_template_revisions", "assignment_templates", "calendar_events",
                              "gradebook_canvas_nightly_runs", "gradebook_canvas_policies", "gradebook_canvas_posting_choices", "gradebook_canvas_snapshots", "gradebook_canvas_published", "gradebook_canvas_posting", "gradebook_canvas_posted", "gradebook_canvas_posts", "gradebook_iclicker_credits", "gradebook_iclicker_runs", "gradebook_nightly_runs", "gradebook_canvas_created", "gradebook_iclicker_sessions", "gradebook_iclicker_links", "gradebook_canvas_sent", "gradebook_canvas_sends", "gradebook_canvas_links", "gradebook_scores", "gradebook_requests", "gradebook_setups",
                              "canvas_announcement_outcomes", "canvas_announcement_posts", "canvas_announcements",
                              "canvas_message_uploads", "canvas_message_attachments", "canvas_message_outcomes", "canvas_message_deliveries", "canvas_messages",
                              "canvas_content_file_uploads", "canvas_content_files",
                              "canvas_section_pushes", "canvas_content_links", "canvas_content_pushes", "canvas_content_revisions", "canvas_course_links", "iclicker_course_links", "section_ta_contacts", "exam_day_plans",
                              "google_calendar_settings", "google_calendar_pushed", "google_calendar_events", "google_calendar_state",
                              "student_support_learning_uses", "student_support_learning_lesson_events", "student_support_learning_lessons",
                              "student_support_learning_events", "student_support_learning_cases",
                              "student_support_watch_events", "student_support_watches", "student_support_context_checks",
                              "student_support_conversation_joins", "student_support_conversation_mail", "student_support_suggestion_undos", "student_support_suggestion_edits", "student_support_suggestion_notes", "student_support_suggestion_replacements", "student_support_suggestion_pauses",
                              "student_support_suggestion_mutes", "student_support_suggestion_priorities",
                              "scheduled_send_events", "scheduled_sends", "gmail_mail_canvas_posts", "gmail_mail_archive", "gmail_mail_trash", "gmail_mail_send_uploads", "gmail_mail_send_attachments", "gmail_mail_outcomes", "gmail_mail_sends", "student_photos", "student_photo_imports",
                              "course_notes_events", "course_notes_parts", "course_notes",
                              "memory_settings", "assistant_draft_memories", "student_support_suggestion_actions", "student_support_suggestion_events", "student_support_suggestion_runs",
                              "assistant_preparation_baselines", "assistant_plan_continuations", "assistant_material_requests", "assistant_preparation_actions", "assistant_preparation_events", "assistant_preparations", "assistant_workflows",
                              "assistant_batch_members", "assistant_action_batches", "assistant_notifications", "assistant_watches",
                              "assistant_artifacts", "assistant_saved_conversations", "assistant_memory_pictures", "assistant_memory_uses", "assistant_memory_revisions", "assistant_memory_items", "assistant_memories",
                              "assistant_action_events", "assistant_actions", "assistant_code_task_events", "assistant_code_tasks",
                              "jev_calls", "comm_links", "comm_revisions", "comm_messages", "comm_files", "comm_bundles", "comm_capture_state",
                              "absence_department_notices", "absence_department_outcomes", "absence_department_receipts", "absence_department_requests", "absence_department_forms",
                              "absence_makeups_missed", "absence_makeup_copies", "absence_makeup_picks", "absence_makeup_offers", "absence_makeup_settings",
                              "absence_applications", "absence_document_events", "absence_documents", "absence_revisions", "absences",
                              "dr_exam_request_followups", "dr_exam_requests", "dr_quiz_sittings", "aim_send_run_ends", "aim_send_runs", "aim_send_outcomes", "aim_sends", "dr_quiz_notices", "dr_quiz_marks", "scheduling_plan_links", "scheduling_plan_revisions", "scheduling_plan_items", "scheduling_replaced_events", "scheduling_uses", "scheduling_revisions", "scheduling_entries", "student_support_meeting_hours", "student_support_term_policy_areas", "student_support_term_policies", "student_support_wellbeing_reviews", "student_support_baselines", "student_support_requests", "student_support_revisions", "student_support_items",
                              "student_note_revisions", "student_notes", "dr_quiz_taker_changes", "print_batch_sections", "sheet_labels",
                              "print_printer_send_batches", "print_printer_sends", "printed_copy_name_fills",
                              "recording_not_kept", "recording_erased_bundles", "recording_erasing",
                              "recording_reading_state", "recording_coordinators", "recording_item_events", "recording_item_readings", "recording_items", "recording_readings",
                              "recording_fetch_state", "recording_capture_state", "recording_links", "recording_revisions", "recordings",
                              "recording_lines", "recording_transcripts", "recording_files", "recording_bundles",
                              "course_term_coordinators"):
                    db.execute(f"DROP TABLE IF EXISTS {table}")
                db.execute("DROP TABLE schema_migrations")  # Exact pre-ledger catalog, with real retained owners.
            restarted = create_app(directory)
            with connect(restarted.config["DATABASE"]) as db:
                self.assertEqual(db.execute("SELECT id,title FROM assignments").fetchall()[0]["id"], assignment["id"])
                records = [dict(row) for row in db.execute("SELECT * FROM schema_migrations ORDER BY namespace,version")]
                legacy = [row for row in records if row["namespace"] in {"document_identity", "response_cards"} and row["version"] not in ("002_mail_card_index", "003_reminders", "013_term_print_jobs", "014_jev_paper_basis", "015_sheet_labels", "016_printer_sends", "017_copy_name_fills", "018_stack_continuations")]
                self.assertEqual(len(legacy), 13)  # document_identity 001–012 and the response cards baseline
                # Term print jobs came after the ledger: a pre-ledger catalog gets its print_batches rebuilt, as a new migration.
                self.assertEqual([row["adoption"] for row in records if row["version"] == "013_term_print_jobs"], ["applied"])
                # JEV PAPERS (014): the registration requests are rebuilt to allow the basis 'jev', as a new migration.
                self.assertEqual([row["adoption"] for row in records if row["version"] == "014_jev_paper_basis"], ["applied"])
                # SCANTRON LABELS (015): a new table, applied as new.
                self.assertEqual([row["adoption"] for row in records if row["version"] == "015_sheet_labels"], ["applied"])
                # SEND TO PRINTER (016): new tables, applied as new.
                self.assertEqual([row["adoption"] for row in records if row["version"] == "016_printer_sends"], ["applied"])
                # NAME FILLED IN (017): a new table, applied as new.
                self.assertEqual([row["adoption"] for row in records if row["version"] == "017_copy_name_fills"], ["applied"])
                # BATCHES SCANNED IN STACKS (018): two owner triggers replaced, applied as new.
                self.assertEqual([row["adoption"] for row in records if row["version"] == "018_stack_continuations"], ["applied"])
                cards = [(row["version"], row["adoption"]) for row in records if row["namespace"] == "response_cards"]
                self.assertEqual(cards, [("001_schema", "adopted"), ("002_mail_card_index", "applied"), ("003_reminders", "applied")])
                self.assertEqual({row["adoption"] for row in legacy}, {"adopted"})
                calendar = [row for row in records if row["namespace"] == "calendar"]
                self.assertEqual([(row["version"], row["adoption"]) for row in calendar], [("001_events", "applied")])
                tas = [row for row in records if row["namespace"] == "teaching_assistants"]
                self.assertEqual([(row["version"], row["adoption"]) for row in tas], [("001_section_tas", "applied")])
                plans = [row for row in records if row["namespace"] == "exam_day"]
                self.assertEqual([(row["version"], row["adoption"]) for row in plans], [("001_plans", "applied")])
                google = [row for row in records if row["namespace"] == "google_calendar"]
                self.assertEqual([(row["version"], row["adoption"]) for row in google], [("001_sync", "applied")])
                mail = [row for row in records if row["namespace"] == "gmail_mail"]
                self.assertEqual([(row["version"], row["adoption"]) for row in mail], [("001_mail", "applied"), ("002_trash", "applied"), ("003_archive", "applied"), ("004_attachments", "applied"), ("005_uploads", "applied"), ("006_canvas_posts", "applied")])
                later = [row for row in records if row["namespace"] == "scheduled_sends"]
                self.assertEqual([(row["version"], row["adoption"]) for row in later], [("001_scheduled", "applied")])
                helper = [row for row in records if row["namespace"] == "assistant"]
                self.assertEqual([(row["version"], row["adoption"]) for row in helper], [("001_actions", "applied"), ("002_code_tasks", "applied"), ("003_workspace", "applied"), ("004_preparations", "applied"), ("005_watch_preparation", "applied"), ("006_daily_reconciliation", "applied"), ("007_workflow_capabilities", "applied"), ("008_material_failed", "applied"), ("009_preparation_status", "applied"), ("010_memory", "applied"), ("011_memory_pictures", "applied"), ("012_draft_memories", "applied"), ("013_memory_always", "applied"), ("014_memory_settings", "applied"), ("015_memory_said_in_a_recording", "applied")])
                photos = [row for row in records if row["namespace"] == "student_photos"]
                self.assertEqual([(row["version"], row["adoption"]) for row in photos], [("001_photos", "applied")])
                notes = [row for row in records if row["namespace"] == "course_notes"]
                self.assertEqual([(row["version"], row["adoption"]) for row in notes], [("001_notes", "applied")])
                bank = [row for row in records if row["namespace"] == "question_bank"]
                self.assertEqual([(row["version"], row["adoption"]) for row in bank], [("001_bank", "applied"), ("002_versions", "applied"), ("003_pictures", "applied")])
                record = [row for row in records if row["namespace"] == "communications"]
                self.assertEqual([(row["version"], row["adoption"]) for row in record], [("001_record", "applied"), ("002_canvas_notices", "applied"),
                                                                                         ("003_coordinator_links", "applied")])
                # COORDINATOR: the term's coordinator record is a new table, and A COORDINATOR'S FOLLOW-UP (student_support 018)
                # makes four follow-up tables again so a term's coordinator may own a record; both applied as new, nothing left behind.
                self.assertEqual([row["adoption"] for row in records if row["namespace"] == "course_coordinators"], ["applied"])
                self.assertEqual([row["adoption"] for row in records if row["version"] == "018_coordinator_owner"], ["applied"])
                self.assertEqual(db.execute("SELECT name FROM sqlite_master WHERE name LIKE '%\\_next' ESCAPE '\\' AND type='table'").fetchall(), [])
                self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])
                absences = [row for row in records if row["namespace"] == "absences"]
                self.assertEqual([(row["version"], row["adoption"]) for row in absences], [("001_absences", "applied"), ("002_makeup_booking", "applied"), ("003_absence_notice", "applied"), ("004_absence_death", "applied"), ("005_makeup_holds", "applied"), ("006_makeup_copies", "applied"), ("007_department_makeups", "applied"), ("008_department_notices", "applied"), ("009_makeups_missed", "applied")])
                notes_of_students = [row for row in records if row["namespace"] == "student_notes"]
                self.assertEqual([(row["version"], row["adoption"]) for row in notes_of_students], [("001_notes", "applied")])
                gradebook = [row for row in records if row["namespace"] == "gradebook"]
                self.assertEqual([(row["version"], row["adoption"]) for row in gradebook], [("001_gradebook", "applied"), ("002_canvas_links", "applied"), ("003_canvas_sends", "applied"), ("004_iclicker_links", "applied"), ("005_canvas_created", "applied"), ("006_iclicker_sessions", "applied"), ("007_nightly_runs", "applied"), ("008_iclicker_runs", "applied"), ("009_canvas_posts", "applied"), ("010_canvas_published", "applied"), ("011_canvas_snapshots", "applied"), ("012_canvas_posting_policy", "applied"), ("013_canvas_nightly_runs", "applied"), ("014_iclicker_credits", "applied")])
                # RECORDINGS (001, step 1 Intake; 002, step 2: a kept transcript's lines and who named a speaker; 003, step 3: readings,
                # items, what became of them, a term's coordinator; 004, step 6: a course link's purpose, `training`; 005, ONLY WHAT
                # IS FACULTY'S IS KEPT: Plaud's ID of a recording that is not kept, and the one way a recording is erased): applied as new.
                kept = [row for row in records if row["namespace"] == "recordings"]
                self.assertEqual([(row["version"], row["adoption"]) for row in kept],
                                 [("001_recordings", "applied"), ("002_transcript_lines", "applied"), ("003_readings", "applied"),
                                  ("004_training", "applied"), ("005_not_kept", "applied")])
                # 004 adds a column to an append-only table in place: every link there was keeps its place and has no purpose.
                self.assertEqual([row[1] for row in db.execute("PRAGMA table_info(recording_links)")][-1], "purpose")
                # 005 replaces the eleven triggers that retain a recording's rows, in place: each is still there, refuses a DELETE
                # as before (no recording is being erased), and a term's coordinator is retained as it was.
                triggers = {row[0]: row[1] for row in db.execute("SELECT name, sql FROM sqlite_master WHERE type='trigger' AND name LIKE 'recording%retained'")}
                self.assertEqual(len(triggers), 12)
                self.assertEqual(sorted(name for name, sql in triggers.items() if "recording_erasing" not in sql), ["recording_coordinators_retained"])
                self.assertEqual(db.execute("SELECT recording_id FROM recording_erasing").fetchall()[0][0], None)
                with migration_transaction(db):
                    apply_sql(db, "document_identity", "999_test", "CREATE TABLE test_upgrade(id INTEGER);")
                self.assertEqual(db.execute("SELECT adoption FROM schema_migrations WHERE version='999_test'").fetchone()[0], "applied")
            create_app(directory)
            with connect(restarted.config["DATABASE"]) as db:
                self.assertEqual([dict(row) for row in db.execute("SELECT * FROM schema_migrations WHERE version!='999_test' ORDER BY namespace,version")], records)

    def test_response_mail_legacy_rename_rolls_back_when_schema_application_fails(self):
        from app import create_app
        from response_cards.service import ResponseCardService
        from rosters import connect
        with tempfile.TemporaryDirectory() as directory:
            app = create_app(directory)
            cards = app.extensions["response_cards"]
            with connect(cards.path) as db:
                db.execute("DELETE FROM schema_migrations WHERE namespace='response_cards'")
                db.execute("DROP TABLE response_card_mail")
                db.execute("CREATE TABLE response_card_mail(id TEXT PRIMARY KEY, card_id TEXT, kind TEXT, submission_id TEXT, UNIQUE(card_id,kind,submission_id))")
                db.execute("INSERT INTO response_card_mail VALUES('retained','card','invitation','')")
                db.execute("CREATE INDEX response_card_mail_queue ON response_card_mail(kind)")
            with patch("response_cards.service.apply_sql", side_effect=sqlite3.OperationalError("interrupted upgrade")):
                with self.assertRaisesRegex(sqlite3.OperationalError, "interrupted"):
                    ResponseCardService(cards.rosters)
            with connect(cards.path) as db:
                self.assertEqual(db.execute("SELECT id FROM response_card_mail").fetchone()[0], "retained")
                self.assertIsNone(db.execute("SELECT 1 FROM sqlite_master WHERE name='response_card_mail_legacy'").fetchone())
                self.assertIsNotNone(db.execute("SELECT 1 FROM sqlite_master WHERE name='response_card_mail_queue'").fetchone())
                self.assertEqual(db.execute("SELECT count(*) FROM schema_migrations WHERE namespace='response_cards'").fetchone()[0], 0)
