-- ASSISTANT-MEMORY "SAID IN A RECORDING" (the fifth named exception to ONLY THE INSTRUCTOR'S OWN TYPED WORDS; RECORDINGS "A
-- MEMORY", step 3): a standing rule said in a recorded meeting is kept only when the instructor presses Remember on it, in the
-- words shown, which they may edit first. Such a memory's `source` is `recording`; the recording and the quote it rests on stay
-- with the recording's item (recording_item_events `remembered`), by reference. Owners are unchanged (HIERARCHY: the workspace,
-- a course or a term).
--
-- `source` is a CHECK of the table, which SQLite cannot change in place: the table is made again with the one more value and
-- every revision carried over as it is, column for column and in the same order (the view reads `r.*`). Run with foreign keys
-- suspended for the transaction and checked before it commits (`catalog_migrations.migration_transaction(rebuild=...)`, as
-- `assistant/service.py` asks for this migration): `assistant_memory_uses` and `assistant_draft_memories` refer to this table
-- by name and keep doing so.
DROP VIEW assistant_memory_latest;
CREATE TABLE assistant_memory_revisions_next (
 memory_id TEXT NOT NULL REFERENCES assistant_memory_items(id),
 revision INTEGER NOT NULL CHECK(revision > 0),
 status TEXT NOT NULL CHECK(status IN ('current','superseded','removed')),
 kind TEXT NOT NULL CHECK(kind IN ('preference','fact')),
 scope TEXT NOT NULL CHECK(scope IN ('workspace','course','term')),
 definition_id TEXT REFERENCES course_definitions(id),
 term_id TEXT REFERENCES course_terms(id),
 title TEXT NOT NULL DEFAULT '' CHECK(length(title) <= 160),
 content TEXT NOT NULL CHECK(length(content) <= 4000),
 -- Whose words: the instructor's, or (`recording`) words said in a meeting that the instructor chose to keep, as they left them.
 -- Where: the Memory form, a chat message, a follow-up's checkbox, Remember on a recording's item.
 source TEXT NOT NULL CHECK(source IN ('form','chat','followup','recording')),
 -- Who made this revision: the instructor (form, Undo, Remove, Restore, Remember) or the Assistant (kept or replaced from a chat message).
 actor TEXT NOT NULL CHECK(actor IN ('instructor','assistant')),
 reason TEXT NOT NULL CHECK(reason IN ('kept','edited','moved','replaced','restored','removed','undone','carried')),
 superseded_by TEXT REFERENCES assistant_memory_items(id),
 capture_call TEXT REFERENCES jev_calls(id),
 consolidate_call TEXT REFERENCES jev_calls(id),
 -- 0: the words may not read on their own (a fragment of a conversation); shown so the instructor can reword them.
 standalone INTEGER NOT NULL DEFAULT 1 CHECK(standalone IN (0,1)),
 erased INTEGER NOT NULL DEFAULT 0 CHECK(erased IN (0,1)),
 created_at TEXT NOT NULL,
 always INTEGER NOT NULL DEFAULT 0 CHECK(always IN (0,1)),
 PRIMARY KEY(memory_id, revision),
 CHECK((scope='workspace' AND definition_id IS NULL AND term_id IS NULL)
    OR (scope='course' AND definition_id IS NOT NULL AND term_id IS NULL)
    OR (scope='term' AND definition_id IS NULL AND term_id IS NOT NULL)),
 CHECK((status='superseded') = (superseded_by IS NOT NULL))
);
INSERT INTO assistant_memory_revisions_next(memory_id,revision,status,kind,scope,definition_id,term_id,title,content,source,actor,reason,
   superseded_by,capture_call,consolidate_call,standalone,erased,created_at,always)
 SELECT memory_id,revision,status,kind,scope,definition_id,term_id,title,content,source,actor,reason,
   superseded_by,capture_call,consolidate_call,standalone,erased,created_at,always FROM assistant_memory_revisions;
DROP TABLE assistant_memory_revisions;
ALTER TABLE assistant_memory_revisions_next RENAME TO assistant_memory_revisions;
CREATE INDEX assistant_memory_revisions_term ON assistant_memory_revisions(term_id) WHERE term_id IS NOT NULL;
CREATE TRIGGER assistant_memory_revisions_next BEFORE INSERT ON assistant_memory_revisions
 WHEN NEW.revision != COALESCE((SELECT MAX(revision) FROM assistant_memory_revisions WHERE memory_id=NEW.memory_id),0)+1
 BEGIN SELECT RAISE(ABORT,'ASSISTANT MEMORY: a revision follows the latest one'); END;
-- The one permitted change of a kept revision is still Erase, which blanks its words and leaves everything else as it was.
CREATE TRIGGER assistant_memory_revisions_kept BEFORE UPDATE ON assistant_memory_revisions
 WHEN NOT (NEW.erased=1 AND NEW.content='' AND NEW.title='' AND NEW.memory_id=OLD.memory_id AND NEW.revision=OLD.revision
   AND NEW.status=OLD.status AND NEW.kind=OLD.kind AND NEW.scope=OLD.scope AND NEW.definition_id IS OLD.definition_id
   AND NEW.term_id IS OLD.term_id AND NEW.source=OLD.source AND NEW.actor=OLD.actor AND NEW.reason=OLD.reason
   AND NEW.superseded_by IS OLD.superseded_by AND NEW.capture_call IS OLD.capture_call
   AND NEW.consolidate_call IS OLD.consolidate_call AND NEW.standalone=OLD.standalone AND NEW.always=OLD.always
   AND NEW.created_at=OLD.created_at)
 BEGIN SELECT RAISE(ABORT,'ASSISTANT MEMORY: revisions are only added; Erase blanks their words'); END;
CREATE TRIGGER assistant_memory_revisions_retained BEFORE DELETE ON assistant_memory_revisions
 BEGIN SELECT RAISE(ABORT,'ASSISTANT MEMORY: revisions are never deleted'); END;
CREATE VIEW assistant_memory_latest AS
 SELECT r.*, i.created_at AS first_at FROM assistant_memory_revisions r JOIN assistant_memory_items i ON i.id=r.memory_id
 WHERE r.revision=(SELECT MAX(revision) FROM assistant_memory_revisions WHERE memory_id=r.memory_id);
