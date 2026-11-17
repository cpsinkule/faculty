-- SCHEDULING CLASS SCHEDULE (requirement, 2026-10-10). Stored here is only what no other record holds: the planned rows, the class days'
-- topics, and which real record a planned row became. Every other date on a section's schedule is read from its owner.
-- Owner: the course term (course_terms.id). A section's own values are keyed by course_sections.id inside the term's revision; a
-- topic names a notes section of the term's course by its number, never a copy of it. Immutable revisions with exact request IDs.
CREATE TABLE scheduling_plan_items (
 id TEXT PRIMARY KEY CHECK(length(id) BETWEEN 8 AND 40),
 term_id TEXT NOT NULL REFERENCES course_terms(id),
 created_at TEXT NOT NULL
);
CREATE TRIGGER scheduling_plan_items_immutable BEFORE UPDATE ON scheduling_plan_items
 BEGIN SELECT RAISE(ABORT,'SCHEDULING: planned rows keep their identity'); END;
CREATE TRIGGER scheduling_plan_items_retained BEFORE DELETE ON scheduling_plan_items
 BEGIN SELECT RAISE(ABORT,'SCHEDULING: planned rows are retained'); END;
CREATE TABLE scheduling_plan_revisions (
 term_id TEXT NOT NULL REFERENCES course_terms(id),
 revision INTEGER NOT NULL CHECK(revision >= 1),
 items_json TEXT NOT NULL CHECK(json_valid(items_json) AND json_type(items_json) = 'array' AND length(items_json) <= 200000),
 topics_json TEXT NOT NULL CHECK(json_valid(topics_json) AND json_type(topics_json) = 'object' AND length(topics_json) <= 200000),
 request_id TEXT NOT NULL CHECK(length(request_id) BETWEEN 1 AND 100),
 request_sha256 TEXT NOT NULL CHECK(length(request_sha256) = 64),
 actor TEXT NOT NULL,
 created_at TEXT NOT NULL,
 PRIMARY KEY(term_id, revision),
 UNIQUE(term_id, request_id)
);
CREATE TRIGGER scheduling_plan_revisions_next BEFORE INSERT ON scheduling_plan_revisions
 WHEN NEW.revision != 1 + COALESCE((SELECT MAX(revision) FROM scheduling_plan_revisions WHERE term_id=NEW.term_id), 0)
 BEGIN SELECT RAISE(ABORT,'SCHEDULING: a class schedule revision must follow the latest one'); END;
CREATE TRIGGER scheduling_plan_revisions_items BEFORE INSERT ON scheduling_plan_revisions
 WHEN EXISTS (SELECT 1 FROM json_each(NEW.items_json) j WHERE NOT EXISTS (
   SELECT 1 FROM scheduling_plan_items i WHERE i.id = json_extract(j.value, '$.item') AND i.term_id = NEW.term_id))
 BEGIN SELECT RAISE(ABORT,'SCHEDULING: every planned row of a revision is one of its term''s rows'); END;
CREATE TRIGGER scheduling_plan_revisions_immutable BEFORE UPDATE ON scheduling_plan_revisions
 BEGIN SELECT RAISE(ABORT,'SCHEDULING: class schedule revisions are immutable'); END;
CREATE TRIGGER scheduling_plan_revisions_retained BEFORE DELETE ON scheduling_plan_revisions
 BEGIN SELECT RAISE(ABORT,'SCHEDULING: class schedule revisions are retained'); END;
-- Which real record a planned row became ("Once the real quiz exists, the row follows its date"): append-only, the latest row of a
-- planned row counts, an unlink names nothing. Kept apart from the revisions, so recording one never refuses a schedule being
-- edited. It names the course-wide assignment (with the term, that is the term's exam record and seating charts) or a column of the
-- term's gradebook by the identity its setup gives it.
CREATE TABLE scheduling_plan_links (
 id TEXT PRIMARY KEY CHECK(length(id) BETWEEN 8 AND 40),
 term_id TEXT NOT NULL REFERENCES course_terms(id),
 item_id TEXT NOT NULL REFERENCES scheduling_plan_items(id),
 action TEXT NOT NULL CHECK(action IN ('link','unlink')),
 assignment_id TEXT REFERENCES assignments(id),
 gradebook_item TEXT CHECK(gradebook_item IS NULL OR length(gradebook_item) BETWEEN 1 AND 100),
 request_id TEXT NOT NULL CHECK(length(request_id) BETWEEN 1 AND 100),
 request_sha256 TEXT NOT NULL CHECK(length(request_sha256) = 64),
 actor TEXT NOT NULL,
 created_at TEXT NOT NULL,
 UNIQUE(term_id, request_id),
 CHECK(CASE action WHEN 'link' THEN (assignment_id IS NOT NULL) + (gradebook_item IS NOT NULL) = 1
       ELSE assignment_id IS NULL AND gradebook_item IS NULL END)
);
CREATE INDEX scheduling_plan_links_item ON scheduling_plan_links(term_id, item_id);
CREATE TRIGGER scheduling_plan_links_scope BEFORE INSERT ON scheduling_plan_links
 WHEN NOT EXISTS (SELECT 1 FROM scheduling_plan_items i WHERE i.id = NEW.item_id AND i.term_id = NEW.term_id)
   OR (NEW.assignment_id IS NOT NULL AND (SELECT definition_id FROM assignments WHERE id = NEW.assignment_id)
       IS NOT (SELECT definition_id FROM course_terms WHERE id = NEW.term_id))
 BEGIN SELECT RAISE(ABORT,'SCHEDULING: a planned row follows a record of its own term and course'); END;
CREATE TRIGGER scheduling_plan_links_immutable BEFORE UPDATE ON scheduling_plan_links
 BEGIN SELECT RAISE(ABORT,'SCHEDULING: planned-row links are immutable'); END;
CREATE TRIGGER scheduling_plan_links_retained BEFORE DELETE ON scheduling_plan_links
 BEGIN SELECT RAISE(ABORT,'SCHEDULING: planned-row links are retained'); END;
