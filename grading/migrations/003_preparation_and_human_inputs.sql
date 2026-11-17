-- Input snapshots for selective recomputation remain immutable.
ALTER TABLE frq_tasks ADD COLUMN human_review_id TEXT REFERENCES frq_reviews(id);
CREATE TRIGGER frq_task_human_input BEFORE UPDATE OF human_review_id ON frq_tasks
 BEGIN SELECT RAISE(ABORT,'Human input snapshot is immutable'); END;
-- Preparation is term workflow metadata referencing course-owned rubric originals.
-- It never changes a rubric pin or grants approval automatically.
CREATE TABLE frq_preparations (
 id TEXT PRIMARY KEY, assignment_id TEXT NOT NULL REFERENCES frq_assignments(id),
 kind TEXT NOT NULL CHECK(kind IN ('source','compile')), request_id TEXT NOT NULL UNIQUE,
 request_sha256 TEXT NOT NULL, payload_json TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('pending','running','complete','failed')),
 attempt INTEGER NOT NULL DEFAULT 0, result_json TEXT, error TEXT NOT NULL DEFAULT '',
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE frq_preparation_parts (
 id INTEGER PRIMARY KEY, preparation_id TEXT NOT NULL REFERENCES frq_preparations(id),
 part INTEGER NOT NULL, result_json TEXT NOT NULL, provider_json TEXT NOT NULL, created_at TEXT NOT NULL,
 UNIQUE(preparation_id,part)
);
CREATE TRIGGER frq_preparation_part_immutable BEFORE UPDATE ON frq_preparation_parts BEGIN SELECT RAISE(ABORT,'Preparation results are immutable'); END;
CREATE TRIGGER frq_preparation_part_retained BEFORE DELETE ON frq_preparation_parts BEGIN SELECT RAISE(ABORT,'Preparation results are retained'); END;
