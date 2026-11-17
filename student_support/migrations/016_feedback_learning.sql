-- ASSISTANT LEARNING: cases belong to an enrollment, lessons to a term or explicitly to the workspace.
-- Source text remains in its existing action/message owner. These tables keep references and reviewed guidance only.
CREATE TABLE student_support_learning_cases (
 id TEXT PRIMARY KEY,
 term_id TEXT NOT NULL REFERENCES course_terms(id),
 enrollment_id TEXT NOT NULL REFERENCES student_enrollments(id),
 run_id TEXT NOT NULL REFERENCES student_support_suggestion_runs(id),
 action_id TEXT NOT NULL REFERENCES assistant_actions(id),
 final_action_id TEXT NOT NULL REFERENCES assistant_actions(id),
 feedback TEXT NOT NULL,
 strength TEXT NOT NULL,
 fingerprint TEXT NOT NULL UNIQUE,
 created_at TEXT NOT NULL
);
CREATE INDEX student_support_learning_term ON student_support_learning_cases(term_id,created_at,id);
CREATE TABLE student_support_learning_events (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 case_id TEXT NOT NULL REFERENCES student_support_learning_cases(id),
 state TEXT NOT NULL,
 detail_json TEXT NOT NULL CHECK(json_valid(detail_json)),
 request_id TEXT UNIQUE,
 request_sha256 TEXT,
 created_at TEXT NOT NULL
);
CREATE INDEX student_support_learning_history ON student_support_learning_events(case_id,id);
CREATE TABLE student_support_learning_lessons (
 id TEXT PRIMARY KEY,
 case_id TEXT NOT NULL UNIQUE REFERENCES student_support_learning_cases(id),
 term_id TEXT REFERENCES course_terms(id),
 scope TEXT NOT NULL CHECK(scope IN ('term','workspace')),
 created_at TEXT NOT NULL,
 CHECK((scope='term' AND term_id IS NOT NULL) OR (scope='workspace' AND term_id IS NULL))
);
CREATE TABLE student_support_learning_lesson_events (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 lesson_id TEXT NOT NULL REFERENCES student_support_learning_lessons(id),
 state TEXT NOT NULL CHECK(state IN ('active','removed')),
 detail_json TEXT NOT NULL CHECK(json_valid(detail_json)),
 request_id TEXT NOT NULL UNIQUE,
 request_sha256 TEXT NOT NULL,
 created_at TEXT NOT NULL
);
CREATE INDEX student_support_learning_lesson_history ON student_support_learning_lesson_events(lesson_id,id);
CREATE TABLE student_support_learning_uses (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 run_id TEXT NOT NULL REFERENCES student_support_suggestion_runs(id),
 source_kind TEXT NOT NULL CHECK(source_kind IN ('case','lesson')),
 source_id TEXT NOT NULL,
 source_revision INTEGER NOT NULL,
 decision_id TEXT NOT NULL REFERENCES jev_calls(id),
 created_at TEXT NOT NULL,
 UNIQUE(run_id,source_kind,source_id,source_revision)
);
CREATE INDEX student_support_learning_use_run ON student_support_learning_uses(run_id,id);
CREATE TRIGGER student_support_learning_cases_immutable BEFORE UPDATE ON student_support_learning_cases
 BEGIN SELECT RAISE(ABORT,'ASSISTANT: learning case identities are immutable'); END;
CREATE TRIGGER student_support_learning_cases_retained BEFORE DELETE ON student_support_learning_cases
 BEGIN SELECT RAISE(ABORT,'ASSISTANT: learning case references are retained'); END;
CREATE TRIGGER student_support_learning_events_immutable BEFORE UPDATE ON student_support_learning_events
 BEGIN SELECT RAISE(ABORT,'ASSISTANT: learning receipts are immutable'); END;
CREATE TRIGGER student_support_learning_events_retained BEFORE DELETE ON student_support_learning_events
 BEGIN SELECT RAISE(ABORT,'ASSISTANT: learning receipts are retained'); END;
CREATE TRIGGER student_support_learning_lessons_immutable BEFORE UPDATE ON student_support_learning_lessons
 BEGIN SELECT RAISE(ABORT,'ASSISTANT: learning lesson owners are immutable'); END;
CREATE TRIGGER student_support_learning_lessons_retained BEFORE DELETE ON student_support_learning_lessons
 BEGIN SELECT RAISE(ABORT,'ASSISTANT: learning lesson identities are retained'); END;
CREATE TRIGGER student_support_learning_lesson_events_immutable BEFORE UPDATE ON student_support_learning_lesson_events
 BEGIN SELECT RAISE(ABORT,'ASSISTANT: learning guidance revisions are immutable'); END;
CREATE TRIGGER student_support_learning_lesson_events_retained BEFORE DELETE ON student_support_learning_lesson_events
 BEGIN SELECT RAISE(ABORT,'ASSISTANT: learning guidance revisions are retained'); END;
CREATE TRIGGER student_support_learning_uses_immutable BEFORE UPDATE ON student_support_learning_uses
 BEGIN SELECT RAISE(ABORT,'ASSISTANT: learning use receipts are immutable'); END;
CREATE TRIGGER student_support_learning_uses_retained BEFORE DELETE ON student_support_learning_uses
 BEGIN SELECT RAISE(ABORT,'ASSISTANT: learning use receipts are retained'); END;
