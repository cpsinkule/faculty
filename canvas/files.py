"""Files for Canvas modules (requirement, 2026-10-10).

A file the instructor adds to a module item is KEPT here, TERM-owned (`course_terms.id`, migration `canvas/008`): once in the
term's verified storage (`canvas-files/<file id>/content`, published immutably under the lifecycle and owner locks, checked at
every backup by `rosters.TermStorage`), whatever number of sections' courses receive it. The item names it (`content.file`,
canvas/content.py); the term's copy and a section's direct edit both name the term's file, never a copy of it. Adding the same
file under the same name again returns the row it has. Nothing here contacts Canvas: the push does (`content_service.Push
.upload_file`), and what Canvas answered for each course is a `canvas_content_file_uploads` row.

FROM THE ASSISTANT (requirement, 2026-10-10). `place`
keeps a file the instructor attached in the Assistant's chat (named by its `/api/assistant/uploads/<id>` route and read through that
route, never bytes from a model) and adds its item to one module of the term's copy as a new revision: a draft, like any other save
of the copy, so the assistant makes it at once (`assistant/policy.py` AUTO, Content drafts). Canvas changes only with the push,
which stays the instructor's Approve card. Only a chat attachment or a file already kept is placed: never a student's paper or any
other Faculty file.

Lock order: the roster lifecycle lock, the term's owner lock (its storage), the file, then ONE `BEGIN IMMEDIATE` recording it.
"""
from __future__ import annotations

import copy
import hashlib
import json
import mimetypes
import re
import unicodedata

from werkzeug.exceptions import Conflict, NotFound

from course_storage import timestamp
from document_identity.storage import publish, read_verified
from rosters import connect

from . import content as model

MAX_FILE = 100 * 1024 * 1024
MEDIA = re.compile(r"[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]{0,126}/[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]{0,126}")
FILE_ID = re.compile(r"cf_[0-9a-f]{16}")
PLACED_FROM = ("assistant.upload_file",)  # the routes `place` reads a file from: what the instructor attached in the chat


def clean_name(text):
    """A file name Canvas and any computer keep: no folders, controls or characters Windows refuses; '' when nothing is left."""
    plain = unicodedata.normalize("NFC", str(text or "")).replace("\\", "/").rsplit("/", 1)[-1]
    plain = re.sub(r'[\x00-\x1f\x7f:*?"<>|]+', " ", plain)
    return " ".join(plain.split()).strip(" .")[:200]


class ContentFiles:
    def __init__(self, service):
        self.s = service  # CanvasContentService
        self.rosters, self.path = service.rosters, service.path

    @staticmethod
    def _shown(row):
        return {"id": row["id"], "name": row["name"], "media_type": row["media_type"], "size": row["size"], "created_at": row["created_at"],
                "url": f"/api/canvas/content/{row['term_id']}/files/{row['id']}"}

    def kept(self, db, term_id):
        """{file id: {name, size, sha256}} of the term's kept files: what a save checks an item's file against and a plan compares."""
        return {row["id"]: {"name": row["name"], "size": row["size"], "sha256": row["sha256"]}
                for row in db.execute("SELECT id,name,size,sha256 FROM canvas_content_files WHERE term_id=?", (term_id,))}

    def shown(self, db, term_id):
        """The term's kept files as the editor lists them, newest first."""
        return [self._shown(row) for row in db.execute("SELECT * FROM canvas_content_files WHERE term_id=? ORDER BY created_at DESC, id", (term_id,))]

    def receipts(self, db, course_ids):
        """{course id: {folder and name: {file_id, sha256, canvas_file_id}}}: what Faculty last put at each place of each course."""
        found = {}
        for cid in course_ids:
            rows = db.execute("""SELECT u.canvas_path, u.file_id, u.canvas_file_id, f.sha256 FROM canvas_content_file_uploads u
                JOIN canvas_content_files f ON f.id=u.file_id WHERE u.course_id=? AND u.status='uploaded' ORDER BY u.id""", (str(cid),))
            found[str(cid)] = {row["canvas_path"]: {"file_id": row["file_id"], "sha256": row["sha256"], "canvas_file_id": row["canvas_file_id"]}
                               for row in rows}
        return found

    def add(self, term_id, content, filename, media="", name=None, actor="workspace"):
        """Keeps one file for the term's Canvas modules and answers its row. `name` is the name it has in Canvas (a corrected file
        keeps the name of the one it replaces); otherwise the uploaded file's own."""
        name = clean_name(name if name not in (None, "") else filename)
        if not name:
            raise ValueError("Give the file a name.")
        if not isinstance(content, bytes) or not content:
            raise ValueError("Choose a file that is not empty.")
        if len(content) > MAX_FILE:
            raise ValueError("That file is larger than 100 MB.")
        media = media if isinstance(media, str) and MEDIA.fullmatch(media) and media != "application/octet-stream" else ""
        media = media or mimetypes.guess_type(name)[0] or "application/octet-stream"
        digest = hashlib.sha256(content).hexdigest()
        with connect(self.path) as db:
            term = self.s._term(db, term_id)
        file_id = "cf_" + hashlib.sha256(f"{term['id']}\n{digest}\n{name}".encode()).hexdigest()[:16]
        path = f"canvas-files/{file_id}/content"
        with self.rosters.lock():
            with connect(self.path) as db:
                term = dict(self.s._term(db, term_id))
            with self.rosters.files(term, write=True) as root:
                publish(root / path, content)
                with connect(self.path) as db:
                    db.execute("BEGIN IMMEDIATE")
                    db.execute("""INSERT OR IGNORE INTO canvas_content_files(id,term_id,name,media_type,size,sha256,path,actor,created_at)
                                  VALUES (?,?,?,?,?,?,?,?,?)""", (file_id, term["id"], name, media, len(content), digest, path, actor, timestamp()))
                    db.commit()
                    row = db.execute("SELECT * FROM canvas_content_files WHERE id=?", (file_id,)).fetchone()
        return self._shown(row)

    def read(self, term_id, file_id):
        """(row, bytes) of a kept file of the term, read under the lifecycle and owner locks and checked against its checksum."""
        if not isinstance(file_id, str) or not FILE_ID.fullmatch(file_id):
            raise NotFound("That file was not found.")
        with self.rosters.lock():
            with connect(self.path) as db:
                row = db.execute("SELECT * FROM canvas_content_files WHERE id=? AND term_id=?", (file_id, str(term_id))).fetchone()
                if not row:
                    raise NotFound("That file was not found.")
                term = dict(self.s._term(db, term_id))
            with self.rosters.files(term) as root:
                if root is None:
                    raise NotFound("That file was not found.")
                return dict(row), read_verified(root, row["path"], row["sha256"])

    # --- a file into a module of the copy (FROM THE ASSISTANT) ---------------------------------------------------------------------
    def _placed(self, term_id, revision):
        """What `place` answers: the revision it made, its module and the item it added (that module's last item naming a file)."""
        with connect(self.path) as db:
            row = db.execute("SELECT content_json, created_at, request_sha256 FROM canvas_content_revisions WHERE term_id=? AND revision=?",
                             (term_id, revision)).fetchone()
            before = db.execute("SELECT content_json FROM canvas_content_revisions WHERE term_id=? AND revision=?", (term_id, revision - 1)).fetchone()
            kept = {f["id"]: f for f in self.shown(db, term_id)}
        content, earlier = json.loads(row["content_json"]), {i["id"] for m in json.loads(before["content_json"])["modules"] for i in m["items"]}
        module, item = next((m, i) for m in content["modules"] for i in m["items"] if i["id"] not in earlier)
        return {"revision": revision, "saved_at": row["created_at"], "module": {"id": module["id"], "name": module["name"], "published": module["published"]},
                "item": {key: item[key] for key in ("id", "title", "published", "sections", "content")}, "file": kept.get(item["content"]["file"]),
                "next": "This is a draft in Faculty's copy: Canvas has nothing yet. Push the copy for the instructor to approve."}

    def place(self, term_id, data, reader, actor="workspace"):
        """Keeps a file and adds its File item at the end of one module of the term's copy, as one new revision. `data`:
        `request_id`, `base_revision` (the copy's revision as read), `module` (its ID in the copy), and the file as `source` (the
        route of a file attached in the Assistant's chat) or `file` (the ID of a file the term already keeps); optional `title`
        (the item's, else the file's name), `name` (the file's name in Canvas), `folder` (in Canvas's Files, else the top one),
        `published` (true unless false) and `sections` (IDs; every section otherwise). The same request ID with the same fields
        answers what it did. Nothing contacts Canvas."""
        from .content_service import _request_id
        data = data if isinstance(data, dict) else {}
        request_id = _request_id(data.get("request_id"))
        fields = {key: data.get(key) for key in ("base_revision", "module", "source", "file", "title", "name", "folder", "published", "sections")}
        request = {"place_file": fields}
        with connect(self.path) as db:
            term = self.s._term(db, term_id)
            done = db.execute("SELECT revision, request_sha256 FROM canvas_content_revisions WHERE term_id=? AND request_id=?", (term["id"], request_id)).fetchone()
        if done:
            if done["request_sha256"] != model.digest(request):
                raise Conflict("This request ID was used for a different change.")
            return self._placed(term["id"], done["revision"])
        if not isinstance(fields["module"], str) or not fields["module"]:
            raise ValueError("Name the module by its ID in Faculty's copy (the outline lists them).")
        if (fields["source"] is None) == (fields["file"] is None):
            raise ValueError("Name the file once: `source` (the route of a file attached in the chat) or `file` (one Faculty already keeps).")
        for key in ("title", "name", "folder"):
            if fields[key] is not None and not isinstance(fields[key], str):
                raise ValueError(f"The {key} must be text.")
        if fields["published"] is not None and not isinstance(fields["published"], bool):
            raise ValueError("Whether it is published must be yes or no.")
        parts = [part.strip() for part in str(fields["folder"] or "").split("/") if part.strip()]
        if parts[:1] == [model.FILES_ROOT]:
            parts = parts[1:]
        if any(part in (".", "..") for part in parts):
            raise ValueError("Name the folder as it is inside Canvas's Files, like Handouts or Exams/Reviews.")
        with connect(self.path) as db:
            copy_now, latest, _ = self.s._latest(db, term["id"])
        # Refused before any file is kept: no copy yet, a stale read, a module the copy does not have.
        if copy_now is None:
            raise Conflict("Start Faculty's copy from one of the term's Canvas courses first.")
        if latest != fields["base_revision"]:
            raise Conflict("Faculty's copy was saved since it was read. Read the outline again, then place the file.")
        if not any(m["id"] == fields["module"] for m in copy_now["modules"]):
            raise ValueError("Faculty's copy has no module with that ID. Read the outline for the modules' IDs.")
        if fields["file"] is not None:
            with connect(self.path) as db:
                found = db.execute("SELECT * FROM canvas_content_files WHERE id=? AND term_id=?", (str(fields["file"]), term["id"])).fetchone()
            if not found:
                raise ValueError("Faculty does not keep that file for this term.")
            kept = self._shown(found)
        else:
            if reader is None or reader.endpoint(fields["source"]) not in PLACED_FROM:
                raise ValueError("Only a file the instructor attached in the chat can be placed (its /api/assistant/uploads/<id> route). "
                                 "Ask them to attach it with the paperclip.")
            _, meta, content = reader.read(fields["source"], fields["name"] or "")
            kept = self.add(term["id"], content, meta["name"], meta["type"], actor=actor)
        path = "/".join([model.FILES_ROOT, *parts, kept["name"]])
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            current, latest, _ = self.s._latest(db, term["id"])
            if latest != fields["base_revision"]:
                raise Conflict("Faculty's copy was saved since it was read. Read the outline again, then place the file.")
            draft = copy.deepcopy(current)
            module = next(m for m in draft["modules"] if m["id"] == fields["module"])
            if any((i.get("content") or {}) == {"name": path, "file": kept["id"]} for i in module["items"]):
                raise Conflict(f"“{module['name']}” already shows this file.")
            module["items"].append({"type": "File", "title": " ".join(str(fields["title"] or kept["name"]).split()), "indent": 0,
                                    "published": fields["published"] is not False, "sections": fields["sections"] or [],
                                    "content": {"name": path, "file": kept["id"]}})
            content = model.normalize(draft, current, sections=self.s._sections(db, term["id"]),
                                      files={key: meta["name"] for key, meta in self.kept(db, term["id"]).items()})
            self.s._record(db, term["id"], latest, content, request_id, request, actor)
        return self._placed(term["id"], latest + 1)

    def record(self, op, course_id, status, push_id, actor, canvas_file_id="", http_status=None, detail="", section_id=None):
        """Canvas's answer to one `upload_file` operation, as it comes."""
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("""INSERT INTO canvas_content_file_uploads(file_id,course_id,canvas_path,status,canvas_file_id,replaced,http_status,
                detail,push_id,section_id,actor,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                       (op["key"], str(course_id), op["path"], status, str(canvas_file_id or ""), int(bool(op.get("replace"))), http_status,
                        str(detail or "")[:500], push_id, section_id, actor, timestamp()))
