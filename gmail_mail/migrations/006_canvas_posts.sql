-- Replies to Canvas messages posted in the Canvas conversation itself (requirement, 2026-10-02). WORKSPACE-owned, like every Mail page send. The intent is the ordinary `gmail_mail_sends` row (To, Cc and Bcc
-- empty: nothing goes through Gmail) and this row says where in Canvas it goes; both are committed in one transaction BEFORE the
-- request leaves. `gmail_mail_outcomes` records Canvas's answer: its `thread_id` is the Canvas conversation, its `message_id` stays
-- empty (that column names Gmail messages only). Append-only and retained.
CREATE TABLE gmail_mail_canvas_posts (
 send_id TEXT PRIMARY KEY REFERENCES gmail_mail_sends(id),
 conversation_id TEXT NOT NULL CHECK(length(conversation_id) BETWEEN 1 AND 20 AND conversation_id NOT GLOB '*[^0-9]*'),
 recipient_user_id TEXT NOT NULL CHECK(length(recipient_user_id) BETWEEN 1 AND 40),
 recipient_name TEXT NOT NULL,
 answered_message_id TEXT NOT NULL CHECK(length(answered_message_id) BETWEEN 1 AND 40),
 course_name TEXT NOT NULL DEFAULT '',
 created_at TEXT NOT NULL
);
CREATE TRIGGER gmail_mail_canvas_posts_immutable BEFORE UPDATE ON gmail_mail_canvas_posts BEGIN SELECT RAISE(ABORT,'Canvas post records are append-only'); END;
CREATE TRIGGER gmail_mail_canvas_posts_retained BEFORE DELETE ON gmail_mail_canvas_posts BEGIN SELECT RAISE(ABORT,'Canvas post records are retained'); END;
