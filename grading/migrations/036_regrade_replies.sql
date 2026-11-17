-- Regrade requests on the Review step (AGENTS.md "Regrade requests"): the instructor's replies to Gradescope regrade requests, posted
-- from Faculty, and the requests they marked handled without a reply. Owned by the TERM grading plan (frq_assignments) the request was
-- matched to; the request itself stays the communications record's captured message (comm_messages, referenced by id). Append-only:
-- a reply is recorded BEFORE anything is sent to Gradescope, its outcome after (accepted only once the page reads back with it), and
-- an uncertain one is settled by reading, never by sending again.
CREATE TABLE frq_regrade_replies (
 id TEXT PRIMARY KEY,
 assignment_id TEXT NOT NULL REFERENCES frq_assignments(id),
 response_id TEXT REFERENCES frq_responses(id),
 message_id TEXT NOT NULL CHECK(length(message_id) BETWEEN 1 AND 64),
 course_id TEXT NOT NULL,
 question_id TEXT NOT NULL,
 question_submission_id TEXT NOT NULL,
 gradescope_request_id TEXT NOT NULL,
 reply TEXT NOT NULL CHECK(length(reply) BETWEEN 1 AND 5000),
 reply_sha256 TEXT NOT NULL CHECK(length(reply_sha256) = 64),
 fingerprint TEXT NOT NULL CHECK(length(fingerprint) = 64),
 request_id TEXT NOT NULL CHECK(length(request_id) BETWEEN 1 AND 100),
 actor TEXT NOT NULL,
 created_at TEXT NOT NULL,
 UNIQUE(assignment_id, request_id)
);
CREATE INDEX frq_regrade_replies_message ON frq_regrade_replies(message_id, created_at);
CREATE TRIGGER frq_regrade_replies_immutable BEFORE UPDATE ON frq_regrade_replies BEGIN SELECT RAISE(ABORT,'Regrade replies are append-only'); END;
CREATE TRIGGER frq_regrade_replies_retained BEFORE DELETE ON frq_regrade_replies BEGIN SELECT RAISE(ABORT,'Regrade replies are retained'); END;

CREATE TABLE frq_regrade_reply_outcomes (
 id TEXT PRIMARY KEY,
 reply_id TEXT NOT NULL REFERENCES frq_regrade_replies(id),
 status TEXT NOT NULL CHECK(status IN ('accepted','failed','uncertain')),
 detail TEXT NOT NULL DEFAULT '' CHECK(length(detail) <= 1000),
 created_at TEXT NOT NULL
);
CREATE INDEX frq_regrade_reply_outcomes_reply ON frq_regrade_reply_outcomes(reply_id, created_at);
CREATE TRIGGER frq_regrade_reply_outcomes_immutable BEFORE UPDATE ON frq_regrade_reply_outcomes BEGIN SELECT RAISE(ABORT,'Regrade reply outcomes are append-only'); END;
CREATE TRIGGER frq_regrade_reply_outcomes_retained BEFORE DELETE ON frq_regrade_reply_outcomes BEGIN SELECT RAISE(ABORT,'Regrade reply outcomes are retained'); END;

CREATE TABLE frq_regrade_marks (
 id TEXT PRIMARY KEY,
 assignment_id TEXT NOT NULL REFERENCES frq_assignments(id),
 message_id TEXT NOT NULL CHECK(length(message_id) BETWEEN 1 AND 64),
 action TEXT NOT NULL CHECK(action IN ('handled','reopened')),
 note TEXT NOT NULL DEFAULT '' CHECK(length(note) <= 1000),
 request_id TEXT NOT NULL CHECK(length(request_id) BETWEEN 1 AND 100),
 actor TEXT NOT NULL,
 created_at TEXT NOT NULL,
 UNIQUE(assignment_id, request_id)
);
CREATE INDEX frq_regrade_marks_message ON frq_regrade_marks(message_id, created_at);
CREATE TRIGGER frq_regrade_marks_immutable BEFORE UPDATE ON frq_regrade_marks BEGIN SELECT RAISE(ABORT,'Regrade marks are append-only'); END;
CREATE TRIGGER frq_regrade_marks_retained BEFORE DELETE ON frq_regrade_marks BEGIN SELECT RAISE(ABORT,'Regrade marks are retained'); END;
