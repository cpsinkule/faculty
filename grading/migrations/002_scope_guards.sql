-- Enforce permanent ownership and immutable run inputs at the catalog boundary.
CREATE TRIGGER frq_question_immutable BEFORE UPDATE ON frq_questions BEGIN SELECT RAISE(ABORT,'Question identity is immutable'); END;
CREATE TRIGGER frq_question_retained BEFORE DELETE ON frq_questions BEGIN SELECT RAISE(ABORT,'Question identity is retained'); END;
CREATE TRIGGER frq_plan_owner BEFORE UPDATE OF event_id ON frq_assignments BEGIN SELECT RAISE(ABORT,'Grading plan owner is immutable'); END;
CREATE TRIGGER frq_pin_revisions_insert BEFORE INSERT ON frq_assignment_questions
 WHEN NOT EXISTS(SELECT 1 FROM frq_question_revisions WHERE id=NEW.question_revision_id AND question_id=NEW.question_id)
 OR (NEW.rubric_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM frq_rubric_revisions WHERE id=NEW.rubric_id AND question_id=NEW.question_id))
 BEGIN SELECT RAISE(ABORT,'Question and rubric revisions must match the pinned question'); END;
CREATE TRIGGER frq_pin_revisions_update BEFORE UPDATE ON frq_assignment_questions
 WHEN NEW.assignment_id!=OLD.assignment_id OR NEW.question_id!=OLD.question_id
 OR NOT EXISTS(SELECT 1 FROM frq_question_revisions WHERE id=NEW.question_revision_id AND question_id=NEW.question_id)
 OR (NEW.rubric_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM frq_rubric_revisions WHERE id=NEW.rubric_id AND question_id=NEW.question_id))
 BEGIN SELECT RAISE(ABORT,'Question and rubric revisions must match the pinned question'); END;
CREATE TRIGGER frq_response_scope BEFORE INSERT ON frq_responses
 WHEN NOT EXISTS(SELECT 1 FROM frq_assignments g JOIN exam_participants p ON p.event_id=g.event_id
 JOIN frq_assignment_questions q ON q.assignment_id=g.id WHERE g.id=NEW.assignment_id
 AND p.section_id=NEW.section_id AND p.enrollment_id=NEW.enrollment_id AND q.question_id=NEW.question_id)
 BEGIN SELECT RAISE(ABORT,'Response must belong to an exam participant and question'); END;
CREATE TRIGGER frq_response_owner BEFORE UPDATE OF assignment_id,question_id,section_id,enrollment_id ON frq_responses
 BEGIN SELECT RAISE(ABORT,'Response owner is immutable'); END;
CREATE TRIGGER frq_task_scope BEFORE INSERT ON frq_tasks
 WHEN NOT EXISTS(SELECT 1 FROM frq_responses r JOIN frq_jobs j ON j.assignment_id=r.assignment_id
 JOIN frq_question_revisions q ON q.question_id=r.question_id JOIN frq_rubric_revisions b ON b.question_id=r.question_id
 WHERE r.id=NEW.response_id AND j.id=NEW.job_id AND q.id=NEW.question_revision_id AND b.id=NEW.rubric_id)
 BEGIN SELECT RAISE(ABORT,'Task must reference the same response question and term'); END;
CREATE TRIGGER frq_task_inputs BEFORE UPDATE OF job_id,response_id,question_revision_id,rubric_id,source_json,source_sha256,reuse_result_id,human_transcript_json ON frq_tasks
 BEGIN SELECT RAISE(ABORT,'Task inputs are immutable'); END;
CREATE TRIGGER frq_result_scope BEFORE INSERT ON frq_results
 WHEN NOT EXISTS(SELECT 1 FROM frq_tasks WHERE id=NEW.task_id AND response_id=NEW.response_id)
 BEGIN SELECT RAISE(ABORT,'Result must reference its task response'); END;
CREATE TRIGGER frq_review_scope BEFORE INSERT ON frq_reviews
 WHEN NOT EXISTS(SELECT 1 FROM frq_results r JOIN frq_tasks t ON t.id=r.task_id
 WHERE r.id=NEW.result_id AND r.response_id=NEW.response_id AND t.rubric_id=NEW.rubric_id
 AND t.source_sha256=NEW.source_sha256 AND t.status='complete')
 BEGIN SELECT RAISE(ABORT,'Review must reference a completed result and its exact rubric and source'); END;
