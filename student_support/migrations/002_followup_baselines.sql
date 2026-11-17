-- TEACHING-WORKFLOWS: workspace-owned start-time decisions and their exact receipts.
CREATE TABLE student_support_baselines (
 request_id TEXT PRIMARY KEY,
 request_sha256 TEXT NOT NULL,
 tracking_since TEXT NOT NULL,
 result_json TEXT NOT NULL CHECK(json_valid(result_json)),
 actor TEXT NOT NULL,
 created_at TEXT NOT NULL
);
CREATE TRIGGER student_support_baselines_immutable BEFORE UPDATE ON student_support_baselines
 BEGIN SELECT RAISE(ABORT,'TEACHING-WORKFLOWS: start-time decisions are immutable'); END;
CREATE TRIGGER student_support_baselines_retained BEFORE DELETE ON student_support_baselines
 BEGIN SELECT RAISE(ABORT,'TEACHING-WORKFLOWS: start-time decisions are retained'); END;
