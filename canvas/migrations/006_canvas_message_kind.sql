-- Group or individual messages (requirement, 2026-10-01). Each course's delivery now says what Canvas was asked for: `group`,
-- one conversation every recipient shares, or `individual`, one conversation per recipient. Every delivery recorded before this
-- column existed was sent individually (the only kind there was), which its default says.
ALTER TABLE canvas_message_deliveries ADD COLUMN kind TEXT NOT NULL DEFAULT 'individual' CHECK(kind IN ('individual','group'));
