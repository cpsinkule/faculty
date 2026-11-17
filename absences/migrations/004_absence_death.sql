-- DEATHS WAIT (requirement, 2026-10-05): an absence revision says whether the absence is for a death, whatever its reason (a death of someone not yet confirmed
-- as immediate family is recorded as Other). Owner: the absence's section membership, like every other fact of the revision.
ALTER TABLE absence_revisions ADD COLUMN death INTEGER NOT NULL DEFAULT 0 CHECK(death IN (0, 1));
