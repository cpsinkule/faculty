-- Exam versions the instructor ADDS to a term's grading plan (requirement, 2026-09-25): a makeup or another version no seating chart handed out, added before its rubrics exist so its blank
-- template can be chosen and its rubrics drafted with the other versions'. Only the plan's list of versions changes; how a paper's
-- version is decided does not (unlike frq_declared_versions: the seat, the blank exams and the instructor still decide). Append-only;
-- the latest row per letter counts: added=1, or 0 when the instructor removed it while it had no questions.
CREATE TABLE frq_added_versions (
 id TEXT PRIMARY KEY, assignment_id TEXT NOT NULL REFERENCES frq_assignments(id),
 exam_version TEXT NOT NULL CHECK(length(exam_version)=1 AND exam_version BETWEEN 'A' AND 'Z'),
 added INTEGER NOT NULL CHECK(added IN (0,1)), actor TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX frq_added_versions_plan ON frq_added_versions(assignment_id,exam_version,created_at);
CREATE TRIGGER frq_added_version_immutable BEFORE UPDATE ON frq_added_versions
 BEGIN SELECT RAISE(ABORT,'Added exam versions are append-only'); END;
CREATE TRIGGER frq_added_version_retained BEFORE DELETE ON frq_added_versions
 BEGIN SELECT RAISE(ABORT,'Added exam versions are retained'); END;
