-- ASSISTANT: an explicitly reviewed watch may prepare the next review.
-- Kept separate: 004_preparations was already recorded by workspace maintenance.
ALTER TABLE assistant_watches ADD COLUMN prepare_review INTEGER NOT NULL DEFAULT 0 CHECK(prepare_review IN (0,1));
