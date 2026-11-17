-- Scrapping a quiz draft (requirement, 2026-10-01). A draft is
-- retained (quiz_draft_retained), so scrapping is one record beside it, owned by the draft (course-wide, like the draft itself): Faculty
-- lists the draft no more, refuses its revisions and approvals, and stops a revision still running in the same transaction. A draft
-- whose approval was carried out, or is being carried out, is never scrapped. Its material, revisions, compiled files and cloud
-- receipts stay where they are. `actor` is who asked (`request_actor`: the instructor's pages, or the assistant after an Approve card).
CREATE TABLE quiz_draft_scraps (
 draft_id TEXT PRIMARY KEY REFERENCES quiz_drafts(id),
 actor TEXT NOT NULL CHECK(actor IN ('workspace','assistant')),
 created_at TEXT NOT NULL
);
CREATE TRIGGER quiz_draft_scrap_immutable BEFORE UPDATE ON quiz_draft_scraps
 BEGIN SELECT RAISE(ABORT,'A scrapped quiz draft stays scrapped'); END;
CREATE TRIGGER quiz_draft_scrap_retained BEFORE DELETE ON quiz_draft_scraps
 BEGIN SELECT RAISE(ABORT,'A scrapped quiz draft stays scrapped'); END;
