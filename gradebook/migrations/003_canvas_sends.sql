-- Sending gradebook values to Canvas (README.md "Sending to Canvas"; requirement, 2026-09-28). A send is the receipt of the instructor's approval of one previewed plan (owned by the TERM, like the requests that
-- write the gradebook), with its progress: only a running send's status and progress change. Every value it sent is recorded
-- with what Canvas showed afterwards, one immutable row per value, owned by the student's permanent SECTION membership: that is
-- how the next send tells a grade Faculty put in Canvas from one someone else entered there, and how an import from Canvas knows
-- Faculty's own values when it reads them back.
CREATE TABLE gradebook_canvas_sends (
    id TEXT PRIMARY KEY,
    term_id TEXT NOT NULL REFERENCES course_terms(id),
    section_id TEXT REFERENCES course_sections(id),
    request_id TEXT NOT NULL CHECK(length(request_id) BETWEEN 8 AND 120),
    request_sha256 TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    plan_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('running', 'done', 'failed', 'cancelled', 'interrupted')),
    progress_json TEXT NOT NULL DEFAULT '{}',
    actor TEXT NOT NULL,
    created_at TEXT NOT NULL,
    finished_at TEXT,
    UNIQUE(term_id, request_id)
);
CREATE INDEX gradebook_canvas_sends_term ON gradebook_canvas_sends(term_id, created_at);
CREATE TRIGGER gradebook_canvas_send_fixed BEFORE UPDATE ON gradebook_canvas_sends
WHEN NEW.id != OLD.id OR NEW.term_id != OLD.term_id OR NEW.section_id IS NOT OLD.section_id OR NEW.request_id != OLD.request_id
  OR NEW.request_sha256 != OLD.request_sha256 OR NEW.fingerprint != OLD.fingerprint OR NEW.plan_json != OLD.plan_json
  OR NEW.actor != OLD.actor OR NEW.created_at != OLD.created_at OR OLD.status != 'running'
BEGIN SELECT RAISE(ABORT, 'A Canvas send keeps its approval; only a running send records progress'); END;
CREATE TRIGGER gradebook_canvas_sends_retained BEFORE DELETE ON gradebook_canvas_sends
BEGIN SELECT RAISE(ABORT, 'Canvas sends are retained'); END;

CREATE TABLE gradebook_canvas_sent (
    id TEXT PRIMARY KEY,
    send_id TEXT NOT NULL REFERENCES gradebook_canvas_sends(id),
    term_id TEXT NOT NULL REFERENCES course_terms(id),
    section_id TEXT NOT NULL,
    enrollment_id TEXT NOT NULL,
    item_id TEXT NOT NULL CHECK(length(item_id) BETWEEN 1 AND 100),
    score_ref TEXT REFERENCES gradebook_scores(id),
    course_id TEXT NOT NULL CHECK(length(course_id) BETWEEN 1 AND 20 AND course_id NOT GLOB '*[^0-9]*'),
    assignment_id TEXT NOT NULL CHECK(length(assignment_id) BETWEEN 1 AND 20 AND assignment_id NOT GLOB '*[^0-9]*'),
    canvas_user_id TEXT NOT NULL CHECK(length(canvas_user_id) BETWEEN 1 AND 20 AND canvas_user_id NOT GLOB '*[^0-9]*'),
    sent_json TEXT NOT NULL,
    canvas_json TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('accepted', 'failed', 'uncertain')),
    created_at TEXT NOT NULL,
    FOREIGN KEY(section_id, enrollment_id) REFERENCES roster_memberships(section_id, enrollment_id)
);
CREATE INDEX gradebook_canvas_sent_value ON gradebook_canvas_sent(term_id, course_id, assignment_id, canvas_user_id, created_at);
CREATE INDEX gradebook_canvas_sent_membership ON gradebook_canvas_sent(section_id, enrollment_id);
CREATE TRIGGER gradebook_canvas_sent_owner BEFORE INSERT ON gradebook_canvas_sent
WHEN NOT EXISTS (SELECT 1 FROM gradebook_canvas_sends WHERE id = NEW.send_id AND term_id = NEW.term_id)
  OR NOT EXISTS (SELECT 1 FROM course_sections WHERE id = NEW.section_id AND term_id = NEW.term_id)
BEGIN SELECT RAISE(ABORT, 'Canvas send receipt owner mismatch'); END;
CREATE TRIGGER gradebook_canvas_sent_immutable BEFORE UPDATE ON gradebook_canvas_sent
BEGIN SELECT RAISE(ABORT, 'Canvas send receipts are immutable'); END;
CREATE TRIGGER gradebook_canvas_sent_retained BEFORE DELETE ON gradebook_canvas_sent
BEGIN SELECT RAISE(ABORT, 'Canvas send receipts are retained'); END;
