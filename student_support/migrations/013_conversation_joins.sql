-- TEACHING-WORKFLOWS SAME MATTER (2026-10-07, requirement, of Fiona Weaver's "Okay thank you so much!" that started a second
-- follow-up beside "Arrange DR-center quizzes for Fiona": "these two follow ups belong together"; they chose that Jev joins a new
-- email the student starts to their existing follow-up when it is confident, with Keep separate, asks with one click when it is
-- unsure, and that they can join or separate conversations themselves). One decision about one of a student's conversations
-- (`source_key`, as the follow-up list names it): `join` reads it as part of the conversation `into_key`; `separate` keeps it on
-- its own (the instructor's Keep separate, which also ends an earlier join); `ask` is Jev's unsure reading, shown as a one-click
-- question naming `into_key`; `none` is Jev finding no follow-up it continues (or no answer), so it is never asked again. The
-- latest decision per conversation counts; records keep the keys they were saved with and lookups resolve them. `call_id` names
-- the Jev receipt a Jev decision came from. Enrollment-owned (the student's conversations). Append-only; nothing is sent here and
-- no message text is copied.
CREATE TABLE student_support_conversation_joins (
 id TEXT PRIMARY KEY,
 enrollment_id TEXT NOT NULL REFERENCES student_enrollments(id),
 source_key TEXT NOT NULL CHECK(length(source_key) BETWEEN 1 AND 500),
 into_key TEXT NOT NULL DEFAULT '' CHECK(length(into_key) <= 500 AND into_key != source_key),
 decision TEXT NOT NULL CHECK(decision IN ('join','separate','ask','none')),
 decided_by TEXT NOT NULL CHECK(decided_by IN ('instructor','jev')),
 call_id TEXT NOT NULL DEFAULT '',
 request_id TEXT NOT NULL UNIQUE CHECK(length(request_id) BETWEEN 1 AND 100),
 request_sha256 TEXT NOT NULL,
 actor TEXT NOT NULL,
 created_at TEXT NOT NULL,
 CHECK((decision IN ('join','ask')) = (into_key != '')),
 CHECK(decided_by = 'jev' OR decision IN ('join','separate'))
);
CREATE INDEX student_support_conversation_joins_source ON student_support_conversation_joins(enrollment_id, source_key);
CREATE TRIGGER student_support_conversation_joins_immutable BEFORE UPDATE ON student_support_conversation_joins
 BEGIN SELECT RAISE(ABORT,'TEACHING-WORKFLOWS: conversation joins are append-only'); END;
CREATE TRIGGER student_support_conversation_joins_retained BEFORE DELETE ON student_support_conversation_joins
 BEGIN SELECT RAISE(ABORT,'TEACHING-WORKFLOWS: conversation joins are retained'); END;
