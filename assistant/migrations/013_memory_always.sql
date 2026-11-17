-- ASSISTANT-MEMORY "WHAT A REQUEST IS TOLD" (requirement, 2026-11-09). No memory goes with a request for being in
-- scope alone: each is judged against the request. `always` marks the few the instructor says belong with every request
-- (a way of writing, a standing habit no request names). It is the instructor's own mark, part of what a revision says, so
-- it has the memory's owner (HIERARCHY: the workspace, a course or a term) and a change of it is a revision like any other.
ALTER TABLE assistant_memory_revisions ADD COLUMN always INTEGER NOT NULL DEFAULT 0 CHECK(always IN (0,1));
-- The one permitted change of a kept revision is still Erase, and it leaves the mark as it was.
DROP TRIGGER assistant_memory_revisions_kept;
CREATE TRIGGER assistant_memory_revisions_kept BEFORE UPDATE ON assistant_memory_revisions
 WHEN NOT (NEW.erased=1 AND NEW.content='' AND NEW.title='' AND NEW.memory_id=OLD.memory_id AND NEW.revision=OLD.revision
   AND NEW.status=OLD.status AND NEW.kind=OLD.kind AND NEW.scope=OLD.scope AND NEW.definition_id IS OLD.definition_id
   AND NEW.term_id IS OLD.term_id AND NEW.source=OLD.source AND NEW.actor=OLD.actor AND NEW.reason=OLD.reason
   AND NEW.superseded_by IS OLD.superseded_by AND NEW.capture_call IS OLD.capture_call
   AND NEW.consolidate_call IS OLD.consolidate_call AND NEW.standalone=OLD.standalone AND NEW.always=OLD.always
   AND NEW.created_at=OLD.created_at)
 BEGIN SELECT RAISE(ABORT,'ASSISTANT MEMORY: revisions are only added; Erase blanks their words'); END;
