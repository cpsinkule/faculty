"""Untrusted printed headings and page areas; no inferred score or approval."""
import difflib
import re

from .models import box, items, number, text

READER = "template-locations-v3"
# Part of a check's input, not of the page-reading profile: changing how areas
# are proposed recomputes checks from retained page receipts without new OCR.
AREAS = "heading-to-next-problem-v1"
# Also part of a check's input: which pages' own notes can hold a question for review (`combine`).
NOTES = "own-pages-v1"
LABEL = re.compile(r"(?:question|problem|q)?\s*([0-9]{1,4})(?!\d)(?:\s*[.)]|(?=\s|$))", re.I)
POINTS = re.compile(r"\(?\[?\s*(\d+(?:\.\d+)?)\s*(?:points?|pts?\.?)\s*\]?\)?", re.I)


def printed_number(value):
    value = text(value, "Printed question number", 30).strip()
    if not value:
        return ""
    if not re.fullmatch(r"\d{1,4}", value) or int(value) < 1:
        raise ValueError("Use the printed whole question number, without a part letter.")
    return str(int(value))


def validate_regions(value, page_count):
    regions = []
    for area in items(value, "Answer areas", 20):
        if not isinstance(area, dict) or type(area.get("page")) is not int or not 1 <= area["page"] <= page_count:
            raise ValueError("Question page is outside the selected document.")
        rect = area.get("region")
        if not isinstance(rect, dict) or set(rect) != {"x", "y", "width", "height"}:
            raise ValueError("Answer areas need x, y, width and height.")
        rect = {k: number(v, "Region", 0, 1) for k, v in rect.items()}
        # Tiny binary rounding from converting right/bottom to width/height
        # must not admit meaningful overflow or reject an exact page edge.
        if rect["x"] + rect["width"] > 1 + 1e-9 or rect["y"] + rect["height"] > 1 + 1e-9 or rect["width"] <= 0 or rect["height"] <= 0:
            raise ValueError("Answer areas must have positive size and stay on the page.")
        rect["width"], rect["height"] = min(rect["width"], 1-rect["x"]), min(rect["height"], 1-rect["y"])
        if rect["width"] <= 0 or rect["height"] <= 0:
            raise ValueError("Answer areas must have positive size.")
        masks = [box(v) for v in items(area.get("privacy_masks", []), "Privacy masks", 30)]
        if None in masks:
            raise ValueError("A privacy mask must be a rectangle.")
        regions.append({"page": area["page"], "region": rect, "privacy_masks": masks})
    if not regions:
        raise ValueError("Define at least one answer area.")
    return regions


def hint(question, filename=""):
    explicit = question["content"].get("printed_number", "")
    if explicit:
        return printed_number(explicit), "question setting"
    match = re.search(r"(?:question|problem)[ _-]+(\d{1,4})(?!\d)", filename, re.I)
    named = match[1] if match else misspelt(filename)
    if named:
        return str(int(named)), "rubric filename"
    match = re.search(r"\b(?:question|problem)\s+(\d{1,4})(?!\d)", question["content"]["title"], re.I)
    if not match:
        match = re.fullmatch(r"q?(\d{1,4})", question["question_key"], re.I)
    return (str(int(match[1])), "question label") if match else ("", "unavailable")


def misspelt(filename):
    """A misspelt "question" or "problem" before the number in a rubric filename ("8_Quesiton_25.csv" on Exam 2 Version C,
    2026-10-25) still names the question; a number after any other word ("Points_4", "Part_2") does not."""
    for match in re.finditer(r"(?<![a-z])([a-z]{5,10})[ _-]+(\d{1,4})(?!\d)", filename, re.I):
        if any(difflib.SequenceMatcher(None, match[1].casefold(), word).ratio() >= .75 for word in ("question", "problem")):
            return match[2]
    return ""


def point_value(value):
    """Only an explicit adjacent total, never a sum of part-credit values."""
    value = text(value, "Printed point text", 100).strip()
    match = POINTS.fullmatch(value)
    if not match:
        match = re.fullmatch(r"[([]\s*(\d+(?:\.\d+)?)\s*[)\]]", value)
    return number(float(match[1]), "Printed total", 0, 10000) if match else None


def ocr_totals(ocr, question_number):
    # The number and score must share a heading, optionally split across lines.
    heading = re.compile(r"(?im)^\s*(?:#{1,6}\s*)?(?:\*\*)?(?:(?:question|problem)\s+)?"
        + re.escape(question_number) + r"(?:[.)]|(?=\s|\())\s*(?:\*\*)?\s*"
        + r"(\(?\[?\s*\d+(?:\.\d+)?\s*(?:points?|pts?\.?)\s*\]?\)?|[([]\s*\d+(?:\.\d+)?\s*[)\]])", re.I)
    return [point_value(match[1]) for match in heading.finditer(ocr)]


def _words(value):
    """Letters and digits only: the reader and the OCR spell maths delimiters, spacing and punctuation differently."""
    return re.sub(r"[^0-9a-z]", "", value.lower())


# How much of a heading's opening is compared: enough to be that problem's own words, and no more, since the two
# readings may spell a formula further on differently.
OPENING = (16, 40)


def ocr_heading(ocr, question_number, label):
    """Whether the OCR prints this number as a heading that begins with the words of the reader's unnumbered heading."""
    wanted = _words(label)[:OPENING[1]]
    if len(wanted) < OPENING[0]:
        return False
    heading = re.compile(r"(?im)^\s*(?:#{1,6}\s*)?(?:\*\*)?(?:(?:question|problem)\s+)?" + re.escape(question_number) + r"(?:[.)]|(?=\s))")
    for match in heading.finditer(ocr):
        rest = ocr[match.end():match.end()+1000]
        # "18. (6 points) Write ..." may be quoted with or without its total.
        total = re.match(r"\s*(?:\*\*)?\s*(?:\(?\[?\s*\d+(?:\.\d+)?\s*(?:points?|pts?\.?)\s*\]?\)?|[([]\s*\d+(?:\.\d+)?\s*[)\]])", rest, re.I)
        if _words(rest).startswith(wanted) or (total and _words(rest[total.end():]).startswith(wanted)):
            return True
    return False


def problem_text(ocr, question_number):
    """The printed statement from this question's heading to the next numbered heading. Context for the grader; it decides nothing."""
    heading = re.compile(r"(?m)^\s*(?:#{1,6}\s*)?(?:\*\*)?(?:(?:question|problem)\s+)?(\d{1,4})(?:[.)]|(?=\s*\())")
    starts = [(match.start(), int(match[1])) for match in heading.finditer(ocr)]
    for index, (start, found) in enumerate(starts):
        if str(found) == question_number:
            # A numbered list inside the statement restarts low; only a later printed question ends it.
            end = next((position for position, number_ in starts[index+1:] if number_ > found), len(ocr))
            return ocr[start:end].strip()[:20000]
    return ""


def page_result(output, ocr):
    if not isinstance(output, dict):
        raise ValueError("Invalid template location output.")
    entries = []
    for item in items(output.get("questions"), "Printed questions", 100):
        if not isinstance(item, dict):
            raise ValueError("Invalid printed question.")
        qnumber = printed_number(item.get("number"))
        if not qnumber:
            raise ValueError("Printed question number is missing.")
        label = text(item.get("label"), "Printed heading", 1000, False)
        match = LABEL.match(label.strip())
        # A heading the reader quoted without its number (2026-09-25: "Using technology, solve ..." for problem 4 of a
        # real blank, which failed every question of that template) stands only where the independent OCR prints
        # that very number as a heading followed by the same words. A heading naming another number never does.
        number_from = "" if match else "ocr_heading" if ocr_heading(ocr, qnumber, label) else None
        if number_from is None or (match and str(int(match[1])) != qnumber):
            raise ValueError("The location does not quote its printed question number.")
        if type(item.get("continued")) is not bool:
            raise ValueError("A continuation must be explicitly identified.")
        # The visual provider's explicit contract uses integer thousandths of
        # the page. Never guess a coordinate scale from the returned values.
        def rectangle(value):
            if not isinstance(value, list) or len(value) != 4 or any(type(v) is not int or not 0 <= v <= 1000 for v in value):
                raise ValueError("Location rectangles must use integer page coordinates from 0 to 1000.")
            return box([v/1000 for v in value])
        header, region = rectangle(item.get("header_bbox")), rectangle(item.get("response_bbox"))
        if header is None or region is None:
            raise ValueError("Question headings and answer areas need valid page geometry.")
        confidence = number(item.get("confidence"), "Location confidence", 0, 1)
        points_text = text(item.get("points_text", ""), "Printed point text", 100)
        entries.append({"number": qnumber, "label": label, "points_text": points_text,
            "printed_points": point_value(points_text), "ocr_totals": ocr_totals(ocr, qnumber),
            "header_bbox": header, "response_bbox": region, "continued": item["continued"], "confidence": confidence,
            "warnings": [text(v, "Question warning", 2000) for v in items(item.get("warnings", []), "Question warnings", 30)],
            **({"number_from": number_from} if number_from else {})})
    return {"questions": entries, "warnings": [text(v, "Location warning", 2000) for v in items(output.get("warnings", []), "Location warnings", 30)]}


def combine(context, pages):
    found, reasons = [], list(context["issues"])
    regions, evidence = [], []
    for page, value in pages:
        if value.get("error"):
            reasons.append(f"Page {page}: {value['error']}")
            continue
        own = [item for item in value["questions"] if item["number"] == context["printed_number"]]
        # A page's own notes concern the questions on it (`NOTES`; requirement, 2026-09-25). A note on a
        # page holding none of this question stays in that page's reading; a question found nowhere is flagged for that anyway.
        if own:
            reasons.extend(f"Page {page}: {v}" for v in value["warnings"])
        for item in own:
            found.append((page, item))
            reasons.extend(f"Page {page}, question {item['number']}: {v}" for v in item.get("warnings", []))
            # Some visual readers separate the heading from the answer area.
            # Propose their union so the printed problem remains in the crop.
            header, response = item["header_bbox"], item["response_bbox"]
            x, y = min(header[0], response[0]), min(header[1], response[1])
            right, bottom = max(header[2], response[2]), max(header[3], response[3])
            # Students write in every gap: run from this heading to the next
            # printed problem, or the end of the page, across the whole sheet.
            # The space above the heading stays out; it may hold a name line.
            others = [o["header_bbox"] for o in value["questions"] if o["number"] != item["number"]]
            below = min((b[1] for b in others if b[1] > header[1]), default=1.0)
            if below > y:
                bottom = below
            # Headings sharing these rows mean columns; keep the reader's width.
            if not any(b[1] < bottom and b[3] > y for b in others):
                x, right = 0.0, 1.0
            regions.append({"page": page, "region": {"x": x, "y": y, "width": right-x, "height": bottom-y}, "privacy_masks": []})
            evidence.append({"page": page, **item})
            if item["confidence"] < .9:
                reasons.append(f"Page {page}: the printed heading or location is uncertain.")
    starts = [(p, i) for p, i in found if not i["continued"]]
    printed = None
    if len(starts) != 1:
        reasons.append("The printed question was not found exactly once; inspect its number and pages.")
    else:
        page, item = starts[0]
        printed = item["printed_points"]
        if printed is None or item["ocr_totals"] != [printed]:
            reasons.append("The independent readings did not agree on one point total next to this question number.")
        if printed is not None and printed != context["rubric_maximum"]:
            reasons.append(f"The printed total is {printed:g}; the rubric maximum is {context['rubric_maximum']:g}.")
        for other_page, other in found:
            if other["continued"] and (other_page <= page or (other["printed_points"] is not None and other["printed_points"] != printed)):
                reasons.append("The continuation pages or their point totals conflict.")
    if len({p for p, _ in found}) != len(found):
        reasons.append("Several locations on the same page claim this question number.")
    ordered = sorted({p for p, _ in found})
    if ordered and ordered != list(range(ordered[0], ordered[-1]+1)):
        reasons.append("The suggested question skips pages; check the complete answer area.")
    imported = context.get("gradescope_regions")
    if imported:
        # GRADESCOPE "IMPORTING RUBRICS FROM GRADESCOPE": the instructor's own boxes from Gradescope's outline are the suggestion; the
        # reading still checks the printed number and points.
        regions = imported
    if not regions:
        reasons.append("No answer area could be suggested. Draw it on its page.")
    return {**({"regions_from": "gradescope"} if imported else {}), "regions": regions, "evidence": evidence, "printed_points": printed,
        "rubric_maximum": context["rubric_maximum"], "points_match": not reasons,
        "issues": list(dict.fromkeys(reasons)), "needs_manual_review": bool(reasons), "confirmation_required": True}
