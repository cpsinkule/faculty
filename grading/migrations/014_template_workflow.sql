-- The term plan owns the instructor's template choice and warning acknowledgement;
-- the referenced templates and document versions retain their course-wide owners.
CREATE TABLE frq_template_reviews (
 id TEXT PRIMARY KEY, assignment_id TEXT NOT NULL REFERENCES frq_assignments(id),
 input_sha256 TEXT NOT NULL, selections_json TEXT NOT NULL,
 warning TEXT NOT NULL, actor TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX frq_template_reviews_plan ON frq_template_reviews(assignment_id,created_at);
CREATE TRIGGER frq_template_review_immutable BEFORE UPDATE ON frq_template_reviews
 BEGIN SELECT RAISE(ABORT,'Template confirmations are immutable'); END;
CREATE TRIGGER frq_template_review_retained BEFORE DELETE ON frq_template_reviews
 BEGIN SELECT RAISE(ABORT,'Template confirmations are retained'); END;
