-- Changing Faculty itself (requirement, 2026-09-29). WORKSPACE-owned. A coding task is one immutable row: the instructor's
-- request in their words, what the assistant gathered, the coding agent chosen, and the branch and commit it began from. What happened
-- to it (each round of work, stops, apply, push, restart, discard) is an append-only list of events; the latest ones count. The code
-- itself lives in the task's own clone on NVMe until it is applied (merged into the live main) or discarded.
CREATE TABLE assistant_code_tasks (
 id TEXT PRIMARY KEY,
 request_id TEXT NOT NULL UNIQUE CHECK(length(request_id) BETWEEN 1 AND 100),
 title TEXT NOT NULL CHECK(length(title) BETWEEN 1 AND 200),
 request TEXT NOT NULL CHECK(length(request) BETWEEN 1 AND 20000),
 brief TEXT NOT NULL CHECK(length(brief) <= 200000),
 page TEXT NOT NULL CHECK(length(page) <= 500),
 coder TEXT NOT NULL CHECK(coder IN ('claude','codex')),
 model TEXT NOT NULL CHECK(length(model) <= 200),
 reasoning TEXT NOT NULL CHECK(length(reasoning) <= 40),
 branch TEXT NOT NULL CHECK(length(branch) BETWEEN 1 AND 200),
 base_commit TEXT NOT NULL CHECK(length(base_commit) = 40),
 directory TEXT NOT NULL,
 created_at TEXT NOT NULL
);
CREATE INDEX assistant_code_tasks_created ON assistant_code_tasks(created_at);
CREATE TABLE assistant_code_task_events (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 task_id TEXT NOT NULL REFERENCES assistant_code_tasks(id),
 kind TEXT NOT NULL CHECK(length(kind) BETWEEN 1 AND 40),
 actor TEXT NOT NULL CHECK(actor IN ('instructor','faculty','assistant')),
 detail_json TEXT NOT NULL CHECK(json_valid(detail_json) AND json_type(detail_json)='object'),
 created_at TEXT NOT NULL
);
CREATE INDEX assistant_code_task_events_task ON assistant_code_task_events(task_id, id);
CREATE TRIGGER assistant_code_tasks_immutable BEFORE UPDATE ON assistant_code_tasks BEGIN SELECT RAISE(ABORT,'Coding tasks are immutable'); END;
CREATE TRIGGER assistant_code_tasks_retained BEFORE DELETE ON assistant_code_tasks BEGIN SELECT RAISE(ABORT,'Coding tasks are retained'); END;
CREATE TRIGGER assistant_code_task_events_immutable BEFORE UPDATE ON assistant_code_task_events BEGIN SELECT RAISE(ABORT,'Coding task events are append-only'); END;
CREATE TRIGGER assistant_code_task_events_retained BEFORE DELETE ON assistant_code_task_events BEGIN SELECT RAISE(ABORT,'Coding task events are retained'); END;
