-- COORDINATOR (requirement, 2026-11-14).
--
-- OWNER: the course TERM (`course_terms.id`): who coordinates a course is a fact of one teaching term, shared by that term's
-- sections, never of a saved offering entry (`courses`) and never copied to a section. Each row is one immutable revision of the
-- term's whole answer (coordinated or not, and who); the highest revision counts and earlier ones are retained, so Faculty can
-- always say who the coordinator was when a message arrived. A term with no row is not coordinated.
CREATE TABLE course_term_coordinators (
 id TEXT PRIMARY KEY,
 term_id TEXT NOT NULL REFERENCES course_terms(id),
 revision INTEGER NOT NULL CHECK(revision >= 1),
 coordinated INTEGER NOT NULL CHECK(coordinated IN (0,1)),
 name TEXT NOT NULL CHECK(length(name) <= 120),
 email TEXT NOT NULL CHECK(length(email) <= 254),
 actor TEXT NOT NULL,
 created_at TEXT NOT NULL,
 UNIQUE(term_id, revision),
 -- A coordinated term names its coordinator; a term that is not keeps no name or address.
 CHECK((coordinated=1 AND name!='' AND email!='') OR (coordinated=0 AND name='' AND email=''))
);
CREATE TRIGGER course_term_coordinators_next BEFORE INSERT ON course_term_coordinators
WHEN NEW.revision != 1 + COALESCE((SELECT MAX(revision) FROM course_term_coordinators WHERE term_id=NEW.term_id), 0)
BEGIN SELECT RAISE(ABORT,'COORDINATOR: a revision must follow the latest one'); END;
CREATE TRIGGER course_term_coordinators_immutable BEFORE UPDATE ON course_term_coordinators
BEGIN SELECT RAISE(ABORT,'COORDINATOR: revisions are append-only'); END;
CREATE TRIGGER course_term_coordinators_retained BEFORE DELETE ON course_term_coordinators
BEGIN SELECT RAISE(ABORT,'COORDINATOR: revisions are retained'); END;
