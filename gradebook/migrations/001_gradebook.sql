-- Term gradebooks (gradebook/README.md). The setup and the request log belong to the permanent TERM
-- (course_terms.id); each score revision belongs to one permanent SECTION MEMBERSHIP
-- (roster_memberships(section_id, enrollment_id)) of that term. Nothing references courses.id.
-- Every row is immutable and retained: a change is a new revision, the latest counts.

CREATE TABLE gradebook_setups (
    id TEXT PRIMARY KEY,
    term_id TEXT NOT NULL REFERENCES course_terms(id),
    revision INTEGER NOT NULL CHECK(revision >= 1),
    setup_json TEXT NOT NULL CHECK(json_valid(setup_json)),
    sha256 TEXT NOT NULL CHECK(length(sha256) = 64),
    request_id TEXT NOT NULL CHECK(length(request_id) BETWEEN 1 AND 128),
    request_sha256 TEXT NOT NULL CHECK(length(request_sha256) = 64),
    actor TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(term_id, revision),
    UNIQUE(term_id, request_id)
);

-- One row per score write, import or pull: the provenance its score revisions name.
CREATE TABLE gradebook_requests (
    id TEXT PRIMARY KEY,
    term_id TEXT NOT NULL REFERENCES course_terms(id),
    request_id TEXT NOT NULL CHECK(length(request_id) BETWEEN 1 AND 128),
    request_sha256 TEXT NOT NULL CHECK(length(request_sha256) = 64),
    kind TEXT NOT NULL CHECK(kind IN ('scores', 'import', 'pull')),
    summary_json TEXT NOT NULL CHECK(json_valid(summary_json)),
    actor TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(term_id, request_id)
);

CREATE TABLE gradebook_scores (
    id TEXT PRIMARY KEY,
    term_id TEXT NOT NULL REFERENCES course_terms(id),
    item_id TEXT NOT NULL CHECK(length(item_id) BETWEEN 1 AND 100),
    section_id TEXT NOT NULL,
    enrollment_id TEXT NOT NULL,
    seq INTEGER NOT NULL CHECK(seq >= 1),
    status TEXT NOT NULL CHECK(status IN ('scored', 'excused', 'missing', 'cleared')),
    points TEXT CHECK((status = 'scored') = (points IS NOT NULL)),
    note TEXT NOT NULL DEFAULT '' CHECK(length(note) <= 1000),
    source TEXT NOT NULL CHECK(source IN ('instructor', 'import', 'grading')),
    request_ref TEXT NOT NULL REFERENCES gradebook_requests(id),
    replaces_id TEXT REFERENCES gradebook_scores(id),
    actor TEXT NOT NULL,
    created_at TEXT NOT NULL,
    CHECK((seq = 1) = (replaces_id IS NULL)),
    UNIQUE(term_id, item_id, section_id, enrollment_id, seq),
    FOREIGN KEY(section_id, enrollment_id) REFERENCES roster_memberships(section_id, enrollment_id)
);

CREATE INDEX gradebook_scores_membership ON gradebook_scores(section_id, enrollment_id);
CREATE INDEX gradebook_scores_request ON gradebook_scores(request_ref);
CREATE INDEX gradebook_requests_recent ON gradebook_requests(term_id, created_at);

-- A setup revision follows the term's latest one; a revision number is never skipped or reused.
CREATE TRIGGER gradebook_setup_sequence BEFORE INSERT ON gradebook_setups
WHEN NEW.revision != COALESCE((SELECT MAX(revision) FROM gradebook_setups WHERE term_id = NEW.term_id), 0) + 1
BEGIN SELECT RAISE(ABORT, 'Gradebook setup revisions follow each other'); END;

-- A score's membership belongs to a section of its own term, and the request that wrote it is that term's.
CREATE TRIGGER gradebook_score_owner BEFORE INSERT ON gradebook_scores
WHEN NOT EXISTS (SELECT 1 FROM course_sections WHERE id = NEW.section_id AND term_id = NEW.term_id)
  OR NOT EXISTS (SELECT 1 FROM gradebook_requests WHERE id = NEW.request_ref AND term_id = NEW.term_id)
BEGIN SELECT RAISE(ABORT, 'Gradebook score owner mismatch'); END;

-- A later revision replaces exactly the cell's previous one (seq - 1), never another cell's.
CREATE TRIGGER gradebook_score_sequence BEFORE INSERT ON gradebook_scores
WHEN NEW.seq > 1 AND NOT EXISTS (SELECT 1 FROM gradebook_scores WHERE id = NEW.replaces_id AND term_id = NEW.term_id
    AND item_id = NEW.item_id AND section_id = NEW.section_id AND enrollment_id = NEW.enrollment_id AND seq = NEW.seq - 1)
BEGIN SELECT RAISE(ABORT, 'Gradebook score revisions follow each other'); END;

CREATE TRIGGER gradebook_setups_immutable BEFORE UPDATE ON gradebook_setups
BEGIN SELECT RAISE(ABORT, 'Gradebook setup revisions are immutable'); END;
CREATE TRIGGER gradebook_setups_retained BEFORE DELETE ON gradebook_setups
BEGIN SELECT RAISE(ABORT, 'Gradebook setup revisions are retained'); END;
CREATE TRIGGER gradebook_requests_immutable BEFORE UPDATE ON gradebook_requests
BEGIN SELECT RAISE(ABORT, 'Gradebook requests are immutable'); END;
CREATE TRIGGER gradebook_requests_retained BEFORE DELETE ON gradebook_requests
BEGIN SELECT RAISE(ABORT, 'Gradebook requests are retained'); END;
CREATE TRIGGER gradebook_scores_immutable BEFORE UPDATE ON gradebook_scores
BEGIN SELECT RAISE(ABORT, 'Gradebook score revisions are immutable'); END;
CREATE TRIGGER gradebook_scores_retained BEFORE DELETE ON gradebook_scores
BEGIN SELECT RAISE(ABORT, 'Gradebook score revisions are retained'); END;
