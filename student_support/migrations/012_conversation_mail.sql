-- TEACHING-WORKFLOWS WRITTEN FROM A FOLLOW-UP (2026-10-07, requirement, of an email written with a follow-up's "Write to
-- student…" that Gmail sent as a new thread, so the follow-up still showed their older reply: "I should be able to see a reply I
-- send through the follow-up"): the Mail send written from one student's conversation on the Follow-up page. Once the
-- communications record captured that email for the student, the follow-up shows it as the instructor's reply, wherever Gmail
-- threaded it; its status is unchanged. Enrollment-owned (the student's conversation, `source_key` as the follow-up names it).
-- Append-only; nothing is sent here and no message text is copied (the text stays in Mail's own send record).
CREATE TABLE student_support_conversation_mail (
 send_id TEXT PRIMARY KEY REFERENCES gmail_mail_sends(id),
 enrollment_id TEXT NOT NULL REFERENCES student_enrollments(id),
 source_key TEXT NOT NULL CHECK(length(source_key) BETWEEN 1 AND 500),
 actor TEXT NOT NULL,
 created_at TEXT NOT NULL
);
CREATE INDEX student_support_conversation_mail_conversation ON student_support_conversation_mail(enrollment_id, source_key);
CREATE TRIGGER student_support_conversation_mail_immutable BEFORE UPDATE ON student_support_conversation_mail
 BEGIN SELECT RAISE(ABORT,'TEACHING-WORKFLOWS: mail written from a follow-up is append-only'); END;
CREATE TRIGGER student_support_conversation_mail_retained BEFORE DELETE ON student_support_conversation_mail
 BEGIN SELECT RAISE(ABORT,'TEACHING-WORKFLOWS: mail written from a follow-up is retained'); END;
