-- TEACHING-WORKFLOWS: enrollment-owned watch identities and bounded due polling.
-- 014 is already recorded; additions belong in a new immutable migration.
CREATE INDEX student_support_watch_due ON student_support_watches(state,next_check,id);
CREATE TRIGGER student_support_watch_identity_immutable BEFORE UPDATE ON student_support_watches
 WHEN NEW.id!=OLD.id OR NEW.created_at!=OLD.created_at
 BEGIN SELECT RAISE(ABORT,'TEACHING-WORKFLOWS: watch identity is immutable'); END;
