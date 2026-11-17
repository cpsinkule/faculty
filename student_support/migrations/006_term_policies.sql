-- TEACHING-WORKFLOWS TERM POLICIES (requirement, 2026-10-03): the instructor's own policies for one course term, saved on the
-- Follow-up page and read by every follow-up suggestion for that term's students. Term-owned (course_terms.id); immutable
-- revisions with exact request IDs; an empty text clears them.
CREATE TABLE student_support_term_policies (
 term_id TEXT NOT NULL REFERENCES course_terms(id),
 revision INTEGER NOT NULL CHECK(revision >= 1),
 content TEXT NOT NULL CHECK(length(content) <= 20000),
 request_id TEXT NOT NULL CHECK(length(request_id) BETWEEN 1 AND 100),
 request_sha256 TEXT NOT NULL,
 actor TEXT NOT NULL,
 created_at TEXT NOT NULL,
 PRIMARY KEY(term_id,revision),
 UNIQUE(term_id,request_id)
);
CREATE TRIGGER student_support_term_policies_next BEFORE INSERT ON student_support_term_policies
 WHEN NEW.revision != 1 + COALESCE((SELECT MAX(revision) FROM student_support_term_policies WHERE term_id=NEW.term_id), 0)
 BEGIN SELECT RAISE(ABORT,'TEACHING-WORKFLOWS: a policy revision must follow the latest one'); END;
CREATE TRIGGER student_support_term_policies_immutable BEFORE UPDATE ON student_support_term_policies
 BEGIN SELECT RAISE(ABORT,'TEACHING-WORKFLOWS: term policy revisions are immutable'); END;
CREATE TRIGGER student_support_term_policies_retained BEFORE DELETE ON student_support_term_policies
 BEGIN SELECT RAISE(ABORT,'TEACHING-WORKFLOWS: term policy revisions are retained'); END;
