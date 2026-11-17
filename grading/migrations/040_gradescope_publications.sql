-- Publishing grades on Gradescope from Certification and Export (requirement, 2026-10-02). Until then Faculty never published; now only on this explicit request: the instructor
-- previews, then approves publishing the plan's linked Gradescope assignments (written grades and bubble sheets), and the sender
-- publishes each with Gradescope's own Review Grades form, reading it before and after. Owners as in 026 and 030: every approved
-- publication and its work per Gradescope assignment belong to the TERM grading plan (frq_assignments.id); the sections it covers
-- are named by number only for the panel (their links stay frq_gradescope_targets / frq_gradescope_sheet_targets). No password,
-- cookie or page is stored here: receipts keep what Gradescope showed (published, title, submissions, versions). A publication and
-- its units are state machines whose inputs never change; a settled unit never changes; nothing is deleted.
CREATE TABLE frq_gradescope_publications (
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
CREATE UNIQUE INDEX frq_gradescope_publications_live ON frq_gradescope_publications(assignment_id) WHERE status IN ('pending','running');
CREATE TRIGGER frq_gradescope_publication_identity BEFORE UPDATE OF id,assignment_id,request_id,request_sha256,fingerprint,plan_json,actor,created_at
 ON frq_gradescope_publications
BEGIN SELECT RAISE(ABORT,'An approved Gradescope publication never changes'); END;
CREATE TRIGGER frq_gradescope_publication_settled BEFORE UPDATE OF status ON frq_gradescope_publications
WHEN OLD.status='cancelled' AND NEW.status!='cancelled'
BEGIN SELECT RAISE(ABORT,'A cancelled Gradescope publication stays cancelled'); END;
CREATE TRIGGER frq_gradescope_publication_retained BEFORE DELETE ON frq_gradescope_publications BEGIN SELECT RAISE(ABORT,'Gradescope publications are retained'); END;

-- One unit per Gradescope assignment or exam version set (`target_type`), in the course of the sections it covers (`sections_json`:
-- their numbers). `part` says whether it holds the written grades or the bubble sheets. `requested_at` is set just before the publish
-- request leaves, so a later read that finds it published knows Faculty asked. `send_id` names its publication (the sender's units
-- all name their approval so).
CREATE TABLE frq_gradescope_publication_units (
 id TEXT PRIMARY KEY,
 send_id TEXT NOT NULL REFERENCES frq_gradescope_publications(id),
 assignment_id TEXT NOT NULL REFERENCES frq_assignments(id),
 part TEXT NOT NULL CHECK(part IN ('written','sheets')),
 course_id TEXT NOT NULL CHECK(length(course_id) BETWEEN 1 AND 20 AND course_id NOT GLOB '*[^0-9]*'),
 course_title TEXT NOT NULL,
 target_type TEXT NOT NULL CHECK(target_type IN ('assignment','assignment_container')),
 target_id TEXT NOT NULL CHECK(length(target_id) BETWEEN 1 AND 20 AND target_id NOT GLOB '*[^0-9]*'),
 target_title TEXT NOT NULL,
 sections_json TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('pending','sending','uncertain','accepted','present','failed','cancelled')),
 attempts INTEGER NOT NULL DEFAULT 0,
 not_before TEXT,
 requested_at TEXT,
 error TEXT NOT NULL DEFAULT '',
 receipt_json TEXT,
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL,
 UNIQUE(send_id,course_id,target_id)
);
CREATE INDEX frq_gradescope_publication_units_queue ON frq_gradescope_publication_units(status,not_before);
CREATE TRIGGER frq_gradescope_publication_unit_refused BEFORE INSERT ON frq_gradescope_publication_units
WHEN lower(trim(NEW.course_title))='math 140 for instructors'
BEGIN SELECT RAISE(ABORT,'Faculty never touches the course Math 140 for Instructors'); END;
CREATE TRIGGER frq_gradescope_publication_unit_identity BEFORE UPDATE OF id,send_id,assignment_id,part,course_id,course_title,target_type,target_id,
 target_title,sections_json,created_at ON frq_gradescope_publication_units
BEGIN SELECT RAISE(ABORT,'An approved Gradescope publication never changes what it publishes'); END;
CREATE TRIGGER frq_gradescope_publication_unit_settled BEFORE UPDATE ON frq_gradescope_publication_units
WHEN OLD.status IN ('accepted','present','cancelled')
BEGIN SELECT RAISE(ABORT,'A settled Gradescope publication never changes'); END;
CREATE TRIGGER frq_gradescope_publication_unit_retained BEFORE DELETE ON frq_gradescope_publication_units
BEGIN SELECT RAISE(ABORT,'Gradescope publications are retained'); END;
