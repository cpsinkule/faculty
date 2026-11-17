-- POSTING GRADES (docs/decisions/canvas.md; requirement, 2026-10-06). A post is the receipt of the instructor's approval of one previewed Post or Hide of a column's grades in every
-- Canvas course it goes to (owned by the TERM, like a send), with its progress: only a running post's status and progress change.
-- Each course's assignment is recorded uncertain BEFORE Canvas is asked and settled by reading Canvas back, one immutable row per
-- step. What Faculty last read of an assignment's posting (how many grades students do not see) is kept for the grid's mark, one
-- immutable row per read; the latest of an assignment counts.
CREATE TABLE gradebook_canvas_posts (
    id TEXT PRIMARY KEY,
    term_id TEXT NOT NULL REFERENCES course_terms(id),
    item_id TEXT NOT NULL CHECK(length(item_id) BETWEEN 1 AND 100),
    action TEXT NOT NULL CHECK(action IN ('post', 'hide')),
    send_id TEXT REFERENCES gradebook_canvas_sends(id),
    request_id TEXT NOT NULL CHECK(length(request_id) BETWEEN 8 AND 160),
    request_sha256 TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    plan_json TEXT NOT NULL CHECK(json_valid(plan_json)),
    status TEXT NOT NULL CHECK(status IN ('running', 'done', 'failed', 'interrupted')),
    progress_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(progress_json)),
    actor TEXT NOT NULL,
    created_at TEXT NOT NULL,
    finished_at TEXT,
    UNIQUE(term_id, request_id)
);
CREATE INDEX gradebook_canvas_posts_term ON gradebook_canvas_posts(term_id, item_id, created_at);
CREATE TRIGGER gradebook_canvas_post_owner BEFORE INSERT ON gradebook_canvas_posts
WHEN NEW.send_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM gradebook_canvas_sends WHERE id = NEW.send_id AND term_id = NEW.term_id)
BEGIN SELECT RAISE(ABORT, 'Canvas post owner mismatch'); END;
CREATE TRIGGER gradebook_canvas_post_fixed BEFORE UPDATE ON gradebook_canvas_posts
WHEN NEW.id != OLD.id OR NEW.term_id != OLD.term_id OR NEW.item_id != OLD.item_id OR NEW.action != OLD.action
  OR NEW.send_id IS NOT OLD.send_id OR NEW.request_id != OLD.request_id OR NEW.request_sha256 != OLD.request_sha256
  OR NEW.fingerprint != OLD.fingerprint OR NEW.plan_json != OLD.plan_json OR NEW.actor != OLD.actor
  OR NEW.created_at != OLD.created_at OR OLD.status != 'running'
BEGIN SELECT RAISE(ABORT, 'A Canvas post keeps its approval; only a running post records progress'); END;
CREATE TRIGGER gradebook_canvas_posts_retained BEFORE DELETE ON gradebook_canvas_posts
BEGIN SELECT RAISE(ABORT, 'Canvas posts are retained'); END;

CREATE TABLE gradebook_canvas_posted (
    id TEXT PRIMARY KEY,
    post_id TEXT NOT NULL REFERENCES gradebook_canvas_posts(id),
    term_id TEXT NOT NULL REFERENCES course_terms(id),
    item_id TEXT NOT NULL CHECK(length(item_id) BETWEEN 1 AND 100),
    course_id TEXT NOT NULL CHECK(length(course_id) BETWEEN 1 AND 20 AND course_id NOT GLOB '*[^0-9]*'),
    assignment_id TEXT NOT NULL CHECK(length(assignment_id) BETWEEN 1 AND 20 AND assignment_id NOT GLOB '*[^0-9]*'),
    state TEXT NOT NULL CHECK(state IN ('accepted', 'failed', 'uncertain')),
    progress_id TEXT CHECK(progress_id IS NULL OR (length(progress_id) BETWEEN 1 AND 20 AND progress_id NOT GLOB '*[^0-9]*')),
    canvas_json TEXT NOT NULL CHECK(json_valid(canvas_json)),
    created_at TEXT NOT NULL
);
CREATE INDEX gradebook_canvas_posted_post ON gradebook_canvas_posted(post_id, course_id, created_at);
CREATE TRIGGER gradebook_canvas_posted_owner BEFORE INSERT ON gradebook_canvas_posted
WHEN NOT EXISTS (SELECT 1 FROM gradebook_canvas_posts WHERE id = NEW.post_id AND term_id = NEW.term_id AND item_id = NEW.item_id)
BEGIN SELECT RAISE(ABORT, 'Canvas post receipt owner mismatch'); END;
CREATE TRIGGER gradebook_canvas_posted_immutable BEFORE UPDATE ON gradebook_canvas_posted
BEGIN SELECT RAISE(ABORT, 'Canvas post receipts are immutable'); END;
CREATE TRIGGER gradebook_canvas_posted_retained BEFORE DELETE ON gradebook_canvas_posted
BEGIN SELECT RAISE(ABORT, 'Canvas post receipts are retained'); END;

CREATE TABLE gradebook_canvas_posting (
    id TEXT PRIMARY KEY,
    term_id TEXT NOT NULL REFERENCES course_terms(id),
    item_id TEXT NOT NULL CHECK(length(item_id) BETWEEN 1 AND 100),
    course_id TEXT NOT NULL CHECK(length(course_id) BETWEEN 1 AND 20 AND course_id NOT GLOB '*[^0-9]*'),
    assignment_id TEXT NOT NULL CHECK(length(assignment_id) BETWEEN 1 AND 20 AND assignment_id NOT GLOB '*[^0-9]*'),
    manual INTEGER NOT NULL CHECK(manual IN (0, 1)),
    graded INTEGER NOT NULL CHECK(graded >= 0),
    hidden INTEGER NOT NULL CHECK(hidden >= 0 AND hidden <= graded),
    source TEXT NOT NULL CHECK(source IN ('sync', 'nightly', 'post', 'hide')),
    read_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX gradebook_canvas_posting_latest ON gradebook_canvas_posting(term_id, course_id, assignment_id, read_at);
CREATE TRIGGER gradebook_canvas_posting_immutable BEFORE UPDATE ON gradebook_canvas_posting
BEGIN SELECT RAISE(ABORT, 'Canvas posting reads are immutable'); END;
CREATE TRIGGER gradebook_canvas_posting_retained BEFORE DELETE ON gradebook_canvas_posting
BEGIN SELECT RAISE(ABORT, 'Canvas posting reads are retained'); END;
