-- NAME FILLED IN (requirement, 2026-10-18). A copy printed for one student may also
-- carry that student's name, printed on the page's "Name" line. This keeps the exact words printed, fixed when the copy is made,
-- so the job's manifest and its retained PDF keep agreeing whatever the roster says later. Owner: the printed copy, by reference.
CREATE TABLE printed_copy_name_fills (
 printed_copy_id TEXT PRIMARY KEY NOT NULL REFERENCES printed_copies(id),
 text TEXT NOT NULL CHECK(length(text) BETWEEN 1 AND 120),
 created_at TEXT NOT NULL
);
CREATE TRIGGER printed_copy_name_fill_immutable BEFORE UPDATE ON printed_copy_name_fills
 BEGIN SELECT RAISE(ABORT,'A printed name is immutable'); END;
CREATE TRIGGER printed_copy_name_fill_retained BEFORE DELETE ON printed_copy_name_fills
 BEGIN SELECT RAISE(ABORT,'A printed name is retained'); END;
