-- Student notes (AGENTS.md "Student notes"): the instructor's own notes about a student, owned by the student's TERM ENROLLMENT
-- (student_enrollments.id, held to its term by a trigger), so a note stays with the student through a section change in the term.
-- Everything is append-only: a note's date and words are revisions (the highest counts), and deleting or restoring a note is a
-- revision that marks it removed or not. Catalog data only: no files, nothing sent anywhere.
CREATE TABLE student_notes (
 id TEXT PRIMARY KEY,
 term_id TEXT NOT NULL REFERENCES course_terms(id),
 enrollment_id TEXT NOT NULL REFERENCES student_enrollments(id),
 request_id TEXT NOT NULL CHECK(length(request_id) BETWEEN 1 AND 100),
 actor TEXT NOT NULL,
 created_at TEXT NOT NULL,
 UNIQUE(term_id, request_id)
);
CREATE INDEX student_notes_enrollment ON student_notes(enrollment_id);
CREATE TRIGGER student_notes_term_matches BEFORE INSERT ON student_notes
WHEN NOT EXISTS (SELECT 1 FROM student_enrollments WHERE id=NEW.enrollment_id AND term_id=NEW.term_id)
BEGIN SELECT RAISE(ABORT,'A student note belongs to the enrollment''s term'); END;
CREATE TRIGGER student_notes_immutable BEFORE UPDATE ON student_notes BEGIN SELECT RAISE(ABORT,'Student notes are append-only'); END;
CREATE TRIGGER student_notes_retained BEFORE DELETE ON student_notes BEGIN SELECT RAISE(ABORT,'Student notes are retained'); END;

CREATE TABLE student_note_revisions (
 id TEXT PRIMARY KEY,
 note_id TEXT NOT NULL REFERENCES student_notes(id),
 revision INTEGER NOT NULL CHECK(revision >= 1),
 note_date TEXT NOT NULL CHECK(note_date GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]'),
 body TEXT NOT NULL CHECK(length(body) BETWEEN 1 AND 5000),
 removed INTEGER NOT NULL DEFAULT 0 CHECK(removed IN (0, 1)),
 request_id TEXT NOT NULL CHECK(length(request_id) BETWEEN 1 AND 100),
 actor TEXT NOT NULL,
 created_at TEXT NOT NULL,
 UNIQUE(note_id, revision),
 UNIQUE(note_id, request_id)
);
CREATE TRIGGER student_note_revisions_next BEFORE INSERT ON student_note_revisions
WHEN NEW.revision != 1 + COALESCE((SELECT MAX(revision) FROM student_note_revisions WHERE note_id=NEW.note_id), 0)
BEGIN SELECT RAISE(ABORT,'A note revision must follow the latest one'); END;
CREATE TRIGGER student_note_revisions_immutable BEFORE UPDATE ON student_note_revisions BEGIN SELECT RAISE(ABORT,'Note revisions are append-only'); END;
CREATE TRIGGER student_note_revisions_retained BEFORE DELETE ON student_note_revisions BEGIN SELECT RAISE(ABORT,'Note revisions are retained'); END;
