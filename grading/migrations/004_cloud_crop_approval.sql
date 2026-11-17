-- Term-owned approval of the exact locally retained cloud crop manifest.
CREATE TABLE frq_cloud_approvals (
 job_id TEXT PRIMARY KEY REFERENCES frq_jobs(id), manifest_sha256 TEXT NOT NULL,
 manifest_json TEXT NOT NULL, actor TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TRIGGER frq_cloud_approval_immutable BEFORE UPDATE ON frq_cloud_approvals
 BEGIN SELECT RAISE(ABORT,'Cloud crop approvals are immutable'); END;
CREATE TRIGGER frq_cloud_approval_retained BEFORE DELETE ON frq_cloud_approvals
 BEGIN SELECT RAISE(ABORT,'Cloud crop approvals are retained'); END;
