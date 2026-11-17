-- TEACHING-WORKFLOWS / TERM POLICIES: structured guidance belongs to the same
-- course term and immutable revision as its text projection. Existing text stays
-- untouched; the editor presents it as Additional guidance until explicitly edited.
CREATE TABLE student_support_term_policy_areas (
 term_id TEXT NOT NULL,
 revision INTEGER NOT NULL,
 areas_json TEXT NOT NULL CHECK(json_valid(areas_json) AND json_type(areas_json)='object'),
 PRIMARY KEY(term_id,revision),
 FOREIGN KEY(term_id,revision) REFERENCES student_support_term_policies(term_id,revision)
);
CREATE TRIGGER student_support_term_policy_areas_immutable BEFORE UPDATE ON student_support_term_policy_areas
 BEGIN SELECT RAISE(ABORT,'TEACHING-WORKFLOWS: term policy areas are immutable'); END;
CREATE TRIGGER student_support_term_policy_areas_retained BEFORE DELETE ON student_support_term_policy_areas
 BEGIN SELECT RAISE(ABORT,'TEACHING-WORKFLOWS: term policy areas are retained'); END;
