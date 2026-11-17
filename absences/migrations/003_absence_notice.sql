-- RULE 7 NOTICE (requirement, 2026-10-05): an absence revision may name the student's own
-- message that first told the instructor about the absence, a reference to the communications record (comm_messages.id), never a
-- copy. Owner: the absence's section membership, like every other fact of the revision; NULL is no notice on record.
ALTER TABLE absence_revisions ADD COLUMN notice_message_id TEXT CHECK(notice_message_id IS NULL OR length(notice_message_id) BETWEEN 1 AND 64);
