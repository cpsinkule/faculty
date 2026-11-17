"""Importing an assignment's rubrics straight from Gradescope (requirement, 2026-10-24). GRADESCOPE "IMPORTING RUBRICS FROM GRADESCOPE" is the rule.

A READ of Gradescope only: the instructor's courses, the course's assignments and the chosen assignment's Edit Rubric page (one per
version). Each chosen question becomes exactly the per-question evaluation CSV Gradescope's own export would give for it (its
question total, scoring method, and every item in the export's order and numbering "q.i", a group's items worded "Group: item"), and
that CSV goes through the ordinary rubric upload (`RubricUploadsMixin.upload_rubric`): the same retained source, draft question,
receipt and checks, and, because the numbering is the export's, the same Gradescope matching when grades are sent back. Nothing is
written to Gradescope, and nothing is approved: the rubric review and the mandatory finalization follow as for an uploaded CSV.
"""
import csv
import difflib
import hashlib
import io
import json
import re
import uuid
from decimal import Decimal

from werkzeug.exceptions import Conflict

from rosters import connect

from . import bubble_keys, gradescope_import, gradescope_web as gw, locations, rubrics
from .gradescope_import import HEADER, TRAILER
from .models import exam_letter, identifier

QUESTIONS_AT_ONCE = 200


def _decimal_text(value):
    return format(Decimal(str(value)).normalize(), "f")


def printed_from_title(title):
    """The printed question number a Gradescope question title starts with: "Question 3", "Problem 3: Limits", "Q3", "#3" or "3.".
    '' when it does not start with one ("Find f(2)"): the question is then imported without a number, as a CSV upload without one
    is, and its number is set in Answer areas. A number elsewhere in a title is never taken for it."""
    title = str(title or "")
    match = re.match(r"\s*(?:(?:question|problem|q)\s*)?#?\s*(\d{1,4})(?![\d.]\d)(?!\d)", title, re.I)
    number = match[1] if match else ""
    if not match:
        # A misspelt word ("Quesiton 25" on the coordinator's Exam 2 Version C, 2026-10-25) still names the question.
        typed = re.match(r"\s*([a-z]{5,10})\s*#?\s*(\d{1,4})(?![\d.]\d)(?!\d)", title, re.I)
        if typed and any(difflib.SequenceMatcher(None, typed[1].casefold(), word).ratio() >= .75 for word in ("question", "problem")):
            number = typed[2]
    return str(int(number)) if number and int(number) >= 1 else ""


def export_csv(question, order):
    """The per-question evaluation CSV Gradescope's export gives for this question, without any student row: the identity header
    with one column per item (worded as the export words it), then the Point Values (the question total under Score), Rubric
    Numbers and Scoring Method rows. Under negative scoring an item's weight is a deduction, so the export prints it negated."""
    negative = question.get("scoring_type") == "negative"
    names = [item["name"] for item in order]
    points = [_decimal_text(-item["points"] if negative else item["points"]) for item in order]
    numbers = [f"{int(question['index'])}.{position}" for position in range(1, len(order) + 1)]
    lead = [""] * (len(HEADER) - 1)
    total = list(lead)
    total[HEADER.index("score") - 1] = _decimal_text(question.get("weight") or 0)
    rows = [[name.title() if name != "sid" else "SID" for name in HEADER] + names + [name.title() for name in TRAILER], [],
            ["Point Values", *total, *points], ["Rubric Numbers", *lead, *numbers],
            ["Scoring Method", "negative" if negative else "positive"]]
    output = io.StringIO(newline="")
    csv.writer(output).writerows(rows)
    return output.getvalue()


def filename(question, printed):
    """A name of the export's own form ("3_Question_7.csv"), so the upload finds the printed number where it finds it in an export."""
    index = int(question["index"])
    if printed:
        return f"{index}_Question_{printed}.csv"
    title = re.sub(r"[^A-Za-z0-9]+", "_", str(question.get("title") or "")).strip("_")[:80]
    return f"{index}_{title or 'Untitled'}.csv"


def rubric_questions(rubric):
    """Each whole top-level question of an Edit Rubric page with what importing it would give, or why it cannot be imported."""
    found = []
    for question in sorted((q for q in rubric.get("questions") or [] if not q.get("parent_id")), key=lambda q: int(q.get("index") or 0)):
        title = str(question.get("title") or "").strip()
        printed = printed_from_title(title)
        entry = {"id": str(question["id"]), "index": int(question.get("index") or 0), "title": title, "printed_number": printed,
                 "points": _decimal_text(question.get("weight") or 0), "scoring": question.get("scoring_type") or "positive",
                 "items": 0, "reason": "", "warnings": []}
        found.append(entry)
        if question.get("children"):
            entry["reason"] = "It has parts on Gradescope; Faculty imports whole questions only."
            continue
        order = gw.export_order(rubric, question["id"])
        entry["items"] = len(order)
        if not order:
            entry["reason"] = "It has no rubric items on Gradescope yet."
            continue
        if any(not item["description"] for item in order):
            entry["reason"] = "One of its rubric items has no wording on Gradescope."
            continue
        if not printed:
            entry["warnings"].append("Its Gradescope title does not print a question number; set it in Answer areas after importing.")
        if question.get("floor") is False or question.get("ceiling") is False:
            entry["warnings"].append("Gradescope lets this question's score go below 0 or above its points; Faculty bounds it by both.")
        source = export_csv(question, order)
        try:
            exported = gradescope_import.inspect(source)
            rubrics.parse(source, exported["max_points"], "csv")
        except (ValueError, TypeError) as error:
            entry["reason"] = str(error)
            continue
        entry["source"], entry["filename"] = source, filename(question, printed)
    return found


def crop_regions(crops, pages):
    """Gradescope's boxes of a question (`crop_rect_list`: page_number and x1, x2, y1, y2 in percent of the page) as Faculty's answer
    areas (page fractions), checked as any answer area is; None when there are none or one does not fit the template."""
    regions = []
    for crop in crops or []:
        try:
            x1, x2, y1, y2 = (float(crop[k]) / 100 for k in ("x1", "x2", "y1", "y2"))
            page = int(crop["page_number"])
        except (KeyError, TypeError, ValueError):
            return None
        x1, x2, y1, y2 = max(0.0, min(x1, x2)), min(1.0, max(x1, x2)), max(0.0, min(y1, y2)), min(1.0, max(y1, y2))
        regions.append({"page": page, "region": {"x": round(x1, 6), "y": round(y1, 6), "width": round(x2 - x1, 6), "height": round(y2 - y1, 6)},
                        "privacy_masks": []})
    if not regions:
        return None
    try:
        return locations.validate_regions(sorted(regions, key=lambda r: (r["page"], r["region"]["y"], r["region"]["x"])), pages)
    except ValueError:
        return None


# Gradescope's bubble-sheet scoring types (`mc_autograder_scoring_type`), each Faculty's rule of the same name (`bubble_keys`).
SHEET_SCORING = {"exact": "exact", "partial": "partial", "either": "either"}


def bubble_questions(outline):
    """A bubble-sheet version's answer key as its Gradescope outline holds it: each question with correct letters (its number from
    its title, else its place), the letters, its points and its scoring. Questions Gradescope has no answer for are not part of
    the key; letters past E, an unknown scoring type, odd points or a number used twice are problems that stop the import."""
    found, problems = [], []
    for question in sorted(outline.get("questions") or [], key=lambda q: int(q.get("index") or 0)):
        data = question.get("data") if isinstance(question.get("data"), dict) else {}
        letters = data.get("mc_autograder_correct_answer_letters")
        if not isinstance(letters, list) or not letters:
            continue
        title = str(question.get("title") or "").strip()
        number = int(printed_from_title(title) or question.get("index") or 0)
        answer = "".join(sorted({str(letter).strip().upper() for letter in letters}))
        scoring = SHEET_SCORING.get(str(data.get("mc_autograder_scoring_type") or ""))
        try:
            points = bubble_keys.parse_points(str(question.get("weight") or "0"), title or f"Question {number}")
            bubble_keys.parse_answer(answer, title or f"Question {number}")
            if not scoring:
                raise ValueError(f"{title or f'Question {number}'} uses Gradescope scoring “{data.get('mc_autograder_scoring_type')}”, which Faculty does not score.")
            if not 1 <= number <= bubble_keys.QUESTIONS:
                raise ValueError(f"{title or 'A question'} has no question number Faculty's bubble sheets read.")
        except ValueError as error:
            problems.append(str(error))
            continue
        found.append({"number": number, "answer": answer, "points": points, "scoring": scoring})
    numbers = [q["number"] for q in found]
    if len(set(numbers)) != len(numbers):
        problems.append("Two of its questions have the same number.")
    return found, problems


def exam_stem(title):
    """What an exam's written and bubble-sheet assignments share in their titles: "Common Exam 2 - Partial Credit" and "Common Exam
    2 - Multiple Choice" are both "common exam 2" (requirement, 2026-10-25)."""
    words = re.sub(r"\b(?:partial\s+credit|multiple\s+choice|free\s+response|bubble\s+sheets?|written|frq|mc|version\s+[a-z])\b", " ",
                   str(title or "").casefold())
    return " ".join(re.sub(r"[^a-z0-9]+", " ", words).split())


def sheet_key(versions):
    """The key as `save_bubble_key` takes it: the most usual points and scoring as its defaults, kept on a question only where it
    differs from them."""
    pairs = [(q["points"], q["scoring"]) for v in versions for q in v["questions"]]
    points, scoring = max(set(pairs), key=lambda pair: (pairs.count(pair), pair)) if pairs else ("1", "exact")
    return {"defaults": {"points": points, "scoring": scoring},
            "versions": [{"letter": v["letter"], "questions": [
                {"number": q["number"], "answer": q["answer"], **({"points": q["points"]} if q["points"] != points else {}),
                 **({"scoring": q["scoring"]} if q["scoring"] != scoring else {})} for q in v["questions"]]} for v in versions]}


class RubricImportMixin:
    def _rubric_import_read(self, aid, data, session=None):
        """Gradescope as the import sees it: the courses, the chosen (or proposed) course's assignments and the chosen (or proposed)
        assignment's questions per version, with the sources the import would upload and, from each version's outline, its template
        and each question's boxes. Reads Gradescope; writes nothing. `session`: the caller's, kept open."""
        data = data if isinstance(data, dict) else {}
        with self.rosters.lock(), connect(self.path) as db:
            plan = self.frq.plan(db, aid)
            targets = self._targets(db, plan)
            term = self.frq.exams._event(db, plan["event_id"]).get("term_label") or ""
            existing = {}
            for (qid,) in db.execute("SELECT question_id FROM frq_assignment_questions WHERE assignment_id=?", (aid,)).fetchall():
                question = self.frq.question(db, aid, qid)
                printed = str((question.get("content") or {}).get("printed_number") or "").strip()
                if printed and question.get("rubric"):
                    existing.setdefault(question.get("exam_version") or "", set()).add(printed)
            key = self.frq.bubble_key(db, aid)
        view = {"assignment_id": aid, **self.settings.status(), "title": plan.get("title") or "", "courses": [], "course_id": "",
                "assignments": [], "gradescope_assignment_id": "", "versions": [], "reason": "", "bubble": None, "sheet_id": "", "companion": None,
                "key_revision": key["revision"] if key else None}
        if not view["configured"]:
            view["reason"] = "Gradescope is not set up for this Faculty."
            return view
        own, session = session is None, session or self.session_factory()
        try:
            courses = [c for c in session.courses() if not gw.forbidden(c["short"], c["name"])]
            # The coordinator's course is listed too, last, and only READ (GRADESCOPE "THE COORDINATOR'S COURSE", 2026-10-25): its
            # rubrics are the ones every instructor copies. It is never proposed: a section links only to its own course.
            coordinator = session.coordinator_courses()
            view["courses"] = [{"id": c["id"], "short": c["short"], "name": c["name"], "term": c["term"], "coordinator": False} for c in courses] + [
                {"id": c["id"], "short": c["short"], "name": c["name"], "term": c["term"], "coordinator": True} for c in coordinator]
            # The first section's linked course, else the course Faculty would propose for it (its number, this term), as a send does.
            from .gradescope_sync import containers, propose_course
            linked = [t["course_id"] for t in targets if t["course_id"] in {c["id"] for c in courses}]
            proposed = [found["id"] for found in (propose_course(courses, t["section"], term) for t in targets) if found]
            course_id = str(data.get("course_id") or next(iter(linked + proposed), ""))
            read = course_id in {c["id"] for c in coordinator}
            if course_id and not read and course_id not in {c["id"] for c in courses}:
                raise Conflict("That Gradescope course is not one of your courses.")
            view["course_id"] = course_id
            if not course_id:
                view["reason"] = "Choose the Gradescope course the rubrics are in."
                return view
            rows = session.assignments(course_id, coordinator_read=read)
            choices = containers(rows)
            # Bubble-sheet exams too (2026-10-25: "import the bubble sheet key from there as well"): their key, not a rubric.
            sheets = [{"id": r["id"], "title": r["title"], "container": r["kind"] == "assignment_container"} for r in rows
                      if r["bubble_sheet"] and not r["parent"] and r["kind"] in ("assignment", "assignment_container")]
            view["assignments"] = [{"id": c["id"], "title": c["title"], "versions": sorted(k for k in c["versions"] if k), "bubble_sheet": False}
                                   for c in choices] + [{"id": c["id"], "title": c["title"], "versions": [], "bubble_sheet": True} for c in sheets]
            chosen = str(data.get("gradescope_assignment_id") or "")
            if not chosen:
                saved = next((t["container_id"] for t in targets if t["course_id"] == course_id and t["container_id"]), "")
                alike = [c["id"] for c in choices + sheets if gw.normalized(c["title"]) == gw.normalized(plan.get("title") or "")]
                chosen = saved if saved in {c["id"] for c in choices} else (alike[0] if len(alike) == 1 else "")
            container = next((c for c in choices if c["id"] == chosen), None)
            sheet = next((c for c in sheets if c["id"] == chosen), None)
            if sheet:
                view["gradescope_assignment_id"] = chosen
                view["bubble"] = self._sheet_key_read(session, course_id, sheet, read)
                return view
            if chosen and not container:
                raise Conflict("That Gradescope assignment is no longer in the course; choose it again.")
            view["gradescope_assignment_id"] = chosen
            if not container:
                view["reason"] = "Choose the Gradescope assignment whose rubrics to import."
                return view
            # The exam's bubble-sheet half comes with it (2026-10-25): the one bubble-sheet exam titled as this one but for its part
            # ("… Multiple Choice" beside "… Partial Credit") is proposed; the instructor may choose another or none (`sheet_id` "").
            if "sheet_id" in data:
                paired = str(data.get("sheet_id") or "")
            else:
                alike = [c["id"] for c in sheets if exam_stem(c["title"]) == exam_stem(container["title"]) and exam_stem(c["title"])]
                paired = alike[0] if len(alike) == 1 else ""
            companion = next((c for c in sheets if c["id"] == paired), None)
            if paired and not companion:
                raise Conflict("That bubble-sheet exam is no longer in the course; choose it again.")
            if companion:
                view["companion"] = {"id": companion["id"], "title": companion["title"], **self._sheet_key_read(session, course_id, companion, read)}
                if view["companion"]["problems"] and "sheet_id" not in data:
                    view["companion"], paired = None, ""  # only a key that imports is proposed; a chosen one shows why it cannot
            view["sheet_id"] = paired
            for letter, assignment_id in sorted(container["versions"].items()):
                rubric = session.rubric(course_id, assignment_id, coordinator_read=read)
                questions = rubric_questions(rubric)
                outline = session.outline(course_id, assignment_id, coordinator_read=read)
                crops = {str(q.get("id")): q.get("crop_rect_list") for q in outline["questions"]}
                ready = outline["template_status"] == "complete" and bool(outline["template_url"]) and outline["pages"] > 0
                for entry in questions:
                    entry["in_faculty"] = entry["printed_number"] in existing.get(letter, set())
                    entry["regions"] = crop_regions(crops.get(entry["id"]), outline["pages"]) if ready else None
                    entry["areas"] = len(entry["regions"] or [])
                view["versions"].append({"letter": letter, "gradescope_id": assignment_id, "questions": questions,
                                         "title": container["title"] + (f" Version {letter}" if letter else ""),
                                         "template": {"pages": outline["pages"], "filename": outline["template_filename"], "ready": ready},
                                         "_outline": outline})
        except gw.GradescopeError as error:
            raise Conflict(f"Gradescope could not be read: {error}") from None
        finally:
            if own:
                session.close()
        return view

    @staticmethod
    def _sheet_key_read(session, course_id, sheet, read):
        """A bubble-sheet exam's key per version: a set's versions from its Edit Exam Version Set page (no student on it), each
        lettered by its own title ("… Version B"; Gradescope's "?" version, where unread sheets go, is left out), else the one
        assignment as Version A. Reads only."""
        if sheet["container"]:
            outlines = [session.outline(course_id, a, coordinator_read=read)
                        for a in session.version_set(course_id, sheet["id"], coordinator_read=read)["selected"]]
            lettered = [(m.group(1).upper(), o) for o in outlines for m in [re.search(r"version\s+([a-z])\s*$", o["title"], re.I)] if m]
        else:
            lettered = [("A", session.outline(course_id, sheet["id"], coordinator_read=read))]
        versions, problems = [], []
        for letter, outline in sorted(lettered, key=lambda pair: pair[0]):
            questions, found = bubble_questions(outline)
            problems += [f"Version {letter}: {p}" for p in found]
            versions.append({"letter": letter, "questions": questions})
        letters = [v["letter"] for v in versions]
        if len(set(letters)) != len(letters) or letters != [chr(65 + i) for i in range(len(letters))]:
            problems.append(f"Faculty's key has versions A, B, C … in order; this exam's are {', '.join(letters) or 'none'}.")
        if not any(v["questions"] for v in versions):
            problems.append("It has no answer key on Gradescope yet.")
        return {"versions": versions, "problems": problems}

    def bubble_key_import(self, aid, data):
        """Saves the chosen Gradescope bubble-sheet exam's key as this plan's answer key (`save_bubble_key`: a new revision of the
        whole key, as the key editor's Save; `base_revision` is the revision the dialog showed, 409 when another save came first).
        Gradescope is read again first and only read."""
        if not isinstance(data, dict):
            raise ValueError("Supply a JSON object.")
        request_id = identifier(data.get("request_id"))
        course_id, assignment_id = str(data.get("course_id") or ""), str(data.get("gradescope_assignment_id") or "")
        if not re.fullmatch(r"\d{1,20}", course_id) or not re.fullmatch(r"\d{1,20}", assignment_id):
            raise ValueError("Choose the Gradescope course and bubble-sheet exam.")
        view = self._rubric_import_read(aid, {"course_id": course_id, "gradescope_assignment_id": assignment_id})
        bubble = view["bubble"]
        if view["gradescope_assignment_id"] != assignment_id or not bubble:
            raise Conflict(view["reason"] or "That is not a bubble-sheet exam on Gradescope; choose it again.")
        return {"assignment_id": aid, **self._save_sheet_key(aid, request_id, bubble, data.get("base_revision"))}

    def _save_sheet_key(self, aid, request_id, bubble, base_revision):
        """The key read from Gradescope saved through the key editor's own Save (`save_bubble_key`)."""
        if bubble["problems"]:
            raise Conflict(" ".join(bubble["problems"]))
        saved = self.frq.save_bubble_key(aid, {"request_id": "gradescope:" + request_id, "base_revision": base_revision,
                                               **sheet_key(bubble["versions"])})
        return {"key_revision": (saved.get("key") or {}).get("revision"), "versions": {v["letter"]: len(v["questions"]) for v in bubble["versions"]}}

    def rubric_import_preview(self, aid, data=None):
        """What the dialog shows: the course and assignment pickers and each question with what importing it would give."""
        view = self._rubric_import_read(aid, data)
        for version in view["versions"]:
            version.pop("_outline", None)
            for entry in version["questions"]:
                entry.pop("source", None)
                entry.pop("regions", None)
                entry["importable"] = not entry["reason"]
        return view

    def rubric_import(self, aid, data):
        """Imports the chosen questions' rubrics, reading Gradescope again first. Each question is its own upload with its own result
        (as each file of a CSV upload is), keyed by the request and the Gradescope question, so Retry repeats nothing."""
        if not isinstance(data, dict):
            raise ValueError("Supply a JSON object.")
        request_id = identifier(data.get("request_id"))
        if len(request_id) > 80:
            raise ValueError("Invalid identifier.")
        course_id, assignment_id = str(data.get("course_id") or ""), str(data.get("gradescope_assignment_id") or "")
        if not re.fullmatch(r"\d{1,20}", course_id) or not re.fullmatch(r"\d{1,20}", assignment_id):
            raise ValueError("Choose the Gradescope course and assignment.")
        wanted, sheet_id = data.get("questions"), data.get("sheet_id", "")
        if not isinstance(sheet_id, str) or (sheet_id and not re.fullmatch(r"\d{1,20}", sheet_id)):
            raise ValueError("Choose the bubble-sheet exam, or none.")
        if not isinstance(wanted, list) or (not wanted and not sheet_id) or len(wanted) > QUESTIONS_AT_ONCE or not all(isinstance(q, str) for q in wanted):
            raise ValueError("Choose the Gradescope questions to import.")
        document_id = data.get("assignment_version_id") or None
        if document_id is not None and not isinstance(document_id, str):
            raise ValueError("Choose the printed document version for these rubrics.")
        # A Gradescope assignment without versions describes the version the instructor names, as an uploaded CSV does.
        plain_letter = exam_letter(data.get("exam_version"))
        with_areas = data.get("areas", True)
        if not isinstance(with_areas, bool):
            raise ValueError("Say areas as true or false.")
        session = self.session_factory()
        try:
            view = self._rubric_import_read(aid, {"course_id": course_id, "gradescope_assignment_id": assignment_id, "sheet_id": sheet_id}, session)
            if view["gradescope_assignment_id"] != assignment_id:
                raise Conflict(view["reason"] or "That Gradescope assignment could not be read; choose it again.")
            found = {entry["id"]: (version, entry) for version in view["versions"] for entry in version["questions"]}
            missing = [q for q in wanted if q not in found]
            if missing:
                raise Conflict("Some chosen questions are no longer on that Gradescope assignment; open the import again.")
            templates = {}
            if with_areas:
                for version in view["versions"]:
                    if any(found[q][0] is version and found[q][1]["regions"] for q in wanted):
                        templates[version["letter"]] = self._gradescope_template(aid, course_id, version, document_id, session)
        finally:
            session.close()
        results = []
        for qid in wanted:
            version, entry = found[qid]
            letter = version["letter"] or plain_letter
            result = {"id": qid, "title": entry["title"], "exam_version": letter, "printed_number": entry["printed_number"]}
            results.append(result)
            if entry["reason"]:
                result.update(status="failed", error=entry["reason"])
                continue
            form = {"request_id": f"{request_id}:{qid}", "assignment_version_id": document_id, **({"exam_version": letter} if letter else {})}
            template = templates.get(version["letter"]) or {}
            areas = {"template_id": template["id"], "regions": entry["regions"]} if template.get("id") and entry["regions"] else None
            try:
                done = self.frq.upload_rubric(aid, entry["filename"], entry["source"].encode(), form, areas=areas)
            except (ValueError, Conflict) as error:
                result.update(status="failed", error=getattr(error, "description", None) or str(error))
                continue
            result.update(status=done["status"], question_id=done["question_id"], max_points=done.get("max_points"),
                          printed_number=done.get("printed_number", entry["printed_number"]),
                          areas=len(areas["regions"]) if areas and done["status"] == "imported" else 0)
        notes = [f"{v['title']}: {templates[v['letter']]['note']}" for v in view["versions"] if (templates.get(v["letter"]) or {}).get("note")]
        # The exam's bubble-sheet key, when the instructor kept it ticked: saved as the key editor's Save would, after the rubrics.
        key = None
        if view["companion"]:
            try:
                key = {"status": "imported", "title": view["companion"]["title"],
                       **self._save_sheet_key(aid, request_id, view["companion"], data.get("base_revision"))}
            except (ValueError, Conflict) as error:
                key = {"status": "failed", "title": view["companion"]["title"], "error": getattr(error, "description", None) or str(error)}
        return {"assignment_id": aid, "course_id": course_id, "gradescope_assignment_id": assignment_id, "results": results, "notes": notes, "key": key,
                "templates": sorted({t["id"] for t in templates.values() if t.get("id")}),
                "imported": sum(r["status"] == "imported" for r in results), "failed": sum(r["status"] == "failed" for r in results)}

    def _gradescope_template(self, aid, course_id, version, document_id, session):
        """The version's Gradescope template as one of the assignment's Assignment Templates (the ordinary template upload: the
        assignment's course-wide templates, converted and kept by the storage manager), or a note saying why its boxes are not
        imported. Only a template whose pages are the printed document's (as the answer-area check requires) is useful for them.
        The same template file is uploaded once: its request ID names the Gradescope template and the file's checksum."""
        outline = version["_outline"]
        with self.rosters.lock(), connect(self.path) as db:
            plan = self.frq.plan(db, aid)
            documents = db.execute("SELECT id,page_count,compiler_json FROM assignment_versions WHERE assignment_id=? ORDER BY created_at",
                                   (plan["source_assignment_id"],)).fetchall()
            labels = {r[0] for r in db.execute("SELECT label FROM assignment_templates WHERE assignment_id=?", (plan["source_assignment_id"],))}
        document = next((d for d in documents if d["id"] == document_id), None) if document_id else (documents[0] if len(documents) == 1 else None)
        if not document:
            return {"note": "choose the printed document to import the answer areas."}
        stamps_only = json.loads(document["compiler_json"]).get("generator") == "faculty-stamps-only"
        if not stamps_only and document["page_count"] != outline["pages"]:
            return {"note": f"its Gradescope template has {outline['pages']} pages and the printed document {document['page_count']}, "
                            "so its answer areas were not imported."}
        try:
            pdf = session.template_pdf(outline)
        except gw.GradescopeError as error:
            return {"note": f"its template could not be read, so its answer areas were not imported ({error})."}
        request = str(uuid.uuid5(uuid.NAMESPACE_URL, "faculty:gradescope-template:" + json.dumps(
            [course_id, version["gradescope_id"], outline["template_id"], hashlib.sha256(pdf).hexdigest()])))
        with connect(self.path) as db:
            kept = db.execute("SELECT id FROM assignment_templates WHERE request_id=?", (request,)).fetchone()
        if kept:
            return {"id": kept[0]}
        label = base = f"{version['title'][:130]} · Gradescope template"
        suffix = 2
        while label in labels:
            label, suffix = f"{base} ({suffix})", suffix + 1
        try:
            template = self.frq.templates.upload(plan["source_assignment_id"], label, request, {"gradescope-template.pdf": pdf})
        except (ValueError, Conflict) as error:
            return {"note": f"its template could not be saved, so its answer areas were not imported ({getattr(error, 'description', None) or error})."}
        return {"id": template["id"]}
