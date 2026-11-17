"""Bubble sheet grading: an answer key per version, and scores derived from each sheet's current reading.

The instructor asked on 2026-09-25 for bubble sheet grading like Gradescope's: click the answers of each version and save them
as the answer key. Ownership: the key belongs to the TERM grading plan (`frq_assignments`), because each term's exam has its own
answers and its version letters are that term's. Every save is a new immutable `frq_bubble_keys` revision holding every version;
the latest counts, and a save names the revision it was made on (409 when another save came first).

Readings stay the bubble sheet reader's (`bubble_sheets`): nothing here changes a reading, a review, an identity, a match or the
Inbox review gate. Scores are DERIVED every time from the latest key and each sheet's current value (its latest review, else the
machine reading) and are never stored, published or emailed. Only sheets of scans that cleared Inbox review are scored, and a
sheet that still needs review, has no readable version, or has a version without a key is listed with that reason, not scored.

Scoring follows Gradescope's documented rules, so the two can be compared question by question:
  exact   full points only when the letters marked are exactly the key's;
  partial a share of the points for each correct letter marked, nothing when any other letter is marked;
  either  full points when any correct letter is marked, nothing when any other letter is marked.
A blank earns nothing, with one exception (`bubble-scoring-v2`; requirement, 2026-09-25): a question keyed with EVERY letter
under Either/Or is given to everyone, and a blank earns its points too. Points are Decimal and each question is rounded to the
cent.
"""
import csv
import io
import json
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from werkzeug.exceptions import Conflict

from course_storage import timestamp
from rosters import connect
from teach_inbox.service import PRESENT

from .models import digest, encoded, text, uid

RULE = "bubble-scoring-v2"
LETTERS = "ABCDE"
SCORING = ("exact", "partial", "either")
QUESTIONS = 100  # The template's first page; the reader reads nothing past it.
MAX_POINTS = Decimal(1000)
CENT = Decimal("0.01")


def points_text(value):
    """A point value as the instructor would write it: 4, 2.5, 0.25."""
    text_ = format(value.quantize(CENT, ROUND_HALF_UP), "f")
    return text_.rstrip("0").rstrip(".") if "." in text_ else text_


def parse_points(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValueError(f"{label} must be a number of points.")
    try:
        points = Decimal(str(value).strip())
    except InvalidOperation:
        raise ValueError(f"{label} must be a number of points.") from None
    if not points.is_finite() or points < 0 or points > MAX_POINTS or points != points.quantize(CENT):
        raise ValueError(f"{label} must be between 0 and {MAX_POINTS} points, in hundredths at most.")
    return points_text(points)


def parse_answer(value, label):
    if not isinstance(value, str) or not value or len(value) > len(LETTERS) or any(letter not in LETTERS for letter in value) \
            or len(set(value)) != len(value):
        raise ValueError(f"{label} must be one or more of the letters A to E.")
    return "".join(sorted(value))


def normalize_key(data):
    """The instructor's key as stored: defaults, then each version A, B, ... with its answered questions in order."""
    defaults = data.get("defaults")
    if not isinstance(defaults, dict):
        raise ValueError("Give the default points and scoring.")
    if defaults.get("scoring") not in SCORING:
        raise ValueError("Choose Exact, Partial or Either/Or scoring.")
    clean_defaults = {"points": parse_points(defaults.get("points"), "Default points"), "scoring": defaults["scoring"]}
    versions = data.get("versions")
    if not isinstance(versions, list) or not 1 <= len(versions) <= 26:
        raise ValueError("An answer key has between 1 and 26 versions.")
    clean_versions = []
    for index, version in enumerate(versions):
        letter = chr(ord("A") + index)
        if not isinstance(version, dict) or version.get("letter") != letter:
            raise ValueError("Versions are lettered A, B, C ... in order.")
        questions = version.get("questions")
        if not isinstance(questions, list) or len(questions) > QUESTIONS:
            raise ValueError(f"Version {letter} must list its answered questions.")
        seen, clean = set(), []
        for question in questions:
            number = question.get("number") if isinstance(question, dict) else None
            if type(number) is not int or not 1 <= number <= QUESTIONS or number in seen:
                raise ValueError(f"Version {letter} lists questions 1 to {QUESTIONS}, each once.")
            seen.add(number)
            item = {"number": number, "answer": parse_answer(question.get("answer"), f"Version {letter}, question {number}")}
            if question.get("points") not in (None, ""):
                item["points"] = parse_points(question["points"], f"Version {letter}, question {number}")
            if question.get("scoring") not in (None, ""):
                if question["scoring"] not in SCORING:
                    raise ValueError(f"Version {letter}, question {number}: choose Exact, Partial or Either/Or scoring.")
                item["scoring"] = question["scoring"]
            clean.append(item)
        clean_versions.append({"letter": letter, "questions": sorted(clean, key=lambda item: item["number"])})
    return {"rule": RULE, "defaults": clean_defaults, "versions": clean_versions}


def question_score(marked, answer, scoring, points):
    """What one question earns under Gradescope's rule for it (see the module notes)."""
    chosen, correct, points = set(marked), set(answer), Decimal(points)
    if scoring == "exact":
        earned = points if chosen == correct else Decimal(0)
    elif scoring == "either" and correct == set(LETTERS):
        earned = points  # Every letter keyed gives the question to everyone, a blank included.
    elif not chosen or chosen - correct:
        earned = Decimal(0)
    elif scoring == "either":
        earned = points
    else:
        earned = points * len(chosen & correct) / len(correct)
    return earned.quantize(CENT, ROUND_HALF_UP)


def score_sheet(answers, version, defaults):
    """A sheet's answers (question 1 first; "" is a blank, "BD" two marks) scored against one version of the key."""
    lines, earned, possible = [], Decimal(0), Decimal(0)
    for question in version["questions"]:
        points, scoring = Decimal(question.get("points", defaults["points"])), question.get("scoring", defaults["scoring"])
        number = question["number"]
        marked = answers[number - 1] if number <= len(answers) else ""
        got = question_score(marked, question["answer"], scoring, points)
        earned += got
        possible += points
        lines.append({"number": number, "marked": marked, "answer": question["answer"], "scoring": scoring,
                      "points": points_text(points), "earned": points_text(got), "read": number <= len(answers)})
    return {"earned": points_text(earned), "possible": points_text(possible), "questions": lines}


def file_title(plan):
    """The plan's title as a download's file name."""
    return "".join(char if char.isalnum() or char in " -_" else "-" for char in plan["title"]).strip() or "bubble-sheets"


class BubbleKeysMixin:
    @staticmethod
    def bubble_key(db, aid):
        row = db.execute("SELECT * FROM frq_bubble_keys WHERE assignment_id=? ORDER BY revision DESC LIMIT 1", (aid,)).fetchone()
        if not row:
            return None
        return {"id": row["id"], "revision": row["revision"], "created_at": row["created_at"], "actor": row["actor"], **json.loads(row["key_json"])}

    @staticmethod
    def _bubble_runs(db, plan):
        """Each present scan's latest complete read, when that read is of this plan's assignment and term."""
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='bubble_runs'").fetchone():
            return []
        return [dict(row) for row in db.execute(f"""SELECT r.*,m.instructor_review_required,j.id job_id FROM bubble_runs r
            JOIN inbox_messages m ON m.id=r.message_id JOIN inbox_jobs j ON j.message_id=m.id
            WHERE r.status='complete' AND r.assignment_id=? AND r.term_id=? AND {PRESENT.format('m.id')}
            AND r.id=(SELECT n.id FROM bubble_runs n WHERE n.message_id=r.message_id AND n.status='complete' ORDER BY n.created_at DESC,n.id DESC LIMIT 1)
            ORDER BY m.created_at,r.id""", (plan["source_assignment_id"], plan["term_id"]))]

    def bubble_brief(self, db, plan):
        """Cheap counts for the assignment card and page: sheets read for this plan, and the key's revision."""
        runs = self._bubble_runs(db, plan)
        sheets = sum(db.execute("SELECT COUNT(*) FROM bubble_sheets WHERE run_id=?", (run["id"],)).fetchone()[0] for run in runs)
        key = db.execute("SELECT MAX(revision) FROM frq_bubble_keys WHERE assignment_id=?", (plan["id"],)).fetchone()[0]
        return {"sheets": sheets, "scans": len(runs), "key_revision": key}

    def _bubble_view(self, aid):
        """The answer key and every sheet of this plan's scans, scored; private fields (enrollment ID, UIN) included."""
        reader = getattr(self.inbox, "bubble_sheets", None)
        with self.rosters.lock(), connect(self.path) as db:
            plan = self.plan(db, aid)
            key = self.bubble_key(db, aid)
            loaded, memo = [], {}  # each student's booklet version, read once for every scan
            for run in self._bubble_runs(db, plan):
                sheets = reader._sheets(db, run["id"], memo) if reader else []
                # Names are read only for scans that cleared review: the others are counted, not listed.
                students = reader._students(db, run) if reader and run["instructor_review_required"] == 0 else {}
                loaded.append((run, sheets, students))
            letters = self.version_letters(db, plan)
        return plan, {"assignment_id": aid, "key": key, "rule": RULE, "question_limit": QUESTIONS, **self._bubble_scores(key, loaded, letters)}

    def bubble_grading(self, aid):
        """What the Bubble sheets page shows: the key, and each sheet of the cleared scans scored against it (no UIN, no private ID)."""
        _, view = self._bubble_view(aid)
        for sheet in view["sheets"]:
            for private in ("enrollment_id", "uin", "questions"):
                sheet.pop(private, None)
        return view

    @staticmethod
    def _bubble_scores(key, loaded, letters=()):
        versions = [version["letter"] for version in key["versions"]] if key else []
        waiting_scans, sheets, marked = [], [], {}
        read_counts = {run["question_count"] for run, _, _ in loaded}
        for run, items, students in loaded:
            if run["instructor_review_required"]:
                # Scans still in the Inbox join grading after their review, like every other paper.
                waiting_scans.append({"job_id": run["job_id"], "sheets": len(items)})
                continue
            for item in items:
                current = item["current"]
                if current["status"] == "repeat":
                    continue  # the same sheet scanned twice is scored once, from the scan that had it first (BUBBLE-SHEETS)
                marked[current["version"]] = marked.get(current["version"], 0) + 1
                student = students.get(current["enrollment_id"], {}) if current["enrollment_id"] else {}
                sheet = {"sheet_id": item["id"], "job_id": run["job_id"], "page": item["source_page_index"], "student": student.get("name", ""),
                         "section": student.get("section_number", ""), "enrollment_id": current["enrollment_id"], "uin": student.get("uin", ""),
                         "marked_version": current["version"], "version": "", "status": "scored", "reason": "", "earned": None, "possible": None,
                         "wrong": [], "answers": current["answers"], "identified_by": current.get("identified_by")}
                if current["status"] == "needs_review":
                    sheet.update(status="needs_review", reason=current["reasons"][0] if current["reasons"] else "This sheet needs review in the Inbox.")
                elif not key:
                    sheet.update(status="no_key", reason="Save an answer key to score this sheet.")
                else:
                    # One version: every sheet takes it, whatever was bubbled. Several: the version marked (or reviewed) on the sheet.
                    letter = versions[0] if len(versions) == 1 else current["version"]
                    version = next((v for v in key["versions"] if v["letter"] == letter), None)
                    if not letter:
                        sheet.update(status="no_version", reason="No version was marked on this sheet. Record it in the sheet's review in the Inbox.")
                    elif not version or not version["questions"]:
                        sheet.update(status="no_key", marked_version=letter, reason=f"Version {letter} has no answers in the key yet.")
                    else:
                        result = score_sheet(current["answers"], version, key["defaults"])
                        sheet.update(version=letter, earned=result["earned"], possible=result["possible"], questions=result["questions"],
                                     wrong=[line["number"] for line in result["questions"] if Decimal(line["earned"]) < Decimal(line["points"])])
                sheets.append(sheet)
        sheets.sort(key=lambda sheet: (not sheet["student"], sheet["student"].casefold(), sheet["page"]))
        # How each keyed question went, per version: a question almost nobody got right is often a key to check.
        stats = {}
        for version in (key["versions"] if key else []):
            scored = [sheet for sheet in sheets if sheet["status"] == "scored" and sheet["version"] == version["letter"]]
            by_number = {}
            for sheet in scored:
                for line in sheet["questions"]:
                    by_number.setdefault(line["number"], []).append(line)
            per = {}
            for question in version["questions"]:
                lines = by_number.get(question["number"], [])
                counts = {}
                for line in lines:
                    counts[line["marked"]] = counts.get(line["marked"], 0) + 1
                common = max(counts.items(), key=lambda pair: (pair[1], pair[0] == question["answer"])) if counts else ("", 0)
                per[str(question["number"])] = {"sheets": len(lines), "full": sum(1 for line in lines if Decimal(line["earned"]) >= Decimal(line["points"])),
                                                "common": common[0], "common_count": common[1]}
            stats[version["letter"]] = per
        states = [sheet["status"] for sheet in sheets]
        fewest = min(read_counts, default=None)
        keyed = sorted({q["number"] for v in (key["versions"] if key else []) for q in v["questions"]})
        # The version tabs to start a new key with: the letters this term's charts handed out or were declared, else those marked on
        # at least two sheets (one stray mark is not a version), from A up to the last of them.
        seen = sorted(letter for letter, count in marked.items() if letter and count >= 2)
        top = max([*letters, *seen, "A"])
        return {"sheets": sheets, "stats": stats, "waiting_scans": waiting_scans,
                "counts": {"scored": states.count("scored"), "needs_review": states.count("needs_review"), "no_version": states.count("no_version"),
                           "no_key": states.count("no_key"), "in_inbox": sum(scan["sheets"] for scan in waiting_scans)},
                "read_questions": max(read_counts, default=0), "fewest_read": fewest,
                # A keyed question past the count a scan was read with: its marks were ignored, so no sheet of that scan can earn it.
                "beyond_read": [number for number in keyed if fewest is not None and number > fewest],
                "suggested_versions": [chr(code) for code in range(ord("A"), ord(top) + 1)]}

    def save_bubble_key(self, aid, data, actor="workspace"):
        if not isinstance(data, dict):
            raise ValueError("Supply a JSON object.")
        request_id = text(data.get("request_id"), "Request ID", 128, False)
        base = data.get("base_revision")
        if base is not None and (type(base) is not int or base < 1):
            raise ValueError("Name the answer key revision these changes were made on.")
        key = normalize_key(data)
        request_sha = digest({"base_revision": base, "key": key})
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            self.plan(db, aid)
            old = db.execute("SELECT request_sha256 FROM frq_bubble_keys WHERE assignment_id=? AND request_id=?", (aid, request_id)).fetchone()
            if old:
                if old[0] != request_sha:
                    raise Conflict("This request ID was used for a different answer key.")
            else:
                current = self.bubble_key(db, aid)
                if (current["revision"] if current else None) != base:
                    raise Conflict("The answer key was saved from another window since you opened it. Reload to see the saved key, then make your changes again.")
                stored = {k: current[k] for k in ("rule", "defaults", "versions")} if current else None
                if stored != key:  # Saving the same key again records nothing.
                    revision = (current["revision"] if current else 0) + 1
                    db.execute("INSERT INTO frq_bubble_keys VALUES(?,?,?,?,?,?,?,?,?)",
                               (uid(), aid, revision, encoded(key), digest(key), request_id, request_sha, actor, timestamp()))
                    self.event(db, aid, "bubble_key_saved", {"revision": revision, "rule": RULE,
                               "versions": {v["letter"]: len(v["questions"]) for v in key["versions"]}}, actor)
        return self.bubble_grading(aid)

    def bubble_scores_csv(self, aid):
        """The instructor's scores, one row per sheet of the cleared scans, for their own records and comparison with Gradescope."""
        plan, view = self._bubble_view(aid)
        key = view["key"]
        numbers = sorted({q["number"] for v in (key["versions"] if key else []) for q in v["questions"]})
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["Name", "UIN", "Section", "Version", "Score", "Max points", "Status", *[f"Q{n}" for n in numbers], "Scan page", "Note"])
        safe = lambda value: "'" + value if value and value[0] in "=+-@\t\r" else value  # spreadsheet formula prefixes
        status = {"scored": "scored", "needs_review": "needs review", "no_version": "no version", "no_key": "no key"}
        for sheet in view["sheets"]:
            lines = {line["number"]: line["earned"] for line in sheet.get("questions", [])}
            writer.writerow([safe(sheet["student"]), sheet["uin"], sheet["section"], sheet["version"] or sheet["marked_version"], sheet["earned"] or "",
                             sheet["possible"] or "", status[sheet["status"]], *[lines.get(n, "") for n in numbers], sheet["page"], safe(sheet["reason"])])
        return output.getvalue().encode("utf-8-sig"), f"{file_title(plan)} - bubble sheet scores.csv"

    def bubble_analysis(self, aid):
        """Item analysis of the sheets this page scores, per version (bubble_analysis.py): read only, and no student in it."""
        return self._bubble_analysis(aid)[1]

    def _bubble_analysis(self, aid):
        from . import bubble_analysis
        plan, view = self._bubble_view(aid)
        key = view["key"]
        return plan, {"assignment_id": aid, "rule": bubble_analysis.ANALYSIS, "scoring_rule": RULE, "minimum": bubble_analysis.MINIMUM,
                      "key_revision": key["revision"] if key else None, "counts": view["counts"],
                      "versions": bubble_analysis.analyse(key, view["sheets"])}

    def bubble_analysis_csv(self, aid):
        """The item analysis as a spreadsheet, one row per version and question: counts and shares only, as on the page."""
        plan, view = self._bubble_analysis(aid)
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["Version", "Question", "Key", "Scoring", "Points", "Sheets", "Percent full", "Difficulty", "Discrimination",
                         "Upper %", "Lower %", *LETTERS, "Blank", "Multiple", "Flags"])
        figure = lambda value: "" if value is None else value
        labels = {"possible_miskey": "Possible miskey", "hard": "Hard", "easy": "Easy", "weak": "Weak"}
        for version in view["versions"]:
            for question in version["questions"]:
                counts = {choice["choice"]: choice["count"] for choice in question["choices"]}
                writer.writerow([version["letter"], question["number"], question["answer"], question["scoring"], question["points"],
                                 question["sheets"], *[figure(question[name]) for name in ("full_pct", "difficulty", "discrimination", "upper_pct", "lower_pct")],
                                 *[counts[choice] for choice in (*LETTERS, "blank", "multiple")],
                                 "; ".join(f"{labels[flag['kind']]}: {flag['text']}" for flag in question["flags"])])
        return output.getvalue().encode("utf-8-sig"), f"{file_title(plan)} - bubble sheet item analysis.csv"
