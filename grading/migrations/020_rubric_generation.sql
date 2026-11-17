-- Rubrics drafted by a model from a blank exam (requirement, 2026-09-23): the alternative to uploading Gradescope CSVs.
-- A generation belongs to the TERM grading plan. It references the course-wide template it read and the printed document
-- version (and exam version) its questions will describe. Its result is a DRAFT: only the instructor's explicit acceptance
-- creates course-wide questions and rubric revisions, each with an ordinary immutable `frq_rubric_uploads` receipt.
CREATE TABLE frq_rubric_generations (
 id TEXT PRIMARY KEY, assignment_id TEXT NOT NULL REFERENCES frq_assignments(id),
 request_id TEXT NOT NULL UNIQUE, request_sha256 TEXT NOT NULL,
 document_id TEXT NOT NULL REFERENCES assignment_versions(id), exam_version TEXT NOT NULL,
 template_id TEXT NOT NULL REFERENCES assignment_templates(id),
 input_json TEXT NOT NULL, profile_json TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('pending','running','complete','failed')),
 cancelled INTEGER NOT NULL DEFAULT 0 CHECK(cancelled IN (0,1)),
 result_json TEXT, provider_json TEXT, error TEXT NOT NULL DEFAULT '',
 not_before TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX frq_rubric_generation_queue ON frq_rubric_generations(status,not_before);
CREATE INDEX frq_rubric_generation_plan ON frq_rubric_generations(assignment_id,document_id,exam_version,created_at);
CREATE TRIGGER frq_rubric_generation_inputs_immutable BEFORE UPDATE OF assignment_id,request_id,request_sha256,document_id,exam_version,template_id,input_json,profile_json ON frq_rubric_generations
 BEGIN SELECT RAISE(ABORT,'Rubric generation inputs are immutable'); END;
CREATE TRIGGER frq_rubric_generation_result_immutable BEFORE UPDATE ON frq_rubric_generations
 WHEN OLD.status IN ('complete','failed') BEGIN SELECT RAISE(ABORT,'Rubric generation results are immutable'); END;
CREATE TRIGGER frq_rubric_generation_retained BEFORE DELETE ON frq_rubric_generations
 BEGIN SELECT RAISE(ABORT,'Rubric generations are retained'); END;
-- Every request a generation sends is an ordinary cloud call, owned by the generation.
ALTER TABLE frq_cloud_calls ADD COLUMN rubric_generation_id TEXT REFERENCES frq_rubric_generations(id);
CREATE INDEX frq_cloud_calls_rubric_generation ON frq_cloud_calls(rubric_generation_id,step);
CREATE TRIGGER frq_cloud_call_rubric_generation_identity BEFORE UPDATE OF rubric_generation_id ON frq_cloud_calls
 BEGIN SELECT RAISE(ABORT,'Cloud rubric generation ownership is immutable'); END;
