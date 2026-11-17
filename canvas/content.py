"""Canvas modules and pages kept in step across a term's courses (requirement, 2026-09-28; canvas/README.md "Modules and pages").

Pure: no database, no Canvas. `canvas/content_service.py` reads Canvas, keeps the records and runs pushes; this module decides.

FACULTY'S COPY (`content`, a term's latest `canvas_content_revisions` row) is course-neutral: modules with their items, and pages.
IDs are Faculty's (`mod_…`, `itm_…`, `pg_…`, from `new_id`), never Canvas's. An item of type Page names one of the copy's pages; an
item that points at other Canvas content (an assignment, quiz, discussion, file or external tool) names it by what is the SAME in
every course made from one template: an assignment's, quiz's or discussion's name, a file's folder and name, a tool's ID. A page's
body is kept as written: links to any of the term's Canvas courses are rewritten for each course when it is pushed (`render`).

MATCHING (`match`) finds, per Canvas course, which Canvas module, item and page is which of Faculty's: first what Faculty recorded
(`links`: the latest row per object, an ID that still exists), then by name (folded; only when exactly one on each side), and for an
item first within its module and then anywhere in the course (a moved item). What is left on the Canvas side is "only in Canvas".

A PLAN (`plan`) says per course what a push would do and why. Each object compares Faculty's copy with Canvas using the record of
the last write (`snapshot`: a digest of what Faculty sent, in Faculty's terms, and of what Canvas held after it): Faculty's copy
changed and Canvas did not → update; Canvas changed (someone edited there) → listed, replaced only when the instructor ticks it; no
record yet and they differ → the same. Nothing only in Canvas is deleted unless ticked; an object Faculty once pushed and has since
removed from its copy is ticked by default, except pages, which are never deleted unless the instructor ticks them. Order is
Faculty's: modules it manages come first in its order, the rest keep their order after them.

FILES (requirement, 2026-10-10; canvas/files.py). A File item may name a file Faculty keeps (`content.file`, beside the folder and
name it has in every course): the plan then puts that file in each course's Files where it is missing (`upload_file`) before the
item is written, and replaces the one Canvas holds only when it is the file Faculty put there earlier, or when ticked.
"""
from __future__ import annotations

import json
import re
import secrets
from datetime import datetime, timezone

from canonical_json import digest

RULE = "canvas-content-v1"
ITEM_TYPES = ("SubHeader", "Page", "ExternalUrl", "Assignment", "Quiz", "Discussion", "File", "ExternalTool")
NAMED = ("Assignment", "Quiz", "Discussion")    # found in each course by name
MAX_MODULES, MAX_ITEMS, MAX_PAGES = 200, 1000, 500
TITLE, BODY, URL = 255, 1_000_000, 2000
MAX_INDENT = 5
KINDS = ("module", "item", "page", "assignment")
MAX_ASSIGNMENTS = 500
GRADING_TYPES = ("points", "percent", "pass_fail", "letter_grade", "gpa_scale", "not_graded")
# Submission kinds Faculty sets; an assignment Canvas hands to a tool or a quiz (WebAssign, a New Quiz) keeps its own.
SUBMISSIONS = ("none", "on_paper", "online_upload", "online_text_entry", "online_url")
FOREIGN = {"online_quiz", "discussion_topic"}  # the quiz's or discussion's own assignment: kept by the quiz or discussion
FILES_ROOT = "course files"  # Canvas's name for the top folder of a course's Files (Folder::ROOT_FOLDER_NAME)
UNFILED = f"{FILES_ROOT}/unfiled"  # where Canvas puts a file uploaded with no folder named (Folder.unfiled_folder)


def new_id(prefix):
    return f"{prefix}_{secrets.token_hex(8)}"


def fold(text):
    return " ".join(str(text or "").split()).casefold()


def default_content():
    return {"rule": RULE, "source_course": "", "started": "", "modules": [], "pages": [], "assignments": []}


def instant(value):
    """A Canvas time as `YYYY-MM-DDTHH:MM:SSZ` (UTC), or None."""
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise ValueError("A date and time must be text.")
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError(f"“{value}” is not a date and time.") from None
    if moment.tzinfo is None:
        raise ValueError("A date and time needs its time zone.")
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --- Faculty's copy: validation ------------------------------------------------------------------------------------------
def _text(value, label, longest, required=True):
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise ValueError(f"{label} must be text.")
    value = value.strip()
    if required and not value:
        raise ValueError(f"{label} is missing.")
    if len(value) > longest:
        raise ValueError(f"{label} can be at most {longest} characters long.")
    return value


def _flag(value, label, default=False):
    if value is None:
        return default
    if not isinstance(value, bool):
        raise ValueError(f"{label} must be yes or no.")
    return value


def _sections(value, sections, label):
    value = value or []
    if not isinstance(value, list) or any(not isinstance(s, str) or s not in sections for s in value):
        raise ValueError(f"{label} may only name sections of this term.")
    return sorted(set(value))


def _url(value, label):
    value = _text(value, label, URL)
    if not re.match(r"https?://[^\s]+$", value):
        raise ValueError(f"{label} must be a web address (http or https).")
    return value


def _ident(entry, known, taken, prefix, what, make=None):
    ident = entry.get("id")
    if ident in (None, ""):
        return (make or new_id)(prefix)
    if not isinstance(ident, str) or ident not in known:
        raise ValueError(f"A {what} was sent with an ID Faculty never gave it: reload and try again.")
    if ident in taken:
        raise ValueError(f"A {what} was sent twice: reload and try again.")
    taken.add(ident)
    return ident


def normalize(data, base=None, *, sections=frozenset(), direct=False, canvas_read=False, files=None):
    """The browser's copy, checked and canonical. IDs are the server's: an entry without `id` gets one, an `id` the base never
    held is refused. A new page may be named by an item of the same save through its `ref` (browser text, never stored).
    `direct`: one course's own content edited directly (`direct`), where two assignments may share a name (items name theirs by
    key, and nothing is found by name in another course). `canvas_read`: a course as Canvas holds it, checked only to be shown,
    so an assignment's missing Assignment Group is not refused (every save is). `files` {file id: name}: the files Faculty
    keeps that an item may name (FILES); an item whose file the base already held for it keeps it either way."""
    if not isinstance(data, dict):
        raise ValueError("Send the modules and pages.")
    base = base or default_content()
    make = None
    if direct:
        # A new object of a direct edit is never stored: the same draft must get the same keys in its preview and its push.
        counter = iter(range(1, 1_000_000))
        make = lambda prefix: f"{prefix}_n{next(counter)}"  # noqa: E731
    if data.get("rule", RULE) != RULE:
        raise ValueError("This copy was made for another version of Faculty: reload the page and make the change again.")
    source = data.get("source_course", base.get("source_course", ""))
    if not isinstance(source, str) or (source and not source.isdigit()):
        raise ValueError("The course the copy was started from is not a Canvas course.")
    pages_in = data.get("pages", base["pages"])
    modules_in = data.get("modules", base["modules"])
    if not isinstance(pages_in, list) or not isinstance(modules_in, list):
        raise ValueError("Send the modules and pages as lists.")
    if len(pages_in) > MAX_PAGES or len(modules_in) > MAX_MODULES:
        raise ValueError(f"Faculty keeps at most {MAX_MODULES} modules and {MAX_PAGES} pages.")
    known_pages = {p["id"] for p in base["pages"]}
    taken, refs, pages, fronts = set(), {}, [], 0
    for position, entry in enumerate(pages_in, 1):
        if not isinstance(entry, dict):
            raise ValueError(f"Page {position} is not a page.")
        ident = _ident(entry, known_pages, taken, "pg", "page", make)
        ref = entry.get("ref")
        if ref is not None and not entry.get("id"):
            if not isinstance(ref, str) or not 1 <= len(ref) <= 64 or ref in refs or ref in known_pages:
                raise ValueError(f"Page {position} was sent with an unusable reference: reload and try again.")
            refs[ref] = ident
        title = _text(entry.get("title"), f"Page {position}'s title", TITLE)
        body = entry.get("body") or ""
        if not isinstance(body, str) or len(body) > BODY:
            raise ValueError(f"The page “{title}” must be text of at most {BODY:,} characters.")
        front = _flag(entry.get("front_page"), f"Whether “{title}” is the front page")
        fronts += front
        published = _flag(entry.get("published"), f"Whether “{title}” is published", True)
        if front and not published:
            raise ValueError(f"The front page “{title}” must be published (Canvas requires it).")
        pages.append({"id": ident, "title": title, "body": body, "published": published, "front_page": front,
                      "sections": _sections(entry.get("sections"), sections, f"“{title}”")})
    if fronts > 1:
        raise ValueError("Only one page can be the front page.")
    page_ids = {p["id"] for p in pages}
    assignments, assignment_refs = _assignments(data.get("assignments", base.get("assignments", [])), base.get("assignments", []), sections, direct, make,
                                                 require_group=not canvas_read)
    assignment_ids = {a["id"] for a in assignments}
    known_modules = {m["id"] for m in base["modules"]}
    known_items = {i["id"] for m in base["modules"] for i in m["items"]}
    kept = (files or {}, {i["id"]: i["content"]["file"] for m in base["modules"] for i in m["items"] if (i.get("content") or {}).get("file")})
    taken_modules, taken_items, modules, count = set(), set(), [], 0
    for position, entry in enumerate(modules_in, 1):
        if not isinstance(entry, dict):
            raise ValueError(f"Module {position} is not a module.")
        ident = _ident(entry, known_modules, taken_modules, "mod", "module", make)
        name = _text(entry.get("name"), f"Module {position}'s name", TITLE)
        items_in = entry.get("items") or []
        if not isinstance(items_in, list):
            raise ValueError(f"The items of “{name}” must be a list.")
        items = []
        for spot, item in enumerate(items_in, 1):
            count += 1
            if count > MAX_ITEMS:
                raise ValueError(f"Faculty keeps at most {MAX_ITEMS} module items.")
            items.append(_item(item, spot, name, known_items, taken_items, page_ids, refs, sections, assignment_ids, assignment_refs, make, kept))
        modules.append({"id": ident, "name": name, "published": _flag(entry.get("published"), f"Whether “{name}” is published", True),
                        "unlock_at": instant(entry.get("unlock_at")),
                        "require_sequential_progress": _flag(entry.get("require_sequential_progress"), f"Sequential progress in “{name}”"),
                        "sections": _sections(entry.get("sections"), sections, f"“{name}”"), "items": items})
    # Canvas keeps a module item of a page or of an assignment published exactly when that page or assignment is (publishing the
    # item publishes it, and back): the copy keeps them alike, the page's or the assignment's own flag deciding.
    page_published = {p["id"]: p["published"] for p in pages}
    assignment_published = {a["id"]: a["published"] for a in assignments}
    for module in modules:
        for item in module["items"]:
            if item["type"] == "Page":
                item["published"] = page_published[item["page"]]
            elif (item.get("content") or {}).get("assignment"):
                item["published"] = assignment_published[item["content"]["assignment"]]
    return {"rule": RULE, "source_course": source, "started": base.get("started", ""), "modules": modules, "pages": pages,
            "assignments": assignments}


def follows_content(item):
    """Whether Canvas publishes a module item together with what it points to in the copy (a page, an assignment of the copy):
    such an item's own published flag is never sent, because Canvas would publish or unpublish the page or assignment with it
    (context_module_items_api_controller#update: update_asset_workflow_state!)."""
    return item["type"] == "Page" or bool((item.get("content") or {}).get("assignment"))


POINTS = re.compile(r"(?:0|[1-9][0-9]{0,5})(?:\.[0-9]{1,4})?")


def _points(value, label):
    text = str(value if value is not None else "").strip()
    if not POINTS.fullmatch(text) or float(text) > 100000:
        raise ValueError(f"{label} must be points from 0 to 100000, in ten-thousandths at most.")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _assignments(raw, prior, sections, direct=False, make=None, require_group=True):
    """Assignments: what Faculty keeps of each (Canvas's own details, rubrics and settings Faculty does not show stay in Canvas)."""
    if not isinstance(raw, list):
        raise ValueError("Send the assignments as a list.")
    if len(raw) > MAX_ASSIGNMENTS:
        raise ValueError(f"Faculty keeps at most {MAX_ASSIGNMENTS} assignments.")
    known, taken, clean, names, refs = {a["id"] for a in prior}, set(), [], set(), {}
    for position, entry in enumerate(raw, 1):
        if not isinstance(entry, dict):
            raise ValueError(f"Assignment {position} is not an assignment.")
        ident = _ident(entry, known, taken, "asg", "assignment", make)
        ref = entry.get("ref")
        if ref is not None and not entry.get("id"):
            if not isinstance(ref, str) or not 1 <= len(ref) <= 64 or ref in refs or ref in known:
                raise ValueError(f"Assignment {position} was sent with an unusable reference: reload and try again.")
            refs[ref] = ident
        name = _text(entry.get("name"), f"Assignment {position}'s name", TITLE)
        if fold(name) in names and not direct:
            raise ValueError(f"Two assignments are named “{name}” (Canvas finds them by name in each course).")
        names.add(fold(name))
        label = f"“{name}”"
        description = entry.get("description") or ""
        if not isinstance(description, str) or len(description) > BODY:
            raise ValueError(f"The description of {label} must be text of at most {BODY:,} characters.")
        grading = entry.get("grading_type") or "points"
        if grading not in GRADING_TYPES:
            raise ValueError(f"Choose how {label} is graded.")
        external = _flag(entry.get("external"), f"Whether {label} belongs to a tool")
        submissions = entry.get("submission_types")
        if external:
            submissions = None
        else:
            submissions = submissions or ["none"]
            if not isinstance(submissions, list) or any(t not in SUBMISSIONS for t in submissions):
                raise ValueError(f"Choose how students hand in {label}.")
            submissions = sorted(set(submissions))
        group = _text(entry.get("group"), f"The Assignment Group of {label}", TITLE, required=False)
        # Required (requirement, 2026-10-02): Canvas puts an assignment made without one in the
        # course's FIRST group, as it did Quiz 3 (Checks for Understanding).
        if require_group and not group:
            raise ValueError(f"Choose the Assignment Group of {label}: Canvas puts an assignment without one in the course's first "
                             "Assignment Group.")
        due = entry.get("section_due") or {}
        if not isinstance(due, dict) or any(k not in sections for k in due):
            raise ValueError(f"The section due dates of {label} may only name sections of this term.")
        item = entry.get("gradebook_item")
        if item is not None and (not isinstance(item, str) or not 1 <= len(item) <= 100):
            raise ValueError(f"{label} names a gradebook column Faculty does not have.")
        # The Faculty assignment it stands for (an assignments.id; "Put on Canvas", canvas/placement.py). Faculty-side only: kept out
        # of assignment_plan_state, so it never changes what is sent, a snapshot or a fingerprint.
        faculty = entry.get("faculty_assignment")
        if faculty is not None and (not isinstance(faculty, str) or not 1 <= len(faculty) <= 100):
            raise ValueError(f"{label} names a Faculty assignment Faculty does not have.")
        clean.append({"id": ident, "name": name, "description": description, "points": _points(entry.get("points", "0"), f"The points of {label}"),
                      "grading_type": grading, "submission_types": submissions, "external": external, "group": group,
                      "due_at": instant(entry.get("due_at")), "section_due": {k: instant(v) for k, v in sorted(due.items()) if instant(v)},
                      "unlock_at": instant(entry.get("unlock_at")), "lock_at": instant(entry.get("lock_at")),
                      "published": _flag(entry.get("published"), f"Whether {label} is published", True),
                      "omit_from_final_grade": _flag(entry.get("omit_from_final_grade"), f"Whether {label} counts in Canvas's total"),
                      "gradebook_item": item, "faculty_assignment": faculty, "sections": _sections(entry.get("sections"), sections, label)})
    return clean, refs


def _item(entry, spot, module, known, taken, page_ids, refs, sections, assignment_ids=frozenset(), assignment_refs=None, make=None,
          kept=({}, {})):
    if not isinstance(entry, dict):
        raise ValueError(f"Item {spot} of “{module}” is not an item.")
    ident = _ident(entry, known, taken, "itm", "module item", make)
    kind = entry.get("type")
    if kind not in ITEM_TYPES:
        raise ValueError(f"Item {spot} of “{module}” has a type Faculty does not keep.")
    title = _text(entry.get("title"), f"Item {spot} of “{module}”", TITLE)
    indent = entry.get("indent", 0)
    if type(indent) is not int or not 0 <= indent <= MAX_INDENT:
        raise ValueError(f"“{title}” can be indented 0 to {MAX_INDENT} levels.")
    clean = {"id": ident, "type": kind, "title": title, "indent": indent,
             "published": _flag(entry.get("published"), f"Whether “{title}” is published", True),
             "sections": _sections(entry.get("sections"), sections, f"“{title}”")}
    if kind in ("ExternalUrl", "ExternalTool"):
        clean["external_url"] = _url(entry.get("external_url"), f"The address of “{title}”")
        clean["new_tab"] = _flag(entry.get("new_tab"), f"Whether “{title}” opens in a new tab")
    if kind == "Page":
        page = entry.get("page")
        if not isinstance(page, str):
            raise ValueError(f"“{title}” must name one of the pages.")
        page = refs.get(page, page)
        if page not in page_ids:
            raise ValueError(f"“{title}” must name one of the pages.")
        clean["page"] = page
    if kind in NAMED or kind == "File":
        content = entry.get("content") if isinstance(entry.get("content"), dict) else {}
        if kind == "Assignment" and content.get("assignment"):
            if not isinstance(content["assignment"], str):
                raise ValueError(f"“{title}” must name one of the assignments.")
            target = (assignment_refs or {}).get(content["assignment"], content["assignment"])
            if target not in assignment_ids:
                raise ValueError(f"“{title}” must name one of the assignments.")
            clean["content"] = {"assignment": target}
        else:
            clean["content"] = {"name": _text(content.get("name"), f"What “{title}” points to", URL)}
        if kind == "File" and content.get("file") is not None:
            # FILES: the file Faculty keeps for this item; the push puts it in each course's Files at this folder and name.
            names, carried = kept
            file_id, path = content["file"], clean["content"]["name"]
            if not isinstance(file_id, str) or (carried.get(entry.get("id")) != file_id and file_id not in names):
                raise ValueError(f"“{title}” names a file Faculty does not keep. Add the file again.")
            folder, _, name = path.rpartition("/")
            if file_id in names and (name != names[file_id] or folder.split("/")[0] != FILES_ROOT
                                     or any(part.strip() != part or part in ("", ".", "..") for part in folder.split("/"))):
                raise ValueError(f"“{title}” must keep its file's name, in a folder of the course's Files.")
            clean["content"]["file"] = file_id
    if kind == "ExternalTool":
        content = entry.get("content") if isinstance(entry.get("content"), dict) else {}
        tool = str(content.get("tool") or "")
        if not tool.isdigit():
            raise ValueError(f"“{title}” must name its external tool.")
        clean["content"] = {"tool": tool}
    return clean


# --- one Canvas course as read -------------------------------------------------------------------------------------------
class Course:
    """What Faculty read of one Canvas course (`read`: modules with items, pages, named content, files), indexed."""

    def __init__(self, read):
        self.id = str(read["course_id"])
        self.modules = read["modules"]
        self.pages = read["pages"]
        self.assignments = read["assignments"]
        self.groups = read["groups"]
        self.named = {kind: read["named"].get(kind, {}) for kind in ("Quiz", "Discussion")}
        self.named["Assignment"] = {a["id"]: a["name"] for a in self.assignments}
        self.assignment_by_id = {a["id"]: a for a in self.assignments}
        self.group_by_name = _unique((fold(name), gid) for gid, name in self.groups.items())
        self.files = read["files"]
        self.file_info = read["file_info"]  # {file id: {size, locked}} (FILES)
        self.module_by_id = {m["id"]: m for m in self.modules}
        self.item_by_id = {i["id"]: (m, i) for m in self.modules for i in m["items"]}
        self.page_by_id = {p["id"]: p for p in self.pages}
        self.page_by_url = {p["url"]: p for p in self.pages}
        self.file_by_path = files_by_path(self.files)
        self.named_by_name = {kind: _unique((fold(name), cid) for cid, name in self.named[kind].items()) for kind in NAMED}

    def resolve(self, item):
        """The Canvas content ID an item of Faculty's copy points to in this course, or (None, reason). An item of one course
        edited directly carries the content its Canvas item points to now (`content_id`, set by the server, never by normalize):
        that is used while it is still in the course, so two quizzes or files of one name never hold it back."""
        kind = item["type"]
        hint = item.get("content_id")
        if hint and ((kind in NAMED and hint in self.named[kind]) or (kind == "File" and hint in self.files) or kind == "ExternalTool"):
            return hint, ""
        if kind in NAMED:
            found = self.named_by_name[kind].get(fold(item["content"]["name"]))
            if found is None:
                return None, f"no {kind.lower()} named “{item['content']['name']}”"
            if found is _MANY:
                return None, f"more than one {kind.lower()} is named “{item['content']['name']}”"
            return found, ""
        if kind == "File":
            found = self.file_by_path.get(item["content"]["name"])
            if found is None:
                return None, f"no file {item['content']['name']}"
            if found is _MANY:
                return None, f"more than one file is {item['content']['name']}"
            return found, ""
        if kind == "ExternalTool":
            return item["content"]["tool"], ""
        return "", ""


_MANY = object()


def files_by_path(files):
    """{folder path/name: file ID, or _MANY} of a course's Files. A file Faculty puts in the top folder arrives in Canvas's
    "unfiled" folder (Canvas's answer to an upload naming no folder, seen on canvas.tamu.edu 2026-10-16: the Week 8 Take Home
    Assignment PDF looked missing from the top folder at every preview, and each push uploaded it again as "…-1.pdf"), so a file
    there also stands for the top folder's file of its name while the top folder holds none."""
    found = _unique((path, fid) for fid, path in files.items())
    for fid, path in files.items():
        folder, _, name = path.rpartition("/")
        if folder == UNFILED and f"{FILES_ROOT}/{name}" not in found:
            found[f"{FILES_ROOT}/{name}"] = fid
    return found


def _unique(pairs):
    found = {}
    for key, value in pairs:
        found[key] = _MANY if key in found and found[key] != value else value
    return found


def content_of(item, course):
    """What a Canvas item points to, in Faculty's terms (the `content` of an item of Faculty's copy), or None."""
    kind = item["type"]
    if kind in NAMED:
        name = course.named[kind].get(item["content_id"])
        return {"name": name} if name is not None else None
    if kind == "File":
        path = course.files.get(item["content_id"])
        return {"name": path} if path is not None else None
    if kind == "ExternalTool":
        return {"tool": item["content_id"]} if item["content_id"] else None
    return None


# --- starting Faculty's copy from one course -------------------------------------------------------------------------------
def from_canvas(course, started):
    """Faculty's copy of one course (`Course`), and the pairs it made: [(kind, plan key, canvas id)]. Items whose target cannot be
    named the same way in another course (a page that is not in the course, content Canvas did not list) are left out and
    reported."""
    pairs, skipped, pages, by_url = [], [], [], {}
    for page in course.pages:
        ident = new_id("pg")
        by_url[page["url"]] = ident
        pages.append({"id": ident, "title": page["title"][:TITLE] or "Untitled page", "body": page["body"], "published": page["published"],
                      "front_page": page["front_page"], "sections": []})
        pairs.append(("page", ident, page["id"]))
    assignments, by_canvas = [], {}
    for a in course.assignments:
        if FOREIGN & set(a["submission_types"]):
            continue  # a quiz's or discussion's own assignment: the quiz or discussion keeps it
        ident = new_id("asg")
        assignments.append(assignment_from(a, course, ident))
        by_canvas[a["id"]] = ident
        pairs.append(("assignment", ident, a["id"]))
    modules = []
    for module in course.modules:
        ident, items = new_id("mod"), []
        pairs.append(("module", ident, module["id"]))
        for item in module["items"]:
            if item["type"] not in ITEM_TYPES:
                skipped.append(f"{module['name']}: “{item['title']}” ({item['type']})")
                continue
            entry = {"id": new_id("itm"), "type": item["type"], "title": item["title"][:TITLE] or "Untitled", "indent": min(item["indent"], MAX_INDENT),
                     "published": item["published"], "sections": []}
            if item["type"] in ("ExternalUrl", "ExternalTool"):
                entry["external_url"], entry["new_tab"] = item["external_url"], item["new_tab"]
            if item["type"] == "Page":
                if item["page_url"] not in by_url:
                    skipped.append(f"{module['name']}: “{item['title']}” (its page was not found)")
                    continue
                entry["page"] = by_url[item["page_url"]]
            if item["type"] in NAMED or item["type"] in ("File", "ExternalTool"):
                content = {"assignment": by_canvas[item["content_id"]]} if item["type"] == "Assignment" and item["content_id"] in by_canvas \
                    else content_of(item, course)
                if content is None:
                    skipped.append(f"{module['name']}: “{item['title']}” (what it points to was not found)")
                    continue
                entry["content"] = content
            items.append(entry)
            pairs.append(("item", entry["id"], item["id"]))
        modules.append({"id": ident, "name": module["name"][:TITLE] or "Untitled module", "published": module["published"],
                        "unlock_at": instant(module["unlock_at"]), "require_sequential_progress": module["require_sequential_progress"],
                        "sections": [], "items": items})
    return {"rule": RULE, "source_course": course.id, "started": started, "modules": modules, "pages": pages,
            "assignments": assignments}, pairs, skipped


# --- one course edited directly ------------------------------------------------------------------------------------------
DIRECT_PREFIX = {"module": "mod_c", "item": "itm_c", "page": "pg_c", "assignment": "asg_c"}


def direct_key(kind, canvas_id):
    return f"{DIRECT_PREFIX[kind]}{canvas_id}"


def direct_canvas_id(key):
    """The Canvas ID a direct key names, or None for anything else (a new object of the draft)."""
    for prefix in DIRECT_PREFIX.values():
        if key.startswith(prefix) and key[len(prefix):].isdigit():
            return key[len(prefix):]
    return None


def direct(course):
    """One course's own content in Faculty's terms, for editing that course directly (canvas/section_content.py): like
    `from_canvas`, but every key is derived from the Canvas ID (`mod_c<id>` ...), so the same read gives the same keys, and
    nothing is paired with another course. Returns (content, skipped): items the editor cannot show (their type, a page or
    target not found, an address Faculty does not keep) are left out and named; the plan never removes them unless ticked."""
    skipped, pages, by_url = [], [], {}
    for page in course.pages:
        key = direct_key("page", page["id"])
        by_url[page["url"]] = key
        pages.append({"id": key, "title": page["title"][:TITLE] or "Untitled page", "body": page["body"], "published": page["published"] or page["front_page"],
                      "front_page": page["front_page"], "sections": []})
    assignments, by_canvas = [], {}
    for a in course.assignments:
        if FOREIGN & set(a["submission_types"]):
            continue
        key = direct_key("assignment", a["id"])
        assignments.append(assignment_from(a, course, key))
        by_canvas[a["id"]] = key
    modules = []
    for module in course.modules:
        items = []
        for item in module["items"]:
            label = f"{module['name']}: “{item['title']}”"
            if item["type"] not in ITEM_TYPES:
                skipped.append(f"{label} ({item['type']})")
                continue
            entry = {"id": direct_key("item", item["id"]), "type": item["type"], "title": item["title"][:TITLE] or "Untitled",
                     "indent": min(item["indent"], MAX_INDENT), "published": item["published"], "sections": []}
            if item["type"] in ("ExternalUrl", "ExternalTool"):
                try:
                    entry["external_url"] = _url(item["external_url"], "An address")
                except ValueError:
                    skipped.append(f"{label} (its address)")
                    continue
                entry["new_tab"] = item["new_tab"]
            if item["type"] == "Page":
                if item["page_url"] not in by_url:
                    skipped.append(f"{label} (its page was not found)")
                    continue
                entry["page"] = by_url[item["page_url"]]
            if item["type"] in NAMED or item["type"] in ("File", "ExternalTool"):
                content = {"assignment": by_canvas[item["content_id"]]} if item["type"] == "Assignment" and item["content_id"] in by_canvas \
                    else content_of(item, course)
                if content is None:
                    skipped.append(f"{label} (what it points to was not found)")
                    continue
                entry["content"] = content
            items.append(entry)
        modules.append({"id": direct_key("module", module["id"]), "name": module["name"][:TITLE] or "Untitled module", "published": module["published"],
                        "unlock_at": instant(module["unlock_at"]), "require_sequential_progress": module["require_sequential_progress"],
                        "sections": [], "items": items})
    return {"rule": RULE, "source_course": course.id, "started": "", "modules": modules, "pages": pages, "assignments": assignments}, skipped


def object_states(content, course):
    """{"kind:key": snapshot(Faculty's terms, Canvas's terms)} of every object of a direct copy that is in `course` now: what the
    page was opened on (its base), and what a preview compares Canvas with to tell an edit made in Canvas since."""
    states = {}
    for a in content["assignments"]:
        row = course.assignment_by_id.get(direct_canvas_id(a["id"]) or "")
        if row:
            states[f"assignment:{a['id']}"] = snapshot(assignment_plan_state(a), assignment_canvas_state(row, course.groups))
    for page in content["pages"]:
        row = course.page_by_id.get(direct_canvas_id(page["id"]) or "")
        if row:
            states[f"page:{page['id']}"] = snapshot(page_plan_state(page), page_canvas_state(row))
    for module in content["modules"]:
        row = course.module_by_id.get(direct_canvas_id(module["id"]) or "")
        if row:
            states[f"module:{module['id']}"] = snapshot(module_plan_state(module), module_state(row))
        for item in module["items"]:
            found = course.item_by_id.get(direct_canvas_id(item["id"]) or "")
            if found:
                states[f"item:{item['id']}"] = snapshot(item_plan_state(item), item_canvas_state(found[1]))
    return states


def points_text(value):
    """Canvas's points possible as Faculty's text ("10", "2.5"), "0" when absent."""
    if value in (None, ""):
        return "0"
    text = f"{float(value):.4f}".rstrip("0").rstrip(".")
    return text or "0"


def is_external(assignment):
    return not set(assignment["submission_types"]) <= set(SUBMISSIONS)


def assignment_from(a, course, ident):
    external = is_external(a)
    return {"id": ident, "name": a["name"][:TITLE] or "Untitled assignment", "description": a["description"], "points": points_text(a["points"]),
            "grading_type": a["grading_type"] if a["grading_type"] in GRADING_TYPES else "points",
            "submission_types": None if external else sorted(a["submission_types"] or ["none"]), "external": external,
            "group": course.groups.get(a["group_id"], ""), "due_at": instant(a["due_at"]), "section_due": {},
            "unlock_at": instant(a["unlock_at"]), "lock_at": instant(a["lock_at"]), "published": a["published"],
            "omit_from_final_grade": a["omit_from_final_grade"], "gradebook_item": None, "faculty_assignment": None, "sections": []}


# --- matching ----------------------------------------------------------------------------------------------------------------
def applies(entry, course_sections):
    return not entry.get("sections") or bool(set(entry["sections"]) & set(course_sections))


def match(content, course, links, course_sections):
    """{kind: {plan key: canvas id}} for this course, and how each was found ({kind: {plan key: "recorded"|"paired"|"name"}}).
    `links` {(kind, plan key): latest link row (dict with canvas_id, source)} of this course."""
    found = {kind: {} for kind in KINDS}
    how = {kind: {} for kind in KINDS}
    claimed = {kind: set() for kind in KINDS}
    exists = {"module": course.module_by_id, "item": course.item_by_id, "page": course.page_by_id}

    def recorded(kind, key):
        link = links.get((kind, key))
        if link and link["canvas_id"] and link["canvas_id"] in exists[kind] and link["canvas_id"] not in claimed[kind]:
            return link
        return None

    modules = [m for m in content["modules"] if applies(m, course_sections)]
    pages = [p for p in content["pages"] if applies(p, course_sections)]
    assignments = [a for a in content.get("assignments", []) if applies(a, course_sections)]
    exists["assignment"] = course.assignment_by_id
    # Recorded pairs first (the instructor's own pairing, or Faculty's writes), for every kind, so names never steal them.
    for kind, entries in (("page", pages), ("module", modules), ("assignment", assignments),
                          ("item", [i for m in modules for i in m["items"] if applies(i, course_sections)])):
        for entry in entries:
            link = recorded(kind, entry["id"])
            if link:
                found[kind][entry["id"]] = link["canvas_id"]
                how[kind][entry["id"]] = "paired" if link["source"] == "paired" else "recorded"
                claimed[kind].add(link["canvas_id"])
    # Then by name: exactly one on each side.
    _by_name("page", pages, course.pages, lambda p: p["title"], found, how, claimed, links)
    _by_name("assignment", assignments, [a for a in course.assignments if not FOREIGN & set(a["submission_types"])],
             lambda a: a["name"], found, how, claimed, links)
    _by_name("module", modules, course.modules, lambda m: m["name"], found, how, claimed, links)
    # A page the course holds more than once: the copy its module item points at, then the rest by name when that leaves one each.
    if _by_module_link(modules, pages, course, course_sections, found, how, claimed, links):
        _by_name("page", pages, course.pages, lambda p: p["title"], found, how, claimed, links)
    for module in modules:
        canvas_module = course.module_by_id.get(found["module"].get(module["id"]))
        items = [i for i in module["items"] if applies(i, course_sections)]
        if canvas_module:
            _by_name("item", items, canvas_module["items"], lambda i: (i["type"], fold(i["title"])), found, how, claimed, links, folded=True)
    everywhere = [i for m in course.modules for i in m["items"]]
    for module in modules:
        items = [i for i in module["items"] if applies(i, course_sections)]
        _by_name("item", items, everywhere, lambda i: (i["type"], fold(i["title"])), found, how, claimed, links, folded=True)
    return found, how


def _by_module_link(modules, pages, course, course_sections, found, how, claimed, links):
    """Pages of one name the course holds more than once (requirement, 2026-09-30: sections 508, 514 and 515 each had two copies of
    five pages): a page of Faculty's copy is the Canvas copy of its name that the course's matching module item points at, i.e. the
    item of the same title in the module matched to the one holding Faculty's item. Only when exactly one such copy is found, and no
    other page of the copy found it too; anything else stays unmatched (and "Same as…" pairs it). Returns whether any was paired."""
    titles = {p["id"]: fold(p["title"]) for p in pages}
    votes = {}
    for module in modules:
        canvas_module = course.module_by_id.get(found["module"].get(module["id"]))
        if not canvas_module:
            continue
        for item in module["items"]:
            key = item.get("page")
            if item["type"] != "Page" or not applies(item, course_sections) or key not in titles or key in found["page"] \
                    or _unpaired(links.get(("page", key))):
                continue
            for other in canvas_module["items"]:
                page = course.page_by_url.get(other["page_url"]) if other["type"] == "Page" and fold(other["title"]) == fold(item["title"]) else None
                if page and page["id"] not in claimed["page"] and fold(page["title"]) == titles[key]:
                    votes.setdefault(key, set()).add(page["id"])
    chosen = {key: next(iter(ids)) for key, ids in votes.items() if len(ids) == 1}
    taken = [cid for cid in chosen.values()]
    paired = False
    for key, cid in chosen.items():
        if taken.count(cid) == 1:
            found["page"][key] = cid
            how["page"][key] = "module"
            claimed["page"].add(cid)
            paired = True
    return paired


def _by_name(kind, entries, canvas, key, found, how, claimed, links, folded=False):
    norm = (lambda value: value) if folded else fold
    open_entries = [e for e in entries if e["id"] not in found[kind] and not _unpaired(links.get((kind, e["id"])))]
    open_canvas = [c for c in canvas if c["id"] not in claimed[kind]]
    ours, theirs = {}, {}
    for entry in open_entries:
        ours.setdefault(norm(key(entry)), []).append(entry)
    for thing in open_canvas:
        theirs.setdefault(norm(key(thing)), []).append(thing)
    for name, mine in ours.items():
        other = theirs.get(name, [])
        if len(mine) == 1 and len(other) == 1:
            found[kind][mine[0]["id"]] = other[0]["id"]
            how[kind][mine[0]["id"]] = "name"
            claimed[kind].add(other[0]["id"])


def _unpaired(link):
    """The instructor said this object is new in this course: never matched by name."""
    return bool(link and link["source"] == "unpaired")


# --- page bodies, per course ---------------------------------------------------------------------------------------------------
LINK = re.compile(r"(?P<host>https?://[A-Za-z0-9.-]+(?::\d+)?)?(?P<api>/api/v1)?/courses/(?P<course>\d+)(?P<rest>(?:/[A-Za-z0-9_.~%:+-]+)*)")


def render(body, target, courses, matches, pending=frozenset()):
    """The body for course `target` (a course ID): every link to one of the term's courses (`courses` {id: Course}, `matches`
    {course id: {kind: {plan key: canvas id}}}) points at the same thing in `target`. Returns (html, problems): a link that cannot
    be found in `target` is left as it was and named in `problems`, and the page is not pushed there. A link to a page of `pending`
    (plan keys of pages this push creates in `target`) is left for the push to write once that page exists."""
    problems = []
    reverse = {cid: {kind: {v: k for k, v in m[kind].items()} for kind in KINDS} for cid, m in matches.items()}

    def swap(found):
        source = found.group("course")
        if source not in courses or target not in courses:
            return found.group(0)
        prefix = f"{found.group('host') or ''}{found.group('api') or ''}/courses/{target}"
        parts = found.group("rest").split("/")[1:]
        if source == target or not parts:
            return prefix + found.group("rest")
        src, dst = courses[source], courses[target]
        kind, rest = parts[0], parts[1:]
        if not rest:
            return prefix + found.group("rest")
        key = rest[0]
        mapped = None
        if kind == "files" and key.isdigit():
            path = src.files.get(key)
            target_id = dst.file_by_path.get(path) if path else None
            mapped = target_id if target_id not in (None, _MANY) else None
            if mapped is None:
                problems.append(f"the file {path or key} is not in this course" if target_id is None else f"more than one file is {path}")
        elif kind == "pages":
            page = src.page_by_url.get(key) or src.page_by_id.get(key.removeprefix("page_id:"))
            plan_key = reverse[source]["page"].get(page["id"]) if page else None
            canvas_id = matches[target]["page"].get(plan_key) if plan_key else None
            target_page = dst.page_by_id.get(canvas_id) if canvas_id else None
            mapped = target_page["url"] if target_page else None
            if mapped is None and plan_key in pending:
                return found.group(0)
            if mapped is None:
                problems.append(f"the page “{page['title'] if page else key}” is not in this course yet")
        elif kind == "modules" and key == "items" and len(rest) > 1:
            plan_key = reverse[source]["item"].get(rest[1])
            mapped_item = matches[target]["item"].get(plan_key) if plan_key else None
            if mapped_item:
                return f"{prefix}/modules/items/{mapped_item}" + "".join(f"/{p}" for p in rest[2:])
            problems.append("a module item link is not in this course yet")
            return found.group(0)
        elif kind == "modules" and key.isdigit():
            plan_key = reverse[source]["module"].get(key)
            mapped = matches[target]["module"].get(plan_key) if plan_key else None
            if mapped is None:
                problems.append("a module link is not in this course yet")
        elif kind == "assignments" and key.isdigit() and reverse[source]["assignment"].get(key):
            plan_key = reverse[source]["assignment"][key]
            mapped = matches[target]["assignment"].get(plan_key)
            if mapped is None and plan_key in pending:
                return found.group(0)
            if mapped is None:
                problems.append(f"the assignment “{src.named['Assignment'].get(key, key)}” is not in this course yet")
        elif kind in ("assignments", "quizzes", "discussion_topics") and key.isdigit():
            named = {"assignments": "Assignment", "quizzes": "Quiz", "discussion_topics": "Discussion"}[kind]
            name = src.named[named].get(key)
            target_id = dst.named_by_name[named].get(fold(name)) if name is not None else None
            mapped = target_id if target_id not in (None, _MANY) else None
            if mapped is None:
                problems.append(f"the {named.lower()} “{name or key}” is not in this course" if target_id is None else f"more than one {named.lower()} is named “{name}”")
        else:
            return prefix + found.group("rest")  # course-wide pages, tools and anything else: the same path in the other course
        if mapped is None:
            return found.group(0)
        return f"{prefix}/{kind}/{mapped}" + "".join(f"/{p}" for p in rest[1:])

    def media(found):
        """A tag embedding a course's uploaded video or audio (`/media_attachments_iframe/<file id>`, which names no course): the
        same file of the target course, found by its folder and name like a file link; the source's `data-media-id` (its own
        media object) is dropped with it, so the target's player never reaches the other course's copy."""
        tag, changed = found.group(0), False

        def one(match):
            nonlocal changed
            file_id = match.group("file")
            owner = next((cid for cid, c in courses.items() if file_id in c.files), None)
            if owner is None or owner == target or target not in courses:
                return match.group(0)
            path = courses[owner].files[file_id]
            mapped = courses[target].file_by_path.get(path)
            if mapped in (None, _MANY):
                problems.append(f"the video or audio file {path} is not in this course" if mapped is None else f"more than one file is {path}")
                return match.group(0)
            changed = True
            return f"/media_attachments_iframe/{mapped}"

        tag = MEDIA_FILE.sub(one, tag)
        return re.sub(r'\s+data-media-id="[^"]*"', "", tag) if changed else tag

    body = LINK.sub(swap, body or "")
    return MEDIA_TAG.sub(media, body), sorted(set(problems))


MEDIA_FILE = re.compile(r"/media_attachments_iframe/(?P<file>\d+)")
MEDIA_TAG = re.compile(r"<[A-Za-z][^<>]*?/media_attachments_iframe/\d+[^<>]*>")


def _squash(html):
    return re.sub(r"\s+", " ", re.sub(r">\s+<", "><", (html or "").strip()))


def same_html(a, b):
    """Two bodies Canvas would show alike: whitespace between and inside tags collapsed."""
    return _squash(a) == _squash(b)


# --- states, in Canvas's terms and in Faculty's ------------------------------------------------------------------------------
def module_state(module):
    return {"name": module["name"], "published": module["published"], "unlock_at": instant(module["unlock_at"]),
            "require_sequential_progress": module["require_sequential_progress"]}


MODULE_FIELDS = ("name", "published", "unlock_at", "require_sequential_progress")


def module_plan_state(module):
    """A module in Faculty's terms; the order is the one `plan` reports an update's `fields` in."""
    return {key: module[key] for key in MODULE_FIELDS}


def item_canvas_state(item):
    state = {"type": item["type"], "title": item["title"], "indent": item["indent"], "published": item["published"]}
    if item["type"] in ("ExternalUrl", "ExternalTool"):
        state.update(external_url=item["external_url"], new_tab=item["new_tab"])
    if item["type"] == "Page":
        state["page_url"] = item["page_url"]
    if item["type"] in NAMED or item["type"] in ("File", "ExternalTool"):
        state["content_id"] = item["content_id"]
    return state


def item_plan_state(item):
    return {key: item[key] for key in ("type", "title", "indent", "published", "external_url", "new_tab", "page", "content") if key in item}


def page_canvas_state(page):
    return {"title": page["title"], "body": _squash(page["body"]),
            "published": page["published"], "front_page": page["front_page"]}


def page_plan_state(page):
    return {key: page[key] for key in ("title", "body", "published", "front_page")}


def snapshot(sent, canvas):
    """What a link records: digests of what Faculty sent (its terms) and of what Canvas held after it (Canvas's terms)."""
    return {"sent": digest(sent), "canvas": digest(canvas)}


# --- the plan --------------------------------------------------------------------------------------------------------------
def plan(content, courses, links, course_sections, matches, choices, files=None, uploads=None):
    """What a push would do, per course. `courses` {id: Course}; `links` {course id: {(kind, key): link}}; `course_sections`
    {course id: [section ids]}; `matches` {course id: (found, how)}; `choices` {course id: {"replace": [object keys] or "all",
    "remove": [object keys] or None (the defaults)}} where an object key is "page:<plan key>", "module:<canvas id>" and so on.
    FILES: `files` {file id: {name, size, sha256}} the files Faculty keeps; `uploads` {course id: {path: {file_id, sha256,
    canvas_file_id}}} what Faculty last put at a folder and name of each course. Returns {course id: CoursePlan} (see
    canvas/README.md)."""
    found_all = {cid: matches[cid][0] for cid in courses}
    result = {}
    for cid, course in courses.items():
        found, how = matches[cid]
        chosen = choices.get(cid) or {}
        replace = chosen.get("replace") or []
        replace_all = replace == "all"
        mine = links.get(cid, {})
        sections = course_sections.get(cid, [])
        entries, ops, blocked = [], [], []

        def decide(kind, key, label, sent, desired_canvas, canvas_now, canvas_id):
            """One object's verdict: same, update, differs (first time), edited_in_canvas or conflict; and whether to write."""
            link = mine.get((kind, key))
            snap = json.loads(link["snapshot_json"]) if link and link["canvas_id"] == canvas_id and link["snapshot_json"] else None
            object_key = f"{kind}:{key}"
            ticked = replace_all or object_key in replace
            now_equal = desired_canvas is not None and _equal(kind, desired_canvas, canvas_now)
            if snap and snap.get("sent") and snap.get("canvas"):
                plan_changed = snap["sent"] != digest(sent)
                canvas_changed = snap["canvas"] != digest(canvas_now)
                if now_equal:
                    verdict = "same"
                elif plan_changed and not canvas_changed:
                    verdict = "update"
                elif canvas_changed and not plan_changed and kind == "assignment" and _published_since(desired_canvas, canvas_now):
                    verdict = "same"  # PUBLISHED IN CANVAS STANDS (docs/decisions/canvas.md): nothing to ask, and never unpublished
                elif canvas_changed and not plan_changed:
                    verdict = "edited_in_canvas"
                elif canvas_changed:
                    verdict = "conflict"
                else:
                    verdict = "same"  # Faculty sent this and Canvas holds what it held then (its own rendering of it)
            else:
                verdict = "same" if now_equal else "differs"
            write = verdict == "update" or (verdict in ("differs", "edited_in_canvas", "conflict") and ticked)
            entries.append({"key": object_key, "kind": kind, "label": label, "canvas_id": canvas_id, "verdict": verdict,
                            "write": write, "how": how[kind].get(key, ""), "ticked": ticked})
            if kind == "assignment" and verdict == "same" and snap and not now_equal and _published_since(desired_canvas, canvas_now):
                entries[-1]["published_in_canvas"] = True
            if verdict in ('differs', 'edited_in_canvas', 'conflict'):
                # CANVAS: compare exact rendered values; snapshots retain hashes, never invented base text.
                fields = []
                for field in sorted(set(desired_canvas or {}) | set(canvas_now or {})):
                    wanted, current = (desired_canvas or {}).get(field), (canvas_now or {}).get(field)
                    if wanted == current:
                        continue
                    clipped = any(isinstance(v, str) and len(v) > 12000 for v in (wanted, current))
                    fields.append({'field': field, 'faculty': wanted[:12000] if isinstance(wanted, str) else wanted,
                                   'canvas': current[:12000] if isinstance(current, str) else current, 'truncated': clipped})
                entries[-1]['differences'] = fields
                entries[-1]['comparison_sha256'] = digest([desired_canvas, canvas_now])
            return write, verdict

        # Assignments (and the groups they name), first: module items and page links find them by name.
        pending = _made_here(content, cid, courses, found_all, sections, found)
        new_groups = []
        for assignment in content.get("assignments", []):
            if not applies(assignment, sections):
                continue
            canvas_id = found["assignment"].get(assignment["id"])
            label = assignment["name"]
            due, due_problem = due_for(assignment, sections)
            description, problems = render(assignment["description"], cid, courses, found_all, pending)
            if due_problem:
                problems = [*problems, due_problem]
            if problems:
                blocked.append({"key": f"assignment:{assignment['id']}", "label": label, "reasons": problems})
                continue
            group = assignment["group"]
            if not group and not canvas_id:
                # Saved before an Assignment Group was required: never made in the course's first group by default.
                blocked.append({"key": f"assignment:{assignment['id']}", "label": label,
                                "reasons": ["choose its Assignment Group (Canvas would put it in the course's first Assignment Group)"]})
                continue
            if group and course.group_by_name.get(fold(group)) is None and fold(group) not in {fold(g) for g in new_groups}:
                new_groups.append(group)
                ops.append({"op": "create_group", "name": group, "label": f"Assignment Group “{group}”"})
            if canvas_id:
                current_row = course.assignment_by_id[canvas_id]
                current = assignment_canvas_state(current_row, course.groups)
                desired = {**assignment_plan_canvas(assignment, due, description), "group": group or current["group"]}
                write, _ = decide("assignment", assignment["id"], label, assignment_plan_state(assignment), desired, current, canvas_id)
                write = write or _relinked(entries[-1], assignment["description"], cid, courses, found_all, pending)
                if write:
                    ops.append({"op": "update_assignment", "key": assignment["id"], "canvas_id": canvas_id, "label": label,
                                "fields": _changed(desired, current)})
            else:
                entries.append({"key": f"assignment:{assignment['id']}", "kind": "assignment", "label": label, "canvas_id": "", "verdict": "new",
                                "write": True, "how": "", "ticked": False})
                ops.append({"op": "create_assignment", "key": assignment["id"], "label": label})
        # Pages.
        page_urls = {}
        for page in content["pages"]:
            if not applies(page, sections):
                continue
            canvas_id = found["page"].get(page["id"])
            body, problems = render(page["body"], cid, courses, found_all, pending)
            if problems:
                blocked.append({"key": f"page:{page['id']}", "label": page["title"], "reasons": problems})
            desired = {**page_plan_state(page), "body": body}
            if canvas_id:
                current = course.page_by_id[canvas_id]
                page_urls[page["id"]] = current["url"]
                if problems:
                    continue
                write, _ = decide("page", page["id"], page["title"], page_plan_state(page), page_canvas_state(desired),
                                  page_canvas_state(current), canvas_id)
                write = write or _relinked(entries[-1], page["body"], cid, courses, found_all, pending)
                if write:
                    ops.append({"op": "update_page", "key": page["id"], "canvas_id": canvas_id, "label": page["title"],
                                "fields": _changed(page_canvas_state(desired), page_canvas_state(current))})
            elif not problems:
                entries.append({"key": f"page:{page['id']}", "kind": "page", "label": page["title"], "canvas_id": "", "verdict": "new",
                                "write": True, "how": "", "ticked": False})
                ops.append({"op": "create_page", "key": page["id"], "label": page["title"]})
        # Modules, then their items.
        managed = []
        for module in content["modules"]:
            if not applies(module, sections):
                continue
            canvas_id = found["module"].get(module["id"])
            managed.append((module, canvas_id))
            wanted = module_plan_state(module)
            if canvas_id:
                current = module_state(course.module_by_id[canvas_id])
                write, _ = decide("module", module["id"], module["name"], wanted, wanted, current, canvas_id)
                if write:
                    ops.append({"op": "update_module", "key": module["id"], "canvas_id": canvas_id, "label": module["name"],
                                "fields": _changed(wanted, current)})
            else:
                entries.append({"key": f"module:{module['id']}", "kind": "module", "label": module["name"], "canvas_id": "", "verdict": "new",
                                "write": True, "how": "", "ticked": False})
                ops.append({"op": "create_module", "key": module["id"], "label": module["name"]})
        file_ops, file_entries, arriving, file_held = _files_plan([m for m, _ in managed], course, sections, files or {},
                                                                  (uploads or {}).get(cid, {}), lambda key: replace_all or key in replace)
        ops.extend(file_ops)
        entries.extend(file_entries)
        for module, module_canvas in managed:
            for item in module["items"]:
                if not applies(item, sections):
                    continue
                label = f"{module['name']} › {item['title']}"
                assignment_key = (item.get("content") or {}).get("assignment")
                if assignment_key:
                    target, reason = found["assignment"].get(assignment_key), ""
                    if any(b["key"] == f"assignment:{assignment_key}" for b in blocked):
                        target, reason = None, "its assignment cannot be pushed here (see the assignment)"
                    elif target is None and assignment_key in pending:
                        target = PENDING  # made by this push: the item is written after it
                    elif target is None:
                        reason = "its assignment is not pushed to this course"
                elif item["id"] in file_held:
                    target, reason = None, file_held[item["id"]]
                elif (item.get("content") or {}).get("file") and item["content"]["name"] in arriving:
                    target, reason = PENDING, ""  # its file is put in this course by this push: the item is written after it
                else:
                    target, reason = course.resolve(item)
                if item["type"] == "Page":
                    page = next((p for p in content["pages"] if p["id"] == item["page"]), None)
                    if not page or not applies(page, sections):
                        blocked.append({"key": f"item:{item['id']}", "label": label, "reasons": ["its page is not pushed to this course"]})
                        continue
                    if any(b["key"] == f"page:{page['id']}" for b in blocked):
                        blocked.append({"key": f"item:{item['id']}", "label": label, "reasons": ["its page cannot be pushed here (see the page)"]})
                        continue
                if target is None:
                    blocked.append({"key": f"item:{item['id']}", "label": label, "reasons": [reason]})
                    continue
                canvas_id = found["item"].get(item["id"])
                if canvas_id:
                    canvas_module, current_item = course.item_by_id[canvas_id]
                    desired_canvas = _desired_item(item, target, page_urls)
                    current = item_canvas_state(current_item)
                    if desired_canvas is not None and follows_content(item):
                        # Published with its page or assignment, which says so itself: the item never asks Canvas for it.
                        desired_canvas["published"] = current["published"]
                    write, _ = decide("item", item["id"], label, item_plan_state(item), desired_canvas, current, canvas_id)
                    moved = module_canvas is not None and canvas_module["id"] != module_canvas
                    if (moved or module_canvas is None) and how["item"].get(item["id"]) == "name":
                        # Found only by its title, in another module, with no record of it: perhaps another item of that title
                        # (2026-10-04 review: a section's own "Zoom link" in Week 3 was moved to the copy's Week 9). Moved, and
                        # written, only when the instructor ticks Replace.
                        entry = entries[-1]
                        entry["verdict"], entry["write"] = "elsewhere", entry["ticked"]
                        write = entry["ticked"]
                        if not write:
                            continue
                    if desired_canvas is None:
                        # Its page, assignment or file is made by this push: the item is replaced by one pointing at it.
                        ops.append({"op": "replace_item", "key": item["id"], "canvas_id": canvas_id, "label": label, "module": module["id"]})
                        continue
                    fields = {}
                    if write:
                        fields = _changed(desired_canvas, current) if desired_canvas else {}
                        fields.pop("type", None)
                        if fields.pop("content_id", None) is not None or ("page_url" in fields and item["type"] == "Page"):
                            # What an item points to cannot change in Canvas: it is replaced by a new item, made in Faculty's module
                            # (so it is not moved as well: the move would name the item the replacement deleted).
                            ops.append({"op": "replace_item", "key": item["id"], "canvas_id": canvas_id, "label": label, "module": module["id"]})
                            continue
                    if moved or module_canvas is None:
                        ops.append({"op": "move_item", "key": item["id"], "canvas_id": canvas_id, "label": label, "module": module["id"]})
                    if fields:
                        ops.append({"op": "update_item", "key": item["id"], "canvas_id": canvas_id, "label": label, "fields": fields})
                else:
                    entries.append({"key": f"item:{item['id']}", "kind": "item", "label": label, "canvas_id": "", "verdict": "new",
                                    "write": True, "how": "", "ticked": False})
                    ops.append({"op": "create_item", "key": item["id"], "label": label, "module": module["id"]})
        # Only in Canvas: offered for removal.
        claimed = {kind: set(found[kind].values()) for kind in KINDS}
        # Only what Faculty paired or wrote since its copy was started (starting again forgets what the earlier copy managed).
        once = {kind: {link["canvas_id"] for (k, _key), link in mine.items() if k == kind and link["canvas_id"]
                       and link["source"] in ("pushed", "matched", "paired") and link["created_at"] >= content.get("started", "")}
                for kind in KINDS}
        plan_keys = {"module": {m["id"] for m in content["modules"] if applies(m, sections)},
                     "item": {i["id"] for m in content["modules"] if applies(m, sections) for i in m["items"] if applies(i, sections)},
                     "page": {p["id"] for p in content["pages"] if applies(p, sections)},
                     "assignment": {a["id"] for a in content.get("assignments", []) if applies(a, sections)}}
        still = {kind: {link["canvas_id"] for (k, key), link in mine.items() if k == kind and key in plan_keys[kind]} for kind in KINDS}
        extra = []
        managed_ids = {canvas_id for _, canvas_id in managed if canvas_id}
        for module in course.modules:
            if module["id"] in claimed["module"]:
                for item in module["items"]:
                    if item["id"] not in claimed["item"]:
                        faculty_once = item["id"] in once["item"] and item["id"] not in still["item"]
                        extra.append({"key": f"item:{item['id']}", "kind": "item", "canvas_id": item["id"], "label": f"{module['name']} › {item['title']}",
                                      "type": item["type"], "was_faculty": faculty_once, "default": faculty_once})
            else:
                faculty_once = module["id"] in once["module"] and module["id"] not in still["module"]
                extra.append({"key": f"module:{module['id']}", "kind": "module", "canvas_id": module["id"], "label": module["name"],
                              "items": len(module["items"]), "was_faculty": faculty_once, "default": faculty_once})
        for a in course.assignments:
            if a["id"] not in claimed["assignment"] and not FOREIGN & set(a["submission_types"]):
                extra.append({"key": f"assignment:{a['id']}", "kind": "assignment", "canvas_id": a["id"], "label": a["name"],
                              "was_faculty": a["id"] in once["assignment"] and a["id"] not in still["assignment"], "default": False,
                              "graded": a["graded"]})
        for page in course.pages:
            if page["id"] not in claimed["page"]:
                extra.append({"key": f"page:{page['id']}", "kind": "page", "canvas_id": page["id"], "label": page["title"],
                              "was_faculty": page["id"] in once["page"] and page["id"] not in still["page"], "default": False})
        removing = chosen.get("remove")
        removing = {e["key"] for e in extra if e["default"]} if removing is None else set(removing) & {e["key"] for e in extra}
        for entry in extra:
            entry["remove"] = entry["key"] in removing
            if entry["remove"]:
                ops.append({"op": f"delete_{entry['kind']}", "canvas_id": entry["canvas_id"], "label": entry["label"]})
        # Order: Faculty's modules first in its order, then the rest; each managed module's items in Faculty's order.
        removed_modules = {e["canvas_id"] for e in extra if e["remove"] and e["kind"] == "module"}
        current_order = [m["id"] for m in course.modules if m["id"] not in removed_modules]
        desired = [f"module:{m['id']}" for m, _ in managed]
        rest = [f"canvas:{mid}" for mid in current_order if mid not in managed_ids]
        existing = {canvas_id: f"module:{m['id']}" for m, canvas_id in managed if canvas_id}
        simulated = [existing.get(mid, f"canvas:{mid}") for mid in current_order] + [f"module:{m['id']}" for m, canvas_id in managed if not canvas_id]
        moves = _placements(simulated, desired + rest)
        if moves:
            ops.append({"op": "order_modules", "order": [m["id"] for m, _ in managed], "moves": moves})
        removed_items = {e["canvas_id"] for e in extra if e["remove"] and e["kind"] == "item"}
        for module, module_canvas in managed:
            wanted = [i for i in module["items"] if applies(i, sections) and not any(b["key"] == f"item:{i['id']}" for b in blocked)]
            present = []
            if module_canvas:
                for item in course.module_by_id[module_canvas]["items"]:
                    if item["id"] in removed_items:
                        continue
                    key = next((k for k, v in found["item"].items() if v == item["id"]), None)
                    replaced = any(o["op"] in ("replace_item", "move_item") and o["key"] == key for o in ops)
                    if key and not replaced and any(i["id"] == key for i in wanted):
                        present.append(f"item:{key}")
                    elif not (key and replaced):
                        present.append(f"canvas:{item['id']}")
            arriving = [f"item:{i['id']}" for i in wanted if f"item:{i['id']}" not in present]
            desired_items = [f"item:{i['id']}" for i in wanted] + [p for p in present if p.startswith("canvas:")]
            moves = _placements(present + arriving, desired_items)
            if moves:
                ops.append({"op": "order_items", "key": module["id"], "label": module["name"], "order": [i["id"] for i in wanted], "moves": moves})
        counts = {}
        for op in ops:
            counts[op["op"]] = counts.get(op["op"], 0) + 1
        attention = [e for e in entries if e["verdict"] in ("differs", "edited_in_canvas", "conflict", "elsewhere")]
        # What Canvas holds that nothing of Faculty's copy was matched to: what "Same as…" offers for a new object.
        unclaimed = {"module": [{"canvas_id": m["id"], "label": m["name"]} for m in course.modules if m["id"] not in claimed["module"]],
                     "item": [{"canvas_id": i["id"], "label": f"{m['name']} › {i['title']}", "type": i["type"]}
                              for m in course.modules for i in m["items"] if i["id"] not in claimed["item"]],
                     "page": [{"canvas_id": p["id"], "label": p["title"]} for p in course.pages if p["id"] not in claimed["page"]],
                     "assignment": [{"canvas_id": a["id"], "label": a["name"]} for a in course.assignments
                                    if a["id"] not in claimed["assignment"] and not FOREIGN & set(a["submission_types"])]}
        result[cid] = {"course_id": cid, "ops": ops, "entries": entries, "attention": attention, "blocked": blocked, "extra": extra,
                       "counts": counts, "sections": sections, "unclaimed": unclaimed}
    return result


def size_text(size):
    """A file's size as a person reads it ("840 KB", "2.4 MB")."""
    size = int(size or 0)
    if size < 1024:
        return f"{size} bytes"
    if size < 1024 * 1024:
        return f"{size / 1024:.0f} KB"
    return f"{size / (1024 * 1024):.1f} MB"


def _files_plan(modules, course, sections, kept, receipts, ticked):
    """FILES: what a push does about the files Faculty keeps for the File items of `modules` (the copy's modules that reach this
    course). Returns (ops, entries, arriving, held): `upload_file` operations, their entries (new, update, or one that waits for
    the instructor's Replace), the folder-and-name paths this push puts in the course for the first time (their items are written
    after them) and {item id: why it is held back}.

    A file is the course's file of its folder and name. Missing: it is put there. There already: left alone when it is the file
    Faculty put there (`receipts`) or one of the same size; replaced without asking only when Canvas still holds the file Faculty
    put there and Faculty's has changed since; anything else is listed, and replaced only when ticked. Faculty never deletes a
    file from Canvas."""
    ops, entries, arriving, held, wanted = [], [], set(), {}, {}
    for module in modules:
        for item in module["items"]:
            key = (item.get("content") or {}).get("file")
            if not key or item["type"] != "File" or not applies(item, sections):
                continue
            first = wanted.setdefault(item["content"]["name"], (key, []))
            if first[0] != key:
                held[item["id"]] = "another item puts a different file of this name in the same folder"
            else:
                first[1].append(item)
    for path, (key, items) in wanted.items():
        meta, at = kept.get(key), course.file_by_path.get(path)
        if meta is None:
            held.update({item["id"]: "Faculty no longer keeps its file: add the file again" for item in items})
            continue
        if at is _MANY:
            continue  # more than one file of that folder and name: each item says so (Course.resolve)
        label = f"File {path.removeprefix(FILES_ROOT + '/')}"
        object_key = f"file:{key}"
        op = {"op": "upload_file", "key": key, "path": path, "label": label, "sha256": meta["sha256"], "size": meta["size"], "replace": at is not None}
        if at is None:
            # Unpublished from the moment it arrives unless an item shows it published (its item publishes it later, as in Canvas).
            ops.append({**op, "locked": not any(item["published"] for item in items)})
            entries.append({"key": object_key, "kind": "file", "label": label, "canvas_id": "", "verdict": "new", "write": True, "how": "", "ticked": False})
            arriving.add(path)
            continue
        receipt = receipts.get(path)
        mine = bool(receipt and receipt["canvas_file_id"] == at)
        size = (course.file_info.get(at) or {}).get("size")
        if (mine and receipt["sha256"] == meta["sha256"]) or (not mine and size == meta["size"]):
            continue
        verdict = "update" if mine else "edited_in_canvas" if receipt else "differs"
        entry = {"key": object_key, "kind": "file", "label": label, "canvas_id": at, "verdict": verdict,
                 "write": mine or ticked(object_key), "how": "", "ticked": ticked(object_key)}
        if not mine:
            entry["differences"] = [{"field": "size", "faculty": size_text(meta["size"]), "canvas": size_text(size), "truncated": False}]
            entry["comparison_sha256"] = digest([meta["sha256"], at, size])
        entries.append(entry)
        if entry["write"]:
            ops.append(op)
    return ops, entries, arriving, held


def _published_since(desired, current):
    """Whether the only difference is that Canvas has published what the copy still holds unpublished."""
    return desired is not None and current["published"] and not desired["published"] and _equal("assignment", {**desired, "published": True}, current)


def _relinked(entry, body, cid, courses, found_all, pending):
    """Whether a page or assignment Canvas holds as Faculty means it (verdict "same") still has a link waiting for a page or
    assignment this push makes here: it is written then, and its links once that exists (the push's relink). Canvas otherwise
    kept it pointing at another section's course for good, when what it links to could not be made before (2026-10-04 review)."""
    if entry["verdict"] != "same" or not pending or entry.get("published_in_canvas"):
        return False  # a write would send the copy's stale "unpublished" (PUBLISHED IN CANVAS STANDS); the morning's follow lets it go
    if not render(body, cid, courses, found_all, frozenset())[1]:
        return False
    entry["verdict"], entry["write"] = "update", True
    return True


def _made_here(content, cid, courses, found_all, sections, found):
    """The plan keys of the assignments and pages this push makes in course `cid`: those not in it yet, less those held back there.
    A link to one of them is written once it exists; a link to one held back is a reason to hold back the linking page or assignment
    too. Holding one back can hold back another that links to it, so this is settled by repetition (2026-10-04 review: a page linking
    to a page held back was made with its link still pointing at another section's course, and never repaired)."""
    assignments = [a for a in content.get("assignments", []) if applies(a, sections) and not found["assignment"].get(a["id"])]
    pages = [p for p in content["pages"] if applies(p, sections) and not found["page"].get(p["id"])]
    made = {a["id"] for a in assignments} | {p["id"] for p in pages}
    while True:
        held = {a["id"] for a in assignments if a["id"] in made and
                (not a["group"] or due_for(a, sections)[1] or render(a["description"], cid, courses, found_all, made)[1])}
        held |= {p["id"] for p in pages if p["id"] in made and render(p["body"], cid, courses, found_all, made)[1]}
        if not held:
            return made
        made -= held


def due_for(assignment, sections):
    """The due time for a course serving `sections`: the sections' own date when they have one (all alike), else the default."""
    own = {assignment["section_due"][s] for s in sections if s in assignment.get("section_due", {})}
    if len(own) > 1:
        return None, "the sections sharing this Canvas course have different due dates (Faculty does not set per-section dates in one course)"
    if own and len(own) == 1 and len([s for s in sections if s in assignment["section_due"]]) < len(sections):
        if assignment["due_at"] not in own:
            return None, "only some sections sharing this Canvas course have their own due date"
    return (next(iter(own)) if own else assignment["due_at"]), ""


def assignment_canvas_state(a, groups):
    external = is_external(a)
    return {"name": a["name"], "description": _squash(a["description"]), "points": points_text(a["points"]), "grading_type": a["grading_type"],
            "submission_types": None if external else sorted(a["submission_types"] or ["none"]), "group": groups.get(a["group_id"], ""),
            "due_at": instant(a["due_at"]), "unlock_at": instant(a["unlock_at"]), "lock_at": instant(a["lock_at"]),
            "published": a["published"], "omit_from_final_grade": a["omit_from_final_grade"]}


def assignment_plan_canvas(a, due, description):
    return {"name": a["name"], "description": _squash(description), "points": a["points"], "grading_type": a["grading_type"],
            "submission_types": None if a["external"] else a["submission_types"], "group": a["group"], "due_at": due,
            "unlock_at": a["unlock_at"], "lock_at": a["lock_at"], "published": a["published"], "omit_from_final_grade": a["omit_from_final_grade"]}


def assignment_plan_state(a):
    return {k: a[k] for k in ("name", "description", "points", "grading_type", "submission_types", "external", "group", "due_at",
                              "section_due", "unlock_at", "lock_at", "published", "omit_from_final_grade")}


def _placements(current, desired):
    """How many positions change when `current` (a list of keys, new ones appended) is put in `desired` order by placing each
    in turn: the number of placements a push makes."""
    order = list(current)
    count = 0
    for index, key in enumerate(desired):
        if key not in order:
            continue
        if order.index(key) != index:
            order.remove(key)
            order.insert(index, key)
            count += 1
    return count


PENDING = "pending"


def _desired_item(item, target, page_urls):
    if target == PENDING:
        return None
    state = {"type": item["type"], "title": item["title"], "indent": item["indent"], "published": item["published"]}
    if item["type"] in ("ExternalUrl", "ExternalTool"):
        state.update(external_url=item["external_url"], new_tab=item["new_tab"])
    if item["type"] == "Page":
        url = page_urls.get(item["page"])
        if url is None:
            return None  # its page is created by this push: the item is written after it
        state["page_url"] = url
    if item["type"] in NAMED or item["type"] in ("File", "ExternalTool"):
        state["content_id"] = target
    return state


def _trimmed(state):
    """A state with its title or name trimmed: Faculty keeps titles without spaces at their ends (normalize), and a title that
    differs from Canvas's only by those spaces is the same title (requirement, 2026-09-30: several of the sections' titles end in
    a space)."""
    return {k: (v.strip() if k in ("title", "name") and isinstance(v, str) else v) for k, v in state.items()}


def _equal(kind, desired, current):
    desired, current = _trimmed(desired), _trimmed(current)
    if kind == "page":
        return desired["title"] == current["title"] and desired["published"] == current["published"] \
            and desired["front_page"] == current["front_page"] and same_html(desired["body"], current["body"])
    return desired == current


def _changed(desired, current):
    return {key: value for key, value in (desired or {}).items() if current.get(key) != value}


def fingerprint(revision, plans, choices):
    """What the instructor approves: the copy's revision, their choices and every operation of every course."""
    return digest({"rule": RULE, "revision": revision, "choices": choices,
                   "plans": {cid: {"ops": p["ops"], "blocked": p["blocked"], "extra": [(e["key"], e["remove"]) for e in p["extra"]],
                                   "attention": [(e["key"], e["verdict"], e["canvas_id"], e.get('comparison_sha256')) for e in p["attention"]]}
                             for cid, p in sorted(plans.items())}})
