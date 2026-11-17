-- ASSISTANT: a material import that certainly changed nothing (refused before or by its destination) is 'failed', not 'uncertain'.
DROP TRIGGER assistant_material_retained;
CREATE TABLE assistant_material_requests_next (
 id TEXT PRIMARY KEY,
 request_id TEXT NOT NULL UNIQUE,
 fingerprint TEXT NOT NULL,
 kind TEXT NOT NULL,
 owner_level TEXT NOT NULL,
 owner_id TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('running','complete','uncertain','failed')),
 result_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(result_json)),
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL
);
INSERT INTO assistant_material_requests_next(id,request_id,fingerprint,kind,owner_level,owner_id,state,result_json,created_at,updated_at)
 SELECT id,request_id,fingerprint,kind,owner_level,owner_id,state,result_json,created_at,updated_at FROM assistant_material_requests;
DROP TABLE assistant_material_requests;
ALTER TABLE assistant_material_requests_next RENAME TO assistant_material_requests;
CREATE TRIGGER assistant_material_retained BEFORE DELETE ON assistant_material_requests
BEGIN SELECT RAISE(ABORT,'ASSISTANT: material receipts are retained'); END;
