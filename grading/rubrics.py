"""Lossless rubric import and explicit rule validation; imports are drafts."""
import csv
import io
import json
import re

from .models import encoded, digest, identifier, items, number, text
from . import gradescope_import

# PART CAPS (GRADING "PART CAPS"; requirement, 2026-10-15). In a deducting rubric each lettered part's lines (wording that BEGINS "Part B:") are
# one line group, `part-b-cap`, whose floor is minus the part's worth, so scoring stops the part's deductions there. The floor is the
# only place a part's worth is kept: the drafters build the caps where they know it (`capped_parts`), and `validate` keeps every line
# in the cap its wording names.
PART_LINE = re.compile(r"\s*part\s+([a-h])\s*:", re.I)
PART_CAP = re.compile(r"part-([a-h])-cap")
PARTS_UNCAPPED = ("Part caps are not set: the parts' points are unknown or do not add up to the question's, so one part's deductions "
                  "can take points another part earned (GRADING \"PART CAPS\").")


def part_letter(description):
    found = PART_LINE.match(description or "")
    return found.group(1).lower() if found else None


def part_cap(group_id):
    """Whether a rubric group is a part's built-in cap."""
    return bool(PART_CAP.fullmatch(str(group_id or "")))


def part_worths(parts, stated=None):
    """{letter: worth} for a problem's lettered parts ([{label, points}]): each part's printed points, else the worth the drafter
    `stated` for it ({letter: points}); {} when a part has neither."""
    worths = {}
    for part in parts or []:
        label = str(part.get("label") or "").strip("() ").lower()
        worth = part.get("points") if part.get("points") is not None else (stated or {}).get(label)
        if not re.fullmatch(r"[a-h]", label) or isinstance(worth, bool) or not isinstance(worth, (int, float)) or worth <= 0:
            return {}
        worths[label] = float(worth)
    return worths


def part_caps_of(rules):
    """A rubric's part caps as a model reads them beside its lines: [{part, deductions_stop_at}] ([] when it has none)."""
    return [{"part": PART_CAP.fullmatch(g["id"]).group(1).upper(), "deductions_stop_at": -g["floor"]}
            for g in (rules or {}).get("groups") or [] if part_cap(g.get("id"))]


def capped_parts(value, worths, max_points):
    """The unvalidated rules `value` with every lettered part's lines in that part's cap. Unchanged for positive scoring or a rubric
    without part lines; when the parts' worths are unknown or do not add up to the question's points it stays uncapped, with a warning."""
    if value.get("mode", "negative") != "negative":
        return value
    lettered = {part_letter(i.get("description")) for i in value.get("items") or []} - {None}
    if not lettered or (len(worths) == 1 and lettered <= set(worths)):
        return value  # no part lines, or the one part is the whole question
    if len(worths) < 2 or lettered - set(worths) or abs(sum(worths.values()) - max_points) > 1e-9:
        return {**value, "warnings": [*(value.get("warnings") or []), PARTS_UNCAPPED]}
    lines = [{**i, "group": f"part-{part_letter(i.get('description'))}-cap"}
             if part_letter(i.get("description")) and (not i.get("group") or part_cap(i.get("group"))) else i for i in value["items"]]
    used = sorted({i["group"] for i in lines if part_cap(i.get("group"))})
    caps = [{"id": g, "description": f"Part {PART_CAP.fullmatch(g).group(1).upper()}", "selection": "multiple",
             "floor": -worths[PART_CAP.fullmatch(g).group(1)], "ceiling": 0} for g in used]
    return {**value, "items": lines, "groups": [g for g in value.get("groups") or [] if not part_cap(g.get("id"))] + caps}


def validate(value, max_points):
    if not isinstance(value, dict):
        raise ValueError("Supply a structured rubric object.")
    allowed = {"mode", "floor", "ceiling", "items", "groups", "notes", "warnings"}
    if set(value) - allowed:
        raise ValueError("Unsupported rubric fields: " + ", ".join(sorted(set(value) - allowed)))
    mode = value.get("mode", "negative")
    if mode not in ("positive", "negative"):
        raise ValueError("Scoring must be positive or negative.")
    floor = number(value.get("floor", 0), "Point floor", 0, max_points)
    ceiling = number(value.get("ceiling", max_points), "Point ceiling", floor, max_points)
    groups, gids = [], set()
    for group in items(value.get("groups", []), "Rubric groups", 100):
        if not isinstance(group, dict) or set(group) - {"id", "description", "selection", "floor", "ceiling"}:
            raise ValueError("Unsupported rubric group fields.")
        gid = identifier(group.get("id"))
        if gid in gids or group.get("selection", "multiple") not in ("single", "multiple"):
            raise ValueError("Groups need unique IDs and single/multiple selection.")
        gids.add(gid)
        groups.append({"id": gid, "description": text(group.get("description", "")),
            "selection": group.get("selection", "multiple"),
            "floor": number(group.get("floor", -max_points if mode == "negative" else 0), "Group floor", -max_points, max_points),
            "ceiling": number(group.get("ceiling", 0 if mode == "negative" else max_points), "Group ceiling", -max_points, max_points)})
        if groups[-1]["floor"] > groups[-1]["ceiling"]:
            raise ValueError("Group floor exceeds its ceiling.")
    normalized, ids = [], set()
    for item in items(value.get("items", []), "Rubric items"):
        if not isinstance(item, dict):
            raise ValueError("Invalid rubric item.")
        if set(item) - {"id", "description", "points", "group", "follow_through", "repeat_penalty_for_same_originating_error", "stackable", "requires", "excludes", "exceptions", "notes", "original_source_text", "condition_handling"}:
            raise ValueError("Unsupported rubric item fields; express the rule explicitly before approval.")
        iid = identifier(item.get("id"))
        if iid in ids:
            raise ValueError("Rubric item IDs must be unique.")
        ids.add(iid)
        # A positive rubric may hold a deduction too (requirement, 2026-10-25): it is subtracted from the credits, then the floor and ceiling.
        points = number(item.get("points"), "Item points", -max_points, 0 if mode == "negative" else max_points)
        group = item.get("group") or None
        if group and group not in gids:
            raise ValueError("Unknown rubric group.")
        booleans = {key: item.get(key, default) for key, default in (
            ("follow_through", False), ("repeat_penalty_for_same_originating_error", True), ("stackable", True))}
        if any(type(v) is not bool for v in booleans.values()):
            raise ValueError("Rubric rule flags must be booleans.")
        normalized.append({"id": iid, "description": text(item.get("description"), "Item description", 4000, False),
            "points": points, "group": group, **booleans,
            "requires": [identifier(v) for v in items(item.get("requires", []), "Dependencies")],
            "excludes": [identifier(v) for v in items(item.get("excludes", []), "Exclusions")],
            "exceptions": text(item.get("exceptions", "")), "notes": text(item.get("notes", "")),
            "original_source_text": text(item.get("original_source_text", item["description"]))})
    if not normalized:
        raise ValueError("A rubric needs at least one item.")
    caps = {g["id"] for g in groups if part_cap(g["id"])}
    if caps:
        if mode != "negative" or any(part_cap(g["id"]) and (g["selection"] != "multiple" or g["ceiling"] != 0 or g["floor"] >= 0) for g in groups):
            raise ValueError('A part cap belongs to a deducting rubric: several lines, a ceiling of 0 and minus the part\'s worth as its floor (GRADING "PART CAPS").')
        # A line is in the cap its wording names, so an edit that rewords "Part A:" as "Part B:" moves it (GRADING "PART CAPS").
        for item in normalized:
            if not item["group"] or part_cap(item["group"]):
                named = f"part-{part_letter(item['description'])}-cap"
                item["group"] = named if named in caps else None
    for original, item in zip(value["items"], normalized):
        handling = original.get("condition_handling", "review")
        if handling not in ("review", "structured"):
            raise ValueError("A condition is reviewed or explicitly enforced by structured rules.")
        if handling == "structured":
            # A part's cap is no rule about the line itself, so it never makes a condition structured.
            if not conditional(item) or not (item["requires"] or item["excludes"] or not item["stackable"] or (item["group"] and not part_cap(item["group"]))):
                raise ValueError("To mark a condition as structured, define its dependencies, exclusions, non-stacking rule or group first.")
            item["condition_handling"] = "structured"
    for item in normalized:
        if (set(item["requires"] + item["excludes"]) - ids or item["id"] in item["requires"] + item["excludes"]):
            raise ValueError("Rubric dependencies must refer to other existing items.")
    graph = {v["id"]: v["requires"] for v in normalized}
    visited = set()
    def visit(key, path):
        if key in path:
            raise ValueError("Rubric dependencies cannot form a cycle.")
        if key in visited:
            return
        for dep in graph[key]:
            visit(dep, path | {key})
        visited.add(key)
    for key in graph:
        visit(key, set())
    return {"mode": mode, "floor": floor, "ceiling": ceiling, "items": normalized, "groups": groups,
        "notes": text(value.get("notes", "")),
        "warnings": [text(v, "Warning", 4000) for v in items(value.get("warnings", []), "Warnings")]}


def semantic_hash(rubric):
    """A point-only change can reuse applications; all interpretation changes cannot."""
    return digest({**rubric, "floor": None, "ceiling": None,
        "items": [{**i, "points": None, "original_source_text": None} for i in rubric["items"]],
        "groups": [{**g, "floor": None, "ceiling": None} for g in rubric["groups"]]})


def parse(source, max_points, format="text", mode="negative"):
    text(source, "Original rubric", 200000, False)
    if format == "json":
        value = json.loads(source)
        if isinstance(value, list):
            value = {"mode": mode, "items": value}
        return validate(value, max_points)
    if format == "csv":
        exported = gradescope_import.inspect(source)
        if exported:
            if exported["max_points"] is not None and exported["max_points"] != max_points:
                raise ValueError(f"This Gradescope question is worth {exported['max_points']:g} points, but the Faculty question is set to {max_points:g}. Edit the question's maximum points before importing; values are never scaled automatically.")
            warnings = ["Gradescope exports omit item groups and selection settings. Review full-credit versus partial-credit alternatives, part totals, and which items may be combined before approval."]
            rows = exported["items"]
            wording_rules(rows, warnings)
            return validate({"mode": exported["mode"] or mode, "items": rows, "warnings": warnings}, max_points)
        reader = csv.DictReader(io.StringIO(source.removeprefix("\ufeff")))
        rows = []
        for i, row in enumerate(reader):
            # A ragged row (a short one gives None cells, a long one a None key) is a refusal, not a server error.
            if None in row:
                raise ValueError(f"CSV row {i+1} has more cells than the header.")
            if row.get("points", "") is None or row.get("description", "") is None:
                raise ValueError(f"CSV row {i+1} has fewer cells than the header.")
            rows.append({"id": row.get("id") or f"R{i+1}", "description": row.get("description", ""),
                "points": float(row.get("points", "")), "original_source_text": encoded(row),
                "notes": row.get("notes", ""), "exceptions": row.get("exceptions", "")})
        warnings = []
        wording_rules(rows, warnings)
        return validate({"mode": mode, "items": rows, "warnings": warnings}, max_points)
    if format not in ("text", "copied"):
        raise ValueError("Supported rubric formats: text, copied text, CSV and JSON; PDF/image imports use the extraction workflow.")
    rows, pending, warnings = [], [], []
    pending_points = None
    for line in source.splitlines():
        standalone = re.fullmatch(r"\s*([+-]?\d+(?:\.\d+)?)\s*(?:points?|pts?\.?)?\s*", line, re.I)
        if standalone:
            if pending_points is not None:
                raise ValueError("A point value is missing its rubric description.")
            pending_points = (float(standalone[1]), line)
            continue
        if pending_points is not None and line.strip():
            rows.append({"id": f"R{len(rows)+1}", "description": line.strip(), "points": pending_points[0],
                         "original_source_text": pending_points[1] + "\n" + line})
            pending_points = None
            continue
        match = re.match(r"^\s*(?:[•*]\s*)?([+-]?\d+(?:\.\d+)?)\s*(?:points?|pts?\.?)?\s*[:–—]?\s+(.+)$", line, re.I)
        trailing = re.match(r"^\s*(.+?)\s*:\s*([+-]?\d+(?:\.\d+)?)\s*(?:points?|pts?\.?)?\s*$", line, re.I)
        if match:
            rows.append({"id": f"R{len(rows)+1}", "description": match[2].strip(), "points": float(match[1]),
                         "original_source_text": line})
        elif trailing:
            rows.append({"id": f"R{len(rows)+1}", "description": trailing[1].strip(), "points": float(trailing[2]), "original_source_text": line})
        elif line.strip():
            if rows:
                rows[-1]["description"] += "\n" + line
                rows[-1]["original_source_text"] += "\n" + line
            else:
                pending.append(line)
    if pending_points is not None:
        raise ValueError("A point value is missing its rubric description.")
    wording_rules(rows, warnings)
    return validate({"mode": mode, "items": rows, "notes": "\n".join(pending), "warnings": warnings}, max_points)


# Wording about how a line interacts with OTHER lines or a cap. "but", "either" and "no work" describe the student's work itself.
# rule-parser-v4 (2026-09-22): "except", "maximum", "at most" and "no more than" count only where they speak of a cap or of other
# lines. "Part A: Correct except for one entry" and "States the maximum profit" describe the work, and flagging them held every
# clean response of their question back. A cap names an amount (`at most 2 points`, `maximum of 3 pts`, `2 points maximum`, a bare
# `maximum 3.`) or other lines (`at most one of the form items`); an exception names a line, an item or a part (`except when R2
# is selected`, `except for part b`). "unless", "only if", "do not also" and "mutually" are rules wherever they appear.
# rule-parser-v5 (2026-09-28): "not both" counts only beside other lines: a line, item, part or R-id, "the next", "the other",
# "above"/"below" earlier in its sentence ("Either this item or the next, but not both"), or right after it ("not both R3 and R4").
# "The constraints x >= 0 and y >= 0 are not both stated" and "gives only the value or only the point, not both" describe the work;
# on two drafted questions they held every response back (61 of 61, Quiz batch 7260e09a).
_AMOUNT = r"(?:\d+(?:\.\d+)?|one|two|three|four|five|six|half)"
_LINES = r"(?:items?|lines?|parts?|deductions?|penalt(?:y|ies)|criteria|r\d[\w.]*)"
_CAP = (rf"(?:at most|no more than|maximum|max\.?)(?:\s+(?:of|a|an|the|total|combined|deduction|penalty|credit|score|possible|allowed))*\s+{_AMOUNT}"
        rf"(?:\s*(?:points?|pts?|marks?)\b|\s+of\s+(?:the\s+|these\s+|those\s+)?(?:[\w-]+\s+){{0,3}}?{_LINES}\b|\s*(?:[.;,)]|$))"
        rf"|\b{_AMOUNT}\s*(?:points?|pts?|marks?)\.?\s+(?:max(?:imum)?|at most)\b")
_EXCEPT = rf"except\s+(?:(?:when|if|where)\b[^.;]*?\b{_LINES}\b|(?:for\s+)?(?:the\s+)?{_LINES}\b)"
_OTHER = rf"(?:{_LINES}|this one|the (?:next|previous|preceding|following|other)(?: one)?|above|below)"
_NOT_BOTH = (rf"\b{_OTHER}\b(?:[^.;]|\.\d)*?\bnot both\b"
             rf"|\bnot both\s+(?:of\s+)?(?:the\s+|these\s+|those\s+)?(?:[\w-]+\s+){{0,3}}?{_LINES}\b")
CONDITIONAL = re.compile(rf"\b(?:(?:unless|do not also|only if|mutually)\b|{_CAP}|{_EXCEPT})|{_NOT_BOTH}")


def conditional(item):
    """Whether a rubric line carries a conditional rule under the CURRENT wording rule.

    The importer marks a line by copying its description into `exceptions`. Rubrics imported before the rule was narrowed
    (2026-09-19) carry that mark on lines such as "Correct answer, but no work to justify answer", and rubric revisions are
    immutable: on real papers every response of eight questions was sent to review for it, and none could be trial-accepted,
    while the identical lines of the other exam version, imported a few hours later, were clean. The importer's own mark is
    therefore honoured only where today's rule still finds the wording. A condition someone WROTE (any other text) always counts.
    """
    text = (item.get("exceptions") or "").strip()
    if not text:
        return False
    return bool(CONDITIONAL.search(text.lower())) if text == (item.get("description") or "").strip() else True


def wording_rules(rows, warnings):
    """Retain explicit follow-through and flag conditional wording for review."""
    for row in rows:
        content = row["description"].lower()
        follows = bool(re.search(r"follow[ -]through|do not penalize subsequent|do not deduct again|(?:based on|from) (?:the |their )?(?:work|ordered pairs)", content))
        row["follow_through"] = follows
        row["repeat_penalty_for_same_originating_error"] = not follows
        # Only wording about how an item interacts with other items or a cap.
        # "but", "either" and "no work" describe the student's work itself;
        # flagging them sent every response of the question to manual review.
        if CONDITIONAL.search(content):
            row["exceptions"] = row.get("exceptions") or row["description"]
            warnings.append(f"{row['id']}: review the conditional rule and explicitly set dependencies/exclusions before approval.")


def compiled(value, imported, max_points):
    """A model may propose rule structure, never replace exported scoring facts."""
    result = validate(value, max_points)
    if any(result[key] != imported[key] for key in ("mode", "floor", "ceiling")):
        raise ValueError("The interpretation changed the imported scoring method or question bounds.")
    originals = {item["id"]: item for item in imported["items"]}
    proposed = {item["id"]: item for item in result["items"]}
    if originals.keys() != proposed.keys() or any(proposed[key]["points"] != item["points"] for key, item in originals.items()):
        raise ValueError("The interpretation changed imported rubric numbers or point values.")
    # Preserve source wording and order even when the model paraphrases them.
    result["items"] = []
    for key, original in originals.items():
        item = proposed[key]
        for field in ("description", "original_source_text"):
            item[field] = original[field]
        if original["follow_through"]:
            item.update(follow_through=True, repeat_penalty_for_same_originating_error=False)
        # A compiler cannot certify that prose has been completely captured by executable rules.
        item.pop("condition_handling", None)
        if original.get("condition_handling"):
            item["condition_handling"] = original["condition_handling"]
        if original["exceptions"]:
            item["exceptions"] = original["exceptions"]
        result["items"].append(item)
    result["warnings"] = list(dict.fromkeys(imported["warnings"] + result["warnings"]))
    return result
