-- Final Quality Audit (requirement, 2026-09-27). An audit belongs to the TERM grading plan. Its input is a snapshot of the plan's FINAL
-- grades as they stood when it was requested (each response's lines, score and final answers by reference to its decision, and a
-- short excerpt of its transcript); its result is a list of findings, each naming the responses and what looks inconsistent. Nothing
-- here changes, approves or finalizes a grade: the instructor acts on a finding through the ordinary review editor or a batch
-- adjustment. Inputs and results are immutable once written; audits are retained.
CREATE TABLE frq_quality_audits (
 id TEXT PRIMARY KEY,
 assignment_id TEXT NOT NULL REFERENCES frq_assignments(id),
 request_id TEXT NOT NULL UNIQUE,
 request_sha256 TEXT NOT NULL,
 input_json TEXT NOT NULL,
 profile_json TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('pending','running','complete','failed')),
 cancelled INTEGER NOT NULL DEFAULT 0 CHECK(cancelled IN (0,1)),
 result_json TEXT,
 provider_json TEXT,
 error TEXT NOT NULL DEFAULT '',
 not_before TEXT NOT NULL DEFAULT '',
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL
);
CREATE INDEX frq_quality_audit_queue ON frq_quality_audits(status,not_before);
CREATE INDEX frq_quality_audit_plan ON frq_quality_audits(assignment_id,created_at);
CREATE TRIGGER frq_quality_audit_inputs_immutable BEFORE UPDATE OF assignment_id,request_id,request_sha256,input_json,profile_json ON frq_quality_audits
 BEGIN SELECT RAISE(ABORT,'Quality audit inputs are immutable'); END;
CREATE TRIGGER frq_quality_audit_result_immutable BEFORE UPDATE ON frq_quality_audits
 WHEN OLD.status IN ('complete','failed') BEGIN SELECT RAISE(ABORT,'Quality audit results are immutable'); END;
CREATE TRIGGER frq_quality_audit_retained BEFORE DELETE ON frq_quality_audits
 BEGIN SELECT RAISE(ABORT,'Quality audits are retained'); END;
-- Every request an audit sends is an ordinary cloud call, owned by the audit.
ALTER TABLE frq_cloud_calls ADD COLUMN quality_audit_id TEXT REFERENCES frq_quality_audits(id);
CREATE INDEX frq_cloud_calls_quality_audit ON frq_cloud_calls(quality_audit_id,step);
CREATE TRIGGER frq_cloud_call_quality_audit_identity BEFORE UPDATE OF quality_audit_id ON frq_cloud_calls
 BEGIN SELECT RAISE(ABORT,'Cloud quality audit ownership is immutable'); END;
