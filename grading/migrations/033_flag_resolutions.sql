-- Flag resolution (requirement, 2026-09-28). A resolution belongs to the TERM grading plan. Its input is a snapshot of the
-- held responses it may rule on, by reference to each one's current result; its result is each response's ruling and the proposed
-- clarifications. A ruling finalizes nothing by itself: the grades it settles are scored again by an ordinary "Recalculate points" batch
-- of the plan (`job_id`, one per source batch, named in the result), which runs every check and the ordinary acceptance gate. Inputs and
-- results are immutable once written; resolutions are retained.
CREATE TABLE frq_flag_resolutions (
 id TEXT PRIMARY KEY,
 assignment_id TEXT NOT NULL REFERENCES frq_assignments(id),
 request_id TEXT NOT NULL UNIQUE,
 request_sha256 TEXT NOT NULL,
 input_json TEXT NOT NULL,
 profile_json TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('pending','running','complete','failed')),
 cancelled INTEGER NOT NULL DEFAULT 0 CHECK(cancelled IN (0,1)),
 result_json TEXT,
 error TEXT NOT NULL DEFAULT '',
 not_before TEXT NOT NULL DEFAULT '',
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL
);
CREATE INDEX frq_flag_resolution_queue ON frq_flag_resolutions(status,not_before);
CREATE INDEX frq_flag_resolution_plan ON frq_flag_resolutions(assignment_id,created_at);
CREATE TRIGGER frq_flag_resolution_inputs_immutable BEFORE UPDATE OF assignment_id,request_id,request_sha256,input_json,profile_json ON frq_flag_resolutions
 BEGIN SELECT RAISE(ABORT,'Flag resolution inputs are immutable'); END;
CREATE TRIGGER frq_flag_resolution_result_immutable BEFORE UPDATE ON frq_flag_resolutions
 WHEN OLD.status IN ('complete','failed') BEGIN SELECT RAISE(ABORT,'Flag resolution results are immutable'); END;
CREATE TRIGGER frq_flag_resolution_retained BEFORE DELETE ON frq_flag_resolutions
 BEGIN SELECT RAISE(ABORT,'Flag resolutions are retained'); END;
-- Every request a resolution sends is an ordinary cloud call, owned by the resolution.
ALTER TABLE frq_cloud_calls ADD COLUMN flag_resolution_id TEXT REFERENCES frq_flag_resolutions(id);
CREATE INDEX frq_cloud_calls_flag_resolution ON frq_cloud_calls(flag_resolution_id,step);
CREATE TRIGGER frq_cloud_call_flag_resolution_identity BEFORE UPDATE OF flag_resolution_id ON frq_cloud_calls
 BEGIN SELECT RAISE(ABORT,'Cloud flag resolution ownership is immutable'); END;
