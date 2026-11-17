-- Optional quality policy belongs to the existing TERM grading plan. It references
-- course-wide immutable rubrics; it never edits their scorer-enforced rules.
CREATE TABLE frq_rubric_quality_options (
 assignment_id TEXT PRIMARY KEY REFERENCES frq_assignments(id),
 enabled INTEGER NOT NULL CHECK(enabled IN (0,1)), version INTEGER NOT NULL,
 review_id TEXT, updated_at TEXT NOT NULL
);
CREATE TABLE frq_rubric_quality_checks (
 id TEXT PRIMARY KEY, assignment_id TEXT NOT NULL REFERENCES frq_assignments(id),
 request_id TEXT NOT NULL UNIQUE, request_sha256 TEXT NOT NULL, input_sha256 TEXT NOT NULL,
 input_json TEXT NOT NULL, profile_json TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('pending','running','complete','failed')),
 result_json TEXT, provider_json TEXT, error TEXT NOT NULL DEFAULT '',
 not_before TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX frq_rubric_quality_queue ON frq_rubric_quality_checks(status,not_before);
CREATE INDEX frq_rubric_quality_assignment ON frq_rubric_quality_checks(assignment_id,created_at);
CREATE TABLE frq_rubric_quality_reviews (
 id TEXT PRIMARY KEY, assignment_id TEXT NOT NULL REFERENCES frq_assignments(id),
 check_id TEXT NOT NULL REFERENCES frq_rubric_quality_checks(id), input_sha256 TEXT NOT NULL,
 policy_json TEXT NOT NULL, actor TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TRIGGER frq_rubric_quality_review_immutable BEFORE UPDATE ON frq_rubric_quality_reviews
 BEGIN SELECT RAISE(ABORT,'Rubric quality confirmations are immutable'); END;
CREATE TRIGGER frq_rubric_quality_review_retained BEFORE DELETE ON frq_rubric_quality_reviews
 BEGIN SELECT RAISE(ABORT,'Rubric quality confirmations are retained'); END;
CREATE TRIGGER frq_rubric_quality_inputs_immutable BEFORE UPDATE OF assignment_id,request_id,request_sha256,input_sha256,input_json,profile_json ON frq_rubric_quality_checks
 BEGIN SELECT RAISE(ABORT,'Rubric quality inputs are immutable'); END;
CREATE TRIGGER frq_rubric_quality_result_immutable BEFORE UPDATE ON frq_rubric_quality_checks
 WHEN OLD.status IN ('complete','failed') BEGIN SELECT RAISE(ABORT,'Rubric quality results are immutable'); END;
CREATE TRIGGER frq_rubric_quality_check_retained BEFORE DELETE ON frq_rubric_quality_checks
 BEGIN SELECT RAISE(ABORT,'Rubric quality checks are retained'); END;
ALTER TABLE frq_cloud_calls ADD COLUMN rubric_check_id TEXT REFERENCES frq_rubric_quality_checks(id);
CREATE INDEX frq_cloud_calls_rubric_check ON frq_cloud_calls(rubric_check_id,step);
CREATE TRIGGER frq_cloud_call_rubric_identity BEFORE UPDATE OF rubric_check_id ON frq_cloud_calls
 BEGIN SELECT RAISE(ABORT,'Cloud rubric check ownership is immutable'); END;
