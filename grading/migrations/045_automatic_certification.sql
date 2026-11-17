-- Late papers certify themselves (requirement, 2026-10-09). The certification itself needs no new column: Faculty's
-- is an ordinary frq_certifications row with actor 'automatic' (amends_id set when it covers nothing new).
-- What it asks the Gradescope sender to send: one row per late student and kind (their written paper with its grades, or papers only
-- where only papers were sent for their section; their bubble sheet), written in the certification's own transaction, so nothing is
-- lost between certifying and sending. Owned by the TERM grading plan (frq_assignments.id) and its certification; `section_id` and
-- `enrollment_id` name the student's section membership. The sender records each as an ordinary send (`send_id`: a
-- frq_gradescope_sends row for 'written', a frq_gradescope_sheet_sends row for 'sheets', actor 'automatic') whose units then follow
-- every rule of a send: settled by reading, never sent again by themselves. A row is a state machine: pending, sending (claimed),
-- then recorded (with its send) or skipped (with why); a settled row never changes; nothing is deleted.
CREATE TABLE frq_gradescope_automatic_sends (
 id TEXT PRIMARY KEY,
 certification_id TEXT NOT NULL REFERENCES frq_certifications(id),
 assignment_id TEXT NOT NULL REFERENCES frq_assignments(id),
 section_id TEXT NOT NULL REFERENCES course_sections(id),
 enrollment_id TEXT NOT NULL REFERENCES student_enrollments(id),
 kind TEXT NOT NULL CHECK(kind IN ('written','sheets')),
 papers_only INTEGER NOT NULL DEFAULT 0 CHECK(papers_only IN (0,1)),
 status TEXT NOT NULL CHECK(status IN ('pending','sending','recorded','skipped')),
 send_id TEXT,
 attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts >= 0),
 not_before TEXT,
 error TEXT NOT NULL DEFAULT '' CHECK(length(error) <= 2000),
 actor TEXT NOT NULL,
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL,
 UNIQUE(certification_id, section_id, enrollment_id, kind),
 CHECK((status = 'recorded') = (send_id IS NOT NULL)),
 CHECK(kind = 'written' OR papers_only = 0)
);
CREATE INDEX frq_gradescope_automatic_sends_plan ON frq_gradescope_automatic_sends(assignment_id, created_at);
CREATE INDEX frq_gradescope_automatic_sends_open ON frq_gradescope_automatic_sends(status, not_before) WHERE status IN ('pending','sending');
CREATE TRIGGER frq_gradescope_automatic_send_identity BEFORE UPDATE OF id,certification_id,assignment_id,section_id,enrollment_id,kind,papers_only,actor,created_at
 ON frq_gradescope_automatic_sends
BEGIN SELECT RAISE(ABORT,'An automatic Gradescope send never changes what it sends'); END;
CREATE TRIGGER frq_gradescope_automatic_send_settled BEFORE UPDATE ON frq_gradescope_automatic_sends
WHEN OLD.status IN ('recorded','skipped')
BEGIN SELECT RAISE(ABORT,'A settled automatic Gradescope send never changes'); END;
CREATE TRIGGER frq_gradescope_automatic_send_retained BEFORE DELETE ON frq_gradescope_automatic_sends
BEGIN SELECT RAISE(ABORT,'Automatic Gradescope sends are retained'); END;
