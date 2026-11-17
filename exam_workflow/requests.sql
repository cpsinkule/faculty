-- Requests belong to a term exam and a permanent section/enrollment membership.
-- Card issues and immutable submissions are references, never copied credentials.
CREATE TABLE IF NOT EXISTS exam_requests (
 id TEXT PRIMARY KEY, event_id TEXT NOT NULL REFERENCES exam_events(id),
 section_id TEXT NOT NULL, enrollment_id TEXT NOT NULL,
 kind TEXT NOT NULL CHECK(kind IN ('seat','schedule','notice','makeup','other')),
 description TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'open'
 CHECK(status IN ('open','in_review','approved','declined','resolved')),
 decision TEXT NOT NULL DEFAULT '', version INTEGER NOT NULL DEFAULT 1,
 source_submission_id TEXT UNIQUE REFERENCES response_card_submissions(id),
 applied_hash TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 FOREIGN KEY(event_id,section_id,enrollment_id)
 REFERENCES exam_participants(event_id,section_id,enrollment_id)
);
CREATE TABLE IF NOT EXISTS exam_request_history (
 id INTEGER PRIMARY KEY, request_id TEXT NOT NULL REFERENCES exam_requests(id),
 kind TEXT NOT NULL, detail_json TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS exam_communication_issues (
 issue_id TEXT PRIMARY KEY REFERENCES response_card_issues(id),
 event_id TEXT NOT NULL REFERENCES exam_events(id),
 kind TEXT NOT NULL CHECK(kind IN ('request','decision','feedback')),
 request_id TEXT REFERENCES exam_requests(id), release_id TEXT,
 created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS exam_managed_templates (
 definition_id TEXT NOT NULL REFERENCES course_definitions(id),
 kind TEXT NOT NULL, template_id TEXT NOT NULL REFERENCES response_card_templates(id),
 PRIMARY KEY(definition_id,kind)
);
CREATE INDEX IF NOT EXISTS exam_requests_event ON exam_requests(event_id,status);
CREATE TRIGGER IF NOT EXISTS exam_request_owner BEFORE UPDATE OF id,event_id,section_id,enrollment_id,kind,description,source_submission_id,created_at ON exam_requests
 BEGIN SELECT RAISE(ABORT,'Exam request source and ownership are retained'); END;
CREATE TRIGGER IF NOT EXISTS exam_request_retained BEFORE DELETE ON exam_requests
 BEGIN SELECT RAISE(ABORT,'Exam requests are retained'); END;
CREATE TRIGGER IF NOT EXISTS exam_request_history_update BEFORE UPDATE ON exam_request_history
 BEGIN SELECT RAISE(ABORT,'Exam request history is immutable'); END;
CREATE TRIGGER IF NOT EXISTS exam_request_history_delete BEFORE DELETE ON exam_request_history
 BEGIN SELECT RAISE(ABORT,'Exam request history is immutable'); END;
