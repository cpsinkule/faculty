-- Bubble sheet answer keys (requirement, 2026-09-25). A key belongs to the TERM grading plan: each term's exam has its own answers, and its version
-- letters are that term's. Every save is a new immutable revision holding every version's key; the latest counts. Scores are
-- derived from a revision and each sheet's current reading each time they are shown, never stored.
CREATE TABLE frq_bubble_keys (
 id TEXT PRIMARY KEY, assignment_id TEXT NOT NULL REFERENCES frq_assignments(id),
 revision INTEGER NOT NULL CHECK(revision>=1), key_json TEXT NOT NULL, sha256 TEXT NOT NULL,
 request_id TEXT NOT NULL, request_sha256 TEXT NOT NULL, actor TEXT NOT NULL, created_at TEXT NOT NULL,
 UNIQUE(assignment_id,revision), UNIQUE(assignment_id,request_id)
);
CREATE TRIGGER frq_bubble_key_immutable BEFORE UPDATE ON frq_bubble_keys
 BEGIN SELECT RAISE(ABORT,'Answer key revisions are immutable'); END;
CREATE TRIGGER frq_bubble_key_retained BEFORE DELETE ON frq_bubble_keys
 BEGIN SELECT RAISE(ABORT,'Answer key revisions are retained'); END;
