-- Owners: events and sittings belong to one permanent course term. Participants
-- belong to that event and an existing section/enrollment membership. Paper,
-- room, chart and course-wide assignment references retain their original owners.
CREATE TABLE IF NOT EXISTS exam_events (
 id TEXT PRIMARY KEY, term_id TEXT NOT NULL REFERENCES course_terms(id),
 assignment_id TEXT NOT NULL REFERENCES assignments(id), title TEXT NOT NULL,
 version INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 UNIQUE(term_id,assignment_id)
);
CREATE TABLE IF NOT EXISTS exam_event_sections (
 event_id TEXT NOT NULL REFERENCES exam_events(id), section_id TEXT NOT NULL REFERENCES course_sections(id),
 PRIMARY KEY(event_id,section_id)
);
CREATE TABLE IF NOT EXISTS exam_sittings (
 id TEXT PRIMARY KEY, event_id TEXT NOT NULL REFERENCES exam_events(id), label TEXT NOT NULL,
 kind TEXT NOT NULL CHECK(kind IN ('main','makeup','dr')), date TEXT NOT NULL DEFAULT '',
 time TEXT NOT NULL DEFAULT '', location TEXT NOT NULL DEFAULT '', room_id TEXT REFERENCES rooms(id),
 version INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS exam_participants (
 event_id TEXT NOT NULL REFERENCES exam_events(id), section_id TEXT NOT NULL, enrollment_id TEXT NOT NULL,
 sitting_id TEXT REFERENCES exam_sittings(id), expectation TEXT NOT NULL DEFAULT 'expected'
 CHECK(expectation IN ('expected','absent','excused')), reason TEXT NOT NULL DEFAULT '',
 version INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 PRIMARY KEY(event_id,section_id,enrollment_id),
 FOREIGN KEY(event_id,section_id) REFERENCES exam_event_sections(event_id,section_id),
 FOREIGN KEY(section_id,enrollment_id) REFERENCES roster_memberships(section_id,enrollment_id)
);
CREATE TABLE IF NOT EXISTS exam_event_batches (
 event_id TEXT NOT NULL REFERENCES exam_events(id), batch_id TEXT NOT NULL UNIQUE REFERENCES print_batches(id),
 sitting_id TEXT REFERENCES exam_sittings(id), created_at TEXT NOT NULL, PRIMARY KEY(event_id,batch_id)
);
CREATE TABLE IF NOT EXISTS exam_event_charts (
 event_id TEXT NOT NULL REFERENCES exam_events(id),
 chart_id TEXT UNIQUE REFERENCES seating_charts(id) ON DELETE SET NULL,
 original_chart_id TEXT NOT NULL UNIQUE,
 sitting_id TEXT REFERENCES exam_sittings(id), created_at TEXT NOT NULL,
 PRIMARY KEY(event_id,original_chart_id)
);
CREATE TABLE IF NOT EXISTS exam_preparation_checks (
 id TEXT PRIMARY KEY, event_id TEXT NOT NULL REFERENCES exam_events(id), fingerprint TEXT NOT NULL,
 snapshot_json TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS exam_history (
 id INTEGER PRIMARY KEY, event_id TEXT NOT NULL REFERENCES exam_events(id), kind TEXT NOT NULL,
 detail_json TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS exam_participants_sitting ON exam_participants(sitting_id);
CREATE INDEX IF NOT EXISTS exam_preparation_event ON exam_preparation_checks(event_id,created_at);
CREATE TRIGGER IF NOT EXISTS exam_scope BEFORE INSERT ON exam_events WHEN
 (SELECT definition_id FROM course_terms WHERE id=NEW.term_id) IS NOT
 (SELECT definition_id FROM assignments WHERE id=NEW.assignment_id)
 BEGIN SELECT RAISE(ABORT,'Exam assignment belongs to another course'); END;
CREATE TRIGGER IF NOT EXISTS exam_owner_retained BEFORE UPDATE OF id,term_id,assignment_id ON exam_events
 BEGIN SELECT RAISE(ABORT,'Exam ownership is permanent'); END;
CREATE TRIGGER IF NOT EXISTS exam_section_scope BEFORE INSERT ON exam_event_sections WHEN
 (SELECT term_id FROM course_sections WHERE id=NEW.section_id) IS NOT
 (SELECT term_id FROM exam_events WHERE id=NEW.event_id)
 BEGIN SELECT RAISE(ABORT,'Exam section belongs to another term'); END;
CREATE TRIGGER IF NOT EXISTS exam_sitting_owner BEFORE UPDATE OF id,event_id ON exam_sittings
 BEGIN SELECT RAISE(ABORT,'Sitting ownership is permanent'); END;
CREATE TRIGGER IF NOT EXISTS exam_participant_owner BEFORE UPDATE OF event_id,section_id,enrollment_id ON exam_participants
 BEGIN SELECT RAISE(ABORT,'Exam participant ownership is permanent'); END;
CREATE TRIGGER IF NOT EXISTS exam_participant_sitting_insert BEFORE INSERT ON exam_participants WHEN
 NEW.sitting_id IS NOT NULL AND (SELECT event_id FROM exam_sittings WHERE id=NEW.sitting_id) IS NOT NEW.event_id
 BEGIN SELECT RAISE(ABORT,'Participant sitting belongs to another exam'); END;
CREATE TRIGGER IF NOT EXISTS exam_participant_sitting_update BEFORE UPDATE OF sitting_id ON exam_participants WHEN
 NEW.sitting_id IS NOT NULL AND (SELECT event_id FROM exam_sittings WHERE id=NEW.sitting_id) IS NOT NEW.event_id
 BEGIN SELECT RAISE(ABORT,'Participant sitting belongs to another exam'); END;
CREATE TRIGGER IF NOT EXISTS exam_batch_scope BEFORE INSERT ON exam_event_batches WHEN NOT EXISTS (
 SELECT 1 FROM print_batches b JOIN assignment_versions v ON v.id=b.assignment_version_id JOIN exam_events e ON e.id=NEW.event_id
 WHERE b.id=NEW.batch_id AND v.assignment_id=e.assignment_id AND b.term_id=e.term_id AND (b.section_id IS NULL
 OR EXISTS(SELECT 1 FROM exam_event_sections s WHERE s.event_id=e.id AND s.section_id=b.section_id)))
 OR (NEW.sitting_id IS NOT NULL AND (SELECT event_id FROM exam_sittings WHERE id=NEW.sitting_id) IS NOT NEW.event_id)
 BEGIN SELECT RAISE(ABORT,'Printed batch belongs to another exam scope'); END;
CREATE TRIGGER IF NOT EXISTS exam_chart_scope BEFORE INSERT ON exam_event_charts
 WHEN NEW.chart_id IS NULL OR NEW.original_chart_id IS NOT NEW.chart_id OR NOT EXISTS (
 SELECT 1 FROM seating_charts c JOIN exam_events e ON e.id=NEW.event_id
 WHERE c.id=NEW.chart_id AND c.term_id=e.term_id AND c.assignment_id=e.assignment_id)
 OR EXISTS (SELECT 1 FROM seating_chart_sections s WHERE s.chart_id=NEW.chart_id AND NOT EXISTS (
 SELECT 1 FROM exam_event_sections es WHERE es.event_id=NEW.event_id AND es.section_id=s.section_id))
 OR (NEW.sitting_id IS NOT NULL AND (SELECT event_id FROM exam_sittings WHERE id=NEW.sitting_id) IS NOT NEW.event_id)
 BEGIN SELECT RAISE(ABORT,'Seating chart belongs to another exam scope'); END;
CREATE TRIGGER IF NOT EXISTS exam_history_immutable BEFORE UPDATE ON exam_history
 BEGIN SELECT RAISE(ABORT,'Exam history is immutable'); END;
CREATE TRIGGER IF NOT EXISTS exam_history_retained BEFORE DELETE ON exam_history
 BEGIN SELECT RAISE(ABORT,'Exam history is retained'); END;
CREATE TRIGGER IF NOT EXISTS exam_checkpoint_immutable BEFORE UPDATE ON exam_preparation_checks
 BEGIN SELECT RAISE(ABORT,'Exam checkpoints are immutable'); END;
CREATE TRIGGER IF NOT EXISTS exam_checkpoint_retained BEFORE DELETE ON exam_preparation_checks
 BEGIN SELECT RAISE(ABORT,'Exam checkpoints are retained'); END;
CREATE TRIGGER IF NOT EXISTS exam_event_retained BEFORE DELETE ON exam_events
 BEGIN SELECT RAISE(ABORT,'Exam events are retained'); END;
CREATE TRIGGER IF NOT EXISTS exam_section_immutable BEFORE UPDATE ON exam_event_sections
 BEGIN SELECT RAISE(ABORT,'Exam section references are retained'); END;
CREATE TRIGGER IF NOT EXISTS exam_section_retained BEFORE DELETE ON exam_event_sections
 BEGIN SELECT RAISE(ABORT,'Exam section references are retained'); END;
CREATE TRIGGER IF NOT EXISTS exam_sitting_retained BEFORE DELETE ON exam_sittings
 BEGIN SELECT RAISE(ABORT,'Exam sittings are retained'); END;
CREATE TRIGGER IF NOT EXISTS exam_participant_retained BEFORE DELETE ON exam_participants
 BEGIN SELECT RAISE(ABORT,'Exam participants are retained'); END;
CREATE TRIGGER IF NOT EXISTS exam_batch_link_immutable BEFORE UPDATE ON exam_event_batches
 BEGIN SELECT RAISE(ABORT,'Exam print links are retained'); END;
CREATE TRIGGER IF NOT EXISTS exam_batch_link_retained BEFORE DELETE ON exam_event_batches
 BEGIN SELECT RAISE(ABORT,'Exam print links are retained'); END;
CREATE TRIGGER IF NOT EXISTS exam_chart_link_immutable BEFORE UPDATE ON exam_event_charts
 WHEN NOT (OLD.chart_id IS NOT NULL AND NEW.chart_id IS NULL
 AND NOT EXISTS(SELECT 1 FROM seating_charts WHERE id=OLD.chart_id)
 AND NEW.event_id IS OLD.event_id AND NEW.original_chart_id IS OLD.original_chart_id
 AND NEW.sitting_id IS OLD.sitting_id AND NEW.created_at IS OLD.created_at)
 BEGIN SELECT RAISE(ABORT,'Exam seating links are retained except when their chart is explicitly deleted'); END;
CREATE TRIGGER IF NOT EXISTS exam_chart_link_retained BEFORE DELETE ON exam_event_charts
 BEGIN SELECT RAISE(ABORT,'Exam seating links are retained'); END;
CREATE TRIGGER IF NOT EXISTS exam_chart_deleted_history AFTER UPDATE OF chart_id ON exam_event_charts
 WHEN OLD.chart_id IS NOT NULL AND NEW.chart_id IS NULL
 BEGIN
  INSERT INTO exam_history(event_id,kind,detail_json,created_at)
   VALUES(OLD.event_id,'chart_deleted',json_object('chart_id',OLD.original_chart_id,'sitting_id',OLD.sitting_id),
    strftime('%Y-%m-%dT%H:%M:%f+00:00','now'));
  UPDATE exam_events SET version=version+1,updated_at=strftime('%Y-%m-%dT%H:%M:%f+00:00','now') WHERE id=OLD.event_id;
 END;
