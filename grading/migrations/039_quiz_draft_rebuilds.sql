-- Rebuilding an approved quiz in Faculty's current page layout (requirement, 2026-10-01): every version compiled again from the approved quiz, registered as a new printed version of the same assignment, the
-- term plan's questions moved onto them (the earlier questions withdrawn from the plan, the new ones set up as the approval did) and new
-- print jobs in place of the superseded ones. Allowed only before anything was printed back, graded or edited by the instructor.
-- One retained record per rebuild, owned by the approval it rebuilds; its progress and result name what it withdrew and what it made.
CREATE TABLE IF NOT EXISTS quiz_draft_rebuilds (
 id TEXT PRIMARY KEY,
 draft_id TEXT NOT NULL REFERENCES quiz_drafts(id),
 approval_id TEXT NOT NULL REFERENCES quiz_draft_approvals(id),
 request_id TEXT NOT NULL UNIQUE,
 request_sha256 TEXT NOT NULL,
 input_json TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('pending','running','complete','failed')),
 progress_json TEXT NOT NULL DEFAULT '{}',
 result_json TEXT,
 error TEXT NOT NULL DEFAULT '',
 not_before TEXT NOT NULL DEFAULT '',
 actor TEXT NOT NULL,
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS quiz_draft_rebuild_once ON quiz_draft_rebuilds(approval_id) WHERE status IN ('pending','running');
CREATE INDEX IF NOT EXISTS quiz_draft_rebuild_queue ON quiz_draft_rebuilds(status,not_before);
CREATE TRIGGER IF NOT EXISTS quiz_draft_rebuild_inputs_immutable BEFORE UPDATE OF draft_id,approval_id,request_id,request_sha256,input_json,actor,created_at ON quiz_draft_rebuilds
 BEGIN SELECT RAISE(ABORT,'Quiz rebuild inputs are immutable'); END;
CREATE TRIGGER IF NOT EXISTS quiz_draft_rebuild_result_immutable BEFORE UPDATE ON quiz_draft_rebuilds
 WHEN OLD.status IN ('complete','failed') BEGIN SELECT RAISE(ABORT,'Quiz rebuild results are immutable'); END;
CREATE TRIGGER IF NOT EXISTS quiz_draft_rebuild_retained BEFORE DELETE ON quiz_draft_rebuilds
 BEGIN SELECT RAISE(ABORT,'Quiz rebuilds are retained'); END;
