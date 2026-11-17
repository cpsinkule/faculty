-- TEACHING-WORKFLOWS MEETING HOURS, MAKEUP ROOMS (requirement, 2026-10-05): the office-hours rooms where a quiz makeup can be taken
-- during office hours, kept in the same term-owned revision as the meeting hours. Revisions recorded before this have none.
ALTER TABLE student_support_meeting_hours ADD COLUMN makeup_rooms_json TEXT NOT NULL DEFAULT '[]'
 CHECK(json_valid(makeup_rooms_json) AND json_type(makeup_rooms_json)='array' AND length(makeup_rooms_json) <= 2000);
