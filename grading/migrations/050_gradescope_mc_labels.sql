-- MC LABELS (requirement, 2026-10-28). Gradescope names each bubble-sheet rubric item by the letters filled ("A", "A, C", "Blank"); the sender
-- renames them so each says whether it is right and what the answer is ("A: incorrect (correct answer: B)"), from Faculty's key.
-- One row per section of one Label run: that section's linked bubble-sheet exam (`frq_gradescope_sheet_targets`) in its course, its
-- versions as they were linked. Owned by the TERM grading plan (frq_assignments.id), like 042 and 048; no student is named.
-- `receipt_json` holds every item renamed ({version, number, item, before, after}) and the counts. A row is a state machine:
-- pending, sending (claimed), uncertain (no answer to a rename: settled by reading only), then accepted, failed or refused; a
-- settled row never changes; labelling again is a new run; nothing is deleted.
CREATE TABLE frq_gradescope_mc_labels (
 id TEXT PRIMARY KEY,
 run_id TEXT NOT NULL,
 assignment_id TEXT NOT NULL REFERENCES frq_assignments(id),
 section_id TEXT NOT NULL REFERENCES course_sections(id),
 course_id TEXT NOT NULL CHECK(length(course_id) BETWEEN 1 AND 20 AND course_id NOT GLOB '*[^0-9]*'),
 container_id TEXT NOT NULL CHECK(length(container_id) BETWEEN 1 AND 20 AND container_id NOT GLOB '*[^0-9]*'),
 versions_json TEXT NOT NULL CHECK(json_valid(versions_json)),
 status TEXT NOT NULL CHECK(status IN ('pending','sending','uncertain','accepted','failed','refused')),
 attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts >= 0),
 not_before TEXT,
 receipt_json TEXT CHECK(receipt_json IS NULL OR json_valid(receipt_json)),
 error TEXT NOT NULL DEFAULT '' CHECK(length(error) <= 2000),
 actor TEXT NOT NULL,
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL
);
CREATE INDEX frq_gradescope_mc_labels_plan ON frq_gradescope_mc_labels(assignment_id, created_at);
CREATE INDEX frq_gradescope_mc_labels_open ON frq_gradescope_mc_labels(status, not_before) WHERE status IN ('pending','sending','uncertain');
CREATE TRIGGER frq_gradescope_mc_label_identity BEFORE UPDATE OF id,run_id,assignment_id,section_id,course_id,container_id,versions_json,actor,created_at
 ON frq_gradescope_mc_labels
BEGIN SELECT RAISE(ABORT,'A Gradescope MC label run never changes what it labels'); END;
CREATE TRIGGER frq_gradescope_mc_label_settled BEFORE UPDATE ON frq_gradescope_mc_labels
WHEN OLD.status IN ('accepted','failed','refused')
BEGIN SELECT RAISE(ABORT,'A settled Gradescope MC label run never changes'); END;
CREATE TRIGGER frq_gradescope_mc_label_retained BEFORE DELETE ON frq_gradescope_mc_labels
BEGIN SELECT RAISE(ABORT,'Gradescope MC label runs are retained'); END;
