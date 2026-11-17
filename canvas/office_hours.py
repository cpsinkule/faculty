"""The Canvas Office Hours page drafted from the Term schedule (SCHEDULING "CANVAS OFFICE HOURS", stage 1f, 2026-10-05: the instructor
chose Faculty's Term schedule as the source of truth for office hours, drafting the Canvas Office Hours text from it).

Pure. The term copy's page titled "Office Hours" keeps its own opening heading (its styled banner) and its closing images; what lies
between becomes two lists: the office hours ("In person (regularly scheduled)") and the meeting hours ("Or by appointment"). The copy
is only changed by the instructor's explicit "Put in Faculty's copy" (a revision of the copy, CANVAS); Canvas itself is written only
by the ordinary Push."""
from __future__ import annotations

import html
import re

from scheduling.clock import display_span

DAYS = ("Mondays", "Tuesdays", "Wednesdays", "Thursdays", "Fridays", "Saturdays", "Sundays")
HEAD = re.compile(r"^\s*(<h[1-3][^>]*>.*?</h[1-3]>)", re.S | re.I)
TAIL = re.compile(r"((?:\s*<p[^>]*>(?:(?!</p>).)*?<img\b(?:(?!</p>).)*</p>)+\s*)$", re.S | re.I)


def find(content):
    """(index, page) of the copy's Office Hours page, or None."""
    for index, page in enumerate((content or {}).get("pages") or []):
        if " ".join(str(page.get("title") or "").split()).casefold() == "office hours":
            return index, page
    return None


def lines(entries, kind):
    """One line per time and room of a kind ("Mondays and Fridays, 3:00–5:00 PM · BLOC 309"), days that share them together."""
    groups = {}
    for entry in entries:
        if entry["kind"] == kind:
            for day in entry["weekdays"]:
                groups.setdefault((entry["start"], entry["end"], entry["room"]), set()).add(day)
    found = []
    for (start, end, room), days in sorted(groups.items(), key=lambda item: (min(item[1]), item[0])):
        names = [DAYS[d] for d in sorted(days)]
        label = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
        found.append(f"{label}, {display_span(start, end)}" + (f" · {room}" if room else ""))
    return found


def draft(body, entries):
    """The page body with its schedule lists drawn from the Term schedule's entries."""
    body = str(body or "")
    head = HEAD.match(body)
    tail = TAIL.search(body[head.end() if head else 0:])
    office, meeting = lines(entries, "office_hours"), lines(entries, "meeting_hours")
    middle = "\n<h3>In person (regularly scheduled)</h3>\n<ul>\n" + "".join(f"<li>{html.escape(line)}</li>\n" for line in office) + "</ul>\n"
    middle += "<h3>Or by appointment (email to request)</h3>\n<ul>\n" + (
        "".join(f"<li>{html.escape(line)}</li>\n" for line in meeting) if meeting else "<li>A time we agree on by email</li>\n") + "</ul>\n"
    return (head.group(1) if head else "<h2>Office Hours</h2>") + middle + (tail.group(1).lstrip("\n") if tail else "")


def text(body):
    """A body's words, for comparing two versions (tags, entities and spacing aside)."""
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", str(body or ""))).replace("\xa0", " ").split())
