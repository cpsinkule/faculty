-- Recordings: ONLY WHAT IS FACULTY'S IS KEPT (docs/decisions/recordings.md, RECORDINGS "What is kept, and who owns it" › ONLY
-- WHAT IS FACULTY'S; requirement, 2026-11-14).
--
-- A recording that is NOT KEPT leaves every table below and holds one row here: Plaud's own ID for it and who decided, so that
-- Plaud listing it again does not bring it back or ask about it again. Nothing of what it was (no title, no time, no length).
-- `state` is `not_kept`, or `ask` once the instructor asked to be asked about what was not kept again: an `ask` recording is
-- listed again and waits for their Keep or Don't keep, and the calendar never decides it is not theirs a second time. The row
-- is taken away when the recording is kept (filed as Faculty's). Mutable runtime state of a decision, not a record of content;
-- workspace-owned, in the nightly catalog backup and never the generic export.
CREATE TABLE recording_not_kept (
 plaud_id TEXT PRIMARY KEY CHECK(length(plaud_id) BETWEEN 1 AND 200),
 state TEXT NOT NULL CHECK(state IN ('not_kept','ask')),
 decided_by TEXT NOT NULL CHECK(decided_by IN ('calendar','instructor')),
 request_id TEXT NOT NULL DEFAULT '' CHECK(length(request_id) <= 100),
 actor TEXT NOT NULL CHECK(length(actor) BETWEEN 1 AND 100),
 decided_at TEXT NOT NULL,
 updated_at TEXT NOT NULL
) WITHOUT ROWID;
CREATE INDEX recording_not_kept_request ON recording_not_kept(request_id) WHERE request_id != '';

-- ERASING. Everything of a recording stays append-only and retained, with ONE way out: the erasure of a whole recording
-- (recordings/service.py `_erase_rows`), which names the recording here inside its own transaction and clears the name before
-- it commits, so no other connection ever sees it set. A DELETE anywhere else is refused as before. Rows that carry their
-- recording are checked against the one named; rows reached only through a file, a reading or an item (a bundle and its
-- files, a transcript and its lines, what a reading said of an item, what became of an item) are deletable only while one
-- is named.
CREATE TABLE recording_erasing (
 id INTEGER PRIMARY KEY CHECK(id = 1),
 recording_id TEXT
);
INSERT INTO recording_erasing(id, recording_id) VALUES (1, NULL);

-- The bundles of an erased recording whose FILES are still to be removed from both drives (runtime state): written in the
-- transaction that erases the rows, taken away once the storage engine has removed every copy. A drive that is away leaves
-- the row, and each round tries again.
CREATE TABLE recording_erased_bundles (
 bundle_id TEXT PRIMARY KEY CHECK(length(bundle_id) = 36),
 erased_at TEXT NOT NULL,
 detail TEXT NOT NULL DEFAULT '' CHECK(length(detail) <= 2000)
) WITHOUT ROWID;

DROP TRIGGER recordings_retained;
CREATE TRIGGER recordings_retained BEFORE DELETE ON recordings
WHEN OLD.id IS NOT (SELECT recording_id FROM recording_erasing WHERE id=1)
BEGIN SELECT RAISE(ABORT,'Recordings are retained'); END;

DROP TRIGGER recording_revisions_retained;
CREATE TRIGGER recording_revisions_retained BEFORE DELETE ON recording_revisions
WHEN OLD.recording_id IS NOT (SELECT recording_id FROM recording_erasing WHERE id=1)
BEGIN SELECT RAISE(ABORT,'Recording revisions are retained'); END;

DROP TRIGGER recording_links_retained;
CREATE TRIGGER recording_links_retained BEFORE DELETE ON recording_links
WHEN OLD.recording_id IS NOT (SELECT recording_id FROM recording_erasing WHERE id=1)
BEGIN SELECT RAISE(ABORT,'Recording links are retained'); END;

DROP TRIGGER recording_readings_retained;
CREATE TRIGGER recording_readings_retained BEFORE DELETE ON recording_readings
WHEN OLD.recording_id IS NOT (SELECT recording_id FROM recording_erasing WHERE id=1)
BEGIN SELECT RAISE(ABORT,'Readings are retained'); END;

DROP TRIGGER recording_items_retained;
CREATE TRIGGER recording_items_retained BEFORE DELETE ON recording_items
WHEN OLD.recording_id IS NOT (SELECT recording_id FROM recording_erasing WHERE id=1)
BEGIN SELECT RAISE(ABORT,'Recording items are retained'); END;

DROP TRIGGER recording_bundles_retained;
CREATE TRIGGER recording_bundles_retained BEFORE DELETE ON recording_bundles
WHEN (SELECT recording_id FROM recording_erasing WHERE id=1) IS NULL
BEGIN SELECT RAISE(ABORT,'Recording bundles are retained'); END;

DROP TRIGGER recording_files_retained;
CREATE TRIGGER recording_files_retained BEFORE DELETE ON recording_files
WHEN (SELECT recording_id FROM recording_erasing WHERE id=1) IS NULL
BEGIN SELECT RAISE(ABORT,'Kept recording files are retained'); END;

DROP TRIGGER recording_transcripts_retained;
CREATE TRIGGER recording_transcripts_retained BEFORE DELETE ON recording_transcripts
WHEN (SELECT recording_id FROM recording_erasing WHERE id=1) IS NULL
BEGIN SELECT RAISE(ABORT,'Read transcripts are retained'); END;

DROP TRIGGER recording_lines_retained;
CREATE TRIGGER recording_lines_retained BEFORE DELETE ON recording_lines
WHEN (SELECT recording_id FROM recording_erasing WHERE id=1) IS NULL
BEGIN SELECT RAISE(ABORT,'Transcript lines are retained'); END;

DROP TRIGGER recording_item_readings_retained;
CREATE TRIGGER recording_item_readings_retained BEFORE DELETE ON recording_item_readings
WHEN (SELECT recording_id FROM recording_erasing WHERE id=1) IS NULL
BEGIN SELECT RAISE(ABORT,'Read items are retained'); END;

DROP TRIGGER recording_item_events_retained;
CREATE TRIGGER recording_item_events_retained BEFORE DELETE ON recording_item_events
WHEN (SELECT recording_id FROM recording_erasing WHERE id=1) IS NULL
BEGIN SELECT RAISE(ABORT,'Item events are retained'); END;
