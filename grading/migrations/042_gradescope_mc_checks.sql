-- The MC check (requirement, 2026-10-06): after a written-grades send completes, and whenever the instructor asks, the sender
-- READS each section's bubble-sheet exam grades on Gradescope (its Download Grades file) and keeps what Gradescope showed, so every
-- page compares it with Faculty's own scoring of the same sheets without contacting Gradescope. Nothing is written to Gradescope.
-- Owners as in 026, 027 and 040: every read belongs to the TERM grading plan (frq_assignments.id); `section_id` names the section
-- whose Gradescope course was read (its link stays frq_gradescope_sheet_targets). One check (`check_id`) is one read per section;
-- `send_id` is the written send whose completion asked for it (NULL: the instructor's Check MC). `scores_json` keeps Gradescope's
-- scores per Faculty enrollment of the term (SID = UIN exactly; no name, email or UIN is stored) and how many rows matched nobody.
-- A read is a state machine: pending, sending (claimed), then read or failed; a settled read never changes; nothing is deleted.
CREATE TABLE frq_gradescope_mc_checks (
 id TEXT PRIMARY KEY,
 check_id TEXT NOT NULL,
 send_id TEXT REFERENCES frq_gradescope_sends(id),
 assignment_id TEXT NOT NULL REFERENCES frq_assignments(id),
 section_id TEXT NOT NULL REFERENCES course_sections(id),
 course_id TEXT NOT NULL CHECK(length(course_id) BETWEEN 1 AND 20 AND course_id NOT GLOB '*[^0-9]*'),
 container_id TEXT NOT NULL CHECK(length(container_id) BETWEEN 1 AND 20 AND container_id NOT GLOB '*[^0-9]*'),
 status TEXT NOT NULL CHECK(status IN ('pending','sending','read','failed')),
 attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts >= 0),
 not_before TEXT,
 scores_json TEXT CHECK(scores_json IS NULL OR json_valid(scores_json)),
 error TEXT NOT NULL DEFAULT '',
 actor TEXT NOT NULL,
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL,
 CHECK((status = 'read') = (scores_json IS NOT NULL))
);
CREATE INDEX frq_gradescope_mc_checks_plan ON frq_gradescope_mc_checks(assignment_id, section_id, created_at);
CREATE INDEX frq_gradescope_mc_checks_open ON frq_gradescope_mc_checks(status, not_before) WHERE status IN ('pending','sending');
CREATE TRIGGER frq_gradescope_mc_check_identity BEFORE UPDATE OF id,check_id,send_id,assignment_id,section_id,course_id,container_id,actor,created_at
 ON frq_gradescope_mc_checks
BEGIN SELECT RAISE(ABORT,'A Gradescope MC check never changes what it reads'); END;
CREATE TRIGGER frq_gradescope_mc_check_settled BEFORE UPDATE ON frq_gradescope_mc_checks
WHEN OLD.status IN ('read','failed')
BEGIN SELECT RAISE(ABORT,'A settled Gradescope MC check never changes'); END;
CREATE TRIGGER frq_gradescope_mc_check_retained BEFORE DELETE ON frq_gradescope_mc_checks BEGIN SELECT RAISE(ABORT,'Gradescope MC checks are retained'); END;
