-- TEACHING-WORKFLOWS MEETING HOURS (requirement, 2026-10-05): the weekly windows in which one course
-- term's individual meetings and quiz makeup times are offered. Term-owned (course_terms.id), so a new term starts with none;
-- immutable revisions with exact request IDs; an empty list clears them.
CREATE TABLE student_support_meeting_hours (
 term_id TEXT NOT NULL REFERENCES course_terms(id),
 revision INTEGER NOT NULL CHECK(revision >= 1),
 windows_json TEXT NOT NULL CHECK(json_valid(windows_json) AND json_type(windows_json)='array' AND length(windows_json) <= 4000),
 request_id TEXT NOT NULL CHECK(length(request_id) BETWEEN 1 AND 100),
 request_sha256 TEXT NOT NULL,
 actor TEXT NOT NULL,
 created_at TEXT NOT NULL,
 PRIMARY KEY(term_id,revision),
 UNIQUE(term_id,request_id)
);
CREATE TRIGGER student_support_meeting_hours_next BEFORE INSERT ON student_support_meeting_hours
 WHEN NEW.revision != 1 + COALESCE((SELECT MAX(revision) FROM student_support_meeting_hours WHERE term_id=NEW.term_id), 0)
 BEGIN SELECT RAISE(ABORT,'TEACHING-WORKFLOWS: a meeting-hours revision must follow the latest one'); END;
CREATE TRIGGER student_support_meeting_hours_immutable BEFORE UPDATE ON student_support_meeting_hours
 BEGIN SELECT RAISE(ABORT,'TEACHING-WORKFLOWS: meeting-hours revisions are immutable'); END;
CREATE TRIGGER student_support_meeting_hours_retained BEFORE DELETE ON student_support_meeting_hours
 BEGIN SELECT RAISE(ABORT,'TEACHING-WORKFLOWS: meeting-hours revisions are retained'); END;
