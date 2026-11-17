-- Files for Canvas modules (requirement, 2026-10-10).
--
-- `canvas_content_files` is a file the instructor added to a module item: TERM-owned (`course_terms.id`), kept once in the term's
-- verified storage (`path`, checked against `sha256` at every backup, rosters.TermStorage) and named by the items of the term's
-- copy, or of a section's direct edit, that show it. One row per (term, content, name): the same file added again is the same row.
-- Immutable and retained; a corrected file is another row.
--
-- `canvas_content_file_uploads` is Canvas's answer to putting one kept file in one Canvas course's Files at a folder and name
-- (`canvas_path`): `uploaded` with the Canvas file's ID, `failed`, or `uncertain` (no answer: the file may be there; the next
-- preview reads Canvas and never sends it again by itself). `section_id` names the section whose direct push sent it; a push of the
-- term's copy leaves it empty. The latest `uploaded` row of a course and path is what tells Faculty's own file there from one
-- somebody else put in Canvas. Append-only and retained.
CREATE TABLE canvas_content_files (
 id TEXT PRIMARY KEY CHECK(length(id) = 19 AND substr(id, 1, 3) = 'cf_'),
 term_id TEXT NOT NULL REFERENCES course_terms(id),
 name TEXT NOT NULL CHECK(length(name) BETWEEN 1 AND 255 AND instr(name, '/') = 0),
 media_type TEXT NOT NULL CHECK(length(media_type) BETWEEN 3 AND 200),
 size INTEGER NOT NULL CHECK(size > 0),
 sha256 TEXT NOT NULL CHECK(length(sha256) = 64),
 path TEXT NOT NULL CHECK(length(path) BETWEEN 1 AND 300),
 actor TEXT NOT NULL,
 created_at TEXT NOT NULL,
 UNIQUE(term_id, sha256, name)
);
CREATE TABLE canvas_content_file_uploads (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 file_id TEXT NOT NULL REFERENCES canvas_content_files(id),
 course_id TEXT NOT NULL CHECK(course_id <> '' AND course_id NOT GLOB '*[^0-9]*'),
 canvas_path TEXT NOT NULL CHECK(length(canvas_path) BETWEEN 1 AND 2000),
 status TEXT NOT NULL CHECK(status IN ('uploaded','failed','uncertain')),
 canvas_file_id TEXT NOT NULL DEFAULT '' CHECK(canvas_file_id NOT GLOB '*[^0-9]*'),
 replaced INTEGER NOT NULL DEFAULT 0 CHECK(replaced IN (0, 1)),
 http_status INTEGER,
 detail TEXT NOT NULL DEFAULT '',
 push_id TEXT NOT NULL,
 section_id TEXT REFERENCES course_sections(id),
 actor TEXT NOT NULL,
 created_at TEXT NOT NULL
);
CREATE INDEX canvas_content_file_uploads_place ON canvas_content_file_uploads(course_id, canvas_path, id);
CREATE TRIGGER canvas_content_files_immutable BEFORE UPDATE ON canvas_content_files BEGIN SELECT RAISE(ABORT,'Canvas content files are immutable'); END;
CREATE TRIGGER canvas_content_files_retained BEFORE DELETE ON canvas_content_files BEGIN SELECT RAISE(ABORT,'Canvas content files are retained'); END;
CREATE TRIGGER canvas_content_file_uploads_immutable BEFORE UPDATE ON canvas_content_file_uploads BEGIN SELECT RAISE(ABORT,'Canvas content file uploads are append-only'); END;
CREATE TRIGGER canvas_content_file_uploads_retained BEFORE DELETE ON canvas_content_file_uploads BEGIN SELECT RAISE(ABORT,'Canvas content file uploads are retained'); END;
