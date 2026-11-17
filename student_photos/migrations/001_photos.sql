-- Student photos (requirement, 2026-09-29). Owner: the student's TERM ENROLLMENT
-- (`student_enrollments.id`, one person in one course term); a photo is shown for that student wherever their section membership is
-- shown, and is never copied into a section, gradebook or mail record. Photos are education records (TAMU SAP 29.01.03.M02.02): catalog
-- rows only, never in the generic workspace export, never in any JSON, served only by the private image route.
--
-- `student_photo_imports` is the receipt of one import of a photo roster PDF into one section: the file's checksum and name, what
-- matched by name and what did not. `student_photos` holds one immutable revision per photo (the latest of an enrollment counts;
-- a changed photo is a new revision, an identical one records nothing). Both are append-only and retained.
CREATE TABLE student_photo_imports (
 id TEXT PRIMARY KEY,
 term_id TEXT NOT NULL REFERENCES course_terms(id),
 section_id TEXT NOT NULL REFERENCES course_sections(id),
 request_id TEXT NOT NULL UNIQUE CHECK(length(request_id) BETWEEN 1 AND 100),
 request_hash TEXT NOT NULL CHECK(length(request_hash) = 64),
 source_name TEXT NOT NULL,
 source_sha256 TEXT NOT NULL CHECK(length(source_sha256) = 64),
 summary_json TEXT NOT NULL CHECK(json_valid(summary_json) AND json_type(summary_json)='object'),
 actor TEXT NOT NULL,
 created_at TEXT NOT NULL
);
CREATE TABLE student_photos (
 id TEXT PRIMARY KEY,
 enrollment_id TEXT NOT NULL REFERENCES student_enrollments(id),
 term_id TEXT NOT NULL REFERENCES course_terms(id),
 revision INTEGER NOT NULL CHECK(revision >= 1),
 import_id TEXT NOT NULL REFERENCES student_photo_imports(id),
 sha256 TEXT NOT NULL CHECK(length(sha256) = 64),
 content_type TEXT NOT NULL CHECK(content_type = 'image/jpeg'),
 width INTEGER NOT NULL CHECK(width BETWEEN 32 AND 4096),
 height INTEGER NOT NULL CHECK(height BETWEEN 32 AND 4096),
 image BLOB NOT NULL CHECK(length(image) BETWEEN 1 AND 2097152),
 created_at TEXT NOT NULL,
 UNIQUE(enrollment_id, revision)
);
CREATE INDEX student_photos_term ON student_photos(term_id, enrollment_id, revision);
CREATE TRIGGER student_photos_next BEFORE INSERT ON student_photos
WHEN NEW.revision != 1 + COALESCE((SELECT MAX(revision) FROM student_photos WHERE enrollment_id=NEW.enrollment_id), 0)
BEGIN SELECT RAISE(ABORT,'A photo revision must follow the latest one'); END;
CREATE TRIGGER student_photos_term_matches BEFORE INSERT ON student_photos
WHEN NEW.term_id != (SELECT term_id FROM student_enrollments WHERE id=NEW.enrollment_id)
BEGIN SELECT RAISE(ABORT,'A photo belongs to the term of its enrollment'); END;
CREATE TRIGGER student_photos_immutable BEFORE UPDATE ON student_photos BEGIN SELECT RAISE(ABORT,'Student photos are append-only'); END;
CREATE TRIGGER student_photos_retained BEFORE DELETE ON student_photos BEGIN SELECT RAISE(ABORT,'Student photos are retained'); END;
CREATE TRIGGER student_photo_imports_immutable BEFORE UPDATE ON student_photo_imports BEGIN SELECT RAISE(ABORT,'Photo imports are append-only'); END;
CREATE TRIGGER student_photo_imports_retained BEFORE DELETE ON student_photo_imports BEGIN SELECT RAISE(ABORT,'Photo imports are retained'); END;
