-- Lecture notes on the course page (requirement, 2026-09-30). Owner: the COURSE DEFINITION
-- (`course_definitions.id`): one set of notes serves every term and section of the course code. Files live in the definition's
-- verified storage under `notes/<notes id>/` (the uploaded PDF and one PDF per part); these rows are the catalog's record of them.
--
-- `course_notes` is one uploaded PDF as it was split (an edition): its checksum, where its original is kept and the rule that split
-- it. The same file split by the same rule is one edition, uploaded again or not. `course_notes_parts` are its parts in page order
-- (front matter, then each numbered section), each with the pages it covers, how it was found and its own retained PDF.
-- `course_notes_events` records every upload, removal and restoration: an edition stands from its latest upload, a removal hides
-- it until it is restored, and the course page shows the newest edition that stands. All three are append-only and retained.
CREATE TABLE course_notes (
 id TEXT PRIMARY KEY,
 definition_id TEXT NOT NULL REFERENCES course_definitions(id),
 source_name TEXT NOT NULL CHECK(length(source_name) BETWEEN 1 AND 200),
 source_sha256 TEXT NOT NULL CHECK(length(source_sha256) = 64),
 source_size INTEGER NOT NULL CHECK(source_size > 0),
 path TEXT NOT NULL,
 page_count INTEGER NOT NULL CHECK(page_count >= 1),
 title TEXT NOT NULL,
 rule TEXT NOT NULL,
 summary_json TEXT NOT NULL CHECK(json_valid(summary_json) AND json_type(summary_json)='object'),
 actor TEXT NOT NULL,
 created_at TEXT NOT NULL,
 UNIQUE(definition_id, source_sha256, rule)
);
CREATE TABLE course_notes_parts (
 notes_id TEXT NOT NULL REFERENCES course_notes(id),
 position INTEGER NOT NULL CHECK(position >= 1),
 kind TEXT NOT NULL CHECK(kind IN ('front', 'section')),
 number TEXT NOT NULL DEFAULT '',
 chapter TEXT NOT NULL DEFAULT '',
 chapter_title TEXT NOT NULL DEFAULT '',
 title TEXT NOT NULL,
 first_page INTEGER NOT NULL CHECK(first_page >= 1),
 last_page INTEGER NOT NULL CHECK(last_page >= first_page),
 found_by TEXT NOT NULL CHECK(found_by IN ('front', 'bookmark', 'heading', 'both')),
 topics_json TEXT NOT NULL CHECK(json_valid(topics_json) AND json_type(topics_json)='array'),
 path TEXT NOT NULL,
 sha256 TEXT NOT NULL CHECK(length(sha256) = 64),
 size INTEGER NOT NULL CHECK(size > 0),
 PRIMARY KEY(notes_id, position)
);
CREATE TABLE course_notes_events (
 id TEXT PRIMARY KEY,
 notes_id TEXT NOT NULL REFERENCES course_notes(id),
 action TEXT NOT NULL CHECK(action IN ('uploaded', 'removed', 'restored')),
 request_id TEXT NOT NULL UNIQUE CHECK(length(request_id) BETWEEN 1 AND 100),
 actor TEXT NOT NULL,
 created_at TEXT NOT NULL
);
CREATE INDEX course_notes_definition ON course_notes(definition_id, created_at);
CREATE INDEX course_notes_events_notes ON course_notes_events(notes_id, created_at);
CREATE TRIGGER course_notes_immutable BEFORE UPDATE ON course_notes BEGIN SELECT RAISE(ABORT,'Course notes are append-only'); END;
CREATE TRIGGER course_notes_retained BEFORE DELETE ON course_notes BEGIN SELECT RAISE(ABORT,'Course notes are retained'); END;
CREATE TRIGGER course_notes_parts_immutable BEFORE UPDATE ON course_notes_parts BEGIN SELECT RAISE(ABORT,'Course notes are append-only'); END;
CREATE TRIGGER course_notes_parts_retained BEFORE DELETE ON course_notes_parts BEGIN SELECT RAISE(ABORT,'Course notes are retained'); END;
CREATE TRIGGER course_notes_events_immutable BEFORE UPDATE ON course_notes_events BEGIN SELECT RAISE(ABORT,'Course notes events are append-only'); END;
CREATE TRIGGER course_notes_events_retained BEFORE DELETE ON course_notes_events BEGIN SELECT RAISE(ABORT,'Course notes events are retained'); END;
