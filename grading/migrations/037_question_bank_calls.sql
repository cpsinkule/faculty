-- A course's question bank (question_bank/, AGENTS.md "Question bank"): tagging its questions and reading questions out of a PDF are
-- ordinary cloud calls owned by their bank run, as a quiz draft's are owned by its revision. The run is the question bank's own record
-- (migration `question_bank/001_bank`, applied after grading's), so this column carries no foreign key.
ALTER TABLE frq_cloud_calls ADD COLUMN bank_run_id TEXT;
CREATE INDEX frq_cloud_calls_bank_run ON frq_cloud_calls(bank_run_id,step);
CREATE TRIGGER frq_cloud_call_bank_run_identity BEFORE UPDATE OF bank_run_id ON frq_cloud_calls
 BEGIN SELECT RAISE(ABORT,'Cloud bank run ownership is immutable'); END;
