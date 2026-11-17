"""Whose sheet is it? UIN first, then the handwritten name, always with a stated confidence.

Bubble sheets usually carry no Data Matrix label, so identity comes from two
handwriting reads that local OCR gets wrong often enough to matter. The rules
keep a guess from becoming a record:

- `matched` by `label`: the sheet carries the Scantron label printed for one
  student of the chosen sections (DOCUMENTS "SCANTRON LABELS"). Only an exact
  UIN read of someone else argues against it, and then it is a suggestion.
- `matched`: the digits read contain exactly one roster UIN of the chosen
  sections, and the name read does not clearly point at someone else. Nine
  digits agreeing with one of a few hundred students by accident is negligible.
- `suggested`: no exact UIN, but one student is clearly ahead on the UIN read
  (a digit or two off) and the name read together: 55% of the UIN similarity
  plus 45% of the name similarity. At the instructor's threshold (an adjustable
  setting, `AUTO_IDENTIFY` = 70% unless changed) or more it identifies the
  student without being asked; below that the instructor confirms it.
- `unmatched`: anything else, with the nearest students listed for a manual pick.

This weighted average is the instructor's choice. A probability model that
weighed each digit by how much it tells students apart was tried on the real
reads in September 2026 and the instructor judged its results worse: do not
bring it back without being asked. A suggestion is never upgraded when something
argues against it (`notes`, such as an exact UIN whose name points at someone
else), and two sheets identified as one student both go back to the instructor.

Roster UINs never leave this module in a result; candidates carry enrollment
IDs, names, sections and similarities only.
"""
import re
from difflib import SequenceMatcher

EXACT_NAME_FLOOR = .45      # with an exact UIN, a name read this similar (or no name read) is no contradiction
CONTRADICTION = .8          # another student's name this similar to the read contradicts an exact UIN
MATCHER = "average-5"       # a changed rule gets a new name, and retained reads are matched again under it (5: Scantron labels)
# A suggestion needs this confidence and this lead over the runner-up. The lead only breaks near-ties: the instructor sets the
# threshold and expects a student shown at or above it to be identified, so a wider margin (it was 0.12) silently overruled them.
SUGGEST, MARGIN = .6, .05
AUTO_IDENTIFY = .7          # default for the instructor's setting: a suggestion this confident identifies the student without a confirmation
LOWEST_THRESHOLD = SUGGEST  # the setting cannot go below what is offered as a suggestion at all
NEAR_UIN, NEAR_NAME = .75, .8


def digits(text):
    return re.sub(r"\D", "", str(text or ""))


def letters(text):
    return re.sub(r"[^a-z]", "", str(text or "").casefold())


def _distance(a, b):
    previous = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        current = [i]
        for j, y in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (x != y)))
        previous = current
    return previous[-1]


def uin_similarity(read, uin):
    """1.0 when the nine-digit UIN appears in the digits read; otherwise how few single-digit edits separate them."""
    if not read or not uin:
        return 0.0
    if len(read) > len(uin):  # a stray stroke or the box's printed label added digits: judge the best nine-digit window
        return max(1 - _distance(read[start:start + len(uin)], uin) / len(uin) for start in range(len(read) - len(uin) + 1))
    return max(0.0, 1 - _distance(read, uin) / len(uin))


def uin_search(typed, students, limit=8):
    """The students whose UIN is closest to digits the instructor typed into a student picker (requirement, 2026-09-24): a whole UIN
    by `uin_similarity`, part of one by its best-matching stretch of the same length. Names and sections only, never a UIN."""
    typed = digits(typed)[:12]
    if len(typed) < 4:
        return []
    scored = []
    for student in students:
        uin = student["uin"]
        if len(typed) >= len(uin):
            score = uin_similarity(typed, uin)
        else:
            score = max(1 - _distance(typed, uin[start:start + len(typed)]) / len(typed) for start in range(len(uin) - len(typed) + 1))
        if score >= .6:
            scored.append({"enrollment_id": student["enrollment_id"], "section_id": student["section_id"], "section_number": student["section_number"],
                           "name": student["name"], "similarity": round(score, 3)})
    scored.sort(key=lambda item: (-item["similarity"], item["name"].casefold()))
    return scored[:limit]


def name_similarity(read, roster_name):
    """Rosters say "Last, First Middle"; students write "First Last". Compare letters only, in either order."""
    read = letters(read)
    if not read or not roster_name:
        return 0.0
    last, _, given = str(roster_name).partition(",")
    first = given.split()[0] if given.split() else ""
    variants = {letters(f"{given} {last}"), letters(f"{last} {given}"), letters(f"{first} {last}"), letters(f"{last} {first}")} - {""}
    return max(SequenceMatcher(None, read, variant).ratio() for variant in variants)


def identified(result, threshold=AUTO_IDENTIFY):
    """Whether a match result names the student without asking: an exact UIN, or an uncontradicted suggestion at the threshold or more."""
    return result["state"] == "matched" or (result["state"] == "suggested" and result["confidence"] >= threshold and not result["notes"])


def match(id_text, name_text, students, label=None):
    """`students`: dicts with enrollment_id, section_id, section_number, uin and name for the chosen sections. `label`: the
    enrollment the sheet's Scantron label was printed for, if it carries one of this assignment."""
    read, name_read = digits(id_text), letters(name_text)
    scored = []
    for student in students:
        uin, name = uin_similarity(read, student["uin"]), name_similarity(name_text, student["name"])
        confidence = .55 * uin + .45 * name if read and name_read else .9 * uin if read else .9 * name
        scored.append({"enrollment_id": student["enrollment_id"], "section_id": student["section_id"], "section_number": student["section_number"],
                       "name": student["name"], "uin_similarity": round(uin, 3), "name_similarity": round(name, 3), "confidence": round(confidence, 3)})
    scored.sort(key=lambda item: (-item["confidence"], item["name"]))
    result = {"state": "unmatched", "method": None, "enrollment_id": None, "section_id": None, "confidence": 0.0,
              "digits_read": len(read), "name_read": bool(name_read), "candidates": scored[:3], "notes": []}
    if not students:
        result["notes"].append("The chosen sections have no roster students.")
        return result
    exact = [item for item in scored if item["uin_similarity"] == 1.0]
    if label:
        found = next((item for item in scored if item["enrollment_id"] == label), None)
        if found is None:
            result["notes"].append("The sheet's Scantron label belongs to a student outside the chosen sections.")
        else:
            others = [item for item in scored if item is not found]
            rival = next((item for item in exact if item is not found), None)
            if rival and found["uin_similarity"] < 1.0:
                result.update(state="suggested", method="label", enrollment_id=found["enrollment_id"], section_id=found["section_id"], confidence=.5,
                              candidates=[found, rival] + [item for item in others if item is not rival][:1])
                result["notes"].append(f"The Scantron label is {found['name']}'s, but the UIN read is {rival['name']}'s.")
                return result
            result.update(state="matched", method="label", enrollment_id=found["enrollment_id"], section_id=found["section_id"], confidence=1.0,
                          candidates=[found] + others[:2])
            return result
    if len(exact) == 1:
        found = exact[0]
        rival = max((item for item in scored if item is not found), key=lambda item: item["name_similarity"], default=None)
        if name_read and found["name_similarity"] < EXACT_NAME_FLOOR and rival and rival["name_similarity"] >= CONTRADICTION:
            result.update(state="suggested", method="uin", enrollment_id=found["enrollment_id"], section_id=found["section_id"], confidence=.5,
                          candidates=[found, rival] + [item for item in scored if item not in (found, rival)][:1])
            result["notes"].append(f"The UIN read is {found['name']}'s, but the name read looks like {rival['name']}.")
            return result
        result.update(state="matched", method="uin", enrollment_id=found["enrollment_id"], section_id=found["section_id"],
                      confidence=round(.85 + .15 * found["name_similarity"], 3) if name_read else .85,
                      candidates=[found] + [item for item in scored if item is not found][:2])
        return result
    if len(exact) > 1:
        result["notes"].append("The digits read contain more than one roster UIN.")
    top, second = scored[0], scored[1] if len(scored) > 1 else None
    lead = top["confidence"] - (second["confidence"] if second else 0.0)
    if top["confidence"] >= SUGGEST and lead >= MARGIN and (top["uin_similarity"] >= NEAR_UIN or top["name_similarity"] >= NEAR_NAME):
        method = "uin+name" if top["uin_similarity"] >= NEAR_UIN and top["name_similarity"] >= .6 else "name" if top["name_similarity"] >= NEAR_NAME else "uin"
        result.update(state="suggested", method=method, enrollment_id=top["enrollment_id"], section_id=top["section_id"], confidence=top["confidence"])
    elif not read and not name_read:
        result["notes"].append("Neither the ID nor the name could be read.")
    elif top["confidence"] >= SUGGEST and second and lead < MARGIN:
        # Said out loud, so a student shown above the threshold who is still not identified is no mystery.
        result["notes"].append(f"Two students are almost equally close to what was read: {top['name']} ({round(top['confidence'] * 100)}%) and {second['name']} ({round(second['confidence'] * 100)}%).")
    return result
