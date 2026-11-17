-- ASSISTANT MEMORY PICTURES (requirement, 2026-10-10).
-- A picture the instructor attached to the chat message a memory was kept from. It belongs to the memory, through every
-- revision of it, and so to the memory's owner (HIERARCHY: the workspace, a course or a term). The bytes are a copy made for
-- keeping, since the attached file itself is scratch for a day: a PNG or JPEG of at most 2 MB held in the catalog, as a
-- student photo is (STORAGE: catalog data, in the nightly catalog backup; no payload file).
CREATE TABLE assistant_memory_pictures (
 id TEXT PRIMARY KEY,
 memory_id TEXT NOT NULL REFERENCES assistant_memory_items(id),
 position INTEGER NOT NULL CHECK(position BETWEEN 0 AND 3),
 media_type TEXT NOT NULL CHECK(media_type IN ('image/png','image/jpeg')),
 sha256 TEXT NOT NULL CHECK(length(sha256) = 64),
 size INTEGER NOT NULL CHECK(size BETWEEN 1 AND 2097152),
 image BLOB NOT NULL,
 erased INTEGER NOT NULL DEFAULT 0 CHECK(erased IN (0,1)),
 created_at TEXT NOT NULL,
 UNIQUE(memory_id, position),
 CHECK((erased=0 AND length(image)=size) OR (erased=1 AND length(image)=0))
);
-- The one permitted change of a kept picture: Erase empties its bytes (a picture that should never have been kept).
CREATE TRIGGER assistant_memory_pictures_kept BEFORE UPDATE ON assistant_memory_pictures
 WHEN NOT (NEW.erased=1 AND length(NEW.image)=0 AND NEW.id=OLD.id AND NEW.memory_id=OLD.memory_id AND NEW.position=OLD.position
   AND NEW.media_type=OLD.media_type AND NEW.sha256=OLD.sha256 AND NEW.size=OLD.size AND NEW.created_at=OLD.created_at)
 BEGIN SELECT RAISE(ABORT,'ASSISTANT MEMORY: a kept picture is only erased'); END;
CREATE TRIGGER assistant_memory_pictures_retained BEFORE DELETE ON assistant_memory_pictures
 BEGIN SELECT RAISE(ABORT,'ASSISTANT MEMORY: a kept picture is erased, never deleted'); END;
