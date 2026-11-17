-- ASSISTANT MEMORY (requirement, 2026-10-10).
-- A memory is an identity; what it says, whose it is and whether it stands live in its revisions, which are only ever added.
-- Owner (HIERARCHY): the workspace, a course (course_definitions.id, every term it is taught) or a term (course_terms.id).
-- The old assistant_memories table stays for a process still running the earlier code; its rows are carried in at start.
CREATE TABLE assistant_memory_items (
 id TEXT PRIMARY KEY, created_at TEXT NOT NULL
);
CREATE TABLE assistant_memory_revisions (
 memory_id TEXT NOT NULL REFERENCES assistant_memory_items(id),
 revision INTEGER NOT NULL CHECK(revision > 0),
 status TEXT NOT NULL CHECK(status IN ('current','superseded','removed')),
 kind TEXT NOT NULL CHECK(kind IN ('preference','fact')),
 scope TEXT NOT NULL CHECK(scope IN ('workspace','course','term')),
 definition_id TEXT REFERENCES course_definitions(id),
 term_id TEXT REFERENCES course_terms(id),
 title TEXT NOT NULL DEFAULT '' CHECK(length(title) <= 160),
 content TEXT NOT NULL CHECK(length(content) <= 4000),
 -- Whose words: always the instructor's. Where they typed them: the Memory form, a chat message, a follow-up's checkbox.
 source TEXT NOT NULL CHECK(source IN ('form','chat','followup')),
 -- Who made this revision: the instructor (form, Undo, Remove, Restore) or the Assistant (kept or replaced from a chat message).
 actor TEXT NOT NULL CHECK(actor IN ('instructor','assistant')),
 reason TEXT NOT NULL CHECK(reason IN ('kept','edited','moved','replaced','restored','removed','undone','carried')),
 superseded_by TEXT REFERENCES assistant_memory_items(id),
 capture_call TEXT REFERENCES jev_calls(id),
 consolidate_call TEXT REFERENCES jev_calls(id),
 -- 0: the words may not read on their own (a fragment of a conversation); shown so the instructor can reword them.
 standalone INTEGER NOT NULL DEFAULT 1 CHECK(standalone IN (0,1)),
 erased INTEGER NOT NULL DEFAULT 0 CHECK(erased IN (0,1)),
 created_at TEXT NOT NULL,
 PRIMARY KEY(memory_id, revision),
 CHECK((scope='workspace' AND definition_id IS NULL AND term_id IS NULL)
    OR (scope='course' AND definition_id IS NOT NULL AND term_id IS NULL)
    OR (scope='term' AND definition_id IS NULL AND term_id IS NOT NULL)),
 CHECK((status='superseded') = (superseded_by IS NOT NULL))
);
CREATE INDEX assistant_memory_revisions_term ON assistant_memory_revisions(term_id) WHERE term_id IS NOT NULL;
CREATE TRIGGER assistant_memory_revisions_next BEFORE INSERT ON assistant_memory_revisions
 WHEN NEW.revision != COALESCE((SELECT MAX(revision) FROM assistant_memory_revisions WHERE memory_id=NEW.memory_id),0)+1
 BEGIN SELECT RAISE(ABORT,'ASSISTANT MEMORY: a revision follows the latest one'); END;
-- The one permitted change of a kept revision: Erase blanks its words (a statement that should never have been kept).
CREATE TRIGGER assistant_memory_revisions_kept BEFORE UPDATE ON assistant_memory_revisions
 WHEN NOT (NEW.erased=1 AND NEW.content='' AND NEW.title='' AND NEW.memory_id=OLD.memory_id AND NEW.revision=OLD.revision
   AND NEW.status=OLD.status AND NEW.kind=OLD.kind AND NEW.scope=OLD.scope AND NEW.definition_id IS OLD.definition_id
   AND NEW.term_id IS OLD.term_id AND NEW.source=OLD.source AND NEW.actor=OLD.actor AND NEW.reason=OLD.reason
   AND NEW.superseded_by IS OLD.superseded_by AND NEW.capture_call IS OLD.capture_call
   AND NEW.consolidate_call IS OLD.consolidate_call AND NEW.standalone=OLD.standalone AND NEW.created_at=OLD.created_at)
 BEGIN SELECT RAISE(ABORT,'ASSISTANT MEMORY: revisions are only added; Erase blanks their words'); END;
CREATE TRIGGER assistant_memory_revisions_retained BEFORE DELETE ON assistant_memory_revisions
 BEGIN SELECT RAISE(ABORT,'ASSISTANT MEMORY: revisions are never deleted'); END;
CREATE TRIGGER assistant_memory_items_fixed BEFORE UPDATE ON assistant_memory_items
 BEGIN SELECT RAISE(ABORT,'ASSISTANT MEMORY: a memory keeps its identity'); END;
CREATE TRIGGER assistant_memory_items_retained BEFORE DELETE ON assistant_memory_items
 BEGIN SELECT RAISE(ABORT,'ASSISTANT MEMORY: a memory is removed by a revision, never deleted'); END;
-- Each memory's latest revision: what it says and whether it stands now.
CREATE VIEW assistant_memory_latest AS
 SELECT r.*, i.created_at AS first_at FROM assistant_memory_revisions r JOIN assistant_memory_items i ON i.id=r.memory_id
 WHERE r.revision=(SELECT MAX(revision) FROM assistant_memory_revisions WHERE memory_id=r.memory_id);
-- A memory chosen for a request by search and the relevance check (not the preferences every request gets).
CREATE TABLE assistant_memory_uses (
 id INTEGER PRIMARY KEY, memory_id TEXT NOT NULL, revision INTEGER NOT NULL, conversation_id TEXT NOT NULL,
 relevance_call TEXT REFERENCES jev_calls(id), used_at TEXT NOT NULL,
 FOREIGN KEY(memory_id, revision) REFERENCES assistant_memory_revisions(memory_id, revision)
);
CREATE INDEX assistant_memory_uses_memory ON assistant_memory_uses(memory_id, used_at);
CREATE TRIGGER assistant_memory_uses_fixed BEFORE UPDATE ON assistant_memory_uses
 BEGIN SELECT RAISE(ABORT,'ASSISTANT MEMORY: a use is a receipt'); END;
