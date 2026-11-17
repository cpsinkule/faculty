"""The assistant's tools (AGENTS.md "Faculty assistant"): one registry, rendered for MCP clients and OpenAI-style tool calling.

Every engine and every outside MCP client ends here, in the web process: `AssistantService.run_tool`. Reads go through the app's
own GET routes (the JSON the pages read); writes go through `AssistantService.write`, which applies `policy`. Repository
documents and source can be read so the assistant can learn what a route expects; the catalog can be queried read-only.
"""
from __future__ import annotations

import json
import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlencode

from rosters import connect

ROOT = Path(__file__).resolve().parent.parent
SOURCE_SUFFIXES = {".py", ".js", ".mjs", ".md", ".sql", ".txt", ".css", ".json", ".ts", ".html"}
SQL_ROWS, SQL_SECONDS = 500, 5.0


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    parameters: dict
    handler: object
    label: object = None  # (args) -> short words for the panel ("Read gradebook")
    read: bool = False  # only reads; ASSISTANT "Selected-area conversations" runs no other tool in the first analysis
    source: object = None  # (args) -> the one GET route this tool reads, which faculty_get can page; None for a composed answer


def _schema(properties=None, required=()):
    return {"type": "object", "properties": properties or {}, "required": list(required), "additionalProperties": False}


S = {"type": "string"}
I = {"type": "integer"}


def _get(path_template, query_keys=()):
    def handler(context, args):
        path = path_template.format(**{k: str(v) for k, v in args.items()})
        query = {key: args.get(key) for key in query_keys}
        return context.get(path, query)
    return handler


def _route(path_template, query_keys=()):
    """The GET path a curated read reads, for the partial-result note (COMPLETE READS)."""
    def source(args):
        try:
            path = path_template.format(**{k: str(v) for k, v in args.items()})
        except (KeyError, IndexError, ValueError):
            return None
        query = {key: str(args[key]) for key in query_keys if args.get(key) not in (None, "")}
        return path + (("&" if "?" in path else "?") + urlencode(query) if query else "")
    return source


# name, description, path, required path params, optional query params
READS = [
    ("workspace_overview", "What needs the instructor now (attention cards: Inbox, grading, seating, Response Cards, queues), work "
     "in progress and what is coming up. Start here for 'what should I do' questions.", "/api/workspace/overview", (), ()),
    ("courses", "Every course offering, its course code, term and sections, with the course group IDs (group_id) other reads "
     "need.", "/api/state?include=core", (), ()),
    ("calendar", "Calendar events from start to end (YYYY-MM-DD): exams, deadlines, Faculty's own events, DR bookings and Google "
     "events.", "/api/calendar", (), ("start", "end")),
    ("class_schedule", "One section's class schedule (section_id from find_student or gradebook_terms): each day's class meeting, "
     "what it covers (notes sections), and the work given or due that day with its time and place. Without start and end it "
     "answers two weeks back to four ahead; send both (YYYY-MM-DD) for other dates. `planned` is a date the instructor planned "
     "for work not created yet. `differs` lists records that disagree about a date: report both dates and settle neither.",
     "/api/scheduling/sections/{section_id}/class-schedule", ("section_id",), ("start", "end")),
    ("dr_quizzes", "A term's upcoming class-schedule quizzes to preschedule with the DR center, for the sections with DR students "
     "(term_id from gradebook_terms), each compared with the imported DR bookings (aim.status not_in_aim, in_aim or aim_differs). "
     "Read only.", "/api/scheduling/terms/{term_id}/dr-quizzes", ("term_id",), ()),
    ("section_roster", "A section's roster: students with names, UIN, email, accommodations, dropped students. group_id from "
     "courses; number is the section number.", "/api/course-groups/{group_id}/sections/{number}/roster", ("group_id", "number"), ()),
    ("ta_contacts", "A section's TA contacts (section_id is the permanent section ID).", "/api/teaching-assistants/sections/{section_id}",
     ("section_id",), ()),
    ("gradebook_terms", "The terms that have gradebooks, with their term IDs.", "/api/gradebook", (), ()),
    ("gradebook", "A term's gradebook: setup (categories, columns, points, due dates), students' values and totals. Pass "
     "section (a section ID) for one section's students.", "/api/gradebook/terms/{term_id}", ("term_id",), ("section",)),
    ("grading_overview", "Every assignment under grading with its stage, progress and what waits (assignment IDs = aid).",
     "/api/grading?view=overview&health=0", (), ()),
    ("grading_assignment", "One assignment's grading plan: questions, versions, rubric state, batches, workflow and progress.",
     "/api/grading/assignments/{aid}", ("aid",), ()),
    ("review_queue", "The responses waiting for the instructor's review for one assignment, by version, question and proposed "
     "lines; failed-to-grade counts.", "/api/grading/assignments/{aid}/review-queue", ("aid",), ("question_id", "job_id")),
    ("certification", "Certification and Export for one assignment: every student's grade state, flags, questions, Gradescope "
     "coverage and certification.", "/api/grading/assignments/{aid}/certification", ("aid",), ()),
    ("inbox", "Teaching Inbox submissions and scans. status: current (default), completed, cancelled, failed, needs_review.",
     "/api/inbox/jobs?files=input", (), ("status", "q")),
    ("inbox_job", "One Inbox submission or scan with its flow graph, steps and history.", "/api/inbox/jobs/{job_id}", ("job_id",), ()),
    ("exams", "Exam records of the current terms.", "/api/exams", (), ()),
    ("exam_overview", "One exam's hub: its eight stages (paper, seating, notices, print, exam day, collected, grading, recorded) "
     "and next step.", "/api/exams/{event_id}/overview", ("event_id",), ()),
    ("seating_charts", "Seating charts with rooms, counts, card and seat-update states.", "/api/seating", (), ()),
    ("seating_chart", "One seating chart: seats, students, versions, card states and issues.", "/api/seating/{chart_id}", ("chart_id",), ()),
    ("response_cards", "Response Card issues and what needs attention.", "/api/response-cards/attention", (), ()),
    ("accommodations", "Accommodations and DR-center exam bookings across current terms.", "/api/accommodations/overview", (), ()),
    ("assignments", "Assignments & printing: every assignment with its lifecycle stages and print jobs.", "/api/documents/catalog", (), ()),
    ("quiz_drafts", "Quizzes drafted by a model, with their revisions and approvals.", "/api/quiz-drafts", (), ()),
    ("mail_threads", "The instructor's Gmail: tab students (default), sent or all; q is a Gmail search.", "/api/mail/threads",
     (), ("tab", "q")),
    ("mail_thread", "One Gmail thread with its messages.", "/api/mail/threads/{thread_id}", ("thread_id",), ()),
]


def _read_tools():
    tools = []
    for name, description, path, required, optional in READS:
        properties = {key: (I if key == "number" else S) for key in required}
        properties.update({key: S for key in optional})
        tools.append(Tool(name, description, _schema(properties, required), _get(path, optional),
                          label=lambda args, n=name: "Read " + n.replace("_", " "), read=True, source=_route(path, optional)))
    return tools


# Generic access ---------------------------------------------------------------------------------------------------------

def _faculty_get(context, args):
    from .transport import narrow
    found = context.get(args["path"], args.get("query") or {})
    found = narrow(found, args.get("find")) if args.get("find") else found
    if any(key in args for key in ('pointer', 'offset', 'limit', 'fingerprint')):
        from .pagination import page
        return page(found, args)
    return found


# One student ----------------------------------------------------------------------------------------------------------------

def _terms(context):
    status, data, _ = context.service.transport.request("GET", "/api/gradebook")
    terms = (data or {}).get("terms", []) if status == 200 else []
    return [term for term in terms if term.get("is_current")] or terms


def _gradebook(context, term_id, section_id=None):
    path = f"/api/gradebook/terms/{term_id}" + (f"?section={section_id}" if section_id else "")
    status, view, _ = context.service.transport.request("GET", path)
    if status != 200 or not isinstance(view, dict):
        raise ValueError((view or {}).get("error") if isinstance(view, dict) else "The gradebook could not be read.")
    return view


def _find_student(context, args):
    """Read verified roster projections; never compute a term's grades to find a name."""
    from seating import student_name
    query = " ".join(str(args.get("query") or "").split())
    if len(query) < 2:
        raise ValueError("Give at least two letters of a name, an email or four or more UIN digits.")
    words = [w for w in re.split(r"[\s,]+", query.casefold()) if w]
    digits = re.sub(r"\D", "", query)
    by_contact = "@" in query or len(digits) >= 4
    terms = {t['id']: t for t in _terms(context)}
    directory = context.get('/api/absences/students')
    if directory.get('error'):
        return directory
    matches = []
    for group in directory.get('groups', []):
        term = terms.get(group['term_id'], {})
        group_id = term.get('group_id')
        students = group['students']
        if by_contact:
            students = []
            for section in term.get('sections', []):
                roster = context.get(f"/api/course-groups/{group_id}/sections/{section['number']}/roster")
                if roster.get('error'):
                    return roster
                for record in roster.get('students', []):
                    text = json.dumps(record, ensure_ascii=False, default=str).casefold()
                    uin = str(record.get('uin') or record.get('fields', {}).get('uin') or '')
                    if ('@' in query and query.casefold() in text) or (len(digits) >= 4 and digits in uin):
                        students.append({'name': student_name(record), 'enrollment_id': record['id'],
                                         'section_id': section['id'], 'section': section['number'], 'dropped': record.get('dropped', False)})
        for student in students:
            if not by_contact and not all(w in str(student['name']).casefold() for w in words):
                continue
            matches.append({'name': student['name'], 'course': group['code'], 'term': group['term_label'],
                            'term_id': group['term_id'], 'group_id': group_id, 'section': student['section'],
                            'section_id': student['section_id'], 'row': student['section_id'] + ':' + student['enrollment_id'],
                            'enrollment_id': student['enrollment_id'], 'dropped': student['dropped'],
                            'page': f"#course/{group_id}/section/{student['section']}"})
    return {'matches': matches[:25], 'count': len(matches),
            'note': '' if matches else 'No current student matches. Try part of the family name, or an email.'}


def _student_grades(context, args):
    """One student's gradebook row: each column's value and the overall and category results, as the section's gradebook shows them."""
    term_id, row = args.get("term_id"), args.get("row")
    view = _gradebook(context, term_id, args.get("section_id"))
    students = view.get("students", [])
    student = next((s for s in students if s.get("row") == row), None) if row else None
    if student is None and args.get("name"):
        words = [w for w in str(args["name"]).casefold().split() if w]
        found = [s for s in students if all(w in str(s.get("name") or "").casefold() for w in words)]
        if len(found) > 1:
            return {"error": "More than one student matches.", "matches": [{"name": s["name"], "row": s["row"], "section": s["section"]} for s in found[:20]]}
        student = found[0] if found else None
    if student is None:
        raise ValueError("Name the student by row (from find_student) or by name.")
    setup, cells = view.get("setup", {}), view.get("cells", {})
    categories = {c.get("id"): c for c in setup.get("categories", [])}
    # GRADEBOOK "ZERO ONCE DUE": what the results do with an empty cell (counted as 0, or why it waits).
    blanks = ((view.get("results") or {}).get("blanks") or {}).get(student["row"]) or {}
    words = {"zero": "counts as 0 (past due, its grading certified, or its grades posted in Canvas)", "makeup": "not counted yet: makeup owed",
             "if_excused": "not counted yet: an absence waits for the instructor's decision", "dr": "not counted yet: DR-center booking",
             "paper": "not counted yet: paper in, not graded"}
    columns = []
    for item in setup.get("items", []):
        if item.get("archived") or (item.get("sections") and student["section_id"] not in item["sections"]):
            continue
        cell = (cells.get(item["id"]) or {}).get(student["row"])
        columns.append({"column": item.get("title"), "category": (categories.get(item.get("category_id")) or {}).get("name"),
                        "points_possible": item.get("points"), "due_date": item.get("due_date") or None,
                        "value": {k: cell.get(k) for k in ("status", "points", "note", "source") if cell.get(k) not in (None, "")} if cell else None,
                        **({"empty_cell": words.get(blanks[item["id"]], blanks[item["id"]])} if not cell and item["id"] in blanks else {})})
    result = ((view.get("results") or {}).get("students") or {}).get(student["row"])
    return {"student": {k: student.get(k) for k in ("name", "section", "row", "dropped", "accommodations")},
            "overall": result, "mode": setup.get("mode"), "categories": [{"name": c.get("name"), "weight": c.get("weight"), "drop_lowest": c.get("drop_lowest")}
                                                                          for c in setup.get("categories", [])],
            "columns": columns, "empty_cells": setup.get("ungraded")}


# Files and papers (requirement, 2026-09-30) ------

def _fetch_files(context, args):
    from . import files
    answer, shown, pictures = files.fetch(context.service.transport, args)
    context.files, context.pictures = shown, pictures
    return answer


def _one_student(context, value):
    """(section_id, enrollment_id, found) of one student: a gradebook row ("section:enrollment", from find_student) or words
    find_student matches exactly one student by; else an answer naming the matches."""
    from gradebook.common import ROW, split_row
    value = str(value or "").strip()
    if ROW.fullmatch(value):
        section_id, enrollment_id = split_row(value)
        with connect(context.service.database) as db:
            row = db.execute("SELECT term_id FROM course_sections WHERE id=?", (section_id,)).fetchone()
        if not row:
            raise ValueError("No such section.")
        return section_id, enrollment_id, {"row": value, "term_id": row["term_id"]}
    found = _find_student(context, {"query": value})
    if len(found["matches"]) != 1:
        return None, None, {"error": "Name one student: " + ("more than one matches." if found["matches"] else "none matches."),
                            "matches": found["matches"][:15]}
    match = found["matches"][0]
    section_id, enrollment_id = split_row(match["row"])
    return section_id, enrollment_id, match


def _assignment_of(context, term_id, value):
    """One assignment of the term's course by its ID, grading plan (aid), exam record, or title words ("exam 1" finds "Exam 1" and
    "Test 1"); with its exam record and grading plan in that term."""
    from exam_links import canonical
    value = " ".join(str(value or "").split())
    if not value:
        raise ValueError("Name the assignment (its title, or an ID).")
    with connect(context.service.database) as db:
        rows = [dict(r) for r in db.execute("""SELECT a.id,a.title,e.id event_id,f.id aid FROM course_terms t
            JOIN assignments a ON a.definition_id=t.definition_id LEFT JOIN exam_events e ON e.assignment_id=a.id AND e.term_id=t.id
            LEFT JOIN frq_assignments f ON f.event_id=e.id WHERE t.id=? ORDER BY a.created_at,a.id""", (term_id,))]
    exact = [r for r in rows if value in (r["id"], r["aid"], r["event_id"]) or r["title"].casefold() == value.casefold()]
    same = [r for r in rows if canonical(r["title"]) == canonical(value)]
    words = [w for w in value.casefold().split() if w]
    worded = [r for r in rows if all(w in r["title"].casefold() for w in words)]
    for chosen in (exact, same, worded):
        if len(chosen) == 1:
            return chosen[0]
    listed = exact or same or worded
    return {"error": "Name one assignment: " + ("more than one matches." if listed else "none of this course matches."),
            "assignments": [r["title"] for r in (listed or rows)][:40]}


def _transcript(result):
    lines = []
    for line in ((result or {}).get("transcript") or {}).get("lines") or []:
        words = [("[crossed out: %s]" if el.get("state") == "crossed_out" else "%s") % (el.get("latex") or el.get("text") or "")
                 for el in line.get("elements") or []]
        if words:
            lines.append(" ".join(words))
    return lines


def _student_paper(context, args):
    """One student's paper of one assignment: its scanned pages (image routes), and where it is graded, each question's grade; with
    question, that question's answer crops and the reader's transcript. Nothing is shown until fetch_files."""
    section_id, enrollment_id, student = _one_student(context, args.get("student"))
    if section_id is None:
        return student
    assignment = _assignment_of(context, student["term_id"], args.get("assignment"))
    if "error" in assignment:
        return assignment
    papers = context.get("/api/documents/papers", {"enrollment_id": enrollment_id, "assignment_id": assignment["id"]})
    answer = {"student": {k: student.get(k) for k in ("name", "section", "row") if student.get(k)} or {"row": student["row"]},
              "assignment": {"title": assignment["title"], "assignment_id": assignment["id"], "aid": assignment["aid"],
                             "event_id": assignment["event_id"]},
              "papers": papers.get("papers", []) if isinstance(papers, dict) else [], "note": (papers or {}).get("note") or (papers or {}).get("error") or ""}
    if assignment["aid"]:
        aid = assignment["aid"]
        graded = context.get(f"/api/grading/assignments/{aid}/certification/students/{section_id}/{enrollment_id}")
        if isinstance(graded, dict) and not graded.get("error") and graded.get("questions") is not None:
            person = graded.get("student") or {}
            answer["student"].setdefault("name", person.get("name"))
            answer["grading"] = {"page": f"#grading/{aid}?tab=export", "exam_version": person.get("exam_version"),
                                 "paper_version": person.get("paper_version"), "collection": person.get("collection"),
                                 "free_response": (graded.get("parts") or {}).get("frq"), "bubble_sheet": (graded.get("parts") or {}).get("bubble"),
                                 "certified": {k: (graded.get("certified") or {}).get(k) for k in ("status", "earned", "possible", "created_at")} if graded.get("certified") else None,
                                 "questions": [{"question": q.get("printed_number") or q.get("title"), "title": q.get("title"), "version": q.get("version"),
                                                "score": q.get("score"), "max": q.get("max"), "state": q.get("state"), "decided_by": (q.get("decided") or {}).get("kind"),
                                                "flags": (q.get("flags") or {}).get("names") or [], "missing": q.get("missing_reason"),
                                                "response_id": q.get("response_id")} for q in graded["questions"]]}
            wanted = str(args.get("question") or "").strip().casefold()
            if wanted:
                chosen = [q for q in graded["questions"] if q.get("response_id") and wanted in (str(q.get("printed_number") or "").casefold(), str(q.get("title") or "").casefold())]
                chosen = chosen or [q for q in graded["questions"] if q.get("response_id") and wanted in str(q.get("title") or "").casefold()]
                answer["question_detail"] = []
                for q in chosen[:4]:
                    response = context.get(f"/api/grading/responses/{q['response_id']}")
                    if not isinstance(response, dict) or response.get("error"):
                        continue
                    hidden = bool(response.get("hidden"))  # a blind acceptance the instructor has not judged: its grade stays unseen
                    result = {} if hidden else response.get("review") or response.get("result") or {}
                    answer["question_detail"].append({
                        "question": q.get("printed_number") or q.get("title"), "response_id": q["response_id"],
                        "crops": [((c.get("images") or {}).get("original") or {}).get("url") for c in response.get("crops") or []],
                        "transcript": _transcript(response.get("result")),
                        "lines_ticked": [a.get("rubric_item_id") for a in result.get("applications") or [] if a.get("applied")],
                        "score": (result.get("score") or {}).get("score"), "grade_hidden_for_blind_review": hidden,
                        "review_page": f"#grading/{aid}?tab=review"})
                if not chosen:
                    answer["question_detail_note"] = "No graded question of this student matches that question."
        else:
            answer["grading"] = {"note": (graded or {}).get("error") or "This student has no grading record for this assignment."}
    answer["next"] = ("To show pages or crops, call fetch_files with their routes (a page's image, a paper's pdf, a question's crops); "
                      "add look=true to read them yourself.")
    return answer


def _find_pictures(context, args):
    """PICTURE-SEARCH: the pages and pictures nearest a description. A score ranks; it is never evidence of what one shows."""
    query = {"limit": str(min(max(int(args.get("limit") or 8), 1), 24))}
    if args.get("kinds"):
        query["sources"] = ",".join(str(kind).strip() for kind in str(args["kinds"]).split(",") if kind.strip())
    if args.get("like"):  # by a picture: a file Faculty serves (a file attached in this chat, a scanned page, a bank picture)
        found = context.get("/api/picture-search/like", {**query, "route": str(args["like"]), "page": str(int(args.get("page") or 1))})
    else:
        found = context.get("/api/picture-search", {**query, "q": str(args.get("query") or "")})
    if not isinstance(found, dict) or found.get("error") or "results" not in found:
        return {"error": (found.get("error") if isinstance(found, dict) else None) or "Picture search did not answer."}
    return {**({"like": str(args["like"])} if args.get("like") else {"query": found.get("query")}), "searched": found.get("searched"),
            "pictures": [{"kind": hit["kind"], "image": hit["image"], **({"pages": str(hit["pdf_page"])} if hit.get("pdf_page") else {}),
                          "label": hit["label"], "where": hit["where"], "page": hit["link"], "matched": hit["matched"], "score": hit["score"],
                          **({"scans": hit["scans"]["count"], "scans_page": hit["scans"]["link"]} if hit.get("scans") else {})}
                         for hit in found.get("results", [])],
            "next": "These are the nearest, not confirmed matches: `matched` words means Faculty's own text about it is near the "
                    "description, look means only that it resembles it. A printed page stands for its scanned copies (`scans`: how "
                    "many; `scans_page` opens the newest scan). To show them, call fetch_files with their image routes (one call "
                    "per PDF, with its `pages`); add look=true to check what one shows before you say it shows it."}


def _find_past_work(context, args):
    """PAST-WORK: the Assistant's recorded actions nearest a described situation. Candidates, never a verdict."""
    from .past_work import NEXT
    found = context.get("/api/assistant/past-work", {"q": str(args.get("situation") or ""), "limit": str(min(max(int(args.get("limit") or 5), 1), 10))})
    if not isinstance(found, dict) or found.get("error") or "results" not in found:
        return {"error": (found.get("error") if isinstance(found, dict) else None) or "Past work did not answer."}
    return {"situation": found.get("query"), "searched": found.get("searched"), "matched_by": found.get("matched_by"),
            **({"not_read_yet": found["not_read_yet"]} if found.get("not_read_yet") else {}),
            "past_work": found["results"], "next": NEXT}


def _faculty_write(context, args):
    return context.write(args["method"], args["path"], args.get("body") or {}, args.get("summary", ""), args.get("page"),
                         args.get("assumption"))


def _propose_code_change(context, args):
    body = {"request_id": "$new_request_id", "title": args.get("title"), "request": args.get("request"),
            "brief": args.get("brief") or "", "page": args.get("page") or ""}
    return context.write("POST", "/api/assistant/code-tasks", body, f"Change Faculty: {args.get('title')}", "")


def _list_actions(context, args):
    return context.service.route_catalog(args.get("filter", ""))


def _open_page(context, args):
    route = args["route"]
    if not isinstance(route, str) or not route.startswith("#") or len(route) > 500 or re.search(r"[\s<>\"']", route):
        raise ValueError("A page route starts with # (for example #gradebook or #grading/assignment/<aid>).")
    return {"link": route, "label": str(args.get("label") or "")[:120]}


# Documents and source ---------------------------------------------------------------------------------------------------

def _read_docs(context, args):
    from .documents import read
    return read(args)


def plain_source(root, name):
    """A regular file inside the repository, reached through no symbolic link (a link could point at credentials or live data)."""
    parts = Path(name).parts
    if not parts or ".." in parts or Path(name).is_absolute():
        return False
    path = Path(root)
    try:
        for part in parts:
            path = path / part
            if path.is_symlink():
                return False
        return path.is_file()
    except OSError:
        return False


def _source_path(context, path):
    files = context.service.source_files()
    clean = str(path or "").strip().lstrip("./")
    if clean not in files or not plain_source(ROOT, clean):
        raise ValueError("Name a tracked source file of the repository (see search_source).")
    return ROOT / clean


def _read_source(context, args):
    from .documents import source
    return source(_source_path(context, args["path"]), args)


def _search_source(context, args):
    if len(str(args.get("pattern") or "")) > 300:
        raise ValueError("Keep the pattern under 300 characters.")
    try:
        pattern = re.compile(args["pattern"])
    except re.error as error:
        raise ValueError(f"The pattern is not a regular expression: {error}") from None
    within = args.get("path_prefix") or ""
    hits, files = [], context.service.source_files()
    for name in files:
        if within and not name.startswith(within):
            continue
        if not plain_source(ROOT, name):
            continue
        try:
            text = (ROOT / name).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for number, line in enumerate(text.splitlines(), 1):
            if pattern.search(line):
                hits.append(f"{name}:{number}: {line.strip()[:240]}")
                if len(hits) >= 120:
                    return {"hits": hits, "note": "Stopped at 120 matches; narrow the pattern or path_prefix."}
    return {"hits": hits}


# The catalog, read-only -------------------------------------------------------------------------------------------------

def _readonly(path):
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
    db.execute("PRAGMA query_only=1")
    db.row_factory = sqlite3.Row

    def authorize(action, arg1, arg2, dbname, source):
        if action in (sqlite3.SQLITE_ATTACH, sqlite3.SQLITE_DETACH, sqlite3.SQLITE_PRAGMA):
            return sqlite3.SQLITE_DENY
        if action == sqlite3.SQLITE_READ and arg2 and _secret_column(arg2):
            return sqlite3.SQLITE_IGNORE  # credentials read as NULL
        if action in (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION, sqlite3.SQLITE_RECURSIVE):
            return sqlite3.SQLITE_OK
        return sqlite3.SQLITE_DENY
    db.set_authorizer(authorize)
    return db


def _secret_column(name):
    from .policy import SECRET_COLUMN
    return bool(SECRET_COLUMN.search(name))


def _catalog_schema(context, args):
    db = _readonly(context.service.database)
    try:
        table = args.get("table")
        db.set_authorizer(None)
        if table:
            row = db.execute("SELECT sql FROM sqlite_master WHERE name=? AND type IN ('table','view')", (table,)).fetchone()
            if not row:
                raise ValueError(f"No table or view {table}.")
            return {"table": table, "sql": row["sql"]}
        rows = db.execute("SELECT name,type FROM sqlite_master WHERE type IN ('table','view') AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()
        return {"tables": [f"{r['name']}{' (view)' if r['type'] == 'view' else ''}" for r in rows],
                "note": "Student names are not in the catalog: they are in the term roster (use section_roster or gradebook)."}
    finally:
        db.close()


def _catalog_sql(context, args):
    query = str(args.get("query") or "").strip()
    if not query or len(query) > 8000:
        raise ValueError("Send one SELECT statement.")
    limit = min(SQL_ROWS, max(1, int(args.get("limit") or 200)))
    db = _readonly(context.service.database)
    deadline = time.monotonic() + SQL_SECONDS
    db.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 20000)
    try:
        cursor = db.execute(query)
        columns = [d[0] for d in cursor.description or []]
        rows = cursor.fetchmany(limit + 1)
        more = len(rows) > limit
        return {"columns": columns, "rows": [list(r) for r in rows[:limit]], "truncated": more}
    except sqlite3.OperationalError as error:
        if "interrupted" in str(error):
            raise ValueError(f"The query ran longer than {SQL_SECONDS:.0f} seconds and was stopped.") from None
        raise ValueError(f"The query failed: {error}") from None
    except sqlite3.DatabaseError as error:
        raise ValueError(f"The query was refused: {error}") from None
    finally:
        db.close()


def registry():
    tools = _read_tools() + [
        Tool("faculty_get", "Read any GET route of Faculty's API (path starting /api/, optional query object). Use list_actions or "
             "the guide to find routes; curated reads above cover the common ones. find keeps only the list items that mention a name, "
             "title or ID. For complete long records, page a JSON pointer (e.g. /entries, /entries/0/body_text) with offset and limit. "
             "Keep the returned fingerprint on later pages; a changed source refuses continuation.",
             _schema({"path": S, "query": {"type": "object", "additionalProperties": {"type": "string"}}, "find": S,
                      "pointer": S, "offset": I, "limit": I, "fingerprint": S}, ("path",)),
             _faculty_get, label=lambda a: "Read " + str(a.get("path", ""))[:80], read=True,
             source=lambda a: str(a.get("path") or "") + ("?" + urlencode(a["query"]) if isinstance(a.get("query"), dict) and a["query"] else "") or None),
        Tool("find_student", "Find students of the current terms by name (any part, any order), email or UIN digits: course, term, "
             "section, and the IDs other reads need (term_id, row, enrollment_id, group_id). Use it before reading a roster or a gradebook "
             "for one student.",
             _schema({"query": S}, ("query",)), _find_student, label=lambda a: "Looked up " + str(a.get("query", ""))[:60], read=True),
        Tool("student_grades", "One student's grades: every gradebook column's value and the overall percent and letter, as the section's "
             "gradebook computes them. term_id and row from find_student (or name instead of row).",
             _schema({"term_id": S, "row": S, "name": S, "section_id": S}, ("term_id",)), _student_grades,
             label=lambda a: "Read one student's grades", read=True),
        Tool("fetch_files", "Fetch files Faculty serves and give them to the instructor: scanned pages and papers (from student_paper "
             "or a scan), grading crops, blank exam pages, print PDFs, lecture notes, mail attachments and seat-chart PDFs from the "
             "communications record, absence documentation, student photos. paths: routes starting /api/ that answer with a file (up "
             "to 12). show (default true): the instructor sees them in the chat, pictures inline, other files as links. look (default "
             "false): the pictures are also sent to you so you can read them (scanned pages, crops, attachments; a PDF's first pages, or "
             "pages like \"2-3\"), text files as text, and a Word (.docx) or Excel (.xlsx) file as its words. Look only when the answer depends on what is on the page; show when the "
             "instructor asks to see something. Photos are shown, never sent to you. caption: a few words above what is shown; labels: "
             "a name for each file in order (\"Page 1\", \"Question 3\").",
             _schema({"paths": {"type": "array", "items": S, "minItems": 1, "maxItems": 12}, "show": {"type": "boolean"},
                      "look": {"type": "boolean"}, "pages": S, "caption": S, "labels": {"type": "array", "items": S, "maxItems": 12}},
                     ("paths",)), _fetch_files,
             label=lambda a: ("Looked at " if a.get("look") and a.get("show") is False else "Fetched ") +
             (f"{len(a['paths'])} files" if isinstance(a.get("paths"), list) and len(a["paths"]) != 1 else "a file"), read=True),
        Tool("student_paper", "One student's paper of one assignment: its scanned pages (image routes for fetch_files), the reviewed "
             "paper's PDF, and when it is graded, every question's score and state. question (a printed number like \"3\" or title "
             "words) adds that question's answer crops and the reader's transcript. student: a row from find_student, or a name or "
             "email that matches one student. assignment: its title (\"exam 1\", \"quiz 2\") or an ID.",
             _schema({"student": S, "assignment": S, "question": S}, ("student", "assignment")), _student_paper,
             label=lambda a: "Found the paper: " + str(a.get("assignment", ""))[:60], read=True),
        Tool("find_pictures", "Find pages and pictures Faculty holds by describing them (\"systems of linear inequalities\", \"a supply "
             "and demand graph\", \"a page with a crossed-out matrix\"): the nearest, best first, each with its image route for "
             "fetch_files and the page that opens it. Kinds: template (the printed pages of quizzes and exams), bank (question bank "
             "pictures), notes (lecture notes pages), scan (scanned pages still in the Inbox), absence (absence documentation) and "
             "mail (attachments on messages students wrote); `kinds` narrows the search to some, comma-separated. Printed pages, bank "
             "pictures and notes are matched on Faculty's own text about them; a scanned page is found through the page it was "
             "printed from (one result a printed page, with its number of scans); only what Faculty has no words about (an "
             "unidentified scan, a student's file) is matched on how it looks, which cannot read handwriting. It never says "
             "whose a page is: for one student's paper use student_paper. Instead of words, `like` takes the route of a picture "
             "or PDF Faculty serves (a file attached in this chat, a scanned page, a bank picture; `page` for a PDF's page) and "
             "finds what looks like it, which is a much surer likeness than words: a photo of a quiz page finds that page's "
             "scans. A student ID photo is never searched by. A "
             "result is a likely match, never a confirmed one; look at it (fetch_files, look=true) before you say what it shows.",
             _schema({"query": S, "like": S, "page": I, "limit": I, "kinds": S}, ()), _find_pictures,
             label=lambda a: "Searched pictures like a file" if a.get("like") else "Searched pictures: " + str(a.get("query", ""))[:60], read=True),
        Tool("find_past_work", "Find what you did before in a situation like the one described (\"a student missed a quiz for a "
             "doctor's appointment\", \"a regrade request where the grade stayed\", \"an email to the whole class\"): your own "
             "recorded actions nearest it (replies sent, absences recorded, follow-ups, grade and calendar changes), gathered by "
             "student or mail thread, what was carried out first. Each says when, how it ended (`outcome`), what the request "
             "itself said (`words`) and the page that shows it. Use it when the instructor asks how something was handled "
             "before, or to do something the way it was done last time. Describe the situation, not a name: it is matched on "
             "meaning. It returns the nearest, never a confirmed precedent, and it cannot tell you that nothing like it "
             "happened: read the results and say so when none fits. A proposal the instructor declined is what they refused, "
             "never how it was handled.",
             _schema({"situation": S, "limit": I}, ("situation",)), _find_past_work,
             label=lambda a: "Looked for past work like: " + str(a.get("situation", ""))[:60], read=True),
        Tool("faculty_write", "Make a change through Faculty's API (method POST/PUT/PATCH/DELETE, path, JSON body). Previews and "
             "searches run at once. Changes in the instructor's chosen categories (calendar events, TA contacts, content drafts, "
             "gradebook columns and due dates, retrying failed work) are made at once. Everything else becomes an Approve card "
             "for the instructor: it is NOT done until they approve it, and you never send a confirmation field. The same change "
             "for several records (a grade change for each of many students): one card each, then ONE group_actions call with all "
             "of them, so the instructor approves them on one summary card. Write "
             "\"$new_request_id\" wherever a request_id is needed. summary: one sentence saying what this does and why. page: "
             "optional #route where the instructor can review it in its own page. assumption: when you chose one reading over "
             "others instead of asking, {text: what you assumed, alternatives: up to 3 other readings in a few words, never edits}; "
             "the card shows it with one-click alternatives.",
             _schema({"method": {"type": "string", "enum": ["POST", "PUT", "PATCH", "DELETE"]}, "path": S,
                      "body": {"type": "object"}, "summary": S, "page": S,
                      "assumption": {"type": "object", "properties": {"text": S, "alternatives": {"type": "array", "items": S}}}},
                     ("method", "path", "summary")),
             _faculty_write, label=lambda a: str(a.get("summary", ""))[:120] or "Change"),
        Tool("propose_code_change", "Hand a change to FACULTY ITSELF (its pages, features, rules or code) to a coding agent. Gather "
             "first: the page the instructor means (route, what it shows: read it), the source files that draw or decide it "
             "(search_source/read_source), and the AGENTS.md sections that govern it. title: a few words. request: the instructor's "
             "request in THEIR words. brief: what you found, in Markdown: the page and its data, the files and functions involved "
             "(paths with line numbers), the rules that apply, and what done looks like. Becomes an Approve card; the instructor's "
             "Approve starts the agent in its own copy, and nothing reaches Faculty until they Apply. Never write code yourself.",
             _schema({"title": S, "request": S, "brief": S, "page": S}, ("title", "request", "brief")), _propose_code_change,
             label=lambda a: "Proposed a code change: " + str(a.get("title", ""))[:100]),
        Tool("code_tasks", "The coding tasks (changes of Faculty itself) and how each is going; with task_id, one task's rounds, "
             "report, files and diff.", _schema({"task_id": S}),
             lambda context, args: context.get(f"/api/assistant/code-tasks/{args['task_id']}" if args.get("task_id") else "/api/assistant/code-tasks"),
             label=lambda a: "Read the coding tasks", read=True),
        Tool("list_actions", "Faculty's API routes (method, path, what the assistant may do with each). filter narrows by words "
             "in the path or name (for example 'gradebook' or 'seating').", _schema({"filter": S}), _list_actions,
             label=lambda a: "Looked up routes " + str(a.get("filter", "")), read=True),
        Tool("read_docs", "Faculty's documentation: without name, the list; with name (and optional section heading words), its "
             "text. Follow next_offset with the same name, section and fingerprint until null. Your primer (assistant/guide.md) is already in your instructions, whole: never read it here. AGENTS.md holds the instructor's rules for every feature.",
             _schema({"name": S, "section": S, "offset": I, "fingerprint": S}), _read_docs, label=lambda a: "Read " + str(a.get("name") or "the docs list"), read=True),
        Tool("search_source", "Search Faculty's source (regular expression; optional path_prefix like 'gradebook/'). Use it to find "
             "what a route's body must hold.", _schema({"pattern": S, "path_prefix": S}, ("pattern",)), _search_source,
             label=lambda a: "Searched the source", read=True),
        Tool("read_source", "Read lines of a Faculty source file (path, start, lines up to 400). Follow next_offset with the same range and fingerprint, then next_start. Positions describe only the returned text.",
             _schema({"path": S, "start": I, "lines": I, "offset": I, "fingerprint": S}, ("path",)), _read_source, label=lambda a: "Read " + str(a.get("path", "")), read=True),
        Tool("catalog_schema", "The catalog's tables, or one table's CREATE statement.", _schema({"table": S}), _catalog_schema,
             label=lambda a: "Read the catalog schema", read=True),
        Tool("catalog_sql", "One read-only SELECT on Faculty's catalog (SQLite; at most 500 rows, 5 seconds). The catalog holds IDs, "
             "states and records; student names are in the rosters (use section_roster/gradebook).",
             _schema({"query": S, "limit": I}, ("query",)), _catalog_sql, label=lambda a: "Queried the catalog", read=True),
        Tool("open_page", "Give the instructor a button that opens a Faculty page (route starting with #).",
             _schema({"route": S, "label": S}, ("route",)), _open_page, label=lambda a: "Link " + str(a.get("route", ""))),
    ]
    from .workspace_tools import workspace_tools
    tools.extend(workspace_tools())
    from .student_tools import student_tools
    tools.extend(student_tools())
    from .task_tools import task_tools
    tools.extend(task_tools())
    from .task_actions import task_actions
    tools.extend(task_actions())
    return {tool.name: tool for tool in tools}


def openai_tools(tools):
    return [{"type": "function", "function": {"name": t.name, "description": t.description, "parameters": t.parameters}}
            for t in tools.values()]


def mcp_tools(tools):
    return [{"name": t.name, "description": t.description, "inputSchema": t.parameters} for t in tools.values()]
