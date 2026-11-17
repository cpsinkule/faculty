-- ASSISTANT / TEACHING-WORKFLOWS: context fingerprints belong to the run's enrollment;
-- watches belong to the same permanent enrollment, never copied messages or files.
CREATE TABLE student_support_context_checks (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 run_id TEXT NOT NULL REFERENCES student_support_suggestion_runs(id),
 fingerprints_json TEXT NOT NULL CHECK(json_valid(fingerprints_json)),
 created_at TEXT NOT NULL
);
CREATE INDEX student_support_context_run ON student_support_context_checks(run_id,id);
CREATE TRIGGER student_support_context_immutable BEFORE UPDATE ON student_support_context_checks
 BEGIN SELECT RAISE(ABORT,'ASSISTANT: context observations are immutable'); END;
CREATE TRIGGER student_support_context_retained BEFORE DELETE ON student_support_context_checks
 BEGIN SELECT RAISE(ABORT,'ASSISTANT: context observations are retained'); END;
CREATE TABLE student_support_watches (
 id TEXT PRIMARY KEY,
 enrollment_id TEXT NOT NULL REFERENCES student_enrollments(id),
 item_key TEXT NOT NULL,
 kind TEXT NOT NULL CHECK(kind IN ('date','absence','paper','grade')),
 target_json TEXT NOT NULL CHECK(json_valid(target_json)),
 check_back TEXT NOT NULL,
 baseline TEXT NOT NULL,
 request_id TEXT NOT NULL UNIQUE,
 request_sha256 TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('active','paused','triggered','removed','error')),
 revision INTEGER NOT NULL DEFAULT 1,
 next_check REAL NOT NULL DEFAULT 0,
 created_at TEXT NOT NULL
);
CREATE TABLE student_support_watch_events (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 watch_id TEXT NOT NULL REFERENCES student_support_watches(id),
 state TEXT NOT NULL,
 detail_json TEXT NOT NULL CHECK(json_valid(detail_json)),
 request_id TEXT UNIQUE,
 created_at TEXT NOT NULL
);
CREATE INDEX student_support_watch_owner ON student_support_watches(enrollment_id,item_key);
CREATE INDEX student_support_watch_history ON student_support_watch_events(watch_id,id);
CREATE TRIGGER student_support_watch_events_immutable BEFORE UPDATE ON student_support_watch_events
 BEGIN SELECT RAISE(ABORT,'TEACHING-WORKFLOWS: watch receipts are immutable'); END;
CREATE TRIGGER student_support_watch_events_retained BEFORE DELETE ON student_support_watch_events
 BEGIN SELECT RAISE(ABORT,'TEACHING-WORKFLOWS: watch receipts are retained'); END;
CREATE TRIGGER student_support_watch_owner_immutable BEFORE UPDATE ON student_support_watches
 WHEN NEW.enrollment_id!=OLD.enrollment_id OR NEW.item_key!=OLD.item_key OR NEW.kind!=OLD.kind
 OR NEW.target_json!=OLD.target_json OR NEW.check_back!=OLD.check_back OR NEW.baseline!=OLD.baseline
 OR NEW.request_id!=OLD.request_id OR NEW.request_sha256!=OLD.request_sha256
 BEGIN SELECT RAISE(ABORT,'TEACHING-WORKFLOWS: watch identity is immutable'); END;
CREATE TRIGGER student_support_watches_retained BEFORE DELETE ON student_support_watches
 BEGIN SELECT RAISE(ABORT,'TEACHING-WORKFLOWS: watches are retained'); END;
