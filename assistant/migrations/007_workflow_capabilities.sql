-- ASSISTANT: workspace-owned integration metadata; teaching data retain their existing owners.
CREATE TABLE assistant_plan_continuations (
 run_id TEXT PRIMARY KEY REFERENCES assistant_preparations(id),
 instruction TEXT NOT NULL,
 after_json TEXT NOT NULL CHECK(json_valid(after_json)),
 request_id TEXT NOT NULL UNIQUE,
 request_sha256 TEXT NOT NULL,
 state TEXT NOT NULL DEFAULT 'waiting' CHECK(state IN ('waiting','advanced','stopped')),
 next_run_id TEXT REFERENCES assistant_preparations(id),
 created_at TEXT NOT NULL
);
CREATE TABLE assistant_material_requests (
 id TEXT PRIMARY KEY,
 request_id TEXT NOT NULL UNIQUE,
 fingerprint TEXT NOT NULL,
 kind TEXT NOT NULL,
 owner_level TEXT NOT NULL,
 owner_id TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('running','complete','uncertain')),
 result_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(result_json)),
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL
);
CREATE TRIGGER assistant_continuation_retained BEFORE DELETE ON assistant_plan_continuations
BEGIN SELECT RAISE(ABORT,'ASSISTANT: continuation receipts are retained'); END;
CREATE TRIGGER assistant_material_retained BEFORE DELETE ON assistant_material_requests
BEGIN SELECT RAISE(ABORT,'ASSISTANT: material receipts are retained'); END;
