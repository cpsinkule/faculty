-- Every grade saved for training (requirement, 2026-09-26).
-- One immutable example per grading DECISION of a response of the TERM grading plan: an instructor review that decides
-- (approved, manual, unreadable or rejected; a draft decides nothing) or an automatic finalization (never a trial acceptance).
-- `example_json` holds what the decision was about, as it was: references to the immutable crop images of the source scan (never
-- copies), the readings, the AI's transcript, rubric lines and score, the final ones, the instructor's feedback and the models and
-- prompts behind the proposal. The question and rubric revisions it was graded under are referenced (immutable, course-wide).
-- Nothing here decides, finalizes or changes a grade. A later decision never rewrites an example; the export says which it superseded.
CREATE TABLE frq_training_examples (
 id TEXT PRIMARY KEY,
 assignment_id TEXT NOT NULL REFERENCES frq_assignments(id),
 response_id TEXT NOT NULL REFERENCES frq_responses(id),
 decision_kind TEXT NOT NULL CHECK(decision_kind IN ('review','automatic')),
 decision_id TEXT NOT NULL,
 result_id TEXT NOT NULL REFERENCES frq_results(id),
 question_revision_id TEXT NOT NULL REFERENCES frq_question_revisions(id),
 rubric_id TEXT NOT NULL REFERENCES frq_rubric_revisions(id),
 source_sha256 TEXT NOT NULL,
 label TEXT NOT NULL CHECK(label IN ('instructor','automatic')),
 status TEXT NOT NULL CHECK(status IN ('approved','manual','unreadable','rejected','automatic')),
 rule TEXT NOT NULL,
 example_json TEXT NOT NULL,
 sha256 TEXT NOT NULL,
 decided_at TEXT NOT NULL,
 created_at TEXT NOT NULL,
 UNIQUE(decision_kind,decision_id),
 CHECK((decision_kind='review')=(label='instructor') AND (label='automatic')=(status='automatic'))
);
CREATE INDEX frq_training_examples_plan ON frq_training_examples(assignment_id,response_id,decided_at);
CREATE TRIGGER frq_training_example_scope BEFORE INSERT ON frq_training_examples
WHEN NOT EXISTS(SELECT 1 FROM frq_responses r WHERE r.id=NEW.response_id AND r.assignment_id=NEW.assignment_id)
  OR NOT EXISTS(SELECT 1 FROM frq_results x JOIN frq_tasks t ON t.id=x.task_id WHERE x.id=NEW.result_id AND x.response_id=NEW.response_id
                AND t.rubric_id=NEW.rubric_id AND t.question_revision_id=NEW.question_revision_id AND t.source_sha256=NEW.source_sha256)
  OR (NEW.decision_kind='review' AND NOT EXISTS(SELECT 1 FROM frq_reviews v WHERE v.id=NEW.decision_id AND v.response_id=NEW.response_id
                AND v.result_id=NEW.result_id AND v.status=NEW.status))
  OR (NEW.decision_kind='automatic' AND NOT EXISTS(SELECT 1 FROM frq_all_acceptances a WHERE a.id=NEW.decision_id AND a.kind='automatic'
                AND a.response_id=NEW.response_id AND a.result_id=NEW.result_id))
BEGIN SELECT RAISE(ABORT,'A training example is one decision of its own response, as that decision was made'); END;
CREATE TRIGGER frq_training_example_immutable BEFORE UPDATE ON frq_training_examples BEGIN SELECT RAISE(ABORT,'Training examples are immutable'); END;
CREATE TRIGGER frq_training_example_retained BEFORE DELETE ON frq_training_examples BEGIN SELECT RAISE(ABORT,'Training examples are retained'); END;
