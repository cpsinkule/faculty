"""One section's Canvas course edited directly (requirement, 2026-09-30). canvas/README.md "Editing one section directly".

Nothing is kept of the course but the receipt of each approved push. The page reads the course (`load`): its modules, pages and
assignments in Faculty's terms with keys derived from their Canvas IDs (`content.direct`), and what each held as read (`base`,
`content.object_states`). The edit is browser memory. A preview reads the course again and plans with the ordinary rules
(`content.plan`) for that one course, each object linked to itself with the snapshot of the page's `base`: what the instructor
changed is written, what someone changed in Canvas since the page was opened is asked about (Replace), removals are offered (an
item or module the instructor removed is ticked, a page or assignment never is). The push re-plans and is refused (409) unless it is
the approved plan, then runs in a thread like a term push, one course, recording a `canvas_section_pushes` row (SECTION-owned).

THE TERM'S COPY IS NEVER CHANGED BY IT (a narrower edit never propagates to its parent) and no `canvas_content_links` row is
written: the copy's next preview sees a changed object it manages as edited in Canvas and asks, redoes its own order of modules
and items, and makes again an object it manages that was deleted here. The page says so for those objects (`managed`).
"""
from __future__ import annotations

import json
import re
import threading
import uuid

from werkzeug.exceptions import Conflict, NotFound

from course_storage import timestamp
from rosters import connect

from . import content as model
from .content_service import Push, _request_id
from .service import _link, _section

DIGEST = re.compile(r"[0-9a-f]{64}")


class SectionPush(Push):
    """A direct push's one course: the same operations, answers kept in the push's own progress (`receipts`) instead of the term's
    links, which a direct edit never touches."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.section_id = self.found["section"]["section_id"]  # on each file upload's receipt (canvas FILES)

    def _record(self, kind, key, canvas_id, sent, canvas_state, source="pushed"):
        self.progress.setdefault("receipts", []).append({"kind": kind, "key": key, "canvas_id": str(canvas_id or ""), "source": source})

    def _forget(self, kind, canvas_id):
        self.progress.setdefault("receipts", []).append({"kind": kind, "key": "", "canvas_id": str(canvas_id), "source": "removed"})


class CanvasSectionContent:
    def __init__(self, service):
        self.s = service  # CanvasContentService: the kept session, the course reads, the push runner and who is pushing
        self.rosters, self.path = service.rosters, service.path

    # --- Faculty's records ---------------------------------------------------------------------------------------------------
    def _place(self, db, section_id):
        """The section, its linked Canvas course and the term's sections sharing that course."""
        section = _section(db, section_id)
        link = _link(db, section["section_id"])
        if not link:
            raise Conflict("Link this section's Canvas course first (on its section tab).")
        targets, _ = self.s._targets(db, section["term_id"])
        target = next(t for t in targets if t["course_id"] == link["course_id"])
        return section, link, target

    def _managed(self, db, term_id, course_id):
        """The direct keys of the objects the term's copy manages in this course (its latest links with a Canvas ID)."""
        links = self.s._links(db, term_id).get(course_id, {})
        return sorted(model.direct_key(kind, link["canvas_id"]) for (kind, _), link in links.items()
                      if link["canvas_id"] and link["source"] in ("pushed", "matched", "paired"))

    def _push_view(self, row):
        status = row["status"]
        if status == "running" and self.s.canvas.owner and row["id"] not in self.s._active:  # CanvasService.owner
            with connect(self.path) as db:
                db.execute("UPDATE canvas_section_pushes SET status='interrupted', finished_at=? WHERE id=? AND status='running'", (timestamp(), row["id"]))
            status = "interrupted"
        return {"id": row["id"], "course_id": row["course_id"], "status": status, "progress": json.loads(row["progress_json"] or "{}"),
                "created_at": row["created_at"], "finished_at": row["finished_at"], "actor": row["actor"]}

    def _progress(self, push_id, progress, status=None):
        with connect(self.path) as db:
            if status:
                db.execute("UPDATE canvas_section_pushes SET progress_json=?, status=?, finished_at=? WHERE id=? AND status='running'",
                           (json.dumps(progress), status, timestamp(), push_id))
            else:
                db.execute("UPDATE canvas_section_pushes SET progress_json=? WHERE id=? AND status='running'", (json.dumps(progress), push_id))

    # --- reading the course ----------------------------------------------------------------------------------------------------
    def _direct(self, course):
        content, skipped = model.direct(course)
        try:
            content = model.normalize(content, content, direct=True, canvas_read=True)
        except ValueError as error:
            raise ValueError(f"This Canvas course holds something Faculty's editor cannot: {error}") from None
        return content, skipped

    def load(self, section_id):
        """The section's Canvas course to edit: its content with Canvas-derived keys, what each object held (`base`, sent back
        with the preview), which of them the term's copy manages, and the latest direct push. Reads Canvas; writes nothing."""
        with self.rosters.lock(), connect(self.path) as db:
            section, link, target = self._place(db, section_id)
            push = db.execute("SELECT * FROM canvas_section_pushes WHERE course_id=? ORDER BY created_at DESC, rowid DESC LIMIT 1",
                              (link["course_id"],)).fetchone()
        course = self.s._read([link["course_id"]])[link["course_id"]]
        content, skipped = self._direct(course)
        with self.rosters.lock(), connect(self.path) as db:
            managed = self._managed(db, section["term_id"], link["course_id"])
        return {"section": section, "course": target, "content": content, "base": model.object_states(content, course), "skipped": skipped,
                "managed": managed, "push": self._push_view(push) if push else None, "canvas": self.s.canvas.status()}

    @staticmethod
    def _base(raw):
        if not isinstance(raw, dict) or len(raw) > 5000:
            raise ValueError("Send back what the page was opened on (`base`).")
        clean = {}
        for key, snap in raw.items():
            kind = key.split(":", 1)[0] if isinstance(key, str) else ""
            if kind not in model.KINDS or not isinstance(snap, dict) or not all(isinstance(snap.get(k), str) and DIGEST.fullmatch(snap[k]) for k in ("sent", "canvas")):
                raise ValueError("What the page was opened on (`base`) is not Faculty's.")
            clean[key] = {"sent": snap["sent"], "canvas": snap["canvas"]}
        return clean

    def _plan(self, section_id, data):
        data = data if isinstance(data, dict) else {}
        base = self._base(data.get("base"))
        with self.rosters.lock(), connect(self.path) as db:
            section, link, target = self._place(db, section_id)
            # FILES: a file added here is the term's kept file (shared by reference, never copied to the section).
            kept, receipts = self.s.files.kept(db, section["term_id"]), self.s.files.receipts(db, [link["course_id"]])
        cid = link["course_id"]
        course = self.s._read([cid])[cid]
        current, _ = self._direct(course)
        draft = data.get("draft")
        if not isinstance(draft, dict):
            raise ValueError("Send the course's modules, pages and assignments.")
        known = {e["id"] for e in current["pages"]} | {e["id"] for e in current["assignments"]} | {m["id"] for m in current["modules"]} \
            | {i["id"] for m in current["modules"] for i in m["items"]}
        # CANVAS: inspect only well-formed IDs here; normalize below gives malformed drafts their validation error.
        entries = [entry for key in ("pages", "assignments", "modules") if isinstance(draft.get(key, []), list)
                   for entry in draft.get(key, []) if isinstance(entry, dict)]
        modules = draft.get("modules")
        entries += [item for module in modules if isinstance(module, dict) and isinstance(module.get("items"), list)
                    for item in module["items"] if isinstance(item, dict)] if isinstance(modules, list) else []
        sent = {entry["id"] for entry in entries if isinstance(entry.get("id"), str)}
        if any(key and key not in known for key in sent):
            raise Conflict("Something you edited was deleted in Canvas since you opened this page. Open it again.")
        content = model.normalize({**draft, "rule": model.RULE, "source_course": cid, "started": ""}, current, direct=True,
                                  files={key: meta["name"] for key, meta in kept.items()})
        # What each existing item points to now in Canvas, so two things of one name never hold an item back.
        for module in content["modules"]:
            for item in module["items"]:
                found = course.item_by_id.get(model.direct_canvas_id(item["id"]) or "")
                if found and found[1].get("content_id") and item["type"] in (*model.NAMED, "File", "ExternalTool") and "assignment" not in item.get("content", {}):
                    item["content_id"] = found[1]["content_id"]
        links = {}
        for key, snap in base.items():
            kind, plan_key = key.split(":", 1)
            canvas_id = model.direct_canvas_id(plan_key)
            if canvas_id:
                links[(kind, plan_key)] = {"canvas_id": canvas_id, "source": "matched", "snapshot_json": json.dumps(snap), "created_at": ""}
        sections = [s["id"] for s in target["sections"]]
        choices = self.s._choices(data.get("choices"), [target])
        matches = {cid: model.match(content, course, links, sections)}
        plans = model.plan(content, {cid: course}, {cid: links}, {cid: sections}, matches, choices, kept, receipts)
        fingerprint = model.fingerprint({"direct": model.digest(content), "base": model.digest(base)}, plans, choices)
        return {"section": section, "target": target, "content": content, "base": base, "courses": {cid: course}, "links": {cid: links},
                "matches": matches, "plans": plans, "choices": choices, "fingerprint": fingerprint}

    def preview(self, section_id, data):
        """What pushing the edit would do in the section's Canvas course, and its fingerprint. Reads Canvas; writes nothing."""
        found = self._plan(section_id, data)
        target, plan = found["target"], found["plans"][found["target"]["course_id"]]
        course = {**target, **{k: plan[k] for k in ("counts", "attention", "blocked", "extra", "unclaimed")},
                  "new": [e for e in plan["entries"] if e["verdict"] == "new"],
                  "ops": [{k: v for k, v in op.items() if k in ("op", "label", "fields", "moves")} for op in plan["ops"]],
                  "remove": sorted(e["key"] for e in plan["extra"] if e["remove"]), "matched_by_name": []}
        return {"fingerprint": found["fingerprint"], "courses": [course], "unlinked": [], "choices": found["choices"], "only": None}

    def push(self, section_id, data, actor="workspace"):
        """Runs the approved plan: the same draft, base and choices as the preview, its `fingerprint`, `request_id` and
        `confirmation: "push"`, all checked here."""
        data = data if isinstance(data, dict) else {}
        request_id = _request_id(data.get("request_id"))
        if data.get("confirmation") != "push":
            raise ValueError("Confirm the push.")
        request_sha = model.digest({k: data.get(k) for k in ("fingerprint", "choices", "draft", "base")})
        with connect(self.path) as db:
            section = _section(db, section_id)
            old = db.execute("SELECT * FROM canvas_section_pushes WHERE section_id=? AND request_id=?", (section["section_id"], request_id)).fetchone()
        if old:
            if old["request_sha256"] != request_sha:
                raise Conflict("This request ID was used for a different push.")
            return self._push_view(old)
        stamp = self.s.stamp()  # before the plan reads Canvas
        found = self._plan(section_id, data)
        if found["fingerprint"] != data.get("fingerprint"):
            raise Conflict("Canvas changed since the preview. Look at the preview again.")
        cid = found["target"]["course_id"]
        push_id = str(uuid.uuid4())
        progress = {"courses": {cid: {"total": len(found["plans"][cid]["ops"]), "done": 0, "failed": 0, "errors": []}}, "current": "", "receipts": []}
        self.s.claim(push_id, [cid], stamp)
        try:
            with self.rosters.lock(), connect(self.path) as db:
                db.execute("BEGIN IMMEDIATE")
                if db.execute("SELECT 1 FROM canvas_section_pushes WHERE section_id=? AND request_id=?", (section["section_id"], request_id)).fetchone():
                    raise Conflict("This push was just started from another window.")
                db.execute("""INSERT INTO canvas_section_pushes(id,section_id,course_id,request_id,request_sha256,fingerprint,draft_json,plan_json,status,
                    progress_json,actor,created_at,finished_at) VALUES(?,?,?,?,?,?,?,?,'running',?,?,?,NULL)""",
                           (push_id, section["section_id"], cid, request_id, request_sha, found["fingerprint"],
                            json.dumps(found["content"], sort_keys=True, ensure_ascii=False), json.dumps(found["plans"][cid]["ops"], sort_keys=True),
                            json.dumps(progress), actor, timestamp()))
            thread = threading.Thread(target=self.s._run, args=(push_id, section["term_id"], found, progress, actor, SectionPush, self._progress),
                                      name=f"canvas-direct-{push_id[:8]}", daemon=True)
            thread.start()
        except BaseException:
            self.s.release(push_id)
            raise
        return self.status(section_id, push_id)

    def status(self, section_id, push_id):
        with connect(self.path) as db:
            row = db.execute("SELECT * FROM canvas_section_pushes WHERE id=? AND section_id=?", (push_id, str(section_id))).fetchone()
        if not row:
            raise NotFound("That push was not found.")
        return self._push_view(row)

    def cancel(self, section_id, push_id):
        self.status(section_id, push_id)
        self.s._cancel.add(push_id)
        return self.status(section_id, push_id)
