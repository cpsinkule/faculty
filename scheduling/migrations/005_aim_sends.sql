-- SCHEDULING "SEND TO AIM" (requirement, 2026-10-27): every write Faculty makes in AIM, recorded BEFORE it is
-- posted, and what came of it. A `date` write adds one DR quiz's exam date to its section in AIM (type Quiz, the day and the base
-- length, never a time: the DR center sets the time and room); an `upload` write attaches the quiz's accessible copy (QUIZZES
-- "ACCESSIBLE COPY") to the students' exam requests AIM lists for that quiz, each request kept only as the sha256 of its row
-- (`rows`, a JSON list), so no name is stored. A write with no outcome, or an uncertain one, is never posted again by itself
-- (AGENTS.md OUTSIDE SERVICES): it waits until a later read of AIM confirms it or the instructor's Retry releases it.
-- Owner: the course term (course_terms.id), naming the term's planned row (scheduling_plan_items.id) and section
-- (course_sections.id) by reference; an upload names the approved quiz and version its file was made from. Append-only.
CREATE TABLE aim_sends (
 id TEXT PRIMARY KEY CHECK(length(id) BETWEEN 8 AND 40),
 term_id TEXT NOT NULL REFERENCES course_terms(id),
 item_id TEXT NOT NULL REFERENCES scheduling_plan_items(id),
 section_id TEXT NOT NULL REFERENCES course_sections(id),
 kind TEXT NOT NULL CHECK(kind IN ('date','upload')),
 date TEXT NOT NULL CHECK(length(date) = 10),
 minutes INTEGER NOT NULL CHECK(minutes BETWEEN 0 AND 999),
 approval_id TEXT NOT NULL,
 version TEXT NOT NULL,
 file_sha256 TEXT NOT NULL,
 rows TEXT NOT NULL CHECK(json_valid(rows)),
 run_id TEXT NOT NULL CHECK(length(run_id) BETWEEN 8 AND 100),
 actor TEXT NOT NULL,
 created_at TEXT NOT NULL,
 CHECK(CASE kind WHEN 'date' THEN approval_id = '' AND version = '' AND file_sha256 = '' AND rows = '[]'
       ELSE approval_id <> '' AND version <> '' AND length(file_sha256) = 64 AND json_array_length(rows) >= 1 END)
);
CREATE INDEX aim_sends_quiz ON aim_sends(term_id, item_id, section_id, kind);
CREATE TRIGGER aim_sends_scope BEFORE INSERT ON aim_sends
 WHEN NOT EXISTS (SELECT 1 FROM scheduling_plan_items i WHERE i.id = NEW.item_id AND i.term_id = NEW.term_id)
   OR NOT EXISTS (SELECT 1 FROM course_sections s WHERE s.id = NEW.section_id AND s.term_id = NEW.term_id)
 BEGIN SELECT RAISE(ABORT,'SCHEDULING: an AIM write names a planned row and a section of its own term'); END;
CREATE TRIGGER aim_sends_immutable BEFORE UPDATE ON aim_sends
 BEGIN SELECT RAISE(ABORT,'SCHEDULING: AIM writes are immutable'); END;
CREATE TRIGGER aim_sends_retained BEFORE DELETE ON aim_sends
 BEGIN SELECT RAISE(ABORT,'SCHEDULING: AIM writes are retained'); END;
-- What came of a write: `accepted` (AIM's next read shows it), `failed` (AIM refused it, or it was never posted), `uncertain`
-- (posted, and no read settled it), `released` (the instructor's Retry: the next Send may post it again). The latest counts; a
-- write with none is uncertain.
CREATE TABLE aim_send_outcomes (
 id INTEGER PRIMARY KEY,
 send_id TEXT NOT NULL REFERENCES aim_sends(id),
 status TEXT NOT NULL CHECK(status IN ('accepted','failed','uncertain','released')),
 detail TEXT NOT NULL CHECK(length(detail) <= 500),
 actor TEXT NOT NULL,
 created_at TEXT NOT NULL
);
CREATE INDEX aim_send_outcomes_send ON aim_send_outcomes(send_id);
CREATE TRIGGER aim_send_outcomes_immutable BEFORE UPDATE ON aim_send_outcomes
 BEGIN SELECT RAISE(ABORT,'SCHEDULING: AIM write outcomes are immutable'); END;
CREATE TRIGGER aim_send_outcomes_retained BEFORE DELETE ON aim_send_outcomes
 BEGIN SELECT RAISE(ABORT,'SCHEDULING: AIM write outcomes are retained'); END;
