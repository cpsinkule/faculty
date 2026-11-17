-- DEPARTMENT MAKEUP EXAMS (ABSENCES; requirement, 2026-10-22). Faculty fills out the
-- Math department's makeup exam request form (a public Google Form) for a student whose excused absence covers an exam's day.
--
-- absence_department_forms: the department's form for a course term, by revision (a new term's form is a new link). Owner: the
-- course term. Append-only.
CREATE TABLE IF NOT EXISTS absence_department_forms (
 term_id TEXT NOT NULL REFERENCES course_terms(id),
 revision INTEGER NOT NULL CHECK(revision >= 1),
 form_url TEXT NOT NULL,
 request_id TEXT NOT NULL UNIQUE,
 request_sha256 TEXT NOT NULL,
 actor TEXT NOT NULL,
 created_at TEXT NOT NULL,
 PRIMARY KEY(term_id, revision)
);
CREATE TRIGGER IF NOT EXISTS absence_department_forms_immutable BEFORE UPDATE ON absence_department_forms
BEGIN SELECT RAISE(ABORT,'Department form records are append-only'); END;
CREATE TRIGGER IF NOT EXISTS absence_department_forms_retained BEFORE DELETE ON absence_department_forms
BEGIN SELECT RAISE(ABORT,'Department form records are retained'); END;

-- absence_department_requests: one response Faculty submitted on the instructor's approval, recorded BEFORE it is sent (so a lost
-- answer is never sent twice). Owner: the student's enrollment (the absence it is for); the exam is the course-wide assignment,
-- referenced. `answers_json` keeps what was given except the student's name (names live only in the verified roster payload);
-- `answers_sha256` is the whole response's. Append-only.
CREATE TABLE IF NOT EXISTS absence_department_requests (
 id TEXT PRIMARY KEY,
 request_id TEXT NOT NULL UNIQUE,
 request_sha256 TEXT NOT NULL,
 absence_id TEXT NOT NULL REFERENCES absences(id),
 term_id TEXT NOT NULL REFERENCES course_terms(id),
 section_id TEXT NOT NULL REFERENCES course_sections(id),
 enrollment_id TEXT NOT NULL,
 assignment_id TEXT NOT NULL,
 form_url TEXT NOT NULL,
 session TEXT NOT NULL,
 session_date TEXT NOT NULL,
 base_minutes INTEGER,
 answers_json TEXT NOT NULL,
 answers_sha256 TEXT NOT NULL,
 actor TEXT NOT NULL,
 created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS absence_department_requests_student ON absence_department_requests(enrollment_id, assignment_id);
CREATE TRIGGER IF NOT EXISTS absence_department_requests_immutable BEFORE UPDATE ON absence_department_requests
BEGIN SELECT RAISE(ABORT,'Department makeup requests are append-only'); END;
CREATE TRIGGER IF NOT EXISTS absence_department_requests_retained BEFORE DELETE ON absence_department_requests
BEGIN SELECT RAISE(ABORT,'Department makeup requests are retained'); END;

-- absence_department_outcomes: what Google answered a request, once (accepted, failed, uncertain). Owner: the request. Append-only.
CREATE TABLE IF NOT EXISTS absence_department_outcomes (
 request_row_id TEXT PRIMARY KEY REFERENCES absence_department_requests(id),
 outcome TEXT NOT NULL CHECK(outcome IN ('accepted','failed','uncertain')),
 detail TEXT NOT NULL DEFAULT '',
 sitting_id TEXT,
 created_at TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS absence_department_outcomes_immutable BEFORE UPDATE ON absence_department_outcomes
BEGIN SELECT RAISE(ABORT,'Department makeup outcomes are append-only'); END;
CREATE TRIGGER IF NOT EXISTS absence_department_outcomes_retained BEFORE DELETE ON absence_department_outcomes
BEGIN SELECT RAISE(ABORT,'Department makeup outcomes are retained'); END;

-- absence_department_receipts: Google's receipt email for one response, read from the instructor's Gmail ("Thanks for filling out
-- this form"), whoever filled it out: a request of Faculty's it confirms (`request_row_id`), or one the instructor made on the form
-- itself. Keeps the UIN, section, session and the rules chosen, never a name. Owner: the workspace (the instructor's mailbox).
-- Append-only.
CREATE TABLE IF NOT EXISTS absence_department_receipts (
 gmail_message_id TEXT PRIMARY KEY,
 received_at TEXT NOT NULL,
 uin TEXT NOT NULL,
 section TEXT NOT NULL,
 session TEXT NOT NULL,
 session_date TEXT NOT NULL,
 rules_json TEXT NOT NULL,
 email TEXT NOT NULL DEFAULT '',
 request_row_id TEXT REFERENCES absence_department_requests(id),
 created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS absence_department_receipts_uin ON absence_department_receipts(uin);
CREATE TRIGGER IF NOT EXISTS absence_department_receipts_immutable BEFORE UPDATE ON absence_department_receipts
BEGIN SELECT RAISE(ABORT,'Department makeup receipts are append-only'); END;
CREATE TRIGGER IF NOT EXISTS absence_department_receipts_retained BEFORE DELETE ON absence_department_receipts
BEGIN SELECT RAISE(ABORT,'Department makeup receipts are retained'); END;
