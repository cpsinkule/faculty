-- Term-owned receipts for explicit rubric uploads. The imported question and
-- rubric remain course-wide assignment-version revisions, pinned by that term.
CREATE TABLE frq_rubric_uploads (
 assignment_id TEXT NOT NULL REFERENCES frq_assignments(id),
 request_id TEXT NOT NULL, input_sha256 TEXT NOT NULL,
 question_id TEXT NOT NULL REFERENCES frq_questions(id),
 rubric_id TEXT NOT NULL REFERENCES frq_rubric_revisions(id),
 source_artifact_id TEXT NOT NULL REFERENCES inbox_artifacts(id),
 created_at TEXT NOT NULL,
 PRIMARY KEY(assignment_id,request_id), UNIQUE(assignment_id,input_sha256)
);
CREATE TRIGGER frq_rubric_upload_scope BEFORE INSERT ON frq_rubric_uploads
 WHEN NOT EXISTS(SELECT 1 FROM frq_assignment_questions p
 JOIN frq_rubric_revisions r ON r.id=NEW.rubric_id AND r.question_id=p.question_id
 WHERE p.assignment_id=NEW.assignment_id AND p.question_id=NEW.question_id
 AND p.rubric_id=NEW.rubric_id AND r.source_artifact_id=NEW.source_artifact_id)
 BEGIN SELECT RAISE(ABORT,'Rubric upload must reference its term pin and retained source'); END;
CREATE TRIGGER frq_rubric_upload_immutable BEFORE UPDATE ON frq_rubric_uploads
 BEGIN SELECT RAISE(ABORT,'Rubric upload receipt is immutable'); END;
CREATE TRIGGER frq_rubric_upload_retained BEFORE DELETE ON frq_rubric_uploads
 BEGIN SELECT RAISE(ABORT,'Rubric upload receipt is retained'); END;
