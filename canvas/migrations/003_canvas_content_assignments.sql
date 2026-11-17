-- Canvas assignments join Faculty's copy of a term's Canvas content (requirement, 2026-09-28). What Faculty knows of each Canvas course's objects now also names assignments: the table is rebuilt with the wider
-- kind (SQLite cannot change a CHECK), every row kept as it was, then its index and its append-only triggers again.
CREATE TABLE canvas_content_links_wide (
 id TEXT PRIMARY KEY,
 term_id TEXT NOT NULL REFERENCES course_terms(id),
 course_id TEXT NOT NULL CHECK(length(course_id) BETWEEN 1 AND 20 AND course_id NOT GLOB '*[^0-9]*'),
 kind TEXT NOT NULL CHECK(kind IN ('module','item','page','assignment')),
 plan_key TEXT NOT NULL,
 canvas_id TEXT NOT NULL CHECK(canvas_id = '' OR (length(canvas_id) BETWEEN 1 AND 20 AND canvas_id NOT GLOB '*[^0-9]*')),
 source TEXT NOT NULL CHECK(source IN ('pushed','matched','paired','unpaired','removed')),
 snapshot_json TEXT NOT NULL,
 push_id TEXT REFERENCES canvas_content_pushes(id),
 actor TEXT NOT NULL,
 created_at TEXT NOT NULL
);
INSERT INTO canvas_content_links_wide SELECT id,term_id,course_id,kind,plan_key,canvas_id,source,snapshot_json,push_id,actor,created_at FROM canvas_content_links;
DROP TABLE canvas_content_links;
ALTER TABLE canvas_content_links_wide RENAME TO canvas_content_links;
CREATE INDEX canvas_content_links_object ON canvas_content_links(term_id, course_id, kind, plan_key, created_at);
CREATE TRIGGER canvas_content_link_immutable BEFORE UPDATE ON canvas_content_links BEGIN SELECT RAISE(ABORT,'Canvas content links are append-only'); END;
CREATE TRIGGER canvas_content_link_retained BEFORE DELETE ON canvas_content_links BEGIN SELECT RAISE(ABORT,'Canvas content links are retained'); END;
