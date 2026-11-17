-- TEACHING-WORKFLOWS: each record belongs to a student's permanent term enrollment.
CREATE TABLE student_support_items (
 id TEXT PRIMARY KEY,
 term_id TEXT NOT NULL REFERENCES course_terms(id),
 enrollment_id TEXT NOT NULL REFERENCES student_enrollments(id),
 kind TEXT NOT NULL CHECK(kind IN ('followup','appointment')),
 source_key TEXT NOT NULL DEFAULT '',
 actor TEXT NOT NULL,
 created_at TEXT NOT NULL
);
CREATE UNIQUE INDEX student_support_conversation ON student_support_items(enrollment_id,source_key)
 WHERE kind='followup' AND source_key!='';
CREATE INDEX student_support_enrollment ON student_support_items(enrollment_id,kind);
CREATE TRIGGER student_support_owner BEFORE INSERT ON student_support_items
 WHEN NOT EXISTS (SELECT 1 FROM student_enrollments WHERE id=NEW.enrollment_id AND term_id=NEW.term_id)
 BEGIN SELECT RAISE(ABORT,'TEACHING-WORKFLOWS: enrollment belongs to another term'); END;
CREATE TRIGGER student_support_items_immutable BEFORE UPDATE ON student_support_items
 BEGIN SELECT RAISE(ABORT,'TEACHING-WORKFLOWS: records are immutable'); END;
CREATE TRIGGER student_support_items_retained BEFORE DELETE ON student_support_items
 BEGIN SELECT RAISE(ABORT,'TEACHING-WORKFLOWS: records are retained'); END;
CREATE TABLE student_support_revisions (
 item_id TEXT NOT NULL REFERENCES student_support_items(id),
 revision INTEGER NOT NULL CHECK(revision>0),
 content_json TEXT NOT NULL CHECK(json_valid(content_json)),
 actor TEXT NOT NULL,
 created_at TEXT NOT NULL,
 PRIMARY KEY(item_id,revision)
);
CREATE TRIGGER student_support_next BEFORE INSERT ON student_support_revisions
 WHEN NEW.revision!=1+COALESCE((SELECT MAX(revision) FROM student_support_revisions WHERE item_id=NEW.item_id),0)
 BEGIN SELECT RAISE(ABORT,'TEACHING-WORKFLOWS: revision must follow its predecessor'); END;
CREATE TRIGGER student_support_revisions_immutable BEFORE UPDATE ON student_support_revisions
 BEGIN SELECT RAISE(ABORT,'TEACHING-WORKFLOWS: revisions are immutable'); END;
CREATE TRIGGER student_support_revisions_retained BEFORE DELETE ON student_support_revisions
 BEGIN SELECT RAISE(ABORT,'TEACHING-WORKFLOWS: revisions are retained'); END;
CREATE TABLE student_support_requests (
 term_id TEXT NOT NULL REFERENCES course_terms(id),
 request_id TEXT NOT NULL,
 fingerprint TEXT NOT NULL,
 item_id TEXT NOT NULL REFERENCES student_support_items(id),
 revision INTEGER NOT NULL,
 created_at TEXT NOT NULL,
 PRIMARY KEY(term_id,request_id)
);
CREATE TRIGGER student_support_requests_immutable BEFORE UPDATE ON student_support_requests
 BEGIN SELECT RAISE(ABORT,'TEACHING-WORKFLOWS: requests are immutable'); END;
CREATE TRIGGER student_support_requests_retained BEFORE DELETE ON student_support_requests
 BEGIN SELECT RAISE(ABORT,'TEACHING-WORKFLOWS: requests are retained'); END;
