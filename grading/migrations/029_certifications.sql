-- Certification of a term grading plan's grades (requirement, 2026-09-26).
-- Owner: the TERM grading plan (frq_assignments.id, one per exam_events row = term + course-wide assignment). One immutable row per
-- certification; the plan's latest counts and a newer one supersedes exactly that one (nothing is ever uncertified). Each
-- `frq_certification_students` row is the certified grade of one participant of the plan's exam, a permanent
-- (section_id, enrollment_id) roster membership: scored (every certified part complete, a part with no work at all counting 0 only
-- where the instructor confirmed it), zero (no work in any part, confirmed by the instructor), excused (as the Exams page records),
-- or left out (a DR exam still ahead, or the empty one of two memberships, confirmed). `parts_json` references each question's final
-- decision and the bubble sheet by ID; it never copies a grade's evidence. The markers say what the certified grades were read
-- from (response grade CONTENT, the bubble sheets and key, the participants and their papers), so a later change reads as
-- "changed since certification". The checks as they were, the instructor's confirmations (zeros, DR students left out, Gradescope
-- items not sent and why) and the gradebook receipt (written in the same transaction) are kept as confirmed. No UIN or name is
-- stored. Nothing here decides, finalizes, publishes or sends a grade: grades stay the reviews' and acceptances' own.
CREATE TABLE frq_certifications (
 id TEXT PRIMARY KEY,
 assignment_id TEXT NOT NULL REFERENCES frq_assignments(id),
 request_id TEXT NOT NULL,
 request_sha256 TEXT NOT NULL,
 fingerprint TEXT NOT NULL,
 rule TEXT NOT NULL,
 parts_json TEXT NOT NULL,
 decisions_sha256 TEXT NOT NULL,
 bubble_sha256 TEXT NOT NULL,
 participants_sha256 TEXT NOT NULL,
 snapshot_sha256 TEXT NOT NULL,
 summary_json TEXT NOT NULL,
 checks_json TEXT NOT NULL,
 confirmations_json TEXT NOT NULL,
 gradescope_json TEXT NOT NULL,
 gradebook_json TEXT NOT NULL,
 supersedes_id TEXT REFERENCES frq_certifications(id),
 actor TEXT NOT NULL,
 created_at TEXT NOT NULL,
 UNIQUE(assignment_id,request_id)
);
CREATE INDEX frq_certifications_plan ON frq_certifications(assignment_id,created_at);
CREATE TRIGGER frq_certification_supersedes BEFORE INSERT ON frq_certifications
WHEN NEW.supersedes_id IS NOT (SELECT c.id FROM frq_certifications c WHERE c.assignment_id=NEW.assignment_id ORDER BY c.rowid DESC LIMIT 1)
BEGIN SELECT RAISE(ABORT,'A certification supersedes exactly the plan''s latest certification'); END;
CREATE TRIGGER frq_certification_immutable BEFORE UPDATE ON frq_certifications BEGIN SELECT RAISE(ABORT,'Certifications are immutable'); END;
CREATE TRIGGER frq_certification_retained BEFORE DELETE ON frq_certifications BEGIN SELECT RAISE(ABORT,'Certifications are retained'); END;

CREATE TABLE frq_certification_students (
 certification_id TEXT NOT NULL REFERENCES frq_certifications(id),
 section_id TEXT NOT NULL,
 enrollment_id TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('scored','zero','excused','left_out')),
 reason TEXT NOT NULL DEFAULT '' CHECK(reason IN ('','missing_part','no_paper','absent','excused','dr_ahead','duplicate_membership')),
 earned TEXT,
 possible TEXT,
 exam_version TEXT NOT NULL DEFAULT '' CHECK(exam_version='' OR (length(exam_version)=1 AND exam_version BETWEEN 'A' AND 'Z')),
 parts_json TEXT NOT NULL,
 dr_date TEXT CHECK(dr_date IS NULL OR dr_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
 PRIMARY KEY(certification_id,section_id,enrollment_id),
 FOREIGN KEY(section_id,enrollment_id) REFERENCES roster_memberships(section_id,enrollment_id),
 CHECK((status IN ('scored','zero')) = (earned IS NOT NULL AND possible IS NOT NULL)),
 CHECK(status!='zero' OR earned='0'),
 CHECK((status='scored' AND reason IN ('','missing_part')) OR (status='zero' AND reason IN ('no_paper','absent'))
       OR (status='excused' AND reason='excused') OR (status='left_out' AND reason IN ('dr_ahead','duplicate_membership'))),
 CHECK(reason!='dr_ahead' OR dr_date IS NOT NULL)
);
CREATE TRIGGER frq_certification_student_scope BEFORE INSERT ON frq_certification_students
WHEN NOT EXISTS(SELECT 1 FROM frq_certifications c JOIN frq_assignments a ON a.id=c.assignment_id
                JOIN exam_participants p ON p.event_id=a.event_id
                WHERE c.id=NEW.certification_id AND p.section_id=NEW.section_id AND p.enrollment_id=NEW.enrollment_id)
  OR NEW.certification_id IS NOT (SELECT n.id FROM frq_certifications n
                WHERE n.assignment_id=(SELECT c.assignment_id FROM frq_certifications c WHERE c.id=NEW.certification_id) ORDER BY n.rowid DESC LIMIT 1)
BEGIN SELECT RAISE(ABORT,'A certified grade belongs to a participant of the certified exam and is written with its certification'); END;
CREATE TRIGGER frq_certification_student_immutable BEFORE UPDATE ON frq_certification_students BEGIN SELECT RAISE(ABORT,'Certified grades are immutable'); END;
CREATE TRIGGER frq_certification_student_retained BEFORE DELETE ON frq_certification_students BEGIN SELECT RAISE(ABORT,'Certified grades are retained'); END;
