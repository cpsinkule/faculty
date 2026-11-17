-- One section's Canvas course edited directly (requirement, 2026-09-30). A direct push is the receipt of the instructor's approval of
-- one previewed plan for that one course: the draft as approved, the plan and its progress (the only rows that change, and only
-- those columns, while it runs). It is SECTION-owned (course_sections.id, the tab it was made from) and names the Canvas course
-- that section links; it records no canvas_content_links rows, so the term's copy never takes a direct edit for its own.
CREATE TABLE canvas_section_pushes (
 id TEXT PRIMARY KEY,
 section_id TEXT NOT NULL REFERENCES course_sections(id),
 course_id TEXT NOT NULL CHECK(length(course_id) BETWEEN 1 AND 20 AND course_id NOT GLOB '*[^0-9]*'),
 request_id TEXT NOT NULL,
 request_sha256 TEXT NOT NULL,
 fingerprint TEXT NOT NULL,
 draft_json TEXT NOT NULL,
 plan_json TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('running','done','failed','cancelled','interrupted')),
 progress_json TEXT NOT NULL DEFAULT '{}',
 actor TEXT NOT NULL,
 created_at TEXT NOT NULL,
 finished_at TEXT,
 UNIQUE(section_id, request_id)
);
CREATE INDEX canvas_section_pushes_section ON canvas_section_pushes(section_id, created_at);
CREATE INDEX canvas_section_pushes_course ON canvas_section_pushes(course_id, status);
CREATE TRIGGER canvas_section_push_fixed BEFORE UPDATE ON canvas_section_pushes
 WHEN NEW.id != OLD.id OR NEW.section_id != OLD.section_id OR NEW.course_id != OLD.course_id OR NEW.request_id != OLD.request_id
  OR NEW.request_sha256 != OLD.request_sha256 OR NEW.fingerprint != OLD.fingerprint OR NEW.draft_json != OLD.draft_json
  OR NEW.plan_json != OLD.plan_json OR NEW.actor != OLD.actor OR NEW.created_at != OLD.created_at OR OLD.status != 'running'
 BEGIN SELECT RAISE(ABORT,'A direct Canvas push keeps its approval; only a running push records progress'); END;
CREATE TRIGGER canvas_section_push_retained BEFORE DELETE ON canvas_section_pushes BEGIN SELECT RAISE(ABORT,'Direct Canvas pushes are retained'); END;
