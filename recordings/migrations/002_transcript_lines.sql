-- Recordings, step 2 (docs/decisions/recordings.md, RECORDINGS "The page, routes and the Assistant"): a kept recording opens on its
-- transcript, plays from any line, and is found by its words; a speaker may be named in Faculty.
--
-- THE LINES OF A KEPT TRANSCRIPT (`recording_lines`), read once from the kept file when it is kept (or, for a transcript kept before
-- this migration, by the capture worker afterwards): each line's place, start and end within the recording in milliseconds, who
-- Plaud says spoke it (`speaker`: the name Plaud shows now; `speaker_key`: Plaud's first label for that voice, "Speaker 2", which a
-- name given in Faculty is kept by), and its words. The kept FILE in verified storage stays the record; these rows are its copy in
-- the catalog so a page and a search need not read storage, and they are keyed by that file, which never changes: a transcript
-- Plaud changed is another file with its own lines. WORKSPACE-owned through the recording, as the file is. Append-only.
-- `recording_transcripts` says a kept transcript file has been read into lines (and whether it was in the shape Faculty reads), so
-- one with no lines is not read again every round.
CREATE TABLE recording_transcripts (
 file_id TEXT PRIMARY KEY REFERENCES recording_files(id),
 lines INTEGER NOT NULL CHECK(lines >= 0),
 readable INTEGER NOT NULL CHECK(readable IN (0,1)),
 created_at TEXT NOT NULL
);
CREATE TRIGGER recording_transcripts_immutable BEFORE UPDATE ON recording_transcripts BEGIN SELECT RAISE(ABORT,'Read transcripts are append-only'); END;
CREATE TRIGGER recording_transcripts_retained BEFORE DELETE ON recording_transcripts BEGIN SELECT RAISE(ABORT,'Read transcripts are retained'); END;

CREATE TABLE recording_lines (
 file_id TEXT NOT NULL REFERENCES recording_transcripts(file_id),
 position INTEGER NOT NULL CHECK(position >= 0),
 start_ms INTEGER NOT NULL CHECK(start_ms >= 0),
 end_ms INTEGER NOT NULL CHECK(end_ms >= start_ms),
 speaker TEXT NOT NULL DEFAULT '' CHECK(length(speaker) <= 200),
 speaker_key TEXT NOT NULL DEFAULT '' CHECK(length(speaker_key) <= 200),
 text TEXT NOT NULL CHECK(length(text) <= 20000),
 PRIMARY KEY(file_id, position)
) WITHOUT ROWID;
CREATE TRIGGER recording_lines_immutable BEFORE UPDATE ON recording_lines BEGIN SELECT RAISE(ABORT,'Transcript lines are append-only'); END;
CREATE TRIGGER recording_lines_retained BEFORE DELETE ON recording_lines BEGIN SELECT RAISE(ABORT,'Transcript lines are retained'); END;

-- WHO NAMED A SPEAKER. A name the instructor gives a speaker is a revision of the recording (`source` 'instructor', its
-- `speakers_json` mapping Plaud's first label to the name; never written to Plaud). Such a write takes a request ID and records who
-- made it, as a filing by hand does; the revisions capture writes say `automatic`.
ALTER TABLE recording_revisions ADD COLUMN request_id TEXT NOT NULL DEFAULT '' CHECK(length(request_id) <= 100);
ALTER TABLE recording_revisions ADD COLUMN actor TEXT NOT NULL DEFAULT 'automatic' CHECK(length(actor) BETWEEN 1 AND 100);
CREATE INDEX recording_revisions_request ON recording_revisions(request_id) WHERE request_id != '';
