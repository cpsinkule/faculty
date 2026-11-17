-- ASSISTANT / TEACHING-WORKFLOWS DONE FOR YOU (2026-10-04, requirement: "increase automation and integration in the app to
-- minimize my effort"; asked, they chose that the Assistant records the absence, attaches the student's files and puts agreed
-- office hours on the calendar by itself, each shown on the follow-up with Undo): the instructor's Undo of one record a follow-up
-- preparation made by itself. The inverse write (an absence withdrawn, a document removed, an appointment cancelled) is the
-- owning service's own revision with the request ID `owner_request_id`; this row records that it was the instructor's Undo.
-- Enrollment-owned through its run. Append-only; nothing is sent here.
CREATE TABLE student_support_suggestion_undos (
 action_id TEXT PRIMARY KEY REFERENCES assistant_actions(id),
 run_id TEXT NOT NULL REFERENCES student_support_suggestion_runs(id),
 kind TEXT NOT NULL CHECK(kind IN ('undone','cancelled')),
 owner_request_id TEXT NOT NULL,
 outcome_json TEXT NOT NULL CHECK(json_valid(outcome_json)),
 request_id TEXT NOT NULL UNIQUE,
 request_sha256 TEXT NOT NULL,
 actor TEXT NOT NULL,
 created_at TEXT NOT NULL
);
CREATE INDEX student_support_suggestion_undo_run ON student_support_suggestion_undos(run_id);
CREATE TRIGGER student_support_suggestion_undos_immutable BEFORE UPDATE ON student_support_suggestion_undos
 BEGIN SELECT RAISE(ABORT,'TEACHING-WORKFLOWS: suggestion undos are append-only'); END;
CREATE TRIGGER student_support_suggestion_undos_retained BEFORE DELETE ON student_support_suggestion_undos
 BEGIN SELECT RAISE(ABORT,'TEACHING-WORKFLOWS: suggestion undos are retained'); END;
