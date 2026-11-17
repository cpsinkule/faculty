-- ASSISTANT-RETENTION: optional workspace conversations, preferences and read-only watches.
-- These private catalog records participate in the existing nightly catalog backup.
CREATE TABLE assistant_saved_conversations (
 id TEXT PRIMARY KEY, session_id TEXT NOT NULL, title TEXT NOT NULL,
 goal TEXT NOT NULL DEFAULT '', state TEXT NOT NULL DEFAULT 'active' CHECK(state IN ('active','paused','completed')),
 snapshot_json TEXT NOT NULL CHECK(json_valid(snapshot_json)),
 retention_days INTEGER NOT NULL CHECK(retention_days IN (7,30,90,365)),
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL, expires_at TEXT NOT NULL
);
CREATE INDEX assistant_saved_session ON assistant_saved_conversations(session_id);
CREATE TABLE assistant_memories (
 id TEXT PRIMARY KEY, title TEXT NOT NULL, content TEXT NOT NULL, revision INTEGER NOT NULL,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE assistant_watches (
 id TEXT PRIMARY KEY, request_id TEXT NOT NULL UNIQUE, request_sha256 TEXT NOT NULL,
 title TEXT NOT NULL, kind TEXT NOT NULL CHECK(kind IN ('scan','grading','exam')),
 target TEXT NOT NULL, condition TEXT NOT NULL CHECK(condition IN ('changed','finished','needs_attention','ready')),
 enabled INTEGER NOT NULL DEFAULT 1, revision INTEGER NOT NULL DEFAULT 1,
 next_check REAL NOT NULL DEFAULT 0, last_json TEXT, last_error TEXT NOT NULL DEFAULT '',
 checked_at TEXT, created_at TEXT NOT NULL
);
CREATE INDEX assistant_watches_due ON assistant_watches(enabled,next_check);
CREATE TABLE assistant_notifications (
 id INTEGER PRIMARY KEY AUTOINCREMENT, watch_id TEXT REFERENCES assistant_watches(id) ON DELETE CASCADE,
 title TEXT NOT NULL, message TEXT NOT NULL, page TEXT NOT NULL, created_at TEXT NOT NULL, read_at TEXT
);
CREATE TABLE assistant_action_batches (
 id TEXT PRIMARY KEY, session_id TEXT NOT NULL, title TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE assistant_batch_members (
 batch_id TEXT NOT NULL REFERENCES assistant_action_batches(id),
 action_id TEXT NOT NULL UNIQUE REFERENCES assistant_actions(id), position INTEGER NOT NULL,
 PRIMARY KEY(batch_id,position)
);
-- ASSISTANT-ARTIFACTS: a file belongs to exactly one retained course/term/section owner.
CREATE TABLE assistant_artifacts (
 id TEXT PRIMARY KEY, request_id TEXT NOT NULL UNIQUE, request_sha256 TEXT NOT NULL,
 definition_id TEXT REFERENCES course_definitions(id), term_id TEXT REFERENCES course_terms(id),
 section_id TEXT REFERENCES course_sections(id),
 title TEXT NOT NULL, filename TEXT NOT NULL, media_type TEXT NOT NULL,
 path TEXT NOT NULL, sha256 TEXT NOT NULL, size INTEGER NOT NULL, created_at TEXT NOT NULL,
 CHECK((definition_id IS NOT NULL)+(term_id IS NOT NULL)+(section_id IS NOT NULL)=1)
);
CREATE TRIGGER assistant_artifacts_immutable BEFORE UPDATE ON assistant_artifacts
 BEGIN SELECT RAISE(ABORT,'Teaching artifacts are immutable; create a new version'); END;
CREATE TRIGGER assistant_artifacts_retained BEFORE DELETE ON assistant_artifacts
 BEGIN SELECT RAISE(ABORT,'Teaching artifacts are retained'); END;
