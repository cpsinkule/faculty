-- Students owed a makeup are left out of a certification (requirement, 2026-10-07). A participant with no work whose excused or undecided absence
-- covers the day it was given is certified `left_out` with reason `makeup`. SQLite cannot change a CHECK, so the table is rebuilt
-- with the wider reason, every row kept as it was and in its order, then its scope and append-only triggers again.
CREATE TABLE frq_certification_students_wide (
 certification_id TEXT NOT NULL REFERENCES frq_certifications(id),
 section_id TEXT NOT NULL,
 enrollment_id TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('scored','zero','excused','left_out')),
 reason TEXT NOT NULL DEFAULT '' CHECK(reason IN ('','missing_part','no_paper','absent','excused','dr_ahead','makeup','duplicate_membership')),
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
       OR (status='excused' AND reason='excused') OR (status='left_out' AND reason IN ('dr_ahead','makeup','duplicate_membership'))),
 CHECK(reason!='dr_ahead' OR dr_date IS NOT NULL)
);
INSERT INTO frq_certification_students_wide(certification_id,section_id,enrollment_id,status,reason,earned,possible,exam_version,parts_json,dr_date)
SELECT certification_id,section_id,enrollment_id,status,reason,earned,possible,exam_version,parts_json,dr_date FROM frq_certification_students ORDER BY rowid;
DROP TABLE frq_certification_students;
ALTER TABLE frq_certification_students_wide RENAME TO frq_certification_students;
CREATE TRIGGER frq_certification_student_scope BEFORE INSERT ON frq_certification_students
WHEN NOT EXISTS(SELECT 1 FROM frq_certifications c JOIN frq_assignments a ON a.id=c.assignment_id
                JOIN exam_participants p ON p.event_id=a.event_id
                WHERE c.id=NEW.certification_id AND p.section_id=NEW.section_id AND p.enrollment_id=NEW.enrollment_id)
  OR NEW.certification_id IS NOT (SELECT n.id FROM frq_certifications n
                WHERE n.assignment_id=(SELECT c.assignment_id FROM frq_certifications c WHERE c.id=NEW.certification_id) ORDER BY n.rowid DESC LIMIT 1)
BEGIN SELECT RAISE(ABORT,'A certified grade belongs to a participant of the certified exam and is written with its certification'); END;
CREATE TRIGGER frq_certification_student_immutable BEFORE UPDATE ON frq_certification_students BEGIN SELECT RAISE(ABORT,'Certified grades are immutable'); END;
CREATE TRIGGER frq_certification_student_retained BEFORE DELETE ON frq_certification_students BEGIN SELECT RAISE(ABORT,'Certified grades are retained'); END;
