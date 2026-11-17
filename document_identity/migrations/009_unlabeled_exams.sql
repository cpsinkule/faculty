-- Exams printed without labels (requirement, 2026-09-24): DR-center and makeup exams arrive in one scan with bubble sheets and
-- labeled paper. On an explicit request the covers are found and read (name, UIN, section, printed version); a confident match
-- identifies the student, anything less or anyone who already has a paper waits for the instructor. Registering an identified
-- cover gives it the page 1 identity of that student's stamps-only label, reused when an issued label was never scanned or
-- issued in a new label job that is never printed; the existing first-page grouping then takes the other sides by position.
-- Runs belong to the source Inbox message; reads are immutable machine readings; decisions and registrations are append-only.
CREATE TABLE IF NOT EXISTS scan_unlabeled_runs (
 id TEXT PRIMARY KEY NOT NULL, message_id TEXT NOT NULL REFERENCES inbox_messages(id), attempt INTEGER NOT NULL CHECK(attempt>=1),
 assignment_id TEXT NOT NULL REFERENCES assignments(id), term_id TEXT NOT NULL REFERENCES course_terms(id),
 sides INTEGER NOT NULL CHECK(sides BETWEEN 1 AND 200),
 status TEXT NOT NULL CHECK(status IN ('pending','running','complete','failed','cancelled')), error TEXT NOT NULL DEFAULT '',
 actor TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE(message_id,attempt)
);
CREATE INDEX IF NOT EXISTS scan_unlabeled_runs_status ON scan_unlabeled_runs(status,created_at);
CREATE TRIGGER IF NOT EXISTS scan_unlabeled_run_scope BEFORE UPDATE ON scan_unlabeled_runs
 WHEN NEW.id!=OLD.id OR NEW.message_id!=OLD.message_id OR NEW.attempt!=OLD.attempt OR NEW.assignment_id!=OLD.assignment_id
  OR NEW.term_id!=OLD.term_id OR NEW.sides!=OLD.sides OR NEW.actor!=OLD.actor OR NEW.created_at!=OLD.created_at
 BEGIN SELECT RAISE(ABORT,'A run keeps its scope'); END;
CREATE TRIGGER IF NOT EXISTS scan_unlabeled_run_retained BEFORE DELETE ON scan_unlabeled_runs BEGIN SELECT RAISE(ABORT,'Runs are retained'); END;

CREATE TABLE IF NOT EXISTS scan_unlabeled_reads (
 run_id TEXT NOT NULL REFERENCES scan_unlabeled_runs(id), scan_page_id TEXT NOT NULL REFERENCES scan_pages(id),
 cover INTEGER NOT NULL CHECK(cover IN (0,1)), reading_json TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(run_id,scan_page_id)
);
CREATE TRIGGER IF NOT EXISTS scan_unlabeled_read_immutable BEFORE UPDATE ON scan_unlabeled_reads BEGIN SELECT RAISE(ABORT,'Machine readings are immutable'); END;
CREATE TRIGGER IF NOT EXISTS scan_unlabeled_read_retained BEFORE DELETE ON scan_unlabeled_reads BEGIN SELECT RAISE(ABORT,'Machine readings are retained'); END;

-- The instructor's word on one cover: this student, not an exam cover, or back to what was read. The latest counts.
CREATE TABLE IF NOT EXISTS scan_unlabeled_reviews (
 id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL REFERENCES scan_unlabeled_runs(id),
 scan_page_id TEXT NOT NULL REFERENCES scan_pages(id), decision TEXT NOT NULL CHECK(decision IN ('student','not_cover','read')),
 enrollment_id TEXT, section_id TEXT REFERENCES course_sections(id), actor TEXT NOT NULL, created_at TEXT NOT NULL,
 CHECK((decision='student')=(enrollment_id IS NOT NULL AND section_id IS NOT NULL)),
 FOREIGN KEY(section_id,enrollment_id) REFERENCES roster_memberships(section_id,enrollment_id)
);
CREATE INDEX IF NOT EXISTS scan_unlabeled_reviews_page ON scan_unlabeled_reviews(run_id,scan_page_id,id);
CREATE TRIGGER IF NOT EXISTS scan_unlabeled_review_immutable BEFORE UPDATE ON scan_unlabeled_reviews BEGIN SELECT RAISE(ABORT,'Reviews are append-only'); END;
CREATE TRIGGER IF NOT EXISTS scan_unlabeled_review_retained BEFORE DELETE ON scan_unlabeled_reviews BEGIN SELECT RAISE(ABORT,'Reviews are retained'); END;

-- What the instructor asked to register: a cover, its student and the copy whose page 1 it takes (an unscanned issued label),
-- or the new label job that issues one. Applied once that job is ready.
CREATE TABLE IF NOT EXISTS scan_unlabeled_plans (
 run_id TEXT NOT NULL REFERENCES scan_unlabeled_runs(id), scan_page_id TEXT NOT NULL REFERENCES scan_pages(id),
 enrollment_id TEXT NOT NULL, section_id TEXT NOT NULL REFERENCES course_sections(id),
 basis TEXT NOT NULL CHECK(basis IN ('uin','confidence','review')), printed_copy_id TEXT REFERENCES printed_copies(id),
 print_batch_id TEXT REFERENCES print_batches(id), request_id TEXT NOT NULL, actor TEXT NOT NULL, created_at TEXT NOT NULL,
 PRIMARY KEY(run_id,scan_page_id), CHECK((printed_copy_id IS NULL)!=(print_batch_id IS NULL)),
 FOREIGN KEY(section_id,enrollment_id) REFERENCES roster_memberships(section_id,enrollment_id)
);
CREATE INDEX IF NOT EXISTS scan_unlabeled_plans_batch ON scan_unlabeled_plans(print_batch_id);
CREATE TRIGGER IF NOT EXISTS scan_unlabeled_plan_immutable BEFORE UPDATE ON scan_unlabeled_plans BEGIN SELECT RAISE(ABORT,'Registration requests are immutable'); END;
CREATE TRIGGER IF NOT EXISTS scan_unlabeled_plan_retained BEFORE DELETE ON scan_unlabeled_plans BEGIN SELECT RAISE(ABORT,'Registration requests are retained'); END;

-- The receipt: this cover took this page 1 identity.
CREATE TABLE IF NOT EXISTS scan_unlabeled_registrations (
 scan_page_id TEXT PRIMARY KEY NOT NULL REFERENCES scan_pages(id), run_id TEXT NOT NULL REFERENCES scan_unlabeled_runs(id),
 printed_copy_id TEXT NOT NULL REFERENCES printed_copies(id), page_stamp_id TEXT NOT NULL REFERENCES page_stamps(id),
 enrollment_id TEXT NOT NULL, basis TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS scan_unlabeled_registrations_run ON scan_unlabeled_registrations(run_id);
CREATE TRIGGER IF NOT EXISTS scan_unlabeled_registration_immutable BEFORE UPDATE ON scan_unlabeled_registrations BEGIN SELECT RAISE(ABORT,'Registrations are immutable'); END;
CREATE TRIGGER IF NOT EXISTS scan_unlabeled_registration_retained BEFORE DELETE ON scan_unlabeled_registrations BEGIN SELECT RAISE(ABORT,'Registrations are retained'); END;
