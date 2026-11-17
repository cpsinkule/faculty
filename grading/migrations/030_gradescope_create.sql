-- Creating the Gradescope exam from Faculty (requirement, 2026-09-27). Faculty
-- makes, in each section's own Gradescope course, the exam its papers and grades go to: one instructor-uploaded exam per version
-- (grouped as an exam version set when there are several), each with that version's blank exam as its template, one question per
-- Faculty question (its printed number, points and confirmed answer areas) and that question's rubric lines with the same wording
-- and points. Owners as in 026: the section's Gradescope course stays frq_gradescope_courses; every approved creation, its work
-- per Gradescope course and the receipt naming which Gradescope question and rubric item each Faculty question and line became
-- belong to the TERM grading plan (frq_assignments.id). The questions and rubric revisions named are the course-wide ones the plan
-- pins. No password, cookie or PDF is stored here: receipts keep Gradescope's own IDs. A creation and its units are state machines
-- whose inputs never change; a unit's progress (the Gradescope IDs found so far) grows as it works; receipts never change.
CREATE TABLE frq_gradescope_creations (
 id TEXT PRIMARY KEY,
 assignment_id TEXT NOT NULL REFERENCES frq_assignments(id),
 request_id TEXT NOT NULL UNIQUE,
 request_sha256 TEXT NOT NULL,
 fingerprint TEXT NOT NULL,
 title TEXT NOT NULL CHECK(length(title) BETWEEN 1 AND 200),
 plan_json TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('pending','running','complete','cancelled')),
 actor TEXT NOT NULL,
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX frq_gradescope_creations_live ON frq_gradescope_creations(assignment_id) WHERE status IN ('pending','running');
CREATE TRIGGER frq_gradescope_creation_identity BEFORE UPDATE OF id,assignment_id,request_id,request_sha256,fingerprint,title,plan_json,actor,created_at
 ON frq_gradescope_creations
BEGIN SELECT RAISE(ABORT,'An approved Gradescope creation never changes'); END;
CREATE TRIGGER frq_gradescope_creation_settled BEFORE UPDATE OF status ON frq_gradescope_creations
WHEN OLD.status='cancelled' AND NEW.status!='cancelled'
BEGIN SELECT RAISE(ABORT,'A cancelled Gradescope creation stays cancelled'); END;
CREATE TRIGGER frq_gradescope_creation_retained BEFORE DELETE ON frq_gradescope_creations BEGIN SELECT RAISE(ABORT,'Gradescope creations are retained'); END;

-- One unit per Gradescope course: the sections whose course it is (several sections may share one course) are linked to what it
-- makes. `send_id` names its creation (the sender's units all name their approval so).
CREATE TABLE frq_gradescope_creation_units (
 id TEXT PRIMARY KEY,
 send_id TEXT NOT NULL REFERENCES frq_gradescope_creations(id),
 assignment_id TEXT NOT NULL REFERENCES frq_assignments(id),
 course_id TEXT NOT NULL CHECK(length(course_id) BETWEEN 1 AND 20 AND course_id NOT GLOB '*[^0-9]*'),
 course_title TEXT NOT NULL,
 sections_json TEXT NOT NULL,
 plan_json TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('pending','sending','uncertain','complete','failed','cancelled')),
 step TEXT NOT NULL DEFAULT '',
 progress_json TEXT NOT NULL DEFAULT '{}',
 container_id TEXT CHECK(container_id IS NULL OR (length(container_id) BETWEEN 1 AND 20 AND container_id NOT GLOB '*[^0-9]*')),
 attempts INTEGER NOT NULL DEFAULT 0,
 not_before TEXT,
 error TEXT NOT NULL DEFAULT '',
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL,
 UNIQUE(send_id,course_id)
);
CREATE INDEX frq_gradescope_creation_units_queue ON frq_gradescope_creation_units(status,not_before);
CREATE TRIGGER frq_gradescope_creation_unit_refused BEFORE INSERT ON frq_gradescope_creation_units
WHEN lower(trim(NEW.course_title))='math 140 for instructors'
BEGIN SELECT RAISE(ABORT,'Faculty never touches the course Math 140 for Instructors'); END;
CREATE TRIGGER frq_gradescope_creation_unit_identity BEFORE UPDATE OF id,send_id,assignment_id,course_id,course_title,sections_json,plan_json,created_at
 ON frq_gradescope_creation_units
BEGIN SELECT RAISE(ABORT,'An approved Gradescope creation never changes what it makes'); END;
CREATE TRIGGER frq_gradescope_creation_unit_settled BEFORE UPDATE ON frq_gradescope_creation_units
WHEN OLD.status IN ('complete','cancelled')
BEGIN SELECT RAISE(ABORT,'A settled Gradescope creation never changes'); END;
CREATE TRIGGER frq_gradescope_creation_unit_retained BEFORE DELETE ON frq_gradescope_creation_units BEGIN SELECT RAISE(ABORT,'Gradescope creations are retained'); END;

-- Which Gradescope question and rubric items a Faculty question and its lines became, written once when a unit completes.
-- `items_json` is {Faculty line ID: {"id": Gradescope item ID, "description": wording sent, "weight": weight sent}}.
CREATE TABLE frq_gradescope_created_questions (
 id TEXT PRIMARY KEY,
 unit_id TEXT NOT NULL REFERENCES frq_gradescope_creation_units(id),
 assignment_id TEXT NOT NULL REFERENCES frq_assignments(id),
 question_id TEXT NOT NULL REFERENCES frq_questions(id),
 rubric_id TEXT NOT NULL REFERENCES frq_rubric_revisions(id),
 exam_version TEXT NOT NULL CHECK(exam_version='' OR (length(exam_version)=1 AND exam_version BETWEEN 'A' AND 'Z')),
 course_id TEXT NOT NULL,
 gradescope_assignment_id TEXT NOT NULL CHECK(length(gradescope_assignment_id) BETWEEN 1 AND 20 AND gradescope_assignment_id NOT GLOB '*[^0-9]*'),
 gradescope_question_id TEXT NOT NULL CHECK(length(gradescope_question_id) BETWEEN 1 AND 20 AND gradescope_question_id NOT GLOB '*[^0-9]*'),
 items_json TEXT NOT NULL,
 created_at TEXT NOT NULL,
 UNIQUE(unit_id,question_id)
);
CREATE INDEX frq_gradescope_created_questions_target ON frq_gradescope_created_questions(course_id,gradescope_assignment_id,question_id);
CREATE TRIGGER frq_gradescope_created_question_immutable BEFORE UPDATE ON frq_gradescope_created_questions
BEGIN SELECT RAISE(ABORT,'A Gradescope creation receipt never changes'); END;
CREATE TRIGGER frq_gradescope_created_question_retained BEFORE DELETE ON frq_gradescope_created_questions
BEGIN SELECT RAISE(ABORT,'Gradescope creation receipts are retained'); END;
