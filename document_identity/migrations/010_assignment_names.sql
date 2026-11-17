-- Renaming an assignment (requirement, 2026-09-27). An assignment stays a retained identity: its id,
-- course, key and creation never change. Its title may change ONLY to the latest name recorded here, in an append-only history
-- that keeps every earlier title (`previous`), so the first row names the original. Printed papers, sent mail and other records
-- keep the text they were made with.
CREATE TABLE IF NOT EXISTS assignment_names (
 id TEXT PRIMARY KEY,
 assignment_id TEXT NOT NULL REFERENCES assignments(id),
 title TEXT NOT NULL CHECK(length(title) BETWEEN 1 AND 240),
 previous TEXT NOT NULL,
 request_id TEXT NOT NULL UNIQUE,
 actor TEXT NOT NULL,
 created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS assignment_names_assignment ON assignment_names(assignment_id, created_at);
CREATE TRIGGER IF NOT EXISTS assignment_name_immutable BEFORE UPDATE ON assignment_names BEGIN SELECT RAISE(ABORT,'Assignment names are append-only'); END;
CREATE TRIGGER IF NOT EXISTS assignment_name_retained BEFORE DELETE ON assignment_names BEGIN SELECT RAISE(ABORT,'Assignment names are retained'); END;
DROP TRIGGER IF EXISTS assignment_immutable;
CREATE TRIGGER assignment_immutable BEFORE UPDATE ON assignments
WHEN NEW.id IS NOT OLD.id OR NEW.definition_id IS NOT OLD.definition_id OR NEW.assignment_key IS NOT OLD.assignment_key
  OR NEW.created_at IS NOT OLD.created_at
  OR NEW.title IS NOT (SELECT n.title FROM assignment_names n WHERE n.assignment_id=NEW.id ORDER BY n.rowid DESC LIMIT 1)
BEGIN SELECT RAISE(ABORT,'Assignments are retained identities'); END;
