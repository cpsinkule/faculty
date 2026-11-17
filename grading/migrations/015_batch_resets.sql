-- Resetting a batch withdraws its unreviewed proposals so the same responses can be graded again with
-- other models or settings. Nothing is erased: results, steps, calls, costs and acceptances stay where
-- they are, and only a response's pointer to its current proposal is cleared. These records say which
-- pointer was cleared, by which request, and whether it was put back. They belong to the term grading
-- plan, like the batch they describe.
CREATE TABLE frq_job_resets (
 id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES frq_jobs(id), assignment_id TEXT NOT NULL REFERENCES frq_assignments(id),
 request_id TEXT NOT NULL UNIQUE, request_sha256 TEXT NOT NULL, detail_json TEXT NOT NULL, actor TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX frq_job_resets_job ON frq_job_resets(job_id,created_at);
CREATE TABLE frq_job_reset_responses (
 reset_id TEXT NOT NULL REFERENCES frq_job_resets(id), response_id TEXT NOT NULL REFERENCES frq_responses(id),
 result_id TEXT NOT NULL REFERENCES frq_results(id), PRIMARY KEY(reset_id,response_id)
);
CREATE INDEX frq_job_reset_responses_response ON frq_job_reset_responses(response_id);
CREATE TABLE frq_job_reset_restores (
 reset_id TEXT PRIMARY KEY REFERENCES frq_job_resets(id), detail_json TEXT NOT NULL, actor TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TRIGGER frq_job_reset_immutable BEFORE UPDATE ON frq_job_resets
 BEGIN SELECT RAISE(ABORT,'Batch resets are immutable'); END;
CREATE TRIGGER frq_job_reset_retained BEFORE DELETE ON frq_job_resets
 BEGIN SELECT RAISE(ABORT,'Batch resets are retained'); END;
CREATE TRIGGER frq_job_reset_response_immutable BEFORE UPDATE ON frq_job_reset_responses
 BEGIN SELECT RAISE(ABORT,'Batch resets are immutable'); END;
CREATE TRIGGER frq_job_reset_response_retained BEFORE DELETE ON frq_job_reset_responses
 BEGIN SELECT RAISE(ABORT,'Batch resets are retained'); END;
CREATE TRIGGER frq_job_reset_restore_immutable BEFORE UPDATE ON frq_job_reset_restores
 BEGIN SELECT RAISE(ABORT,'Batch resets are immutable'); END;
CREATE TRIGGER frq_job_reset_restore_retained BEFORE DELETE ON frq_job_reset_restores
 BEGIN SELECT RAISE(ABORT,'Batch resets are retained'); END;
