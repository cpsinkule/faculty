-- ASSISTANT: opt-in workspace automation rules; preparation runs belong to a term
-- (or the workspace for an unassigned Inbox scan). All actions keep their owners.
CREATE TABLE assistant_workflows (
 id TEXT PRIMARY KEY, kind TEXT NOT NULL CHECK(kind IN ('readiness','reconciliation','watch')),
 target TEXT NOT NULL, term_id TEXT REFERENCES course_terms(id), title TEXT NOT NULL,
 watch_id TEXT UNIQUE REFERENCES assistant_watches(id) ON DELETE CASCADE,
 enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1)), revision INTEGER NOT NULL DEFAULT 1,
 lead_days INTEGER NOT NULL DEFAULT 7 CHECK(lead_days BETWEEN 1 AND 30),
 next_check REAL NOT NULL DEFAULT 0, last_fingerprint TEXT NOT NULL DEFAULT '', last_error TEXT NOT NULL DEFAULT '',
 request_id TEXT NOT NULL UNIQUE, request_sha256 TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX assistant_workflows_due ON assistant_workflows(enabled,next_check,id);
CREATE TABLE assistant_preparations (
 id TEXT PRIMARY KEY, workflow_id TEXT REFERENCES assistant_workflows(id), term_id TEXT REFERENCES course_terms(id),
 kind TEXT NOT NULL, target TEXT NOT NULL, title TEXT NOT NULL, source_fingerprint TEXT NOT NULL,
 request_id TEXT NOT NULL UNIQUE, request_sha256 TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE assistant_preparation_events (
 id INTEGER PRIMARY KEY, run_id TEXT NOT NULL REFERENCES assistant_preparations(id),
 state TEXT NOT NULL, detail_json TEXT NOT NULL CHECK(json_valid(detail_json)), created_at TEXT NOT NULL
);
CREATE INDEX assistant_preparation_state ON assistant_preparation_events(run_id,id);
CREATE TABLE assistant_preparation_actions (
 run_id TEXT NOT NULL REFERENCES assistant_preparations(id), action_id TEXT NOT NULL UNIQUE REFERENCES assistant_actions(id),
 PRIMARY KEY(run_id,action_id)
);
CREATE TRIGGER assistant_preparations_immutable BEFORE UPDATE ON assistant_preparations BEGIN SELECT RAISE(ABORT,'ASSISTANT: preparations are immutable'); END;
CREATE TRIGGER assistant_preparations_retained BEFORE DELETE ON assistant_preparations BEGIN SELECT RAISE(ABORT,'ASSISTANT: preparations are retained'); END;
CREATE TRIGGER assistant_preparation_events_immutable BEFORE UPDATE ON assistant_preparation_events BEGIN SELECT RAISE(ABORT,'ASSISTANT: preparation events are append-only'); END;
CREATE TRIGGER assistant_preparation_events_retained BEFORE DELETE ON assistant_preparation_events BEGIN SELECT RAISE(ABORT,'ASSISTANT: preparation events are retained'); END;
