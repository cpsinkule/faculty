-- DR quiz takers (ACCOMMODATIONS "DR QUIZ TAKERS"), owned by the student's TERM ENROLLMENT (student_enrollments.id, held to its term
-- by a trigger): one flag that follows them through a section change. Append-only changes; the highest revision counts, none is off.
CREATE TABLE dr_quiz_taker_changes (
 id TEXT PRIMARY KEY, term_id TEXT NOT NULL REFERENCES course_terms(id), enrollment_id TEXT NOT NULL REFERENCES student_enrollments(id),
 revision INTEGER NOT NULL CHECK(revision >= 1), taker INTEGER NOT NULL CHECK(taker IN (0, 1)),
 request_id TEXT NOT NULL CHECK(length(request_id) BETWEEN 1 AND 100), actor TEXT NOT NULL, created_at TEXT NOT NULL,
 UNIQUE(enrollment_id, revision), UNIQUE(enrollment_id, request_id)
);
CREATE TRIGGER dr_quiz_taker_changes_term_matches BEFORE INSERT ON dr_quiz_taker_changes
WHEN NOT EXISTS (SELECT 1 FROM student_enrollments WHERE id=NEW.enrollment_id AND term_id=NEW.term_id)
BEGIN SELECT RAISE(ABORT,'A DR quiz taker change belongs to the enrollment''s term'); END;
CREATE TRIGGER dr_quiz_taker_changes_next BEFORE INSERT ON dr_quiz_taker_changes
WHEN NEW.revision != 1 + COALESCE((SELECT MAX(revision) FROM dr_quiz_taker_changes WHERE enrollment_id=NEW.enrollment_id), 0)
BEGIN SELECT RAISE(ABORT,'A DR quiz taker change must follow the latest one'); END;
CREATE TRIGGER dr_quiz_taker_changes_immutable BEFORE UPDATE ON dr_quiz_taker_changes BEGIN SELECT RAISE(ABORT,'DR quiz taker changes are append-only'); END;
CREATE TRIGGER dr_quiz_taker_changes_retained BEFORE DELETE ON dr_quiz_taker_changes BEGIN SELECT RAISE(ABORT,'DR quiz taker changes are retained'); END;
