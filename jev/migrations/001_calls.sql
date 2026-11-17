-- JEV: workspace computing receipts reference their teaching/conversation owner.
CREATE TABLE jev_calls (
 id TEXT PRIMARY KEY,
 call_key TEXT NOT NULL UNIQUE,
 purpose TEXT NOT NULL,
 owner_id TEXT NOT NULL,
 enrollment_id TEXT REFERENCES student_enrollments(id),
 input_sha256 TEXT NOT NULL,
 questions_json TEXT NOT NULL CHECK(json_valid(questions_json)),
 model TEXT NOT NULL,
 rule TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('running','complete','failed','uncertain')),
 result_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(result_json)),
 error TEXT NOT NULL DEFAULT '',
 created_at TEXT NOT NULL,
 completed_at TEXT
);
CREATE TRIGGER jev_calls_identity BEFORE UPDATE ON jev_calls
 WHEN NEW.id!=OLD.id OR NEW.call_key!=OLD.call_key OR NEW.purpose!=OLD.purpose
 OR NEW.owner_id!=OLD.owner_id OR NEW.enrollment_id IS NOT OLD.enrollment_id
 OR NEW.input_sha256!=OLD.input_sha256 OR NEW.questions_json!=OLD.questions_json
 OR NEW.model!=OLD.model OR NEW.rule!=OLD.rule OR NEW.created_at!=OLD.created_at
 OR OLD.status!='running'
 BEGIN SELECT RAISE(ABORT,'JEV: receipts and completed outcomes are immutable'); END;
CREATE TRIGGER jev_calls_retained BEFORE DELETE ON jev_calls
 BEGIN SELECT RAISE(ABORT,'JEV: computing receipts are retained'); END;
