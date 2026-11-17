"""The MC check (requirement, 2026-10-06). Gradescope scores the bubble sheets Faculty
uploaded with its own key; Faculty scores the same sheets with the instructor's key (`bubble_keys`). This compares the two, student
by student and question by question, as a check: Faculty's scoring is the grade (requirement, 2026-10-22). The read changes nothing;
a question Gradescope scores differently is then corrected there by `gradescope_mc_corrections` (GRADESCOPE "MC CORRECTIONS").

When a written-grades send completes (the papers and sheets went first, so Gradescope has had time to score the sheets), and whenever
the instructor presses Check MC, the sender queues one READ per section whose bubble-sheet exam Faculty links
(`frq_gradescope_sheet_targets`): Gradescope's Download Grades file of that exam (`gradescope_web.container_scores`). What Gradescope
showed is kept per Faculty enrollment (SID = UIN exactly, as for uploads) in `frq_gradescope_mc_checks` (migration 042). Every page then
compares the latest read of each section with Faculty's scoring as it is NOW (a key edited since needs no new read) without contacting
Gradescope: the Gradescope panels (written grades and bubble sheets) and Certification, where a difference is a check to confirm.

A student DIFFERS when Gradescope filed the sheet under another version, could not read its version ("?"), or gave any question other
points than Faculty's key gives it. The difference names the questions with both scores and what Faculty read on the sheet; a blank on
a question keyed with every letter, which Faculty's key gives to everyone (bubble-scoring-v2) and Gradescope scores 0, says so.
"""
import json
import logging
import re
import time
from decimal import Decimal, InvalidOperation

from werkzeug.exceptions import Conflict

from course_storage import timestamp
from rosters import connect

from . import gradescope_web as gw
from .models import uid

LOG = logging.getLogger(__name__)
LETTERS = set("ABCDE")
NOT_GRADED = "not graded"  # a question nobody graded on Gradescope, as the comparison and the corrections say it
TRIES = 20  # busy answers in a row before a read, a correction or a label unit fails for the instructor's retry (the sender's BUSY_TRIES)
WAIT_FOR_SHEETS = 60  # seconds a unit waits while a bubble-sheet send of its plan is still running
UNGRADED_SHEETS = 12  # a read looks for the ungraded questions of at most this many sheets of a section (more: Gradescope is still at work)


def dec(value):
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def points_str(value):
    number = dec(value)
    if number is None:
        return str(value or "")
    return format(number.normalize(), "f") if number != number.to_integral() else str(number.to_integral())


def why(line):
    """What Faculty knows about one question that scores differently: the instructor's key and what Faculty read on the sheet."""
    marked = line.get("marked") or ""
    if not marked and line.get("scoring") == "either" and set(line.get("answer") or "") >= LETTERS:
        return "left blank; your key gives this question to everyone, Gradescope gives a blank 0"
    read = f"“{marked}”" if marked else "a blank"
    return f"Faculty read {read}; your key: {line.get('answer') or '—'}"


def compare(sheets, reads, sections=None, uploaded=None):
    """Faculty's scored sheets (`FRQService._bubble_view`'s, private fields included) against the latest read of each section
    ({section_id: {"students": {enrollment: Gradescope's row}, "unmatched": n}}). `sections` {enrollment: section_id} places Faculty's
    students in the sections read; `uploaded` {enrollment: sheet_id} picks the sheet Faculty uploaded when a student has several.
    Returns counts and the students who differ, each with a `row` ("<section>:<enrollment>"), name, section number, both scores, the
    questions that differ and a one-line `detail`."""
    sections, uploaded = sections or {}, uploaded or {}
    faculty = {}
    for sheet in sheets:
        enrollment = sheet.get("enrollment_id")
        if not enrollment:
            continue
        if enrollment in faculty and uploaded.get(enrollment) != sheet.get("sheet_id"):
            continue
        faculty[enrollment] = sheet
    counts = {"compared": 0, "agree": 0, "differ": 0, "not_on_gradescope": 0, "not_graded": 0, "faculty_unscored": 0, "unmatched": 0}
    differ = []
    for section_id, read in reads.items():
        counts["unmatched"] += int(read.get("unmatched") or 0)
        students = read.get("students") or {}
        for enrollment, sheet in faculty.items():
            if sections.get(enrollment) == section_id and sheet.get("status") == "scored" and enrollment not in students:
                counts["not_on_gradescope"] += 1
        for enrollment, theirs in students.items():
            mine = faculty.get(enrollment)
            if not mine or mine.get("status") != "scored":
                counts["faculty_unscored"] += 1  # Gradescope scored a sheet Faculty has no score for (the Bubble sheets page says why)
                continue
            if theirs.get("total") is None or str(theirs.get("status") or "").lower() != "graded":
                counts["not_graded"] += 1
                # Questions nobody graded there (the read found them on their grading pages): a difference Faculty's score settles.
                ungraded = {str(n) for n in theirs.get("ungraded") or []}
                found = _difference(mine, {**theirs, "questions": {n: p for n, p in (theirs.get("questions") or {}).items() if n not in ungraded}}) if ungraded else None
                if found and found["questions"]:
                    for question in found["questions"]:
                        if str(question["number"]) in ungraded:
                            question.update(gradescope=NOT_GRADED, ungraded=True)
                    found["detail"] = _detail(found["gradescope"], found["faculty"], found["questions"])
                    differ.append({"row": f"{section_id}:{enrollment}", "name": mine.get("student") or "", "section": mine.get("section") or "", **found})
                continue
            counts["compared"] += 1
            found = _difference(mine, theirs)
            if not found:
                counts["agree"] += 1
                continue
            counts["differ"] += 1
            differ.append({"row": f"{section_id}:{enrollment}", "name": mine.get("student") or "", "section": mine.get("section") or "", **found})
    differ.sort(key=lambda d: (str(d["section"]), d["name"].casefold()))
    return {"counts": counts, "differ": differ}


def _difference(mine, theirs):
    """None when Gradescope and Faculty agree on the version and on every question; else what differs."""
    gradescope, faculty = {"version": theirs.get("version") or "", "total": points_str(theirs.get("total"))}, {"version": mine.get("version") or "", "total": points_str(mine.get("earned"))}
    if theirs.get("several"):
        return {"gradescope": gradescope, "faculty": faculty, "questions": [],
                "detail": "Gradescope has this student’s sheets in more than one version; Faculty uploads one sheet per student"}
    if gradescope["version"] == "?":
        return {"gradescope": gradescope, "faculty": faculty, "questions": [],
                "detail": f"Gradescope could not read the version on this sheet (Faculty: Version {faculty['version']}); it scored nothing"}
    if gradescope["version"] != faculty["version"]:
        return {"gradescope": gradescope, "faculty": faculty, "questions": [],
                "detail": f"Gradescope has Version {gradescope['version']}, Faculty Version {faculty['version']}: Gradescope {gradescope['total']}, Faculty {faculty['total']}"}
    lines = {str(line["number"]): line for line in mine.get("questions") or []}
    numbers = sorted(set(lines) | set(theirs.get("questions") or {}), key=int)
    questions = []
    for number in numbers:
        line, points = lines.get(number), (theirs.get("questions") or {}).get(number)
        ours, their = dec(line["earned"]) if line else None, dec(points) if points is not None else None
        if ours is None and their is None:
            continue
        if ours is None or their is None or ours != their:
            questions.append({"number": int(number), "gradescope": points_str(points) if points is not None else "—",
                              "faculty": points_str(line["earned"]) if line else "—",
                              "why": why(line) if line else "not in your key"})
    totals_differ = dec(gradescope["total"]) != dec(faculty["total"])
    if not questions and not totals_differ:
        return None
    return {"gradescope": gradescope, "faculty": faculty, "questions": questions, "detail": _detail(gradescope, faculty, questions)}


def _detail(gradescope, faculty, questions):
    parts = [f"Gradescope {gradescope['total']}, Faculty {faculty['total']}"]
    parts += [f"Q{q['number']}: Gradescope {q['gradescope']}, Faculty {q['faculty']} ({q['why']})" for q in questions[:4]]
    if len(questions) > 4:
        parts.append(f"{len(questions) - 4} more questions")
    return " · ".join(parts)


class McCheckMixin:
    """Mixed into `GradescopeSender`: queues the reads, works them as sender units (kind "mc_checks"), and shows the comparison."""

    # --- queueing ---------------------------------------------------------------------------------------------------------
    @staticmethod
    def _mc_targets(db, plan):
        """Each section's linked bubble-sheet exam with versions, in the section's current Gradescope course (the sheets' own rule)."""
        from .gradescope_sync import section_course_row
        found = []
        for section in db.execute("""SELECT DISTINCT s.id,s.number FROM frq_gradescope_sheet_targets t JOIN course_sections s ON s.id=t.section_id
                WHERE t.assignment_id=? ORDER BY s.number""", (plan["id"],)).fetchall():
            course = section_course_row(db, section["id"])
            target = db.execute("SELECT * FROM frq_gradescope_sheet_targets WHERE assignment_id=? AND section_id=? ORDER BY created_at DESC,rowid DESC LIMIT 1",
                                (plan["id"], section["id"])).fetchone()
            if target and course and target["course_link_id"] == course["id"]:
                found.append({"section_id": section["id"], "section": section["number"], "course_id": course["course_id"],
                              "container_id": target["container_id"], "versions": json.loads(target["versions_json"] or "{}")})
        return found

    def _mc_queue(self, db, aid, send_id=None, actor="workspace"):
        """One read per linked section, unless a check of the plan is already waiting or running. Returns the check's ID, or None."""
        if db.execute("SELECT 1 FROM frq_gradescope_mc_checks WHERE assignment_id=? AND status IN ('pending','sending') LIMIT 1", (aid,)).fetchone():
            return None
        targets = self._mc_targets(db, self.frq.plan(db, aid))
        if not targets:
            return None
        check_id, now = uid(), timestamp()
        for target in targets:
            db.execute("""INSERT INTO frq_gradescope_mc_checks(id,check_id,send_id,assignment_id,section_id,course_id,container_id,status,attempts,not_before,
                scores_json,error,actor,created_at,updated_at) VALUES(?,?,?,?,?,?,?,'pending',0,NULL,NULL,'',?,?,?)""",
                       (uid(), check_id, send_id, aid, target["section_id"], target["course_id"], target["container_id"], actor, now, now))
        self.frq.event(db, aid, "gradescope_mc_check", {"check_id": check_id, "send_id": send_id, "sections": len(targets)}, actor)
        return check_id

    def mc_check_start(self, aid, actor="workspace"):
        """The instructor's Check MC: reads Gradescope's scores again for every linked section (in the sender, a moment later)."""
        if not self.settings.status().get("configured"):
            raise Conflict("Gradescope is not set up for this Faculty, so its scores cannot be read.")
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            plan = self.frq.plan(db, aid)
            if not self._mc_targets(db, plan):
                raise Conflict("No section links this assignment’s bubble-sheet exam on Gradescope yet: send the bubble sheets first.")
            self._mc_queue(db, aid, None, actor)
        self.wake.set()
        return self.mc_check_status(aid)

    # --- the sender's unit ------------------------------------------------------------------------------------------------
    def _mc_next(self, db, skip, ids, now):
        return db.execute(f"""SELECT 'mc_checks' kind,* FROM frq_gradescope_mc_checks WHERE status='pending' {skip}
            AND (not_before IS NULL OR not_before<=?) ORDER BY created_at,rowid LIMIT 1""", (*ids, now)).fetchone()

    def _mc_set(self, unit_id, status, *, error="", scores=None, not_before=None, attempts=None):
        with self.rosters.lock(), connect(self.path) as db:
            sets, args = ["status=?", "error=?", "updated_at=?", "not_before=?"], [status, error[:2000], timestamp(), not_before]
            if scores is not None:
                sets.append("scores_json=?")
                args.append(json.dumps(scores, sort_keys=True))
            if attempts is not None:
                sets.append("attempts=?")
                args.append(attempts)
            db.execute(f"UPDATE frq_gradescope_mc_checks SET {','.join(sets)} WHERE id=? AND status IN ('pending','sending')", (*args, unit_id))

    def _mc_unit(self, unit):
        """One section's read. Never raises: a busy Gradescope or a refused sign-in waits like any unit, anything else fails the read
        with its reason (Check MC reads again). Nothing is ever written to Gradescope."""
        from .gradescope_sync import PAUSE, later
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM frq_gradescope_sheet_sends WHERE assignment_id=? AND status IN ('pending','running') LIMIT 1", (unit["assignment_id"],)).fetchone():
                db.execute("UPDATE frq_gradescope_mc_checks SET not_before=?,updated_at=? WHERE id=? AND status='pending'", (later(WAIT_FOR_SHEETS), timestamp(), unit["id"]))
                return  # the sheets go first: Gradescope scores them as they arrive
            claimed = db.execute("UPDATE frq_gradescope_mc_checks SET status='sending',attempts=attempts+1,updated_at=? WHERE id=? AND status='pending'",
                                 (timestamp(), unit["id"])).rowcount
            target = db.execute("SELECT versions_json FROM frq_gradescope_sheet_targets WHERE assignment_id=? AND section_id=? AND container_id=? ORDER BY created_at DESC,rowid DESC LIMIT 1",
                                (unit["assignment_id"], unit["section_id"], unit["container_id"])).fetchone()
            term = self.frq.plan(db, unit["assignment_id"])["term_id"]
            enrollments = {r[1]: r[0] for r in db.execute("SELECT id,uin FROM student_enrollments WHERE term_id=?", (term,))}
            uploads = {r["enrollment_id"]: dict(r) for r in db.execute("""SELECT * FROM frq_gradescope_sheet_uploads WHERE assignment_id=? AND section_id=?
                AND course_id=? AND status='accepted' ORDER BY created_at,rowid""", (unit["assignment_id"], unit["section_id"], unit["course_id"]))}
        if not claimed:
            return
        attempts = unit["attempts"] + 1
        if not target or not json.loads(target["versions_json"] or "{}"):
            self._mc_set(unit["id"], "failed", error="Faculty reads Gradescope’s scores only for a bubble-sheet exam with versions; check this one on Gradescope.")
            return
        try:
            session = self._session_now()
            found = session.container_scores(unit["course_id"], unit["container_id"])
            # A sheet Gradescope has not finished grading: which of its questions nobody graded there, from each question's grading
            # page (Download Grades shows such a question as 0), so Faculty's score can be put there too (MC CORRECTIONS "UNGRADED").
            unfinished = [(sid, row) for sid, row in found.items() if enrollments.get(sid) in uploads and row.get("version") not in ("", "?")
                          and not row.get("several") and str(row.get("status") or "").lower() != "graded"]
            if len(unfinished) <= UNGRADED_SHEETS:
                for sid, row in unfinished:
                    row["ungraded"] = self._mc_ungraded(session, uploads[enrollments[sid]], sorted(int(n) for n in row.get("questions") or {}))
        except gw.SignInRefused as error:
            self._paused = (time.monotonic() + PAUSE, self._sign_in_stamp())
            self._drop_session()
            self._mc_set(unit["id"], "pending", error=str(error), not_before=later(PAUSE), attempts=attempts)
            return
        except gw.GradescopeBusy as error:
            delay = self._pause(error)
            if attempts >= TRIES:
                self._mc_set(unit["id"], "failed", error=f"Gradescope stayed busy through {attempts} tries. Check MC again when it is quieter.")
            else:
                self._mc_set(unit["id"], "pending", error=f"Gradescope is busy; Faculty reads again in {delay} s.", not_before=later(delay), attempts=attempts)
            return
        except gw.GradescopeError as error:
            self._mc_set(unit["id"], "failed", error=str(error))
            return
        except Exception:
            LOG.exception("Reading Gradescope's multiple-choice scores stopped unexpectedly")
            self._mc_set(unit["id"], "failed", error="Faculty stopped unexpectedly while reading Gradescope’s scores. Check MC again.")
            return
        students, unmatched = {}, 0
        for sid, row in found.items():
            enrollment = enrollments.get(sid) if re.fullmatch(r"[0-9]{9}", sid or "") else None
            if enrollment:
                students[enrollment] = row
            else:
                unmatched += 1
        self._mc_set(unit["id"], "read", scores={"students": students, "unmatched": unmatched})
        self._mc_correct_after_read(unit["id"])  # Faculty's score controls: Gradescope is corrected where it differs (MC CORRECTIONS)
        self._mc_label_after_read(unit["id"])  # answers Gradescope met since are labelled with the correct answer (MC LABELS)

    # --- what the pages show (Faculty's records only) ---------------------------------------------------------------------
    def _mc_reads(self, db, aid):
        """The latest settled read of each section, and whether a check is still waiting or running."""
        reads, failed = {}, []
        for row in db.execute("""SELECT m.*,s.number FROM frq_gradescope_mc_checks m JOIN course_sections s ON s.id=m.section_id
                WHERE m.assignment_id=? AND m.status IN ('read','failed') ORDER BY m.created_at DESC,m.rowid DESC""", (aid,)).fetchall():
            if row["section_id"] in reads or any(f["section_id"] == row["section_id"] for f in failed):
                continue
            if row["status"] == "read":
                reads[row["section_id"]] = {**json.loads(row["scores_json"]), "at": row["updated_at"], "section": row["number"]}
            else:
                failed.append({"section_id": row["section_id"], "section": row["number"], "error": row["error"], "at": row["updated_at"]})
        open_row = db.execute("SELECT status,error FROM frq_gradescope_mc_checks WHERE assignment_id=? AND status IN ('pending','sending') ORDER BY created_at LIMIT 1",
                              (aid,)).fetchone()
        return reads, failed, open_row

    def mc_compare(self, db, aid, bubble):
        """The comparison for the pages, from the records and Faculty's bubble view (`FRQService._bubble_view`'s, private fields included;
        or a function giving it, called only when there is a read to compare). None when the plan links no bubble-sheet exam and was
        never checked."""
        reads, failed, open_row = self._mc_reads(db, aid)
        targets = self._mc_targets(db, self.frq.plan(db, aid))
        if not reads and not failed and not open_row and not targets:
            return None
        found = {"running": bool(open_row), "waiting": (open_row["error"] if open_row else "") or "", "checked": bool(reads),
                 "at": max((r["at"] for r in reads.values()), default=None), "sections": sorted(r["section"] for r in reads.values()),
                 "failed": [{k: f[k] for k in ("section", "error", "at")} for f in failed], "counts": None, "differ": []}
        if reads:
            members = {r[1]: r[0] for r in db.execute("""SELECT m.section_id,m.enrollment_id FROM roster_memberships m JOIN course_sections s ON s.id=m.section_id
                WHERE s.term_id=?""", (self.frq.plan(db, aid)["term_id"],))}
            uploaded = {r[0]: r[1] for r in db.execute("""SELECT enrollment_id,sheet_id FROM frq_gradescope_sheet_uploads WHERE assignment_id=? AND status='accepted'
                ORDER BY created_at,rowid""", (aid,))}
            view = bubble() if callable(bubble) else bubble
            found.update(compare((view or {}).get("sheets") or [], reads, members, uploaded))
            # MC CORRECTIONS: every correction Faculty made or is making, listed for the instructor to look at; a difference whose every
            # question a standing correction covers (waiting, or landed and not read again yet) is `corrected`, not one to confirm.
            found["corrections"] = self.mc_corrections(db, aid, view)
            standing = {(c["enrollment_id"], c["number"], c["version"], c["faculty"]): c["state"] for c in found["corrections"]
                        if c["state"] in ("pending", "sending", "uncertain", "accepted")}
            for differ in found["differ"]:
                enrollment = differ["row"].split(":", 1)[1]
                for question in differ["questions"]:
                    state = standing.get((enrollment, question["number"], differ["faculty"]["version"], question["faculty"]))
                    if state and differ["gradescope"]["version"] == differ["faculty"]["version"]:
                        question["correction"] = "corrected" if state == "accepted" else "correcting"
                differ["corrected"] = bool(differ["questions"]) and all(q.get("correction") for q in differ["questions"])
            # SETTLED (requirement, 2026-11-01): a finished correction whose section was read after it, on a sheet Gradescope graded, where the
            # question no longer differs from Faculty, is settled; Certification lists only the rest.
            still = {(d["row"], q["number"]) for d in found["differ"] for q in d["questions"]}
            whole = {d["row"] for d in found["differ"] if not d["questions"]}
            for correction in found["corrections"]:
                section_id, enrollment = correction["row"].split(":", 1)
                read = reads.get(section_id) or {}
                theirs = (read.get("students") or {}).get(enrollment) or {}
                graded = str(theirs.get("status") or "").lower() == "graded" and str(correction["number"]) in (theirs.get("questions") or {})
                correction["settled"] = (correction["state"] not in ("pending", "sending", "uncertain") and str(read.get("at") or "") > correction["at"]
                                         and graded and correction["row"] not in whole and (correction["row"], correction["number"]) not in still)
        return found

    def mc_check_status(self, aid):
        """What the panels show about the MC check (no UIN; names and section numbers, as the problem lists). Cheap for a plan with no
        bubble-sheet exam on Gradescope: Faculty's sheets are scored only when a read is there to compare."""
        with self.rosters.lock(), connect(self.path) as db:
            if not db.execute("""SELECT 1 FROM frq_gradescope_sheet_targets WHERE assignment_id=?
                    UNION ALL SELECT 1 FROM frq_gradescope_mc_checks WHERE assignment_id=? LIMIT 1""", (aid, aid)).fetchone():
                return None
            found = self.mc_compare(db, aid, lambda: self.frq._bubble_view(aid)[1])  # the lock is re-entered, as Certification does
        labels = self.mc_label_status(aid) if found is not None else None  # MC LABELS (gradescope_mc_labels), named only once there is a run
        return {**found, "labels": labels} if labels else found
