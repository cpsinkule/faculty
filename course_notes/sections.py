"""Where each section of a set of lecture notes begins (AGENTS.md "Course notes"): a pure rule over what the document sandbox read.

The sandbox (`document_identity/process.py` operation `notes_outline`) returns the PDF's page count, its title, its bookmarks with
the page each points to, and the first lines of text of every page. A SECTION is a number and a title, "2.3 Systems of Two
Equations in Two Unknowns": it is found where a bookmark names it, where its heading opens a page, or both. The notes the instructor
first uploaded (MATH 140, 204 pages) have a bookmark for every section but 5.1, and every section's heading opens its own page, so
both kinds of evidence are kept and compared, never one guessed from the other:

- a heading counts only among the first lines of a page, after the lines every few pages repeat (the running header, the page
  number); a page listing three or more section headings is a table of contents and opens nothing;
- bookmark and heading agreeing is `both`; a section with only one of them is kept and says which; where they name different pages
  the heading's page is used (it is what the page shows) unless that breaks the order of the sections, then the bookmark's;
- sections must come in number order on non-decreasing pages; one that does not is left out and named in the notes;
- a section that begins below the top of a page (a bookmark to a page whose heading is further down, or two sections on one page)
  shares that page with the section before it, so nothing is cut off;
- pages before the first section are the front matter; every other bookmark becomes a topic of the part holding its page.

Nothing is inferred beyond that: no sections found leaves the notes whole, and every disagreement is named in `notes` for the
instructor. A changed rule gets a new RULE name (an edition records the rule that split it).
"""
from __future__ import annotations

import math
import re

RULE = "notes-sections-v1"
MAX_PARTS = 300
MAX_TOPICS = 80
TOP_LINES = 5            # a heading opens a page: it is among the first lines once running lines are set aside
CONTENTS_HEADINGS = 3    # a page listing this many section headings is a table of contents
# "2.3 Systems of Two Equations" or "Section 2.3: Systems ...": a number, then a title beginning with a capital letter (a measurement
# such as "1.5 liters" is no heading). LISTED is any line opening with a section number, dot leaders and page numbers included.
HEADING = re.compile(r"^(?:[Ss]ection\s+)?(\d{1,2})\.(\d{1,2})[.:]?\s+([A-Z(\"'“‘].{0,200})$")
LISTED = re.compile(r"^(?:section\s+)?\d{1,2}\.\d{1,2}[.:]?\s+\S", re.I)
LEADERS = re.compile(r"(\.\s*){3,}\s*\d*\s*$|…\s*\d*\s*$")
PAGE_NUMBER = re.compile(r"^(?:page\s+)?(?:\d{1,4}|[ivxlcdm]{1,8})$", re.I)


def _clean(text, limit=200):
    return " ".join(str(text or "").split())[:limit]


def heading(line):
    """(chapter, section, title) of a line that reads as a section heading, else None. A contents line (dot leaders) is none."""
    line = _clean(line, 400)
    match = HEADING.match(line)
    if not match or LEADERS.search(line):
        return None
    return int(match.group(1)), int(match.group(2)), match.group(3).strip()


def _normal(line):
    """A line without its numbers: a running header reads the same beside its page number on odd and even pages."""
    return " ".join(re.sub(r"\d+", " ", _clean(line).casefold()).split())


def running_lines(pages):
    """Lines (digits aside) that open many pages: a running header and its page number. A line opening with a section number is
    never one, however often it is repeated (a contents page, a mention and the heading itself)."""
    counts = {}
    for page in pages:
        for line in set(_normal(line) for line in page.get("lines", []) if not LISTED.match(_clean(line))):
            counts[line] = counts.get(line, 0) + 1
    floor = max(3, math.ceil(0.2 * len(pages)))
    return {line for line, count in counts.items() if count >= floor}


def _content(page, running):
    return [_clean(line) for line in page.get("lines", []) if _clean(line) and _normal(line) not in running and not PAGE_NUMBER.match(_clean(line))]


def _title_from_heading(lines, index, title):
    """The heading's title, with the next line when a long title wrapped onto it ("3.2 Graphing Systems of Linear Inequalities in
    Two" / "Variables"): a short line of words that is no sentence and no label."""
    if len(lines[index]) >= 40 and index + 1 < len(lines):
        following = lines[index + 1]
        words = following.split()
        if (1 <= len(words) <= 6 and following[:1].isupper() and not re.search(r"[\d:;.?!]", following)
                and heading(following) is None):
            return f"{title} {following}"
    return title


def _chapter_title(lines, index):
    """The words above a chapter's first section heading on its page ("Matrices", "Linear Models and Systems of Linear Equations")."""
    above = " ".join(lines[:index]).strip()
    return above[:120] if above and not re.search(r"\d", above) and len(above) <= 120 else ""


def _order(entries):
    """The longest run of sections, in number order, whose pages never go back (pages may repeat: two sections on one page)."""
    best = [1] * len(entries)
    before = [-1] * len(entries)
    for i, entry in enumerate(entries):
        for j in range(i):
            if entries[j]["page"] <= entry["page"] and best[j] + 1 > best[i]:
                best[i], before[i] = best[j] + 1, j
    if not entries:
        return set()
    index = max(range(len(entries)), key=lambda i: (best[i], -i))
    kept = set()
    while index >= 0:
        kept.add(index)
        index = before[index]
    return kept


def plan(evidence):
    """The parts of the notes: [{position, kind, number, chapter, chapter_title, title, first_page, last_page, found_by, topics}],
    plus `notes` naming anything the rule could not settle. `evidence` is what the sandbox read (see the module docstring)."""
    total = int(evidence.get("pages") or 0)
    if total < 1:
        raise ValueError("The PDF has no pages.")
    pages = sorted((page for page in evidence.get("text", []) if 1 <= int(page.get("page") or 0) <= total), key=lambda page: page["page"])
    by_page = {page["page"]: page for page in pages}
    running = running_lines(pages)
    notes = []

    # Headings that open a page.
    found = {}
    for page in pages:
        lines = _content(page, running)
        listed = sum(1 for line in page.get("headings", []) if LISTED.match(_clean(line)))
        if listed >= CONTENTS_HEADINGS:
            continue
        for index, line in enumerate(lines[:TOP_LINES]):
            parsed = heading(line)
            if parsed:
                chapter, section, title = parsed
                key = (chapter, section)
                if key not in found:
                    found[key] = {"page": page["page"], "title": _title_from_heading(lines, index, title),
                                  "chapter_title": _chapter_title(lines, index)}
                break

    # Bookmarks that name a section, and every other bookmark (a topic).
    marked, topics, parents = {}, [], []
    for item in evidence.get("toc", []):
        title, page, level = _clean(item.get("title")), item.get("page"), int(item.get("level") or 0)
        if not title or not isinstance(page, int) or not 1 <= page <= total:
            continue
        del parents[level:]
        parsed = heading(title)
        if parsed:
            key = parsed[:2]
            if key not in marked:
                parent = parents[-1] if parents else ""
                marked[key] = {"page": page, "title": parsed[2], "parent": parent}
        else:
            topics.append({"title": title, "page": page})
        parents.append(title)

    entries = []
    for key in sorted(set(found) | set(marked)):
        heading_, bookmark = found.get(key), marked.get(key)
        label = f"{key[0]}.{key[1]}"
        if heading_ and bookmark:
            kind = "both" if heading_["page"] == bookmark["page"] else "differ"
        else:
            kind = "heading" if heading_ else "bookmark"
        entries.append({"key": key, "label": label, "heading": heading_, "bookmark": bookmark, "kind": kind,
                        "page": (heading_ or bookmark)["page"]})
    # Where bookmark and heading disagree, the heading's page stands unless it breaks the order; then the bookmark's.
    for entry in entries:
        if entry["kind"] != "differ":
            continue
        kept = _order(entries)
        if entries.index(entry) not in kept:
            entry["page"] = entry["bookmark"]["page"]
            notes.append(f"{entry['label']}: its bookmark points to page {entry['bookmark']['page']}, its heading is on page "
                         f"{entry['heading']['page']}; the bookmark's page is used.")
        else:
            notes.append(f"{entry['label']}: its heading is on page {entry['heading']['page']}, its bookmark points to page "
                         f"{entry['bookmark']['page']}; the heading's page is used.")
    kept = _order(entries)
    for index, entry in enumerate(entries):
        if index not in kept:
            notes.append(f"{entry['label']} was left out: page {entry['page']} is out of order with the sections around it.")
    entries = [entry for index, entry in enumerate(entries) if index in kept][:MAX_PARTS - 1]
    for entry in entries:
        if entry["kind"] == "heading":
            notes.append(f"{entry['label']} was found from its heading on page {entry['page']}; the PDF has no bookmark for it.")
        elif entry["kind"] == "bookmark" and by_page.get(entry["page"], {}).get("lines"):
            notes.append(f"{entry['label']}: no heading opens page {entry['page']}; its bookmark was followed.")

    parts = []
    first = entries[0]["page"] if entries else total + 1
    if first > 1:
        parts.append({"kind": "front", "number": "", "chapter": "", "chapter_title": "", "found_by": "front",
                      "title": "Front matter" if entries else "Whole notes", "first_page": 1, "last_page": min(first - 1, total)})
    chapter_titles = {}
    for index, entry in enumerate(entries):
        chapter = str(entry["key"][0])
        heading_, bookmark = entry["heading"], entry["bookmark"]
        title = bookmark["title"] if bookmark else heading_["title"]
        named = (heading_ or {}).get("chapter_title") if heading_ and heading_["page"] == entry["page"] else ""
        if chapter not in chapter_titles and (named or (bookmark or {}).get("parent")):
            chapter_titles[chapter] = named or bookmark["parent"]
        following = entries[index + 1] if index + 1 < len(entries) else None
        last = total
        if following:
            last = following["page"] - 1
            # A section that begins below the top of its page shares that page with the one before it.
            below_top = following["page"] == entry["page"] or (following["kind"] == "bookmark" and _heading_below_top(by_page.get(following["page"]), following["key"], running))
            if below_top:
                last = following["page"]
        found_by = "both" if entry["kind"] in ("both", "differ") else entry["kind"]
        parts.append({"kind": "section", "number": entry["label"], "chapter": chapter, "chapter_title": "", "title": _clean(title),
                      "first_page": entry["page"], "last_page": max(entry["page"], last), "found_by": found_by})
    for part in parts:
        if part["kind"] == "section":
            part["chapter_title"] = _clean(chapter_titles.get(part["chapter"], ""), 120)
    for position, part in enumerate(parts, 1):
        part["position"] = position
        # A topic on a page shared with the next part belongs to the part that begins there.
        following = parts[position] if position < len(parts) else None
        end = part["last_page"] - 1 if following and following["first_page"] == part["last_page"] > part["first_page"] else part["last_page"]
        part["topics"] = [topic for topic in topics if part["first_page"] <= topic["page"] <= end][:MAX_TOPICS]
    if not entries:
        notes.append("No numbered sections were found (no bookmark or page heading like “1.1 Title”); the notes are kept whole.")
    return {"rule": RULE, "pages": total, "parts": parts, "notes": notes}


def _heading_below_top(page, key, running):
    """Whether this section's heading is on the page, but not among its first lines."""
    if not page:
        return False
    lines = _content(page, running)
    top = [heading(line) for line in lines[:TOP_LINES]]
    if any(parsed and parsed[:2] == key for parsed in top):
        return False
    return any((parsed := heading(line)) and parsed[:2] == key for line in page.get("headings", []))
