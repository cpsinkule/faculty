-- A COORDINATOR'S FOLLOW-UP (TEACHING-WORKFLOWS; COORDINATOR; requirement, 2026-11-14).
--
-- OWNER. A follow-up about a student's conversation belongs to that student's term enrollment. One about the coordinator's
-- conversation belongs to the course TERM (`course_terms.id`): where these records name the enrollment, it names the term's
-- owner key `coordinator:<term id>` (`course_coordinators.service.owner_key`). An enrollment ID is ten digits, so the two never
-- collide. The column keeps its name: it says whose conversation the record is about.
--
-- SQLite cannot drop a column's reference in place, so the four tables that a coordinator's follow-up writes are made again
-- with every row they hold, column for column and in the same order, and the same indexes and triggers: the follow-up itself,
-- the Assistant's preparation runs, Don't suggest for this conversation, and mail written from a follow-up. What the dropped
-- reference to `student_enrollments` enforced is now each table's owner trigger: a record names an enrollment (of its term,
-- where it has one) or a term's coordinator key. Run with foreign keys suspended for the transaction and checked before it
-- commits (`catalog_migrations.migration_transaction(rebuild=...)`, as `student_support/service.py` asks for this migration):
-- the tables that refer to these by name keep doing so. Office-hours appointments, joins, watches, the lessons learned from
-- edits and wellbeing reviews stay a student's alone.

CREATE TABLE student_support_items_next (
 id TEXT PRIMARY KEY,
 term_id TEXT NOT NULL REFERENCES course_terms(id),
 enrollment_id TEXT NOT NULL,
 kind TEXT NOT NULL CHECK(kind IN ('followup','appointment')),
 source_key TEXT NOT NULL DEFAULT '',
 actor TEXT NOT NULL,
 created_at TEXT NOT NULL,
 -- An appointment is a meeting with a student.
 CHECK(kind='followup' OR enrollment_id NOT LIKE 'coordinator:%')
);
INSERT INTO student_support_items_next(id,term_id,enrollment_id,kind,source_key,actor,created_at)
 SELECT id,term_id,enrollment_id,kind,source_key,actor,created_at FROM student_support_items;
DROP TABLE student_support_items;
ALTER TABLE student_support_items_next RENAME TO student_support_items;
CREATE UNIQUE INDEX student_support_conversation ON student_support_items(enrollment_id,source_key)
 WHERE kind='followup' AND source_key!='';
CREATE INDEX student_support_enrollment ON student_support_items(enrollment_id,kind);
CREATE TRIGGER student_support_owner BEFORE INSERT ON student_support_items
 WHEN NEW.enrollment_id != 'coordinator:' || NEW.term_id
  AND NOT EXISTS (SELECT 1 FROM student_enrollments WHERE id=NEW.enrollment_id AND term_id=NEW.term_id)
 BEGIN SELECT RAISE(ABORT,'TEACHING-WORKFLOWS: enrollment belongs to another term'); END;
CREATE TRIGGER student_support_items_immutable BEFORE UPDATE ON student_support_items
 BEGIN SELECT RAISE(ABORT,'TEACHING-WORKFLOWS: records are immutable'); END;
CREATE TRIGGER student_support_items_retained BEFORE DELETE ON student_support_items
 BEGIN SELECT RAISE(ABORT,'TEACHING-WORKFLOWS: records are retained'); END;

CREATE TABLE student_support_suggestion_runs_next (
 id TEXT PRIMARY KEY,
 enrollment_id TEXT NOT NULL,
 item_key TEXT NOT NULL,
 item_id TEXT NOT NULL,
 source_key TEXT NOT NULL,
 source_sha256 TEXT NOT NULL,
 source_json TEXT NOT NULL CHECK(json_valid(source_json)),
 parent_id TEXT REFERENCES student_support_suggestion_runs(id),
 reason TEXT NOT NULL CHECK(reason IN ('automatic','revise','retry','answer','receipts')),
 request_id TEXT NOT NULL UNIQUE,
 request_sha256 TEXT NOT NULL,
 instruction TEXT NOT NULL,
 model TEXT NOT NULL,
 created_at TEXT NOT NULL
);
INSERT INTO student_support_suggestion_runs_next(id,enrollment_id,item_key,item_id,source_key,source_sha256,source_json,parent_id,
   reason,request_id,request_sha256,instruction,model,created_at)
 SELECT id,enrollment_id,item_key,item_id,source_key,source_sha256,source_json,parent_id,
   reason,request_id,request_sha256,instruction,model,created_at FROM student_support_suggestion_runs;
DROP TABLE student_support_suggestion_runs;
ALTER TABLE student_support_suggestion_runs_next RENAME TO student_support_suggestion_runs;
CREATE INDEX student_support_suggestion_target ON student_support_suggestion_runs(enrollment_id,item_key,created_at,id);
CREATE TRIGGER student_support_suggestion_runs_owner BEFORE INSERT ON student_support_suggestion_runs
 WHEN NOT EXISTS (SELECT 1 FROM student_enrollments WHERE id=NEW.enrollment_id)
  AND NOT EXISTS (SELECT 1 FROM course_terms WHERE 'coordinator:' || id = NEW.enrollment_id)
 BEGIN SELECT RAISE(ABORT,'TEACHING-WORKFLOWS: a suggestion run names an enrollment or a term''s coordinator'); END;
CREATE TRIGGER student_support_suggestion_runs_immutable BEFORE UPDATE ON student_support_suggestion_runs
 BEGIN SELECT RAISE(ABORT,'TEACHING-WORKFLOWS: suggestion runs are immutable'); END;
CREATE TRIGGER student_support_suggestion_runs_retained BEFORE DELETE ON student_support_suggestion_runs
 BEGIN SELECT RAISE(ABORT,'TEACHING-WORKFLOWS: suggestion runs are retained'); END;

CREATE TABLE student_support_suggestion_mutes_next (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 enrollment_id TEXT NOT NULL,
 item_key TEXT NOT NULL,
 muted INTEGER NOT NULL CHECK(muted IN (0,1)),
 request_id TEXT NOT NULL UNIQUE,
 request_sha256 TEXT NOT NULL,
 actor TEXT NOT NULL,
 created_at TEXT NOT NULL
);
INSERT INTO student_support_suggestion_mutes_next(id,enrollment_id,item_key,muted,request_id,request_sha256,actor,created_at)
 SELECT id,enrollment_id,item_key,muted,request_id,request_sha256,actor,created_at FROM student_support_suggestion_mutes;
DROP TABLE student_support_suggestion_mutes;
ALTER TABLE student_support_suggestion_mutes_next RENAME TO student_support_suggestion_mutes;
CREATE INDEX student_support_suggestion_mute_target ON student_support_suggestion_mutes(enrollment_id,item_key,id);
CREATE TRIGGER student_support_suggestion_mutes_owner BEFORE INSERT ON student_support_suggestion_mutes
 WHEN NOT EXISTS (SELECT 1 FROM student_enrollments WHERE id=NEW.enrollment_id)
  AND NOT EXISTS (SELECT 1 FROM course_terms WHERE 'coordinator:' || id = NEW.enrollment_id)
 BEGIN SELECT RAISE(ABORT,'TEACHING-WORKFLOWS: a suggestion mute names an enrollment or a term''s coordinator'); END;
CREATE TRIGGER student_support_suggestion_mutes_immutable BEFORE UPDATE ON student_support_suggestion_mutes
 BEGIN SELECT RAISE(ABORT,'TEACHING-WORKFLOWS: suggestion mutes are append-only'); END;
CREATE TRIGGER student_support_suggestion_mutes_retained BEFORE DELETE ON student_support_suggestion_mutes
 BEGIN SELECT RAISE(ABORT,'TEACHING-WORKFLOWS: suggestion mutes are retained'); END;

CREATE TABLE student_support_conversation_mail_next (
 send_id TEXT PRIMARY KEY REFERENCES gmail_mail_sends(id),
 enrollment_id TEXT NOT NULL,
 source_key TEXT NOT NULL CHECK(length(source_key) BETWEEN 1 AND 500),
 actor TEXT NOT NULL,
 created_at TEXT NOT NULL
);
INSERT INTO student_support_conversation_mail_next(send_id,enrollment_id,source_key,actor,created_at)
 SELECT send_id,enrollment_id,source_key,actor,created_at FROM student_support_conversation_mail;
DROP TABLE student_support_conversation_mail;
ALTER TABLE student_support_conversation_mail_next RENAME TO student_support_conversation_mail;
CREATE INDEX student_support_conversation_mail_conversation ON student_support_conversation_mail(enrollment_id, source_key);
CREATE TRIGGER student_support_conversation_mail_owner BEFORE INSERT ON student_support_conversation_mail
 WHEN NOT EXISTS (SELECT 1 FROM student_enrollments WHERE id=NEW.enrollment_id)
  AND NOT EXISTS (SELECT 1 FROM course_terms WHERE 'coordinator:' || id = NEW.enrollment_id)
 BEGIN SELECT RAISE(ABORT,'TEACHING-WORKFLOWS: mail written from a follow-up names an enrollment or a term''s coordinator'); END;
CREATE TRIGGER student_support_conversation_mail_immutable BEFORE UPDATE ON student_support_conversation_mail
 BEGIN SELECT RAISE(ABORT,'TEACHING-WORKFLOWS: mail written from a follow-up is append-only'); END;
CREATE TRIGGER student_support_conversation_mail_retained BEFORE DELETE ON student_support_conversation_mail
 BEGIN SELECT RAISE(ABORT,'TEACHING-WORKFLOWS: mail written from a follow-up is retained'); END;
