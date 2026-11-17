-- A MISSED MAKEUP, RECORD 0 (ABSENCES "MAKEUPS"; requirement, 2026-10-31). Until then
-- only a received paper ended a makeup owed.
--
-- absence_makeups_missed: whether a student missed the makeup of one exam record, so it is owed no more and their empty cell counts
-- as 0 (Certify records the 0). Owner: the student's section membership on that exam record (`absence_id` names the absence that
-- claimed it when it was recorded; none for a student on a makeup sitting without an absence). A revision per change, append-only;
-- `missed` 0 is the instructor's Undo. No row: not missed.
CREATE TABLE IF NOT EXISTS absence_makeups_missed (
 event_id TEXT NOT NULL REFERENCES exam_events(id),
 section_id TEXT NOT NULL,
 enrollment_id TEXT NOT NULL,
 revision INTEGER NOT NULL CHECK(revision >= 1),
 missed INTEGER NOT NULL CHECK(missed IN (0, 1)),
 absence_id TEXT REFERENCES absences(id),
 request_id TEXT NOT NULL UNIQUE CHECK(length(request_id) BETWEEN 1 AND 100),
 actor TEXT NOT NULL,
 created_at TEXT NOT NULL,
 PRIMARY KEY(event_id, section_id, enrollment_id, revision)
);
CREATE TRIGGER IF NOT EXISTS absence_makeups_missed_next BEFORE INSERT ON absence_makeups_missed
WHEN NEW.revision != 1 + COALESCE((SELECT MAX(revision) FROM absence_makeups_missed WHERE event_id=NEW.event_id
  AND section_id=NEW.section_id AND enrollment_id=NEW.enrollment_id), 0)
BEGIN SELECT RAISE(ABORT,'A missed makeup must follow the latest one'); END;
CREATE TRIGGER IF NOT EXISTS absence_makeups_missed_immutable BEFORE UPDATE ON absence_makeups_missed
BEGIN SELECT RAISE(ABORT,'Missed makeups are append-only'); END;
CREATE TRIGGER IF NOT EXISTS absence_makeups_missed_retained BEFORE DELETE ON absence_makeups_missed
BEGIN SELECT RAISE(ABORT,'Missed makeups are retained'); END;
