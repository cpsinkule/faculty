"""Paginated repository documentation, including the indexed teaching decisions.

DOCS-RETRIEVAL: returned positions describe the text actually returned, not the
requested range. A document fingerprint prevents silently skipping revised text.
"""
import hashlib
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PAGE_CHARS = 5400
# The assistant's primer is in its instructions, whole (conversations.SYSTEM). QUIET STEPS (2026-10-08): models read it again page
# by page, which cost time and filled the evidence check with text that proves nothing about the question, so it is not listed and
# a read of it answers with a note.
PRIMER = "assistant/guide.md"


def catalog():
    names = list(ROOT.glob("*.md")) + list(ROOT.glob("*/README.md"))
    names += list((ROOT / "docs/decisions").glob("*.md"))
    return {str(p.relative_to(ROOT)): p for p in sorted(set(names)) if p.is_file() and not p.is_symlink()
            and not any(part.startswith(".") for part in p.relative_to(ROOT).parts)}


def page(text, args, **metadata):
    digest = hashlib.sha256(text.encode()).hexdigest()
    if args.get("fingerprint") and args["fingerprint"] != digest:
        raise ValueError("This document changed. Read it again from the beginning (DOCS-RETRIEVAL).")
    offset = args.get("offset", 0)
    if type(offset) is not int or not 0 <= offset <= len(text):
        raise ValueError("offset must be a character position in this document.")
    end = min(len(text), offset + PAGE_CHARS)
    # Prefer complete lines, except for a line longer than a whole page.
    if end < len(text):
        boundary = text.rfind("\n", offset, end)
        if boundary > offset:
            end = boundary + 1
    return {**metadata, "text": text[offset:end], "offset": offset, "next_offset": end if end < len(text) else None,
            "total_characters": len(text), "fingerprint": digest}


def read(args):
    docs = catalog()
    name = args.get("name")
    if not name:
        names = sorted(docs)
        fingerprint = hashlib.sha256("\n".join(names).encode()).hexdigest()
        offset = args.get("offset", 0)
        if type(offset) is not int or not 0 <= offset <= len(names):
            raise ValueError("Choose a valid document-list offset.")
        if args.get("fingerprint") and args["fingerprint"] != fingerprint:
            raise ValueError("The document list changed. Read it again.")
        end = offset + 50
        return {"documents": names[offset:end], "fingerprint": fingerprint, "offset": offset,
                "next_offset": end if end < len(names) else None,
                "note": "Read name and optional section; follow next_offset until null."}
    if name == PRIMER:
        return {"name": name, "note": "This is your primer. It is already in your instructions, whole: use that copy."}
    if name not in docs:
        raise ValueError("Choose a document from read_docs.")
    text = docs[name].read_text(encoding="utf-8")
    section = args.get("section")
    if section:
        match = re.search(r"^(#+)\s*.*" + re.escape(section) + r".*$", text, re.M | re.I)
        if not match:
            # Existing clients can still ask AGENTS.md for an old section heading.
            if name == "AGENTS.md":
                matches = [n for n, p in docs.items() if n.startswith("docs/decisions/") and
                           section.casefold() in p.read_text(encoding="utf-8").split("\n", 1)[0].casefold()]
                if len(matches) == 1:
                    return read({**args, "name": matches[0], "section": None})
            return {"error": f"No heading containing {section!r}.", "headings": re.findall(r"^#+\s*(.+)$", text, re.M)}
        end = re.compile(r"^#{1," + str(len(match.group(1))) + r"}\s", re.M).search(text, match.end())
        text = text[match.start():end.start() if end else len(text)]
    return page(text, args, name=name, section=section)


def source(path, args):
    raw = path.read_text(encoding="utf-8", errors="replace")
    fingerprint = hashlib.sha256(raw.encode()).hexdigest()
    if args.get("fingerprint") and args["fingerprint"] != fingerprint:
        raise ValueError("This source file changed. Read it again from the beginning (DOCS-RETRIEVAL).")
    lines = raw.splitlines()
    start = max(1, int(args.get("start") or 1))
    count = min(400, max(1, int(args.get("lines") or 200)))
    text = "\n".join(f"{start + i}: {line}" for i, line in enumerate(lines[start - 1:start - 1 + count]))
    result = page(text, {**args, "fingerprint": None}, path=args["path"], start=start, total_lines=len(lines))
    result["fingerprint"] = fingerprint
    offset, end = result["offset"], result["offset"] + len(result["text"])
    result["first_line"] = start + text[:offset].count("\n")
    result["end"] = start + text[:end].count("\n") - int(bool(end) and text[end - 1] == "\n") if end else start - 1
    result["last_line_complete"] = end == len(text) or text[end - 1:end] == "\n"
    result["next_start"] = start + count if result["next_offset"] is None and start + count <= len(lines) else None
    return result
