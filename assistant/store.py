"""Optional workspace-owned conversations and explicit instructor preferences.

ASSISTANT-RETENTION: no transcript is retained until Save is chosen. Deletion
and expiry remove the active copy; existing catalog backups keep their ordinary
retention. Immutable action receipts are independent and are never deleted.
"""
import json
from datetime import datetime, timedelta, timezone

from werkzeug.exceptions import NotFound
from rosters import connect


def now():
    return datetime.now(timezone.utc)


def text(value, name, maximum, required=True):
    if not isinstance(value, str) or len(value.strip()) > maximum or (required and not value.strip()):
        raise ValueError(f"{name} must be {'1' if required else '0'} to {maximum} characters.")
    return value.strip()


class AssistantStore:
    def __init__(self, service):
        self.service = service

    def expire(self):
        with self.service.rosters.lock(), connect(self.service.database) as db:
            db.execute("DELETE FROM assistant_saved_conversations WHERE expires_at <= ?", (now().isoformat(),))

    def listing(self):
        self.expire()
        with connect(self.service.database) as db:
            rows = db.execute("""SELECT id,title,goal,state,retention_days,created_at,updated_at,expires_at
                FROM assistant_saved_conversations ORDER BY updated_at DESC,id""").fetchall()
        return {"conversations": [dict(r) for r in rows], "memories": self.memories(), "memory": self.service.memory.listing()}

    def load(self, ident):
        self.expire()
        with connect(self.service.database) as db:
            row = db.execute("SELECT * FROM assistant_saved_conversations WHERE id=?", (ident,)).fetchone()
        if not row:
            raise NotFound("That saved conversation was removed or expired.")
        return {**dict(row), "snapshot": json.loads(row["snapshot_json"])}

    def save(self, conversation, data=None):
        data = data or {}
        with conversation._lock:
            options = {**conversation.saved, **data}
            days = options.get("retention_days", 30)
            if type(days) is not int or days not in (7, 30, 90, 365):
                raise ValueError("Keep the conversation for 7, 30, 90 or 365 days after its last activity.")
            title = text(options.get("title") or "Saved conversation", "Title", 160)
            goal = text(options.get("goal", ""), "Task", 2000, False)
            state = options.get("state", "active")
            if state not in ("active", "paused", "completed"):
                raise ValueError("Choose active, paused or completed.")
            snapshot = conversation.snapshot()
            encoded = json.dumps(snapshot, ensure_ascii=False)
            if len(encoded) > 4_000_000:
                raise ValueError("This conversation is too large to save. Start a new saved task.")
            stamp = now()
            expires = (stamp + timedelta(days=days)).isoformat()
            with self.service.rosters.lock(), connect(self.service.database) as db:
                db.execute("""INSERT INTO assistant_saved_conversations VALUES(?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(id) DO UPDATE SET session_id=excluded.session_id,title=excluded.title,goal=excluded.goal,
                    state=excluded.state,snapshot_json=excluded.snapshot_json,retention_days=excluded.retention_days,
                    updated_at=excluded.updated_at,expires_at=excluded.expires_at""",
                    (conversation.id, conversation.session["id"], title, goal, state, encoded, days,
                     stamp.isoformat(), stamp.isoformat(), expires))
            conversation.saved = {"title": title, "goal": goal, "state": state, "retention_days": days, "expires_at": expires}
        return conversation.saved

    def delete(self, ident):
        with self.service.rosters.lock(), connect(self.service.database) as db:
            db.execute("DELETE FROM assistant_saved_conversations WHERE id=?", (ident,))

    # Preferences are the workspace's memories (assistant/memory.py, ASSISTANT MEMORY): these keep the names and shapes the
    # Saved tab, the `saved_preferences` tool has always used.
    def memories(self):
        return self.service.memory.preferences()

    def memory(self, data, ident=None, source='form'):
        return self.service.memory.write(data, ident, source)

    def forget_memory(self, ident, revision):
        return self.service.memory.remove(ident, revision)
