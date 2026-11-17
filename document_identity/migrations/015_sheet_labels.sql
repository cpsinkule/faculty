-- SCANTRON LABELS (requirement, 2026-10-15). A print job may give each printed copy one more peel-off label for that student's bubble sheet. It is
-- no page of the paper: scan intake leaves the sheet an unlabeled page (so the paper, its sequence grouping and bubble detection
-- see what they saw before), and a bubble sheet read takes its student from the label. Owner: the printed copy, by reference.
-- The token shares the page stamps' format; a token is never in both tables.
CREATE TABLE sheet_labels (
 id TEXT PRIMARY KEY NOT NULL, printed_copy_id TEXT NOT NULL UNIQUE REFERENCES printed_copies(id), token TEXT NOT NULL UNIQUE
 CHECK(length(token)=29 AND substr(token,1,3)='T1-' AND substr(token,4,1) BETWEEN '0' AND '7'
 AND substr(token,4) NOT GLOB '*[^0123456789ABCDEFGHJKMNPQRSTVWXYZ]*'),
 created_at TEXT NOT NULL
);
CREATE TRIGGER sheet_label_immutable BEFORE UPDATE ON sheet_labels BEGIN SELECT RAISE(ABORT,'Sheet labels are immutable'); END;
CREATE TRIGGER sheet_label_retained BEFORE DELETE ON sheet_labels BEGIN SELECT RAISE(ABORT,'Sheet labels are retained'); END;
