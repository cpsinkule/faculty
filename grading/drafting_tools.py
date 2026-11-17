"""What a model drafting or checking rubrics may call on a Claude or ChatGPT subscription connection (`providers/tools.py`; instructor,
2026-10-01: "go ahead and build the quiz drafting, rubric drafting, and question bank changes you recommended", then "give the rubric
check the same tools too").

The rubric writers (`rubric_generation.py`: one problem, every version at once, and adapting one version's rubric; `quiz_drafts.py`:
a drafted quiz's rubrics) and the rubric check (`rubric_quality.py`: one question, a problem's versions together, and resolving its
concerns) get read-only CONTEXT of the course the rubric is for (its course definition): a closer look at the printed pages, the
course's current lecture notes (COURSE-NOTES), its question bank (QUESTION-BANK) and the rubrics the instructor approved for its other
assignments, in any term. The surveys get only the closer look. Questions of the assignment being drafted or checked are never offered
(`assignment_id`): a draft is the model's own, never a copy of what it may replace, and a check judges these rubrics, not their earlier
revisions. Everything is read from the catalog and the definition's verified storage as the pages read it; nothing is written, and the
department's rubric rules in the prompts still decide what a rubric holds. The quiz writer (`quiz_drafts.py`) reads the same lecture
notes and question bank (requirement, 2026-10-01). A drafted quiz's rubric writers are also SENT the
text of the notes sections the quiz covers with every request, on every connection (`quiz_notes`), so that no rubric is built on a
method the students were not taught. The sections the instructor CHOSE for a draft from its new-draft window (`notes_sections`;
requirement, 2026-10-28) are sent to the quiz writer itself with every request that writes the quiz, and join
the sections the quiz names for its rubric writers (`chosen_notes`, `quiz_notes`). The question bank's questions filed under those
sections go with them, as models the writer must change, never copy (`chosen_bank`; instructor, the same day: "We need to make sure
it doesn't just copy those questions, it needs to change them slightly").
"""
import io
import json
import re

from rosters import connect

from .providers import tools

NOTES_PAGES = 6          # pages of lecture notes per call
NOTES_TEXT = 80000       # characters of lecture notes sent with each rubric writer's request of a drafted quiz (3 MATH 140 sections: ~20,000)
READ_PAGES = 12          # pages one sandbox `text` read takes (media.TEXT_PAGES)
SECTION_NUMBER = re.compile(r"(?<![\d.])(\d{1,2})\.(\d{1,2})(?![\d.])")
SECTION_RANGE = re.compile(r"(?<![\d.])(\d{1,2})\.(\d{1,2})\s*(?:-{1,3}|–|—|to|through|thru)\s*(\d{1,2})\.(\d{1,2})(?![\d.])", re.I)
CHAPTERS = re.compile(r"\bchapters?\s+(\d{1,2}(?:\s*(?:,|and|&|-|–)\s*\d{1,2})*)", re.I)
MATH_LETTERS = re.compile(r"([\U0001D400-\U0001D7FF])\1*")
STATEMENT = 1500         # characters of a statement, solution or answer key shown per match
BANK_PER_SECTION = 6     # bank questions sent to the quiz writer for each chosen notes section
BANK_TEXT = 40000        # characters of bank questions sent to the quiz writer in all
DIFFICULTY = {"medium": 0, "hard": 1, "easy": 2}
LOOK_SIDE = 1600         # the long side of a closer look, in pixels; never enlarged more than twice
REGION_MIN = .05         # the smallest region, as a share of the page's width and height
STOP = frozenset("the and for with that this from are was were has have its into each find show what which when where then than their there these "
                 "those your you use using given let all any how not but per can will would should may must one two three problem part".split())
REGION = {"type": "object", "description": "Optional: the part of the page to enlarge, as shares of its width and height from its top left corner.",
          "properties": {k: {"type": "number", "minimum": 0, "maximum": 1} for k in ("left", "top", "right", "bottom")},
          "required": ["left", "top", "right", "bottom"]}


def words(value):
    return {word for word in re.findall(r"[a-z][a-z0-9]{2,}", str(value).casefold()) if word not in STOP}


def closer(png, region=None):
    """A page picture, or a region of it, as JPEG with its long side at most LOOK_SIDE (a small region enlarged at most twice)."""
    from PIL import Image
    with Image.open(io.BytesIO(png)) as image:
        image = image.convert("RGB")
        if region is not None:
            width, height = image.size
            image = image.crop((round(region["left"] * width), round(region["top"] * height), round(region["right"] * width), round(region["bottom"] * height)))
        scale = min(2.0, LOOK_SIDE / max(image.size))
        if scale != 1:
            image = image.resize((max(1, round(image.width * scale)), max(1, round(image.height * scale))), Image.LANCZOS)
        out = io.BytesIO()
        image.save(out, "JPEG", quality=85, optimize=True)
        return out.getvalue()


def region_of(value):
    if value is None:
        return None
    if not isinstance(value, dict) or any(isinstance(value.get(k), bool) or not isinstance(value.get(k), (int, float)) or not 0 <= value[k] <= 1
                                          for k in ("left", "top", "right", "bottom")):
        raise tools.ToolError("A region gives left, top, right and bottom as shares of the page from 0 to 1.")
    if value["right"] - value["left"] < REGION_MIN or value["bottom"] - value["top"] < REGION_MIN:
        raise tools.ToolError(f"A region is at least {REGION_MIN:.0%} of the page wide and high, right of its left and below its top.")
    return value


def _cut(value, limit=STATEMENT):
    value = " ".join(str(value or "").split())
    return value if len(value) <= limit else value[:limit] + " …"


class TemplatePages:
    """A blank template's page pictures as `look_closer` reads them: each page only when the model asks for it."""

    def __init__(self, templates, template_id):
        self.templates, self.template_id = templates, template_id
        self.count = templates.get(template_id)['page_count']

    def __len__(self):
        return self.count

    def __getitem__(self, index):
        if not 0 <= index < self.count:
            raise IndexError(index)
        return self.templates.read(self.template_id, 'image', index + 1)[0]


def look_closer_tool(pages, what):
    """`pages`: {version: [PNG of each page]} or `TemplatePages` (one version's key may be ""). `what` names the pages for the model."""
    names = sorted(pages)

    def look(arguments):
        version = arguments.get("version") if len(names) > 1 else names[0]
        if version not in pages:
            raise tools.ToolError(f"Name one version: {', '.join(names)}.")
        page = arguments.get("page")
        if type(page) is not int or not 1 <= page <= len(pages[version]):
            raise tools.ToolError(f"Choose a page from 1 to {len(pages[version])}.")
        region = region_of(arguments.get("region"))
        label = (f"Version {version}, " if version else "") + f"page {page}" + (" (the region asked for)" if region else "")
        return label + ".", [closer(pages[version][page - 1], region)]
    properties = {"page": {"type": "integer", "minimum": 1}, "region": REGION}
    if len(names) > 1:
        properties["version"] = {"type": "string", "enum": names}
    return tools.Tool("look_closer", f"A sharper picture of one page of {what}, or of a region of it enlarged: small print, exponents, signs, "
                      "graphs and tables you could not read for certain in the pictures you were sent.",
                      {"type": "object", "properties": properties, "required": ["page", *(["version"] if len(names) > 1 else [])]}, look)


def notes_tool(grading, definition_id):
    def notes(arguments):
        service = getattr(getattr(grading, "question_bank", None), "notes", None)
        current = service.view(definition_id)["current"] if service else None
        if not current:
            raise tools.ToolError("This course has no lecture notes in Faculty.")
        sections = [part for part in current["parts"] if part["kind"] == "section"]
        wanted = str(arguments.get("section") or "").strip()
        if not wanted:
            return "\n".join([f"{current['title']}: {len(sections)} sections. Ask for one by its number to read it."] +
                             [f"{p['number']} {p['title']}" + (f" (chapter {p['chapter']}: {p['chapter_title']})" if p["chapter_title"] else "")
                              + f", {p['pages']} page{'s' if p['pages'] != 1 else ''}"
                              + (f"; topics: {'; '.join(t['title'] if isinstance(t, dict) else str(t) for t in p['topics'][:12])}" if p["topics"] else "")
                              for p in sections])
        part = next((p for p in sections if p["number"] == wanted.removeprefix("Section ").strip()), None)
        if not part:
            raise tools.ToolError(f"No section {wanted} in the notes. Call lecture_notes without a section for the list.")
        start = arguments.get("page") or 1
        if type(start) is not int or not 1 <= start <= part["pages"]:
            raise tools.ToolError(f"Section {part['number']} has pages 1 to {part['pages']}.")
        from document_identity import process
        pdf, _, _ = service.read(current["id"], part["position"])
        last = min(part["pages"], start + NOTES_PAGES - 1)
        read = process.run("text", pdf, {"pages": list(range(start, last + 1))})
        more = f" Ask for page {last + 1} to read on." if last < part["pages"] else ""
        return "\n\n".join([f"Section {part['number']} {part['title']}, pages {start}-{last} of {part['pages']}.{more}"] +
                           [f"[page {p['page']}]\n{p['text'].strip()}" for p in read["pages"]])
    return tools.Tool("lecture_notes", "The course's lecture notes: without a section, the list of sections with their topics; with a section "
                      "number (\"2.3\"), its text, a few pages at a time. Use it to match the methods, notation and wording the course teaches.",
                      {"type": "object", "properties": {"section": {"type": "string"}, "page": {"type": "integer", "minimum": 1}}}, notes)


def covered_sections(sections, mentions):
    """The notes sections a quiz covers, in the notes' order: each section its mentions name ("Sections 3.1–3.3", "Section 3.2",
    "3.1, 3.2 and 3.4", "Chapter 3"), a range taking every section between its ends. `mentions`: the quiz's sections line and its
    problems' tags, what the quiz itself prints."""
    named, ranges, chapters = set(), [], set()
    for mention in mentions:
        mention = str(mention or "")
        ranges += [((int(m[1]), int(m[2])), (int(m[3]), int(m[4]))) for m in SECTION_RANGE.finditer(mention)]
        named |= {(int(m[1]), int(m[2])) for m in SECTION_NUMBER.finditer(mention)}
        for m in CHAPTERS.finditer(mention):
            chapters |= {int(n) for n in re.findall(r"\d+", m[1])}
            chapters |= {n for a, b in re.findall(r"(\d+)\s*[-–]\s*(\d+)", m[1]) for n in range(int(a), int(b) + 1)}
    found = []
    for part in sections:
        number = SECTION_NUMBER.fullmatch(str(part["number"]))
        if number and ((key := (int(number[1]), int(number[2]))) in named or key[0] in chapters or any(lo <= key <= hi for lo, hi in ranges)):
            found.append(part)
    return found


def undoubled(text):
    """Word's equations in a notes PDF read every math letter twice ("𝑃𝑃 = 4𝑥𝑥 + 3𝑦𝑦": all 3,457 runs of the MATH 140 notes); halve
    the runs when every run of a text is doubled, else leave the text as it is."""
    runs = [len(m[0]) for m in MATH_LETTERS.finditer(text)]
    if not runs or any(n % 2 for n in runs):
        return text
    return MATH_LETTERS.sub(lambda m: m[0][:len(m[0]) // 2], text)


def quiz_notes(grading, definition_id, mentions, chosen=()):
    """(notes, warning) for every rubric writer of a drafted quiz: the text of the course's lecture-notes sections the quiz covers
    (requirement, 2026-10-01), on every connection, tools or not, with the sections the
    instructor chose for the draft (`chosen`, numbers). `notes` is None, and the warning says why, when the course has no notes,
    nothing in them matches what the quiz covers, or they cannot be read; at most NOTES_TEXT characters are sent, and the warning
    names what was left out."""
    current = _current_notes(grading, definition_id)
    if not current:
        return None, "The rubrics were drafted without lecture notes: this course has none in Faculty."
    sections = [part for part in current["parts"] if part["kind"] == "section"]
    covered = {part["number"] for part in covered_sections(sections, mentions)} | set(chosen)
    picked = [part for part in sections if part["number"] in covered]
    if not picked:
        said = "; ".join(str(m) for m in mentions if m) or "nothing"
        return None, f"The rubrics were drafted without lecture notes: no section of the notes matches what the quiz covers ({said})."
    return _notes_text(grading, current, picked, "The rubrics were drafted without lecture notes", "the rubric writers")


def chosen_notes(grading, definition_id, numbers):
    """(notes, warning) for the quiz writer: the text of the lecture-notes sections the instructor chose for the draft, by NUMBER
    resolved against the edition the course page shows now (COURSE-NOTES "NAMED FROM OUTSIDE BY NUMBER"), in the notes' order, at
    most NOTES_TEXT characters. (None, "") when none were chosen; a chosen section the notes no longer hold is named in the warning."""
    if not numbers:
        return None, ""
    current = _current_notes(grading, definition_id)
    if not current:
        return None, "The quiz was written without the lecture notes you chose: this course has none in Faculty now."
    sections = [part for part in current["parts"] if part["kind"] == "section"]
    picked = [part for part in sections if part["number"] in set(numbers)]
    gone = [n for n in numbers if n not in {part["number"] for part in picked}]
    if not picked:
        return None, f"The quiz was written without the lecture notes you chose: the notes have no section {', '.join(gone)} now."
    notes, warning = _notes_text(grading, current, picked, "The quiz was written without the lecture notes you chose", "the quiz writer")
    if notes and gone:
        warning = " ".join(filter(None, [warning, f"The notes have no section {', '.join(gone)} now, so the quiz writer was not sent it."]))
    return notes, warning


def _current_notes(grading, definition_id):
    service = getattr(getattr(grading, "question_bank", None), "notes", None)
    return service.view(definition_id)["current"] if service else None


def _notes_text(grading, current, chosen, without, reader):
    """The text of `chosen` notes parts, read in the document sandbox, at most NOTES_TEXT characters in all."""
    from werkzeug.exceptions import NotFound
    from document_identity import process
    service = grading.question_bank.notes
    sent, room, left_out = [], NOTES_TEXT, []
    try:
        for part in chosen:
            pdf, _, _ = service.read(current["id"], part["position"])
            pages = []
            for start in range(1, part["pages"] + 1, READ_PAGES):
                pages += process.run("text", pdf, {"pages": list(range(start, min(part["pages"], start + READ_PAGES - 1) + 1))})["pages"]
            text, last = "", 0
            for page in pages:
                block = f"[page {page['page']}]\n" + "\n".join(line.rstrip() for line in page["text"].strip().splitlines() if line.strip())
                if len(text) + len(block) + 2 > room:
                    break
                text, last = text + ("\n\n" if text else "") + block, page["page"]
            if last < part["pages"]:
                left_out.append(f"{part['number']} from page {last + 1}" if last else part["number"])
            if text:
                text = undoubled(text)
                room -= len(text)
                sent.append({"number": part["number"], "title": part["title"], "pages": part["pages"], "text": text})
    except (ValueError, OSError, NotFound) as failure:
        return None, f"{without}: they could not be read ({failure})."
    if not sent:
        return None, f"{without}: the sections {'the quiz covers' if reader == 'the rubric writers' else 'you chose'} have no text to read."
    notes = {"title": current["title"], "edition": current["id"], "sections": sent}
    return notes, (f"The lecture notes sent to {reader} were cut at {NOTES_TEXT:,} characters; left out: section "
                   f"{', '.join(left_out)}." if left_out else "")


def chosen_bank(grading, definition_id, numbers):
    """(bank, warning) for the quiz writer: questions of the course's question bank (QUESTION-BANK) filed under each lecture-notes
    section the instructor chose for the draft (a question tagged with the section, or a topic or objective under it), at most
    BANK_PER_SECTION a section and BANK_TEXT characters in all. A section's questions printed on the course's past assessments come
    first, then medium, hard and easy ones, newest first, the kinds taken in turn; a question filed under two sections is sent once, and
    one version of a problem printed in several (its linked versions, or its family). Retired questions never. (None, "") when no section was chosen or the bank holds none of theirs."""
    service = getattr(grading, "question_bank", None)
    if not numbers or service is None:
        return None, ""
    with connect(grading.path) as db:
        questions = [q for q in service._latest(db, definition_id) if not q["retired"] and q["classification"]]
        outline = service._outline(db, definition_id)
        service._sources(db, questions)
    parents = {node["id"]: node["parent_id"] for node in outline}
    numbered = {node["id"]: node["number"] for node in outline if node["level"] == "section" and node["number"]}
    filed = {number_: [] for number_ in numbers}
    for q in questions:
        under = set()
        for node in json.loads(q["classification"]["nodes_json"] or "[]"):
            seen = set()
            while node and node not in seen:
                seen.add(node)
                if node in numbered:
                    under.add(numbered[node])
                node = parents.get(node)
        first = next((n for n in numbers if n in under), None)
        if first:
            filed[first].append(q)
    sent, room, used = [], BANK_TEXT, set()
    for number_ in numbers:
        ranked = sorted(filed[number_], key=lambda q: q["revised_at"], reverse=True)
        ranked.sort(key=lambda q: (not (q["frq_question_id"] or q.get("printed_link")), DIFFICULTY.get((q["classification"] or {}).get("difficulty"), 3)))
        kinds = {}
        for q in ranked:
            kinds.setdefault(q["content"].get("kind") or "", []).append(q)
        turns = [q for group in zip(*[iter(v) for v in kinds.values()]) for q in group] if len(kinds) > 1 else ranked
        taken = {q["id"] for q in turns}
        turns += [q for q in ranked if q["id"] not in taken]
        chosen = []
        for q in turns:
            if len(chosen) >= BANK_PER_SECTION:
                break
            same = (q.get("group") or {}).get("group_id") or q.get("family") or q["id"]
            if same in used:
                continue
            shown = bank_question(q)
            size = len(json.dumps(shown, ensure_ascii=False))
            if size > room:
                continue
            room -= size
            used.add(same)
            chosen.append(shown)
        if chosen:
            sent.append({"number": number_, "available": len(filed[number_]), "questions": chosen})
    if not sent:
        return None, ""
    short = [s["number"] for s in sent if s["available"] > len(s["questions"]) and len(s["questions"]) < BANK_PER_SECTION]
    return {"sections": sent}, (f"The question bank questions sent to the quiz writer were cut at {BANK_TEXT:,} characters; fewer were sent "
                                f"for section {', '.join(short)}." if short else "")


def bank_question(q):
    """One bank question as the quiz writer reads it."""
    c, tags = q["content"], q["classification"] or {}
    shown = {"source": q.get("source_label") or "", "difficulty": tags.get("difficulty") or "", "kind": c.get("kind") or "",
             "statement": _cut(c["statement"], 3000)}
    if c.get("figure"):
        shown["figure"] = _cut(c["figure"], 600)
    if c["parts"]:
        shown["parts"] = [{"label": p["label"], "text": _cut(p["text"], 800), **({"points": p["points"]} if p["points"] is not None else {})} for p in c["parts"]]
    if c["choices"]:
        shown["choices"] = [_cut(choice, 300) for choice in c["choices"]]
        if c["correct_choice"]:
            shown["correct_choice"] = c["correct_choice"]
    if c["final_answers"]:
        shown["final_answers"] = [{"part": a["part"], "answer": _cut(a["answer"], 300)} for a in c["final_answers"]]
    if c["solution"]:
        shown["solution"] = _cut(c["solution"])
    return shown


def bank_tool(grading, definition_id, assignment_id):
    def bank(arguments):
        service = getattr(grading, "question_bank", None)
        query, limit = words(arguments.get("query")), arguments.get("limit") or 4
        if not query:
            raise tools.ToolError("Say in a few words what the problem is about (its topic, method or wording).")
        if type(limit) is not int or not 1 <= limit <= 8:
            raise tools.ToolError("Ask for 1 to 8 questions.")
        if service is None:
            raise tools.ToolError("The question bank is not available here.")
        with connect(grading.path) as db:
            questions = [q for q in service._latest(db, definition_id) if not q["retired"]]
            service._sources(db, questions)
            outline = {node["id"]: node for node in service._outline(db, definition_id)}
        scored = []
        for q in questions:
            if assignment_id and q.get("assignment_id") == assignment_id:
                continue
            c = q["content"]
            found = len(query & words(" ".join([c["statement"], *(p["text"] for p in c["parts"]), *c["choices"], c["solution"]])))
            if found:
                scored.append((found, q["revised_at"], q))
        scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
        if not scored:
            return "No question in this course's bank matches those words."
        shown = []
        for index, (_, _, q) in enumerate(scored[:limit], 1):
            c, tags = q["content"], q["classification"] or {}
            under = [outline[n]["title"] if not outline[n]["number"] else f"{outline[n]['number']} {outline[n]['title']}"
                     for n in json.loads(tags.get("nodes_json") or "[]") if n in outline]
            lines = [f"[{index}] {q['source_label']}" + (f" · {tags['difficulty']}" if tags.get("difficulty") else "") + (f" · under {'; '.join(under[:4])}" if under else ""),
                     f"Statement: {_cut(c['statement'])}"]
            lines += [f"Part {p['label']}" + (f" ({p['points']:g} points)" if p["points"] is not None else "") + f": {_cut(p['text'], 500)}" for p in c["parts"]]
            lines += [f"({chr(97 + i)}) {_cut(choice, 300)}" for i, choice in enumerate(c["choices"])]
            if c["correct_choice"]:
                lines.append(f"Correct choice: ({c['correct_choice']})")
            if c["final_answers"]:
                lines.append("Final answers: " + "; ".join(f"{a['part'] + ': ' if a['part'] else ''}{_cut(a['answer'], 300)}" for a in c["final_answers"]))
            if c["solution"]:
                lines.append(f"Solution: {_cut(c['solution'])}")
            shown.append("\n".join(lines))
        return f"{len(scored)} question{'s' if len(scored) != 1 else ''} matched; the closest {len(shown)}:\n\n" + "\n\n".join(shown)
    return tools.Tool("question_bank", "Questions of this course's question bank that match a few words, with their final answers and "
                      "solutions: how the course has asked and solved similar problems.",
                      {"type": "object", "properties": {"query": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 8}},
                       "required": ["query"]}, bank)


def rubrics_tool(grading, definition_id, assignment_id):
    def rubrics(arguments):
        query, limit = words(arguments.get("query")), arguments.get("limit") or 3
        if not query:
            raise tools.ToolError("Say in a few words what the problem is about (its topic, method or wording).")
        if type(limit) is not int or not 1 <= limit <= 6:
            raise tools.ToolError("Ask for 1 to 6 rubrics.")
        with connect(grading.path) as db:
            rows = [dict(row) for row in db.execute("""SELECT q.id AS question_id, q.exam_version, a.id AS assignment_id, a.title, rr.revision,
                    rr.rules_json, ap.created_at AS approved_at,
                    (SELECT content_json FROM frq_question_revisions WHERE question_id=q.id ORDER BY revision DESC LIMIT 1) AS content_json
                FROM frq_rubric_revisions rr JOIN frq_rubric_approvals ap ON ap.rubric_id=rr.id JOIN frq_questions q ON q.id=rr.question_id
                JOIN assignment_versions v ON v.id=q.assignment_version_id JOIN assignments a ON a.id=v.assignment_id
                WHERE a.definition_id=? AND a.id IS NOT ?""", (definition_id, assignment_id))]
        latest = {}
        for row in rows:  # the newest approved revision of each question's rubric
            if row["question_id"] not in latest or row["revision"] > latest[row["question_id"]]["revision"]:
                latest[row["question_id"]] = row
        scored = []
        for row in latest.values():
            content, rules = json.loads(row["content_json"] or "{}"), json.loads(row["rules_json"])
            found = len(query & words(" ".join([str(content.get("problem") or ""), str(content.get("title") or ""),
                                                *(item["description"] for item in rules["items"])])))
            if found:
                scored.append((found, row["approved_at"], row, content, rules))
        scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
        if not scored:
            return "No approved rubric of this course matches those words."
        shown = []
        for index, (_, _, row, content, rules) in enumerate(scored[:limit], 1):
            number = content.get("printed_number") or content.get("title") or ""
            head = " · ".join(filter(None, [row["title"], f"Version {row['exam_version']}" if row["exam_version"] else "", f"Problem {number}" if number else ""]))
            points = content.get("max_points")
            lines = [f"[{index}] {head}" + (f" ({points:g} points" if isinstance(points, (int, float)) else " (") + f", {rules.get('mode', 'negative')} scoring)"]
            if content.get("problem"):
                lines.append(f"Problem: {_cut(content['problem'])}")
            if content.get("answer_key"):
                lines.append(f"Answer key: {_cut(content['answer_key'] if isinstance(content['answer_key'], str) else json.dumps(content['answer_key'], ensure_ascii=False), 600)}")
            lines += [f"  {item['id']} ({item['points']:+g}): {_cut(item['description'], 600)}" for item in rules["items"]]
            if rules.get("notes"):
                lines.append(f"Notes: {_cut(rules['notes'], 600)}")
            shown.append("\n".join(lines))
        return (f"{len(scored)} approved rubric{'s' if len(scored) != 1 else ''} matched; the closest {len(shown)} (the instructor's approved "
                f"lines, for their style and how points were split, never values to copy):\n\n" + "\n\n".join(shown))
    return tools.Tool("past_rubrics", "Rubrics the instructor approved for this course's other assignments (any term) whose problem or lines "
                      "match a few words: how they split points, phrase lines and treat methods and partial work.",
                      {"type": "object", "properties": {"query": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 6}},
                       "required": ["query"]}, rubrics)


def rubric_tools(grading, endpoint, definition_id, pages, *, assignment_id=None, survey=False, reviewer=False):
    """The ToolSet of a rubric survey (`survey`: the closer look only), writer, or check (`reviewer`), or None when the connection runs
    no tools. `pages`: {version: pages} of the blank assignment the problems are printed on (none: no closer look)."""
    if not tools.offered(endpoint):
        return None
    look = [look_closer_tool(pages, "the blank assignment" if survey else "the printed problem's assignment")] if pages else []
    if survey:
        return tools.ToolSet("rubric-survey-v1", look, budget=6, guide=(
            "If small print, an exponent, a sign, a graph or a table on a page is unclear in the pictures, look closer at that page or "
            "region with look_closer before you list it."))
    context = [*look, notes_tool(grading, definition_id), bank_tool(grading, definition_id, assignment_id), rubrics_tool(grading, definition_id, assignment_id)]
    if reviewer:
        return tools.ToolSet("rubric-reviewer-v1", context, budget=10, guide=(
            "You may " + ("look closer at the blank pages these problems are printed on (look_closer), " if look else "") + "read the course's "
            "lecture notes (lecture_notes), and see how this course's question bank (question_bank) and the instructor's approved rubrics for "
            "other assignments (past_rubrics) treat similar problems. Use them to judge whether these rubrics follow the methods and notation "
            "the course teaches and the way the instructor grades similar work; these instructions still decide what you report, and every "
            "line you name is a line of the rubric in front of you."))
    return tools.ToolSet("rubric-writer-v1", context, budget=10, guide=(
        "You may look closer at the printed pages (look_closer), read the course's lecture notes (lecture_notes), and see how this "
        "course's question bank (question_bank) and the instructor's approved rubrics for other assignments (past_rubrics) treat similar "
        "problems. Use them to follow the methods, notation and line style the course already uses; the department's rubric rules in "
        "these instructions still decide, and every value, answer and point in your lines is this problem's own."))


def definition_of(db, assignment_id):
    row = db.execute("SELECT definition_id FROM assignments WHERE id=?", (assignment_id,)).fetchone()
    return row["definition_id"] if row else None
