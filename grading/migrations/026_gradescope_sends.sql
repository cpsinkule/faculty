-- Sending grades to Gradescope (requirement, 2026-09-25).
-- Owners (AGENTS.md course data hierarchy): which Gradescope course a SECTION is belongs to the section (course_sections.id);
-- which Gradescope exam a term grading plan's section and version go to, every approved send, and the receipt of every paper
-- uploaded and every grade set belong to the TERM grading plan (frq_assignments.id). Students are permanent
-- (section_id, enrollment_id) memberships. No UIN, name, password, cookie or PDF is stored here: a receipt keeps Gradescope's own
-- IDs, the reviewed paper's revision and the uploaded file's sha256. Links are append-only (the latest row counts); sends and
-- units are state machines whose inputs and receipts never change. Gradescope's course "Math 140 for Instructors" is never
-- touched: code refuses it before any request; the trigger below is only a backstop.
CREATE TABLE frq_gradescope_courses (
 id TEXT PRIMARY KEY,
 section_id TEXT NOT NULL REFERENCES course_sections(id),
 course_id TEXT NOT NULL CHECK(length(course_id) BETWEEN 1 AND 20 AND course_id NOT GLOB '*[^0-9]*'),
 course_title TEXT NOT NULL,
 actor TEXT NOT NULL,
 created_at TEXT NOT NULL
);
CREATE INDEX frq_gradescope_courses_section ON frq_gradescope_courses(section_id,created_at);
CREATE TRIGGER frq_gradescope_course_refused BEFORE INSERT ON frq_gradescope_courses
WHEN lower(trim(NEW.course_title))='math 140 for instructors'
BEGIN SELECT RAISE(ABORT,'Faculty never touches the course Math 140 for Instructors'); END;
CREATE TRIGGER frq_gradescope_course_immutable BEFORE UPDATE ON frq_gradescope_courses BEGIN SELECT RAISE(ABORT,'Gradescope course links are append-only'); END;
CREATE TRIGGER frq_gradescope_course_retained BEFORE DELETE ON frq_gradescope_courses BEGIN SELECT RAISE(ABORT,'Gradescope course links are retained'); END;

CREATE TABLE frq_gradescope_targets (
 id TEXT PRIMARY KEY,
 assignment_id TEXT NOT NULL REFERENCES frq_assignments(id),
 section_id TEXT NOT NULL REFERENCES course_sections(id),
 course_link_id TEXT NOT NULL REFERENCES frq_gradescope_courses(id),
 container_id TEXT NOT NULL CHECK(length(container_id) BETWEEN 1 AND 20 AND container_id NOT GLOB '*[^0-9]*'),
 container_title TEXT NOT NULL,
 versions_json TEXT NOT NULL,
 actor TEXT NOT NULL,
 created_at TEXT NOT NULL
);
CREATE INDEX frq_gradescope_targets_plan ON frq_gradescope_targets(assignment_id,section_id,created_at);
CREATE TRIGGER frq_gradescope_target_scope BEFORE INSERT ON frq_gradescope_targets
WHEN NOT EXISTS(SELECT 1 FROM frq_assignments g JOIN exam_event_sections s ON s.event_id=g.event_id WHERE g.id=NEW.assignment_id AND s.section_id=NEW.section_id)
  OR NOT EXISTS(SELECT 1 FROM frq_gradescope_courses c WHERE c.id=NEW.course_link_id AND c.section_id=NEW.section_id)
BEGIN SELECT RAISE(ABORT,'A Gradescope exam is chosen for a section of this exam, in that section''s own course'); END;
CREATE TRIGGER frq_gradescope_target_immutable BEFORE UPDATE ON frq_gradescope_targets BEGIN SELECT RAISE(ABORT,'Gradescope exam links are append-only'); END;
CREATE TRIGGER frq_gradescope_target_retained BEFORE DELETE ON frq_gradescope_targets BEGIN SELECT RAISE(ABORT,'Gradescope exam links are retained'); END;

CREATE TABLE frq_gradescope_sends (
 id TEXT PRIMARY KEY,
 assignment_id TEXT NOT NULL REFERENCES frq_assignments(id),
 request_id TEXT NOT NULL UNIQUE,
 request_sha256 TEXT NOT NULL,
 fingerprint TEXT NOT NULL,
 plan_json TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('pending','running','complete','cancelled')),
 actor TEXT NOT NULL,
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX frq_gradescope_sends_live ON frq_gradescope_sends(assignment_id) WHERE status IN ('pending','running');
CREATE TRIGGER frq_gradescope_send_identity BEFORE UPDATE OF id,assignment_id,request_id,request_sha256,fingerprint,plan_json,actor,created_at ON frq_gradescope_sends
BEGIN SELECT RAISE(ABORT,'An approved Gradescope send never changes'); END;
CREATE TRIGGER frq_gradescope_send_settled BEFORE UPDATE OF status ON frq_gradescope_sends
WHEN OLD.status='cancelled' AND NEW.status!='cancelled'
BEGIN SELECT RAISE(ABORT,'A cancelled Gradescope send stays cancelled'); END;
CREATE TRIGGER frq_gradescope_send_retained BEFORE DELETE ON frq_gradescope_sends BEGIN SELECT RAISE(ABORT,'Gradescope sends are retained'); END;

CREATE TABLE frq_gradescope_uploads (
 id TEXT PRIMARY KEY,
 send_id TEXT NOT NULL REFERENCES frq_gradescope_sends(id),
 assignment_id TEXT NOT NULL REFERENCES frq_assignments(id),
 section_id TEXT NOT NULL,
 enrollment_id TEXT NOT NULL,
 exam_version TEXT NOT NULL CHECK(exam_version='' OR (length(exam_version)=1 AND exam_version BETWEEN 'A' AND 'Z')),
 course_id TEXT NOT NULL,
 gradescope_assignment_id TEXT NOT NULL,
 gradescope_user_id TEXT NOT NULL,
 assembly_revision_id TEXT NOT NULL,
 selection_sha256 TEXT NOT NULL,
 pages INTEGER NOT NULL CHECK(pages>=1),
 status TEXT NOT NULL CHECK(status IN ('pending','sending','accepted','failed','uncertain','present','cancelled')),
 attempts INTEGER NOT NULL DEFAULT 0,
 not_before TEXT,
 pdf_sha256 TEXT,
 gradescope_submission_id TEXT,
 receipt_json TEXT,
 error TEXT NOT NULL DEFAULT '',
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL,
 FOREIGN KEY(section_id,enrollment_id) REFERENCES roster_memberships(section_id,enrollment_id),
 UNIQUE(send_id,section_id,enrollment_id)
);
CREATE INDEX frq_gradescope_uploads_queue ON frq_gradescope_uploads(status,not_before);
CREATE INDEX frq_gradescope_uploads_student ON frq_gradescope_uploads(assignment_id,section_id,enrollment_id);
CREATE UNIQUE INDEX frq_gradescope_uploads_once ON frq_gradescope_uploads(gradescope_assignment_id,gradescope_user_id)
 WHERE status IN ('pending','sending','accepted','uncertain');
CREATE TRIGGER frq_gradescope_upload_identity BEFORE UPDATE OF id,send_id,assignment_id,section_id,enrollment_id,exam_version,course_id,gradescope_assignment_id,
 gradescope_user_id,assembly_revision_id,selection_sha256,pages,created_at ON frq_gradescope_uploads
BEGIN SELECT RAISE(ABORT,'An approved Gradescope upload never changes what it sends'); END;
CREATE TRIGGER frq_gradescope_upload_settled BEFORE UPDATE OF status ON frq_gradescope_uploads
WHEN OLD.status IN ('accepted','present','cancelled') AND NEW.status!=OLD.status
BEGIN SELECT RAISE(ABORT,'A settled Gradescope upload never changes'); END;
CREATE TRIGGER frq_gradescope_upload_receipt BEFORE UPDATE OF receipt_json,gradescope_submission_id,pdf_sha256 ON frq_gradescope_uploads
WHEN (OLD.receipt_json IS NOT NULL AND NEW.receipt_json IS NOT OLD.receipt_json)
  OR (OLD.gradescope_submission_id IS NOT NULL AND NEW.gradescope_submission_id IS NOT OLD.gradescope_submission_id)
BEGIN SELECT RAISE(ABORT,'A Gradescope upload receipt never changes'); END;
CREATE TRIGGER frq_gradescope_upload_retained BEFORE DELETE ON frq_gradescope_uploads BEGIN SELECT RAISE(ABORT,'Gradescope uploads are retained'); END;

CREATE TABLE frq_gradescope_grades (
 id TEXT PRIMARY KEY,
 send_id TEXT NOT NULL REFERENCES frq_gradescope_sends(id),
 upload_id TEXT NOT NULL REFERENCES frq_gradescope_uploads(id),
 assignment_id TEXT NOT NULL REFERENCES frq_assignments(id),
 response_id TEXT NOT NULL REFERENCES frq_responses(id),
 decision TEXT NOT NULL CHECK(decision IN ('review','automatic')),
 decision_id TEXT NOT NULL,
 result_id TEXT NOT NULL,
 rubric_id TEXT NOT NULL,
 gradescope_question_id TEXT NOT NULL,
 items_json TEXT NOT NULL,
 adjustment TEXT NOT NULL,
 expected_score TEXT NOT NULL,
 replaces_id TEXT REFERENCES frq_gradescope_grades(id),
 status TEXT NOT NULL CHECK(status IN ('pending','sending','accepted','failed','uncertain','present','refused','cancelled')),
 attempts INTEGER NOT NULL DEFAULT 0,
 not_before TEXT,
 receipt_json TEXT,
 error TEXT NOT NULL DEFAULT '',
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL,
 UNIQUE(send_id,response_id)
);
CREATE INDEX frq_gradescope_grades_queue ON frq_gradescope_grades(status,not_before);
CREATE INDEX frq_gradescope_grades_response ON frq_gradescope_grades(response_id,created_at);
CREATE INDEX frq_gradescope_grades_upload ON frq_gradescope_grades(upload_id);
CREATE UNIQUE INDEX frq_gradescope_grades_once ON frq_gradescope_grades(response_id) WHERE status IN ('pending','sending','uncertain');
CREATE TRIGGER frq_gradescope_grade_identity BEFORE UPDATE OF id,send_id,upload_id,assignment_id,response_id,decision,decision_id,result_id,rubric_id,
 gradescope_question_id,items_json,adjustment,expected_score,replaces_id,created_at ON frq_gradescope_grades
BEGIN SELECT RAISE(ABORT,'An approved Gradescope grade never changes what it sends'); END;
CREATE TRIGGER frq_gradescope_grade_settled BEFORE UPDATE OF status ON frq_gradescope_grades
WHEN OLD.status IN ('accepted','present','refused','cancelled') AND NEW.status!=OLD.status
BEGIN SELECT RAISE(ABORT,'A settled Gradescope grade never changes'); END;
CREATE TRIGGER frq_gradescope_grade_receipt BEFORE UPDATE OF receipt_json ON frq_gradescope_grades
WHEN OLD.receipt_json IS NOT NULL AND NEW.receipt_json IS NOT OLD.receipt_json
BEGIN SELECT RAISE(ABORT,'A Gradescope grade receipt never changes'); END;
CREATE TRIGGER frq_gradescope_grade_retained BEFORE DELETE ON frq_gradescope_grades BEGIN SELECT RAISE(ABORT,'Gradescope grades are retained'); END;
