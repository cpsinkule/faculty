-- Recordings (docs/decisions/recordings.md, RECORDINGS; requirement, 2026-11-12). Step 1, Intake: what Plaud lists, what each recording is filed under, and what Faculty kept.
--
-- OWNERS. A recording (`recordings`, with its immutable `recording_revisions`) is WORKSPACE-owned, as a captured message is: one
-- recording may concern several owners. What it concerns is its LINKS (`recording_links`), each belonging to exactly one owner: a
-- student's TERM ENROLLMENT (`student_enrollments.id`), a TERM (`course_terms.id`), a COURSE (`course_definitions.id`) or a
-- SECTION's class day (`course_sections.id` with the campus date); a memo and "Not Faculty's" have no owner below the workspace.
-- Kept files (transcript, outline, marks, Plaud's notes, the audio) are files in verified storage: a `recording_bundles` row is one
-- storage owner (the files one fetch kept, written at once and never changed; staged on NVMe, backed up by the nightly
-- coordinator, then kept on the HDD alone), and `recording_files` names each of its files with its checksum. Everything is
-- append-only and retained, except the storage columns the storage manager keeps and the capture state.
CREATE TABLE recording_bundles (
 id TEXT PRIMARY KEY,
 created_at TEXT NOT NULL,
 storage_status TEXT NOT NULL DEFAULT '',
 storage_snapshot TEXT NOT NULL DEFAULT '',
 storage_verified_at TEXT NOT NULL DEFAULT '',
 storage_initialized INTEGER NOT NULL DEFAULT 0,
 storage_hot_required INTEGER NOT NULL DEFAULT 0,
 storage_error TEXT NOT NULL DEFAULT ''
);
CREATE TRIGGER recording_bundles_identity BEFORE UPDATE OF id, created_at ON recording_bundles
BEGIN SELECT RAISE(ABORT,'A recording bundle keeps its identity'); END;
CREATE TRIGGER recording_bundles_retained BEFORE DELETE ON recording_bundles BEGIN SELECT RAISE(ABORT,'Recording bundles are retained'); END;

-- One kept file of one bundle, named by what it is: a bundle holds at most one of each.
CREATE TABLE recording_files (
 id TEXT PRIMARY KEY,
 bundle_id TEXT NOT NULL REFERENCES recording_bundles(id),
 role TEXT NOT NULL CHECK(role IN ('transcript','outline','marks','notes','audio')),
 path TEXT NOT NULL CHECK(length(path) BETWEEN 1 AND 200),
 sha256 TEXT NOT NULL CHECK(length(sha256) = 64),
 size INTEGER NOT NULL CHECK(size >= 0),
 content_type TEXT NOT NULL CHECK(length(content_type) <= 200),
 created_at TEXT NOT NULL,
 UNIQUE(bundle_id, role),
 UNIQUE(bundle_id, path)
);
CREATE TRIGGER recording_files_immutable BEFORE UPDATE ON recording_files BEGIN SELECT RAISE(ABORT,'Kept recording files are append-only'); END;
CREATE TRIGGER recording_files_retained BEFORE DELETE ON recording_files BEGIN SELECT RAISE(ABORT,'Kept recording files are retained'); END;

-- One recording at Plaud, known by Plaud's own ID. What Faculty knows of it is in its revisions.
CREATE TABLE recordings (
 id TEXT PRIMARY KEY,
 plaud_id TEXT NOT NULL UNIQUE CHECK(length(plaud_id) BETWEEN 1 AND 200),
 captured_at TEXT NOT NULL
);
CREATE TRIGGER recordings_immutable BEFORE UPDATE ON recordings BEGIN SELECT RAISE(ABORT,'Recordings are append-only'); END;
CREATE TRIGGER recordings_retained BEFORE DELETE ON recordings BEGIN SELECT RAISE(ABORT,'Recordings are retained'); END;

-- What Faculty holds of a recording; the latest revision counts. `source`: Plaud's list said it (`list`: title, start, length; the
-- row a recording has before it is filed as Faculty's), a fetch kept or replaced files (`fetch`: a transcript Plaud changed is a new
-- revision), or the instructor named a speaker in Faculty (`instructor`, step 2; never written to Plaud). `started_at` is the instant
-- in UTC ("2026-11-11T16:02:11Z"); `start_known` is 0 when Plaud gave only the time it received the recording, which files nothing
-- by the calendar. A revision carries forward the files it did not replace.
CREATE TABLE recording_revisions (
 id TEXT PRIMARY KEY,
 recording_id TEXT NOT NULL REFERENCES recordings(id),
 revision INTEGER NOT NULL CHECK(revision >= 1),
 source TEXT NOT NULL CHECK(source IN ('list','fetch','instructor')),
 title TEXT NOT NULL DEFAULT '' CHECK(length(title) <= 500),
 started_at TEXT NOT NULL CHECK(length(started_at) = 20),
 start_known INTEGER NOT NULL DEFAULT 1 CHECK(start_known IN (0,1)),
 duration_ms INTEGER NOT NULL CHECK(duration_ms >= 0),
 transcript_file_id TEXT REFERENCES recording_files(id),
 outline_file_id TEXT REFERENCES recording_files(id),
 marks_file_id TEXT REFERENCES recording_files(id),
 notes_file_id TEXT REFERENCES recording_files(id),
 audio_file_id TEXT REFERENCES recording_files(id),
 speakers_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(speakers_json) AND json_type(speakers_json)='object'),
 content_sha256 TEXT NOT NULL CHECK(length(content_sha256) = 64),
 captured_at TEXT NOT NULL,
 UNIQUE(recording_id, revision)
);
CREATE INDEX recording_revisions_recording ON recording_revisions(recording_id, revision);
CREATE TRIGGER recording_revisions_next BEFORE INSERT ON recording_revisions
WHEN NEW.revision != 1 + COALESCE((SELECT MAX(revision) FROM recording_revisions WHERE recording_id=NEW.recording_id), 0)
BEGIN SELECT RAISE(ABORT,'A recording revision must follow the latest one'); END;
CREATE TRIGGER recording_revisions_immutable BEFORE UPDATE ON recording_revisions BEGIN SELECT RAISE(ABORT,'Recording revisions are append-only'); END;
CREATE TRIGGER recording_revisions_retained BEFORE DELETE ON recording_revisions BEGIN SELECT RAISE(ABORT,'Recording revisions are retained'); END;

-- What a recording concerns. For each recording and owner (`owner_key`) the LATEST row counts: `filed` is a link, `withdrawn` takes
-- it back (re-filing writes a withdrawal of the old owner and a filing of the new one in one transaction). A recording with no
-- filed link NEEDS FILING; one whose only filed link is `not_faculty` is never fetched. `filed_by`: where its start fell on the
-- calendar (`calendar`, with what the calendar showed in `basis_json`) or the instructor's choice (`instructor`). An enrollment
-- link may name the appointment it fell in (`student_support_items.id`, by reference). A link may carry a start and end within
-- the recording (step 4: several students in one recording); its owner then sees that part alone.
CREATE TABLE recording_links (
 id TEXT PRIMARY KEY,
 recording_id TEXT NOT NULL REFERENCES recordings(id),
 kind TEXT NOT NULL CHECK(kind IN ('enrollment','term','course','class_day','memo','not_faculty')),
 owner_key TEXT NOT NULL CHECK(length(owner_key) BETWEEN 1 AND 200),
 revision INTEGER NOT NULL CHECK(revision >= 1),
 state TEXT NOT NULL CHECK(state IN ('filed','withdrawn')),
 enrollment_id TEXT REFERENCES student_enrollments(id),
 term_id TEXT REFERENCES course_terms(id),
 definition_id TEXT REFERENCES course_definitions(id),
 section_id TEXT REFERENCES course_sections(id),
 class_date TEXT NOT NULL DEFAULT '',
 appointment_id TEXT,
 part_start_ms INTEGER,
 part_end_ms INTEGER,
 filed_by TEXT NOT NULL CHECK(filed_by IN ('calendar','instructor')),
 basis_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(basis_json) AND json_type(basis_json)='object'),
 request_id TEXT NOT NULL DEFAULT '' CHECK(length(request_id) <= 100),
 actor TEXT NOT NULL CHECK(length(actor) BETWEEN 1 AND 100),
 created_at TEXT NOT NULL,
 UNIQUE(recording_id, owner_key, revision),
 CHECK(
  (kind='enrollment' AND enrollment_id IS NOT NULL AND term_id IS NOT NULL AND definition_id IS NULL AND section_id IS NULL
   AND class_date='' AND owner_key='enrollment:' || enrollment_id)
  OR (kind='term' AND term_id IS NOT NULL AND enrollment_id IS NULL AND definition_id IS NULL AND section_id IS NULL
   AND class_date='' AND owner_key='term:' || term_id)
  OR (kind='course' AND definition_id IS NOT NULL AND enrollment_id IS NULL AND term_id IS NULL AND section_id IS NULL
   AND class_date='' AND owner_key='course:' || definition_id)
  OR (kind='class_day' AND section_id IS NOT NULL AND term_id IS NOT NULL AND enrollment_id IS NULL AND definition_id IS NULL
   AND class_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]' AND owner_key='class_day:' || section_id || ':' || class_date)
  OR (kind IN ('memo','not_faculty') AND enrollment_id IS NULL AND term_id IS NULL AND definition_id IS NULL AND section_id IS NULL
   AND class_date='' AND owner_key=kind)),
 CHECK(appointment_id IS NULL OR kind='enrollment'),
 CHECK((part_start_ms IS NULL AND part_end_ms IS NULL)
       OR (part_start_ms IS NOT NULL AND part_end_ms IS NOT NULL AND part_start_ms >= 0 AND part_end_ms > part_start_ms))
);
CREATE INDEX recording_links_recording ON recording_links(recording_id, owner_key, revision);
CREATE INDEX recording_links_enrollment ON recording_links(enrollment_id, recording_id) WHERE enrollment_id IS NOT NULL;
CREATE INDEX recording_links_term ON recording_links(term_id, recording_id) WHERE term_id IS NOT NULL;
CREATE INDEX recording_links_request ON recording_links(request_id) WHERE request_id != '';
CREATE TRIGGER recording_links_next BEFORE INSERT ON recording_links
WHEN NEW.revision != 1 + COALESCE((SELECT MAX(revision) FROM recording_links WHERE recording_id=NEW.recording_id AND owner_key=NEW.owner_key), 0)
BEGIN SELECT RAISE(ABORT,'A recording link revision must follow the latest one'); END;
CREATE TRIGGER recording_links_enrollment_term BEFORE INSERT ON recording_links
WHEN NEW.kind='enrollment' AND NEW.term_id IS NOT (SELECT term_id FROM student_enrollments WHERE id=NEW.enrollment_id)
BEGIN SELECT RAISE(ABORT,'A recording link belongs to the term of its enrollment'); END;
CREATE TRIGGER recording_links_section_term BEFORE INSERT ON recording_links
WHEN NEW.kind='class_day' AND NEW.term_id IS NOT (SELECT term_id FROM course_sections WHERE id=NEW.section_id)
BEGIN SELECT RAISE(ABORT,'A class day belongs to the term of its section'); END;
CREATE TRIGGER recording_links_immutable BEFORE UPDATE ON recording_links BEGIN SELECT RAISE(ABORT,'Recording links are append-only'); END;
CREATE TRIGGER recording_links_retained BEFORE DELETE ON recording_links BEGIN SELECT RAISE(ABORT,'Recording links are retained'); END;

-- Where capture stands (mutable runtime state, not a record): the last round, what it found or why it waits, when the next round
-- is due, and whether the instructor asked for one now. `status` is `sign_in` after Plaud refused a renewal; `refused_file` is then
-- the token file's size, time and inode (never anything of its contents), and capture stays stopped until those change.
CREATE TABLE recording_capture_state (
 id INTEGER PRIMARY KEY CHECK(id = 1),
 status TEXT NOT NULL DEFAULT 'idle' CHECK(status IN ('idle','ok','waiting','sign_in','failed')),
 detail TEXT NOT NULL DEFAULT '' CHECK(length(detail) <= 2000),
 counts_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(counts_json) AND json_type(counts_json)='object'),
 last_round_at TEXT NOT NULL DEFAULT '',
 last_success_at TEXT NOT NULL DEFAULT '',
 next_due_at TEXT NOT NULL DEFAULT '',
 requested_at TEXT NOT NULL DEFAULT '',
 refused_file TEXT NOT NULL DEFAULT '' CHECK(length(refused_file) <= 200),
 updated_at TEXT NOT NULL DEFAULT ''
);
INSERT INTO recording_capture_state(id) VALUES (1);

-- Where each recording's own capture stands (mutable runtime state): why it still needs filing (`filing_detail`), and for one
-- filed as Faculty's, whether its files are kept, what it waits on, and when Plaud is asked about it again.
CREATE TABLE recording_fetch_state (
 recording_id TEXT PRIMARY KEY REFERENCES recordings(id),
 status TEXT NOT NULL DEFAULT 'idle' CHECK(status IN ('idle','kept','waiting','failed')),
 detail TEXT NOT NULL DEFAULT '' CHECK(length(detail) <= 2000),
 filing_detail TEXT NOT NULL DEFAULT '' CHECK(length(filing_detail) <= 2000),
 tries INTEGER NOT NULL DEFAULT 0 CHECK(tries >= 0),
 checked_at TEXT NOT NULL DEFAULT '',
 next_check_at TEXT NOT NULL DEFAULT '',
 updated_at TEXT NOT NULL DEFAULT ''
);
