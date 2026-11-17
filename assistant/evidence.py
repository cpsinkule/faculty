"""Evidence of actual reads. Metadata is captured, never supplied by a model.

ASSISTANT-EVIDENCE: a digest identifies the observed response, not a promise
that it is still current. File labels and scan/crop routes remain the existing
fetch_files contract. No teaching API response is changed by this module.
"""
import re
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone

from canonical_json import ascii_digest

READS = ContextVar("assistant_evidence", default=None)
READ_TEXT = ContextVar("assistant_evidence_text", default=False)


@contextmanager
def capture(include_text=False):
    sources = []
    token = READS.set(sources)
    text_token = READ_TEXT.set(include_text)
    try:
        yield sources
    finally:
        READ_TEXT.reset(text_token)
        READS.reset(token)


def digest(value):
    return ascii_digest(value, default=str)


# STUDENT-NOTES "SEEN BY THE ASSISTANT": the instructor's private notes reach the preparation and chat, but "Jev does not read
# them". Every text given to a Jev/Decisions check passes through `without_notes` (2026-10-09 review: they reached the To do
# obligation, draft, evidence and learning checks). NOTES_HEAD opens the notes block of FOLLOW-UP FACTS (`_note_facts`).
NOTES_HEAD = "Your private notes about this student"
WITHHELD = "[private student notes are not given to this check]"
_NOTES_BLOCK = re.compile(r"(?m)^" + re.escape(NOTES_HEAD) + r"[^\n]*(?:\n(?:- |\()[^\n]*)*\n?")


def _note_list(value):
    if isinstance(value, dict):
        value = value.get("notes")
    return isinstance(value, list) and bool(value) and all(isinstance(n, dict) and "text" in n and "date" in n for n in value)


def without_notes(value, path=""):
    """A copy of `value` for a Jev/Decisions check, without private student notes: a notes route's data, a `notes` list of
    notes (or the notes reader's payload), the FOLLOW-UP FACTS notes block in any text, and a proposed note's body."""
    if str(path or "").startswith("/api/student-notes"):
        return WITHHELD
    if isinstance(value, str):
        return _NOTES_BLOCK.sub("", value) if NOTES_HEAD in value else value
    if isinstance(value, list):
        return [without_notes(v) for v in value]
    if isinstance(value, dict):
        if str(value.get("path") or "").startswith("/api/student-notes"):
            return {k: v for k, v in value.items() if k not in ("body", "data", "query", "text")} | {"body": WITHHELD}
        return {k: WITHHELD if k == "notes" and _note_list(v) else without_notes(v) for k, v in value.items()}
    return value


def record(path, value):
    sources = READS.get()
    if sources is None or any(s["path"] == path for s in sources) or len(sources) >= 20:
        return
    from .transport import clean
    value = clean(value)
    versions = {}
    if isinstance(value, dict):
        for key in ("revision", "version", "sha256", "updated_at", "created_at", "certification_id", "current_result_id"):
            if isinstance(value.get(key), (str, int)):
                versions[key] = value[key]
        for section in ("setup", "certification", "certified"):
            item = value.get(section)
            if isinstance(item, dict):
                versions[section] = {k: item[k] for k in ("id", "revision", "state", "status", "created_at") if k in item}
    file = isinstance(value, dict) and set(value) == {"media_type", "size", "sha256"}
    sources.append({"id": uuid.uuid4().hex, "path": path, "observed_at": datetime.now(timezone.utc).isoformat(),
                    "fingerprint": value["sha256"] if file else digest(value), "fingerprint_of": "file bytes" if file else "canonical JSON response", "versions": versions,
                    "note": "Observed at this time. Follow the source to inspect current data; a certification is historical."})
    # JEV: private ephemeral input, removed before provenance is returned or saved.
    if READ_TEXT.get():
        from .transport import render
        rendered = render(without_notes(value, path))
        sources[-1]["_jev_text"] = rendered[:8000]
        sources[-1]["_jev_truncated"] = len(rendered) > 8000
