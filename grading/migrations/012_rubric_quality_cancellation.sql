-- Cancellation is an explicit marker on the retained check. The original status
-- CHECK and all recorded migration history stay unchanged.
ALTER TABLE frq_rubric_quality_checks ADD COLUMN cancelled INTEGER NOT NULL DEFAULT 0 CHECK(cancelled IN (0,1));
