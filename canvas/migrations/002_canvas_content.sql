-- Canvas modules and pages kept in step across a term's courses (requirement, 2026-09-28). Faculty's copy is TERM-owned (course_terms.id: one syllabus for the term's sections): immutable revisions, the latest
-- counts. What Faculty knows of each Canvas course's objects (which Canvas module, item or page is which of Faculty's, and what it
-- held when Faculty last wrote or paired it) is append-only per (term, Canvas course, object); the latest row counts. A push is the
-- receipt of the instructor's approval of one previewed plan, with its progress (the only rows that change, and only those columns).
CREATE TABLE canvas_content_revisions (
 id TEXT PRIMARY KEY,
 term_id TEXT NOT NULL REFERENCES course_terms(id),
 revision INTEGER NOT NULL CHECK(revision >= 1),
 content_json TEXT NOT NULL,
 sha256 TEXT NOT NULL,
 request_id TEXT NOT NULL,
 request_sha256 TEXT NOT NULL,
 actor TEXT NOT NULL,
 created_at TEXT NOT NULL,
 UNIQUE(term_id, revision),
 UNIQUE(term_id, request_id)
);
CREATE TRIGGER canvas_content_revision_next BEFORE INSERT ON canvas_content_revisions
 WHEN NEW.revision != COALESCE((SELECT MAX(revision) FROM canvas_content_revisions WHERE term_id=NEW.term_id), 0) + 1
 BEGIN SELECT RAISE(ABORT,'A Canvas content revision follows the term''s latest one'); END;
CREATE TRIGGER canvas_content_revision_immutable BEFORE UPDATE ON canvas_content_revisions BEGIN SELECT RAISE(ABORT,'Canvas content revisions are immutable'); END;
CREATE TRIGGER canvas_content_revision_retained BEFORE DELETE ON canvas_content_revisions BEGIN SELECT RAISE(ABORT,'Canvas content revisions are retained'); END;

CREATE TABLE canvas_content_pushes (
 id TEXT PRIMARY KEY,
 term_id TEXT NOT NULL REFERENCES course_terms(id),
 revision INTEGER NOT NULL,
 request_id TEXT NOT NULL,
 request_sha256 TEXT NOT NULL,
 fingerprint TEXT NOT NULL,
 plan_json TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('running','done','failed','cancelled','interrupted')),
 progress_json TEXT NOT NULL DEFAULT '{}',
 actor TEXT NOT NULL,
 created_at TEXT NOT NULL,
 finished_at TEXT,
 UNIQUE(term_id, request_id)
);
CREATE INDEX canvas_content_pushes_term ON canvas_content_pushes(term_id, created_at);
CREATE TRIGGER canvas_content_push_fixed BEFORE UPDATE ON canvas_content_pushes
 WHEN NEW.id != OLD.id OR NEW.term_id != OLD.term_id OR NEW.revision != OLD.revision OR NEW.request_id != OLD.request_id
  OR NEW.request_sha256 != OLD.request_sha256 OR NEW.fingerprint != OLD.fingerprint OR NEW.plan_json != OLD.plan_json
  OR NEW.actor != OLD.actor OR NEW.created_at != OLD.created_at OR OLD.status != 'running'
 BEGIN SELECT RAISE(ABORT,'A Canvas push keeps its approval; only a running push records progress'); END;
CREATE TRIGGER canvas_content_push_retained BEFORE DELETE ON canvas_content_pushes BEGIN SELECT RAISE(ABORT,'Canvas pushes are retained'); END;

CREATE TABLE canvas_content_links (
 id TEXT PRIMARY KEY,
 term_id TEXT NOT NULL REFERENCES course_terms(id),
 course_id TEXT NOT NULL CHECK(length(course_id) BETWEEN 1 AND 20 AND course_id NOT GLOB '*[^0-9]*'),
 kind TEXT NOT NULL CHECK(kind IN ('module','item','page')),
 plan_key TEXT NOT NULL,
 canvas_id TEXT NOT NULL CHECK(canvas_id = '' OR (length(canvas_id) BETWEEN 1 AND 20 AND canvas_id NOT GLOB '*[^0-9]*')),
 source TEXT NOT NULL CHECK(source IN ('pushed','matched','paired','unpaired','removed')),
 snapshot_json TEXT NOT NULL,
 push_id TEXT REFERENCES canvas_content_pushes(id),
 actor TEXT NOT NULL,
 created_at TEXT NOT NULL
);
CREATE INDEX canvas_content_links_object ON canvas_content_links(term_id, course_id, kind, plan_key, created_at);
CREATE TRIGGER canvas_content_link_immutable BEFORE UPDATE ON canvas_content_links BEGIN SELECT RAISE(ABORT,'Canvas content links are append-only'); END;
CREATE TRIGGER canvas_content_link_retained BEFORE DELETE ON canvas_content_links BEGIN SELECT RAISE(ABORT,'Canvas content links are retained'); END;
