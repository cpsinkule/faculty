-- ASSISTANT / TEACHING-WORKFLOWS EDIT IN PLACE (2026-10-04, requirement: "I would rather edit it in place because I want to see
-- everything in the follow up card at once before I approve any part of it"): the instructor's own edit of a waiting mail card,
-- saved on the card. The edited mail is a new waiting card of the same run (an ordinary Assistant action, approved only by the
-- instructor's Send); the card it replaces is declined as edited. Enrollment-owned through its run. Append-only; nothing is sent
-- here and no message body is copied (the edited text lives in the new action's own request).
CREATE TABLE student_support_suggestion_edits (
 action_id TEXT PRIMARY KEY REFERENCES assistant_actions(id),
 run_id TEXT NOT NULL REFERENCES student_support_suggestion_runs(id),
 edited_action_id TEXT NOT NULL UNIQUE REFERENCES assistant_actions(id),
 request_id TEXT NOT NULL UNIQUE,
 request_sha256 TEXT NOT NULL,
 actor TEXT NOT NULL,
 created_at TEXT NOT NULL
);
CREATE INDEX student_support_suggestion_edit_run ON student_support_suggestion_edits(run_id);
CREATE TRIGGER student_support_suggestion_edits_immutable BEFORE UPDATE ON student_support_suggestion_edits
 BEGIN SELECT RAISE(ABORT,'TEACHING-WORKFLOWS: suggestion edits are append-only'); END;
CREATE TRIGGER student_support_suggestion_edits_retained BEFORE DELETE ON student_support_suggestion_edits
 BEGIN SELECT RAISE(ABORT,'TEACHING-WORKFLOWS: suggestion edits are retained'); END;
