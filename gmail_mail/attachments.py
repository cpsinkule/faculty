"""Files attached to a message Faculty sends (requirement, 2026-10-01).

An attachment from Faculty is named by the route that serves the file (a GET under /api/: the routes the assistant's `fetch_files`
shows, and its own saved files), never by bytes from a model. The server fetches each one through the app's own route, in process, so
every guard of the ordinary request applies, and the preview names it with its type, size and SHA-256. The message's fingerprint covers
those, so a file that changes after the review makes the review stale. Nothing is copied or kept: the file stays with its owner, and
the send record keeps only the route, name, type, size and digest.

A file from the instructor's computer (requirement, 2026-10-01) comes with the request itself, as a part of a multipart preview or send; the page sends the same file to
both, and the fingerprint covers its digest. Only the Mail page sends those (the assistant's requests are JSON). It is never stored:
the send record keeps its name, type, size and digest.
"""
import hashlib
import mimetypes
import re
from pathlib import Path

from app_files import file_name

MAX_FILES = 10
# Bytes of all files together: Gmail's own 25 MB of attachments (requirement, 2026-11-12). Base64 makes them about 34 MB, inside the upload URI's 35 MB.
MAX_TOTAL = 25_000_000
# Gmail refuses these as attachments ("blocked for security reasons"); refused in the preview, before anything is recorded.
BLOCKED_EXTENSIONS = {".ade", ".adp", ".apk", ".appx", ".appxbundle", ".bat", ".cab", ".chm", ".cmd", ".com", ".cpl", ".diagcab",
                      ".diagcfg", ".diagpack", ".dll", ".dmg", ".ex", ".ex_", ".exe", ".hta", ".img", ".ins", ".iso", ".isp", ".jar",
                      ".jnlp", ".js", ".jse", ".lib", ".lnk", ".mde", ".msc", ".msi", ".msix", ".msixbundle", ".msp", ".mst", ".nsh",
                      ".pif", ".ps1", ".scr", ".sct", ".shb", ".sys", ".vb", ".vbe", ".vbs", ".vhd", ".vxd", ".wsc", ".wsf", ".wsh", ".xll"}
MEDIA = re.compile(r"[a-z]+/[a-z0-9.+-]{1,100}")


def uploaded(upload, wanted, max_total=None):
    """{name, type, size, sha256, from: "computer"} and the bytes of a file sent with the request: (filename, media type, bytes)."""
    filename, media, content = upload
    max_total = MAX_TOTAL if max_total is None else max_total
    if len(content) > max_total:
        raise ValueError(f"{str(filename)[:120]} is larger than {max_total / 1e6:.0f} MB.")
    if not content:
        raise ValueError(f"{str(filename)[:120]} is empty.")
    name = " ".join(str(wanted or filename or "").split()) or "file"
    media = mimetypes.guess_type(name)[0] or (media if isinstance(media, str) and MEDIA.fullmatch(media) else "") or "application/octet-stream"
    name = file_name(name, "", "", media)
    return {"name": name, "type": media, "size": len(content), "sha256": hashlib.sha256(content).hexdigest(), "from": "computer"}, content


def collect(files, value, uploads=(), *, max_files=None, max_total=None, blocked=None, service="Gmail"):
    """([{route, name, type, size, sha256}] | [{name, type, size, sha256, from}], [bytes]) for a message's `attachments`: each a route,
    {route, name}, or {upload: n, name} for the n-th file sent with the request. Each file is read now; the list is refused whole when
    one is wrong, so nothing goes with a file missing, and every file sent with the request must be listed once. The limits are
    Gmail's unless the caller names its own (canvas/messages.py: a Canvas message's files), read when called."""
    max_files = MAX_FILES if max_files is None else max_files
    max_total = MAX_TOTAL if max_total is None else max_total
    blocked = BLOCKED_EXTENSIONS if blocked is None else blocked
    uploads = list(uploads or ())
    if value in (None, [], ""):
        if uploads:
            raise ValueError("A file was sent that the message does not list.")
        return [], []
    if not isinstance(value, list):
        raise ValueError("Attachments must be a list of Faculty file routes.")
    if len(value) > max_files:
        raise ValueError(f"A message can carry at most {max_files} attachments.")
    listed, contents, seen, used = [], [], set(), set()
    for item in value:
        if isinstance(item, dict) and "upload" in item:
            index = item.get("upload")
            if type(index) is not int or not 0 <= index < len(uploads) or index in used:
                raise ValueError("An attachment names a file that was not sent with the message.")
            if item.get("name") is not None and not isinstance(item.get("name"), str):
                raise ValueError("An attachment's name must be text.")
            used.add(index)
            meta, content = uploaded(uploads[index], item.get("name"), max_total)
            listed.append(meta)
            contents.append(content)
            continue
        if files is None:
            raise ValueError("Attachments cannot be read here.")
        path, wanted = (item.get("route") or item.get("path"), item.get("name")) if isinstance(item, dict) else (item, "")
        if wanted is not None and not isinstance(wanted, str):
            raise ValueError("An attachment's name must be text.")
        route, meta, content = files.read(path, wanted)
        if route in seen:
            raise ValueError(f"{route[:120]} is attached twice.")
        seen.add(route)
        listed.append({"route": route, **meta})
        contents.append(content)
    if len(used) != len(uploads):
        raise ValueError("A file was sent that the message does not list.")
    for meta in listed:
        if Path(meta["name"]).suffix.lower() in blocked:
            raise ValueError(f"{service} does not send {Path(meta['name']).suffix.lower()} files ({meta['name']}); share it another way.")
    total = sum(meta["size"] for meta in listed)
    if total > max_total:
        raise ValueError(f"The attachments come to {total / 1e6:.1f} MB; {service} takes at most {max_total / 1e6:.0f} MB of files in one message.")
    return listed, contents
