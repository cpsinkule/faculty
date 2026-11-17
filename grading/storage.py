"""Derived crops retain the existing verified source Inbox owner."""
import uuid

from course_storage import timestamp
from document_identity.storage import publish
from rosters import connect
from teach_inbox.service import PRESENT

from .models import digest


def retain(inbox, message_id, task_id, region_index, prepared):
    with inbox.rosters.lock():
        with connect(inbox.path) as db:
            message = db.execute(f"SELECT * FROM inbox_messages WHERE id=? AND {PRESENT.format('inbox_messages.id')}", (message_id,)).fetchone()
            job = db.execute("SELECT id FROM inbox_jobs WHERE message_id=? ORDER BY created_at LIMIT 1", (message_id,)).fetchone()
            if not message or not job:
                raise ValueError("The source scan is no longer present.")
            message = dict(message)
        artifacts = {}
        with inbox.files(message, write=True) as root:
            for kind, content in prepared["images"].items():
                aid = str(uuid.uuid5(uuid.UUID(task_id), f"crop:{region_index}:{kind}"))
                relative = f"grading/{task_id}/{region_index}-{kind}.png"
                checksum = digest(content)
                with connect(inbox.path) as db:
                    existing = db.execute("SELECT * FROM inbox_artifacts WHERE id=?", (aid,)).fetchone()
                if existing:
                    if existing["sha256"] != checksum:
                        raise ValueError("A retained response crop differs from the retry output.")
                else:
                    publish(root / relative, content)
                    with connect(inbox.path) as db:
                        db.execute("INSERT INTO inbox_artifacts VALUES(?,?,?,?,?,?,?,?,?)", (aid, message_id, job["id"],
                            f"response-{region_index}-{kind}.png", relative, "output", len(content), checksum, timestamp()))
                artifacts[kind] = {"artifact_id": aid, "sha256": checksum, "url": f"/api/inbox/artifacts/{aid}/download"}
        return {"images": artifacts, "metadata": prepared["metadata"]}


def read(inbox, reference):
    _, content = inbox.read_artifact(reference["artifact_id"])
    if digest(content) != reference["sha256"]:
        raise ValueError("The retained response image failed verification.")
    return content
