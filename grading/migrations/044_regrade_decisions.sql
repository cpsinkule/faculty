-- Regrade requests answered in one panel on the Return stage (requirement, 2026-10-07). Owned by the TERM grading plan (frq_assignments) the request was
-- matched to; the request itself stays the communications record's captured message (comm_messages, referenced by id).

-- CERTIFY JUST THIS CHANGE (asked, 2026-10-07): a certification identical to the plan's latest except one response's grade names the
-- certification it amends. NULL for an ordinary certification. The certified archive's cutoff stays the newest ordinary one.
ALTER TABLE frq_certifications ADD COLUMN amends_id TEXT REFERENCES frq_certifications(id);

-- A reply drafted by a model for the instructor to edit ("Drafted by a model", asked 2026-10-07): one row per draft asked for, an
-- ordinary cloud call owned by it (`frq_cloud_calls.regrade_draft_id`). The inputs never change; the answer is written once.
CREATE TABLE frq_regrade_drafts (
 id TEXT PRIMARY KEY,
 assignment_id TEXT NOT NULL REFERENCES frq_assignments(id),
 response_id TEXT NOT NULL REFERENCES frq_responses(id),
 message_id TEXT NOT NULL CHECK(length(message_id) BETWEEN 1 AND 64),
 kind TEXT NOT NULL CHECK(kind IN ('keep','change')),
 input_sha256 TEXT NOT NULL CHECK(length(input_sha256) = 64),
 input_json TEXT NOT NULL,
 profile_json TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('pending','running','complete','failed')),
 reply TEXT CHECK(reply IS NULL OR length(reply) <= 5000),
 assessment TEXT CHECK(assessment IS NULL OR length(assessment) <= 1000),
 error TEXT NOT NULL DEFAULT '' CHECK(length(error) <= 1000),
 attempts INTEGER NOT NULL DEFAULT 0,
 not_before TEXT,
 actor TEXT NOT NULL,
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL
);
CREATE INDEX frq_regrade_drafts_message ON frq_regrade_drafts(message_id, created_at);
CREATE INDEX frq_regrade_drafts_queue ON frq_regrade_drafts(status, not_before);
CREATE TRIGGER frq_regrade_draft_inputs_immutable BEFORE UPDATE OF id,assignment_id,response_id,message_id,kind,input_sha256,input_json,profile_json,actor,created_at
 ON frq_regrade_drafts BEGIN SELECT RAISE(ABORT,'A regrade draft''s inputs are immutable'); END;
CREATE TRIGGER frq_regrade_draft_settled BEFORE UPDATE ON frq_regrade_drafts WHEN OLD.status IN ('complete','failed')
 BEGIN SELECT RAISE(ABORT,'A settled regrade draft is immutable'); END;
CREATE TRIGGER frq_regrade_drafts_retained BEFORE DELETE ON frq_regrade_drafts BEGIN SELECT RAISE(ABORT,'Regrade drafts are retained'); END;
ALTER TABLE frq_cloud_calls ADD COLUMN regrade_draft_id TEXT REFERENCES frq_regrade_drafts(id);
CREATE INDEX frq_cloud_calls_regrade_draft ON frq_cloud_calls(regrade_draft_id, step);
CREATE TRIGGER frq_cloud_call_regrade_draft_identity BEFORE UPDATE OF regrade_draft_id ON frq_cloud_calls
 BEGIN SELECT RAISE(ABORT,'Cloud regrade draft ownership is immutable'); END;

-- APPLY: the instructor's one approval of everything a decision changes (that grade on Gradescope, the reply, the gradebook through
-- a certification of just this change, the student's Canvas cell). The approved plan never changes; each step appends what it did,
-- naming its own receipt (the send, the reply, the certification, the Canvas send). The page posts each next step while it is open.
CREATE TABLE frq_regrade_applies (
 id TEXT PRIMARY KEY,
 assignment_id TEXT NOT NULL REFERENCES frq_assignments(id),
 response_id TEXT REFERENCES frq_responses(id),
 message_id TEXT NOT NULL CHECK(length(message_id) BETWEEN 1 AND 64),
 request_id TEXT NOT NULL CHECK(length(request_id) BETWEEN 8 AND 100),
 request_sha256 TEXT NOT NULL CHECK(length(request_sha256) = 64),
 fingerprint TEXT NOT NULL CHECK(length(fingerprint) = 64),
 plan_json TEXT NOT NULL,
 actor TEXT NOT NULL,
 created_at TEXT NOT NULL,
 UNIQUE(assignment_id, request_id)
);
CREATE INDEX frq_regrade_applies_message ON frq_regrade_applies(message_id, created_at);
CREATE TRIGGER frq_regrade_applies_immutable BEFORE UPDATE ON frq_regrade_applies BEGIN SELECT RAISE(ABORT,'A regrade apply is immutable'); END;
CREATE TRIGGER frq_regrade_applies_retained BEFORE DELETE ON frq_regrade_applies BEGIN SELECT RAISE(ABORT,'Regrade applies are retained'); END;

CREATE TABLE frq_regrade_apply_steps (
 id TEXT PRIMARY KEY,
 apply_id TEXT NOT NULL REFERENCES frq_regrade_applies(id),
 step TEXT NOT NULL CHECK(step IN ('gradescope','reply','gradebook','canvas','cancel')),
 status TEXT NOT NULL CHECK(status IN ('started','done','already','stopped')),
 detail TEXT NOT NULL DEFAULT '' CHECK(length(detail) <= 1000),
 receipt_json TEXT NOT NULL DEFAULT '{}',
 created_at TEXT NOT NULL
);
CREATE INDEX frq_regrade_apply_steps_apply ON frq_regrade_apply_steps(apply_id, created_at);
CREATE TRIGGER frq_regrade_apply_steps_immutable BEFORE UPDATE ON frq_regrade_apply_steps BEGIN SELECT RAISE(ABORT,'Regrade apply steps are append-only'); END;
CREATE TRIGGER frq_regrade_apply_steps_retained BEFORE DELETE ON frq_regrade_apply_steps BEGIN SELECT RAISE(ABORT,'Regrade apply steps are retained'); END;
