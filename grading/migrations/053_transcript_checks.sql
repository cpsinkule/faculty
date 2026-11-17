-- TRANSCRIPTIONS CHECKED AGAINST THEIR CROPS (requirement, 2026-11-02). What one reading of a student's answer settled to: owned by the term
-- response (`frq_responses.id`) and its paper (`source_sha256`), since a crop's handwriting does not change with its grade. It sits BESIDE
-- the saved examples, which stay immutable: nothing here changes a grade, a transcript a grade was given under, or an example.
CREATE TABLE frq_transcript_checks (
 id TEXT PRIMARY KEY,
 assignment_id TEXT NOT NULL REFERENCES frq_assignments(id),
 response_id TEXT NOT NULL REFERENCES frq_responses(id),
 source_sha256 TEXT NOT NULL,
 example_id TEXT NOT NULL REFERENCES frq_training_examples(id),
 rule TEXT NOT NULL,
 verdict TEXT NOT NULL CHECK(verdict IN ('confirmed','corrected','unsettled')),
 text TEXT NOT NULL,
 saved_text TEXT NOT NULL,
 readings_json TEXT NOT NULL,
 doubts_json TEXT NOT NULL,
 created_at TEXT NOT NULL,
 UNIQUE(response_id,source_sha256,rule)
);
CREATE INDEX frq_transcript_checks_plan ON frq_transcript_checks(assignment_id,verdict);
CREATE TRIGGER frq_transcript_check_immutable BEFORE UPDATE ON frq_transcript_checks
 BEGIN SELECT RAISE(ABORT,'A transcript check is immutable: a later rule is a new row'); END;
CREATE TRIGGER frq_transcript_check_retained BEFORE DELETE ON frq_transcript_checks
 BEGIN SELECT RAISE(ABORT,'Transcript checks are retained'); END;
-- Every request a check sends is an ordinary cloud call, owned by "<response id>:<source sha256>". Its row has no id until the check is
-- settled, so the key names no table; what was answered before a restart is replayed from the receipt, never bought twice.
ALTER TABLE frq_cloud_calls ADD COLUMN transcript_check_key TEXT;
CREATE INDEX frq_cloud_calls_transcript_check ON frq_cloud_calls(transcript_check_key,step) WHERE transcript_check_key IS NOT NULL;
CREATE TRIGGER frq_cloud_call_transcript_check_identity BEFORE UPDATE OF transcript_check_key ON frq_cloud_calls
 BEGIN SELECT RAISE(ABORT,'Cloud transcript check ownership is immutable'); END;
