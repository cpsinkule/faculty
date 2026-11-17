-- Excused absences (AGENTS.md "Excused absences"): a student's absence from one course section, owned by the permanent section
-- membership (section_id, enrollment_id) of that section's term. Everything is append-only: the absence's facts and the instructor's
-- decision are revisions (the highest counts), documentation is added and removed by record, and each "excuse the covered work" is
-- a receipt. Uploaded documents live in the section's verified storage (absences/<absence id>/...); a document picked from the
-- communications record is a reference to that kept attachment, never a copy.
CREATE TABLE absences (
 id TEXT PRIMARY KEY,
 term_id TEXT NOT NULL REFERENCES course_terms(id),
 section_id TEXT NOT NULL,
 enrollment_id TEXT NOT NULL,
 request_id TEXT NOT NULL CHECK(length(request_id) BETWEEN 1 AND 100),
 actor TEXT NOT NULL,
 created_at TEXT NOT NULL,
 UNIQUE(term_id, request_id),
 FOREIGN KEY(section_id, enrollment_id) REFERENCES roster_memberships(section_id, enrollment_id)
);
CREATE INDEX absences_member ON absences(section_id, enrollment_id);
CREATE TRIGGER absences_term_matches BEFORE INSERT ON absences
WHEN NOT EXISTS (SELECT 1 FROM course_sections WHERE id=NEW.section_id AND term_id=NEW.term_id)
BEGIN SELECT RAISE(ABORT,'An absence belongs to its section''s term'); END;
CREATE TRIGGER absences_immutable BEFORE UPDATE ON absences BEGIN SELECT RAISE(ABORT,'Absences are append-only'); END;
CREATE TRIGGER absences_retained BEFORE DELETE ON absences BEGIN SELECT RAISE(ABORT,'Absences are retained'); END;

CREATE TABLE absence_revisions (
 id TEXT PRIMARY KEY,
 absence_id TEXT NOT NULL REFERENCES absences(id),
 revision INTEGER NOT NULL CHECK(revision >= 1),
 first_date TEXT NOT NULL CHECK(first_date GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]'),
 last_date TEXT NOT NULL CHECK(last_date GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]' AND last_date >= first_date),
 reason TEXT NOT NULL CHECK(reason IN ('illness','family','dependent','activity','religious','legal','military','interview','pregnancy','other')),
 details TEXT NOT NULL DEFAULT '' CHECK(length(details) <= 2000),
 status TEXT NOT NULL CHECK(status IN ('pending','excused','not_excused','withdrawn')),
 decision_note TEXT NOT NULL DEFAULT '' CHECK(length(decision_note) <= 2000),
 request_id TEXT NOT NULL CHECK(length(request_id) BETWEEN 1 AND 100),
 actor TEXT NOT NULL,
 created_at TEXT NOT NULL,
 UNIQUE(absence_id, revision),
 UNIQUE(absence_id, request_id)
);
CREATE TRIGGER absence_revisions_next BEFORE INSERT ON absence_revisions
WHEN NEW.revision != 1 + COALESCE((SELECT MAX(revision) FROM absence_revisions WHERE absence_id=NEW.absence_id), 0)
BEGIN SELECT RAISE(ABORT,'An absence revision must follow the latest one'); END;
CREATE TRIGGER absence_revisions_immutable BEFORE UPDATE ON absence_revisions BEGIN SELECT RAISE(ABORT,'Absence revisions are append-only'); END;
CREATE TRIGGER absence_revisions_retained BEFORE DELETE ON absence_revisions BEGIN SELECT RAISE(ABORT,'Absence revisions are retained'); END;

CREATE TABLE absence_documents (
 id TEXT PRIMARY KEY,
 absence_id TEXT NOT NULL REFERENCES absences(id),
 kind TEXT NOT NULL CHECK(kind IN ('upload','message')),
 name TEXT NOT NULL CHECK(length(name) BETWEEN 1 AND 200),
 content_type TEXT NOT NULL CHECK(length(content_type) <= 100),
 size INTEGER NOT NULL CHECK(size >= 0),
 sha256 TEXT NOT NULL CHECK(length(sha256) = 64),
 path TEXT CHECK(path IS NULL OR length(path) <= 300),
 message_id TEXT,
 attachment_index INTEGER CHECK(attachment_index IS NULL OR attachment_index >= 0),
 request_id TEXT NOT NULL CHECK(length(request_id) BETWEEN 1 AND 100),
 actor TEXT NOT NULL,
 created_at TEXT NOT NULL,
 UNIQUE(absence_id, request_id),
 CHECK((kind='upload' AND path IS NOT NULL AND message_id IS NULL) OR (kind='message' AND path IS NULL AND message_id IS NOT NULL AND attachment_index IS NOT NULL))
);
CREATE TRIGGER absence_documents_immutable BEFORE UPDATE ON absence_documents BEGIN SELECT RAISE(ABORT,'Absence documents are append-only'); END;
CREATE TRIGGER absence_documents_retained BEFORE DELETE ON absence_documents BEGIN SELECT RAISE(ABORT,'Absence documents are retained'); END;

CREATE TABLE absence_document_events (
 id TEXT PRIMARY KEY,
 document_id TEXT NOT NULL REFERENCES absence_documents(id),
 action TEXT NOT NULL CHECK(action IN ('removed','restored')),
 request_id TEXT NOT NULL CHECK(length(request_id) BETWEEN 1 AND 100),
 actor TEXT NOT NULL,
 created_at TEXT NOT NULL,
 UNIQUE(document_id, request_id)
);
CREATE TRIGGER absence_document_events_immutable BEFORE UPDATE ON absence_document_events BEGIN SELECT RAISE(ABORT,'Document events are append-only'); END;
CREATE TRIGGER absence_document_events_retained BEFORE DELETE ON absence_document_events BEGIN SELECT RAISE(ABORT,'Document events are retained'); END;

CREATE TABLE absence_applications (
 id TEXT PRIMARY KEY,
 absence_id TEXT NOT NULL REFERENCES absences(id),
 revision INTEGER NOT NULL,
 request_id TEXT NOT NULL CHECK(length(request_id) BETWEEN 1 AND 100),
 fingerprint TEXT NOT NULL CHECK(length(fingerprint) = 64),
 chosen_json TEXT NOT NULL CHECK(json_valid(chosen_json) AND json_type(chosen_json)='array'),
 result_json TEXT NOT NULL CHECK(json_valid(result_json) AND json_type(result_json)='object'),
 actor TEXT NOT NULL,
 created_at TEXT NOT NULL,
 UNIQUE(absence_id, request_id)
);
CREATE TRIGGER absence_applications_immutable BEFORE UPDATE ON absence_applications BEGIN SELECT RAISE(ABORT,'Absence receipts are append-only'); END;
CREATE TRIGGER absence_applications_retained BEFORE DELETE ON absence_applications BEGIN SELECT RAISE(ABORT,'Absence receipts are retained'); END;
