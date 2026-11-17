"""JEV PAPERS (requirement, 2026-10-15): Jev looks at a
scanned page and says whether a student wrote their name on it, where the name and UIN are, and which roster student wrote them.
The local OCR keeps reading text; Jev decides which page is a first page and whose paper it is. Measured that day on the
instructor's challenge scan (100 sides, 50 one-page quizzes scanned two-sided, some without a Name or UIN line, one upside down)
and on graded scans (documents.md "JEV PAPERS" has the numbers).

Two checks, each a Jev receipt (`jev_calls`, purposes below) owned by the scan page:
- `page_check`: the whole page (Jev's IMAGE_MODEL): a handwritten name anywhere (noul), which way up the page is (choice), and a box
  around the handwritten name and one around the handwritten UIN.
- `identify`: the name, cut from its box with a margin, asked of each section's roster (a choice in both orders, `NONE` offered);
  every student any section picked is a candidate; then a NAME round (the name again, among the candidates only, surname included)
  and a UIN round (the UIN, cut the same way, among the candidates' roster UINs). One round decides when both its orders agree on a
  candidate; the paper is that student's when one round decides and the other does not name someone else.
Receipts keep enrollment IDs and counts, never roster names or UINs (`receipt`). Nothing here writes teaching records: the caller
keeps the result in its own immutable reading, and its own rules decide what an identification may do.

GEMINI MATCHES THE STUDENT (requirement, 2026-11-12): the three requests of `identify` are asked of Gemini Decisions when it is set up (`jev.service.provider_for`), with the
same questions, crops, rounds and rule; `page_check` stays Jev's, since only Jev answers a Box. A result Gemini gave says so
(`reader`), and what the instructor reads names it. Replayed that day through this code on 119 first pages the app had read:
the student named on 118, none wrong (Jev had named 110 of them, the same students); with a page's own student taken off the
roster, nobody named on 30 of 30.
"""
import hashlib
import io
import json

from jev import client
from jev.service import both, distinct_labels, fingerprint, noul, provider_for

RULE = "jev-papers-v1"
PAGE_PURPOSE = "paper-first-page"
STUDENT_PURPOSE = "paper-student"
ASSIGNMENT_PURPOSE = "paper-assignment"
NO_ASSIGNMENT = "None of these assignments"
WHICH_ASSIGNMENT = ("The image is a scanned page of a student\u2019s paper. Which of the course\u2019s assignments is this page from? Read what is "
                    "printed on it (title, sections, problems). Faculty\u2019s names for assignments may differ from the printed title. Choose "
                    "\"" + NO_ASSIGNMENT + "\" when it is none of them.")
DESCRIBED = 300   # characters of an assignment's blank first page shown beside its name (measured 2026-10-15 with the first 300)
# A handwritten name at or above this makes a first page. Measured 2026-10-15: first pages >= .97, written later pages <= .16; .9 until
# 2026-10-17, when a take-home first page with its name and UIN written in the top corner read .70 and fell out of its paper. Replayed
# on every page Jev looked at then (472 of three scans): first pages .93-.99 and that one .70, written later pages at most .37
# (an upside-down back), blank backs at most .19.
FIRST_PAGE = .5
MARGIN = .02      # added on every side of a name or UIN box before it is cut (Jev's boxes clip a last letter on about 1 in 6)
MAX_OPTIONS = 254  # a Choice holds at most 255 options; one is NONE
NONE = "None of these students"
NO_UIN = "No UIN is written, or it matches none of these"
ORIENTATIONS = {
    "upright": "The printed text reads normally, top of the page at the top.",
    "upside down": "The page is turned 180 degrees: printed text is upside down.",
    "turned left": "The page is turned 90 degrees so printed lines run bottom to top.",
    "turned right": "The page is turned 90 degrees so printed lines run top to bottom.",
    "mirrored": "The printed text appears mirror-reversed (for example the back of the sheet showing through).",
    "blank": "No printed quiz and no handwriting: an empty page or the empty back of a sheet.",
}
NAME_ON_ROSTER = ("The image is a crop of a scanned quiz or exam page showing the name a student handwrote. Which listed student wrote "
                  "their name here? The roster lists legal names as \"Last, First Middle\"; a student may write first name first, a "
                  "nickname or preferred first name, or only part of the name. Choose \"" + NONE + "\" when no listed student fits.")
NAME_ROUND = ("The image is a crop of a scanned quiz or exam page showing the name a student handwrote. Which listed student wrote their "
              "name here? Names are legal names, \"Last, First Middle\"; a student may write first name first, a preferred first name, "
              "or only part of the name. Compare the whole name, surname included. Choose \"" + NONE + "\" when none fits.")
UIN_ROUND = ("The image is a crop of a scanned quiz or exam page where a student wrote their 9-digit UIN. Which of these UINs is written? "
             "Compare every digit. Choose \"" + NO_UIN + "\" when no UIN is written or none matches.")


def _kept_choice(answer, ids):
    """A choice as a receipt keeps it: the enrollment ID chosen (or "none"), and its confidence; never the option's words."""
    return {"type": "choice", "choice": ids.get(answer["choice"], "none"), "confidence": answer["confidence"]}


def _redacted(questions, ids):
    """The questions as their receipt keeps them: each one's kind, instructions and option count, and a hash of the options."""
    return {name: {"type": q["type"], "instructions": q["instructions"], "options": len(q["criteria"]),
                   "options_sha256": hashlib.sha256(json.dumps([ids.get(o, o) for o in q["criteria"]]).encode()).hexdigest()}
            for name, q in questions.items()}


def _receipt(questions, ids):
    return {"questions": _redacted(questions, ids),
            "result": lambda result: {**result, "answers": {name: _kept_choice(answer, ids) for name, answer in result["answers"].items()}}}


def _png(image):
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


def page_check(jev, owner, picture):
    """{status, first_page, has_name, orientation, name_box, uin_box, receipt} for one scanned page (`picture`: its raster). `status` is
    the receipt's: anything but "complete" decides nothing, and the caller keeps its own reading."""
    questions = {
        "orientation": {"type": "choice", "instructions": "How does this scanned page appear in the image?", "criteria": ORIENTATIONS},
        "has_name": noul("Is there a student's handwritten name anywhere on this page?"),
        "name": {"type": "box", "instructions": "Find the box around the student's handwritten name on this page, wherever it is written "
                 "(on the printed \"Name\" line or anywhere else, such as a top corner), not the printed word \"Name\"."},
        "uin": {"type": "box", "instructions": "Find the box around the student's handwritten UIN (a 9-digit university ID number) on "
                "this page, wherever it is written, not the printed word \"UIN\"."},
    }
    answer = jev.ask(PAGE_PURPOSE, owner, "A scanned page of a student's quiz or exam.", questions, RULE, images=[client.prepare_image(picture)])
    if answer.get("status") != "complete":
        return {"status": answer.get("status") or "failed", "receipt": answer.get("id")}
    found = answer["answers"]
    has_name = found["has_name"]["noul"]
    check = {"status": "complete", "receipt": answer["id"], "has_name": round(has_name, 3), "first_page": has_name >= FIRST_PAGE,
             "orientation": found["orientation"]["choice"], "name_box": found["name"]["box"], "uin_box": found["uin"]["box"],
             "name_confidence": found["name"]["confidence"], "uin_confidence": found["uin"]["confidence"]}
    if check["orientation"] == "upside down" and not check["first_page"]:
        # UPSIDE DOWN (2026-10-17: four take-home first pages scanned upside down read .21 to .74 and fell out of their papers): Jev
        # sees which way up the page is but reads a name poorly upside down, so it is asked again with the page turned upright. Its
        # boxes are turned back, so they stay boxes on the retained raster (`crop` turns the cut part upright).
        turned = jev.ask(PAGE_PURPOSE, owner, "A scanned page of a student's quiz or exam.", questions, RULE, images=[client.prepare_image(_turned(picture))])
        if turned.get("status") == "complete":
            again = turned["answers"]
            has_name = again["has_name"]["noul"]
            check.update(turned_receipt=turned["id"], has_name=round(has_name, 3), first_page=has_name >= FIRST_PAGE,
                         name_box=_turn_box(again["name"]["box"]), uin_box=_turn_box(again["uin"]["box"]),
                         name_confidence=again["name"]["confidence"], uin_confidence=again["uin"]["confidence"])
    return check


def _turned(picture):
    from PIL import Image
    return _png(Image.open(io.BytesIO(picture)).convert("L").rotate(180))


def _turn_box(box):
    """A box (fractions) on a page turned half a turn, as a box on the page as scanned."""
    if not box:
        return box
    return {**box, "left": round(1 - box["right"], 4), "right": round(1 - box["left"], 4), "top": round(1 - box["bottom"], 4), "bottom": round(1 - box["top"], 4)}


def crop(picture, box, turned=False):
    """The part of the page inside `box` (fractions) with MARGIN added, turned upright when the page is upside down."""
    from PIL import Image
    image = Image.open(io.BytesIO(picture)).convert("L")
    width, height = image.size
    part = image.crop((int(max(0, box["left"] - MARGIN) * width), int(max(0, box["top"] - MARGIN) * height),
                       int(min(1, box["right"] + MARGIN) * width), int(min(1, box["bottom"] + MARGIN) * height)))
    return _png(part.rotate(180) if turned else part)


def _agreed(answers, name):
    first, second = answers[name], answers[name + "__reversed"]
    return first["choice"] if first["choice"] == second["choice"] and first["choice"] != "none" else None


def _labels(students, keys):
    """Option words for these students: their roster names, made distinct where two share one."""
    keys = sorted(keys, key=lambda k: (students[k]["name"].casefold(), k))
    names = distinct_labels((students[key]["name"] or "Student" for key in keys), reserved=(NONE,))
    return dict(zip(names, keys))


def reader():
    """Who answers `identify` now: "Gemini" (GEMINI MATCHES THE STUDENT) or "Jev"."""
    return "Jev" if provider_for(STUDENT_PURPOSE) is client else "Gemini"


def identify(jev, owner, picture, check, students):
    """{status, enrollment_id (or None), candidates, name_round, uin_round, reason, reader (only when Gemini answered)}. `students`:
    enrollment ID -> {name, uin, section_id} (the roster the caller matches against). Only a complete page check with a name box is
    asked."""
    who = reader()
    found = _identify(jev, owner, picture, check, students, who)
    return found if who == "Jev" or found["status"] == "not_asked" else {**found, "reader": who}


def _identify(jev, owner, picture, check, students, who):
    if check.get("status") != "complete":
        return {"status": "not_asked", "enrollment_id": None, "reason": "The page was not checked."}
    turned = check.get("orientation") == "upside down"
    name_crop = client.prepare_image(crop(picture, check["name_box"], turned))
    sections = {}
    for key, student in students.items():
        sections.setdefault(student["section_id"], []).append(key)
    ids = _labels(students, students)  # distinct across every section, so one option's words name one student
    words = {key: label for label, key in ids.items()}
    questions = {}
    for number, (section, keys) in enumerate(sorted(sections.items())):
        keys = sorted(keys, key=lambda k: (words[k].casefold(), k))
        for part in range(0, len(keys), MAX_OPTIONS):
            questions.update(both(f"roster{number}_{part // MAX_OPTIONS}", NAME_ON_ROSTER, dict.fromkeys([*(words[k] for k in keys[part:part + MAX_OPTIONS]), NONE])))
    if not questions:
        return {"status": "complete", "enrollment_id": None, "candidates": [], "reason": "No roster students to match."}
    asked = jev.ask(STUDENT_PURPOSE, owner, "A handwritten name on a student's quiz or exam, to match to a class roster.", questions, RULE,
                    images=[name_crop], receipt=_receipt(questions, ids))
    if asked.get("status") != "complete":
        return {"status": asked.get("status") or "failed", "enrollment_id": None, "reason": f"{who} did not finish matching the name."}
    candidates = sorted({a["choice"] for a in asked["answers"].values() if a["choice"] in students})
    result = {"status": "complete", "enrollment_id": None, "candidates": candidates, "receipts": [asked["id"]]}
    if not candidates:
        return {**result, "reason": "No roster student's name fits."}
    labels = {words[key]: key for key in candidates}
    rounds = both("name", NAME_ROUND, dict.fromkeys([*labels, NONE]))
    named = jev.ask(STUDENT_PURPOSE, owner, "A handwritten name to match among a few students.", rounds, RULE,
                    images=[name_crop], receipt=_receipt(rounds, labels))
    uins = {students[key]["uin"]: key for key in candidates if students[key].get("uin")}
    uins = {uin: key for uin, key in uins.items() if sum(students[k].get("uin") == uin for k in candidates) == 1}
    numbered = {"status": "not_asked"}
    if uins:
        uin_crop = client.prepare_image(crop(picture, check["uin_box"], turned))
        uin_rounds = both("uin", UIN_ROUND, dict.fromkeys([*uins, NO_UIN]))
        numbered = jev.ask(STUDENT_PURPOSE, owner, "A handwritten UIN to match among a few students.", uin_rounds, RULE,
                           images=[uin_crop], receipt=_receipt(uin_rounds, uins))
    by_name = _agreed(named["answers"], "name") if named.get("status") == "complete" else None
    by_uin = _agreed(numbered["answers"], "uin") if numbered.get("status") == "complete" else None
    result.update(name_round=by_name, uin_round=by_uin,
                  receipts=[asked["id"], *(r["id"] for r in (named, numbered) if r.get("id"))])
    if by_name and by_uin and by_name != by_uin:
        return {**result, "reason": "The name and the UIN point to different students."}
    if by_name or by_uin:
        return {**result, "enrollment_id": by_name or by_uin}
    return {**result, "reason": "Neither the name nor the UIN settled one student."}


def describe(text):
    """An assignment's option text: how its blank paper begins (`DESCRIBED` characters of its first page), or that none is on file."""
    text = " ".join(str(text or "").split())[:DESCRIBED]
    return f'Its blank paper begins: "{text}"' if text else "No blank paper is on file yet."


def which_assignment(jev, owner, picture, assignments):
    """{status, assignment_id (None: none agreed), none (both orders chose NO_ASSIGNMENT), confidence}. `assignments`: [{id, title,
    description}] (the course's assignments; `describe`). JEV MIXED SCANS (requirement, 2026-10-15): asked in both orders; an
    assignment counts only when both choose it. Measured that day on 58 graded pages and 100 of an unknown quiz: with the blank
    paper's opening lines, 57 of 58 right and one split, never a wrong assignment, and the unknown quiz "None" on all 100."""
    if not assignments or len(assignments) > MAX_OPTIONS:
        return {"status": "not_asked", "assignment_id": None}
    ids, criteria = {}, {}
    ordered = sorted(assignments, key=lambda a: (a["title"].casefold(), a["id"]))
    labels = distinct_labels((item["title"] for item in ordered), reserved=(NO_ASSIGNMENT,), numbered_by_position=True)
    for item, words in zip(ordered, labels):
        ids[words], criteria[words] = item["id"], item.get("description") or describe("")
    criteria[NO_ASSIGNMENT] = None
    questions = both("assignment", WHICH_ASSIGNMENT, criteria)
    asked = jev.ask(ASSIGNMENT_PURPOSE, owner, "A scanned page of a student\u2019s math paper.", questions, RULE,
                    images=[client.prepare_image(picture)], receipt=_receipt(questions, ids))
    if asked.get("status") != "complete":
        return {"status": asked.get("status") or "failed", "assignment_id": None}
    first, second = asked["answers"]["assignment"], asked["answers"]["assignment__reversed"]
    agreed = first["choice"] if first["choice"] == second["choice"] else None
    return {"status": "complete", "receipt": asked["id"], "assignment_id": agreed if agreed not in (None, "none") else None,
            "none": agreed == "none", "confidence": min(first["confidence"], second["confidence"]) if agreed else 0}


def roster_key(students):
    """What `identify` depends on besides the page: each student's identity, section, name and UIN."""
    return fingerprint(sorted([key, s["section_id"], s["name"], s.get("uin", "")] for key, s in students.items()))
