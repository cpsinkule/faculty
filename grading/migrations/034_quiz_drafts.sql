-- Quizzes drafted by a model from the instructor's material (requirement, 2026-09-28). A draft is COURSE-WIDE authoring (it owns no term): it belongs to the course definition, like the
-- assignments and printed versions it becomes, and its material and compiled files are kept in the definition's verified storage.
-- Each model run is an immutable revision; asking for changes adds one. Approving a revision is the instructor's decision for ONE
-- term: it makes the course-wide assignment and one printed version per quiz version, the term's grading plan with every version's
-- questions and rubrics, and each section's print jobs, recorded as an approval of that draft and term. Everything is retained.
CREATE TABLE quiz_drafts (
 id TEXT PRIMARY KEY,
 definition_id TEXT NOT NULL REFERENCES course_definitions(id),
 request_id TEXT NOT NULL UNIQUE,
 request_sha256 TEXT NOT NULL,
 input_json TEXT NOT NULL,
 created_at TEXT NOT NULL
);
CREATE INDEX quiz_draft_course ON quiz_drafts(definition_id,created_at);
CREATE TRIGGER quiz_draft_immutable BEFORE UPDATE ON quiz_drafts
 BEGIN SELECT RAISE(ABORT,'Quiz drafts are immutable'); END;
CREATE TRIGGER quiz_draft_retained BEFORE DELETE ON quiz_drafts
 BEGIN SELECT RAISE(ABORT,'Quiz drafts are retained'); END;
CREATE TABLE quiz_draft_revisions (
 id TEXT PRIMARY KEY,
 draft_id TEXT NOT NULL REFERENCES quiz_drafts(id),
 number INTEGER NOT NULL CHECK(number >= 1),
 based_on TEXT REFERENCES quiz_draft_revisions(id),
 request_id TEXT NOT NULL UNIQUE,
 request_sha256 TEXT NOT NULL,
 input_json TEXT NOT NULL,
 profile_json TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('pending','running','complete','failed')),
 cancelled INTEGER NOT NULL DEFAULT 0 CHECK(cancelled IN (0,1)),
 result_json TEXT,
 provider_json TEXT,
 error TEXT NOT NULL DEFAULT '',
 not_before TEXT NOT NULL DEFAULT '',
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL,
 UNIQUE(draft_id,number)
);
CREATE INDEX quiz_draft_revision_queue ON quiz_draft_revisions(status,not_before);
CREATE TRIGGER quiz_draft_revision_inputs_immutable BEFORE UPDATE OF draft_id,number,based_on,request_id,request_sha256,input_json,profile_json ON quiz_draft_revisions
 BEGIN SELECT RAISE(ABORT,'Quiz draft revision inputs are immutable'); END;
CREATE TRIGGER quiz_draft_revision_result_immutable BEFORE UPDATE ON quiz_draft_revisions
 WHEN OLD.status IN ('complete','failed') BEGIN SELECT RAISE(ABORT,'Quiz draft revision results are immutable'); END;
CREATE TRIGGER quiz_draft_revision_retained BEFORE DELETE ON quiz_draft_revisions
 BEGIN SELECT RAISE(ABORT,'Quiz draft revisions are retained'); END;
-- The instructor's approval of one revision for one term. Its input (the revision, the term, the assignment's key and title, and each
-- section's copies per version) never changes; the worker carries it out step by step, each step idempotent, recording how far it got,
-- and its outcome (what it made, or why it stopped) is immutable once written. A failed approval may be approved again: every step
-- reuses what an earlier attempt made.
CREATE TABLE quiz_draft_approvals (
 id TEXT PRIMARY KEY,
 draft_id TEXT NOT NULL REFERENCES quiz_drafts(id),
 revision_id TEXT NOT NULL REFERENCES quiz_draft_revisions(id),
 term_id TEXT NOT NULL REFERENCES course_terms(id),
 request_id TEXT NOT NULL UNIQUE,
 request_sha256 TEXT NOT NULL,
 input_json TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('pending','running','complete','failed')),
 progress_json TEXT NOT NULL DEFAULT '{}',
 result_json TEXT,
 error TEXT NOT NULL DEFAULT '',
 not_before TEXT NOT NULL DEFAULT '',
 actor TEXT NOT NULL,
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX quiz_draft_approval_once ON quiz_draft_approvals(draft_id,term_id) WHERE status!='failed';
CREATE INDEX quiz_draft_approval_queue ON quiz_draft_approvals(status,not_before);
CREATE TRIGGER quiz_draft_approval_inputs_immutable BEFORE UPDATE OF draft_id,revision_id,term_id,request_id,request_sha256,input_json,actor,created_at ON quiz_draft_approvals
 BEGIN SELECT RAISE(ABORT,'Quiz draft approval inputs are immutable'); END;
CREATE TRIGGER quiz_draft_approval_result_immutable BEFORE UPDATE ON quiz_draft_approvals
 WHEN OLD.status IN ('complete','failed') BEGIN SELECT RAISE(ABORT,'Quiz draft approval results are immutable'); END;
CREATE TRIGGER quiz_draft_approval_retained BEFORE DELETE ON quiz_draft_approvals
 BEGIN SELECT RAISE(ABORT,'Quiz draft approvals are retained'); END;
-- Every request a revision sends is an ordinary cloud call, owned by the revision.
ALTER TABLE frq_cloud_calls ADD COLUMN quiz_revision_id TEXT REFERENCES quiz_draft_revisions(id);
CREATE INDEX frq_cloud_calls_quiz_revision ON frq_cloud_calls(quiz_revision_id,step);
CREATE TRIGGER frq_cloud_call_quiz_revision_identity BEFORE UPDATE OF quiz_revision_id ON frq_cloud_calls
 BEGIN SELECT RAISE(ABORT,'Cloud quiz draft ownership is immutable'); END;
