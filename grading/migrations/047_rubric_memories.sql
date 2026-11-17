-- RUBRIC MEMORIES (requirement, 2026-10-14). General rules and watches for the rubric writers and
-- the rubric reviewer, learned from one assignment's rubric questions with the instructor's answers and from where AI graders
-- stopped. A memory belongs to the COURSE (course_definitions): every term of the course drafts and checks with it. It stands only
-- once the instructor keeps it; nothing here changes a rubric, a confirmation or a grade.

-- One learning pass over one TERM grading plan's evidence: an ordinary cloud call owned by it (`frq_cloud_calls.rubric_memory_run_id`).
-- The inputs never change; the answer is written once.
CREATE TABLE frq_rubric_memory_runs (
 id TEXT PRIMARY KEY,
 definition_id TEXT NOT NULL REFERENCES course_definitions(id),
 assignment_id TEXT NOT NULL REFERENCES frq_assignments(id),
 trigger TEXT NOT NULL CHECK(trigger IN ('certified','instructor')),
 input_sha256 TEXT NOT NULL CHECK(length(input_sha256) = 64),
 input_json TEXT NOT NULL,
 profile_json TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('pending','running','complete','failed')),
 result_json TEXT,
 error TEXT NOT NULL DEFAULT '' CHECK(length(error) <= 1000),
 attempts INTEGER NOT NULL DEFAULT 0,
 not_before TEXT,
 actor TEXT NOT NULL,
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL
);
CREATE INDEX frq_rubric_memory_runs_course ON frq_rubric_memory_runs(definition_id, created_at);
CREATE INDEX frq_rubric_memory_runs_queue ON frq_rubric_memory_runs(status, not_before);
CREATE TRIGGER frq_rubric_memory_run_inputs_immutable BEFORE UPDATE OF id,definition_id,assignment_id,trigger,input_sha256,input_json,profile_json,actor,created_at
 ON frq_rubric_memory_runs BEGIN SELECT RAISE(ABORT,'A rubric memory run''s inputs are immutable'); END;
CREATE TRIGGER frq_rubric_memory_run_settled BEFORE UPDATE ON frq_rubric_memory_runs WHEN OLD.status IN ('complete','failed')
 BEGIN SELECT RAISE(ABORT,'A settled rubric memory run is immutable'); END;
CREATE TRIGGER frq_rubric_memory_runs_retained BEFORE DELETE ON frq_rubric_memory_runs BEGIN SELECT RAISE(ABORT,'Rubric memory runs are retained'); END;

-- A memory as proposed, never edited: a revision (narrowed, widened, a watch answered, a retirement) is a new row that names the
-- row it replaces and keeps its number. Which rows stand follows from the instructor's decisions alone.
CREATE TABLE frq_rubric_memories (
 id TEXT PRIMARY KEY,
 definition_id TEXT NOT NULL REFERENCES course_definitions(id),
 number INTEGER NOT NULL CHECK(number >= 1),
 kind TEXT NOT NULL CHECK(kind IN ('rule','watch')),
 action TEXT NOT NULL CHECK(action IN ('new','narrow','widen','answer','retire')),
 replaces_id TEXT REFERENCES frq_rubric_memories(id),
 rule TEXT NOT NULL CHECK(length(rule) BETWEEN 1 AND 700),
 covers TEXT NOT NULL CHECK(length(covers) BETWEEN 1 AND 500),
 not_when TEXT NOT NULL CHECK(length(not_when) <= 700),
 why TEXT NOT NULL DEFAULT '' CHECK(length(why) <= 700),
 sources_json TEXT NOT NULL,
 run_id TEXT REFERENCES frq_rubric_memory_runs(id),
 created_at TEXT NOT NULL,
 CHECK((action = 'new') = (replaces_id IS NULL))
);
CREATE INDEX frq_rubric_memories_course ON frq_rubric_memories(definition_id, number, created_at);
CREATE TRIGGER frq_rubric_memories_immutable BEFORE UPDATE ON frq_rubric_memories
 BEGIN SELECT RAISE(ABORT,'A rubric memory is immutable: a change is a new row'); END;
CREATE TRIGGER frq_rubric_memories_retained BEFORE DELETE ON frq_rubric_memories BEGIN SELECT RAISE(ABORT,'Rubric memories are retained'); END;

-- The instructor's Keep and Drop, append-only: the latest decision about a row is its state (none yet: waiting).
CREATE TABLE frq_rubric_memory_decisions (
 id TEXT PRIMARY KEY,
 memory_id TEXT NOT NULL REFERENCES frq_rubric_memories(id),
 decision TEXT NOT NULL CHECK(decision IN ('keep','drop')),
 actor TEXT NOT NULL,
 created_at TEXT NOT NULL
);
CREATE INDEX frq_rubric_memory_decisions_memory ON frq_rubric_memory_decisions(memory_id, created_at);
CREATE TRIGGER frq_rubric_memory_decisions_immutable BEFORE UPDATE ON frq_rubric_memory_decisions
 BEGIN SELECT RAISE(ABORT,'Rubric memory decisions are append-only'); END;
CREATE TRIGGER frq_rubric_memory_decisions_retained BEFORE DELETE ON frq_rubric_memory_decisions BEGIN SELECT RAISE(ABORT,'Rubric memory decisions are retained'); END;

ALTER TABLE frq_cloud_calls ADD COLUMN rubric_memory_run_id TEXT REFERENCES frq_rubric_memory_runs(id);
CREATE INDEX frq_cloud_calls_rubric_memory_run ON frq_cloud_calls(rubric_memory_run_id, step);
CREATE TRIGGER frq_cloud_call_rubric_memory_run_identity BEFORE UPDATE OF rubric_memory_run_id ON frq_cloud_calls
 BEGIN SELECT RAISE(ABORT,'Cloud rubric memory run ownership is immutable'); END;
