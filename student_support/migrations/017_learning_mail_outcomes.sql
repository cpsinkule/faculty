-- ASSISTANT: an enrollment-owned learning case may refer to the confirmed Mail replacement.
-- Existing final_action_id remains the replaced action for those cases; final_send_id identifies the sent correction.
ALTER TABLE student_support_learning_cases ADD COLUMN final_send_id TEXT REFERENCES gmail_mail_sends(id);
