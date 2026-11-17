"""Artificial handwriting-like marks, never student data or accuracy benchmarks."""
import io

CASES = [
    ("clean", "3x - 8 = 10\nx = 6"),
    ("messy", "3(x + 4) = 18\n3x + 4 = 18\nx = 14/3"),
    ("mixed", "Divide both sides by 3.\nx = 8"),
    ("fraction", r"\frac{x+1}{7} = \frac{1}{2}"),
    ("superscript", r"x^{2} - 0.5x < 7"),
    ("inequality", r"-2x \leq 6\nx \geq -3"),
    ("matrix", r"\begin{pmatrix}1&0\\-1&7\end{pmatrix}"),
    ("follow_through", "-2x = 6\nx = 3\nx + 1 = 4"),
    ("independent_errors", "2x - 3 = 7\n2x = 4\nx = 8"),
    ("ambiguous", "x = 1?\ny = 0?"),
]


def page(messy=False, template=False):
    from PIL import Image, ImageDraw
    image = Image.new("RGB", (800, 1000), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((65, 150, 735, 700), outline="black", width=3)
    draw.line((70, 100, 650, 100), fill="black", width=4)
    draw.text((75, 165), "Synthetic question: show your work", fill="black")
    if not template:
        for index, (_, text) in enumerate(CASES[:6]):
            draw.text((100 + (index % 3 if messy else 0), 215+index*62), text, fill=(90, 90, 90) if messy else "black")
        # Faint minus and decimal strokes must survive exact original cropping.
        draw.line((140, 670, 156, 670), fill=(190, 190, 190), width=1)
        draw.point((160, 673), fill="black")
    data = io.BytesIO()
    image.save(data, "PNG")
    return data.getvalue()


def confirm_for_review(frq, aid):
    """The instructor's confirmation of every rubric for the rubric reviewer (2026-09-24), as the page sends it."""
    readiness = frq.rubric_quality(aid)["readiness"]
    asked = {qid: {"rubric_id": r["rubric_id"], "comments_sha256": r["comments_sha256"]} for qid, r in readiness.items() if not r["ready"]}
    return frq.confirm_rubric_review(aid, {"questions": asked}) if asked else None


def finalize_rubrics(frq, aid, edit=None):
    """The mandatory rubric finalization, as a fixture: a scripted reviewer that proposes nothing, confirmed as it stands.

    Nothing is sent anywhere. Already confirmed for the current rubrics, it does nothing; `edit(questions)` may change the
    confirmed entries (pairs, clarifications) first. The built-in exclusions are the product's own and are never scripted.
    """
    import uuid
    from unittest.mock import patch
    status = frq.rubric_quality(aid)
    if status["state"] == "missing" or (status["state"] == "confirmed" and not edit):
        return status  # Another version still lacks its rubrics: the fixture finalizes once the last one is in.
    if not (status["check"] and status["check"]["current"] and status["check"]["status"] == "complete"):
        def reviewer(owner, role, endpoint, request, settings):
            payload, nothing = request["payload"], lambda rubric: [{"item_id": item["id"], "clarification": "", "excludes": [], "reason": ""} for item in rubric["items"]]
            if request["prompt"] == "rubric_quality_group":  # A plan with versions: the fixtures print each problem under one number on every version.
                groups = {}
                for question in payload["questions"]:
                    groups.setdefault(question["printed_number"] or question["title"], []).append(question["ref"])
                return {"metadata": {}, "raw": {}, "output": {"groups": [{"refs": refs} for refs in groups.values()], "notes": []}}
            if "problem" in payload:  # Every version of one problem at once.
                return {"metadata": {}, "raw": {}, "output": {"summary": "Nothing to change.", "suspected_errors": [], "consistency_issues": [],
                    "versions": [{"version": v["version"], "summary": "", "items": nothing(v["question"]["rubric"])} for v in payload["problem"]["versions"]]}}
            return {"metadata": {}, "raw": {}, "output": {"summary": "Nothing to change.", "suspected_errors": [], "items": nothing(payload["question"]["rubric"])}}
        # Reading a printed problem off the blank exam is the check's own test; a fixture needs neither the sandbox nor a template.
        confirm_for_review(frq, aid)
        status = frq.rubric_quality(aid)
        with patch.object(frq, "cloud_call", side_effect=reviewer), patch.object(frq, "rubric_problem_images", return_value=[]):
            frq.queue_rubric_quality(aid, {"request_id": str(uuid.uuid4()), "input_sha256": status["input_sha256"]})
            for _ in range(5):
                if frq.rubric_quality(aid)["state"] not in ("pending", "running"):
                    break
                frq.process_cloud()
        status = frq.rubric_quality(aid)
    questions = [dict(q) for q in (status["review"]["policy"] if status["review"] else status["check"]["result"])["questions"]]
    # The scripted reviewer pairs nothing. Where a fixture rubric's lines add up to more than its question, the instructor's part
    # is played here: every line of a still-open combination is an alternative of the others, until nothing exceeds the question.
    from grading.rubric_quality import heaviest_combination
    asked = {q["question_id"]: q for q in status["context"]["questions"]}
    for entry in questions:
        about = asked[entry["question_id"]]
        while (over := heaviest_combination(about["rubric"], entry, about["max_points"])):
            entry["pairs"] = [list(pair) for pair in sorted({*map(tuple, entry["pairs"]), *((a, b) for a in over["lines"] for b in over["lines"] if a < b)})]
    if edit:
        edit(questions)
    return frq.confirm_rubric_quality(aid, {"version": status["version"], "check_id": status["check"]["id"], "input_sha256": status["input_sha256"],
        "confirm_review": True, "questions": questions})


def keep_memory(path, definition_id, number=1, kind="rule", text="A value wrong only because of a mistake already charged is not charged again.", waiting=False):
    """A rubric memory of the course, kept by the instructor (GRADING "RUBRIC MEMORIES"), or only proposed (`waiting`)."""
    import uuid as _uuid
    from rosters import connect as _connect
    mid = str(_uuid.uuid4())
    with _connect(path) as db:
        db.execute("""INSERT INTO frq_rubric_memories(id,definition_id,number,kind,action,replaces_id,rule,covers,not_when,why,sources_json,run_id,created_at)
            VALUES(?,?,?,?,'new',NULL,?,'Any later line.','','','{"ids":[]}',NULL,'2020-01-01T00:00:00+00:00')""", (mid, definition_id, number, kind, text))
        if not waiting:
            db.execute("INSERT INTO frq_rubric_memory_decisions(id,memory_id,decision,actor,created_at) VALUES(?,?,'keep','workspace','2020-01-01T00:00:00+00:00')",
                       (str(_uuid.uuid4()), mid))
    return mid
