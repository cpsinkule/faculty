"""Read-only, bounded watches in the existing fair worker rotation (ASSISTANT-WATCHES).

Observations use the same projections as the owning pages. Notifications are
local to Faculty, with no mail or model calls and no action on the watched work.
"""
import json
import re
import time
import uuid

from werkzeug.exceptions import Conflict, NotFound
from rosters import connect
from .evidence import digest
from .store import now, text

INTERVAL = 120


class Watches:
    def __init__(self, service):
        self.service = service
        self._next_cleanup = 0

    def read(self, path):
        status, data, note = self.service.transport.get(path)
        if status != 200 or not isinstance(data, dict):
            raise ValueError((data or {}).get("error", note or f"Source returned {status}.") if isinstance(data, dict) else f"Source returned {status}.")
        return data

    def observe(self, kind, target):
        if kind == "scan":
            job = self.read(f"/api/inbox/jobs/{target}")
            if job.get("deleted"):
                raise ValueError("This Inbox item was removed. Pause or remove its watch.")
            overview = {}
            if job.get("message_id"):
                overview = self.read(f"/api/documents/messages/{job['message_id']}?view=summary").get("overview", {})
            state = overview.get("state") or job.get("status")
            finished = state == "done" or (not overview and state == "completed")
            attention = state in ("todo", "check", "failed", "needs_review")
            return {"state": state, "finished": finished, "ready": finished, "needs_attention": attention,
                    "stages": [{"key": s.get("key"), "state": s.get("state")} for s in overview.get("stages", [])],
                    "page": f"#inbox/job/{target}", "message": "Scan finished." if finished else "Scan needs your attention." if attention else "Scan is processing."}
        if kind == "grading":
            data = self.read(f"/api/grading/assignments/{target}/certification")
            certification = data.get("certification") or {}
            state = certification.get("state") or certification.get("status")
            blocking = data.get("blocking")
            ready = blocking == 0 and not data.get("live") and state != "current"
            return {"state": state, "blocking": blocking, "counts": data.get("counts"), "live": bool(data.get("live")),
                    "finished": state == "current", "ready": ready, "needs_attention": bool(blocking) or ready,
                    "page": f"#grading/{target}?tab=export",
                    "message": "Grading is ready for your certification review." if ready else "Grades are certified." if state == "current" else "Grading readiness changed."}
        data = self.read(f"/api/exams/{target}/overview")
        readiness = data.get("readiness", [])
        stages = [{"key": s.get("key"), "state": s.get("state")} for s in data.get("stages", [])]
        ready = bool(readiness) and all(item.get("ok") for item in readiness)
        return {"stages": stages, "readiness": [{"key": r.get("key"), "ok": bool(r.get("ok"))} for r in readiness],
                "finished": any(s["key"] == "recorded" and s["state"] == "done" for s in stages), "ready": ready,
                "needs_attention": bool(readiness) and not ready, "page": f"#exams/{target}",
                "message": "The exam readiness checklist is complete." if ready else "Exam readiness changed."}

    def preview(self, data):
        kind, condition = data.get("kind"), data.get("condition", "changed")
        if kind not in ("scan", "grading", "exam") or condition not in ("changed", "finished", "needs_attention", "ready"):
            raise ValueError("Choose scan, grading or exam, and changed, finished, needs_attention or ready.")
        target = data.get("target")
        if not isinstance(target, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", target):
            raise ValueError("Choose the exact Inbox job, grading plan or exam ID.")
        canonical = {"title": text(data.get("title"), "Watch title", 160), "kind": kind, "target": target, "condition": condition}
        if data.get('prepare_review') is not None and type(data['prepare_review']) is not bool:
            raise ValueError('Choose whether a matching change prepares a review.')
        if data.get('prepare_review'):
            canonical['prepare_review'] = True
        observation = self.observe(kind, target)
        return {**canonical, "fingerprint": digest(canonical), "observation": observation,
                "note": "Checks every two minutes while the worker runs. Notifies in Faculty on matching changes or source errors; unchanged checks are quiet."}

    def create(self, data):
        preview = self.preview(data)
        if data.get("fingerprint") != preview["fingerprint"]:
            raise Conflict("Review the watch configuration again.")
        request_id = text(data.get("request_id"), "Request ID", 120)
        with self.service.rosters.lock(), connect(self.service.database) as db:
            old = db.execute("SELECT * FROM assistant_watches WHERE request_id=?", (request_id,)).fetchone()
            if old:
                if old["request_sha256"] != preview["fingerprint"]:
                    raise Conflict("This request ID already created a different watch.")
                return self.view(old)
            if db.execute("SELECT COUNT(*) FROM assistant_watches").fetchone()[0] >= 50:
                raise ValueError("Keep at most 50 watches. Remove an old one first.")
            ident, stamp = uuid.uuid4().hex, now().isoformat()
            db.execute("""INSERT INTO assistant_watches(id,request_id,request_sha256,title,kind,target,condition,next_check,last_json,checked_at,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?)""", (ident, request_id, preview["fingerprint"], preview["title"], preview["kind"], preview["target"],
                preview["condition"], time.time() + INTERVAL, json.dumps(preview["observation"]), stamp, stamp))
            if preview.get('prepare_review'):
                db.execute('UPDATE assistant_watches SET prepare_review=1 WHERE id=?', (ident,))
            row = db.execute("SELECT * FROM assistant_watches WHERE id=?", (ident,)).fetchone()
        return self.view(row)

    @staticmethod
    def view(row):
        return {k: row[k] for k in ("id", "title", "kind", "target", "condition", "enabled", "revision", "last_error", "checked_at", "created_at", "prepare_review")} | {
            "observation": json.loads(row["last_json"]) if row["last_json"] else None}

    def listing(self):
        with connect(self.service.database) as db:
            watches = [self.view(r) for r in db.execute("SELECT * FROM assistant_watches ORDER BY created_at DESC,id")]
            notifications = [dict(r) for r in db.execute("""SELECT * FROM assistant_notifications WHERE read_at IS NULL
                OR id IN (SELECT id FROM assistant_notifications WHERE read_at IS NOT NULL ORDER BY id DESC LIMIT 200) ORDER BY id DESC""")]
        return {"watches": watches, "notifications": notifications}

    def update(self, ident, data, delete=False):
        with self.service.rosters.lock(), connect(self.service.database) as db:
            row = db.execute("SELECT * FROM assistant_watches WHERE id=?", (ident,)).fetchone()
            if not row:
                raise NotFound("This watch was removed.")
            if row["revision"] != data.get("revision"):
                raise Conflict("This watch changed. Read it again.")
            if delete:
                db.execute("DELETE FROM assistant_watches WHERE id=?", (ident,))
            else:
                if type(data.get("enabled")) is not bool:
                    raise ValueError("Choose whether this watch is enabled.")
                db.execute("UPDATE assistant_watches SET enabled=?,revision=revision+1,next_check=0 WHERE id=?", (int(data["enabled"]), ident))
        if delete or data.get('enabled') is False:
            self.service.workflows.revoke_watch(ident)
        return {"removed": delete, "updated": not delete}

    def mark_read(self, identifiers):
        if not isinstance(identifiers, list) or len(identifiers) > 200 or any(type(i) is not int for i in identifiers):
            raise ValueError("Name up to 200 notifications.")
        with self.service.rosters.lock(), connect(self.service.database) as db:
            db.executemany("UPDATE assistant_notifications SET read_at=? WHERE id=? AND read_at IS NULL", [(now().isoformat(), i) for i in identifiers])
        return {"read": True}

    def process_one(self):
        if time.monotonic() >= self._next_cleanup:
            self.service.store.expire()
            self._next_cleanup = time.monotonic() + 600
        # Claim one due read. A second worker cannot claim it within this bounded turn.
        with self.service.rosters.lock(), connect(self.service.database) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM assistant_watches WHERE enabled=1 AND next_check<=? ORDER BY next_check,id LIMIT 1", (time.time(),)).fetchone()
            if not row:
                return False
            claimed_until = time.time() + INTERVAL
            db.execute("UPDATE assistant_watches SET next_check=? WHERE id=?", (claimed_until, row["id"]))
        observation, error = None, ""
        try:
            observation = self.observe(row["kind"], row["target"])
        except Exception as problem:
            error = str(problem)[:1000] or type(problem).__name__
        previous = json.loads(row["last_json"]) if row["last_json"] else None
        recovered = bool(row["last_error"]) and not error
        changed = previous != observation and previous is not None
        matched = changed and (row["condition"] == "changed" or bool(observation and observation.get(row["condition"])) and not bool(previous.get(row["condition"])))
        notify = (bool(error) and error != row["last_error"]) or recovered or (not error and matched)
        stamp = now().isoformat()
        with self.service.rosters.lock(), connect(self.service.database) as db:
            db.execute("BEGIN IMMEDIATE")
            current = db.execute("SELECT enabled,revision,next_check FROM assistant_watches WHERE id=?", (row["id"],)).fetchone()
            if not current or not current["enabled"] or current["revision"] != row["revision"] or current["next_check"] != claimed_until:
                return True
            db.execute("UPDATE assistant_watches SET last_json=?,last_error=?,checked_at=? WHERE id=?",
                (json.dumps(observation or previous), error, stamp, row["id"]))
            if notify:
                message = "Could not check: " + error if error else ("The source can be read again. " if recovered else "") + observation["message"]
                page = (observation or previous or {}).get("page", "#overview")
                db.execute("INSERT INTO assistant_notifications(watch_id,title,message,page,created_at) VALUES(?,?,?,?,?)", (row["id"], row["title"], message, page, stamp))
                # Read notifications beyond the latest 200 may be discarded; unread ones are retained.
                db.execute("DELETE FROM assistant_notifications WHERE read_at IS NOT NULL AND id < COALESCE((SELECT id FROM assistant_notifications ORDER BY id DESC LIMIT 1 OFFSET 199),0)")
        if not error and matched and row['prepare_review']:
            self.service.workflows.from_watch(row['id'], row['revision'])
        return True
