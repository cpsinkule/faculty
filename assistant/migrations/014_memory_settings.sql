-- ASSISTANT-MEMORY "ONE PAGE" (requirement, 2026-11-10).
-- The switches and numbers of that page. Owner: the workspace (HIERARCHY): one row, the instructor's own settings for how
-- Faculty learns, read by the Assistant's memory (ASSISTANT-MEMORY) and by rubric learning (GRADING "RUBRIC MEMORIES").
-- A number left NULL is the measured value the code names; `learning_since` is the instant learning from corrections was
-- last turned on, so nothing corrected while it was off is learned from afterwards.
CREATE TABLE memory_settings (
 id INTEGER PRIMARY KEY CHECK(id = 1),
 version INTEGER NOT NULL DEFAULT 0 CHECK(version >= 0),
 capture INTEGER NOT NULL DEFAULT 1 CHECK(capture IN (0,1)),
 learning INTEGER NOT NULL DEFAULT 1 CHECK(learning IN (0,1)),
 learning_since TEXT NOT NULL DEFAULT '',
 rubric_learning INTEGER NOT NULL DEFAULT 1 CHECK(rubric_learning IN (0,1)),
 given INTEGER CHECK(given IS NULL OR given BETWEEN 1 AND 20),
 use_gate REAL CHECK(use_gate IS NULL OR use_gate BETWEEN 0.3 AND 0.9),
 always_chars INTEGER CHECK(always_chars IS NULL OR always_chars BETWEEN 0 AND 20000),
 updated_at TEXT NOT NULL DEFAULT ''
);
INSERT INTO memory_settings(id) VALUES(1);
CREATE TRIGGER memory_settings_one BEFORE DELETE ON memory_settings
 BEGIN SELECT RAISE(ABORT,'ASSISTANT MEMORY: the settings row is kept'); END;
