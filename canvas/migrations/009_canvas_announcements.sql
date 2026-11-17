-- Announcements in the Canvas courses of chosen sections (requirement, 2026-10-15, after the Assistant could not publish an Exam 2
-- announcement in all their Canvas courses; canvas ANNOUNCEMENTS, canvas/announcements.py). WORKSPACE-owned, like messages: the
-- instructor's own posts, which may go to sections of several terms. They name the sections ticked by reference (course_sections.id)
-- and copy nothing of theirs.
--
-- `canvas_announcements` is the approved announcement (its text and the HTML Canvas was given); `canvas_announcement_posts` is one
-- Canvas course's post of it: both are committed BEFORE any request leaves. `canvas_announcement_outcomes` is what Canvas answered
-- for a post (the latest counts; `posted` with the topic's ID and link). A post with no outcome was interrupted: it is shown as
-- uncertain and never posted again. Everything here is append-only and retained.
CREATE TABLE canvas_announcements (
 id TEXT PRIMARY KEY,
 request_id TEXT NOT NULL UNIQUE CHECK(length(request_id) BETWEEN 1 AND 100),
 fingerprint TEXT NOT NULL CHECK(length(fingerprint) = 64),
 title TEXT NOT NULL CHECK(length(title) BETWEEN 1 AND 255),
 message_text TEXT NOT NULL CHECK(length(message_text) >= 1),
 message_html TEXT NOT NULL CHECK(length(message_html) >= 1),
 sections_json TEXT NOT NULL CHECK(json_valid(sections_json) AND json_type(sections_json)='array'),
 actor TEXT NOT NULL,
 created_at TEXT NOT NULL
);
CREATE INDEX canvas_announcements_created ON canvas_announcements(created_at);
CREATE TABLE canvas_announcement_posts (
 id TEXT PRIMARY KEY,
 announcement_id TEXT NOT NULL REFERENCES canvas_announcements(id),
 course_id TEXT NOT NULL CHECK(length(course_id) BETWEEN 1 AND 20 AND course_id NOT GLOB '*[^0-9]*'),
 course_name TEXT NOT NULL,
 sections_json TEXT NOT NULL CHECK(json_valid(sections_json) AND json_type(sections_json)='array'),
 created_at TEXT NOT NULL,
 UNIQUE(announcement_id, course_id)
);
CREATE TABLE canvas_announcement_outcomes (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 post_id TEXT NOT NULL REFERENCES canvas_announcement_posts(id),
 status TEXT NOT NULL CHECK(status IN ('posted','failed','uncertain')),
 http_status INTEGER,
 topic_id TEXT NOT NULL DEFAULT '' CHECK(topic_id NOT GLOB '*[^0-9]*'),
 url TEXT NOT NULL DEFAULT '',
 detail TEXT NOT NULL DEFAULT '',
 created_at TEXT NOT NULL
);
CREATE INDEX canvas_announcement_outcomes_post ON canvas_announcement_outcomes(post_id, id);
CREATE TRIGGER canvas_announcements_immutable BEFORE UPDATE ON canvas_announcements BEGIN SELECT RAISE(ABORT,'Canvas announcements are append-only'); END;
CREATE TRIGGER canvas_announcements_retained BEFORE DELETE ON canvas_announcements BEGIN SELECT RAISE(ABORT,'Canvas announcements are retained'); END;
CREATE TRIGGER canvas_announcement_posts_immutable BEFORE UPDATE ON canvas_announcement_posts BEGIN SELECT RAISE(ABORT,'Canvas announcement posts are append-only'); END;
CREATE TRIGGER canvas_announcement_posts_retained BEFORE DELETE ON canvas_announcement_posts BEGIN SELECT RAISE(ABORT,'Canvas announcement posts are retained'); END;
CREATE TRIGGER canvas_announcement_outcomes_immutable BEFORE UPDATE ON canvas_announcement_outcomes BEGIN SELECT RAISE(ABORT,'Canvas announcement outcomes are append-only'); END;
CREATE TRIGGER canvas_announcement_outcomes_retained BEFORE DELETE ON canvas_announcement_outcomes BEGIN SELECT RAISE(ABORT,'Canvas announcement outcomes are retained'); END;
