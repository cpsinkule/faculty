-- ASSISTANT / TEACHING-WORKFLOWS: automatic proposals belong to the student's term enrollment.
-- Source metadata contains identities, revisions and digests, never copied communications bodies.
CREATE TABLE student_support_suggestion_runs (
 id TEXT PRIMARY KEY,
 enrollment_id TEXT NOT NULL REFERENCES student_enrollments(id),
 item_key TEXT NOT NULL,
 item_id TEXT NOT NULL,
 source_key TEXT NOT NULL,
 source_sha256 TEXT NOT NULL,
 source_json TEXT NOT NULL CHECK(json_valid(source_json)),
 parent_id TEXT REFERENCES student_support_suggestion_runs(id),
 reason TEXT NOT NULL CHECK(reason IN ('automatic','revise','retry','answer','receipts')),
 request_id TEXT NOT NULL UNIQUE,
 request_sha256 TEXT NOT NULL,
 instruction TEXT NOT NULL,
 model TEXT NOT NULL,
 created_at TEXT NOT NULL
);
CREATE INDEX student_support_suggestion_target ON student_support_suggestion_runs(enrollment_id,item_key,created_at,id);
CREATE TABLE student_support_suggestion_events (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 run_id TEXT NOT NULL REFERENCES student_support_suggestion_runs(id),
 state TEXT NOT NULL CHECK(state IN ('queued','running','ready','needs_input','failed','interrupted','superseded','settled')),
 detail_json TEXT NOT NULL CHECK(json_valid(detail_json)),
 created_at TEXT NOT NULL
);
CREATE INDEX student_support_suggestion_event_run ON student_support_suggestion_events(run_id,id);
CREATE TABLE student_support_suggestion_actions (
 run_id TEXT NOT NULL REFERENCES student_support_suggestion_runs(id),
 action_id TEXT NOT NULL UNIQUE REFERENCES assistant_actions(id),
 request_sha256 TEXT NOT NULL,
 PRIMARY KEY(run_id,action_id)
);
CREATE TRIGGER student_support_suggestion_runs_immutable BEFORE UPDATE ON student_support_suggestion_runs
 BEGIN SELECT RAISE(ABORT,'TEACHING-WORKFLOWS: suggestion runs are immutable'); END;
CREATE TRIGGER student_support_suggestion_runs_retained BEFORE DELETE ON student_support_suggestion_runs
 BEGIN SELECT RAISE(ABORT,'TEACHING-WORKFLOWS: suggestion runs are retained'); END;
CREATE TRIGGER student_support_suggestion_events_immutable BEFORE UPDATE ON student_support_suggestion_events
 BEGIN SELECT RAISE(ABORT,'TEACHING-WORKFLOWS: suggestion events are append-only'); END;
CREATE TRIGGER student_support_suggestion_events_retained BEFORE DELETE ON student_support_suggestion_events
 BEGIN SELECT RAISE(ABORT,'TEACHING-WORKFLOWS: suggestion events are retained'); END;
CREATE TRIGGER student_support_suggestion_actions_immutable BEFORE UPDATE ON student_support_suggestion_actions
 BEGIN SELECT RAISE(ABORT,'TEACHING-WORKFLOWS: suggestion action references are immutable'); END;
CREATE TRIGGER student_support_suggestion_actions_retained BEFORE DELETE ON student_support_suggestion_actions
 BEGIN SELECT RAISE(ABORT,'TEACHING-WORKFLOWS: suggestion action references are retained'); END;
