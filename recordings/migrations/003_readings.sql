-- Recordings, step 3 (docs/decisions/recordings.md, RECORDINGS "The reading (step 3)", "What an item may become"): Faculty reads a
-- filed recording's transcript itself, with the Assistant's engine, and keeps what the meeting left the instructor with.
--
-- A READING (`recording_readings`) is an immutable RUN: which kept transcript file was read, under what the recording was filed
-- then (`filed_as`: the owner keys of its links, so a recording filed elsewhere is read again), by which engine, with the checksums
-- of what it was asked (`prompt_sha256`: the instructions; `input_sha256`: the facts and the transcript's lines), where the meeting
-- began, and what the quote check DROPPED with the reason (`dropped_json`). `status` is `read`, or `failed` with why in `detail`.
-- Read again, or a changed transcript, is a new run. WORKSPACE-owned through the recording. Append-only.
CREATE TABLE recording_readings (
 id TEXT PRIMARY KEY,
 recording_id TEXT NOT NULL REFERENCES recordings(id),
 run INTEGER NOT NULL CHECK(run >= 1),
 transcript_file_id TEXT NOT NULL REFERENCES recording_files(id),
 filed_as TEXT NOT NULL CHECK(length(filed_as) BETWEEN 1 AND 1000),
 status TEXT NOT NULL CHECK(status IN ('read','failed')),
 engine TEXT NOT NULL CHECK(length(engine) <= 200),
 version TEXT NOT NULL CHECK(length(version) BETWEEN 1 AND 40),
 prompt_sha256 TEXT NOT NULL CHECK(length(prompt_sha256) = 64),
 input_sha256 TEXT NOT NULL CHECK(length(input_sha256) = 64),
 begins_line INTEGER,
 dropped_json TEXT NOT NULL DEFAULT '[]' CHECK(json_valid(dropped_json) AND json_type(dropped_json)='array'),
 detail TEXT NOT NULL DEFAULT '' CHECK(length(detail) <= 2000),
 request_id TEXT NOT NULL DEFAULT '' CHECK(length(request_id) <= 100),
 actor TEXT NOT NULL CHECK(length(actor) BETWEEN 1 AND 100),
 created_at TEXT NOT NULL,
 UNIQUE(recording_id, run)
);
CREATE INDEX recording_readings_request ON recording_readings(request_id) WHERE request_id != '';
CREATE TRIGGER recording_readings_next BEFORE INSERT ON recording_readings
WHEN NEW.run != 1 + COALESCE((SELECT MAX(run) FROM recording_readings WHERE recording_id=NEW.recording_id), 0)
BEGIN SELECT RAISE(ABORT,'A reading must follow the latest one'); END;
CREATE TRIGGER recording_readings_immutable BEFORE UPDATE ON recording_readings BEGIN SELECT RAISE(ABORT,'Readings are append-only'); END;
CREATE TRIGGER recording_readings_retained BEFORE DELETE ON recording_readings BEGIN SELECT RAISE(ABORT,'Readings are retained'); END;

-- AN ITEM is one thing a meeting left the instructor with. It keeps its identity by its kind and its QUOTE (`item_key`), so an
-- item a later reading finds again is the same item and what the instructor answered stays answered.
CREATE TABLE recording_items (
 id TEXT PRIMARY KEY,
 recording_id TEXT NOT NULL REFERENCES recordings(id),
 item_key TEXT NOT NULL CHECK(length(item_key) = 64),
 created_at TEXT NOT NULL,
 UNIQUE(recording_id, item_key)
);
CREATE TRIGGER recording_items_immutable BEFORE UPDATE ON recording_items BEGIN SELECT RAISE(ABORT,'Recording items are append-only'); END;
CREATE TRIGGER recording_items_retained BEFORE DELETE ON recording_items BEGIN SELECT RAISE(ABORT,'Recording items are retained'); END;

-- AN ITEM AS ONE READING SAID IT: one sentence, the quote it rests on with who spoke it and when (taken from the transcript by the
-- quote check, never from the model), whom it is for, the day and the clock time that were said (or ''), whether a dated thing is
-- happening, and
-- what in Faculty it concerns (`about_json`, only what the facts named). The latest `read` run's rows are what the page shows.
CREATE TABLE recording_item_readings (
 reading_id TEXT NOT NULL REFERENCES recording_readings(id),
 item_id TEXT NOT NULL REFERENCES recording_items(id),
 place INTEGER NOT NULL CHECK(place >= 0),
 kind TEXT NOT NULL CHECK(kind IN ('todo','answer','date','ruling','policy','waiting','covered','announced')),
 sentence TEXT NOT NULL CHECK(length(sentence) BETWEEN 1 AND 300),
 quote TEXT NOT NULL CHECK(length(quote) BETWEEN 1 AND 2000),
 line INTEGER NOT NULL CHECK(line >= 0),
 line_end INTEGER NOT NULL CHECK(line_end >= line),
 start_ms INTEGER NOT NULL CHECK(start_ms >= 0),
 end_ms INTEGER NOT NULL CHECK(end_ms >= start_ms),
 speaker_key TEXT NOT NULL DEFAULT '' CHECK(length(speaker_key) <= 200),
 for_whom TEXT NOT NULL CHECK(for_whom IN ('you','coordinator','others','unclear')),
 day TEXT NOT NULL DEFAULT '' CHECK(day = '' OR day GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
 clock TEXT NOT NULL DEFAULT '' CHECK(clock = '' OR (day != '' AND clock GLOB '[0-2][0-9]:[0-5][0-9]')),
 day_words TEXT NOT NULL DEFAULT '' CHECK(length(day_words) <= 80),
 happening INTEGER NOT NULL DEFAULT 1 CHECK(happening IN (0,1)),
 about_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(about_json) AND json_type(about_json)='object'),
 PRIMARY KEY(reading_id, item_id)
) WITHOUT ROWID;
CREATE INDEX recording_item_readings_item ON recording_item_readings(item_id);
CREATE TRIGGER recording_item_readings_immutable BEFORE UPDATE ON recording_item_readings BEGIN SELECT RAISE(ABORT,'Read items are append-only'); END;
CREATE TRIGGER recording_item_readings_retained BEFORE DELETE ON recording_item_readings BEGIN SELECT RAISE(ABORT,'Read items are retained'); END;

-- WHAT BECAME OF AN ITEM, by the instructor's own press (or the Assistant's, on an Approve card): `done`, `dismissed`, `reopened`;
-- `remembered` (a policy kept as an Assistant memory, in `detail_json` its ID), `carded` (an Approve card was made for it, its
-- action ID) and `accepted`/`declined` (a ruling offered at its assessment's rubric finalization). The latest of done, dismissed
-- and reopened says whether the item is still open. "Already done" by Faculty's own records is a view and is never written here.
CREATE TABLE recording_item_events (
 id TEXT PRIMARY KEY,
 item_id TEXT NOT NULL REFERENCES recording_items(id),
 event TEXT NOT NULL CHECK(event IN ('done','dismissed','reopened','remembered','carded','accepted','declined')),
 detail_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(detail_json) AND json_type(detail_json)='object'),
 request_id TEXT NOT NULL DEFAULT '' CHECK(length(request_id) <= 100),
 actor TEXT NOT NULL CHECK(length(actor) BETWEEN 1 AND 100),
 created_at TEXT NOT NULL
);
CREATE INDEX recording_item_events_item ON recording_item_events(item_id, created_at);
CREATE INDEX recording_item_events_request ON recording_item_events(request_id) WHERE request_id != '';
CREATE TRIGGER recording_item_events_immutable BEFORE UPDATE ON recording_item_events BEGIN SELECT RAISE(ABORT,'Item events are append-only'); END;
CREATE TRIGGER recording_item_events_retained BEFORE DELETE ON recording_item_events BEGIN SELECT RAISE(ABORT,'Item events are retained'); END;

-- A TERM'S COORDINATOR: a name alone, TERM-owned (`course_terms.id`), told to a reading as a fact so "the coordinator" in a
-- meeting is a person. The latest revision counts; an empty name takes it back.
CREATE TABLE recording_coordinators (
 term_id TEXT NOT NULL REFERENCES course_terms(id),
 revision INTEGER NOT NULL CHECK(revision >= 1),
 name TEXT NOT NULL CHECK(length(name) <= 100),
 request_id TEXT NOT NULL CHECK(length(request_id) BETWEEN 1 AND 100),
 actor TEXT NOT NULL CHECK(length(actor) BETWEEN 1 AND 100),
 created_at TEXT NOT NULL,
 PRIMARY KEY(term_id, revision)
);
CREATE TRIGGER recording_coordinators_next BEFORE INSERT ON recording_coordinators
WHEN NEW.revision != 1 + COALESCE((SELECT MAX(revision) FROM recording_coordinators WHERE term_id=NEW.term_id), 0)
BEGIN SELECT RAISE(ABORT,'A coordinator revision must follow the latest one'); END;
CREATE TRIGGER recording_coordinators_immutable BEFORE UPDATE ON recording_coordinators BEGIN SELECT RAISE(ABORT,'Coordinator names are append-only'); END;
CREATE TRIGGER recording_coordinators_retained BEFORE DELETE ON recording_coordinators BEGIN SELECT RAISE(ABORT,'Coordinator names are retained'); END;

-- When each recording is read next (mutable runtime state, not a record): a reading the engine could not make is tried again
-- later, less often (`tries`, `next_try_at`, why in `detail`); `requested_at` marks one asked for (Read again, with the request ID
-- and who asked; a recording filed elsewhere), which the next round reads whatever came before.
CREATE TABLE recording_reading_state (
 recording_id TEXT PRIMARY KEY REFERENCES recordings(id),
 tries INTEGER NOT NULL DEFAULT 0 CHECK(tries >= 0),
 next_try_at TEXT NOT NULL DEFAULT '',
 detail TEXT NOT NULL DEFAULT '' CHECK(length(detail) <= 2000),
 requested_at TEXT NOT NULL DEFAULT '',
 request_id TEXT NOT NULL DEFAULT '' CHECK(length(request_id) <= 100),
 actor TEXT NOT NULL DEFAULT '' CHECK(length(actor) <= 100),
 updated_at TEXT NOT NULL DEFAULT ''
);
