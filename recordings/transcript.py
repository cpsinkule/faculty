"""What a kept recording's files say, as Faculty reads them (docs/decisions/recordings.md, RECORDINGS; step 2). Pure.

A TRANSCRIPT is Plaud's `transaction` block as kept: a JSON list of segments, each `{start_time, end_time, speaker,
original_speaker, content, segment_uid}` with times in milliseconds from the start of the recording (seen on the two real
recordings kept on 2026-11-12, structure only; Plaud's own client reads the same keys). `speaker` is the name Plaud shows now (a
voice named in its app reads "Chris"); `original_speaker` is its first label for that voice ("Speaker 2"), which does not change
when the voice is named, so a name the instructor gives in Faculty is kept by it. An OUTLINE is a list of
`{start_time, end_time, topic}`. Plaud's NOTES are kept in Faculty's own shape, `[{type, tab, content}]` (recordings/service.py).

Anything not in these shapes is read as nothing and said to be unreadable; nothing here guesses.
"""
import json
import re

MAX_LINES = 20000      # the most lines one transcript is read into (a three-hour class is about 2,000)
MAX_TEXT = 20000       # the most characters of one line
MAX_WORDS = 8          # the most words one search looks for
WORD = re.compile(r"[^\s]+")


def _loaded(content):
    try:
        return json.loads(content.decode("utf-8") if isinstance(content, (bytes, bytearray)) else content)
    except (ValueError, UnicodeDecodeError):
        return None


def _ms(value):
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0 else None


def _text(value, limit):
    return " ".join(str(value).split())[:limit] if isinstance(value, str) else ""


def lines(content):
    """(readable, [{position, start_ms, end_ms, speaker, speaker_key, text}]) of a kept transcript. A segment with no words or no
    start is left out; a transcript that is not a list of segments is not readable."""
    found = _loaded(content)
    if not isinstance(found, list) or any(not isinstance(item, dict) for item in found):
        return False, []
    read = []
    for item in found[:MAX_LINES]:
        start, text = _ms(item.get("start_time")), " ".join(str(item.get("content") or "").split())[:MAX_TEXT] if isinstance(item.get("content"), str) else ""
        if start is None or not text:
            continue
        end = _ms(item.get("end_time"))
        speaker = _text(item.get("speaker"), 200)
        read.append({"position": len(read), "start_ms": start, "end_ms": end if end is not None and end >= start else start,
                     "speaker": speaker, "speaker_key": _text(item.get("original_speaker"), 200) or speaker, "text": text})
    return bool(read) or not found, read


def outline(content):
    """[{start_ms, topic}] of a kept outline (Plaud's words), or [] when it is not in that shape."""
    found = _loaded(content)
    if not isinstance(found, list):
        return []
    return [{"start_ms": _ms(item.get("start_time")), "topic": _text(item.get("topic"), 300)} for item in found[:500]
            if isinstance(item, dict) and _ms(item.get("start_time")) is not None and _text(item.get("topic"), 300)]


def notes(content):
    """[{tab, content}] of Plaud's kept notes (its summary is the tab "Summary"): Plaud's words, never the source of anything."""
    found = _loaded(content)
    if not isinstance(found, list):
        return []
    return [{"tab": _text(item.get("tab"), 200) or "Notes", "content": str(item.get("content"))[:200000]} for item in found[:20]
            if isinstance(item, dict) and isinstance(item.get("content"), str) and item["content"].strip()]


def words(query):
    """The words a search looks for: what was typed, split at spaces, punctuation at a word's ends dropped, each once, at most
    MAX_WORDS. A line is found when it holds every one of them, whatever their case."""
    found = []
    for word in WORD.findall(str(query or "")):
        word = word.strip(".,;:!?\"'()[]{}“”‘’").casefold()
        if word and word not in found:
            found.append(word[:100])
    return found[:MAX_WORDS]


def like(word):
    """A LIKE pattern that finds `word` anywhere, its own % and _ meaning themselves (ESCAPE '\\')."""
    return "%" + word.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def offset(ms):
    """"12:34" or "1:02:34": how far into the recording a moment is."""
    seconds = max(0, int(ms)) // 1000
    hours, minutes, seconds = seconds // 3600, seconds % 3600 // 60, seconds % 60
    return f"{hours}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes}:{seconds:02d}"
