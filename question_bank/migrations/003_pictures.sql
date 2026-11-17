-- Pictures that go with a bank question (requirement, 2026-10-01). AGENTS.md "Question bank".
--
-- A picture belongs to one question (so to the question's course definition, whose verified storage keeps its file under
-- `question-bank/pictures/<picture id>/`). It is an image the instructor uploaded (`file`) or a region cut from a page of a PDF read for
-- the same course's bank (`page`: the upload, its page and the region as shares of the page from its top left corner). A picture serves
-- every revision of its question; it never changes the question's content, so a question retained from an assessment keeps following its
-- source. Rows are append-only and retained: removing a picture is an event (`question_bank_picture_events`), and Restore brings it back.
CREATE TABLE question_bank_pictures (
 id TEXT PRIMARY KEY,
 question_id TEXT NOT NULL REFERENCES question_bank_questions(id),
 origin TEXT NOT NULL CHECK(origin IN ('file', 'page')),
 media_type TEXT NOT NULL CHECK(media_type IN ('image/png', 'image/jpeg', 'image/gif', 'image/webp')),
 sha256 TEXT NOT NULL CHECK(length(sha256) = 64),
 size INTEGER NOT NULL CHECK(size > 0),
 width INTEGER NOT NULL CHECK(width > 0),
 height INTEGER NOT NULL CHECK(height > 0),
 path TEXT NOT NULL,
 caption TEXT NOT NULL DEFAULT '' CHECK(length(caption) <= 1000),
 source_name TEXT NOT NULL DEFAULT '' CHECK(length(source_name) <= 200),
 upload_id TEXT REFERENCES question_bank_uploads(id),
 page INTEGER CHECK(page IS NULL OR page >= 1),
 region_json TEXT CHECK(region_json IS NULL OR (json_valid(region_json) AND json_type(region_json) = 'object')),
 request_id TEXT NOT NULL UNIQUE CHECK(length(request_id) BETWEEN 1 AND 160),
 actor TEXT NOT NULL,
 created_at TEXT NOT NULL,
 CHECK((origin = 'page') = (upload_id IS NOT NULL)),
 CHECK((origin = 'page') = (page IS NOT NULL AND region_json IS NOT NULL))
);
CREATE INDEX question_bank_pictures_question ON question_bank_pictures(question_id, created_at);
CREATE TABLE question_bank_picture_events (
 id TEXT PRIMARY KEY,
 picture_id TEXT NOT NULL REFERENCES question_bank_pictures(id),
 action TEXT NOT NULL CHECK(action IN ('removed', 'restored')),
 request_id TEXT NOT NULL UNIQUE CHECK(length(request_id) BETWEEN 1 AND 160),
 actor TEXT NOT NULL,
 created_at TEXT NOT NULL
);
CREATE INDEX question_bank_picture_events_picture ON question_bank_picture_events(picture_id, created_at);

-- A picture is cut from a PDF read for its own course.
CREATE TRIGGER question_bank_picture_scope BEFORE INSERT ON question_bank_pictures
 WHEN NEW.upload_id IS NOT NULL AND (SELECT definition_id FROM question_bank_uploads WHERE id = NEW.upload_id)
   IS NOT (SELECT definition_id FROM question_bank_questions WHERE id = NEW.question_id)
 BEGIN SELECT RAISE(ABORT, 'A picture is cut from a PDF of its own course'); END;

CREATE TRIGGER question_bank_pictures_immutable BEFORE UPDATE ON question_bank_pictures BEGIN SELECT RAISE(ABORT, 'Bank pictures are append-only'); END;
CREATE TRIGGER question_bank_pictures_retained BEFORE DELETE ON question_bank_pictures BEGIN SELECT RAISE(ABORT, 'Bank pictures are retained'); END;
CREATE TRIGGER question_bank_picture_events_immutable BEFORE UPDATE ON question_bank_picture_events BEGIN SELECT RAISE(ABORT, 'Bank picture events are append-only'); END;
CREATE TRIGGER question_bank_picture_events_retained BEFORE DELETE ON question_bank_picture_events BEGIN SELECT RAISE(ABORT, 'Bank picture events are retained'); END;
