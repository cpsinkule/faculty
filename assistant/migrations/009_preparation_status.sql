-- ASSISTANT (2026-10-09 review of preparations; instructor: "Fix all of the issues and implement your suggestions").
-- Owner (HIERARCHY): the WORKSPACE, as for the rule itself. A rule's status: the normal morning wait as neutral text
-- (`waiting`, no longer kept in `last_error`), and when and how its last check ended (`last_checked_at`, `last_outcome`).
-- Teaching records keep their owners; nothing is copied from them.
ALTER TABLE assistant_workflows ADD COLUMN waiting TEXT NOT NULL DEFAULT '';
ALTER TABLE assistant_workflows ADD COLUMN last_checked_at TEXT;
ALTER TABLE assistant_workflows ADD COLUMN last_outcome TEXT NOT NULL DEFAULT '';
UPDATE assistant_workflows SET waiting=last_error, last_error='' WHERE last_error LIKE 'Waiting for %';
-- Owner: the WORKSPACE, referencing its immutable preparation run. A run fingerprinted before scheme 2 (the narrowed decision
-- basis) is re-baselined once to the owner facts current when it is next compared, never expired by the scheme change itself.
CREATE TABLE assistant_preparation_baselines (
 run_id TEXT PRIMARY KEY REFERENCES assistant_preparations(id),
 fingerprint TEXT NOT NULL,
 parts_json TEXT NOT NULL CHECK(json_valid(parts_json)),
 created_at TEXT NOT NULL
);
CREATE TRIGGER assistant_preparation_baselines_immutable BEFORE UPDATE ON assistant_preparation_baselines
BEGIN SELECT RAISE(ABORT,'ASSISTANT: preparation baselines are immutable'); END;
CREATE TRIGGER assistant_preparation_baselines_retained BEFORE DELETE ON assistant_preparation_baselines
BEGIN SELECT RAISE(ABORT,'ASSISTANT: preparation baselines are retained'); END;
