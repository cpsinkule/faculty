"""POSTING POLICY (docs/decisions/canvas.md POSTING GRADES; requirement, 2026-10-08). Pure: what each column's grades should be in Canvas, and what a Sync with Canvas does about it.

Each column's DESIRED state, the first that applies:
1. The instructor's CHOICE from the column's menu (`gradebook_canvas_posting_choices`, the latest of the column): Post keeps its
   grades shown, Hide keeps them hidden, until they choose again (or go back to the category's rule).
2. ALWAYS BY HAND (instructor: "assignment categories that have 0% weighting or no effect on students' grades must always be
   manually posted"): a column that does not count or is archived, and, in weighted mode, one without a category or in a
   category of weight 0. Nothing posts it but a choice.
3. ICLICKER (instructor: "iclicker's are automatically posted"): a column an iClicker session's scores go to is posted.
4. ITS CATEGORY'S RULE (`posting` {"due", "certified"} on the category; none: by hand). "due": a column Faculty does not grade
   is posted once its due date has passed on the campus clock (due today is not past due, as ZERO ONCE DUE has it). "certified"
   (instructor: "Post on Certification ... in addition to the other options ... a category that might have mixed assignments"):
   a column linked to Faculty's grading is posted once its grading plan is certified.
5. Otherwise nothing: it waits for the instructor.

POSTED STAYS POSTED (instructor: "Yes, posted stays posted"): Canvas hides a grade that arrives after posting when the assignment
posts by hand, so every sync posts what Canvas still hides in a posted column. A rule never hides; only a Hide choice does.

MANUAL POSTING IN CANVAS (instructor: "Switch it to manual, but only for assignment categories that are set to manual posting in
faculty, don't turn everything to manual", asked about grades that "show the moment Faculty sends them"): a column FACULTY SENDS
(not WebAssign's or a Canvas quiz's, not kept off Canvas) that posts by hand here (always by hand, or not iClicker's and its
category has no rule) whose Canvas assignment posts grades automatically is switched to post by hand before its grades are sent.
"""
from __future__ import annotations

import hashlib
import json
from decimal import Decimal, InvalidOperation

from . import blanks, setup as setups

RULES = ("due", "certified")
CHOICES = ("post", "hide", "rule")
CURRENT = ("active", "invited")  # student enrollments Canvas lists for an assignment and posts for


def _graded(submission):
    return submission.get("score") is not None or bool(submission.get("excused"))


def counts(submissions):
    """{total, graded, hidden, posted, unposted} of one assignment's submissions ({score, excused, posted}): `hidden` counts the
    grades students do not see; `unposted` every submission not posted, graded or not (Canvas's "Everyone" posts them all)."""
    rows = list(submissions)
    return {"total": len(rows), "graded": sum(1 for s in rows if _graded(s)), "hidden": sum(1 for s in rows if _graded(s) and not s.get("posted")),
            "posted": sum(1 for s in rows if s.get("posted")), "unposted": sum(1 for s in rows if not s.get("posted"))}


def by_assignment(canvas):
    """{(course, assignment): [submission]} of a read in `canvas_export.from_read`'s shape, indexed once (a course holds thousands).
    Only the course's CURRENT students count (an active or invited student enrollment), the ones Canvas lists for an assignment and
    posts for (requirement, 2026-10-07)."""
    index = {}
    for course, data in canvas.items():
        students = data.get("students")
        current = None if students is None else {str(s.get("user_id")) for s in students if s.get("state") in CURRENT}
        for (assignment, user), submission in (data.get("submissions") or {}).items():
            if current is None or str(user) in current:
                index.setdefault((course, assignment), []).append(submission)
    return index


def rule(category):
    """The category's automatic posting: a subset of RULES (empty: by hand)."""
    posting = (category or {}).get("posting") or {}
    return frozenset(key for key in RULES if posting.get(key) is True)


def _weight(category):
    try:
        return Decimal(str(category.get("weight", "0")))
    except (InvalidOperation, ValueError):
        return Decimal(0)


def categories_of(setup):
    return {category["id"]: category for category in setup.get("categories", [])}


def forced(setup, item, categories=None):
    """Why the column always posts by hand (it has no effect on a grade), or None."""
    if item.get("archived"):
        return "archived"
    if not item.get("counts", True):
        return "not counted"
    if setup.get("mode") == "weighted":
        category = (categories if categories is not None else categories_of(setup)).get(item.get("category_id") or "")
        if category is None:
            return "no category"
        if _weight(category) <= 0:
            return "weight 0"
    return None


def desired(setup, item, *, choice=None, today, certified=frozenset(), iclicker=frozenset(), categories=None):
    """{state: "posted" | "hidden" | None, why: "choice" | "iclicker" | "due" | "certified" | None, manual, switch, forced, rules} of
    one column (the module's order). `manual`: the column posts by hand here; `switch`: and Faculty sends its grades, so its Canvas
    assignment is switched to post by hand."""
    categories = categories if categories is not None else categories_of(setup)
    why_forced = forced(setup, item, categories)
    rules = rule(categories.get(item.get("category_id") or ""))
    clicker = item["id"] in iclicker
    manual = bool(why_forced) or (not clicker and not rules)
    found = {"state": None, "why": None, "manual": manual, "switch": manual and item.get("canvas") not in setups.CANVAS,
             "forced": why_forced, "rules": sorted(rules)}
    if choice in ("post", "hide"):
        return {**found, "state": "posted" if choice == "post" else "hidden", "why": "choice"}
    if why_forced:
        return found
    if clicker:
        return {**found, "state": "posted", "why": "iclicker"}
    if blanks.linked(item):
        if "certified" in rules and item["id"] in certified:
            return {**found, "state": "posted", "why": "certified"}
    elif "due" in rules and item.get("due_date") and item["due_date"] < today:
        return {**found, "state": "posted", "why": "due"}
    return found


def policies(setup, *, choices, today, certified=frozenset(), iclicker=frozenset()):
    """{column ID: `desired`} of every live column that Canvas may hold (not archived, not kept off Canvas)."""
    categories = categories_of(setup)
    return {item["id"]: desired(setup, item, choice=choices.get(item["id"]), today=today, certified=certified, iclicker=iclicker,
                                categories=categories)
            for item in setup.get("items", []) if not item.get("archived") and item.get("canvas") != "off"}


def steps(setup, where, canvas, out, *, policy, courses=None, unsure=frozenset(), automatic=False):
    """What a Sync with Canvas does about posting, per column and Canvas course, from its own read: [{key, item_id, title, course_id,
    assignment_id, new, action: "post" | "hide" | "switch", why, hidden, shown, waits: [reason]}]. `where` the columns' Canvas
    assignments (`canvas_export.targets`); `canvas` the read (`canvas_export.from_read`); `out` the To-Canvas plan (what it sends,
    creates and holds); `policy` `policies`; `courses` the courses this sync writes to (None: all); `unsure` {(column, course)}
    whose latest post, hide or switch Faculty could not read back (the morning sync never asks again by itself: `automatic`).
    A step with `waits` is shown and not done."""
    index = by_assignment(canvas)
    columns = {(c["item_id"], c["course_id"]): c for c in out["columns"]}
    titles = {item["id"]: item["title"] for item in setup.get("items", [])}
    found = []
    for item_id, want in policy.items():
        for course in sorted(canvas, key=int):
            if courses is not None and course not in courses:
                continue
            target = where.get((item_id, course))
            column = columns.get((item_id, course))
            new = target is None and bool(column and column["assignment"] and column["assignment"].get("new"))
            if (target is None and not new) or (target is not None and "problem" in target):
                continue
            seen = counts(index.get((course, target["id"])) or []) if target else counts([])
            sending = bool(column and column["send"] and column["values"])
            base = {"key": f"{item_id}:{course}", "item_id": item_id, "title": titles.get(item_id, ""), "course_id": course,
                    "assignment_id": target["id"] if target else None, "new": new, "why": want["why"]}
            waits = []
            if automatic and (item_id, course) in unsure:
                waits.append("Faculty could not tell whether its last change of posting here reached Canvas: Sync with Canvas yourself")
            if want["switch"] and column is not None and (new or not target.get("post_manually")):
                found.append({**base, "action": "switch", "why": "manual", "hidden": 0, "shown": 0, "waits": list(waits)})
            if want["state"] == "posted" and (seen["hidden"] or sending):
                if column and column["problems"]:
                    waits.append(f"its grades cannot sync here: {column['problems'][0]}")
                if column and column.get("unsure"):
                    waits.append(f"{column['unsure']} of its grades wait for your own Sync (an earlier send's outcome is not known)")
                if target and not target.get("published", True) and not (column and column.get("publish")):
                    waits.append("its Canvas assignment is not published")
                found.append({**base, "action": "post", "hidden": seen["hidden"], "shown": seen["posted"], "waits": waits})
            elif want["state"] == "hidden" and seen["posted"]:
                found.append({**base, "action": "hide", "hidden": seen["hidden"], "shown": seen["posted"], "waits": waits})
    return found


def settle(found, out, held, publish=()):
    """The steps a Sync carries out once the instructor's picks are known: a post waits while a grade of that column changed in
    Canvas waits for them there (`held`: the picks), or while its unpublished assignment is not ticked to be published. Returns
    the steps, each with its final `waits`."""
    columns = {(c["item_id"], c["course_id"]): c for c in out["columns"]}
    final = []
    for step in found:
        waits = list(step["waits"])
        column = columns.get((step["item_id"], step["course_id"]))
        if step["action"] == "post" and column:
            undecided = sum(1 for h in column["held"] if not held.get(h["key"]))
            if column["send"] and undecided:
                waits.append(f"{undecided} of its grades changed in Canvas and wait for your choice")
            if column.get("publish") and step["item_id"] not in publish:
                waits.append("its Canvas assignment is not published")
        final.append({**step, "waits": sorted(set(waits), key=waits.index)})
    return final


def digest(found):
    """The steps' fingerprint: what is done where, never how many grades (a student's submission between the summary and Sync
    changes the counts, not the plan)."""
    plain = sorted((s["item_id"], s["course_id"], s["action"], s["assignment_id"] or "", bool(s["waits"])) for s in found)
    return hashlib.sha256(json.dumps(plain, separators=(",", ":")).encode()).hexdigest()


def pending(policy, reads):
    """{column ID: [what the next sync does: "post" | "hide" | "switch"]} from what Faculty last read of each Canvas assignment
    (`reads` [{item_id, manual, graded, hidden}], the grid's mark)."""
    found = {}
    for row in reads:
        want = policy.get(row["item_id"])
        if want is None:
            continue
        actions = found.setdefault(row["item_id"], set())
        if want["state"] == "posted" and row["hidden"]:
            actions.add("post")
        elif want["state"] == "hidden" and row["graded"] > row["hidden"]:
            actions.add("hide")
        if want["switch"] and not row["manual"]:
            actions.add("switch")
    return {item_id: sorted(actions) for item_id, actions in found.items() if actions}
