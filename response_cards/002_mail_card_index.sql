-- A card's mail is read by card: the seating chart's card states, the room roster, an issue's history, resend checks and
-- Home's seat-update count. Without this index each of those reads scanned every mail row, bodies included.
CREATE INDEX IF NOT EXISTS response_card_mail_card ON response_card_mail(card_id, created_at);
