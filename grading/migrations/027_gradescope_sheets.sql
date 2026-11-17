-- Bubble sheets sent to Gradescope (requirement, 2026-09-25): the multiple choice is upload only, "once the student is matched in
-- faculty, it should upload according to that student's uin. the bubble sheet version controls over anything else. separate on
-- bubble sheets page". Each matched sheet's page goes up as that student to the version of the section's Gradescope bubble-sheet
-- exam that the sheet itself names; Gradescope reads and scores it with its own answer key, so nothing here is ever a grade.
-- Owners as in 026: a section's Gradescope course stays frq_gradescope_courses (the same course as the written exam's); which
-- bubble-sheet exam a term grading plan's section goes to, every approved send and the receipt of every sheet uploaded belong to
-- the TERM grading plan (frq_assignments.id), and students are permanent (section_id, enrollment_id) memberships. A unit names the
-- immutable bubble_sheets reading and the review that made its student and version, and the scan page and its checksum it sends.
-- No UIN, name, password, cookie or PDF is stored here. Links are append-only; sends and units are state machines whose inputs and
-- receipts never change, exactly as 026's.
CREATE TABLE frq_gradescope_sheet_targets (
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
CREATE INDEX frq_gradescope_sheet_targets_plan ON frq_gradescope_sheet_targets(assignment_id,section_id,created_at);
CREATE TRIGGER frq_gradescope_sheet_target_scope BEFORE INSERT ON frq_gradescope_sheet_targets
WHEN NOT EXISTS(SELECT 1 FROM frq_assignments g JOIN exam_events e ON e.id=g.event_id JOIN course_sections s ON s.term_id=e.term_id
                WHERE g.id=NEW.assignment_id AND s.id=NEW.section_id)
  OR NOT EXISTS(SELECT 1 FROM frq_gradescope_courses c WHERE c.id=NEW.course_link_id AND c.section_id=NEW.section_id)
BEGIN SELECT RAISE(ABORT,'A Gradescope bubble-sheet exam is chosen for a section of this term, in that section''s own course'); END;
CREATE TRIGGER frq_gradescope_sheet_target_immutable BEFORE UPDATE ON frq_gradescope_sheet_targets BEGIN SELECT RAISE(ABORT,'Gradescope exam links are append-only'); END;
CREATE TRIGGER frq_gradescope_sheet_target_retained BEFORE DELETE ON frq_gradescope_sheet_targets BEGIN SELECT RAISE(ABORT,'Gradescope exam links are retained'); END;

CREATE TABLE frq_gradescope_sheet_sends (
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
CREATE UNIQUE INDEX frq_gradescope_sheet_sends_live ON frq_gradescope_sheet_sends(assignment_id) WHERE status IN ('pending','running');
CREATE TRIGGER frq_gradescope_sheet_send_identity BEFORE UPDATE OF id,assignment_id,request_id,request_sha256,fingerprint,plan_json,actor,created_at ON frq_gradescope_sheet_sends
BEGIN SELECT RAISE(ABORT,'An approved Gradescope send never changes'); END;
CREATE TRIGGER frq_gradescope_sheet_send_settled BEFORE UPDATE OF status ON frq_gradescope_sheet_sends
WHEN OLD.status='cancelled' AND NEW.status!='cancelled'
BEGIN SELECT RAISE(ABORT,'A cancelled Gradescope send stays cancelled'); END;
CREATE TRIGGER frq_gradescope_sheet_send_retained BEFORE DELETE ON frq_gradescope_sheet_sends BEGIN SELECT RAISE(ABORT,'Gradescope sends are retained'); END;

CREATE TABLE frq_gradescope_sheet_uploads (
 id TEXT PRIMARY KEY,
 send_id TEXT NOT NULL REFERENCES frq_gradescope_sheet_sends(id),
 assignment_id TEXT NOT NULL REFERENCES frq_assignments(id),
 section_id TEXT NOT NULL,
 enrollment_id TEXT NOT NULL,
 sheet_id TEXT NOT NULL,
 review_id TEXT,
 scan_page_id TEXT NOT NULL,
 content_sha256 TEXT NOT NULL,
 rotation INTEGER NOT NULL CHECK(rotation IN (0,180)),
 exam_version TEXT NOT NULL CHECK(exam_version='' OR (length(exam_version)=1 AND exam_version BETWEEN 'A' AND 'Z')),
 course_id TEXT NOT NULL,
 container_id TEXT NOT NULL,
 gradescope_assignment_id TEXT NOT NULL,
 gradescope_user_id TEXT NOT NULL,
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
 UNIQUE(send_id,sheet_id)
);
CREATE INDEX frq_gradescope_sheet_uploads_queue ON frq_gradescope_sheet_uploads(status,not_before);
CREATE INDEX frq_gradescope_sheet_uploads_student ON frq_gradescope_sheet_uploads(assignment_id,section_id,enrollment_id);
-- Gradescope keeps one sheet per student per bubble-sheet exam, whatever its version.
CREATE UNIQUE INDEX frq_gradescope_sheet_uploads_once ON frq_gradescope_sheet_uploads(container_id,gradescope_user_id)
 WHERE status IN ('pending','sending','accepted','uncertain');
CREATE TRIGGER frq_gradescope_sheet_upload_identity BEFORE UPDATE OF id,send_id,assignment_id,section_id,enrollment_id,sheet_id,review_id,scan_page_id,
 content_sha256,rotation,exam_version,course_id,container_id,gradescope_assignment_id,gradescope_user_id,pages,created_at ON frq_gradescope_sheet_uploads
BEGIN SELECT RAISE(ABORT,'An approved Gradescope upload never changes what it sends'); END;
CREATE TRIGGER frq_gradescope_sheet_upload_settled BEFORE UPDATE OF status ON frq_gradescope_sheet_uploads
WHEN OLD.status IN ('accepted','present','cancelled') AND NEW.status!=OLD.status
BEGIN SELECT RAISE(ABORT,'A settled Gradescope upload never changes'); END;
CREATE TRIGGER frq_gradescope_sheet_upload_receipt BEFORE UPDATE OF receipt_json,gradescope_submission_id,pdf_sha256 ON frq_gradescope_sheet_uploads
WHEN (OLD.receipt_json IS NOT NULL AND NEW.receipt_json IS NOT OLD.receipt_json)
  OR (OLD.gradescope_submission_id IS NOT NULL AND NEW.gradescope_submission_id IS NOT OLD.gradescope_submission_id)
BEGIN SELECT RAISE(ABORT,'A Gradescope upload receipt never changes'); END;
CREATE TRIGGER frq_gradescope_sheet_upload_retained BEFORE DELETE ON frq_gradescope_sheet_uploads BEGIN SELECT RAISE(ABORT,'Gradescope uploads are retained'); END;
