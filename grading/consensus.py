"""Conservative structural TeX alignment. Never infer a corrected answer."""
import difflib
import re


TOKEN = re.compile(r"\\[A-Za-z]+|\\.|\d|[A-Za-z]|[^\s]", re.UNICODE)


def tokens(value):
    value = value.replace("−", "-").replace("≤", r"\leq").replace("≥", r"\geq")
    value = re.sub(r"\\(?:left|right|displaystyle|textstyle)\b", "", value)
    value = re.sub(r"\\(?:,|;|!|quad\b|qquad\b)|\$", "", value)
    value = re.sub(r"\\le\b", r"\\leq", value)
    value = re.sub(r"\\ge\b", r"\\geq", value)
    # Expand single-token TeX arguments. Braces and operator structure survive.
    value = re.sub(r"([_^])([A-Za-z0-9])", r"\1{\2}", value)
    value = re.sub(r"\\frac\s*([A-Za-z0-9])\s*([A-Za-z0-9])", r"\\frac{\1}{\2}", value)
    value = re.sub(r"(?<=\d)\s*\\cdot\s*(?=[A-Za-z])", "", value)
    return TOKEN.findall(value)


def comparable(transcript):
    """One reading as the token source for a comparison, derived the SAME way for every provider: from its retained elements.

    A whole-transcript `latex` or `plain_text` is whatever the provider chose to send, and either may be a stub. Mathpix's page
    `latex` was one symbol beside 528 characters of text (measured 2026-09-21, 201 of 240 responses), and because this function
    preferred `latex`, every visual-reader-versus-OCR comparison reported the reader's whole reading as one huge difference, on
    every response, with nothing real ever surfacing. The elements are what both providers actually retain, so they compare.
    Tokens ignore whitespace, so joining the parts with a space yields exactly the per-element tokens concatenated, which keeps
    the location mapping below exact rather than guessed.
    """
    parts = [element.get("latex") or element.get("text", "") for line in transcript["lines"] for element in line["elements"]]
    return " ".join(part for part in parts if part) or transcript.get("latex") or transcript.get("plain_text") or ""


def align(candidates):
    disagreements, comparisons, differing = [], 0, 0
    if len(candidates) < 2:
        return {"agreement": None, "disagreements": [], "providers": len(candidates)}
    first = candidates[0]
    base = tokens(comparable(first["transcript"]))
    locations_by_candidate = {}

    def location_ranges(index, value, whole):
        if index not in locations_by_candidate:
            position, flattened, ranges = 0, [], []
            for line in value["transcript"]["lines"]:
                for element in line["elements"]:
                    symbols = tokens(element.get("latex") or element.get("text", ""))
                    ranges.append((position, position + len(symbols), line["id"], element))
                    flattened.extend(symbols)
                    position += len(symbols)
            # GRADING: never assign pixel geometry based on a guessed token offset. PERFORMANCE: this map is local to one
            # comparison and is built once per reading, instead of tokenizing every element again for every disagreement.
            locations_by_candidate[index] = ranges if flattened == whole else []
        return locations_by_candidate[index]

    for index, candidate in enumerate(candidates[1:], 1):
        other = tokens(comparable(candidate["transcript"]))
        comparisons += max(len(base), len(other))
        for operation, a, b, c, d in difflib.SequenceMatcher(None, base, other, autojunk=False).get_opcodes():
            if operation == "equal":
                continue
            differing += max(b-a, d-c)
            locations = []
            for reading_index, value, start, end, whole in ((0, first, a, b, base), (index, candidate, c, d, other)):
                locations.extend({"provider": value["provider"], "line_id": lid, "bbox": element["bbox"], "crop": element.get("crop", 0)}
                    for begin, stop, lid, element in location_ranges(reading_index, value, whole)
                    if begin < max(end, start+1) and stop > start and element.get("bbox"))
            disagreements.append({"providers": [first["provider"], candidate["provider"]],
                "first_span": [a, b], "second_span": [c, d], "first": base[a:b], "second": other[c:d],
                "grading_critical": True, "locations": locations, "bbox": locations[0]["bbox"] if locations else None})
    return {"agreement": 1 - differing / max(1, comparisons), "disagreements": disagreements, "providers": len(candidates)}
