"""How the assistant reaches the app: through its own routes, in-process (`app.test_client()`), so every guard, lock, check and
privacy omission of the ordinary request pipeline applies, and it reads exactly the JSON the pages read. Reads return JSON; files
come back only through `fetch` (the `fetch_files` tool, assistant/files.py). Credentials are removed from every answer."""
from __future__ import annotations

import json
import io
from urllib.parse import urlencode, urlsplit, parse_qsl

from werkzeug.exceptions import MethodNotAllowed, NotFound
from werkzeug.routing import RequestRedirect

from request_actor import ASSISTANT, ENVIRON_KEY

from . import policy

HEADERS = {"X-Teaching-Request": "1"}
# Marks every request as the assistant's (request_actor.py): records that name an actor say the assistant made them.
ENVIRON = {ENVIRON_KEY: ASSISTANT}
LIST_ITEMS = 60       # a longer list is cut to this many items with a count of the rest
STRING_CHARS = 6000   # a longer string is cut with a note
RESULT_CHARS = 120000  # an answer is cut to this many characters of JSON


class Transport:
    def __init__(self, app):
        self.app = app

    def match(self, method, path):
        """(endpoint, view_args) of a path, or raises ValueError with an instructor-readable reason."""
        parts = urlsplit(path)
        if parts.scheme or parts.netloc or not parts.path.startswith("/api/"):
            raise ValueError("Name an /api/ path of this workspace.")
        adapter = self.app.url_map.bind("localhost")
        try:
            endpoint, args = adapter.match(parts.path, method=method)
        except NotFound:
            raise ValueError(f"No route {method} {parts.path}. Use list_actions to find one.") from None
        except MethodNotAllowed:
            raise ValueError(f"{parts.path} does not take {method}.") from None
        except RequestRedirect as redirect:
            raise ValueError(f"Use {urlsplit(redirect.new_url).path}.") from None
        return endpoint, args

    def build(self, endpoint, view_args, query=None):
        adapter = self.app.url_map.bind("localhost")
        path = adapter.build(endpoint, view_args)
        return path + ("?" + urlencode(query) if query else "")

    def request(self, method, path, body=None):
        """(status, json or None, note). Never raises for an HTTP error: the status and the route's own message say it."""
        client = self.app.test_client()
        headers = dict(HEADERS) if method != "GET" else {}
        response = client.open(path, method=method, headers=headers, json=body if method != "GET" else None, environ_base=ENVIRON)
        try:
            kind = response.mimetype
            if kind != "application/json":
                size = response.content_length or len(response.get_data())
                return response.status_code, None, f"This route answers with a file ({kind}, {size} bytes), not data."
            data = response.get_json(silent=True)
            if method == "GET" and response.status_code == 200:
                from .evidence import record
                record(path, data)
            return response.status_code, data, ""
        finally:
            response.close()

    def material_upload(self, path, fields, files):
        """ASSISTANT: server-validated material bridge only; never a general model upload tool."""
        endpoint, _ = self.match('POST', path)
        if endpoint not in ('documents.compile_version', 'quiz_drafts.create', 'course_notes.upload'):
            raise ValueError('ASSISTANT: that route is not a teaching-material destination.')
        data = dict(fields)
        for key, entries in files.items():
            data[key] = [(io.BytesIO(content), name) for name, content in entries]
        response = self.app.test_client().post(path, data=data, content_type='multipart/form-data', headers=HEADERS, environ_base=ENVIRON)
        try:
            return response.status_code, response.get_json(silent=True) or {'error': 'The material destination returned no JSON receipt.'}
        finally:
            response.close()

    def get(self, path, query=None):
        parts = urlsplit(path)
        if parts.scheme or parts.netloc:
            raise ValueError("Name an /api/ path of this workspace.")
        endpoint, args = self.match("GET", parts.path)
        if endpoint in policy.GET_BLOCKED or (endpoint.startswith("assistant.") and endpoint not in policy.GET_OWN):
            raise ValueError("The assistant does not read this route (sign-in, access links, photos, exports or instructor-only reviews).")
        values = dict(parse_qsl(parts.query))
        values.update({k: v for k, v in (query or {}).items() if v is not None and v != ""})
        for name in policy.GET_DROPPED_ARGS.get(endpoint, ()):
            values.pop(name, None)
        full = parts.path + ("?" + urlencode(values) if values else "")
        return self.request("GET", full)

    def fetch(self, path, query=None):
        """A file a GET route serves, for `fetch_files`: (endpoint, the path as fetched, status, media type, bytes, disposition).
        Sign-in windows, callbacks, access links and the workspace export are never fetched (policy.FILE_BLOCKED)."""
        parts = urlsplit(str(path or ""))
        if parts.scheme or parts.netloc:
            raise ValueError("Name an /api/ path of this workspace.")
        endpoint, _ = self.match("GET", parts.path)
        if endpoint in policy.FILE_BLOCKED or (endpoint.startswith("assistant.") and endpoint not in policy.FILE_OWN):
            raise ValueError("The assistant does not fetch this route (sign-in, access links or the workspace export).")
        values = dict(parse_qsl(parts.query))
        values.update({k: str(v) for k, v in (query or {}).items() if v is not None and v != ""})
        for name in policy.GET_DROPPED_ARGS.get(endpoint, ()):
            values.pop(name, None)
        full = parts.path + ("?" + urlencode(values) if values else "")
        response = self.app.test_client().get(full, environ_base=ENVIRON)
        try:
            content = response.get_data()
            if response.status_code == 200:
                import hashlib
                from .evidence import record
                record(full, {"media_type": response.mimetype, "size": len(content), "sha256": hashlib.sha256(content).hexdigest()})
            return endpoint, full, response.status_code, response.mimetype, content, response.headers.get("Content-Disposition", "")
        finally:
            response.close()


def clean(value):
    """The answer without credentials."""
    if isinstance(value, dict):
        return {k: ("[removed]" if policy.SECRET_KEY.search(str(k)) and v not in (None, "", [], {}) else clean(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [clean(v) for v in value]
    return value


def narrow(value, needle):
    """Only the items of each list (and of each large mapping) that mention `needle`, where some do; everything else as it was. A
    gradebook of 150 students read with find="Frost" keeps its columns and that one student's entries."""
    needle = str(needle or "").casefold().strip()
    if not needle:
        return value
    mentions = lambda item: needle in json.dumps(item, ensure_ascii=False, default=str).casefold()
    if isinstance(value, list):
        hits = [item for item in value if mentions(item)]
        return [narrow(item, needle) for item in (hits or value)]
    if isinstance(value, dict):
        if len(value) > LIST_ITEMS:
            hits = {key: item for key, item in value.items() if needle in str(key).casefold() or mentions(item)}
            value = hits or value
        return {key: narrow(item, needle) for key, item in value.items()}
    return value


def shrink(value, *, items=LIST_ITEMS, chars=STRING_CHARS, omissions=None, path="", mappings=None):
    """A model-sized answer: long lists and strings cut with a note of what was left out."""
    if isinstance(value, dict):
        entries = list(value.items())
        if mappings is not None and len(entries) > mappings:
            if omissions is not None:
                omissions.append({"pointer": path, "offset": mappings, "total": len(entries), "kind": "object"})
            entries = entries[:mappings]
        return {k: shrink(v, items=items, chars=chars, omissions=omissions,
                          path=path + "/" + str(k).replace("~", "~0").replace("/", "~1"), mappings=mappings) for k, v in entries}
    if isinstance(value, list):
        kept = [shrink(v, items=items, chars=chars, omissions=omissions, path=path + f"/{i}", mappings=mappings)
                for i, v in enumerate(value[:items])]
        if len(value) > items:
            if omissions is not None:
                omissions.append({"pointer": path, "offset": items, "total": len(value), "kind": "array"})
            kept.append(f"[{len(value) - items} more items not shown: read again with find=<a name, title or ID> to keep only the "
                        "items that mention it (for one student: find_student, then student_grades)]")
        return kept
    if isinstance(value, str) and len(value) > chars:
        if omissions is not None:
            omissions.append({"pointer": path, "offset": chars, "total": len(value), "kind": "string"})
        return value[:chars] + f"[... {len(value) - chars} more characters]"
    return value


def render(value, source=None, composed=False):
    """ASSISTANT: bounded, valid JSON, with explicit pointers for omitted data. `source`: the GET path a tool read, which faculty_get
    can page; `composed`: a tool's own composed answer (student_paper, catalog_sql, find_student), which has no GET route to page."""
    value = clean(value)
    if composed:
        note = ("Partial result. This tool composes its answer, so faculty_get cannot page it: ask this tool for less (a narrower "
                "query, find, one student, question or line range).")
        whole = "This result is too large. Ask this tool for less (a narrower query, find, one student, question or line range)."
    elif source:
        note = (f"Partial result. Use faculty_get with path {source}, pointer, offset and limit; keep the returned fingerprint for "
                "continuation.")
        whole = f"This result is too large. Read a specific field using faculty_get with path {source}, pointer, offset and limit."
    else:
        note = "Partial result. Use faculty_get with the source path, pointer, offset and limit; keep the returned fingerprint for continuation."
        whole = "This result is too large. Read a specific field using faculty_get with pointer, offset and limit."
    for scale in (1, 2, 4, 8, 16, 32, 64):
        omissions = []
        result = shrink(value, items=max(1, LIST_ITEMS // scale), chars=max(80, STRING_CHARS // scale),
                        omissions=omissions, mappings=None if scale == 1 else max(4, 120 // scale))
        if omissions:
            info = {"fields": omissions[:30], "more_fields": max(0, len(omissions) - 30),
                    "note": note}
            result = {**result, "_truncated": info} if isinstance(result, dict) else {"data": result, "_truncated": info}
        text = json.dumps(result, ensure_ascii=False, separators=(",", ":"), default=str)
        if len(text) <= RESULT_CHARS:
            return text
    return json.dumps({"_truncated": {"fields": [{"pointer": "", "offset": 0}], "note": whole}})
