-- Rubric lines prepared by the instructor before the rubric reviewer reads them (requirement, 2026-09-24). Edited wording and
-- points are ordinary immutable course-wide rubric revisions (compiler `instructor-edited`). What lives here belongs to the TERM
-- grading plan: the instructor's comment on each line, guidance the reviewer folds into that line's clarification, and the
-- instructor's confirmation that a question's exact rubric revision and comments are ready for the reviewer. Both are append-only:
-- the latest comment of a line is its current one (an empty comment clears it), and a confirmation names what it confirmed.
CREATE TABLE frq_rubric_line_comments (
 id TEXT PRIMARY KEY, assignment_id TEXT NOT NULL REFERENCES frq_assignments(id),
 question_id TEXT NOT NULL REFERENCES frq_questions(id), item_id TEXT NOT NULL,
 rubric_id TEXT NOT NULL REFERENCES frq_rubric_revisions(id),
 comment TEXT NOT NULL, actor TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX frq_rubric_line_comments_line ON frq_rubric_line_comments(assignment_id,question_id,item_id,created_at);
CREATE TRIGGER frq_rubric_line_comment_scope BEFORE INSERT ON frq_rubric_line_comments
 WHEN NOT EXISTS(SELECT 1 FROM frq_assignment_questions p JOIN frq_rubric_revisions r ON r.question_id=p.question_id
   WHERE p.assignment_id=NEW.assignment_id AND p.question_id=NEW.question_id AND r.id=NEW.rubric_id)
 BEGIN SELECT RAISE(ABORT,'A rubric line comment must belong to a question and rubric of its grading plan'); END;
CREATE TRIGGER frq_rubric_line_comment_immutable BEFORE UPDATE ON frq_rubric_line_comments
 BEGIN SELECT RAISE(ABORT,'Rubric line comments are append-only'); END;
CREATE TRIGGER frq_rubric_line_comment_retained BEFORE DELETE ON frq_rubric_line_comments
 BEGIN SELECT RAISE(ABORT,'Rubric line comments are retained'); END;
CREATE TABLE frq_rubric_review_ready (
 id TEXT PRIMARY KEY, assignment_id TEXT NOT NULL REFERENCES frq_assignments(id),
 question_id TEXT NOT NULL REFERENCES frq_questions(id), rubric_id TEXT NOT NULL REFERENCES frq_rubric_revisions(id),
 comments_sha256 TEXT NOT NULL, actor TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX frq_rubric_review_ready_question ON frq_rubric_review_ready(assignment_id,question_id,created_at);
CREATE TRIGGER frq_rubric_review_ready_scope BEFORE INSERT ON frq_rubric_review_ready
 WHEN NOT EXISTS(SELECT 1 FROM frq_assignment_questions WHERE assignment_id=NEW.assignment_id AND question_id=NEW.question_id AND rubric_id=NEW.rubric_id)
 BEGIN SELECT RAISE(ABORT,'A review confirmation must name the rubric its grading plan pins'); END;
CREATE TRIGGER frq_rubric_review_ready_immutable BEFORE UPDATE ON frq_rubric_review_ready
 BEGIN SELECT RAISE(ABORT,'Rubric review confirmations are append-only'); END;
CREATE TRIGGER frq_rubric_review_ready_retained BEFORE DELETE ON frq_rubric_review_ready
 BEGIN SELECT RAISE(ABORT,'Rubric review confirmations are retained'); END;
