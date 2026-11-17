"""Sending Faculty's final grades to Gradescope (requirement, 2026-09-25).
AGENTS.md "Sending grades to Gradescope" is the rule book; `gradescope_web` is the transport.

For a term grading plan, a PREVIEW reads Gradescope (courses, rosters, the exam's versions, outlines, rubrics and who already has a
paper) and Faculty (the final grades exactly as an export counts them, each student's reviewed paper and exam version), and says
per section, version and question what would be sent and why anything would not. Nothing is written anywhere. The instructor's
SEND names that preview's fingerprint; Faculty reads everything again, refuses a changed preview (409), records the section and exam
links, the approved send and one pending unit per paper and per grade, and the background sender (`worker`, beside the Inbox
worker loop, under its own lock) works PARALLEL (24) units at once, papers and sheets up to UPLOADS_AT_ONCE, each on its own connection
of the one sign-in:

- A paper is the student's current reviewed assembly (pages in paper order, so a sheet fed back side first goes front first),
  each page grading found upside down turned upright (`_turned_pages`, 2026-09-27; lossless, the scan is never changed), merged
  in the document sandbox after every page's checksum is verified,
  uploaded as that student (matched by UIN = Gradescope SID, never guessed) to the Gradescope exam of THEIR version, under the
  name `faculty-<receipt id>.pdf`, so Faculty can always tell its own upload from anyone else's. A student who already has a paper
  there that Faculty did not upload is left alone, and so are their grades: that paper may not be the one Faculty graded.
- A grade checks exactly the Gradescope items its Faculty lines are (the Gradescope export's numbering plus equal points, checked
  on all 890 Test 1 lines), with Faculty's point adjustment and, as its only comment, the grade's batch lines from Certification
  and Export (`batch_adjustments.comment_text`, 2026-09-27) and, on a plan graded for participation, its participation line
  (GRADING "GRADED FOR PARTICIPATION", 2026-11-01; nothing else is ever sent as a comment). It is sent only when
  Gradescope would give exactly Faculty's score, only while it is still final, and only onto a question nobody has graded, or
  one still showing exactly what Faculty itself wrote there before (a corrected grade then replaces Faculty's own), or one showing
  exactly the grade someone else made that the instructor approved replacing (REPLACING OTHERS' GRADES, below).
- Every write is read back. Whatever Gradescope answered, the unit is `accepted` only when the page shows it; a request whose
  outcome is unknown is `uncertain` and resolved by reading Gradescope (its own file or grade there: accepted; nothing: failed),
  never by sending again. Failed units wait for the instructor's explicit Retry. A send never publishes anything; publishing is the
  instructor's separate request (`gradescope_publish`, 2026-10-02), and a send waits while one runs.
- An exam whose grades are already published is sent to only when the instructor confirmed it (requirement, 2026-09-26): the preview warns, the send names each published exam it confirms (`published`), and the
  sender writes to it only for a send that confirmed it. An exam published after the approval still gets nothing.
- PAPERS ONLY (requirement, 2026-09-28): a preview and a send may say `papers_only: true`. The plan is the same
  one, papers chosen by the same rules, but no grade is planned, no rubric is read and nothing is ticked; the choice is in the
  fingerprint, the request and the send's record, and an ordinary send later finds the papers already there and sends only the
  grades. Making the assignment and its rubric there is `gradescope_create`.
- REPLACING OTHERS' GRADES (requirement, 2026-10-01): a preview and a send may say
  `replace_others: true`. The preview then reads, for every grade on a paper Faculty uploaded earlier, the question's list of
  submissions on Gradescope (`question_submissions`, one page per question) and the grading page of each one somebody graded;
  where that grade is not Faculty's (not what Faculty last wrote there, or changed since) and not the same as Faculty's final
  grade, the grade is planned to replace it, carrying exactly what Gradescope showed (`items_json.others`: items, adjustment,
  comment, score, who last graded it). A grade Faculty sent that still shows Faculty's score there is taken as unchanged (the list
  shows scores, not items). The send must name how many it replaces (`replacing`, from the confirmation popup; checked on the
  server), and the sender replaces a grade only while Gradescope still shows exactly what the preview showed: anything changed
  after the preview is left alone. The send carries the preview's `replacements` back and plans them as shown without reading
  every graded question again (requirement, 2026-10-01: the second read doubled the wait, and a TA grading meanwhile made the
  whole send stale); a send without them reads again. The receipt keeps what was replaced (`receipt_json.replaced`). Without the
  choice nothing of this runs and nothing someone else made is ever written over.
"""
import hashlib
import json
import logging
import os
import re
import sqlite3
import threading
import time
import uuid
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor
from concurrent.futures import wait as wait_for
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from werkzeug.exceptions import Conflict, NotFound

from course_storage import StorageError, exclusive, timestamp
from document_identity import process
from rosters import connect

from . import gradescope_web as gw
from . import participation
from .batch_adjustments import batch_lines, comment_text
from .exam_versions import GRADABLE
from .gradescope_create import CreateMixin, map_created
from .gradescope_automatic import AutomaticSendMixin
from .gradescope_after import AfterCertifyingMixin
from .gradescope_mc import McCheckMixin
from .gradescope_mc_corrections import McCorrectionMixin
from .gradescope_mc_labels import McLabelMixin
from .gradescope_create import Processing as TemplateProcessing
from .gradescope_publish import PUBLISHING, PublishMixin
from .gradescope_rubric_import import RubricImportMixin
from .gradescope_sheets import SheetsMixin
from .models import digest, uid
from .progress import ONLY, decision_rows, only_args

LOG = logging.getLogger(__name__)
LOCK = "5f1c7e2a-9b8d-4c3e-a1f0-6d2b8e4c7a90"  # one sender across processes; holding it proves a unit left 'sending' lost its process
CONFIRMATION = "send"
WAIT = 60  # seconds before a unit whose request never left this machine is tried again
# Seconds before a grade whose paper Gradescope is still processing is tried again (requirement, 2026-09-25).
PROCESSING_WAIT = 15
PAUSE = 600  # seconds the sender waits after Gradescope refused the saved sign-in, unless the sign-in file is saved again sooner
# Gradescope busy (`gw.BUSY`; requirement, 2026-09-25): the whole sender waits BUSY_WAIT seconds, twice as long after each busy answer
# in a row up to BUSY_MAX, never less than a Retry-After Gradescope sent (honoured up to BUSY_CAP); a paper or grade that meets
# BUSY_TRIES busy answers in a row fails for the instructor's Retry.
BUSY_WAIT, BUSY_MAX, BUSY_CAP, BUSY_TRIES = 30, 900, 3600, 20
# How many units the background sender works on at once, each on its own connection of the one sign-in (requirement, 2026-09-25,
# after a sandbox test answered 1 to 24 parallel grade sends without a single refusal, 18 to 231 grades a minute, and 1 to 8 parallel
# 12-page uploads, up to 59 papers a minute, without one either). Uploads may take every slot (instructor: "Raise to 8"). Raised to
# 24 for grades and uploads alike (requirement, 2026-09-26); 24 parallel
# uploads were never tried in the sandbox, so a busy Gradescope is met by the busy pause and slow-down below, as for any unit.
PARALLEL, UPLOADS_AT_ONCE = 24, 24
QUANTUM = Decimal("0.0001")
LISTED = 400  # students named in a preview's lists; counts stay whole
# Connections of the preview's one sign-in that read questions at once when a preview looks for grades to replace (one page per
# question, then one per graded question submission): a person with a few tabs open, far below what the sender itself uses.
PREVIEW_READS = 8
# A send writes to an exam whose grades are published only when the instructor confirmed that exam for it (requirement, 2026-09-26). An exam
# published after the send was approved was never confirmed, so its papers and grades still wait for a new, confirmed send.
PUBLISHED = ("Grades on this Gradescope assignment were published after the send was approved, so students would see this at once. Faculty "
             "sends to a published assignment only when you confirm it: preview and send again to confirm, or leave it.")
UNIT = {"uploads": "frq_gradescope_uploads", "grades": "frq_gradescope_grades"}  # a written exam's send
# The sends and units of papers, grades, sheets, creations and publications (the automatic, run, makeup and MC kinds keep their own tables); bubble sheets (`gradescope_sheets`) have sends of their own.
# Making an assignment on Gradescope (`gradescope_create`) is units of the same sender too: one per Gradescope course, a step at a time.
# Publishing a plan's grades there (`gradescope_publish`, 2026-10-02, only on the instructor's request) is one unit per Gradescope assignment.
TABLES = {**UNIT, "sheets": "frq_gradescope_sheet_uploads", "creations": "frq_gradescope_creation_units", "publications": "frq_gradescope_publication_units"}
SENDS = {"uploads": "frq_gradescope_sends", "grades": "frq_gradescope_sends", "sheets": "frq_gradescope_sheet_sends", "creations": "frq_gradescope_creations",
         "publications": "frq_gradescope_publications"}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def amount(value):
    return Decimal(str(value)).quantize(QUANTUM)


def text_amount(value):
    return format(amount(value).normalize(), "f")


def after(seconds):
    """'in a minute', 'in 8 minutes', 'in 30 seconds': when a waiting unit is tried again."""
    seconds = int(seconds)
    if seconds < 60:
        return f"in {seconds} seconds"
    minutes = round(seconds / 60)
    return "in a minute" if minutes == 1 else f"in {minutes} minutes"


def later(seconds):
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()


def section_course_row(db, section_id):
    """The section's linked Gradescope course row, or None. GRADESCOPE "Owners (migration 026)": the table is append-only and the latest row counts."""
    return db.execute("SELECT * FROM frq_gradescope_courses WHERE section_id=? ORDER BY created_at DESC,rowid DESC LIMIT 1", (section_id,)).fetchone()


def request_head(data, confirmation, *, no_id, not_confirmed):
    """The request ID and the preview's fingerprint of a Send, Create or Publish, checked in this order (a ValueError says what is missing)."""
    request_id = str(data.get("request_id") or "")
    try:
        uuid.UUID(request_id)
    except ValueError:
        raise ValueError(no_id) from None
    if data.get("confirmation") != confirmation:
        raise ValueError(not_confirmed)
    fingerprint = str(data.get("fingerprint") or "")
    if not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
        raise ValueError("Send the fingerprint of the preview you looked at.")
    return request_id, fingerprint


def letter(value):
    """'Version A', 'A' or '' -> 'A' or ''."""
    match = re.fullmatch(r"(?:version\s*)?([a-z])", str(value or "").strip(), re.I)
    return match.group(1).upper() if match else ""


def paper_letters(people):
    """{section ID: the versions its current papers are in}, decided versions only ("" for a plan without versions): what the
    section's Gradescope exam must have. A version no paper of a section is in is not that section's concern (instructor,
    2026-10-06: a makeup's Version E, which one section's student sat, warned on every section)."""
    found = {}
    for person in people:
        if person["revision"] and person["version"].get("status") in GRADABLE:
            found.setdefault(person["section_id"], set()).add(person["version"].get("version") or "")
    return found


def rubric_numbers(rubric):
    """{Faculty line: (Gradescope question index, item number)} for a rubric imported from Gradescope's evaluation export, whose
    lines keep their `rubric_number` ("3.2"). None when any line did not come from that export (a drafted or adapted rubric, an
    added line), or the lines name more than one Gradescope question: then no Gradescope item can be named for sure."""
    numbers = {}
    for item in (rubric or {}).get("rules", {}).get("items", []):
        try:
            number = json.loads(item.get("original_source_text") or "{}")["rubric_number"]
            index, position = (int(part) for part in str(number).split("."))
        except (ValueError, KeyError, TypeError, AttributeError):
            return None
        numbers[item["id"]] = (index, position)
    if not numbers or len({index for index, _ in numbers.values()}) != 1:
        return None
    return numbers


def map_question(question, rubric):
    """(mapping, warnings) or (None, reason): the Gradescope question a Faculty question is, and the Gradescope item each of its
    lines is. The question is the Gradescope question whose number the lines' `rubric_number` names, which must print the same
    question number and be worth the same; each line is the item at its number in the export's order and must be worth the same.
    Its wording on Gradescope must be the wording Faculty imported from the export OR the line's current wording in Faculty (a
    correction made in either place); anything else is refused, since equal points alone cannot tell a reordered or inserted item
    from the right one. A line reworded only in Faculty is a warning. A different item count is refused too."""
    numbers = rubric_numbers(question.get("rubric"))
    if not numbers:
        return None, "Its rubric was not imported from Gradescope's export, so Faculty cannot tell which Gradescope item each line is."
    index = next(iter(numbers.values()))[0]
    candidates = [q for q in rubric.get("questions", []) if not q.get("parent_id") and int(q.get("index") or 0) == index]
    if len(candidates) != 1:
        return None, f"Gradescope's assignment has no question {index} to match its rubric numbers."
    target = candidates[0]
    if target.get("children"):
        return None, f"Gradescope's question {index} has parts; Faculty sends only whole questions."
    printed = str((question.get("content") or {}).get("printed_number") or "").strip()
    if printed and printed not in re.findall(r"\d+", str(target.get("title") or "")):
        return None, f"Gradescope's question {index} is “{target.get('title')}”, not question {printed}."
    worth = (question.get("content") or {}).get("max_points")
    if worth is not None and amount(target.get("weight") or 0) != amount(worth):
        return None, f"It is worth {text_amount(worth)} in Faculty and {text_amount(target.get('weight') or 0)} on Gradescope."
    order = gw.export_order(rubric, target["id"])
    items, warnings = {}, []
    # Under negative scoring a Faculty deduction of -2 is Gradescope's item weight 2 (its export prints it -2), as `line_weight` sends it.
    negative = question["rubric"]["rules"].get("mode") == "negative" and target.get("scoring_type") == "negative"
    for line in question["rubric"]["rules"]["items"]:
        position = numbers[line["id"]][1]
        if position > len(order):
            return None, f"Gradescope's question {index} has no item {index}.{position} ({line['id']})."
        item = order[position - 1]
        if item["points"] != (-amount(line["points"]) if negative else amount(line["points"])):
            shown = -item["points"] if negative else item["points"]
            return None, f"{line['id']} is worth {text_amount(line['points'])} in Faculty and {text_amount(shown)} on Gradescope."
        try:
            imported = str(json.loads(line.get("original_source_text") or "{}").get("description") or "")
        except ValueError:
            imported = ""
        known = {gw.normalized(imported), gw.normalized(line["description"])} - {""}
        if gw.normalized(item["name"]) not in known:
            return None, f"{line['id']} is “{item['name'][:80]}” on Gradescope, which is neither its imported nor its current wording."
        if gw.normalized(item["name"]) != gw.normalized(line["description"]):
            warnings.append(f"{line['id']} is worded differently in Faculty than on Gradescope (“{item['name'][:80]}”).")
        items[line["id"]] = item
    if len(order) != len(items):
        return None, f"Gradescope's question {index} has {len(order)} rubric items and Faculty's rubric {len(items)}; the rubrics differ."
    return {"question": target, "items": items, "order": order}, warnings


def expected(mapping, final):
    """(chosen Gradescope items, adjustment, Gradescope's score) for a Faculty final grade, or a reason it cannot be sent."""
    score = final.get("score") or {}
    selected = score.get("selected") or []
    missing = [line for line in selected if line not in mapping["items"]]
    if missing:
        return None, f"Its lines {', '.join(missing)} have no Gradescope item."
    chosen = sorted({mapping["items"][line]["id"] for line in selected})
    adjustment = sum((amount(a.get("points") or 0) for a in score.get("adjustments") or []), Decimal(0))
    # GRADING "PART CAPS": Gradescope cannot cap a part, so what the caps gave back goes with Faculty's adjustments (GRADESCOPE).
    adjustment += amount(score.get("part_caps") or 0)
    if not chosen and adjustment == 0:
        return None, "No rubric line is checked; on Gradescope that looks exactly like an ungraded question."
    crowded = [group for group in {i["group_id"] for i in mapping["order"] if i["exclusive"]}
               if sum(1 for i in mapping["order"] if i["group_id"] == group and i["id"] in chosen) > 1]
    if crowded:
        return None, "It checks two items of a Gradescope group whose items are mutually exclusive; Gradescope would keep only one."
    gradescope = amount(gw.gradescope_score(mapping["question"], mapping["order"], set(chosen), adjustment))
    if score.get("score") is None or gradescope != amount(score["score"]):
        return None, f"Gradescope would give {text_amount(gradescope)} for these items; Faculty's grade is {text_amount(score.get('score') or 0)}."
    return (chosen, adjustment, gradescope), None


# What kind of work a title names (GRADESCOPE "THE SAME KIND AND NUMBER"): an exam by any of its names, a quiz, homework.
KINDS = {"exam": "exam", "exams": "exam", "test": "exam", "tests": "exam", "midterm": "exam", "final": "exam",
         "quiz": "quiz", "quizzes": "quiz", "homework": "homework", "hw": "homework"}


def title_kinds(title):
    return {KINDS[word] for word in re.findall(r"[a-z]+", str(title or "").casefold()) if word in KINDS}


def fits_title(plan_title, title):
    """Whether a Gradescope assignment may be PROPOSED for the plan by its title: it carries a number of the plan's title ("Test 1"
    and "Common Exam 1"), and names no other kind of work (requirement, 2026-10-26). A title naming no kind, or a plan's naming none, is judged by
    its number alone, as before."""
    numbered = set(re.findall(r"\d+", plan_title or ""))
    if not numbered or not numbered & set(re.findall(r"\d+", title or "")):
        return False
    mine, theirs = title_kinds(plan_title), title_kinds(title)
    return not mine or not theirs or bool(mine & theirs)


def propose_course(courses, section_number, term):
    """The course whose short name ends in the section number (140-507) under the Faculty term's heading (Fall 2026), if only one."""
    found = [c for c in courses if re.search(rf"(?:^|\D){int(section_number)}$", c["short"].strip()) and gw.normalized(c["term"]) == gw.normalized(term)]
    return found[0] if len(found) == 1 else None


def papers_only(data):
    """Whether a preview or send request asks for the papers alone, without grades (only a JSON true counts)."""
    value = (data or {}).get("papers_only", False) if isinstance(data, dict) else False
    if not isinstance(value, bool):
        raise ValueError("Say papers_only as true or false.")
    return value


def replace_others(data):
    """Whether a preview or send request asks to replace grades someone else made on Gradescope (only a JSON true counts)."""
    value = (data or {}).get("replace_others", False) if isinstance(data, dict) else False
    if not isinstance(value, bool):
        raise ValueError("Say replace_others as true or false.")
    return value


def send_mode(data):
    """(papers_only, replace_others) of a preview or send request. Papers only sets no grade, so it replaces none."""
    only, replace = papers_only(data), replace_others(data)
    if only and replace:
        raise ValueError("Papers only sends no grade, so it replaces none: send papers and grades to replace grades on Gradescope.")
    return only, replace


def only_responses(data):
    """The one response a preview or send is limited to (a regrade request's Apply, REGRADES: that student's one grade, no paper and
    nothing else of the plan), or None for the whole plan."""
    value = (data or {}).get("only_responses") if isinstance(data, dict) else None
    if value is None:
        return None
    if not (isinstance(value, list) and len(value) == 1 and isinstance(value[0], str) and value[0]):
        raise ValueError("Name exactly one response to send.")
    if papers_only(data):
        raise ValueError("Papers only sends no grade: one response's grade is sent without papers_only.")
    return list(value)


def confirmed_replacing(data):
    """How many grades someone else made the instructor confirmed replacing (the send dialog's popup), 0 when not said."""
    value = data.get("replacing", 0)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("Say how many grades you confirmed replacing as a whole number.")
    return value


def shown_replacements(data):
    """The grades someone else made that the approved preview showed this send replacing (its `replacements`: [response ID, what
    Gradescope showed]), or None when the send does not carry them (a dialog opened before 2026-10-01's change): that send reads
    Gradescope again as its preview did."""
    if "replacements" not in data:
        return None
    value = data["replacements"]

    def shaped(r):
        if not (isinstance(r, list) and len(r) == 2 and isinstance(r[0], str) and isinstance(r[1], dict)
                and set(r[1]) == {"applied", "points", "comments", "score", "by"} and isinstance(r[1]["applied"], list)
                and all(isinstance(i, str) for i in r[1]["applied"]) and all(r[1][k] is None or isinstance(r[1][k], str) for k in ("points", "comments", "score", "by"))):
            return False
        return r[1]["score"] is None or re.fullmatch(r"-?\d+(\.\d+)?", r[1]["score"]) is not None
    if not isinstance(value, list) or not all(shaped(r) for r in value):
        raise ValueError("Send the replacements of the preview you looked at.")
    return value


def check_replacing(replacing, confirmed):
    """The send must confirm exactly the number of grades the plan, read again, replaces. Checked on the server: the popup is not
    the control (as for published exams)."""
    if replacing == confirmed:
        return
    if not confirmed:
        raise ValueError(f"This send replaces {replacing} {'grade' if replacing == 1 else 'grades'} someone else made on Gradescope. "
                         "Confirm replacing them and send again.")
    raise Conflict("The grades confirmed for replacing are not the ones on Gradescope now. Look at the new preview and send again.")


def published_warning(title, unit="paper"):
    """What the preview says about a section whose Gradescope exam has published grades."""
    what = "each sheet and its score" if unit == "sheet" else "each paper and grade"
    return (f"Grades on {title} are published on Gradescope: students see {what} as soon as Faculty writes it. "
            "Sending asks you to confirm it.")


def confirmed_published(data):
    """The published exams (Gradescope exam IDs) a send request says the instructor confirmed sending to."""
    named = data.get("published") or []
    if not isinstance(named, list) or not all(isinstance(n, str) and n for n in named):
        raise ValueError("Name the published Gradescope assignments you confirmed as a list of their IDs.")
    return sorted(set(named))


def check_published(found, confirmed):
    """The send must confirm exactly the published exams the plan, read again, sends to (`found`: exam ID -> title and the
    assignment IDs it covers). Checked on the server: the dialog's popup is not the control."""
    if sorted(found) == confirmed:
        return
    missing = [found[exam]["title"] for exam in sorted(found) if exam not in confirmed]
    if missing:
        raise ValueError(f"Grades on {', '.join(missing)} are published on Gradescope, so students would see what "
                         f"Faculty writes there at once. Confirm sending to {'this published assignment' if len(missing) == 1 else 'these published assignments'} and send again.")
    raise Conflict("The assignments confirmed as published are not the ones published on Gradescope now. Look at the new preview and send again.")


def published_record(found):
    """What a send keeps of the published exams it confirmed: every Gradescope assignment ID they cover, for the sender."""
    return sorted({str(i) for exam in found.values() for i in exam["ids"]})


def containers(assignments):
    """What a plan can be sent to: an exam with versions (a container and its version assignments) or a plain assignment.
    `published` names the assignments whose grades students can see: Faculty sends there only when the instructor confirms it."""
    found = []
    for row in assignments:
        if row["bubble_sheet"] or row["parent"]:
            continue
        if row["kind"] == "assignment_container":
            children = [r for r in assignments if r["parent"] == row["id"]]
            versions = {letter(r["version"]) or letter(r["title"][-1:]): r["id"] for r in children}
            found.append({"id": row["id"], "title": row["title"], "versions": {k: v for k, v in versions.items() if k},
                          "published": sorted(r["id"] for r in children if r["published"]) + ([row["id"]] if row["published"] else [])})
        elif row["kind"] == "assignment":
            found.append({"id": row["id"], "title": row["title"], "versions": {"": row["id"]}, "published": [row["id"]] if row["published"] else []})
    return found


class Processing(gw.GradescopeError):
    """Gradescope has the paper but has not finished processing it (a read: nothing was sent). Its grades try again soon."""

    def __init__(self, message):
        super().__init__(message, outcome="unavailable")


class GradescopeSender(SheetsMixin, CreateMixin, PublishMixin, McCheckMixin, McCorrectionMixin, McLabelMixin, AutomaticSendMixin,
                       AfterCertifyingMixin, RubricImportMixin):
    def __init__(self, frq, settings=None, session_factory=None):
        self.frq, self.inbox, self.rosters, self.path = frq, frq.inbox, frq.rosters, frq.path
        self.settings = settings or gw.Settings.environment()
        self.session_factory = session_factory or (lambda: gw.Session(self.settings))
        self.wake = threading.Event()
        self._session, self._paused = None, None
        self._local, self._sessions_lock, self._epoch = threading.local(), threading.Lock(), 0
        self._busy_until, self._busy_streak, self._busy_tries = None, 0, {}
        # The students and final grades `coverage` counts against, per (kind, assignment), kept only to be reused while a send
        # of that assignment is live (see `_coverage_basis`).
        self._coverage_basis_cache = {}

    # --- what the page shows without contacting Gradescope ----------------------------------------------------------------
    def status(self, aid):
        with self.rosters.lock():
            with connect(self.path) as db:
                live = db.execute("SELECT 1 FROM frq_gradescope_sends WHERE assignment_id=? AND status IN ('pending','running') LIMIT 1", (aid,)).fetchone()
            rows = self._coverage_basis(("written", aid), bool(live), lambda: [
                {k: r[k] for k in ("id", "state", "current_result_id", "current_review_id", "section_id", "enrollment_id")}
                for r in self.frq.responses(aid)])  # the states the workflow counts as final (approved, manual, automatic)
            with connect(self.path) as db:
                plan = self.frq.plan(db, aid)
                sends = []
                for row in db.execute("SELECT * FROM frq_gradescope_sends WHERE assignment_id=? ORDER BY created_at DESC,rowid DESC LIMIT 5", (aid,)):
                    counts = {}
                    for kind, table in UNIT.items():
                        counts[kind] = {r[0]: r[1] for r in db.execute(f"SELECT status,COUNT(*) FROM {table} WHERE send_id=? GROUP BY status", (row["id"],))}
                    waiting = db.execute("""SELECT error FROM frq_gradescope_uploads WHERE send_id=? AND status IN ('pending','uncertain') AND error!=''
                        UNION ALL SELECT error FROM frq_gradescope_grades WHERE send_id=? AND status IN ('pending','uncertain') AND error!='' LIMIT 1""", (row["id"], row["id"])).fetchone()
                    shown = json.loads(row["plan_json"])
                    late = shown.get("automatic") or {}
                    # A late paper's version that was not published when it was sent: no longer said once a publication of the plan
                    # has finished since, or while Faculty's own run publishes it (Certification and Export says where that is).
                    if late.get("unpublished_version") and db.execute("""SELECT 1 FROM frq_gradescope_publications WHERE assignment_id=? AND status='complete'
                            AND created_at>=? UNION ALL SELECT 1 FROM frq_gradescope_automatic_runs WHERE certification_id=? LIMIT 1""",
                                                                      (aid, row["created_at"], late.get("certification_id") or "")).fetchone():
                        shown = {**shown, "automatic": {k: v for k, v in late.items() if k != "unpublished_version"}}
                    sends.append({"id": row["id"], "status": row["status"], "created_at": row["created_at"], "updated_at": row["updated_at"],
                                  "plan": shown, "counts": counts, "waiting": waiting[0] if waiting else ""})
                targets = self._targets(db, plan)
                coverage = self._coverage(db, aid, rows)
                creations = self._creations(db, aid)
                automatic = self.automatic_status(db, aid)  # what Faculty did not send or add by itself, and why (only when there is any)
        # THE MC CHECK (gradescope_mc): Gradescope's last read beside Faculty's own scoring, from the records only.
        return {"assignment_id": aid, **self.settings.status(), "targets": targets, "sends": sends, "coverage": coverage, "creations": creations,
                "mc_check": self.mc_check_status(aid), **({"automatic": automatic} if automatic else {})}

    def _coverage_basis(self, key, live, compute):
        """What coverage counts against: the responses and their states (`responses()`, about 0.4 s on Test 1) or the matched bubble
        sheets. The panel polls every 4 s while a send is live, under the roster lifecycle lock the sender threads also need, so while
        one is live the basis read at its first poll is reused; the receipts it is compared with are read fresh on every poll, so the
        sent counts still move. The first read after the send settles (and every read with none live) counts afresh. Only a basis
        read while a send was live is reused, so the first live poll never serves one read before the send. Read only."""
        kept = self._coverage_basis_cache.get(key)
        if live and kept and kept[1]:
            return kept[0]
        value = compute()
        if live:
            self._coverage_basis_cache[key] = (value, True)
        else:
            self._coverage_basis_cache.pop(key, None)  # no later call could serve it, so it is not kept
        return value

    @staticmethod
    def _grade_coverage(db, aid, rows, only=None, decisions=None):
        """{response_id: sent|changed|not_sent} for every FINAL response (state approved, manual or auto_accepted), from the receipts
        alone: `sent` when the latest accepted grade receipt is for exactly the current decision and result, `changed` when it is for
        another, `not_sent` without one. The ONE rule `_coverage` totals and `coverage_detail` breaks down. `only`: the IDs of `rows`
        when they are a few of the plan's (a drill-down), to read those alone; every lookup below is by one of them. `decisions`:
        `progress.decision_rows` of these rows when the caller has them (the grading overview that Home and the Assistant read, Certification and Export): each final
        grade's decision and result are then taken from there, the very pairs read below, instead of read again."""
        final = [r for r in rows if r["state"] in ("approved", "manual", "auto_accepted")]
        limit = lambda column: ONLY.format(column) if only is not None else ""
        known = decisions is not None and all(r["id"] in decisions for r in final)
        # Each map is read only when a final grade looks in it (PERFORMANCE, 2026-10-02): the automatic acceptances for an automatic
        # grade, the reviews for the instructor's.
        reviewed = {r[0]: r[1] for r in db.execute(f"""SELECT v.id,v.result_id FROM frq_reviews v JOIN frq_responses r ON r.current_review_id=v.id
            WHERE r.assignment_id=?{limit('r.id')}""", (aid, *only_args(only)))} if not known and any(r["state"] != "auto_accepted" for r in final) else {}
        # The plan's current results as a list rather than a join, so SQLite looks each up in the view's arms; the same pairs.
        automatic = {r[0]: r[1] for r in db.execute(f"""SELECT a.result_id,a.id FROM frq_all_acceptances a
            WHERE a.result_id IN (SELECT current_result_id FROM frq_responses WHERE assignment_id=?{limit('id')}) AND a.kind='automatic' AND a.active=1""",
            (aid, *only_args(only)))} if not known and any(r["state"] == "auto_accepted" for r in final) else {}
        if known:  # `decision_rows` names an automatic grade's active acceptance and a review's own result, as the reads above do
            for row in final:
                entry = decisions[row["id"]]
                if entry["kind"] == "automatic":
                    automatic[row["current_result_id"]] = entry["decision_id"]
                elif entry["kind"] == "review":
                    reviewed[row["current_review_id"]] = entry["result_id"]
        # The latest accepted receipt per response, its items as stored: the comment Gradescope got is read only from a receipt for
        # the current decision, once per distinct items (`_receipt_comment` is pure).
        receipts = {r[0]: ((r[1], r[2]), r[3]) for r in db.execute(f"""SELECT response_id,decision_id,result_id,items_json
            FROM frq_gradescope_grades WHERE assignment_id=? AND status='accepted'{limit('response_id')} ORDER BY created_at,rowid""",
            (aid, *only_args(only)))} if final else {}
        received = {}

        def received_comment(items_json):
            if items_json not in received:
                received[items_json] = GradescopeSender._receipt_comment(items_json)
            return received[items_json]
        comments = GradescopeSender.expected_comments(db, aid)
        states = {}
        for row in final:
            current = (automatic.get(row["current_result_id"]), row["current_result_id"]) if row["state"] == "auto_accepted" \
                else (row["current_review_id"], reviewed.get(row["current_review_id"]))
            receipt = receipts.get(row["id"])
            # A grade whose batch lines are not the comment Gradescope got (sent before 2026-09-27, or lines changed) is not on it yet.
            states[row["id"]] = "sent" if receipt and receipt[0] == current and received_comment(receipt[1]) == comments.get(row["id"]) \
                else "changed" if receipt else "not_sent"
        return states

    @staticmethod
    def _receipt_comment(items_json):
        try:
            return gw.comment((json.loads(items_json or "{}") or {}).get("comment"))
        except (ValueError, AttributeError):
            return None

    @staticmethod
    def expected_comments(db, aid):
        """{response_id: comment} of the current grades that carry batch lines (`batch_adjustments.comment_text`), from the counted
        decision's score (the current review's, else the current result's); a response not listed carries none. Catalog reads only."""
        lines = batch_lines(db, aid)
        # GRADING "GRADED FOR PARTICIPATION": every grade of such a plan below its worth is sent with the participation line.
        counted = participation.enabled(db, aid)
        if not lines and not counted:
            return {}
        found = {}
        ids = [r[0] for r in db.execute("SELECT id FROM frq_responses WHERE assignment_id=?", (aid,))] if counted else list(lines)
        for start in range(0, len(ids), 500):
            chunk = ids[start:start + 500]
            for r in db.execute(f"""SELECT r.id,COALESCE(v.score_json,x.score_json) score FROM frq_responses r LEFT JOIN frq_reviews v ON v.id=r.current_review_id
                    LEFT JOIN frq_results x ON x.id=r.current_result_id WHERE r.id IN ({",".join("?" * len(chunk))})""", chunk):
                if counted:
                    score = participation.stored_comment_score(r[1])
                    text = comment_text(score.get("adjustments"), [*lines.get(r[0], []), *participation.line(score)])
                else:
                    text = comment_text((json.loads(r[1] or "null") or {}).get("adjustments"), lines.get(r[0]))
                if text:
                    found[r[0]] = gw.comment(text)
        return found

    @staticmethod
    def _coverage(db, aid, rows, decisions=None):
        """What is on Gradescope now across EVERY send, from the receipts alone (read only; nothing is contacted): students with a
        paper Faculty uploaded (accepted), and how many of the current final grades have an accepted grade receipt for exactly
        that decision and result. A final grade with an accepted receipt for an earlier decision is `changed`; the rest are
        `not_sent`. The latest send's own card says only what that send did (2026-09-26: a 10-paper follow-up send read as if
        only 10 papers had gone). `decisions`: as `_grade_coverage` takes them."""
        states = GradescopeSender._grade_coverage(db, aid, rows, decisions=decisions)
        students = {(r["section_id"], r["enrollment_id"]) for r in rows}
        uploaded = {tuple(r) for r in db.execute("SELECT DISTINCT section_id,enrollment_id FROM frq_gradescope_uploads WHERE assignment_id=? AND status='accepted'",
                                                 (aid,))}
        sent, changed = sum(v == "sent" for v in states.values()), sum(v == "changed" for v in states.values())
        return {"papers": {"total": len(students), "sent": len(students & uploaded)},
                "grades": {"final": len(states), "sent": sent, "changed": changed, "not_sent": len(states) - sent - changed}}

    @staticmethod
    def coverage_detail(db, aid, rows, participants=(), decisions=None):
        """`_coverage` per question and per student, for Certification and Export (read only, receipts only; Gradescope is never
        contacted, so what is published there is not known here). Same rule as `_coverage` (`_grade_coverage`), plus:

        - `same_content` (critique C7): a `changed` grade whose accepted receipt checked exactly the Gradescope items the current
          final's lines are, with the same adjustment and expected score, IS on Gradescope (approving an automatic grade unchanged
          makes a new decision, not a new grade). It is covered here and still counted `changed` by `_coverage`.
        - what Faculty can NEVER send again (critique G), so no "Send to Gradescope" is offered for it: a question whose rubric was
          neither imported from Gradescope's export nor created there by Faculty (`unsendable`), a grade with no line checked and no adjustment (it would look
          ungraded there), and a student whose reviewed paper changed after Faculty's accepted upload (Faculty never replaces a
          submission) (`never`).

        `rows`: `responses()` rows; `participants`: exam participants, each with `collection` (`_collection`) when paper changes
        are to be seen; `decisions`: `progress.decision_rows(...)` when the caller has it (one catalog read otherwise).
        Answer: {questions: {qid: {final, sent, same_content, changed, not_sent, not_final, unsendable}}, students: {row: {paper,
        paper_changed, grades: {sent, changed, not_sent}}}, uncovered: [[response_id, status]], papers_missing: [row],
        unsendable: {qid: reason}, never: {"grades": {response_id: reason}, "papers": {row: reason}}, totals: {...}}."""
        decisions = decisions if decisions is not None else decision_rows(db, aid, rows)
        states = GradescopeSender._grade_coverage(db, aid, rows, decisions=decisions)
        comments = GradescopeSender.expected_comments(db, aid)
        decisions = participation.counted_entries(db, aid, decisions)  # what was sent: GRADING "GRADED FOR PARTICIPATION"
        latest = {}
        for r in db.execute("""SELECT response_id,items_json,adjustment,expected_score FROM frq_gradescope_grades WHERE assignment_id=? AND status='accepted'
                ORDER BY created_at,rowid""", (aid,)):
            latest[r["response_id"]] = r
        rubrics = {}
        for r in db.execute("""SELECT p.question_id,v.rules_json FROM frq_assignment_questions p JOIN frq_rubric_revisions v ON v.id=p.rubric_id
                WHERE p.assignment_id=?""", (aid,)):
            try:
                rubrics[r[0]] = {"rules": json.loads(r[1])}
            except ValueError:
                rubrics[r[0]] = None
        # A question Faculty itself made on Gradescope is sent by what it made (its creation receipt, `map_created`), whatever its
        # rubric's origin: only a rubric neither imported from Gradescope's export NOR created there by Faculty can never be sent
        # (2026-09-28: Quiz 2's drafted rubrics, created on Gradescope and all 1,304 grades sent, were listed as never sendable).
        created = {r[0] for r in db.execute("SELECT DISTINCT question_id FROM frq_gradescope_created_questions WHERE assignment_id=?", (aid,))}
        unsendable = {qid: "Its rubric was neither imported from Gradescope's export nor created on Gradescope by Faculty, so Faculty cannot tell "
                           "which Gradescope item each line is."
                      for qid, rubric in rubrics.items() if not rubric_numbers(rubric) and qid not in created}
        row_key = lambda section_id, enrollment_id: f"{section_id}:{enrollment_id}"
        questions, students, uncovered, never_grades = {}, {}, [], {}
        for row in rows:
            question = questions.setdefault(row["question_id"], {"final": 0, "sent": 0, "same_content": 0, "changed": 0, "not_sent": 0, "not_final": 0,
                                                                 "unsendable": unsendable.get(row["question_id"])})
            state = states.get(row["id"])
            if not state:
                question["not_final"] += 1
                continue
            question["final"] += 1
            decision = decisions.get(row["id"]) or {}
            if state == "changed" and GradescopeSender._same_content(latest.get(row["id"]), decision, comments.get(row["id"])):
                state = "same_content"
            question[state] += 1
            student = students.setdefault(row_key(row["section_id"], row["enrollment_id"]),
                                          {"paper": False, "paper_changed": False, "grades": {"sent": 0, "changed": 0, "not_sent": 0}})
            student["grades"]["sent" if state in ("sent", "same_content") else state] += 1
            if state in ("changed", "not_sent"):
                uncovered.append([row["id"], state])
                if row["question_id"] in unsendable:
                    never_grades[row["id"]] = unsendable[row["question_id"]]
                elif decision.get("kind") and not decision.get("selected") and not any(a not in (None, "0") for a in decision.get("adjustments") or []):
                    never_grades[row["id"]] = "No rubric line is checked; on Gradescope that looks exactly like an ungraded question."
        uploads = {}
        for r in db.execute("""SELECT section_id,enrollment_id,assembly_revision_id FROM frq_gradescope_uploads WHERE assignment_id=? AND status='accepted'
                ORDER BY created_at,rowid""", (aid,)):
            uploads[row_key(r[0], r[1])] = r[2]
        papers = {row_key(p["section_id"], p["enrollment_id"]): ((p.get("collection") or {}).get("assembly_revision_id")) for p in participants}
        never_papers = {}
        for key, student in students.items():
            student["paper"] = key in uploads
            current = papers.get(key)
            if key in uploads and current and uploads[key] != current:
                student["paper_changed"] = True
                never_papers[key] = ("Gradescope has the earlier reviewed paper Faculty uploaded; the paper has changed since, and Faculty never "
                                     "replaces a submission there.")
        papers_missing = sorted(key for key, student in students.items() if not student["paper"])
        totals = {"final": sum(q["final"] for q in questions.values()), "sent": sum(q["sent"] + q["same_content"] for q in questions.values()),
                  "changed": sum(q["changed"] for q in questions.values()), "not_sent": sum(q["not_sent"] for q in questions.values()),
                  "papers": {"students": len(students), "sent": sum(1 for s in students.values() if s["paper"])}}
        return {"questions": questions, "students": students, "uncovered": sorted(uncovered), "papers_missing": papers_missing,
                "unsendable": unsendable, "never": {"grades": never_grades, "papers": never_papers}, "totals": totals}

    @staticmethod
    def _same_content(receipt, decision, comment=None):
        """Whether an accepted grade receipt checked exactly what the current final grade would: the Gradescope items its lines are
        (through the receipt's own line map), the same adjustment, the same score and the same comment (its batch lines)."""
        if not receipt or not decision or not decision.get("kind"):
            return False
        try:
            items = json.loads(receipt["items_json"])
            lines = items.get("lines") or {}
            chosen = sorted({lines[line] for line in decision["selected"]})
            adjustment = sum((Decimal(a) for a in decision.get("adjustments") or [] if a is not None), Decimal(0)) + Decimal(decision.get("part_caps") or 0)
            return (chosen == sorted(items.get("chosen") or []) and amount(receipt["adjustment"]) == adjustment
                    and decision.get("score") is not None and amount(receipt["expected_score"]) == amount(decision["score"])
                    and gw.comment(items.get("comment")) == gw.comment(comment))
        except (KeyError, ValueError, TypeError, ArithmeticError):
            return False

    def problems(self, aid, send_id):
        """The units of a send that did not go through, with the student's name (never a UIN) and the reason."""
        with self.rosters.lock(), connect(self.path) as db:
            plan = self.frq.plan(db, aid)
            send = db.execute("SELECT id FROM frq_gradescope_sends WHERE id=? AND assignment_id=?", (send_id, aid)).fetchone()
            if not send:
                raise NotFound("That send was not found for this assignment.")
            names = self._names(db, plan)
            listed = []
            for row in db.execute("""SELECT u.section_id,u.enrollment_id,u.status,u.error,'' question,s.number FROM frq_gradescope_uploads u
                    JOIN course_sections s ON s.id=u.section_id WHERE u.send_id=? AND u.status NOT IN ('accepted','pending','sending')
                    UNION ALL SELECT r.section_id,r.enrollment_id,g.status,g.error,q.question_key,s.number FROM frq_gradescope_grades g
                    JOIN frq_responses r ON r.id=g.response_id JOIN frq_questions q ON q.id=r.question_id JOIN course_sections s ON s.id=r.section_id
                    WHERE g.send_id=? AND g.status NOT IN ('accepted','pending','sending') ORDER BY 6,2""", (send_id, send_id)):
                listed.append({"name": names.get((row[0], row[1]), ""), "section": row[5], "status": row[2], "question": row[4], "reason": row[3]})
        return {"send_id": send_id, "problems": listed}

    # --- preview and send -------------------------------------------------------------------------------------------------
    def preview(self, aid, data=None):
        only, replace = send_mode(data)
        plan = self._plan(aid, (data or {}).get("targets"), papers_only=only, replace=replace, responses=only_responses(data))
        return plan["view"]

    def send(self, aid, data):
        request_id, fingerprint = request_head(data, CONFIRMATION, no_id="Send needs a request ID from the preview.",
                                               not_confirmed="Confirm the send: nothing goes to Gradescope without it.")
        requested = data.get("targets") or {}
        confirmed = confirmed_published(data)
        only, replace = send_mode(data)
        responses = only_responses(data)
        replacing = confirmed_replacing(data) if replace else 0
        shown = shown_replacements(data) if replace else None
        # Named only when true, so a send of papers and grades keeps the request hash it always had. The replacements shown are
        # pinned by the fingerprint, so they are not named again here.
        command = digest({"assignment_id": aid, "request_id": request_id, "fingerprint": fingerprint, "targets": requested,
                          **({"published": confirmed} if confirmed else {}), **({"papers_only": True} if only else {}),
                          **({"replace_others": True, "replacing": replacing} if replace else {}),
                          **({"only_responses": responses} if responses else {})})
        # THE BUBBLE SHEETS TOO (requirement, 2026-10-26): the
        # dialog's Bubble sheets part is the Bubble sheets page's own send of the preview it showed, under a request ID made from this
        # one, so a retried request returns both sends already recorded and never makes a second. It is recorded once the written
        # send has passed its own checks and just before it (the sheets go up before the written grades, as the MC check wants); its
        # own checks refuse it (409, nothing recorded) as they would on its own page.
        sheets = data.get("sheets")
        if sheets is not None and not isinstance(sheets, dict):
            raise ValueError("Name the bubble-sheet preview this send includes.")

        def send_sheets():
            return self.sheet_send(aid, {"request_id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"faculty-sheets:{request_id}")), "confirmation": CONFIRMATION,
                                         "fingerprint": sheets.get("fingerprint"), "targets": sheets.get("targets") or {},
                                         **({"published": sheets["published"]} if sheets.get("published") else {})}) if sheets else None
        with self.rosters.lock(), connect(self.path) as db:
            earlier = db.execute("SELECT * FROM frq_gradescope_sends WHERE request_id=?", (request_id,)).fetchone()
        if earlier:
            if earlier["request_sha256"] != command or earlier["assignment_id"] != aid:
                raise Conflict("That request ID was already used for a different send.")
            sheet_receipt = send_sheets()  # recorded with it before: answered from its record
            return {**self._receipt(earlier), **({"sheets": sheet_receipt} if sheet_receipt else {})}
        # Everything read again, Gradescope included, except the grades of others the preview showed: they are planned as it showed
        # them, and the sender checks each one on Gradescope before it writes (2026-10-01: no second read of every graded question).
        plan = self._plan(aid, requested, papers_only=only, replace=replace, shown=shown, responses=responses)
        if plan["view"]["fingerprint"] != fingerprint or (shown is not None and len(shown) != plan["view"]["replacing"]["grades"]):
            raise Conflict("Something changed since the preview, in Faculty or on Gradescope. Look at the new preview and send again.")
        if plan["view"]["running"]:  # checked again in the transaction; said first, since a running send holds its own papers
            raise Conflict("A send to Gradescope is still running for this assignment.")
        if not plan["uploads"] and not plan["grades"]:
            raise Conflict("There is nothing to send.")
        if responses and (plan["uploads"] or len(plan["grades"]) != 1 or any(s.get("proposed") for s in plan["view"]["sections"])):
            # One response's grade goes onto the paper Faculty already uploaded, to the section's linked exam, or not at all.
            raise Conflict("Only that one grade can be sent this way, onto the paper Faculty uploaded to the section's linked Gradescope assignment.")
        check_published(plan["published"], confirmed)
        if replace:
            check_replacing(plan["view"]["replacing"]["grades"], replacing)
        sheet_receipt = send_sheets()
        now, send_id = timestamp(), uid()
        try:
            return {**self._record_send(aid, plan, request_id, command, fingerprint, now, send_id), **({"sheets": sheet_receipt} if sheet_receipt else {})}
        except sqlite3.IntegrityError:
            # Another send recorded some of these papers or grades while this one was being checked: its preview is out of date.
            raise Conflict("Something changed since the preview (another send recorded some of these). Look at the new preview and send again.") from None

    def _record_send(self, aid, plan, request_id, command, fingerprint, now, send_id, actor="workspace", automatic=None, after=None):
        """`automatic` and `after` (GRADESCOPE "AUTOMATIC LATE-PAPER SEND"): what the plan's card says Faculty sent by itself, and what
        is written in the same transaction (the automatic send's own row); `actor` names who sent it."""
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            earlier = db.execute("SELECT * FROM frq_gradescope_sends WHERE request_id=?", (request_id,)).fetchone()
            if earlier:
                if earlier["request_sha256"] != command:
                    raise Conflict("That request ID was already used for a different send.")
                return self._receipt(earlier)
            if db.execute("SELECT 1 FROM frq_gradescope_sends WHERE assignment_id=? AND status IN ('pending','running')", (aid,)).fetchone():
                raise Conflict("A send to Gradescope is still running for this assignment.")
            if self._publishing(db, aid):  # what it writes would land on an assignment published under it, and stop there
                raise Conflict(PUBLISHING)
            links = self._record_links(db, aid, plan["targets"], now)
            summary = {"targets": plan["view"]["sections"], "summary": plan["view"]["summary"]}
            if plan["view"]["papers_only"]:
                summary["papers_only"] = True  # the panel's card says no grades were part of this send
            if plan["view"].get("only_responses"):
                summary["only_responses"] = plan["view"]["only_responses"]  # a regrade request's one grade (REGRADES)
            replacing = plan["view"]["replacing"]["grades"] if plan["view"].get("replace_others") else None
            if replacing is not None:
                summary["replace_others"], summary["replacing"] = True, replacing  # the card says how many grades of others it replaces
            if plan["published"]:
                summary["published"] = published_record(plan["published"])  # what the sender may write to although its grades are published
            if automatic:
                summary["automatic"] = automatic  # a late paper Faculty certified and sent by itself
            db.execute("INSERT INTO frq_gradescope_sends VALUES(?,?,?,?,?,?,'pending',?,?,?)",
                       (send_id, aid, request_id, command, fingerprint, json.dumps(summary, sort_keys=True), actor, now, now))
            upload_ids = {}
            for unit in plan["uploads"]:
                upload_ids[unit["key"]] = uid()
                db.execute("""INSERT INTO frq_gradescope_uploads(id,send_id,assignment_id,section_id,enrollment_id,exam_version,course_id,gradescope_assignment_id,
                    gradescope_user_id,assembly_revision_id,selection_sha256,pages,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,'pending',?,?)""",
                    (upload_ids[unit["key"]], send_id, aid, unit["section_id"], unit["enrollment_id"], unit["version"], unit["course_id"], unit["assignment"],
                     unit["user"], unit["revision"], unit["selection"], unit["pages"], now, now))
            for unit in plan["grades"]:
                upload = upload_ids.get(unit["key"]) or unit["upload_id"]
                db.execute("""INSERT INTO frq_gradescope_grades(id,send_id,upload_id,assignment_id,response_id,decision,decision_id,result_id,rubric_id,gradescope_question_id,
                    items_json,adjustment,expected_score,replaces_id,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,'pending',?,?)""",
                    (uid(), send_id, upload, aid, unit["response_id"], unit["decision"], unit["decision_id"], unit["result_id"], unit["rubric_id"], unit["question"],
                     json.dumps(unit["items"], sort_keys=True), text_amount(unit["adjustment"]), text_amount(unit["expected"]), unit["replaces"], now, now))
            self.frq.event(db, aid, "gradescope_send", {"send_id": send_id, "request_id": request_id, "fingerprint": fingerprint, "links": links,
                                                        "uploads": len(plan["uploads"]), "grades": len(plan["grades"]),
                                                        **({"papers_only": True} if plan["view"]["papers_only"] else {}),
                                                        **({"only_responses": plan["view"]["only_responses"]} if plan["view"].get("only_responses") else {}),
                                                        **({"replace_others": True, "replacing": replacing} if replacing is not None else {}),
                                                        **({"published_confirmed": sorted(plan["published"])} if plan["published"] else {}),
                                                        **({"automatic": automatic} if automatic else {})}, actor)
            if after:
                after(db, send_id)
            row = db.execute("SELECT * FROM frq_gradescope_sends WHERE id=?", (send_id,)).fetchone()
        self.wake.set()
        return self._receipt(row)

    def cancel(self, aid, send_id):
        """Stops a send: nothing still waiting is sent. A paper or grade already on its way finishes and keeps its receipt."""
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM frq_gradescope_sends WHERE id=? AND assignment_id=?", (send_id, aid)).fetchone()
            if not row:
                raise NotFound("That send was not found for this assignment.")
            if row["status"] in ("pending", "running"):
                now = timestamp()
                for table in UNIT.values():
                    db.execute(f"UPDATE {table} SET status='cancelled',updated_at=? WHERE send_id=? AND status='pending'", (now, send_id))
                db.execute("UPDATE frq_gradescope_sends SET status='cancelled',updated_at=? WHERE id=?", (now, send_id))
                self.frq.event(db, aid, "gradescope_send_cancelled", {"send_id": send_id})
            row = db.execute("SELECT * FROM frq_gradescope_sends WHERE id=?", (send_id,)).fetchone()
        return self._receipt(row)

    def retry(self, aid, send_id):
        """The instructor's explicit Retry: units that failed (Gradescope did not keep them, as Faculty read back) are sent again."""
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM frq_gradescope_sends WHERE id=? AND assignment_id=?", (send_id, aid)).fetchone()
            if not row:
                raise NotFound("That send was not found for this assignment.")
            if row["status"] == "cancelled":
                raise Conflict("A cancelled send is not retried. Preview and send again.")
            if db.execute("SELECT 1 FROM frq_gradescope_sends WHERE assignment_id=? AND id!=? AND status IN ('pending','running')", (aid, send_id)).fetchone():
                raise Conflict("Another send to Gradescope is running for this assignment.")
            if self._publishing(db, aid):
                raise Conflict(PUBLISHING)
            now = timestamp()
            # Gradescope keeps one paper per student and Faculty one open grade per response: what a later send has uploaded, or
            # is sending, since this one failed is not sent again (its grades stay failed with it; that send carries them).
            retried = db.execute("""UPDATE frq_gradescope_uploads SET status='pending',not_before=NULL,error='',updated_at=? WHERE send_id=? AND status='failed'
                AND NOT EXISTS(SELECT 1 FROM frq_gradescope_uploads o WHERE o.gradescope_assignment_id=frq_gradescope_uploads.gradescope_assignment_id
                    AND o.gradescope_user_id=frq_gradescope_uploads.gradescope_user_id AND o.id!=frq_gradescope_uploads.id
                    AND o.status IN ('pending','sending','accepted','uncertain'))""", (now, send_id)).rowcount
            retried += db.execute("""UPDATE frq_gradescope_grades SET status='pending',not_before=NULL,error='',updated_at=? WHERE send_id=? AND status='failed'
                AND upload_id IN (SELECT id FROM frq_gradescope_uploads WHERE status IN ('pending','accepted'))
                AND NOT EXISTS(SELECT 1 FROM frq_gradescope_grades o WHERE o.response_id=frq_gradescope_grades.response_id AND o.id!=frq_gradescope_grades.id
                    AND o.status IN ('pending','sending','uncertain'))""", (now, send_id)).rowcount
            if not retried:
                left = db.execute("""SELECT (SELECT COUNT(*) FROM frq_gradescope_uploads WHERE send_id=? AND status='failed')
                    + (SELECT COUNT(*) FROM frq_gradescope_grades WHERE send_id=? AND status='failed')""", (send_id, send_id)).fetchone()[0]
                raise Conflict("What failed in this send was sent again by a later send (or is being sent); preview and send again for anything still missing."
                               if left else "Nothing in this send failed.")
            db.execute("UPDATE frq_gradescope_sends SET status='running',updated_at=? WHERE id=?", (now, send_id))
            self.frq.event(db, aid, "gradescope_send_retried", {"send_id": send_id, "units": retried})
            row = db.execute("SELECT * FROM frq_gradescope_sends WHERE id=?", (send_id,)).fetchone()
        self.wake.set()
        return self._receipt(row)

    @staticmethod
    def _receipt(row):
        return {"send_id": row["id"], "request_id": row["request_id"], "status": row["status"], "created_at": row["created_at"],
                "plan": json.loads(row["plan_json"])}

    # --- links ------------------------------------------------------------------------------------------------------------
    @staticmethod
    def _targets(db, plan):
        """The latest recorded choice per section: its Gradescope course (the section's) and this plan's exam in it."""
        found = []
        for section in db.execute("""SELECT s.id,s.number FROM exam_event_sections x JOIN course_sections s ON s.id=x.section_id
                WHERE x.event_id=? ORDER BY s.number""", (plan["event_id"],)).fetchall():
            course = section_course_row(db, section["id"])
            target = db.execute("SELECT * FROM frq_gradescope_targets WHERE assignment_id=? AND section_id=? ORDER BY created_at DESC,rowid DESC LIMIT 1",
                                (plan["id"], section["id"])).fetchone()
            same = bool(target and course and target["course_link_id"] == course["id"])
            found.append({"section_id": section["id"], "section": section["number"], "course_id": course["course_id"] if course else "",
                          "course_title": course["course_title"] if course else "",
                          "container_id": target["container_id"] if same else "", "container_title": target["container_title"] if same else ""})
        return found

    # A section's Gradescope course, set once on its course page (requirement, 2026-09-27). The same SECTION-owned, append-only
    # `frq_gradescope_courses` row a send records; the dialogs then show it as a fact, with Change for the rare swap.
    @staticmethod
    def _section(db, section_id):
        row = db.execute("""SELECT s.id,s.number,t.term,t.term_other,t.year FROM course_sections s JOIN course_terms t ON t.id=s.term_id
            WHERE s.id=?""", (str(section_id),)).fetchone()
        if not row:
            raise NotFound("That section was not found.")
        from seating import term_label
        return {"section_id": row["id"], "section": row["number"], "term_label": term_label(row)}

    @staticmethod
    def _section_course(db, section_id):
        row = section_course_row(db, section_id)
        return {"course_id": row["course_id"], "course_title": row["course_title"], "linked_at": row["created_at"]} if row else None

    def section_link(self, section_id):
        """The section's linked Gradescope course, from Faculty's own records: Gradescope is not contacted."""
        with self.rosters.lock(), connect(self.path) as db:
            section = self._section(db, section_id)
            return {**section, **self.settings.status(), "linked": self._section_course(db, section["section_id"])}

    def section_courses(self, section_id):
        """The instructor's Gradescope courses to choose from (never the coordinator's), with Faculty's proposal for this section: the
        course whose short name ends in the section number under this term. Reads Gradescope; writes nothing."""
        view = self.section_link(section_id)
        if not view["configured"]:
            return {**view, "courses": [], "proposed": ""}
        session = self.session_factory()
        try:
            courses = session.courses()
        except gw.GradescopeError as error:
            raise Conflict(f"Gradescope could not be read: {error}") from None
        finally:
            session.close()
        proposed = propose_course(courses, view["section"], view["term_label"])
        return {**view, "courses": [{"id": c["id"], "short": c["short"], "name": c["name"], "term": c["term"]} for c in courses],
                "proposed": proposed["id"] if proposed else ""}

    def link_section(self, section_id, data):
        """Records the section's Gradescope course. `base` names the course the page showed ('' for none): anything newer is a 409.
        The course is checked against the instructor's courses on Gradescope first, so a link always names one of them."""
        course_id = str(data.get("course_id") or "")
        if not re.fullmatch(r"\d{1,20}", course_id):
            raise ValueError("Choose a Gradescope course.")
        if not isinstance(data.get("base", ""), str):
            raise ValueError("Name the course the page showed as base ('' for none).")
        base = data.get("base") or ""
        view = self.section_link(section_id)
        if not view["configured"]:
            raise Conflict("Gradescope is not set up for this Faculty.")
        session = self.session_factory()
        try:
            course = next((c for c in session.courses() if c["id"] == course_id), None)
        except gw.GradescopeError as error:
            raise Conflict(f"Gradescope could not be read: {error}") from None
        finally:
            session.close()
        if not course or gw.forbidden(course["short"], course["name"]):
            raise Conflict("That Gradescope course is not one of your courses (or is one Faculty never touches).")
        title = course["short"] or course["name"]
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            current = self._section_course(db, view["section_id"])
            if (current["course_id"] if current else "") != base:
                raise Conflict("This section's Gradescope course was changed elsewhere. Look at it again.")
            if not current or current["course_id"] != course_id or current["course_title"] != title:
                db.execute("INSERT INTO frq_gradescope_courses VALUES(?,?,?,?,'workspace',?)", (uid(), view["section_id"], course_id, title, timestamp()))
            linked = self._section_course(db, view["section_id"])
        return {**view, "linked": linked}

    def _record_links(self, db, aid, targets, now, table="frq_gradescope_targets"):
        links = []
        for section_id, target in sorted(targets.items()):
            if gw.forbidden(target["course_title"], target.get("course_name", "")):
                raise Conflict("That is the course Faculty never touches (Math 140 for Instructors).")
            course = section_course_row(db, section_id)
            if not course or course["course_id"] != target["course_id"] or course["course_title"] != target["course_title"]:
                course_id = uid()
                db.execute("INSERT INTO frq_gradescope_courses VALUES(?,?,?,?,'workspace',?)", (course_id, section_id, target["course_id"], target["course_title"], now))
            else:
                course_id = course["id"]
            latest = db.execute(f"SELECT * FROM {table} WHERE assignment_id=? AND section_id=? ORDER BY created_at DESC,rowid DESC LIMIT 1",
                                (aid, section_id)).fetchone()
            versions = json.dumps(target["versions"], sort_keys=True)
            if not latest or latest["course_link_id"] != course_id or latest["container_id"] != target["container_id"] or latest["versions_json"] != versions:
                db.execute(f"INSERT INTO {table} VALUES(?,?,?,?,?,?,?,'workspace',?)",
                           (uid(), aid, section_id, course_id, target["container_id"], target["container_title"], versions, now))
            links.append({"section_id": section_id, "course_id": target["course_id"], "container_id": target["container_id"], "versions": target["versions"]})
        return links

    # --- the plan ---------------------------------------------------------------------------------------------------------
    def _names(self, db, plan):
        event = self.frq.exams._event(db, plan["event_id"])
        return {(p["section_id"], p["enrollment_id"]): p["name"] for p in self.frq.exams._participants(db, event)}

    def _faculty(self, aid, responses=None, student=None):
        """Everything Faculty knows for the plan, read once under the lifecycle lock: final grades as an export counts them, each
        participant's UIN (catalog), name (roster, read once), reviewed paper and exam version, the questions with their rubrics,
        the recorded links and every earlier receipt. With `responses` (one response ID, a regrade request's Apply), only that
        response's grade and its student; with `student` ((section_id, enrollment_id), a late paper Faculty certified), only theirs."""
        with self.rosters.lock(), connect(self.path) as db:
            only, person_filter = "", ()
            if student:
                only, person_filter = " AND p.section_id=? AND p.enrollment_id=?", tuple(student)
                plan, decisions, _ = self.frq.final_decisions(db, aid, section_id=student[0])  # their section's grades only
                decisions = [d for d in decisions if (d["row"]["section_id"], d["row"]["enrollment_id"]) == tuple(student)]
            elif responses:
                found = db.execute("SELECT section_id,enrollment_id FROM frq_responses WHERE id=? AND assignment_id=?", (responses[0], aid)).fetchone()
                if not found:
                    raise NotFound("That response is not one of this assignment's.")
                only, person_filter = " AND p.section_id=? AND p.enrollment_id=?", (found["section_id"], found["enrollment_id"])
                plan, decisions, _ = self.frq.final_decisions(db, aid, response_id=responses[0])
            else:
                plan, decisions, _ = self.frq.final_decisions(db, aid)
            # GRADING "GRADED FOR PARTICIPATION": sent as each grade counts, the participation line in its adjustment and comment.
            plan, decisions, _ = participation.counted_decisions(db, aid, (plan, decisions, _))
            event = self.frq.exams._event(db, plan["event_id"])
            names = self._names(db, plan)
            people, shared = [], {}
            for row in db.execute(f"""SELECT p.section_id,p.enrollment_id,s.number,e.uin FROM exam_participants p JOIN course_sections s ON s.id=p.section_id
                    JOIN student_enrollments e ON e.id=p.enrollment_id WHERE p.event_id=?{only} ORDER BY s.number,p.enrollment_id""", (event["id"], *person_filter)).fetchall():
                person = self.frq.exams._participant_paper(db, event, row["section_id"], row["enrollment_id"])
                revision_id = ((person or {}).get("collection") or {}).get("assembly_revision_id")
                version = self.frq.paper_version(db, plan, person, shared) if person else {"status": "no_paper", "reason": "Not a participant of this exam."}
                revision = db.execute("SELECT id,selection_json,selection_sha256 FROM assembly_revisions WHERE id=?", (revision_id,)).fetchone() if revision_id else None
                selected = json.loads(revision["selection_json"]) if revision else []
                pages = sorted(int(p["page_number"]) for p in selected)
                people.append({"section_id": row["section_id"], "enrollment_id": row["enrollment_id"], "section": row["number"], "uin": row["uin"],
                               "name": names.get((row["section_id"], row["enrollment_id"]), ""), "revision": revision["id"] if revision else None,
                               "selection": revision["selection_sha256"] if revision else None, "pages": pages, "version": version,
                               "scan_pages": {int(p["page_number"]): p["scan_page_id"] for p in selected}})
            questions = {row[0]: self.frq.question(db, aid, row[0]) for row in db.execute("SELECT question_id FROM frq_assignment_questions WHERE assignment_id=?", (aid,))}
            uploads = {(r["gradescope_assignment_id"], r["gradescope_user_id"]): dict(r) for r in db.execute(
                "SELECT * FROM frq_gradescope_uploads WHERE assignment_id=? AND status='accepted' ORDER BY created_at,rowid", (aid,))}
            graded = {}
            for r in db.execute("SELECT * FROM frq_gradescope_grades WHERE assignment_id=? AND status='accepted' ORDER BY created_at,rowid", (aid,)):
                graded[r["response_id"]] = dict(r)
            # Units not settled yet (waiting, being sent, or uncertain until Gradescope is read), in any send: Gradescope keeps one paper
            # per student and Faculty sends one grade per response at a time, so none of these is planned again until it settles.
            unsettled = {"uploads": {(r[0], r[1]): r[2] for r in db.execute("""SELECT gradescope_assignment_id,gradescope_user_id,status FROM frq_gradescope_uploads
                             WHERE status IN ('pending','sending','uncertain')""")},
                         "grades": {r[0]: r[1] for r in db.execute("SELECT response_id,status FROM frq_gradescope_grades WHERE assignment_id=? AND status IN ('pending','sending','uncertain')", (aid,))}}
            live = db.execute("SELECT id FROM frq_gradescope_sends WHERE assignment_id=? AND status IN ('pending','running')", (aid,)).fetchone()
            targets = {t["section_id"]: t for t in self._targets(db, plan)}
            created = self._created_receipts(db, aid)  # questions Faculty made on Gradescope itself
            responses = db.execute("SELECT COUNT(*) FROM frq_responses WHERE assignment_id=?", (aid,)).fetchone()[0]
            lines = batch_lines(db, aid)  # the grade's comment: its batch lines from Certification and Export
            for decision in decisions:  # and the participation line
                extra = participation.line(decision["final"].get("score"))
                if extra:
                    lines[decision["row"]["id"]] = [*lines.get(decision["row"]["id"], []), *extra]
        return {"plan": plan, "event": event, "decisions": decisions, "people": people, "questions": questions,
                "uploads": uploads, "graded": graded, "live": live[0] if live else None, "targets": targets, "responses": responses, "unsettled": unsettled,
                "created": created, "lines": lines}

    def _send_view(self, aid, faculty, papers_only=False):
        """The empty view `_compose` fills in for a send of written grades (the preview, a late paper's send, a send after certifying)."""
        return {"assignment_id": aid, **self.settings.status(), "courses": [], "sections": [], "summary": {}, "questions": [],
                "skipped": [], "refused": [], "warnings": [], "published": [], "fingerprint": None, "running": faculty["live"],
                "papers_only": bool(papers_only)}

    def _plan(self, aid, requested, papers_only=False, replace=False, shown=None, responses=None):
        faculty = self._faculty(aid, responses)
        view = self._send_view(aid, faculty, papers_only)
        if responses:
            view["only_responses"] = list(responses)  # only when asked, so the ordinary preview stays what it always was
        if replace:
            # Only when asked, so the ordinary preview stays what it always was. `replacements` is every one exactly as Gradescope
            # showed it, which the send carries back.
            view["replace_others"] = True
            view["replacing"] = {"grades": 0, "score_changes": 0, "same": 0, "same_by": [], "by": [], "listed": []}
            view["replacements"] = []
        if not view["configured"]:
            return {"view": view, "uploads": [], "grades": [], "targets": {}, "published": {}}
        requested = requested if isinstance(requested, dict) else {}
        session = self.session_factory()
        try:
            return self._compose(aid, faculty, requested, session, view, papers_only=bool(papers_only), replace=bool(replace), shown=shown,
                                 responses=responses)
        except gw.GradescopeError as error:
            raise Conflict(f"Gradescope could not be read: {error}") from None
        finally:
            session.close()

    def _compose(self, aid, faculty, requested, session, view, papers_only=False, replace=False, shown=None, responses=None, student=None):
        event = faculty["event"]
        courses = session.courses()
        by_id = {c["id"]: c for c in courses}
        view["courses"] = [{"id": c["id"], "short": c["short"], "name": c["name"], "term": c["term"]} for c in courses]
        letters = sorted({(q["exam_version"] or "") for q in faculty["questions"].values()})
        targets, gradescope, fingerprint = {}, {}, {"targets": {}, "uploads": [], "grades": [], "skipped": [], "refused": []}
        if papers_only:
            fingerprint["papers_only"] = True  # only when true: a preview of papers and grades keeps the fingerprint it always had
        if replace:
            fingerprint["replace_others"], fingerprint["replacing"] = True, []  # what is replaced, exactly as Gradescope shows it
        if responses:
            fingerprint["only"] = sorted(responses)  # only when asked: a regrade request's one grade
        if student:
            fingerprint["students"] = [student]  # only when asked: a late paper Faculty certified (AUTOMATIC LATE-PAPER SEND)
        candidates = []  # with `replace`: every grade on a paper Faculty uploaded earlier, to look at on Gradescope
        published = {}  # exam ID -> its title and assignment IDs, for each section sent to an exam whose grades are published
        sections = sorted({(p["section_id"], p["section"]) for p in faculty["people"]}, key=lambda s: s[1])
        needed = paper_letters(faculty["people"])  # an exam lacking one of these is that section's problem; another version is not
        left_out = set()
        for section_id, number in sections:
            ask = requested.get(section_id) if isinstance(requested.get(section_id), dict) else {}
            saved = faculty["targets"].get(section_id) or {}
            if ask.get("leave_out") is True:
                # The instructor leaves this section out of THIS send (requirement, 2026-09-25: send one section first and check it).
                # Its course is not read, nothing of it is sent, no link is recorded, and the next preview offers it again.
                left_out.add(section_id)
                fingerprint["targets"][section_id] = {"left_out": True}
                view["sections"].append({"section_id": section_id, "section": number, "course_id": str(ask.get("course_id") or saved.get("course_id") or ""),
                                         "course": "", "proposed": False, "containers": [], "container_id": str(ask.get("container_id") or ""), "container": "",
                                         "versions": {}, "problem": "", "left_out": True})
                continue
            course_id = str(ask.get("course_id") or saved.get("course_id") or "")
            proposed = False
            if not course_id:
                course = propose_course(courses, number, event["term_label"])
                course_id, proposed = (course["id"], True) if course else ("", False)
            entry = {"section_id": section_id, "section": number, "course_id": course_id, "course": "", "proposed": proposed,
                     "linked": bool(saved.get("course_id")) and saved.get("course_id") == course_id,  # the section's own link: shown as a fact
                     "containers": [], "container_id": "", "container": "", "versions": {}, "problem": ""}
            view["sections"].append(entry)
            if not course_id:
                entry["problem"] = "Choose this section's Gradescope course."
                continue
            if course_id not in by_id:
                entry["problem"] = "That Gradescope course is not one of your courses (or is one Faculty never touches)."
                continue
            course = by_id[course_id]
            entry["course"] = gw.course_label(course)
            choices = containers(session.assignments(course_id))
            entry["containers"] = [{"id": c["id"], "title": c["title"], "versions": sorted(c["versions"])} for c in choices]
            container_id = str(ask.get("container_id") or (saved.get("container_id") if saved.get("course_id") == course_id else "") or "")
            if not container_id:
                # Proposed only when exactly one exam fits AND its title carries the Faculty assignment's number ("Test 1" and
                # "Common Exam 1") and names no other kind of work: a lone Exam 1 must never be offered for Test 2 before Test 2
                # exists on Gradescope, nor Quiz 2 for Exam 2 (`fits_title`).
                fitting = [c for c in choices if all(l in c["versions"] for l in letters) and (letters != [""] or len(choices) == 1)
                           and fits_title(faculty["plan"]["title"], c["title"])]
                if len(fitting) == 1:
                    container_id, entry["proposed"] = fitting[0]["id"], True
            container = next((c for c in choices if c["id"] == container_id), None)
            if not container:
                entry["problem"] = "Choose the Gradescope assignment this goes to in this course."
                continue
            entry["container_id"], entry["container"] = container["id"], container["title"]
            if container["published"]:
                # Sent to only with the instructor's confirmation of this exam (`check_published`); the preview says so.
                entry["published"], entry["warning"] = True, published_warning(container["title"])
                published[container["id"]] = {"title": container["title"], "ids": [container["id"], *container["versions"].values()]}
                view["published"].append({"section_id": section_id, "section": number, "course": entry["course"], "container_id": container["id"],
                                          "container": container["title"]})
            missing = [l for l in letters if l not in container["versions"] and l in needed.get(section_id, ())]
            if missing:
                entry["problem"] = f"{container['title']} has no version {', '.join(l or '(no versions)' for l in missing)}."
                # A version set lacking a lettered version can get it (Create the assignment on Gradescope adds it to the set).
                entry["missing_versions"] = [l for l in missing if l] if "" not in container["versions"] else []
            entry["versions"] = {l: container["versions"][l] for l in letters if l in container["versions"]}
            targets[section_id] = {"course_id": course_id, "course_title": course["short"] or course["name"], "course_name": course["name"], "container_id": container["id"],
                                   "container_title": container["title"], "versions": entry["versions"]}
            if container["published"]:
                targets[section_id]["published"] = True  # in the fingerprint: a change of publication makes the preview stale
            fingerprint["targets"][section_id] = targets[section_id]
            roster = session.roster(course_id)
            by_sid = {}
            for member in roster:
                by_sid.setdefault(member["sid"], []).append(member)
            state = {}
            if student and container["published"]:
                # A late paper Faculty sends by itself onto a published set: its own version may not be published (a makeup version
                # Faculty added lands unpublished). Said on the send's card; publishing stays the instructor's (PUBLISHING GRADES).
                hidden = sorted(l for l in needed.get(section_id, ()) if l in container["versions"] and container["versions"][l] not in container["published"])
                if hidden:
                    view["unpublished_version"] = hidden[0]
            for version, assignment in entry["versions"].items():
                if (responses or student) and version not in needed.get(section_id, ()):
                    continue  # one response's grade, or one late student's paper: only their own version is read
                outline = session.outline(course_id, assignment)
                rubric = None if papers_only else session.rubric(course_id, assignment)  # papers only: no grade, so no rubric to match
                listing = session.submissions(course_id, assignment)
                state[version] = {"assignment": assignment, "outline": outline, "rubric": rubric, "listing": listing,
                                  "students": {s["id"]: s for s in listing["students"]},
                                  "owners": {user: sub for sub, users in listing["submissions"].items() for user in users}}
            gradescope[section_id] = {"course": course, "by_sid": by_sid, "state": state}
        view["sections"] = sorted(view["sections"], key=lambda s: s["section"])

        # Questions: each Faculty question matched once per section and version (rubrics may differ between sections).
        mappings = {}

        def mapping(section_id, question):
            key = (section_id, question["question_id"])
            if key not in mappings:
                version = question["exam_version"] or ""
                state = gradescope[section_id]["state"][version]
                # A question Faculty made on Gradescope is matched by what it made (its receipt), whatever its rubric's origin.
                receipt = faculty["created"].get((gradescope[section_id]["course"]["id"], str(state["assignment"]), question["question_id"]))
                mappings[key] = map_created(question, state["rubric"], receipt) if receipt else map_question(question, state["rubric"])
            return mappings[key]

        decisions = {}
        for decision in faculty["decisions"]:
            decisions.setdefault((decision["row"]["section_id"], decision["row"]["enrollment_id"]), []).append(decision)
        uploads, grades = [], []
        counts = {"papers": {"upload": 0, "already": 0, "skipped": 0}, "grades": {"send": 0, "update": 0, "already": 0, "refused": 0, "held": 0},
                  "final": len(faculty["decisions"]), "responses": faculty["responses"], "not_final": faculty["responses"] - len(faculty["decisions"])}
        if replace:
            counts["grades"]["replace"] = 0
        if left_out:
            counts["papers"]["left_out"] = counts["grades"]["left_out"] = 0
        per_question, warnings = {}, set()

        def question_row(question):
            key = question["question_id"]
            if key not in per_question:
                per_question[key] = {"version": question["exam_version"], "faculty": question["question_key"], "gradescope": "", "send": 0, "update": 0,
                                     "already": 0, "refused": 0, "held": 0, "reasons": {}, **({"replace": 0} if replace else {})}
            return per_question[key]

        def skip(person, reason):
            counts["papers"]["skipped"] += 1
            fingerprint["skipped"].append([person["section_id"], person["enrollment_id"], reason])
            if len(view["skipped"]) < LISTED:
                view["skipped"].append({"name": person["name"], "section": person["section"], "reason": reason})
            if papers_only:
                return  # no grade would go with the paper anyway
            for decision in decisions.get((person["section_id"], person["enrollment_id"]), []):
                counts["grades"]["held"] += 1
                question_row(decision["question"])["held"] += 1

        for person in faculty["people"]:
            key = (person["section_id"], person["enrollment_id"])
            if person["section_id"] in left_out:
                counts["papers"]["left_out"] += 1 if person["revision"] else 0
                counts["grades"]["left_out"] += len(decisions.get(key, []))
                continue
            if not person["revision"]:
                if decisions.get(key):
                    skip(person, "No current reviewed paper.")
                continue  # absent, excused or not yet scanned: nothing to send and nothing to say
            if person["section_id"] not in gradescope:
                # The section's own problem when it has one (an exam without the paper's version is not an exam "not chosen").
                problem = next((s["problem"] for s in view["sections"] if s["section_id"] == person["section_id"] and s["container_id"]), "")
                skip(person, f"Section {person['section']}: {problem}" if problem else f"Section {person['section']} has no Gradescope assignment chosen.")
                continue
            version_state = person["version"]
            if version_state.get("status") not in GRADABLE:
                skip(person, version_state.get("reason") or "Its exam version is not decided.")
                continue
            version = version_state.get("version") or ""
            course = gradescope[person["section_id"]]
            if version not in course["state"]:
                skip(person, f"The Gradescope assignment has no version {version or '(without versions)'}.")
                continue
            matches = course["by_sid"].get(person["uin"], [])
            if len(matches) != 1:
                skip(person, f"{'No student' if not matches else 'More than one student'} on the Gradescope roster of {course['course']['short']} has this student's UIN.")
                continue
            user = matches[0]["user_id"]
            state = course["state"][version]
            if len(person["pages"]) > state["outline"]["pages"] and self._empty_past(person, state["outline"]["pages"]):
                person = {**person, "pages": person["pages"][:state["outline"]["pages"]]}  # EMPTY BACKS: sent without its empty sides
            if person["pages"] != list(range(1, len(person["pages"]) + 1)) or len(person["pages"]) != state["outline"]["pages"]:
                skip(person, f"The reviewed paper has {len(person['pages'])} pages; the Gradescope assignment's template has {state['outline']['pages']}.")
                continue
            ours = faculty["uploads"].get((state["assignment"], user))
            there = state["students"].get(user) or {}
            upload_key = f"{person['section_id']}:{person['enrollment_id']}"
            unsettled = faculty["unsettled"]["uploads"].get((state["assignment"], user))
            if unsettled:
                skip(person, "Faculty is still checking whether its earlier upload of this paper reached Gradescope; preview again shortly." if unsettled != "pending"
                     else "An earlier send still has this paper waiting to go; preview again when it has finished.")
                continue
            if ours and state["owners"].get(user) == ours["gradescope_submission_id"]:
                if ours["assembly_revision_id"] != person["revision"] or ours["selection_sha256"] != person["selection"]:
                    # Gradescope keeps the paper Faculty sent; the student's reviewed paper has changed since, and a grade on it would
                    # sit on pages that are not the ones graded. Faculty never replaces a submission: the instructor decides there.
                    skip(person, "Gradescope has the earlier reviewed paper of this student that Faculty uploaded; the paper has changed since, so its grades are not sent onto it.")
                    continue
                counts["papers"]["already"] += 1
                upload_id = ours["id"]
            elif there.get("submission") or user in state["owners"]:
                skip(person, "Gradescope already has a paper for this student that Faculty did not upload; it and its grades are left alone.")
                continue
            elif ours:
                # Faculty's upload was accepted and is gone from Gradescope since (deleted there). Its receipt stands, and the catalog
                # keeps one accepted upload per student and Gradescope exam (migration 026), so it is not uploaded again from here.
                skip(person, "Gradescope no longer has the paper Faculty uploaded for this student. Faculty keeps its receipt and does not upload it again; upload it on Gradescope if it is needed.")
                continue
            elif responses:
                # One response's grade goes only onto the paper Faculty uploaded earlier (REGRADES): a paper is sent with Send to Gradescope.
                skip(person, "Faculty has not uploaded this student's paper to Gradescope, so the grade cannot go onto it from here.")
                continue
            else:
                counts["papers"]["upload"] += 1
                upload_id = None
                unit = {"key": upload_key, "section_id": person["section_id"], "enrollment_id": person["enrollment_id"], "version": version,
                        "course_id": course["course"]["id"], "assignment": state["assignment"], "user": user, "revision": person["revision"],
                        "selection": person["selection"], "pages": len(person["pages"])}
                uploads.append(unit)
                fingerprint["uploads"].append([unit[k] for k in ("section_id", "enrollment_id", "version", "assignment", "user", "revision", "selection", "pages")])
            if papers_only:
                continue  # the paper alone: its grades stay in Faculty until a send of papers and grades
            for decision in decisions.get(key, []):
                question, final, row = decision["question"], decision["final"], decision["row"]
                entry = question_row(question)
                mapped, notes = mapping(person["section_id"], question)
                if mapped:
                    entry["gradescope"] = mapped["question"].get("title") or entry["gradescope"]
                    warnings.update(f"{question['question_key']}: {note}" for note in notes)
                reason, result = None, None
                if mapped:
                    result, reason = expected(mapped, final)
                else:
                    reason = notes
                if not reason and row["id"] in faculty["unsettled"]["grades"]:
                    reason = "Faculty is still checking whether its earlier grade for this question reached Gradescope; preview again shortly."
                if reason:
                    counts["grades"]["refused"] += 1
                    entry["refused"] += 1
                    entry["reasons"][reason] = entry["reasons"].get(reason, 0) + 1
                    fingerprint["refused"].append([row["id"], reason])
                    if len(view["refused"]) < LISTED:
                        view["refused"].append({"name": person["name"], "section": person["section"], "question": question["question_key"], "reason": reason})
                    continue
                chosen, adjustment, score = result
                previous = faculty["graded"].get(row["id"])
                items = {"chosen": chosen, "items": {i["id"]: text_amount(i["points"]) for i in mapped["order"]},
                         "lines": {line: item["id"] for line, item in mapped["items"].items()}}
                comment = gw.comment(comment_text((final.get("score") or {}).get("adjustments"), faculty["lines"].get(row["id"])))
                if comment:
                    items["comment"] = comment
                decision_id = final["id"]
                replaces = previous["id"] if previous and upload_id and previous["upload_id"] == upload_id else None
                unit = {"key": upload_key, "section_id": person["section_id"], "upload_id": upload_id, "response_id": row["id"], "decision": decision["kind"], "decision_id": decision_id,
                        "result_id": row["result_id"], "rubric_id": decision["task"]["rubric_id"], "question": str(mapped["question"]["id"]),
                        "items": items, "adjustment": adjustment, "expected": score, "replaces": replaces}
                planned = [row["id"], decision["kind"], decision_id, row["result_id"], unit["question"], chosen, text_amount(adjustment),
                           text_amount(score), replaces, items["items"], *([comment] if comment else [])]
                # A grade on a paper Faculty uploaded earlier may have someone else's grade on Gradescope: looked at below when asked.
                candidate = {"unit": unit, "planned": planned, "entry": entry, "person": person, "question": question, "previous": previous,
                             "course_id": course["course"]["id"], "upload": ours} if replace and upload_id else None
                if previous and previous["decision_id"] == decision_id and json.loads(previous["items_json"])["chosen"] == chosen \
                        and previous["adjustment"] == text_amount(adjustment) and self._receipt_comment(previous["items_json"]) == comment \
                        and previous["upload_id"] == (upload_id or previous["upload_id"]) and upload_id:
                    counts["grades"]["already"] += 1
                    entry["already"] += 1
                    if candidate:
                        candidates.append({**candidate, "kind": "already"})
                    continue
                counts["grades"]["update" if replaces else "send"] += 1
                entry["update" if replaces else "send"] += 1
                grades.append(unit)
                fingerprint["grades"].append(planned)
                if candidate:
                    candidates.append({**candidate, "kind": "update" if replaces else "send"})
        if candidates and shown is None:
            self._replacements(session, candidates, grades, counts, fingerprint, view)
        elif candidates:
            self._as_shown(candidates, shown, grades, counts, fingerprint, view)
        view["summary"] = counts
        view["questions"] = sorted(({**q, "reasons": [{"reason": k, "count": v} for k, v in sorted(q["reasons"].items(), key=lambda kv: -kv[1])]}
                                    for q in per_question.values()), key=lambda q: (q["version"], q["faculty"]))
        view["warnings"] = sorted(warnings)
        view["targets"] = {section_id: {"course_id": t["course_id"], "container_id": t["container_id"]} for section_id, t in targets.items()}
        # A published exam asks for the instructor's confirmation only where this send writes something to it, and says how much.
        writes = {}
        for kind, units in ((0, uploads), (1, grades)):
            for unit in units:
                writes.setdefault(unit["section_id"], [0, 0])[kind] += 1
        view["published"] = [{**p, "papers": writes[p["section_id"]][0], "grades": writes[p["section_id"]][1]} for p in view["published"] if p["section_id"] in writes]
        published = {p["container_id"]: published[p["container_id"]] for p in view["published"]}
        view["fingerprint"] = digest(fingerprint)
        return {"view": view, "uploads": uploads, "grades": grades, "targets": targets, "published": published}

    def _replacements(self, session, candidates, grades, counts, fingerprint, view):
        """With `replace_others`: which of these grades would replace a grade someone else made on Gradescope, read now. Each is on a
        paper Faculty uploaded earlier (a paper this send uploads has no grade yet). Per Gradescope question, its list of submissions
        says which are graded, by whom last and with what score (one page); the grading page of each graded one says exactly what is
        there. What a graded question shows decides:
        - Faculty's final grade (whoever put it there): as planned, so it is recorded as on Gradescope (`same` counts those Faculty
          never sent, `same_by` who graded them there: the dialog names them, requirement, 2026-10-02);
        - what Faculty itself last wrote there: as planned (a question of Faculty's whose score the list shows unchanged is taken as
          unchanged and not opened: the list shows scores, not items);
        - anything else: the grade replaces it, carrying exactly what Gradescope shows (`items.others`), and is counted `replace`
          instead of `send`, `update` or `already` (an `already` grade joins the plan).
        Reads only; nothing is written anywhere."""
        missing = {}
        for c in candidates:
            receipt = json.loads(c["upload"]["receipt_json"] or "{}")
            c["qsub"] = str((receipt.get("questions") or {}).get(c["unit"]["question"]) or "") or None
            if not c["qsub"]:
                paper = (c["course_id"], c["upload"]["gradescope_assignment_id"], c["upload"]["gradescope_submission_id"])
                missing.setdefault(paper, []).append(c)
        for paper, seen in zip(missing, self._read_all(session, [("submission", paper) for paper in missing])):
            for c in missing[paper]:
                found = seen["questions"].get(c["unit"]["question"])
                c["qsub"] = str(found) if found else None  # not made yet by Gradescope: nobody can have graded it
        questions = sorted({(c["course_id"], c["unit"]["question"]) for c in candidates if c["qsub"]})
        lists = dict(zip(questions, self._read_all(session, [("question_submissions", q) for q in questions])))
        opened = []
        for c in candidates:
            listed = (lists.get((c["course_id"], c["unit"]["question"])) or {}).get(c["qsub"]) if c["qsub"] else None
            if not listed or not listed["graded"]:
                continue
            if c["unit"]["replaces"] and listed["score"] is not None:
                sent = json.loads(c["previous"]["receipt_json"] or "{}").get("score")
                if sent is not None and amount(listed["score"]) == amount(sent):
                    continue
            c["listed"] = listed
            opened.append(c)
        same, replaced = [], []
        for c, grader in zip(opened, self._read_all(session, [("grader", (c["course_id"], c["unit"]["question"], c["qsub"])) for c in opened])):
            unit, current = c["unit"], self._state(grader)
            if current == self._wanted({"items_json": json.dumps(unit["items"]), "adjustment": text_amount(unit["adjustment"])}):
                if c["kind"] == "send":
                    same.append(c["listed"]["by"])
                continue
            if unit["replaces"] and current == self._wanted(c["previous"]):
                continue
            self._replace(c, {**current, "score": None if grader["score"] is None else text_amount(grader["score"]), "by": c["listed"]["by"]},
                          grades, counts, fingerprint)
            replaced.append(c)
        self._replacing_view(replaced, same, fingerprint, view)

    def _as_shown(self, candidates, shown, grades, counts, fingerprint, view):
        """With `replace_others`, at send: the replacements the approved preview showed (its `replacements`), planned again exactly
        as it showed them, without reading Gradescope. The fingerprint holds each one as shown, so a list that is not the preview's
        makes the send stale; a grade changed on Gradescope since is left alone by the sender (`present`), which writes a
        replacement only while Gradescope still shows what was approved."""
        others = dict(shown)
        replaced = []
        for c in candidates:
            if c["unit"]["response_id"] in others:
                self._replace(c, others[c["unit"]["response_id"]], grades, counts, fingerprint)
                replaced.append(c)
        self._replacing_view(replaced, [], fingerprint, view)

    @staticmethod
    def _replace(c, others, grades, counts, fingerprint):
        """Plans grade `c` to replace `others`, the grade someone else made, exactly as Gradescope showed it (`items.others`): it
        counts `replace` instead of `send`, `update` or `already` (an `already` grade joins the plan)."""
        unit = c["unit"]
        unit["items"]["others"] = others
        counts["grades"][c["kind"]] -= 1
        c["entry"][c["kind"]] -= 1
        counts["grades"]["replace"] += 1
        c["entry"]["replace"] += 1
        if c["kind"] == "already":
            grades.append(unit)
            fingerprint["grades"].append(c["planned"])
        fingerprint["replacing"].append([unit["response_id"], others])

    @staticmethod
    def _replacing_view(replaced, same, fingerprint, view):
        """`same` is who graded each grade already on Gradescope exactly as Faculty did (never sent by Faculty)."""
        view["replacements"] = fingerprint["replacing"]

        def graders(names):
            by = {}
            for name in names:
                name = name or "someone (Gradescope names nobody)"
                by[name] = by.get(name, 0) + 1
            return [{"name": name, "grades": n} for name, n in sorted(by.items(), key=lambda kv: (-kv[1], kv[0]))]
        others = lambda c: c["unit"]["items"]["others"]
        view["replacing"].update(
            grades=len(replaced), same=len(same), same_by=graders(same),
            score_changes=sum(1 for c in replaced if others(c)["score"] is None or amount(others(c)["score"]) != amount(c["unit"]["expected"])),
            by=graders(others(c)["by"] for c in replaced),
            listed=[{"name": c["person"]["name"], "section": c["person"]["section"], "question": c["question"]["question_key"], "by": others(c)["by"],
                     "gradescope": others(c)["score"], "faculty": text_amount(c["unit"]["expected"])} for c in replaced[:LISTED]])

    @staticmethod
    def _read_all(session, calls):
        """Read-only Session calls [(method, args)], answered in order, on up to PREVIEW_READS connections of the preview's one
        sign-in (`share`), as a person with a few tabs open. One failed read fails the preview: nothing is decided on half a read."""
        if len(calls) <= 1:
            return [getattr(session, name)(*args) for name, args in calls]
        local = threading.local()

        def run(call):
            if getattr(local, "connection", None) is None:
                local.connection = gw.Session(session.settings, share=session)
            return getattr(local.connection, call[0])(*call[1])
        with ThreadPoolExecutor(max_workers=min(PREVIEW_READS, len(calls))) as pool:
            return list(pool.map(run, calls))

    # --- the background sender --------------------------------------------------------------------------------------------
    def lock(self):
        return exclusive(self.inbox.storage, LOCK, self.path.parent / ".gradescope-sender.lock", "Another Gradescope sender is running.")

    def worker(self, stop):
        """Beside the Inbox worker loop, never under its execution lock: uploads and grades are network work, PARALLEL at a time."""
        while not stop.is_set():
            try:
                with self.lock():
                    self._recover()
                    self._dispatch(stop, PARALLEL)
            except Conflict:
                stop.wait(10)
            except Exception:
                LOG.exception("The Gradescope sender stopped unexpectedly")
                stop.wait(10)
        self._drop_session()

    def drain(self, limit=10000, parallel=1):
        """Tests: hold the lock and do every unit that is ready now (one at a time unless `parallel`)."""
        with self.lock():
            self._recover()
            if parallel > 1:
                return self._dispatch(threading.Event(), parallel, until_idle=True)
            done = 0
            while done < limit and self.process_one():
                done += 1
        return done

    def _dispatch(self, stop, parallel, until_idle=False):
        """Up to `parallel` units at once (at most UPLOADS_AT_ONCE of them papers or sheets), each on a thread with its own connection
        of the one sign-in. A unit is never worked on twice at once: those in flight are left out of the choice, and each is still
        claimed before anything leaves. A busy Gradescope or a refused sign-in stops new units; those in flight finish first."""
        flying, done = {}, 0
        with ThreadPoolExecutor(max_workers=parallel, thread_name_prefix="gradescope-sender") as pool:
            while True:
                for future in [f for f in flying if f.done()]:
                    kind, unit_id = flying.pop(future)
                    done += 1
                    try:
                        future.result()
                    except Exception:
                        # What it may have sent is unknown: like a unit of a stopped process, it is settled by reading Gradescope.
                        LOG.exception("A Gradescope %s stopped unexpectedly", kind)
                        self._orphaned(kind, unit_id)
                while len(flying) < parallel and not stop.is_set() and self._ready():
                    uploads = sum(1 for kind, _ in flying.values() if kind in ("uploads", "sheets"))
                    unit = self._next_unit(exclude={unit_id for _, unit_id in flying.values()}, uploads=uploads < UPLOADS_AT_ONCE)
                    if not unit:
                        break
                    flying[pool.submit(self._work, unit)] = (unit["kind"], unit["id"])
                if not flying:
                    if until_idle or stop.is_set():
                        return done
                    if self.wake.wait(2):
                        self.wake.clear()
                else:
                    wait_for(list(flying), timeout=1, return_when=FIRST_COMPLETED)

    def _orphaned(self, kind, unit_id):
        if kind == "makeup_versions":
            return  # it recorded nothing it would have to settle: the next look tries again
        with self.rosters.lock(), connect(self.path) as db:
            if kind == "automatic":  # nothing was recorded unless its send was (one transaction): it is prepared again
                db.execute("UPDATE frq_gradescope_automatic_sends SET status='pending',not_before=?,updated_at=? WHERE id=? AND status='sending'",
                           (later(WAIT), timestamp(), unit_id))
                return
            if kind == "runs":  # a step records nothing unless its creation, send or publication was (one transaction): looked at again
                db.execute("UPDATE frq_gradescope_automatic_runs SET status='pending',not_before=?,updated_at=? WHERE id=? AND status='working'",
                           (later(WAIT), timestamp(), unit_id))
                return
            if kind == "mc_corrections":  # its adjustment may have gone: only a read can say (GRADESCOPE "MC CORRECTIONS")
                db.execute("UPDATE frq_gradescope_mc_corrections SET status='uncertain',error=?,updated_at=? WHERE id=? AND status='sending'",
                           ("Faculty stopped while correcting this; it will read Gradescope to see whether it landed.", timestamp(), unit_id))
                return
            if kind == "mc_labels":  # a rename may have gone: only a read can say (GRADESCOPE "MC LABELS")
                db.execute("UPDATE frq_gradescope_mc_labels SET status='uncertain',error=?,updated_at=? WHERE id=? AND status='sending'",
                           ("Faculty stopped while labelling answers; it will read the rubric to see what landed.", timestamp(), unit_id))
                return
            if kind == "mc_checks":
                db.execute("UPDATE frq_gradescope_mc_checks SET status='failed',error=?,updated_at=? WHERE id=? AND status='sending'",
                           ("Faculty stopped unexpectedly while reading Gradescope’s scores. Check MC again.", timestamp(), unit_id))
                return
            db.execute(f"UPDATE {TABLES[kind]} SET status='uncertain',error=?,updated_at=? WHERE id=? AND status='sending'",
                       ("Faculty stopped while sending this; it will read Gradescope to see whether it landed.", timestamp(), unit_id))

    def _session_now(self):
        """This thread's connection. The first is the sign-in; every other thread's shares it, as a browser tab makes several
        requests at once (Gradescope keeps sessions apart, but one sign-in is enough and asks the least of it)."""
        if self._paused and time.monotonic() < self._paused[0] and self._sign_in_stamp() == self._paused[1]:
            # Gradescope refused the saved sign-in: no thread signs in again until the pause is over (or the file is saved again).
            raise gw.SignInRefused("Gradescope did not accept the saved sign-in; Faculty waits before trying again.", outcome="unavailable")
        local = self._local
        if getattr(local, "session", None) is None or local.epoch != self._epoch:
            with self._sessions_lock:
                if self._session is None:
                    self._session = self.session_factory()
                    session = self._session
                else:
                    session = gw.Session(self._session.settings, share=self._session)
            local.session, local.epoch = session, self._epoch
        return local.session

    def _drop_session(self):
        with self._sessions_lock:
            if self._session is not None:
                self._session.close()
            self._session = None
            self._epoch += 1  # every thread's connection belonged to it: each takes a new one on the next sign-in

    def _recover(self):
        """Holding the lock proves a unit still 'sending' lost its process: its outcome is unknown until Gradescope is read."""
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            for table in TABLES.values():
                db.execute(f"UPDATE {table} SET status='uncertain',error='Faculty stopped while sending this; it will read Gradescope to see whether it landed.',updated_at=? WHERE status='sending'", (timestamp(),))
            # An MC check only reads: one cut off is simply read again.
            db.execute("UPDATE frq_gradescope_mc_checks SET status='pending',updated_at=? WHERE status='sending'", (timestamp(),))
            # An MC correction cut off may have changed Gradescope: it is settled by reading only.
            db.execute("UPDATE frq_gradescope_mc_corrections SET status='uncertain',error='Faculty stopped while correcting this; it will read Gradescope to see whether it landed.',updated_at=? WHERE status='sending'", (timestamp(),))
            # So may a label run's rename (GRADESCOPE "MC LABELS").
            db.execute("UPDATE frq_gradescope_mc_labels SET status='uncertain',error='Faculty stopped while labelling answers; it will read the rubric to see what landed.',updated_at=? WHERE status='sending'", (timestamp(),))
            # An automatic send cut off while it was being prepared recorded nothing (its send is one transaction): prepared again.
            db.execute("UPDATE frq_gradescope_automatic_sends SET status='pending',updated_at=? WHERE status='sending'", (timestamp(),))
            # So did a run cut off in the middle of a step (GRADESCOPE "AFTER CERTIFYING"): the step is looked at again.
            db.execute("UPDATE frq_gradescope_automatic_runs SET status='pending',updated_at=? WHERE status='working'", (timestamp(),))

    def _sign_in_stamp(self):
        try:
            return os.stat(self.settings.credentials_file).st_mtime_ns
        except OSError:
            return None

    def process_one(self):
        """One unit: an uncertain unit resolved by reading Gradescope, else the next paper, else the next grade whose paper is there.
        After Gradescope refused the saved sign-in the sender does nothing for PAUSE seconds, or until the sign-in file is saved again:
        one refusal is never followed by one more sign-in per waiting paper and grade (that could lock the instructor's account)."""
        if not self._ready():
            return False
        unit = self._next_unit()
        if not unit:
            return False
        self._work(unit)
        return True

    def _ready(self):
        if self._paused:
            until, stamp = self._paused
            if time.monotonic() < until and self._sign_in_stamp() == stamp:
                return False
            self._paused = None
        if self._busy_until:
            if time.monotonic() < self._busy_until:
                return False  # Gradescope said it was busy: nothing at all is asked of it until the wait is over
            self._busy_until = None
        return True

    def _next_unit(self, exclude=(), uploads=True):
        """The next unit to work on, none of those in `exclude` (in flight); papers and sheets only when `uploads`."""
        now = datetime.now(timezone.utc).isoformat()
        skip = f"AND id NOT IN ({','.join('?' * len(exclude))})" if exclude else ""
        gskip = skip.replace("id NOT IN", "g.id NOT IN")
        ids = tuple(exclude)
        with self.rosters.lock(), connect(self.path) as db:
            self._settle_sends(db)
            live = "SELECT id FROM frq_gradescope_sends WHERE status IN ('pending','running')"
            # An uncertain unit is settled by READING Gradescope, whatever became of its send (a cancelled send too: only a read can
            # say whether its paper or grade landed). One whose read failed waits its minute like any other unit.
            unit = db.execute(f"""SELECT 'uploads' kind,* FROM frq_gradescope_uploads WHERE status='uncertain' {skip}
                    AND (not_before IS NULL OR not_before<=?) ORDER BY updated_at LIMIT 1""", (*ids, now)).fetchone() \
                or db.execute(f"""SELECT 'grades' kind,g.* FROM frq_gradescope_grades g WHERE g.status='uncertain' {gskip}
                    AND (g.not_before IS NULL OR g.not_before<=?) ORDER BY g.updated_at LIMIT 1""", (*ids, now)).fetchone() \
                or db.execute(f"""SELECT 'sheets' kind,* FROM frq_gradescope_sheet_uploads WHERE status='uncertain' {skip}
                    AND (not_before IS NULL OR not_before<=?) ORDER BY updated_at LIMIT 1""", (*ids, now)).fetchone() \
                or db.execute(f"""SELECT 'publications' kind,* FROM frq_gradescope_publication_units WHERE status='uncertain' {skip}
                    AND (not_before IS NULL OR not_before<=?) ORDER BY updated_at LIMIT 1""", (*ids, now)).fetchone() \
                or db.execute(f"""SELECT 'creations' kind,* FROM frq_gradescope_creation_units WHERE status IN ('pending','uncertain') {skip}
                    AND send_id IN (SELECT id FROM frq_gradescope_creations WHERE status IN ('pending','running'))
                    AND (not_before IS NULL OR not_before<=?) ORDER BY updated_at,rowid LIMIT 1""", (*ids, now)).fetchone() \
                or db.execute(f"""SELECT 'publications' kind,* FROM frq_gradescope_publication_units WHERE status='pending' {skip}
                    AND send_id IN (SELECT id FROM frq_gradescope_publications WHERE status IN ('pending','running'))
                    AND (not_before IS NULL OR not_before<=?) ORDER BY created_at,rowid LIMIT 1""", (*ids, now)).fetchone() \
                or (uploads and db.execute(f"""SELECT 'uploads' kind,* FROM frq_gradescope_uploads WHERE status='pending' AND send_id IN ({live}) {skip}
                    AND (not_before IS NULL OR not_before<=?) ORDER BY created_at,rowid LIMIT 1""", (*ids, now)).fetchone()) \
                or (uploads and db.execute(f"""SELECT 'sheets' kind,* FROM frq_gradescope_sheet_uploads WHERE status='pending' {skip}
                    AND send_id IN (SELECT id FROM frq_gradescope_sheet_sends WHERE status IN ('pending','running'))
                    AND (not_before IS NULL OR not_before<=?) ORDER BY created_at,rowid LIMIT 1""", (*ids, now)).fetchone()) \
                or db.execute(f"""SELECT 'grades' kind,g.* FROM frq_gradescope_grades g JOIN frq_gradescope_uploads u ON u.id=g.upload_id
                    WHERE g.status='pending' AND g.send_id IN ({live}) AND u.status IN ('accepted','failed','present','cancelled') {gskip}
                    AND (g.not_before IS NULL OR g.not_before<=?) ORDER BY g.created_at,g.rowid LIMIT 1""", (*ids, now)).fetchone() \
                or self._auto_next(db, skip, ids, now) \
                or self._run_next(db, skip, ids, now) \
                or self._makeup_next(db, ids) \
                or self._mc_fix_next(db, skip, ids, now) \
                or self._mc_label_next(db, skip, ids, now) \
                or self._mc_next(db, skip, ids, now)  # the MC check reads last: papers, sheets and grades go first
        return dict(unit) if unit else None

    def _work(self, unit):
        if unit["kind"] == "mc_checks":
            self._mc_unit(unit)  # a read: it settles its own busy, sign-in and failure (gradescope_mc)
            return
        if unit["kind"] == "mc_corrections":
            self._mc_fix_unit(unit)  # one question corrected on Gradescope: it settles its own busy, sign-in and failure
            return
        if unit["kind"] == "mc_labels":
            self._mc_label_unit(unit)  # one section's answers labelled on Gradescope: it settles its own busy, sign-in and failure
            return
        if unit["kind"] == "automatic":
            self._auto_unit(unit)  # a late paper's send, prepared and recorded: it settles its own busy, sign-in and failure
            return
        if unit["kind"] == "makeup_versions":
            self._auto_version(unit)  # a settled makeup version added on Gradescope: it settles its own busy, sign-in and failure
            return
        if unit["kind"] == "runs":
            self._run_unit(unit)  # Gradescope after a certification, one step: it settles its own busy, sign-in and failure
            return
        try:
            if unit["kind"] == "creations":
                try:
                    self._create_step(unit)
                except gw.GradescopeError:
                    raise
                except (NotFound, Conflict) as error:
                    self._creation_fail(unit, error.description)
                except Exception:
                    LOG.exception("Making an assignment on Gradescope stopped unexpectedly")
                    self._creation_fail(unit, "Faculty stopped unexpectedly while making this on Gradescope. Use Retry.")
            elif unit["kind"] == "publications":
                self._publish_unit(unit)
            elif unit["kind"] in ("uploads", "sheets"):
                self._resolve_upload(unit) if unit["status"] == "uncertain" else self._upload(unit)
            else:
                self._resolve_grade(unit) if unit["status"] == "uncertain" else self._grade(unit)
        except gw.SignInRefused as error:
            self._paused = (time.monotonic() + PAUSE, self._sign_in_stamp())
            self._drop_session()
            self._defer(unit, str(error))
        except gw.GradescopeBusy as error:
            self._busy(unit, error)
        except (Processing, TemplateProcessing) as error:
            self._defer(unit, str(error), delay=PROCESSING_WAIT)  # Gradescope takes seconds to process a paper, not a minute
        except gw.GradescopeError as error:
            # A read before anything was written failed: nothing left for this unit. Try again later.
            self._defer(unit, str(error))

    def _settle_sends(self, db):
        now = timestamp()
        cancelled = "SELECT id FROM frq_gradescope_sends WHERE status='cancelled'"
        db.execute(f"UPDATE frq_gradescope_uploads SET status='cancelled',updated_at=? WHERE status='pending' AND send_id IN ({cancelled})", (now,))
        db.execute(f"""UPDATE frq_gradescope_grades SET status='cancelled',updated_at=? WHERE status='pending' AND (send_id IN ({cancelled})
            OR upload_id IN (SELECT id FROM frq_gradescope_uploads WHERE status='cancelled'))""", (now,))
        for row in db.execute("SELECT id,status,assignment_id FROM frq_gradescope_sends WHERE status IN ('pending','running')").fetchall():
            open_units = db.execute("""SELECT (SELECT COUNT(*) FROM frq_gradescope_uploads WHERE send_id=? AND status IN ('pending','sending','uncertain'))
                + (SELECT COUNT(*) FROM frq_gradescope_grades WHERE send_id=? AND status IN ('pending','sending','uncertain'))""", (row["id"], row["id"])).fetchone()[0]
            if not open_units:
                db.execute("UPDATE frq_gradescope_sends SET status='complete',updated_at=? WHERE id=?", (now, row["id"]))
                self.frq.event(db, row["assignment_id"], "gradescope_send_complete", {"send_id": row["id"]})
                # Upload first, send the written grades, then check the MC (requirement, 2026-10-06): Gradescope has scored the sheets.
                # A regrade request's one grade (REGRADES) changes no sheet, so it reads nothing more.
                if "only_responses" not in json.loads(db.execute("SELECT plan_json FROM frq_gradescope_sends WHERE id=?", (row["id"],)).fetchone()[0] or "{}"):
                    self._mc_queue(db, row["assignment_id"], row["id"])
        self._settle_sheet_sends(db, now)
        self._settle_creations(db, now)
        self._settle_publications(db, now)

    def _set(self, kind, unit_id, status, *, error="", receipt=None, before=None, **columns):
        table = TABLES[kind]
        sets, args = ["status=?", "error=?", "updated_at=?"], [status, error[:2000], timestamp()]
        if receipt is not None:
            sets.append("receipt_json=?")
            args.append(json.dumps(receipt, sort_keys=True))
        if before is not None:
            sets.append("not_before=?")
            args.append(before)
        for column, value in columns.items():
            sets.append(f"{column}=?")
            args.append(value)
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(f"UPDATE {table} SET {','.join(sets)} WHERE id=?", (*args, unit_id))
            if kind == "uploads" and status in ("failed", "present", "cancelled"):
                # Its grades cannot go where the paper is not: failed ones come back with the paper on Retry, the others never.
                follow = {"failed": ("failed", "Its paper was not uploaded."), "present": ("refused", "Gradescope has a paper for this student that Faculty did not upload."),
                          "cancelled": ("cancelled", "")}[status]
                db.execute("UPDATE frq_gradescope_grades SET status=?,error=?,updated_at=? WHERE upload_id=? AND status='pending'", (*follow, timestamp(), unit_id))
            db.execute(f"UPDATE {SENDS[kind]} SET status=CASE WHEN status='pending' THEN 'running' ELSE status END,updated_at=? WHERE id=(SELECT send_id FROM "
                       f"{table} WHERE id=?)", (timestamp(), unit_id))

    def _claim(self, unit):
        """pending -> sending, committed before anything is sent (the record exists before the request leaves)."""
        table = TABLES[unit["kind"]]
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            claimed = db.execute(f"UPDATE {table} SET status='sending',attempts=attempts+1,updated_at=? WHERE id=? AND status='pending'", (timestamp(), unit["id"])).rowcount
            if claimed:
                db.execute(f"UPDATE {SENDS[unit['kind']]} SET status=CASE WHEN status='pending' THEN 'running' ELSE status END,updated_at=? WHERE id=?",
                           (timestamp(), unit["send_id"]))
        return bool(claimed)

    def _defer(self, unit, reason, delay=WAIT):
        """Nothing was written by this turn (a sign-in or a read failed): the unit waits a minute (`delay` seconds). A waiting or claimed
        unit goes back to waiting; an uncertain one STAYS uncertain, since only a read of Gradescope may settle it, and is read again then."""
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            send = db.execute(f"SELECT status FROM {SENDS[unit['kind']]} WHERE id=?", (unit["send_id"],)).fetchone()
            if send and send["status"] == "cancelled":
                # The send was cancelled while this was being tried: it is cancelled too (an uncertain one still waits for its read).
                db.execute(f"UPDATE {TABLES[unit['kind']]} SET status='cancelled',error='',updated_at=? WHERE id=? AND status IN ('pending','sending')", (timestamp(), unit["id"]))
                if unit["kind"] == "uploads":
                    db.execute("UPDATE frq_gradescope_grades SET status='cancelled',updated_at=? WHERE upload_id=? AND status='pending'", (timestamp(), unit["id"]))
                return
            db.execute(f"""UPDATE {TABLES[unit['kind']]} SET status=CASE WHEN status='uncertain' THEN 'uncertain' ELSE 'pending' END,not_before=?,
                error=CASE WHEN status='uncertain' THEN ? ELSE ? END,updated_at=? WHERE id=? AND status IN ('pending','sending','uncertain')""",
                (later(delay), f"Gradescope could not be read to see whether this landed; Faculty looks again {after(delay)}: {reason}"[:2000],
                 f"Waiting to try again: {reason}"[:2000], timestamp(), unit["id"]))

    def _pause(self, error):
        """Gradescope answered busy: the whole sender waits (the session has already slowed its pace). Returns the wait in seconds."""
        delay = min(BUSY_WAIT * 2 ** min(self._busy_streak, 10), BUSY_MAX)
        if error.retry_after:
            delay = max(delay, min(int(error.retry_after), BUSY_CAP))
        self._busy_streak += 1
        self._busy_until = time.monotonic() + delay
        return delay

    def _busy(self, unit, error, reason=None):
        """Gradescope was busy for this unit (requirement, 2026-09-25): nothing fails for it. Everything waits, the session sends more
        slowly, and the unit goes again after the wait; each try reads Gradescope before it writes, so a write that landed after
        all is found there and never sent twice. Only BUSY_TRIES busy answers in a row fail it, for the instructor's Retry."""
        delay = self._pause(error)
        tries = self._busy_tries[unit["id"]] = self._busy_tries.get(unit["id"], 0) + 1
        if tries >= BUSY_TRIES and unit["status"] != "uncertain":
            self._busy_tries.pop(unit["id"], None)
            with self.rosters.lock(), connect(self.path) as db:
                open_unit = db.execute(f"SELECT 1 FROM {TABLES[unit['kind']]} WHERE id=? AND status IN ('pending','sending')", (unit["id"],)).fetchone()
            if open_unit:
                self._set(unit["kind"], unit["id"], "failed", error=f"Gradescope stayed busy through {tries} tries ({reason or error}) Use Retry failed when it is quieter.")
                return
        self._defer(unit, f"{reason or error} Faculty tries again {after(delay)}, more slowly.", delay=delay)

    def _settled(self, unit_id):
        """A unit went through: Gradescope is answering again."""
        self._busy_streak = 0
        self._busy_tries.pop(unit_id, None)

    # --- papers -----------------------------------------------------------------------------------------------------------
    def _paper(self, upload):
        """The student's reviewed paper as one PDF: still their current paper, every page the one reviewed (checksums), merged in
        the document sandbox. Raises Conflict when anything differs from what was approved."""
        with self.rosters.lock(), connect(self.path) as db:
            plan = self.frq.plan(db, upload["assignment_id"])
            event = self.frq.exams._event(db, plan["event_id"])
            person = self.frq.exams._participant_paper(db, event, upload["section_id"], upload["enrollment_id"])
            current = ((person or {}).get("collection") or {}).get("assembly_revision_id")
            if current != upload["assembly_revision_id"]:
                raise Conflict("The student's reviewed paper changed after the send was approved; preview and send again.")
            version = self.frq.paper_version(db, plan, person)
            if version.get("status") not in GRADABLE or (version.get("version") or "") != upload["exam_version"]:
                raise Conflict("The student's exam version changed after the send was approved (or is no longer decided); preview and send again.")
            revision = db.execute("SELECT selection_json,selection_sha256 FROM assembly_revisions WHERE id=?", (current,)).fetchone()
            if not revision or revision["selection_sha256"] != upload["selection_sha256"]:
                raise Conflict("The reviewed paper is not the one approved.")
            selection = sorted(json.loads(revision["selection_json"]), key=lambda page: int(page["page_number"]))
            past = [p for p in selection if int(p["page_number"]) > upload["pages"]]
            if past and all(self.frq._empty_side(db, p["scan_page_id"]) for p in past):
                selection = [p for p in selection if int(p["page_number"]) <= upload["pages"]]  # EMPTY BACKS: left out of the upload
            if [int(p["page_number"]) for p in selection] != list(range(1, upload["pages"] + 1)):
                raise Conflict("The reviewed paper's pages are not the ones approved.")
            pages = [(db.execute("SELECT page_artifact_id FROM scan_pages WHERE id=?", (p["scan_page_id"],)).fetchone(), p["content_sha256"]) for p in selection]
            turned = self._turned_pages(db, upload, selection)
        contents = []
        for (row, expected_sha), page in zip(pages, selection):
            if not row:
                raise Conflict("A page of the reviewed paper is missing.")
            _, content = self.inbox.read_artifact(row[0])
            if sha(content) != expected_sha:
                raise Conflict("A page of the reviewed paper does not match what was reviewed.")
            contents.append((content, 180) if page["scan_page_id"] in turned else content)
        upload["turned"] = [int(p["page_number"]) for p in selection if p["scan_page_id"] in turned]
        return process.merge(contents)

    def _empty_past(self, person, count):
        """EMPTY BACKS (DOCUMENTS; 2026-10-17, requirement: "I need this fixed" when 38 one-page take-homes were held from the send as
        "2 pages"): whether the paper's pages past the template's `count` carry no writing (`JobsMixin._empty_side`), so the paper
        is uploaded as its first `count` pages. Asked only of a paper longer than the template."""
        past = [n for n in person["pages"] if n > count]
        with self.rosters.lock(), connect(self.path) as db:
            return bool(past) and all(self.frq._empty_side(db, person["scan_pages"].get(n)) for n in past)

    def _turned_pages(self, db, upload, selection):
        """{scan_page_id} of the paper's pages that grading found upside down, turned a half turn when the paper is merged for
        Gradescope (requirement, 2026-09-27). The retained scan is never turned; the merge sets the page's rotation (lossless). The paper's own whole-paper version
        check (`version_match`, `turned` page numbers of this very assembly) decides where one exists; otherwise a page counts only
        where EVERY crop grading cut from it under an orientation rule (`extraction` metadata `orientation`) was cut from the page
        turned over. A page no crop came from, or crops that disagree, goes as scanned. Catalog reads only."""
        by_number = {int(p["page_number"]): p["scan_page_id"] for p in selection}
        for row in db.execute("""SELECT result_json FROM frq_version_checks WHERE assignment_id=? AND section_id=? AND enrollment_id=?
                AND assembly_revision_id=? AND status='complete' ORDER BY updated_at DESC,rowid DESC""",
                (upload["assignment_id"], upload["section_id"], upload["enrollment_id"], upload["assembly_revision_id"])):
            result = json.loads(row[0] or "null") or {}
            if "turned" in result:
                return {by_number[int(n)] for n in result["turned"] if int(n) in by_number}
        votes = {}
        for task in db.execute("""SELECT t.* FROM frq_tasks t JOIN frq_responses r ON r.id=t.response_id
                WHERE r.assignment_id=? AND r.section_id=? AND r.enrollment_id=? AND t.status='complete'""",
                (upload["assignment_id"], upload["section_id"], upload["enrollment_id"])):
            task = dict(task)
            try:
                crops = self.frq.step(db, task, "extracting").get("crops") or []
            except ValueError:
                continue
            regions = (json.loads(task["source_json"] or "{}") or {}).get("regions") or []
            if len(crops) != len(regions):
                continue
            for region, crop in zip(regions, crops):
                orientation = ((crop or {}).get("metadata") or {}).get("orientation")
                if isinstance(orientation, dict) and orientation.get("rule") and region.get("scan_page_id"):
                    votes.setdefault(region["scan_page_id"], set()).add(bool(orientation.get("turned")))
        pages = set(by_number.values())
        return {page for page, seen in votes.items() if seen == {True} and page in pages}

    def _ours(self, session, upload):
        """('ours', receipt) when the student's paper on Gradescope is the file this unit uploaded, ('other', None) when they have a
        paper Faculty did not upload (in any version), ('none', None) when they have none."""
        listing = session.submissions(upload["course_id"], upload["gradescope_assignment_id"])
        user = upload["gradescope_user_id"]
        mine = f"faculty-{upload['id']}.pdf"
        for submission_id, owners in listing["submissions"].items():
            if user in owners:
                seen = session.submission(upload["course_id"], upload["gradescope_assignment_id"], submission_id)
                if seen["filename"] == mine:
                    return "ours", {"submission_id": submission_id, "filename": mine, "pages": seen["pages"], "status": seen["status"], "questions": seen["questions"]}
                return "other", None
        student = next((s for s in listing["students"] if s["id"] == user), None)
        if student is None:
            raise gw.GradescopeError("The student is no longer on the Gradescope roster.")
        return ("other" if student["submission"] else "none"), None

    def _upload(self, upload):
        """A student's reviewed paper, or a bubble sheet (`kind` "sheets", `gradescope_sheets`): the same upload, read back."""
        kind = upload["kind"]
        thing = "sheet" if kind == "sheets" else "paper"
        if not self._claim(upload):
            return
        try:
            pdf = self._sheet_page(upload) if kind == "sheets" else self._paper(upload)
        except (Conflict, NotFound) as error:
            self._set(kind, upload["id"], "failed", error=getattr(error, "description", str(error)))
            return
        except (StorageError, OSError) as error:  # storage being moved or backed up, a drive check: it passes, so wait
            self._defer(upload, f"The {thing} could not be read just now ({error}).")
            return
        except ValueError as error:  # the sandbox: a time limit passes, anything else about the paper does not
            if "time limit" in str(error).lower():
                self._defer(upload, f"The {thing} could not be prepared just now ({error}).")
            else:
                self._set(kind, upload["id"], "failed", error=str(error))
            return
        session = self._session_now()
        found, receipt = self._ours(session, upload)  # a read: failing here sent nothing (process_one defers)
        if found == "ours":
            self._accept_upload(upload, receipt, sha(pdf))
            return
        if found == "other":
            self._set(kind, upload["id"], "present", error=f"Gradescope already has a {thing} for this student that Faculty did not upload; it is left alone.")
            return
        exam = upload["container_id"] if kind == "sheets" else upload["gradescope_assignment_id"]
        if (self._exam_published(session, upload["course_id"], upload["container_id"]) if kind == "sheets"
                else self._published(session, upload["course_id"], upload["gradescope_assignment_id"])) and not self._confirmed(kind, upload, exam):
            self._set(kind, upload["id"], "failed", error=PUBLISHED)
            return
        with self.rosters.lock(), connect(self.path) as db:  # what is about to leave, recorded before it does
            db.execute(f"UPDATE {TABLES[kind]} SET pdf_sha256=?,updated_at=? WHERE id=?", (sha(pdf), timestamp(), upload["id"]))
        upload["pdf_sha256"] = sha(pdf)
        busy = None
        try:
            answer = session.upload(upload["course_id"], upload["gradescope_assignment_id"], upload["gradescope_user_id"], pdf, f"faculty-{upload['id']}.pdf")
        except gw.GradescopeBusy as error:
            if error.outcome != "unknown":  # 429 or 503: Gradescope did not take it; it goes again after the wait
                self._busy(upload, error)
                return
            busy, answer = error, error.status  # 502 or 504: it may have landed, so it is read back first
        except gw.GradescopeError as error:
            if error.outcome != "unknown":
                self._defer(upload, str(error))
                return
            answer = None
        try:
            found, receipt = self._ours(session, upload)
        except gw.GradescopeError as error:
            wait = {"before": later(self._pause(error))} if isinstance(error, gw.GradescopeBusy) else {}
            self._set(kind, upload["id"], "uncertain", error=f"Gradescope did not answer when Faculty checked the upload ({error}); it will look again.", **wait)
            return
        if found == "ours":
            self._accept_upload(upload, receipt, sha(pdf))
        elif found == "other":
            self._set(kind, upload["id"], "present", error=f"Another {thing} for this student appeared on Gradescope; Faculty's was not kept.")
        elif busy:
            self._busy({**upload, "status": "sending"}, busy, f"Gradescope {gw.BUSY[busy.status]} and did not keep the {thing}.")  # it goes again after the wait
        else:
            self._set(kind, upload["id"], "failed", error=f"Gradescope did not keep the {thing} (it answered {answer or 'nothing'}). Retry to send it again.")

    def _confirmed(self, kind, unit, assignment_id):
        """Whether the unit's send confirmed writing to this Gradescope exam although its grades are published."""
        with self.rosters.lock(), connect(self.path) as db:
            row = db.execute(f"SELECT plan_json FROM {SENDS[kind]} WHERE id=?", (unit["send_id"],)).fetchone()
        return bool(row) and str(assignment_id) in (json.loads(row["plan_json"]).get("published") or [])

    @staticmethod
    def _published(session, course_id, assignment_id):
        rows = session.assignments(course_id)
        row = next((r for r in rows if r["id"] == str(assignment_id)), None)
        if row is None:
            raise gw.GradescopeError("The Gradescope assignment is gone from its course.")
        parent = next((r for r in rows if r["id"] == row["parent"]), None) if row["parent"] else None
        return row["published"] or bool(parent and parent["published"])

    def _accept_upload(self, upload, receipt, pdf_sha):
        self._settled(upload["id"])
        turned = {"turned": upload["turned"]} if upload.get("turned") else {}  # the pages turned upright in what was uploaded
        self._set(upload["kind"], upload["id"], "accepted", receipt={**receipt, **turned, "pdf_sha256": pdf_sha, "at": timestamp()},
                  gradescope_submission_id=receipt["submission_id"], pdf_sha256=pdf_sha)

    def _resolve_upload(self, upload):
        """An upload whose outcome is unknown: Gradescope is READ, never written, to settle it."""
        kind = upload["kind"]
        thing = "sheet" if kind == "sheets" else "paper"
        found, receipt = self._ours(self._session_now(), upload)
        if found == "ours":
            self._accept_upload(upload, receipt, upload.get("pdf_sha256") or "")
        elif found == "other":
            self._set(kind, upload["id"], "present", error=f"Gradescope has a {thing} for this student that Faculty did not upload.")
        else:
            self._set(kind, upload["id"], "failed", error=f"Faculty's {thing} is not on Gradescope; it did not land. Retry to send it again.")

    # --- grades -----------------------------------------------------------------------------------------------------------
    def _still_final(self, grade):
        """The grade approved for sending must still be this response's final decision, by the export's own rule."""
        with self.rosters.lock(), connect(self.path) as db:
            _, decisions, skipped = self.frq.final_decisions(db, grade["assignment_id"], grade["response_id"])
        decision = decisions[0] if decisions else None
        if not decision:
            return skipped[0]["reason"] if skipped else "It is no longer a final grade."
        if decision["final"]["id"] != grade["decision_id"] or decision["row"]["result_id"] != grade["result_id"]:
            return "Faculty's grade changed after the send was approved; preview and send again."
        return None

    def _question_submission(self, session, grade, upload):
        receipt = json.loads(upload["receipt_json"] or "{}")
        found = (receipt.get("questions") or {}).get(grade["gradescope_question_id"])
        if not found:
            seen = session.submission(upload["course_id"], upload["gradescope_assignment_id"], upload["gradescope_submission_id"])
            found = seen["questions"].get(grade["gradescope_question_id"])
        if not found:
            raise Processing("Gradescope has not made this question's submission yet.")
        return found

    @staticmethod
    def _state(grader):
        return {"applied": sorted(grader["applied"]), "points": None if grader["points"] in (None, "") else text_amount(grader["points"]),
                "comments": gw.comment(grader["comments"])}

    @staticmethod
    def _wanted(grade):
        items = json.loads(grade["items_json"])
        adjustment = Decimal(grade["adjustment"] or "0")
        return {"applied": sorted(items["chosen"]), "points": None if adjustment == 0 else text_amount(adjustment), "comments": gw.comment(items.get("comment"))}

    def _grade(self, grade):
        with self.rosters.lock(), connect(self.path) as db:
            upload = dict(db.execute("SELECT * FROM frq_gradescope_uploads WHERE id=?", (grade["upload_id"],)).fetchone())
        if upload["status"] != "accepted":
            # `_set` settles the waiting grades of a paper that did not go; one that waits here anyway is settled the same way, never
            # left waiting (the queue would offer it again at once).
            follow = {"failed": ("failed", "Its paper was not uploaded."), "present": ("refused", "Gradescope has a paper for this student that Faculty did not upload."),
                      "cancelled": ("cancelled", "")}.get(upload["status"])
            if follow:
                self._set("grades", grade["id"], follow[0], error=follow[1])
            return
        if not self._claim(grade):
            return
        problem = self._still_final(grade)
        if problem:
            self._set("grades", grade["id"], "refused", error=problem)
            return
        session = self._session_now()
        if not self._still_ours(session, upload):
            self._set("grades", grade["id"], "refused", error="The paper on Gradescope is no longer the one Faculty uploaded (it was replaced or reassigned); nothing was sent.")
            return
        qsub = self._question_submission(session, grade, upload)
        grader = session.grader(upload["course_id"], grade["gradescope_question_id"], qsub)
        # The grading page does not say whether students see grades; the assignments list does (as for uploads), read again for
        # every grade, since an exam may be published after its papers went up. The grade's own send must have confirmed it then.
        if (grader["published"] or self._published(session, upload["course_id"], upload["gradescope_assignment_id"])) \
                and not self._confirmed("grades", grade, upload["gradescope_assignment_id"]):
            self._set("grades", grade["id"], "failed", error=PUBLISHED)
            return
        items = json.loads(grade["items_json"])
        changed = [i for i, points in items["items"].items() if i not in grader["items"] or text_amount(grader["items"][i]) != points]
        if changed:
            self._set("grades", grade["id"], "refused", error="Gradescope's rubric for this question changed after the send was approved; preview and send again.")
            return
        # Gradescope's score for these items NOW, from the grading page's own question and items: the question's worth, scoring
        # and bounds may have changed since the preview, and nothing is written that would not give Faculty's score.
        now = self._score_now(grader, items["chosen"], Decimal(grade["adjustment"] or "0"))
        if now is None or now != amount(grade["expected_score"]):
            self._set("grades", grade["id"], "refused", error="Gradescope's grading page no longer says what this question is worth; nothing was sent." if now is None
                      else f"Gradescope would give {text_amount(now)} for these items now; Faculty's grade is {text_amount(grade['expected_score'])}. Preview and send again.")
            return
        wanted, current = self._wanted(grade), self._state(grader)
        if current == wanted:
            if grader["score"] is None or amount(grader["score"]) != amount(grade["expected_score"]):
                self._set("grades", grade["id"], "failed", error=f"Gradescope shows these items with a different score ({text_amount(grader['score'] or 0)}) than Faculty's. Look at it on Gradescope.")
                return
            self._accept_grade(grade, grader, "It was already on Gradescope.")
            return
        replacing = None
        if grader["evaluation_id"] is not None or current["applied"] or current["points"] is not None or current["comments"]:
            earlier, others = self._earlier(grade), self._others(grade)
            if others is not None and current == others:
                replacing = items["others"]  # the grade someone else made that the instructor approved replacing, as the preview showed it
            elif earlier is None or current != earlier:
                self._set("grades", grade["id"], "present", error="Someone graded this question on Gradescope; Faculty never overwrites it." if others is None
                          else "Someone changed this grade on Gradescope after the preview you approved; Faculty replaces only the grade the preview showed, so it is left alone.")
                return
        busy = None
        try:
            # The comment is the grade's batch lines; one Faculty wrote before and the grade no longer has is cleared with an empty
            # comment (none at all may leave it standing), and a question with no comment is saved as before.
            comments = wanted["comments"] or ("" if current["comments"] else None)
            session.save_grade(grader, set(items["chosen"]), None if wanted["points"] is None else Decimal(wanted["points"]), comments)
        except gw.GradescopeBusy as error:
            if error.outcome != "unknown":  # 429 or 503: not saved; it goes again after the wait
                self._busy(grade, error)
                return
            busy = error  # 502 or 504: it may have been saved, so it is read back first
        except gw.GradescopeError as error:
            if error.outcome != "unknown":
                self._defer(grade, str(error))
                return
        self._check_grade(grade, upload, qsub, wanted, busy, replaced=replacing)

    def _still_ours(self, session, upload):
        """The student's active paper on Gradescope is still the file Faculty uploaded, read before EVERY grade: a paper
        reassigned or replaced on Gradescope while its grades go gets none after that (never remembered from an earlier grade)."""
        seen = session.submission(upload["course_id"], upload["gradescope_assignment_id"], upload["gradescope_submission_id"])
        if seen["status"] != "complete" or not seen["owners"]:
            raise Processing("Gradescope has not finished processing the paper yet.")  # a read: the grade waits PROCESSING_WAIT
        return seen["active"] and upload["gradescope_user_id"] in seen["owners"] and seen["filename"] == f"faculty-{upload['id']}.pdf"

    @staticmethod
    def _score_now(grader, chosen, adjustment):
        question = grader["question"] or {}
        if question.get("weight") in (None, ""):
            return None
        return amount(gw.gradescope_score(question, [{"id": item, "points": points} for item, points in grader["items"].items()], set(chosen), adjustment))

    def _earlier(self, grade):
        """What Faculty itself last wrote on this question, when this grade replaces it."""
        if not grade["replaces_id"]:
            return None
        with self.rosters.lock(), connect(self.path) as db:
            row = db.execute("SELECT * FROM frq_gradescope_grades WHERE id=? AND status='accepted'", (grade["replaces_id"],)).fetchone()
        return self._wanted(dict(row)) if row else None

    @staticmethod
    def _others(grade):
        """The grade someone else made on Gradescope that the instructor approved replacing with this one, as the preview showed it
        (items, adjustment, comment), or None: a send without `replace_others`, or nothing of someone else's there then."""
        others = json.loads(grade["items_json"]).get("others")
        return {key: others.get(key) for key in ("applied", "points", "comments")} if others else None

    def _check_grade(self, grade, upload, qsub, wanted, busy=None, replaced=None):
        try:
            grader = self._session_now().grader(upload["course_id"], grade["gradescope_question_id"], qsub)
        except gw.GradescopeError as error:
            wait = {"before": later(self._pause(error))} if isinstance(error, gw.GradescopeBusy) else {}
            self._set("grades", grade["id"], "uncertain", error=f"Gradescope did not answer when Faculty checked the grade ({error}); it will look again.", **wait)
            return
        current = self._state(grader)
        if current == wanted and grader["score"] is not None and amount(grader["score"]) == amount(grade["expected_score"]):
            self._accept_grade(grade, grader, "", replaced=replaced)
        elif busy and ((not current["applied"] and current["points"] is None and not current["comments"]) or current == self._earlier(grade)
                       or (replaced is not None and current == self._others(grade))):
            self._busy({**grade, "status": "sending"}, busy, f"Gradescope {gw.BUSY[busy.status]} and did not keep the grade.")  # it goes again after the wait
        elif not current["applied"] and current["points"] is None:
            self._set("grades", grade["id"], "failed", error="Gradescope did not keep the grade. Retry to send it again.")
        elif replaced is not None and current == self._others(grade):
            self._set("grades", grade["id"], "failed", error="Gradescope still shows the grade you approved replacing; Faculty's did not land. Retry to send it again.")
        else:
            self._set("grades", grade["id"], "failed", error=f"Gradescope shows a different grade ({text_amount(grader['score'] or 0)}) than Faculty sent. Look at it on Gradescope.")

    def _accept_grade(self, grade, grader, note, replaced=None):
        self._settled(grade["id"])
        self._set("grades", grade["id"], "accepted", error=note,
                  receipt={"applied": sorted(grader["applied"]), "points": self._state(grader)["points"], "comments": self._state(grader)["comments"],
                           "score": text_amount(grader["score"] or 0), "at": timestamp(),
                           **({"replaced": replaced} if replaced is not None else {})})  # what someone else's grade was, when this replaced it

    def _resolve_grade(self, grade):
        with self.rosters.lock(), connect(self.path) as db:
            upload = dict(db.execute("SELECT * FROM frq_gradescope_uploads WHERE id=?", (grade["upload_id"],)).fetchone())
        session = self._session_now()
        qsub = self._question_submission(session, grade, upload)
        grader = session.grader(upload["course_id"], grade["gradescope_question_id"], qsub)
        wanted, current = self._wanted(grade), self._state(grader)
        if current == wanted and grader["score"] is not None and amount(grader["score"]) == amount(grade["expected_score"]):
            self._accept_grade(grade, grader, "")
        elif current == wanted:
            self._set("grades", grade["id"], "failed", error=f"Gradescope shows these items with a different score ({text_amount(grader['score'] or 0)}) than Faculty's. Look at it on Gradescope.")
        elif not current["applied"] and current["points"] is None:
            self._set("grades", grade["id"], "failed", error="Faculty's grade is not on Gradescope; it did not land. Retry to send it again.")
        elif current == self._earlier(grade):
            self._set("grades", grade["id"], "failed", error="Gradescope still shows Faculty's earlier grade; the new one did not land. Retry to send it again.")
        elif self._others(grade) is not None and current == self._others(grade):
            self._set("grades", grade["id"], "failed", error="Gradescope still shows the grade you approved replacing; Faculty's did not land. Retry to send it again.")
        else:
            self._set("grades", grade["id"], "present", error="Gradescope shows a grade Faculty did not send; it is left alone.")
