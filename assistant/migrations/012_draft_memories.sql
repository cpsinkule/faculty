-- ASSISTANT-MEMORY: references given to a draft, never a copy of its prompt or words.
-- HIERARCHY: a follow-up row belongs to its run's enrollment; a preparation row
-- belongs to its existing workspace preparation. Memory ownership is unchanged.
CREATE TABLE assistant_draft_memories (
 followup_run_id TEXT REFERENCES student_support_suggestion_runs(id),
 preparation_id TEXT REFERENCES assistant_preparations(id),
 memory_id TEXT NOT NULL,
 revision INTEGER NOT NULL,
 selection TEXT NOT NULL CHECK(selection IN ('all','chosen','nearest','picture')),
 given_at TEXT NOT NULL,
 CHECK((followup_run_id IS NOT NULL) != (preparation_id IS NOT NULL)),
 FOREIGN KEY(memory_id,revision) REFERENCES assistant_memory_revisions(memory_id,revision)
);
CREATE UNIQUE INDEX assistant_draft_memories_followup ON assistant_draft_memories(followup_run_id,memory_id,revision)
 WHERE followup_run_id IS NOT NULL;
CREATE UNIQUE INDEX assistant_draft_memories_preparation ON assistant_draft_memories(preparation_id,memory_id,revision)
 WHERE preparation_id IS NOT NULL;
CREATE INDEX assistant_draft_memories_memory ON assistant_draft_memories(memory_id,revision);
CREATE TRIGGER assistant_draft_memories_fixed BEFORE UPDATE ON assistant_draft_memories
 BEGIN SELECT RAISE(ABORT,'ASSISTANT-MEMORY: draft references are immutable'); END;
CREATE TRIGGER assistant_draft_memories_retained BEFORE DELETE ON assistant_draft_memories
 BEGIN SELECT RAISE(ABORT,'ASSISTANT-MEMORY: draft references are retained'); END;
