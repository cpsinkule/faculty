-- Recordings, step 6 (docs/decisions/recordings.md, RECORDINGS "Memos, training, search by meaning (step 6)"; COURSE-NOTES
-- "TRAINING RECORDINGS"): a TRAINING is a recording filed under a COURSE (`course_definitions.id`) to be kept for reference in
-- every term, beside the course's notes. It is a link of kind `course` whose `purpose` is `training`: the same owner and the
-- same owner key as a meeting filed under that course, so re-filing between the two is one more revision of that link, and
-- the latest revision says which it is. A training is kept, shown and searched, and is NOT read for items. No other kind of
-- link has a purpose.
ALTER TABLE recording_links ADD COLUMN purpose TEXT NOT NULL DEFAULT '' CHECK(purpose IN ('', 'training'));
CREATE TRIGGER recording_links_training BEFORE INSERT ON recording_links
WHEN NEW.purpose = 'training' AND NEW.kind != 'course'
BEGIN SELECT RAISE(ABORT,'Only a recording filed under a course is a training'); END;
