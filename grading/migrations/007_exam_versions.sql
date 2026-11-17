-- One printed label sheet can stand in front of several exam versions (A, B, ...).
-- The exam version is part of a course-wide question's identity, beside its printed
-- document version: the same printed number exists once per exam version, each with
-- its own blank exam, locations, answer key and rubric. '' is an exam without versions.
ALTER TABLE frq_questions ADD COLUMN exam_version TEXT NOT NULL DEFAULT ''
 CHECK(exam_version='' OR (length(exam_version)=1 AND exam_version BETWEEN 'A' AND 'Z'));
-- Questions imported before versions existed may be told ONCE which exam version they
-- describe. Nothing else about a question's identity ever changes.
DROP TRIGGER frq_question_immutable;
CREATE TRIGGER frq_question_immutable BEFORE UPDATE ON frq_questions
 WHEN NEW.id!=OLD.id OR NEW.assignment_version_id!=OLD.assignment_version_id OR NEW.question_key!=OLD.question_key
 OR NEW.created_at!=OLD.created_at OR OLD.exam_version!=''
 BEGIN SELECT RAISE(ABORT,'Question identity is immutable'); END;
-- A paper's exam version comes from its seat. The comparison of the whole reviewed paper
-- with each version's blank exam confirms it; this is its term-owned, retained receipt.
-- It references the existing reviewed assembly and permanent membership and decides nothing.
CREATE TABLE frq_version_checks (
 id TEXT PRIMARY KEY, assignment_id TEXT NOT NULL REFERENCES frq_assignments(id),
 section_id TEXT NOT NULL, enrollment_id TEXT NOT NULL, assembly_revision_id TEXT NOT NULL,
 input_sha256 TEXT NOT NULL, input_json TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('pending','running','complete','failed')),
 attempt INTEGER NOT NULL DEFAULT 0, result_json TEXT, error TEXT NOT NULL DEFAULT '',
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 FOREIGN KEY(section_id,enrollment_id) REFERENCES roster_memberships(section_id,enrollment_id),
 UNIQUE(assignment_id,section_id,enrollment_id,input_sha256)
);
CREATE INDEX frq_version_checks_queue ON frq_version_checks(status,updated_at);
CREATE TRIGGER frq_version_check_inputs BEFORE UPDATE OF assignment_id,section_id,enrollment_id,assembly_revision_id,input_sha256,input_json ON frq_version_checks
 BEGIN SELECT RAISE(ABORT,'Exam version check inputs are immutable'); END;
CREATE TRIGGER frq_version_check_retained BEFORE DELETE ON frq_version_checks
 BEGIN SELECT RAISE(ABORT,'Exam version checks are retained'); END;
-- The instructor's own decision for a paper whose seat and pages disagree, or that has
-- no seat version. Append-only; the latest one for the paper's current assembly counts.
CREATE TABLE frq_version_reviews (
 id TEXT PRIMARY KEY, assignment_id TEXT NOT NULL REFERENCES frq_assignments(id),
 section_id TEXT NOT NULL, enrollment_id TEXT NOT NULL, assembly_revision_id TEXT NOT NULL,
 exam_version TEXT NOT NULL CHECK(length(exam_version)=1 AND exam_version BETWEEN 'A' AND 'Z'),
 check_id TEXT REFERENCES frq_version_checks(id), detail_json TEXT NOT NULL,
 actor TEXT NOT NULL, created_at TEXT NOT NULL,
 FOREIGN KEY(section_id,enrollment_id) REFERENCES roster_memberships(section_id,enrollment_id)
);
CREATE INDEX frq_version_reviews_paper ON frq_version_reviews(assignment_id,section_id,enrollment_id);
CREATE TRIGGER frq_version_review_immutable BEFORE UPDATE ON frq_version_reviews
 BEGIN SELECT RAISE(ABORT,'Exam version decisions are immutable'); END;
CREATE TRIGGER frq_version_review_retained BEFORE DELETE ON frq_version_reviews
 BEGIN SELECT RAISE(ABORT,'Exam version decisions are retained'); END;
