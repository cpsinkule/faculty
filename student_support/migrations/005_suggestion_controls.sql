-- ASSISTANT / TEACHING-WORKFLOWS (2026-10-03, requirement's follow-up suggestion review): the instructor's own controls over
-- automatic follow-up suggestions. Every table is append-only: the latest row decides. Nothing here copies a message body.

-- "Prepare now": one queued suggestion goes first. Enrollment-owned through its run.
CREATE TABLE student_support_suggestion_priorities (
 run_id TEXT NOT NULL REFERENCES student_support_suggestion_runs(id),
 request_id TEXT NOT NULL UNIQUE,
 actor TEXT NOT NULL,
 created_at TEXT NOT NULL,
 PRIMARY KEY(run_id,request_id)
);

-- "Don't suggest for this conversation" and "Turn suggestions back on". Enrollment-owned.
CREATE TABLE student_support_suggestion_mutes (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 enrollment_id TEXT NOT NULL REFERENCES student_enrollments(id),
 item_key TEXT NOT NULL,
 muted INTEGER NOT NULL CHECK(muted IN (0,1)),
 request_id TEXT NOT NULL UNIQUE,
 request_sha256 TEXT NOT NULL,
 actor TEXT NOT NULL,
 created_at TEXT NOT NULL
);
CREATE INDEX student_support_suggestion_mute_target ON student_support_suggestion_mutes(enrollment_id,item_key,id);

-- Pausing automatic preparation for the whole workspace. Workspace-owned.
CREATE TABLE student_support_suggestion_pauses (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 paused INTEGER NOT NULL CHECK(paused IN (0,1)),
 request_id TEXT NOT NULL UNIQUE,
 request_sha256 TEXT NOT NULL,
 actor TEXT NOT NULL,
 created_at TEXT NOT NULL
);

-- An edited version the instructor sent from Mail in a mail card's place. Enrollment-owned through its run; the Mail send keeps
-- its own owner and is referenced by ID.
CREATE TABLE student_support_suggestion_replacements (
 action_id TEXT PRIMARY KEY REFERENCES assistant_actions(id),
 run_id TEXT NOT NULL REFERENCES student_support_suggestion_runs(id),
 send_id TEXT NOT NULL UNIQUE REFERENCES gmail_mail_sends(id),
 request_id TEXT NOT NULL UNIQUE,
 actor TEXT NOT NULL,
 created_at TEXT NOT NULL
);

-- The instructor's note on a card they declined, read by later preparation of the same follow-up. Enrollment-owned through its run.
CREATE TABLE student_support_suggestion_notes (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 run_id TEXT NOT NULL REFERENCES student_support_suggestion_runs(id),
 action_id TEXT NOT NULL REFERENCES assistant_actions(id),
 text TEXT NOT NULL CHECK(length(text) BETWEEN 1 AND 2000),
 request_id TEXT NOT NULL UNIQUE,
 request_sha256 TEXT NOT NULL,
 actor TEXT NOT NULL,
 created_at TEXT NOT NULL
);
CREATE INDEX student_support_suggestion_note_action ON student_support_suggestion_notes(action_id,id);

CREATE TRIGGER student_support_suggestion_priorities_immutable BEFORE UPDATE ON student_support_suggestion_priorities
 BEGIN SELECT RAISE(ABORT,'TEACHING-WORKFLOWS: suggestion priorities are append-only'); END;
CREATE TRIGGER student_support_suggestion_priorities_retained BEFORE DELETE ON student_support_suggestion_priorities
 BEGIN SELECT RAISE(ABORT,'TEACHING-WORKFLOWS: suggestion priorities are retained'); END;
CREATE TRIGGER student_support_suggestion_mutes_immutable BEFORE UPDATE ON student_support_suggestion_mutes
 BEGIN SELECT RAISE(ABORT,'TEACHING-WORKFLOWS: suggestion mutes are append-only'); END;
CREATE TRIGGER student_support_suggestion_mutes_retained BEFORE DELETE ON student_support_suggestion_mutes
 BEGIN SELECT RAISE(ABORT,'TEACHING-WORKFLOWS: suggestion mutes are retained'); END;
CREATE TRIGGER student_support_suggestion_pauses_immutable BEFORE UPDATE ON student_support_suggestion_pauses
 BEGIN SELECT RAISE(ABORT,'TEACHING-WORKFLOWS: suggestion pauses are append-only'); END;
CREATE TRIGGER student_support_suggestion_pauses_retained BEFORE DELETE ON student_support_suggestion_pauses
 BEGIN SELECT RAISE(ABORT,'TEACHING-WORKFLOWS: suggestion pauses are retained'); END;
CREATE TRIGGER student_support_suggestion_replacements_immutable BEFORE UPDATE ON student_support_suggestion_replacements
 BEGIN SELECT RAISE(ABORT,'TEACHING-WORKFLOWS: suggestion replacements are append-only'); END;
CREATE TRIGGER student_support_suggestion_replacements_retained BEFORE DELETE ON student_support_suggestion_replacements
 BEGIN SELECT RAISE(ABORT,'TEACHING-WORKFLOWS: suggestion replacements are retained'); END;
CREATE TRIGGER student_support_suggestion_notes_immutable BEFORE UPDATE ON student_support_suggestion_notes
 BEGIN SELECT RAISE(ABORT,'TEACHING-WORKFLOWS: suggestion notes are append-only'); END;
CREATE TRIGGER student_support_suggestion_notes_retained BEFORE DELETE ON student_support_suggestion_notes
 BEGIN SELECT RAISE(ABORT,'TEACHING-WORKFLOWS: suggestion notes are retained'); END;
