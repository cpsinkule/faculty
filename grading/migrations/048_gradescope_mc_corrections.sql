-- MC corrections (requirement, 2026-10-22). One row per question of one student's bubble sheet that an MC check read found Gradescope scoring differently
-- from Faculty: the sender puts a point adjustment on that question's grading page so Gradescope shows Faculty's score, leaving the
-- rubric item Gradescope applied as it is, and reads it back. Owned by the TERM grading plan (frq_assignments.id), like 042, and
-- names the student by their permanent (section_id, enrollment_id) membership, like 027;
-- `read_id` is the MC check read that found it, `sheet_upload_id` the sheet Faculty uploaded (its receipt names the question
-- submissions). `gradescope_points` is what that read showed, `faculty_points` Faculty's score of the question then; `adjustment`
-- is what Faculty sent (set when it is claimed). No name, email or UIN is stored. A row is a state machine: pending, sending
-- (claimed), uncertain (no answer: settled by reading only), then accepted, failed or refused; a settled row never changes; a retry
-- is a new row (the instructor's Check MC); nothing is deleted.
CREATE TABLE frq_gradescope_mc_corrections (
 id TEXT PRIMARY KEY,
 read_id TEXT NOT NULL REFERENCES frq_gradescope_mc_checks(id),
 assignment_id TEXT NOT NULL REFERENCES frq_assignments(id),
 section_id TEXT NOT NULL,
 enrollment_id TEXT NOT NULL,
 sheet_upload_id TEXT NOT NULL REFERENCES frq_gradescope_sheet_uploads(id),
 course_id TEXT NOT NULL CHECK(length(course_id) BETWEEN 1 AND 20 AND course_id NOT GLOB '*[^0-9]*'),
 version TEXT NOT NULL CHECK(length(version) BETWEEN 1 AND 5),
 question_number INTEGER NOT NULL CHECK(question_number BETWEEN 1 AND 200),
 gradescope_points TEXT NOT NULL,
 faculty_points TEXT NOT NULL,
 adjustment TEXT,
 status TEXT NOT NULL CHECK(status IN ('pending','sending','uncertain','accepted','failed','refused')),
 attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts >= 0),
 not_before TEXT,
 receipt_json TEXT CHECK(receipt_json IS NULL OR json_valid(receipt_json)),
 error TEXT NOT NULL DEFAULT '' CHECK(length(error) <= 2000),
 actor TEXT NOT NULL,
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL,
 FOREIGN KEY(section_id,enrollment_id) REFERENCES roster_memberships(section_id,enrollment_id)
);
CREATE INDEX frq_gradescope_mc_corrections_plan ON frq_gradescope_mc_corrections(assignment_id, enrollment_id, question_number, created_at);
CREATE INDEX frq_gradescope_mc_corrections_open ON frq_gradescope_mc_corrections(status, not_before) WHERE status IN ('pending','sending','uncertain');
CREATE TRIGGER frq_gradescope_mc_correction_identity BEFORE UPDATE OF id,read_id,assignment_id,section_id,enrollment_id,sheet_upload_id,course_id,version,
 question_number,gradescope_points,faculty_points,actor,created_at ON frq_gradescope_mc_corrections
BEGIN SELECT RAISE(ABORT,'A Gradescope MC correction never changes what it corrects'); END;
CREATE TRIGGER frq_gradescope_mc_correction_settled BEFORE UPDATE ON frq_gradescope_mc_corrections
WHEN OLD.status IN ('accepted','failed','refused')
BEGIN SELECT RAISE(ABORT,'A settled Gradescope MC correction never changes'); END;
CREATE TRIGGER frq_gradescope_mc_correction_retained BEFORE DELETE ON frq_gradescope_mc_corrections
BEGIN SELECT RAISE(ABORT,'Gradescope MC corrections are retained'); END;
