-- OCR readings belong to the immutable course-wide template. Checks and human
-- confirmations belong to a term grading plan and reference its pinned inputs.
CREATE TABLE frq_template_readings (
 id TEXT PRIMARY KEY, template_id TEXT NOT NULL REFERENCES assignment_templates(id),
 reader_sha256 TEXT NOT NULL, page INTEGER NOT NULL CHECK(page>0),
 attempt INTEGER NOT NULL CHECK(attempt BETWEEN 0 AND 2),
 result_json TEXT NOT NULL, provider_json TEXT NOT NULL, created_at TEXT NOT NULL,
 UNIQUE(template_id,reader_sha256,page,attempt)
);
CREATE TABLE frq_location_checks (
 id TEXT PRIMARY KEY, assignment_id TEXT NOT NULL REFERENCES frq_assignments(id),
 question_id TEXT NOT NULL REFERENCES frq_questions(id),
 input_sha256 TEXT NOT NULL, input_json TEXT NOT NULL, profile_json TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('pending','running','complete','failed','stale')),
 attempt INTEGER NOT NULL DEFAULT 0 CHECK(attempt BETWEEN 0 AND 2),
 result_json TEXT, error TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 UNIQUE(assignment_id,question_id,input_sha256)
);
CREATE INDEX frq_location_pending ON frq_location_checks(status,updated_at);
CREATE TABLE frq_location_reviews (
 id TEXT PRIMARY KEY, check_id TEXT NOT NULL REFERENCES frq_location_checks(id),
 assignment_id TEXT NOT NULL REFERENCES frq_assignments(id),
 question_id TEXT NOT NULL REFERENCES frq_questions(id),
 question_revision_id TEXT NOT NULL REFERENCES frq_question_revisions(id),
 rubric_id TEXT NOT NULL REFERENCES frq_rubric_revisions(id),
 template_id TEXT NOT NULL REFERENCES assignment_templates(id),
 manual_review INTEGER NOT NULL CHECK(manual_review IN (0,1)),
 detail_json TEXT NOT NULL, actor TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX frq_location_confirmed ON frq_location_reviews(assignment_id,question_id,question_revision_id,rubric_id);
CREATE TRIGGER frq_template_reading_immutable BEFORE UPDATE ON frq_template_readings BEGIN SELECT RAISE(ABORT,'Template readings are immutable'); END;
CREATE TRIGGER frq_template_reading_retained BEFORE DELETE ON frq_template_readings BEGIN SELECT RAISE(ABORT,'Template readings are retained'); END;
CREATE TRIGGER frq_location_input_immutable BEFORE UPDATE OF assignment_id,question_id,input_sha256,input_json,profile_json ON frq_location_checks BEGIN SELECT RAISE(ABORT,'Location check inputs are immutable'); END;
CREATE TRIGGER frq_location_check_retained BEFORE DELETE ON frq_location_checks BEGIN SELECT RAISE(ABORT,'Location checks are retained'); END;
CREATE TRIGGER frq_location_review_immutable BEFORE UPDATE ON frq_location_reviews BEGIN SELECT RAISE(ABORT,'Location confirmations are immutable'); END;
CREATE TRIGGER frq_location_review_retained BEFORE DELETE ON frq_location_reviews BEGIN SELECT RAISE(ABORT,'Location confirmations are retained'); END;
CREATE TRIGGER frq_location_review_scope BEFORE INSERT ON frq_location_reviews
 WHEN NOT EXISTS(SELECT 1 FROM frq_location_checks c
 JOIN frq_assignment_questions q ON q.assignment_id=c.assignment_id AND q.question_id=c.question_id
 JOIN frq_question_revisions r ON r.id=NEW.question_revision_id AND r.question_id=q.question_id
 JOIN frq_rubric_revisions b ON b.id=NEW.rubric_id AND b.question_id=q.question_id
 JOIN frq_questions i ON i.id=q.question_id JOIN assignment_versions v ON v.id=i.assignment_version_id
 JOIN assignment_templates t ON t.assignment_id=v.assignment_id AND t.id=NEW.template_id
 WHERE c.id=NEW.check_id AND c.assignment_id=NEW.assignment_id AND c.question_id=NEW.question_id)
 BEGIN SELECT RAISE(ABORT,'Location confirmation belongs to another question or template'); END;
