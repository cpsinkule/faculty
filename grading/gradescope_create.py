"""Creating the Gradescope assignment from Faculty (requirement, 2026-09-27).
AGENTS.md "Sending grades to Gradescope" is the rule book; `gradescope_web` is the transport; the sender's thread does the work.

Asked, the instructor chose EVERYTHING, in EVERY section at once: for a term grading plan of any kind (a quiz, homework, a worksheet,
an exam), in each section's own Gradescope course that has no assignment for it yet, Faculty makes

- one instructor-uploaded assignment per version (Gradescope's "Exam / Quiz" type: a fixed-length paper with a template, whatever
  the paper is), titled with the plan's title (and "Version A", "Version B" ... when there are versions), whose template is that
  version's confirmed blank (the same PDF its answer areas were found on, so the page counts agree with the papers);
- one question per Faculty question, in printed-number order, with its printed number in the title, its points and its confirmed
  answer areas as the question's boxes;
- each question's scoring (positive or negative) and that question's rubric lines, in Faculty's order, with Faculty's CURRENT
  wording and points (a deduction's points as Gradescope's positive weight under negative scoring). No groups, no comments;
- with several versions, the assignments grouped as one version set named with the plan's title (Gradescope names the versions A,
  B, C in that order, so Faculty's letters must run from A without a gap).

A PREVIEW (POST, writes nothing anywhere) reads Faculty and Gradescope and says per course what would be made or why not; the
instructor's CREATE names its fingerprint with a request ID and `confirmation: "create"` (all checked on the server), everything is
read again (409 when anything changed), and the approved creation is recorded with one unit per Gradescope course. The sender works
a unit one step at a time (make an assignment, save its outline, one question's rubric, group the versions), and EVERY step reads
Gradescope before it writes, so a step whose outcome was unknown is simply read again and never made twice. An assignment Faculty
finds under its title is Faculty's own only while it holds no submission and its template has Faculty's page count; an outline or
rubric that differs from what Faculty made stops the unit for the instructor (Retry after fixing it on Gradescope). Faculty never
deletes anything on Gradescope except the default item Gradescope puts on each new question.

A section already sent to a version set that lacks a version its papers are in (requirement, 2026-10-06: a makeup's Version E) is
an ADD unit instead: Faculty makes only the missing versions, as above, then saves them into that set after its own versions
through the set's Edit page (Gradescope letters versions by their order), and links the section to the set with all of them.
GRADESCOPE "A VERSION THE SET LACKS" holds its rules.

When a unit is done its receipt names, per Faculty question, the Gradescope question and the item each rubric line became
(`frq_gradescope_created_questions`), and each of its sections is linked to the new assignment (`frq_gradescope_targets`), so the next
send preview proposes it. Grades are then matched by that receipt (`map_created`), which is how drafted and adapted rubrics, which
never came from a Gradescope export, can be sent too; every other check of a grade is unchanged.

THE BUBBLE-SHEET EXAM TOO (requirement, 2026-10-26): a plan with an answer key of two or more versions also gets, in each section's course with no bubble-sheet exam
linked for it, a SHEETS unit: Gradescope's Bubble Sheet type named after the dialog's second name ("<title> - Multiple Choice" unless
changed), its versions added with the answer key editor's Add Version until it has Faculty's letters, then every version's key saved
at once and read back. Each step reads Gradescope before it writes, as above. A finished unit links its sections to the set
(`frq_gradescope_sheet_targets`), so the Bubble sheets page's send uses it. Only the instructor's Create makes it: never Faculty by
itself after certifying, nor for a makeup version. GRADESCOPE "THE BUBBLE-SHEET EXAM TOO" holds its rules.
"""
import json
import re
import sqlite3
from decimal import Decimal

from werkzeug.exceptions import Conflict, NotFound

from course_storage import timestamp
from rosters import connect

from . import gradescope_web as gw
from .models import digest, uid
from .rubrics import part_cap

CREATE_CONFIRMATION = "create"
CREATE_RULE = "gradescope-create-v1"
TITLE_LIMIT = 100
SHEET_VERSIONS = 5  # Edit Answer Key's own limit (`maxVersions`, read 2026-10-26)


class CreationRefused(Exception):
    """What Gradescope shows is not what Faculty made (or cannot be made): the unit stops for the instructor."""


class Processing(gw.GradescopeError):
    """Gradescope has the new template but is still reading it into pages (a read: nothing was sent)."""

    def __init__(self, message):
        super().__init__(message, outcome="unavailable")


def _amount(value):
    return Decimal(str(value)).quantize(Decimal("0.0001"))


def _text(value):
    return format(_amount(value).normalize(), "f")


def clean_title(value, fallback):
    title = " ".join(str(value if value not in (None, "") else fallback or "").split())
    if not title:
        raise ValueError("Name the Gradescope assignment.")
    if len(title) > TITLE_LIMIT or any(ord(c) < 32 for c in title):
        raise ValueError(f"Use a name of at most {TITLE_LIMIT} characters.")
    return title


def question_title(question):
    """The Gradescope question's title: Faculty's own title when it prints the question's number, else "Question N" (with the title
    after it). The number must be in it: a grade is sent only to the Gradescope question that prints Faculty's number."""
    content = question.get("content") or {}
    printed = str(content.get("printed_number") or "").strip()
    title = " ".join(str(content.get("title") or "").split())
    if printed and printed in re.findall(r"\d+", title):
        return title[:200]
    return (f"Question {printed}" + (f": {title}" if title and title != question.get("question_key") else ""))[:200]


def crop_rects(regions):
    """Faculty's answer areas (page fractions) as Gradescope's question boxes (page percentages, one decimal, as its editor rounds)."""
    rects = []
    for area in regions or []:
        r = area["region"]
        clip = lambda value: min(100.0, max(0.0, round(100 * value, 1)))  # noqa: E731
        rects.append({"page_number": int(area["page"]), "x1": clip(r["x"]), "x2": clip(r["x"] + r["width"]),
                      "y1": clip(r["y"]), "y2": clip(r["y"] + r["height"])})
    return rects


def line_weight(line, mode):
    """A Faculty line's points as Gradescope's item weight: the same number under positive scoring; under negative scoring Gradescope
    takes each checked item's weight FROM the question's worth, so a deduction of -2 is weight 2 (and a bonus of +1 is -1)."""
    points = _amount(line["points"])
    return -points if mode == "negative" else points


def same_rects(have, want):
    if len(have or []) != len(want):
        return False
    for a, b in zip(sorted(have, key=lambda r: (r["page_number"], r["y1"], r["x1"])), sorted(want, key=lambda r: (r["page_number"], r["y1"], r["x1"]))):
        if int(a["page_number"]) != b["page_number"] or any(abs(float(a[k]) - b[k]) > 0.051 for k in ("x1", "x2", "y1", "y2")):
            return False
    return True


def sheet_key(key):
    """Faculty's answer key as Gradescope's answer key editor holds it: per version name, each answered question's correct letters,
    scoring and points (a question's own, else the key's defaults)."""
    defaults = key["defaults"]
    return {f"Version {version['letter']}": {str(q["number"]): {"answer": list(q["answer"]), "scoring_type": q.get("scoring", defaults["scoring"]),
                                                                 "weight": float(_amount(q.get("points", defaults["points"])))}
                                             for q in version["questions"]} for version in key["versions"]}


def same_sheet_key(have, want):
    """Whether Gradescope holds exactly this key: the same questions, letters, scoring and points (2.5 and "2.5" alike)."""
    def plain(entries):
        return {str(n): ("".join(sorted(str(a).upper() for a in (e.get("answer") or []))), str(e.get("scoring_type") or ""),
                         _amount(e.get("weight") or 0)) for n, e in (entries or {}).items()}
    return plain(have) == plain(want)


def map_created(question, rubric, receipt):
    """(mapping, warnings) or (None, reason) for a question Faculty created on Gradescope: the question and the item each line became
    are the ones its receipt names, still worth what Faculty sent, still worded as Faculty's line is now or as it was sent, and the
    Gradescope question holds nothing else. Same shape as `gradescope_sync.map_question`."""
    target = next((q for q in rubric.get("questions", []) if str(q.get("id")) == receipt["gradescope_question_id"]), None)
    if not target:
        return None, "The Gradescope question Faculty created for it is gone."
    worth = (question.get("content") or {}).get("max_points")
    if worth is not None and _amount(target.get("weight") or 0) != _amount(worth):
        return None, f"It is worth {_text(worth)} in Faculty and {_text(target.get('weight') or 0)} on Gradescope."
    order = gw.export_order(rubric, target["id"])
    by_id = {i["id"]: i for i in order}
    made = json.loads(receipt["items_json"])
    mode = ((question.get("rubric") or {}).get("rules") or {}).get("mode", "negative")
    items, warnings = {}, []
    for line in question["rubric"]["rules"]["items"]:
        sent = made.get(line["id"])
        if not sent:
            return None, f"{line['id']} was added in Faculty after the Gradescope rubric was made; add it there too, or make the rubric again."
        item = by_id.get(str(sent["id"]))
        if not item:
            return None, f"The Gradescope item made for {line['id']} is gone."
        if item["points"] != _amount(line_weight(line, mode)):
            return None, f"{line['id']} is worth {_text(line['points'])} in Faculty and {_text(item['points'])} on Gradescope."
        # RENAMED ITEMS (GRADESCOPE): a wording the instructor recorded for this item, read on Gradescope, is known as well.
        recorded = (receipt.get("wordings") or {}).get(line["id"]) or ()
        known = {gw.normalized(sent.get("description")), gw.normalized(line["description"]), *map(gw.normalized, recorded)} - {""}
        if gw.normalized(item["name"]) not in known:
            return None, f"{line['id']} is “{item['name'][:80]}” on Gradescope, which is neither the wording Faculty sent nor its current wording."
        if gw.normalized(item["name"]) != gw.normalized(line["description"]):
            warnings.append(f"{line['id']} is worded differently in Faculty than on Gradescope (“{item['name'][:80]}”).")
        items[line["id"]] = item
    if len(order) != len(items):
        return None, f"Gradescope's question has {len(order)} rubric items and Faculty's rubric {len(items)}; the rubrics differ."
    return {"question": target, "items": items, "order": order}, warnings


class CreateMixin:
    # --- what Faculty would make -------------------------------------------------------------------------------------------
    def _faculty_assignment(self, aid):
        """The plan's versions as they would go to Gradescope, and what stops that. Catalog only, under the lifecycle lock. Each
        problem and warning names its version (None: the whole assignment), so adding one version to a set already on Gradescope
        is held up only by what that version lacks (2026-10-06)."""
        problems, warnings, versions = [], [], {}
        with self.rosters.lock(), connect(self.path) as db:
            plan = self.frq.plan(db, aid)
            questions = [self.frq.question(db, aid, r[0]) for r in db.execute(
                "SELECT question_id FROM frq_assignment_questions WHERE assignment_id=? ORDER BY rowid", (aid,))]
            for question in questions:
                letter = question["exam_version"] or ""
                label = question_title(question) + (f" (Version {letter})" if letter else "")
                rules = (question.get("rubric") or {}).get("rules")
                content = question["content"]
                if not rules or not rules.get("items"):
                    problems.append((letter, f"{label} has no rubric yet."))
                    continue
                if not str(content.get("printed_number") or "").strip():
                    problems.append((letter, f"{label} has no printed question number (set it in Answer areas)."))
                    continue
                status = self.frq.location_status(db, aid, question)
                template = status["context"]["template"]
                if status["status"] != "confirmed" or not content.get("regions") or not template:
                    problems.append((letter, f"{label}: confirm its answer areas first (Answer areas step)."))
                    continue
                entry = versions.setdefault(letter, {"template": template, "questions": []})
                if entry["template"]["id"] != template["id"]:
                    problems.append((letter, f"Version {letter or '(no version)'}'s questions are on different blank templates."))
                    continue
                mode = rules.get("mode", "negative")
                maximum = content["max_points"]
                if _amount(rules.get("floor", 0)) != 0 or _amount(rules.get("ceiling", maximum)) != _amount(maximum):
                    warnings.append((letter, f"{label}: Gradescope bounds a score by 0 and the question's points only; grades Faculty bounds "
                                             "otherwise may be refused when sending."))
                # A part's cap is no warning: what it gives back goes as the grade's point adjustment (GRADING "PART CAPS").
                if any(g.get("selection") == "single" or _amount(g.get("floor", 0)) not in (_amount(0), _amount(-maximum))
                       or _amount(g.get("ceiling", 0)) not in (_amount(0), _amount(maximum)) for g in rules.get("groups") or [] if not part_cap(g.get("id"))):
                    warnings.append((letter, f"{label}: its line groups become plain items on Gradescope; a grade Gradescope would score "
                                             "differently is refused when sending, never changed."))
                entry["questions"].append({
                    "question_id": question["question_id"], "rubric_id": question["rubric_id"], "title": question_title(question),
                    "printed": str(content["printed_number"]).strip(), "key": question["question_key"], "weight": _text(maximum),
                    "crops": crop_rects(content["regions"]), "scoring": "negative" if mode == "negative" else "positive",
                    "lines": [{"id": line["id"], "description": " ".join(str(line.get("description") or "").split()),
                               "weight": _text(line_weight(line, mode))} for line in rules["items"]]})
                if any(not line["description"] for line in entry["questions"][-1]["lines"]):
                    problems.append((letter, f"{label} has a rubric line without wording."))
            letters = sorted(versions)
            every = sorted({q["exam_version"] or "" for q in questions})  # with the versions a problem kept out of `versions`
            if not questions:
                problems.append((None, "This assignment has no questions yet."))
            elif len(every) > 1 and "" in every:
                problems.append((None, "Some questions have a version and others none."))
            elif len(every) > 1 and every != [chr(65 + i) for i in range(len(every))]:
                problems.append((None, f"Gradescope names versions A, B, C … in order; this assignment's versions are {', '.join(every)}."))
            for letter, entry in versions.items():
                entry["questions"].sort(key=lambda q: (int(q["printed"]) if q["printed"].isdigit() else 10**6, q["key"]))
                printed = [q["printed"] for q in entry["questions"]]
                if len(set(printed)) != len(printed):
                    problems.append((letter, f"Version {letter or '(no version)'} has two questions with the same printed number."))
                row = db.execute("SELECT manifest_sha256,page_count,label FROM assignment_templates WHERE id=?", (entry["template"]["id"],)).fetchone()
                entry["template"] = {"id": entry["template"]["id"], "pages": row["page_count"], "manifest": row["manifest_sha256"],
                                     "label": row["label"] or "blank"}
        return {"plan": plan, "letters": letters, "every": every, "versions": versions, "problems": problems, "warnings": warnings}

    @staticmethod
    def _for_letters(found, made):
        """The texts of the problems (or warnings) that concern the versions being made, and the whole assignment's."""
        return [text for letter, text in found if letter is None or letter in made]

    def _faculty_sheets(self, aid, title, data):
        """The bubble-sheet exam Faculty would make beside the written one (THE BUBBLE-SHEET EXAM TOO), from the plan's answer key,
        or None when the plan has no key. Catalog only, under the lifecycle lock: the key, the sections already linked to a
        bubble-sheet exam, and what stops it."""
        with self.rosters.lock(), connect(self.path) as db:
            plan = self.frq.plan(db, aid)
            key = self.frq.bubble_key(db, aid)
            linked = {t["section_id"] for t in self._sheet_targets(db, plan) if t["container_id"]}
        if not key or not any(v["questions"] for v in key["versions"]):
            return None
        sheet_title = clean_title(data.get("sheet_title"), f"{title} - Multiple Choice")
        letters = [v["letter"] for v in key["versions"]]
        problems = []
        if len(letters) < 2:
            # A bubble sheet without versions is listed by Gradescope like any assignment, so the Bubble sheets page could not find it.
            problems.append("Faculty makes a bubble-sheet exam with versions only; this answer key has one. Make it on Gradescope "
                            "(Bubble Sheet), then choose it on the Bubble sheets page.")
        elif len(letters) > SHEET_VERSIONS:
            problems.append(f"Gradescope's bubble sheets have at most {SHEET_VERSIONS} versions; this answer key has {len(letters)}.")
        problems += [f"Version {v['letter']} of the answer key has no answered question." for v in key["versions"] if not v["questions"]]
        return {"title": sheet_title, "letters": letters, "key": sheet_key(key), "revision": key["revision"], "linked": linked,
                "problems": problems,
                "versions": [{"letter": v["letter"], "title": f"{sheet_title} Version {v['letter']}", "questions": len(v["questions"]),
                              "points": format(sum((_amount(q.get("points", key["defaults"]["points"])) for q in v["questions"]), _amount(0)).normalize(), "f")}
                             for v in key["versions"]]}

    def _creation_plan(self, aid, data, makeup=None, session=None, linked_only=False):
        """`makeup` (a makeup letter; GRADESCOPE "A VERSION THE SET LACKS", 2026-10-09): only that version, added to the set every section
        has LINKED for the plan (whether or not its papers need it yet), never a new assignment or a course or set Faculty would only
        propose; a set must already hold every letter before it. `session`: the sender's own (its sign-in pauses hold); not closed here.
        `linked_only` (GRADESCOPE "AFTER CERTIFYING"): Faculty making the assignment by itself after a certification, only in a
        section's LINKED Gradescope course, never one it would only propose."""
        data = data if isinstance(data, dict) else {}
        faculty = self._faculty_assignment(aid)
        plan = faculty["plan"]
        title = clean_title(data.get("title"), plan.get("title"))
        letters = faculty["letters"]
        names = {letter: (f"{title} Version {letter}" if letter else title) for letter in faculty["every"]}
        # The bubble-sheet exam only on the instructor's own Create (never by itself after certifying, nor for a makeup version).
        sheets = None if makeup or linked_only else self._faculty_sheets(aid, title, data)

        def version_rows(made):
            return [{"letter": letter, "title": names[letter], "pages": faculty["versions"][letter]["template"]["pages"],
                     "questions": len(faculty["versions"][letter]["questions"]),
                     "lines": sum(len(q["lines"]) for q in faculty["versions"][letter]["questions"])} for letter in letters if letter in made]
        view = {"assignment_id": aid, **self.settings.status(), "title": title, "rule": CREATE_RULE, "courses": [], "sections": [],
                "versions": version_rows(set(letters)), "problems": [text for _, text in faculty["problems"]],
                "warnings": [text for _, text in faculty["warnings"]], "fingerprint": None, "ready": False, "running": None}
        with self.rosters.lock(), connect(self.path) as db:
            view["running"] = self._creating(db, aid)
            publishing = self._publishing(db, aid)
            saved = {t["section_id"]: t for t in self._targets(db, plan)}
            event = self.frq.exams._event(db, plan["event_id"])
            term = event.get("term_label") or ""
        if not view["configured"]:
            return {"view": view, "units": []}
        # The send's own imports (here: that module mixes this one in).
        from .gradescope_sync import containers, paper_letters, propose_course
        # A section already sent to a version set that lacks a version its papers are in gets that version added to the set
        # (requirement, 2026-10-06). Its papers are read only when some section already has a set.
        if makeup:  # every section with a linked set needs the makeup's version, papers or not ("all sections", 2026-10-09)
            needed = {sid: {makeup} for sid, t in saved.items() if t["container_id"]}
        else:
            needed = paper_letters(self._faculty(aid)["people"]) if any(t["container_id"] for t in saved.values()) else {}
        requested = data.get("targets") if isinstance(data.get("targets"), dict) else {}
        own, session = session is None, session or self.session_factory()
        units, extra = [], []
        try:
            courses = session.courses()
            by_id = {c["id"]: c for c in courses}
            listed = {}

            def assignments(course_id):
                if course_id not in listed:
                    listed[course_id] = session.assignments(course_id)
                return listed[course_id]

            def course_row(course_id, course, entry):
                return {"course_id": course_id, "course": gw.course_label(course),
                        "sections": sorted(s["section"] for s in view["sections"] if s["section_id"] in entry["sections"]), "problem": ""}
            wanted, adding, sheet_wanted = {}, {}, {}
            for target in saved.values():
                ask = requested.get(target["section_id"]) if isinstance(requested.get(target["section_id"]), dict) else {}
                entry = {"section_id": target["section_id"], "section": target["section"], "course_id": "", "course": "", "reason": ""}
                view["sections"].append(entry)
                if ask.get("leave_out") is True:
                    entry["reason"] = "Left out of this send."
                    continue
                course_id = str(ask.get("course_id") or target["course_id"] or "")
                if makeup and not (target["course_id"] and target["container_id"]):
                    entry["reason"] = "No Gradescope assignment is linked for this section yet; Faculty adds a version only to a linked one."
                    continue
                if not course_id and linked_only:
                    entry["reason"] = "This section's Gradescope course is not linked yet."
                    continue
                if not course_id:
                    proposed = propose_course(courses, target["section"], term)
                    course_id = proposed["id"] if proposed else ""
                chosen = ask.get("container_id") if "container_id" in ask else (target["container_id"] if course_id == target["course_id"] else "")
                chosen = str(chosen or "")
                if not course_id:
                    entry["reason"] = "Choose this section's Gradescope course first."
                    continue
                if course_id not in by_id:
                    entry["reason"] = "That Gradescope course is not one of your courses (or is one Faculty never touches)."
                    continue
                course = by_id[course_id]
                entry.update(course_id=course_id, course=gw.course_label(course))
                if sheets is not None and target["section_id"] not in sheets["linked"]:
                    sheet_wanted.setdefault(course_id, {"course": course, "sections": []})["sections"].append(target["section_id"])
                if chosen:
                    entry["reason"] = "Already goes to an assignment on Gradescope."
                    need = sorted(l for l in needed.get(target["section_id"], ()) if l)
                    found = next((c for c in containers(assignments(course_id)) if c["id"] == chosen), None) if need else None
                    lacking = [l for l in need if found and l not in found["versions"]]
                    if not lacking:
                        if makeup and found:
                            entry["reason"] = f"“{found['title']}” on Gradescope already has Version {makeup}."
                        elif makeup:
                            entry["reason"] = "The linked Gradescope assignment is not in this course any more."
                        continue
                    have = sorted(found["versions"])
                    # Gradescope letters a set's versions by their order, so the set must run from A and Faculty adds every letter up
                    # to the one a paper needs, each of them one of the plan's versions.
                    add = [chr(65 + i) for i in range(len(have), ord(max(lacking)) - 64)]
                    if "" in have or have != [chr(65 + i) for i in range(len(have))] or any(l not in faculty["every"] for l in add) \
                            or (makeup and add != [makeup]):
                        entry["reason"] = (f"“{found['title']}” on Gradescope has versions {', '.join(have) or '(none)'}; Faculty cannot add "
                                           f"Version {', '.join(lacking)} to it (Gradescope letters a set's versions in order).")
                        continue
                    entry["reason"] = ""
                    key = (course_id, chosen)
                    known = adding.setdefault(key, {"course": course, "container": found, "letters": [], "sections": []})
                    known["sections"].append(target["section_id"])
                    known["letters"] = add if len(add) > len(known["letters"]) else known["letters"]
                    continue
                wanted.setdefault(course_id, {"course": course, "sections": []})["sections"].append(target["section_id"])
            for course_id, entry in sorted(wanted.items()):
                course = entry["course"]
                row = course_row(course_id, course, entry)
                existing = {gw.normalized(a["title"]) for a in assignments(course_id)}
                clash = [names[letter] for letter in letters if gw.normalized(names[letter]) in existing]
                if len(letters) > 1 and gw.normalized(title) in existing:
                    clash.insert(0, title)
                alike = self._alike(containers(assignments(course_id)), letters, title) if linked_only and not clash else ""
                if clash:
                    row["problem"] = (f"{row['course']} already has an assignment named “{clash[0]}”. Choose it as the section's assignment, "
                                      "or give the new one another name.")
                elif alike:
                    # By itself Faculty never makes a second assignment beside one that may be this one (made there by hand).
                    row["problem"] = (f"“{alike}” in {row['course']} may be this assignment, so Faculty does not make another one by itself. "
                                      "Choose it with Send to Gradescope…, or create the assignment yourself.")
                else:
                    units.append({"course_id": course_id, "course_title": course["short"] or course["name"], "course_name": course["name"],
                                  "sections": sorted(entry["sections"])})
                view["courses"].append(row)
            for (course_id, container_id), entry in sorted(adding.items()):
                course, found, add = entry["course"], entry["container"], entry["letters"]
                row = course_row(course_id, course, entry)
                row["adds"] = {"container": found["title"], "has": sorted(found["versions"]), "letters": add}
                existing = {gw.normalized(a["title"]) for a in assignments(course_id)}
                clash = [names[letter] for letter in add if gw.normalized(names[letter]) in existing]
                if any(u["course_id"] == course_id for u in units):
                    row["problem"] = "Faculty makes one thing in a Gradescope course at a time: create again once the other is made."
                elif clash:
                    row["problem"] = (f"{row['course']} already has an assignment named “{clash[0]}”. Add it to “{found['title']}” on "
                                      "Gradescope yourself (Organize Exam Versions), or give the new one another name.")
                else:
                    units.append({"course_id": course_id, "course_title": course["short"] or course["name"], "course_name": course["name"],
                                  "sections": sorted(entry["sections"]), "letters": add,
                                  "add_to": {"container_id": found["id"], "container_title": found["title"], "existing": found["versions"]}})
                    if found["published"]:
                        extra.append(f"Grades of “{found['title']}” are published in {row['course']}: Version {', '.join(add)} is added there "
                                     "unpublished. Publish grades again once its grades are sent.")
                view["courses"].append(row)
            sheet_units = []
            if sheets is not None:
                # THE BUBBLE-SHEET EXAM TOO: in each course of a section with no bubble-sheet exam linked for the plan, one set named
                # with the second name; a course that already has anything of its names is refused, as the written one is.
                sheet_names = [sheets["title"], *(v["title"] for v in sheets["versions"])]
                view["sheets"] = {"title": sheets["title"], "versions": sheets["versions"], "problems": sheets["problems"], "courses": [],
                                  "linked": sorted(s["section"] for s in view["sections"] if s["section_id"] in sheets["linked"])}
                for course_id, entry in sorted(sheet_wanted.items()):
                    course = entry["course"]
                    row = course_row(course_id, course, entry)
                    existing = {gw.normalized(a["title"]) for a in assignments(course_id)}
                    clash = next((name for name in sheet_names if gw.normalized(name) in existing), "")
                    if clash:
                        row["problem"] = (f"{row['course']} already has an assignment named “{clash}”. Choose it on the Bubble sheets page, "
                                          "or give the bubble-sheet exam another name.")
                    else:
                        sheet_units.append({"kind": "sheets", "course_id": course_id, "course_title": course["short"] or course["name"],
                                            "course_name": course["name"], "sections": sorted(entry["sections"])})
                    view["sheets"]["courses"].append(row)
        except gw.GradescopeError as error:
            if not own:
                raise  # the sender's: it waits on a busy Gradescope or a refused sign-in itself
            raise Conflict(f"Gradescope could not be read: {error}") from None
        finally:
            if own:
                session.close()
        # What is made: every version where a whole assignment is made, else only the versions added to sets. Only their problems
        # and warnings count (a version already on Gradescope holds nothing up).
        made = set(faculty["every"]) if not units or any(not u.get("add_to") for u in units) else {l for u in units for l in u["letters"]}
        view["versions"] = version_rows(made)
        view["problems"] = self._for_letters(faculty["problems"], made)
        from .gradescope_publish import PUBLISHING
        if publishing:
            view["problems"].append(PUBLISHING)
        view["warnings"] = self._for_letters(faculty["warnings"], made) + extra
        if sheets is not None and sheet_units and not units:
            # Only the bubble-sheet exam is made: what the written one lacks holds nothing up and is not shown.
            view["problems"] = [p for p in view["problems"] if publishing and p == PUBLISHING]
            view["versions"], view["warnings"] = [], []
        if view["problems"]:
            units = []
        if sheets is not None and (sheets["problems"] or publishing):
            sheet_units = []
        exam = {"title": title, "letters": letters, "container_title": title if len(letters) > 1 else None,
                "versions": {letter: {"title": names[letter], "template": faculty["versions"][letter]["template"],
                                      "questions": faculty["versions"][letter]["questions"]} for letter in letters}}
        sheet_exam = {"kind": "sheets", "title": sheets["title"], "letters": sheets["letters"], "key": sheets["key"],
                      "key_revision": sheets["revision"]} if sheets is not None else None
        # One unit per Gradescope course: a course that gets the written assignment makes the bubble-sheet exam after it.
        by_course = {u["course_id"]: u for u in units}
        for unit in (sheet_units if sheets is not None else []):
            if unit["course_id"] in by_course:
                by_course[unit["course_id"]]["sheets"] = unit["sections"]
            else:
                units.append(unit)
        view["ready"] = bool(units) and not view["running"]
        # Named only with a bubble-sheet exam, so a plan without an answer key keeps the fingerprint it always had.
        view["fingerprint"] = digest({"rule": CREATE_RULE, "assignment": exam, "units": units, "courses": view["courses"], "sections": view["sections"],
                                      "problems": view["problems"], **({"sheets": {"exam": sheet_exam, "view": view["sheets"]}} if sheets is not None else {})})
        return {"view": view, "units": units, "assignment": exam, "sheets": sheet_exam}

    @staticmethod
    def _alike(choices, letters, title):
        """The title of an assignment or version set already in a course that may be this one (GRADESCOPE "AFTER CERTIFYING": the
        send's own proposal rule without its "exactly one", plus a title that holds the other): it has every version letter, and
        carries a number of the title or one title contains the other. '' when there is none."""
        numbered, mine = set(re.findall(r"\d+", title or "")), gw.normalized(title or "")
        for choice in choices:
            if not all(letter in choice["versions"] for letter in letters):
                continue
            theirs = gw.normalized(choice["title"])
            if (numbered & set(re.findall(r"\d+", choice["title"]))) or (mine and theirs and (mine in theirs or theirs in mine)):
                return choice["title"]
        return ""

    @staticmethod
    def _unit_plan(assignment, unit, sheets=None):
        """What one unit makes: the whole assignment, (adding to a set) only its new versions, grouped into that set, or the
        bubble-sheet exam (a SHEETS unit)."""
        if unit.get("kind") == "sheets":
            return {"title": sheets["title"], "letters": [], "versions": {}, "container_title": None, "sheets": {**sheets, "sections": unit["sections"]}}
        if not unit.get("add_to"):
            made = assignment
        else:
            made = {**assignment, "letters": unit["letters"], "container_title": unit["add_to"]["container_title"],
                    "versions": {letter: assignment["versions"][letter] for letter in unit["letters"]}, "add_to": unit["add_to"]}
        return {**made, "sheets": {**sheets, "sections": unit["sheets"]}} if unit.get("sheets") else made

    def create_preview(self, aid, data=None):
        return self._creation_plan(aid, data)["view"]

    def create(self, aid, data):
        from .gradescope_sync import request_head  # imported here: that module mixes this one in
        request_id, fingerprint = request_head(data, CREATE_CONFIRMATION, no_id="Creating needs a request ID from the preview.",
                                               not_confirmed="Confirm what Faculty will make on Gradescope: nothing is made without it.")
        # The second name only when given, so a request without it hashes as it always did.
        command = digest({"assignment_id": aid, "request_id": request_id, "fingerprint": fingerprint, "title": data.get("title"),
                          "targets": data.get("targets") or {}, **({"sheet_title": data["sheet_title"]} if data.get("sheet_title") else {})})
        with self.rosters.lock(), connect(self.path) as db:
            earlier = db.execute("SELECT * FROM frq_gradescope_creations WHERE request_id=?", (request_id,)).fetchone()
        if earlier:
            if earlier["request_sha256"] != command or earlier["assignment_id"] != aid:
                raise Conflict("That request ID was already used for something else.")
            return self._creation_receipt(earlier)
        planned = self._creation_plan(aid, data)  # everything read again, Gradescope included
        view = planned["view"]
        if view["fingerprint"] != fingerprint:
            raise Conflict("Something changed since the preview, in Faculty or on Gradescope. Look at the new preview and create again.")
        if view["running"]:
            raise Conflict("Faculty is still making this assignment on Gradescope.")
        if not planned["units"]:  # a part with problems has no units (THE BUBBLE-SHEET EXAM TOO: each part is held by its own)
            raise Conflict("There is nothing Faculty can make on Gradescope for this assignment yet.")
        return self._record_creation(aid, planned, request_id, command, fingerprint)

    def _record_creation(self, aid, planned, request_id, command, fingerprint, actor="workspace", automatic=None):
        """The approved creation and its units, in one transaction (`create`'s, and Faculty's own makeup version: `actor` automatic)."""
        view = planned["view"]
        now, creation_id = timestamp(), uid()
        try:
            with self.rosters.lock(), connect(self.path) as db:
                db.execute("BEGIN IMMEDIATE")
                earlier = db.execute("SELECT * FROM frq_gradescope_creations WHERE request_id=?", (request_id,)).fetchone()
                if earlier:
                    if earlier["request_sha256"] != command:
                        raise Conflict("That request ID was already used for something else.")
                    return self._creation_receipt(earlier)
                # GRADESCOPE: a publication must not race a creation approved before it started.
                if self._publishing(db, aid):
                    from .gradescope_publish import PUBLISHING
                    raise Conflict(PUBLISHING)
                summary = {"view": {k: view[k] for k in ("title", "versions", "courses", "sections", "warnings", "sheets") if k in view},
                           **({"automatic": automatic} if automatic else {})}
                db.execute("INSERT INTO frq_gradescope_creations VALUES(?,?,?,?,?,?,?,'pending',?,?,?)",
                           (creation_id, aid, request_id, command, fingerprint, view["title"], json.dumps(summary, sort_keys=True), actor, now, now))
                for unit in planned["units"]:
                    db.execute("""INSERT INTO frq_gradescope_creation_units(id,send_id,assignment_id,course_id,course_title,sections_json,plan_json,status,
                        created_at,updated_at) VALUES(?,?,?,?,?,?,?,'pending',?,?)""",
                        (uid(), creation_id, aid, unit["course_id"], unit["course_title"], json.dumps(unit["sections"]),
                         json.dumps({**self._unit_plan(planned["assignment"], unit, planned.get("sheets")), "course_name": unit["course_name"]}, sort_keys=True),
                         now, now))
                self.frq.event(db, aid, "gradescope_create", {"creation_id": creation_id, "request_id": request_id, "fingerprint": fingerprint,
                                                              "title": view["title"], "courses": [u["course_id"] for u in planned["units"]],
                                                              **({"automatic": automatic} if automatic else {})}, actor)
                row = db.execute("SELECT * FROM frq_gradescope_creations WHERE id=?", (creation_id,)).fetchone()
        except sqlite3.IntegrityError:
            raise Conflict("Faculty is already making this assignment on Gradescope. Look at the new preview.") from None
        self.wake.set()
        return self._creation_receipt(row)

    def create_cancel(self, aid, creation_id):
        """Stops a creation: nothing still waiting is made. What Gradescope already has stays there (Faculty deletes nothing)."""
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM frq_gradescope_creations WHERE id=? AND assignment_id=?", (creation_id, aid)).fetchone()
            if not row:
                raise NotFound("That creation was not found for this assignment.")
            if row["status"] in ("pending", "running"):
                now = timestamp()
                db.execute("UPDATE frq_gradescope_creation_units SET status='cancelled',updated_at=? WHERE send_id=? AND status IN ('pending','uncertain','failed')",
                           (now, creation_id))
                db.execute("UPDATE frq_gradescope_creations SET status='cancelled',updated_at=? WHERE id=?", (now, creation_id))
                self.frq.event(db, aid, "gradescope_create_cancelled", {"creation_id": creation_id})
            row = db.execute("SELECT * FROM frq_gradescope_creations WHERE id=?", (creation_id,)).fetchone()
        return self._creation_receipt(row)

    def create_retry(self, aid, creation_id):
        """The instructor's explicit Retry of the courses where making the assignment stopped (after fixing what it named)."""
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM frq_gradescope_creations WHERE id=? AND assignment_id=?", (creation_id, aid)).fetchone()
            if not row:
                raise NotFound("That creation was not found for this assignment.")
            if row["status"] == "cancelled":
                raise Conflict("A cancelled creation is not retried. Preview and create again.")
            if db.execute("SELECT 1 FROM frq_gradescope_creations WHERE assignment_id=? AND id!=? AND status IN ('pending','running')", (aid, creation_id)).fetchone():
                raise Conflict("Faculty is making this assignment on Gradescope in another request; let it finish first.")
            if self._publishing(db, aid):
                from .gradescope_publish import PUBLISHING
                raise Conflict(PUBLISHING)
            now = timestamp()
            retried = db.execute("UPDATE frq_gradescope_creation_units SET status='pending',not_before=NULL,error='',updated_at=? WHERE send_id=? AND status='failed'",
                                 (now, creation_id)).rowcount
            if not retried:
                raise Conflict("Nothing in this creation stopped.")
            db.execute("UPDATE frq_gradescope_creations SET status='running',updated_at=? WHERE id=?", (now, creation_id))
            self.frq.event(db, aid, "gradescope_create_retried", {"creation_id": creation_id, "units": retried})
            row = db.execute("SELECT * FROM frq_gradescope_creations WHERE id=?", (creation_id,)).fetchone()
        self.wake.set()
        return self._creation_receipt(row)

    @staticmethod
    def _creation_receipt(row):
        return {"creation_id": row["id"], "request_id": row["request_id"], "status": row["status"], "title": row["title"],
                "created_at": row["created_at"], "plan": json.loads(row["plan_json"])}

    def _creations(self, db, aid):
        """The latest creations of the plan, with where each course's work is (for the panel)."""
        found = []
        for row in db.execute("SELECT * FROM frq_gradescope_creations WHERE assignment_id=? ORDER BY created_at DESC,rowid DESC LIMIT 3", (aid,)).fetchall():
            units = [{"course_id": u["course_id"], "course": u["course_title"], "status": u["status"], "step": u["step"], "error": u["error"],
                      "container_id": u["container_id"],
                      # A unit that makes the bubble-sheet exam too (or only) names it (THE BUBBLE-SHEET EXAM TOO).
                      **({"sheets": made["sheets"]["title"]} if (made := json.loads(u["plan_json"])).get("sheets") else {})} for u in db.execute(
                "SELECT * FROM frq_gradescope_creation_units WHERE send_id=? ORDER BY course_title,course_id", (row["id"],))]
            automatic = json.loads(row["plan_json"] or "{}").get("automatic")  # a makeup version Faculty added by itself (2026-10-09)
            found.append({"id": row["id"], "title": row["title"], "status": row["status"], "created_at": row["created_at"],
                          "updated_at": row["updated_at"], "units": units, **({"automatic": automatic} if automatic else {})})
        return found

    def _created_receipts(self, db, aid):
        """{(Gradescope course, Gradescope assignment, Faculty question): the latest receipt}, for matching grades."""
        found, wordings = {}, {}
        for row in db.execute("SELECT receipt_id,item_id,description FROM frq_gradescope_item_wordings WHERE assignment_id=? ORDER BY created_at,rowid", (aid,)):
            wordings.setdefault(row[0], {}).setdefault(row[1], []).append(row[2])
        for row in db.execute("SELECT * FROM frq_gradescope_created_questions WHERE assignment_id=? ORDER BY created_at,rowid", (aid,)):
            found[(row["course_id"], row["gradescope_assignment_id"], row["question_id"])] = {**dict(row), "wordings": wordings.get(row["id"], {})}
        return found

    def record_item_wordings(self, aid, data):
        """RENAMED ITEMS (GRADESCOPE; requirement, 2026-10-31): `question_id`, `items` (Faculty line IDs) and `reason`. For every course
        where Faculty made that question, each item as Gradescope words it NOW is recorded as a wording the receipt match accepts, when
        it is the item Faculty made, worth what Faculty's line is, and worded neither as sent nor as the line is now. Gradescope is
        only read; Faculty's rubric and its grades are left as they are. Idempotent: a wording already known is not recorded again."""
        qid = str(data.get("question_id") or "")
        lines = data.get("items")
        reason = str(data.get("reason") or "").strip()
        if not isinstance(lines, list) or not lines or len(lines) > 200 or not all(isinstance(i, str) and i for i in lines):
            raise ValueError("Name the rubric lines whose Gradescope wording to record.")
        if not reason or len(reason) > 2000:
            raise ValueError("Say why Gradescope's wording differs (at most 2,000 characters).")
        with self.rosters.lock(), connect(self.path) as db:
            question = self.frq.question(db, aid, qid)
            rules = {i["id"]: i for i in ((question.get("rubric") or {}).get("rules") or {}).get("items", [])}
            if set(lines) - set(rules):
                raise ValueError("Name lines of this question's rubric.")
            mode = question["rubric"]["rules"].get("mode", "negative")
            receipts = [r for r in self._created_receipts(db, aid).values() if r["question_id"] == qid]
        if not receipts:
            raise Conflict("Faculty did not make this question on Gradescope, so there is no item of its own to match.")
        session = self.session_factory()
        found = []
        try:
            for receipt in receipts:
                order = {i["id"]: i for i in gw.export_order(session.rubric(receipt["course_id"], receipt["gradescope_assignment_id"]),
                                                              int(receipt["gradescope_question_id"]))}
                made = json.loads(receipt["items_json"])
                for line in lines:
                    sent = made.get(line)
                    item = order.get(str((sent or {}).get("id")))
                    if not sent or not item:
                        raise Conflict(f"{line} has no item Faculty made in Gradescope course {receipt['course_id']}; nothing was recorded.")
                    if item["points"] != _amount(line_weight(rules[line], mode)):
                        raise Conflict(f"{line} is worth {_text(item['points'])} on Gradescope in course {receipt['course_id']} and "
                                       f"{_text(rules[line]['points'])} in Faculty; nothing was recorded.")
                    known = {gw.normalized(sent.get("description")), gw.normalized(rules[line]["description"]),
                             *map(gw.normalized, receipt["wordings"].get(line, ()))}
                    if gw.normalized(item["name"]) not in known:
                        found.append((receipt, line, str(sent["id"]), item["name"]))
        except gw.GradescopeError as error:
            raise Conflict(f"Gradescope could not be read: {error}") from None
        finally:
            session.close()
        now = timestamp()
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            for receipt, line, item_id, wording in found:
                db.execute("INSERT OR IGNORE INTO frq_gradescope_item_wordings VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                           (uid(), aid, qid, receipt["id"], line, receipt["course_id"], item_id, wording, reason, "workspace", now))
            if found:
                self.frq.event(db, aid, "gradescope_item_wordings_recorded", {"question_id": qid, "items": sorted({f[1] for f in found}),
                                                                             "courses": sorted({f[0]["course_id"] for f in found}), "count": len(found)})
        return {"question_id": qid, "recorded": [{"course_id": r["course_id"], "item_id": line, "description": wording}
                                                 for r, line, _, wording in found]}

    # --- the sender's side: one step of one course at a time ----------------------------------------------------------------
    def _settle_creations(self, db, now):
        cancelled = "SELECT id FROM frq_gradescope_creations WHERE status='cancelled'"
        db.execute(f"UPDATE frq_gradescope_creation_units SET status='cancelled',updated_at=? WHERE status IN ('pending','uncertain') AND send_id IN ({cancelled})", (now,))
        for row in db.execute("SELECT id,assignment_id FROM frq_gradescope_creations WHERE status IN ('pending','running')").fetchall():
            if not db.execute("SELECT 1 FROM frq_gradescope_creation_units WHERE send_id=? AND status IN ('pending','sending','uncertain')", (row["id"],)).fetchone():
                db.execute("UPDATE frq_gradescope_creations SET status='complete',updated_at=? WHERE id=?", (now, row["id"]))
                self.frq.event(db, row["assignment_id"], "gradescope_create_complete", {"creation_id": row["id"]})

    def _creation_claim(self, unit):
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            claimed = db.execute("UPDATE frq_gradescope_creation_units SET status='sending',attempts=attempts+1,updated_at=? WHERE id=? AND status IN ('pending','uncertain')",
                                 (timestamp(), unit["id"])).rowcount
            if claimed:
                db.execute("UPDATE frq_gradescope_creations SET status=CASE WHEN status='pending' THEN 'running' ELSE status END,updated_at=? WHERE id=?",
                           (timestamp(), unit["send_id"]))
        return bool(claimed)

    def _creation_save(self, unit, progress, step, status="pending"):
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("UPDATE frq_gradescope_creation_units SET status=?,step=?,progress_json=?,error='',not_before=NULL,updated_at=? WHERE id=? AND status='sending'",
                       (status, step[:500], json.dumps(progress, sort_keys=True), timestamp(), unit["id"]))

    def _creation_fail(self, unit, reason):
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("UPDATE frq_gradescope_creation_units SET status='failed',error=?,updated_at=? WHERE id=? AND status IN ('pending','sending','uncertain')",
                       (str(reason)[:2000], timestamp(), unit["id"]))

    def _create_step(self, unit):
        """One step for one Gradescope course. Every step reads Gradescope first, so a step whose write went unanswered is only read again."""
        if not self._creation_claim(unit):
            return
        plan, progress = json.loads(unit["plan_json"]), json.loads(unit["progress_json"] or "{}")
        try:
            session = self._session_now()
            course = session.guard(unit["course_id"])
            if gw.forbidden(unit["course_title"], plan.get("course_name", "")):
                raise CreationRefused("That is the course Faculty never touches (Math 140 for Instructors).")
            for letter in plan["letters"]:
                version, done = plan["versions"][letter], progress.setdefault("versions", {}).setdefault(letter, {})
                if not done.get("assignment"):
                    return self._create_assignment(unit, session, course, version, done, progress)
                if not done.get("outline"):
                    return self._create_outline(unit, session, course, version, done, progress)
                if not done.get("rubric"):
                    return self._create_rubric(unit, session, course, version, done, progress)
            if plan.get("add_to"):
                if not progress.get("container"):
                    return self._create_add_versions(unit, session, course, plan, progress)
            elif plan["container_title"] and not progress.get("container"):
                return self._create_version_set(unit, session, course, plan, progress)
            if plan.get("sheets") and not progress.get("sheets", {}).get("key"):
                return self._create_sheets(unit, session, course, plan["sheets"], progress.setdefault("sheets", {}), progress)
            return self._create_finish(unit, plan, progress)
        except CreationRefused as error:
            self._creation_fail(unit, error)

    def _create_assignment(self, unit, session, course, version, done, progress):
        found = [a for a in session.assignments(course) if gw.normalized(a["title"]) == gw.normalized(version["title"]) and a["kind"] == "assignment"]
        if len(found) > 1:
            raise CreationRefused(f"Gradescope has two assignments named “{version['title']}”; delete the one Faculty should not use, then Retry.")
        if not found:
            pdf, _ = self.frq.templates.read(version["template"]["id"], "pdf")
            session.create_assignment(course, version["title"], pdf, f"{re.sub(r'[^A-Za-z0-9._-]+', '-', version['title']).strip('-') or 'template'}.pdf")
            found = [a for a in session.assignments(course) if gw.normalized(a["title"]) == gw.normalized(version["title"]) and a["kind"] == "assignment"]
            if not found:
                raise CreationRefused(f"Gradescope did not keep the new assignment “{version['title']}”.")
        assignment = found[0]
        # Faculty's own: the preview found no assignment of this name, and this one holds nothing (a paper would mean it is someone's).
        if assignment["submissions"] or assignment["parent"]:
            raise CreationRefused(f"“{version['title']}” on Gradescope already has papers or belongs to a version set; Faculty leaves it alone.")
        done["assignment"] = assignment["id"]
        self._creation_save(unit, progress, f"Made {version['title']}")

    def _create_outline(self, unit, session, course, version, done, progress):
        outline = session.outline(course, done["assignment"])
        if outline["template_status"] == "failed":
            raise CreationRefused(f"Gradescope could not read the blank template of {version['title']}.")
        if outline["template_status"] != "complete":
            raise Processing("Gradescope is still reading the blank template into pages.")
        if outline["pages"] != version["template"]["pages"]:
            raise CreationRefused(f"{version['title']} has {outline['pages']} template pages on Gradescope and Faculty's blank has {version['template']['pages']}.")
        wanted = [{"title": q["title"], "weight": float(q["weight"]), "crop_rect_list": q["crops"]} for q in version["questions"]]
        if not outline["questions"]:
            session.save_outline(course, done["assignment"], wanted, outline["csrf"])
            outline = session.outline(course, done["assignment"])
        have = sorted(outline["questions"], key=lambda q: int(q.get("index") or 0))
        if len(have) != len(wanted) or any(h.get("parent_id") or h.get("children") or h["title"] != w["title"]
                                           or _amount(h["weight"]) != _amount(w["weight"]) or not same_rects(h.get("crop_rect_list"), w["crop_rect_list"])
                                           for h, w in zip(have, wanted)):
            raise CreationRefused(f"The outline of {version['title']} on Gradescope is not the one Faculty made (was it edited there?).")
        done["questions"] = {q["question_id"]: str(h["id"]) for q, h in zip(version["questions"], have)}
        # The items Gradescope put on each new question before Faculty touched its rubric: the only thing Faculty ever deletes there.
        rubric = session.rubric(course, done["assignment"])
        done["defaults"] = {gq: sorted(str(i["id"]) for i in rubric.get("rubric_items", []) if str(i.get("question_id")) == gq)
                            for gq in done["questions"].values()}
        done["outline"] = True
        self._creation_save(unit, progress, f"Saved the questions of {version['title']}")

    def _create_rubric(self, unit, session, course, version, done, progress):
        """One question's scoring and rubric lines per step: its defaults deleted, the lines Gradescope lacks added in order, then read back."""
        finished = done.setdefault("rubric_done", [])
        for question in version["questions"]:
            if question["question_id"] in finished:
                continue
            gq = done["questions"][question["question_id"]]
            editor = session.rubric_editor(course, done["assignment"])
            target = next((q for q in editor["props"].get("questions", []) if str(q.get("id")) == gq), None)
            if not target:
                raise CreationRefused(f"{question['title']} is gone from {version['title']} on Gradescope.")
            if target.get("scoring_type") != question["scoring"] or not target.get("floor") or not target.get("ceiling"):
                session.save_grading_options(course, editor, gq, question["scoring"], True, True)
            items = sorted((i for i in editor["props"].get("rubric_items", []) if str(i.get("question_id")) == gq), key=lambda i: int(i.get("position") or 0))
            defaults = set(done["defaults"].get(gq, []))
            for item in items:
                if str(item["id"]) in defaults:
                    session.delete_rubric_item(course, editor, gq, item["id"])
            ours = [i for i in items if str(i["id"]) not in defaults]
            lines = question["lines"]
            if len(ours) > len(lines) or any(i.get("group_id") or gw.normalized(i.get("description")) != gw.normalized(line["description"])
                                             or _amount(i.get("weight") or 0) != _amount(line["weight"]) for i, line in zip(ours, lines)):
                raise CreationRefused(f"The rubric of {question['title']} ({version['title']}) on Gradescope is not the one Faculty is making (was it edited there?).")
            for line in lines[len(ours):]:
                session.add_rubric_item(course, editor, gq, line["description"], line["weight"])
            rubric = session.rubric(course, done["assignment"])
            question_row = next((q for q in rubric.get("questions", []) if str(q.get("id")) == gq), {})
            made = gw.export_order(rubric, gq)
            if (question_row.get("scoring_type") != question["scoring"] or len(made) != len(lines)
                    or any(m["group_id"] or gw.normalized(m["description"]) != gw.normalized(line["description"]) or m["points"] != _amount(line["weight"])
                           for m, line in zip(made, lines))):
                raise CreationRefused(f"Gradescope did not keep the rubric of {question['title']} ({version['title']}) as Faculty sent it.")
            done.setdefault("items", {})[question["question_id"]] = {line["id"]: {"id": m["id"], "description": line["description"], "weight": line["weight"]}
                                                                    for m, line in zip(made, lines)}
            finished.append(question["question_id"])
            self._creation_save(unit, progress, f"Rubric of {question['title']} ({version['title']})")
            return
        done["rubric"] = True
        self._creation_save(unit, progress, f"Rubrics of {version['title']} done")

    def _create_version_set(self, unit, session, course, plan, progress):
        ids = [progress["versions"][letter]["assignment"] for letter in plan["letters"]]

        def parents():
            rows = {a["id"]: a for a in session.assignments(course)}
            return rows, {rows[i]["parent"] if i in rows else None for i in ids}
        rows, found = parents()
        if None in found:
            raise CreationRefused("One of the new versions is gone from Gradescope.")
        if found == {""}:
            session.link_versions(course, plan["container_title"], ids)
            rows, found = parents()
        container = next(iter(found)) if len(found) == 1 else ""
        if not container or container not in rows or gw.normalized(rows[container]["title"]) != gw.normalized(plan["container_title"]) \
                or any(re.sub(r"(?i)^version\s*", "", rows[i]["version"]).upper() != letter for i, letter in zip(ids, plan["letters"])):
            raise CreationRefused("Gradescope did not group the new versions as Faculty asked (Organize Exam Versions); group them there, then Retry.")
        progress["container"] = container
        self._creation_save(unit, progress, f"Grouped the versions as {plan['container_title']}")

    def _create_add_versions(self, unit, session, course, plan, progress):
        """Adds the new versions to the set the sections already go to, as its Edit page's Save does: the set's versions in their
        order, then the new ones (Gradescope letters them in that order, so Version E follows A to D). Read before and after: a
        Save whose answer was lost is only read again. Anything unlike the preview (the set changed, a new version taken by
        another set) stops the unit for the instructor."""
        add = plan["add_to"]
        container, existing = str(add["container_id"]), {k: str(v) for k, v in add["existing"].items()}
        new = {letter: str(progress["versions"][letter]["assignment"]) for letter in plan["letters"]}
        order = [existing[k] for k in sorted(existing)] + [new[k] for k in plan["letters"]]
        named = f"Version {', '.join(plan['letters'])}"

        def versions(rows):
            return {re.sub(r"(?i)^version\s*", "", a["version"]).upper(): a["id"] for a in rows.values() if a["parent"] == container}
        rows = {a["id"]: a for a in session.assignments(course)}
        if container not in rows:
            raise CreationRefused(f"“{add['container_title']}” is gone from Gradescope.")
        if any(i not in rows for i in new.values()):
            raise CreationRefused(f"The new {named} is gone from Gradescope.")
        if any(rows[i]["parent"] not in ("", container) for i in new.values()):
            raise CreationRefused(f"The new {named} belongs to another version set on Gradescope; Faculty leaves it alone.")
        if any(rows[i]["parent"] != container for i in new.values()):
            if versions(rows) != existing:
                raise CreationRefused(f"“{add['container_title']}” on Gradescope no longer has the versions the preview showed; "
                                      "preview and create again.")
            form = session.version_set(course, container)
            if form["selected"] != [existing[k] for k in sorted(existing)] or any(i not in form["offered"] for i in new.values()):
                raise CreationRefused(f"Gradescope's Edit page of “{add['container_title']}” does not offer the new {named} after its "
                                      "versions; add it there (Organize Exam Versions), then Retry.")
            session.save_version_set(course, form, order)
            rows = {a["id"]: a for a in session.assignments(course)}
        if versions(rows) != {**existing, **new}:
            raise CreationRefused(f"Gradescope did not add {named} to “{add['container_title']}” as Faculty asked; add it there "
                                  "(Organize Exam Versions), then Retry.")
        progress["container"] = container
        self._creation_save(unit, progress, f"Added {named} to {add['container_title']}")

    @staticmethod
    def _sheet_exam(session, course, title):
        """The course's one top-level assignment or version set under the bubble-sheet exam's name (None when there is none)."""
        found = [a for a in session.assignments(course) if not a["parent"] and gw.normalized(a["title"]) == gw.normalized(title)
                 and a["kind"] in ("assignment", "assignment_container")]
        if len(found) > 1:
            raise CreationRefused(f"Gradescope has two assignments named “{title}”; delete the one Faculty should not use, then Retry.")
        return found[0] if found else None

    def _create_sheets(self, unit, session, course, sheets, done, progress):
        """THE BUBBLE-SHEET EXAM TOO, one step at a time, each reading Gradescope first: make the Bubble Sheet assignment; add one
        version per step on its Edit Answer Key page until it has Faculty's letters (Gradescope names them, and the first Add Version
        makes the assignment Version A of a new set); then save every version's key at once and read it back. Anything Faculty did not
        make (papers, other versions, another key) stops the unit for the instructor."""
        title, wanted = sheets["title"], [f"Version {letter}" for letter in sheets["letters"]]
        exam = self._sheet_exam(session, course, title)
        if not done.get("exam"):
            if exam is None:
                session.create_bubble_sheet(course, title)
                exam = self._sheet_exam(session, course, title)
                if exam is None:
                    raise CreationRefused(f"Gradescope did not keep the new bubble-sheet exam “{title}”.")
            # Faculty's own: the preview found nothing of this name, and this one holds nothing and has no versions yet.
            if exam["kind"] != "assignment" or exam["submissions"]:
                raise CreationRefused(f"“{title}” on Gradescope already has sheets or versions; Faculty leaves it alone.")
            done["exam"] = exam["id"]
            self._creation_save(unit, progress, f"Made {title}")
            return
        if exam is None:
            raise CreationRefused(f"“{title}” is gone from Gradescope.")
        if exam["submissions"]:
            raise CreationRefused(f"“{title}” on Gradescope has sheets now; Faculty leaves it alone.")
        editor = session.bubble_key_editor(course, exam["kind"], exam["id"])
        names = sorted(editor["keys"])
        if names != wanted[:len(names)]:
            raise CreationRefused(f"“{title}” on Gradescope has {', '.join(names)}; Faculty makes {', '.join(wanted)} (was it edited there?).")
        if not done.get("versions"):
            if len(names) < len(wanted):
                session.add_bubble_version(course, editor)
                self._creation_save(unit, progress, f"Added a version to {title}")
                return
            versions = session.exam(course, exam["id"])["versions"] if exam["kind"] == "assignment_container" else {}
            if sorted(versions) != sheets["letters"] or versions.get("A") != done["exam"]:
                raise CreationRefused(f"Gradescope did not make the versions of “{title}” as Faculty asked; Faculty leaves it alone.")
            done.update(versions=versions, container=exam["id"])
            self._creation_save(unit, progress, f"Versions {', '.join(sheets['letters'])} of {title}")
            return
        want = sheets["key"]
        if not all(same_sheet_key(editor["keys"][name], want[name]) for name in wanted):
            if any(editor["keys"][name] for name in wanted):
                raise CreationRefused(f"“{title}” on Gradescope has another answer key than Faculty's (was it edited there?).")
            session.save_bubble_key(course, editor, {name: want[name] for name in wanted})
            editor = session.bubble_key_editor(course, exam["kind"], exam["id"])
            if sorted(editor["keys"]) != wanted or not all(same_sheet_key(editor["keys"][name], want[name]) for name in wanted):
                raise CreationRefused(f"Gradescope did not keep the answer key of “{title}” as Faculty sent it.")
        done["key"] = True
        self._creation_save(unit, progress, f"Answer key of {title}")

    def _create_finish(self, unit, plan, progress):
        letters, sheets = plan["letters"], plan.get("sheets")
        targets, versions, container = {}, {}, None
        if letters:  # the written assignment (a unit making only the bubble-sheet exam has none)
            container = progress.get("container") or progress["versions"][letters[0]]["assignment"]
            # Adding to a set: its versions already there stay, and the sections now go to all of them.
            versions = {**(plan.get("add_to") or {}).get("existing", {}), **{letter: progress["versions"][letter]["assignment"] for letter in letters}}
            targets = {section: {"course_id": unit["course_id"], "course_title": unit["course_title"], "course_name": plan.get("course_name", ""),
                                 "container_id": container, "container_title": plan["container_title"] or plan["versions"][letters[0]]["title"], "versions": versions}
                       for section in json.loads(unit["sections_json"])}
        sheet_targets = {section: {"course_id": unit["course_id"], "course_title": unit["course_title"], "course_name": plan.get("course_name", ""),
                                   "container_id": progress["sheets"]["container"], "container_title": sheets["title"],
                                   "versions": progress["sheets"]["versions"]} for section in sheets["sections"]} if sheets else {}
        now = timestamp()
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            if not db.execute("SELECT 1 FROM frq_gradescope_creation_units WHERE id=? AND status='sending'", (unit["id"],)).fetchone():
                return
            for letter in letters:
                version, done = plan["versions"][letter], progress["versions"][letter]
                for question in version["questions"]:
                    db.execute("""INSERT OR IGNORE INTO frq_gradescope_created_questions VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                               (uid(), unit["id"], unit["assignment_id"], question["question_id"], question["rubric_id"], letter, unit["course_id"],
                                done["assignment"], done["questions"][question["question_id"]],
                                json.dumps(done["items"][question["question_id"]], sort_keys=True), now))
            links = self._record_links(db, unit["assignment_id"], targets, now)
            # The bubble-sheet exam's sections go to it from now on (the Bubble sheets page's send proposes it as linked).
            sheet_links = self._record_links(db, unit["assignment_id"], sheet_targets, now, table="frq_gradescope_sheet_targets") if sheets else []
            made = " and ".join(([plan["title"]] if letters else []) + ([sheets["title"]] if sheets else []))
            db.execute("UPDATE frq_gradescope_creation_units SET status='complete',step=?,progress_json=?,container_id=?,error='',updated_at=? WHERE id=?",
                       (f"Made {made}", json.dumps(progress, sort_keys=True), container or (progress["sheets"]["container"] if sheets else None), now, unit["id"]))
            self.frq.event(db, unit["assignment_id"], "gradescope_created", {"creation_id": unit["send_id"], "course_id": unit["course_id"],
                                                                             "container_id": container, "versions": versions, "links": links,
                                                                             **({"sheets": {"container_id": progress["sheets"]["container"],
                                                                                            "versions": progress["sheets"]["versions"], "links": sheet_links}}
                                                                                if sheets else {})})
