"""Gradescope after certifying (requirement, 2026-10-12). GRADESCOPE "AFTER
CERTIFYING".

A certification queues ONE row in `frq_gradescope_automatic_runs` (migration 046), in its own transaction:

- `certified`: the instructor's Certify with "Send to Gradescope and publish the grades" ticked (`plan_json`: each part's decision).
  Faculty then, by itself: makes the assignment in every section's LINKED Gradescope course that has none (the instructor's Create,
  with its default title: `_creation_plan(..., linked_only=True)`), sends the bubble sheets and then the papers and grades that are
  not there yet (the dialogs' own plans, a section left out when it is not linked or only papers were ever sent for it), and
  publishes each section's assignment.
- `late`: a late paper Faculty certified and sent by itself (`plan_json.rows`). Once its send has finished, Faculty publishes the
  version set it went to, only where the instructor had already published that set (a makeup version Faculty added lands
  unpublished).

Here, in the sender thread (its sign-in pauses and busy waits hold), a run is looked at one step at a time. Each step records an
ORDINARY creation, send or publication with actor `automatic` (its own request ID, so it is made once), or says why not, and moves
on; from then on that creation, send or publication follows every rule of its kind (read back, uncertain settled by reading, failed
only on the instructor's Retry). A step waits while anything of the plan is still going to Gradescope.

HOLD THAT SECTION (the instructor's choice): Faculty publishes a section's assignment only when it is clean. Held, with why, when
Gradescope shows it less than fully graded, a final grade of a paper on Gradescope is not there as Faculty has it, something of
Faculty's own send did not go through, or (bubble sheets) Gradescope scored a sheet differently from the key, holds another sheet
than Faculty's, or its scores could not be compared. A student with nothing on Gradescope (not on its roster) holds nothing up.
Sections sharing one Gradescope assignment are held together. A held section waits for the instructor's own Publish grades;
nothing is retried by itself. Faculty never unpublishes and never emails students.
"""
import json
import logging
import sqlite3
import time
import uuid
from datetime import datetime, timezone

from werkzeug.exceptions import Conflict, NotFound

from course_storage import timestamp
from rosters import connect

from . import gradescope_web as gw
from .gradescope_automatic import GIVE_UP, NAMESPACE, QUIET_SKIPS
from .models import digest, uid

LOG = logging.getLogger(__name__)
SWITCH = "auto_gradescope"  # Compute settings "Send to Gradescope and publish after certifying", on by default
STEPS = {"certified": ("create", "sheets", "written", "publish"), "late": ("publish",)}
OWN_WAIT = 10  # seconds between two looks while something of the plan is still going to Gradescope
DOING = {"create": "Making the assignment on Gradescope", "sheets": "Sending the bubble sheets", "written": "Sending the papers and grades",
         "publish": "Publishing the grades"}
MC_TRIES = 5  # reads of Gradescope's bubble-sheet scores a run asks for while some sheet there is not scored yet (a minute apart)
MC_READING = "Faculty is reading Gradescope’s scores of the bubble sheets."
LINKED_ALREADY = "Already goes to an assignment on Gradescope."
LIVE = (("frq_gradescope_creations", "Faculty is making this assignment on Gradescope."),
        ("frq_gradescope_sheet_sends", "Its bubble sheets are going to Gradescope."),
        ("frq_gradescope_sends", "Its papers and grades are going to Gradescope."),
        ("frq_gradescope_publications", "Its grades are being published on Gradescope."))


def plural(count, one, many=None):
    return f"{count:,} {one if count == 1 else many or one + 's'}"


def queue_run(db, certification_id, aid, kind, asked, actor, now):
    """The certification's own transaction: what it asks the Gradescope sender to do by itself afterwards."""
    db.execute("""INSERT INTO frq_gradescope_automatic_runs(id,certification_id,assignment_id,kind,plan_json,step,status,outcome_json,attempts,error,actor,
        created_at,updated_at) VALUES(?,?,?,?,?,?,'pending','{}',0,'',?,?,?)""",
               (uid(), certification_id, aid, kind, json.dumps(asked, sort_keys=True), STEPS[kind][0], actor, now, now))


class AfterCertifyingMixin:
    # --- the sender's unit ------------------------------------------------------------------------------------------------------
    def _run_next(self, db, skip, ids, now):
        # The sender's unit `kind` is 'runs'; the row's own kind (certified or late) is its `part`.
        return db.execute(f"""SELECT 'runs' kind,kind part,id,certification_id,assignment_id,plan_json,step,status,outcome_json,attempts,not_before,error,actor,
            created_at,updated_at FROM frq_gradescope_automatic_runs WHERE status='pending' {skip} AND (not_before IS NULL OR not_before<=?)
            ORDER BY created_at,rowid LIMIT 1""", (*ids, now)).fetchone()

    def _run_unit(self, unit):
        """One look at one run: claimed, one step taken or left waiting. Never raises."""
        from .gradescope_sync import PAUSE, WAIT
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            claimed = db.execute("UPDATE frq_gradescope_automatic_runs SET status='working',attempts=attempts+1,updated_at=? WHERE id=? AND status='pending'",
                                 (timestamp(), unit["id"])).rowcount
        if not claimed:
            return
        try:
            stale = self._run_stale(unit)
            if stale:
                return self._run_stop(unit, stale)
            getattr(self, f"_run_{unit['step']}")(unit, json.loads(unit["plan_json"] or "{}"))
        except gw.SignInRefused as error:
            self._paused = (time.monotonic() + PAUSE, self._sign_in_stamp())
            self._drop_session()
            self._run_wait(unit, str(error), PAUSE)
        except gw.GradescopeBusy as error:
            delay = self._pause(error)
            self._run_wait(unit, f"Gradescope is busy; Faculty tries again in {delay} s.", delay)
        except gw.GradescopeError as error:
            self._run_wait(unit, f"Gradescope could not be read: {error}", WAIT)
        except Exception:
            LOG.exception("Faculty's own Gradescope work after a certification stopped unexpectedly")
            self._run_wait(unit, "Faculty stopped unexpectedly while preparing this.", WAIT)

    def _run_stale(self, unit):
        """Why a queued run is not carried out any more, looked at before every step (None while it stands): Gradescope is not set
        up, the switch was turned off (the instructor's way to stop what waits), the instructor certified the whole assignment again
        since (that certification decides, with its own run or none), or `GIVE_UP` has passed since its certification (a sender that
        could not work for hours never resumes an old run)."""
        if not self.settings.status().get("configured"):
            return "Gradescope is not set up."
        if not self.frq.settings()["settings"].get(SWITCH, True):
            return "“Send to Gradescope and publish after certifying” was turned off in Compute settings."
        with self.rosters.lock(), connect(self.path) as db:
            again = db.execute("""SELECT 1 FROM frq_certifications WHERE assignment_id=? AND actor!='automatic' AND amends_id IS NULL
                AND rowid>(SELECT rowid FROM frq_certifications WHERE id=?) LIMIT 1""", (unit["assignment_id"], unit["certification_id"])).fetchone()
        if again:
            return "You certified this assignment again since; that certification decides what goes to Gradescope."
        if datetime.now(timezone.utc) - datetime.fromisoformat(unit["created_at"].replace("Z", "+00:00")) > GIVE_UP:
            return "More than six hours passed since the certification; Faculty does nothing more by itself."
        return None

    @staticmethod
    def _run_move(db, unit, did):
        """The step is taken: what it did is kept, and the run moves to its next step (or is done)."""
        steps = STEPS[unit["part"]]
        at = steps.index(unit["step"])
        following = steps[at + 1] if at + 1 < len(steps) else "done"
        outcome = {**json.loads(unit["outcome_json"] or "{}"), unit["step"]: did}
        db.execute("""UPDATE frq_gradescope_automatic_runs SET step=?,status=?,outcome_json=?,error='',not_before=NULL,updated_at=? WHERE id=? AND status='working'""",
                   (following, "done" if following == "done" else "pending", json.dumps(outcome, sort_keys=True), timestamp(), unit["id"]))

    def _run_took(self, unit, did):
        with self.rosters.lock(), connect(self.path) as db:
            self._run_move(db, unit, did)
        self.wake.set()

    def _run_stop(self, unit, why):
        """Nothing more is done by itself, and the page says why."""
        outcome = {**json.loads(unit["outcome_json"] or "{}"), "stopped": why}
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("""UPDATE frq_gradescope_automatic_runs SET step='done',status='done',outcome_json=?,error='',not_before=NULL,updated_at=?
                WHERE id=? AND status='working'""", (json.dumps(outcome, sort_keys=True), timestamp(), unit["id"]))

    def _run_wait(self, unit, reason, delay, note=None):
        """Nothing was recorded by this look: the run waits `delay` seconds and is looked at again (`_run_stale` ends a run that has
        waited `GIVE_UP`). `note`: something to keep meanwhile (the MC check it asked for)."""
        from .gradescope_sync import later
        outcome = json.dumps({**json.loads(unit["outcome_json"] or "{}"), **(note or {})}, sort_keys=True)
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("""UPDATE frq_gradescope_automatic_runs SET status='pending',error=?,not_before=?,outcome_json=?,updated_at=? WHERE id=? AND status='working'""",
                       (f"Waiting: {reason}"[:2000], later(delay), outcome, timestamp(), unit["id"]))

    def _run_busy(self, aid, late=True):
        """What of the plan is still going to Gradescope (catalog only, no lock): a step waits for it. `late`: a late paper's send too."""
        with connect(self.path) as db:
            for table, words in LIVE:
                if db.execute(f"SELECT 1 FROM {table} WHERE assignment_id=? AND status IN ('pending','running') LIMIT 1", (aid,)).fetchone():
                    return words
            if late and db.execute("SELECT 1 FROM frq_gradescope_automatic_sends WHERE assignment_id=? AND status IN ('pending','sending') LIMIT 1", (aid,)).fetchone():
                return "A late paper is going to Gradescope."
        return None

    def _run_earlier(self, unit, table, request_id, did):
        """Its creation, send or publication was recorded and the run's own row was not moved (a stop in between): moved now."""
        with self.rosters.lock(), connect(self.path) as db:
            earlier = db.execute(f"SELECT id FROM {table} WHERE request_id=?", (request_id,)).fetchone()
            if earlier:
                self._run_move(db, unit, did(earlier[0]))
        return bool(earlier)

    @staticmethod
    def _run_request(unit, what):
        return str(uuid.uuid5(NAMESPACE, f"after-{what}:{unit['id']}"))

    def _run_parts(self, unit):
        with self.rosters.lock(), connect(self.path) as db:
            row = db.execute("SELECT parts_json FROM frq_certifications WHERE id=?", (unit["certification_id"],)).fetchone()
        return json.loads(row[0] or "[]") if row else []

    @staticmethod
    def _declined(asked, part):
        return (asked.get(part) or {}).get("decision") == "do_not_send"

    def _run_automatic(self, unit, **more):
        return {"certification_id": unit["certification_id"], "run": unit["id"], "after": unit["part"], **more}

    # --- make what is missing ---------------------------------------------------------------------------------------------------
    def _run_create(self, unit, asked):
        from .gradescope_sync import WAIT
        aid = unit["assignment_id"]
        if "frq" not in self._run_parts(unit) or self._declined(asked, "written"):
            return self._run_took(unit, {})
        request_id = self._run_request(unit, "create")
        if self._run_earlier(unit, "frq_gradescope_creations", request_id, lambda found: {"creation_id": found}):
            return
        busy = self._run_busy(aid)
        if busy:
            return self._run_wait(unit, busy, OWN_WAIT)
        try:
            planned = self._creation_plan(aid, {}, session=self._session_now(), linked_only=True)
        except (Conflict, NotFound, ValueError) as error:
            return self._run_took(unit, {"notes": [str(getattr(error, "description", error))]})
        view = planned["view"]
        if view["running"]:
            return self._run_wait(unit, LIVE[0][1], OWN_WAIT)
        from .gradescope_publish import PUBLISHING
        if PUBLISHING in view["problems"]:
            return self._run_wait(unit, PUBLISHING, OWN_WAIT)  # a publication takes seconds: wait, never a step that took
        # What keeps the assignment from being made matters only where some linked course would get one.
        notes = (list(view["problems"]) if view["courses"] else []) + [f"{c['course']}: {c['problem']}" for c in view["courses"] if c.get("problem")] \
            + [f"Section {s['section']}: {s['reason']}" for s in view["sections"] if s["reason"] and s["reason"] != LINKED_ALREADY]
        if view["problems"] or not planned["units"]:
            return self._run_took(unit, {"notes": notes[:20]} if notes else {})
        command = digest({"assignment_id": aid, "request_id": request_id, "fingerprint": view["fingerprint"], "automatic": unit["id"]})
        try:
            receipt = self._record_creation(aid, planned, request_id, command, view["fingerprint"], actor="automatic", automatic=self._run_automatic(unit))
        except Conflict as error:
            return self._run_wait(unit, str(error.description), WAIT)
        self._run_took(unit, {"creation_id": receipt["creation_id"], **({"notes": notes[:20]} if notes else {})})

    # --- send what is not there -------------------------------------------------------------------------------------------------
    @staticmethod
    def _papers_only(db, aid, section_id):
        """Whether only papers ever went to Gradescope for this section (its grades are made there): Faculty then sends it no grade."""
        if not db.execute("SELECT 1 FROM frq_gradescope_uploads WHERE assignment_id=? AND section_id=? AND status='accepted' LIMIT 1", (aid, section_id)).fetchone():
            return False
        return not db.execute("""SELECT 1 FROM frq_gradescope_grades g JOIN frq_gradescope_uploads u ON u.id=g.upload_id WHERE g.assignment_id=? AND u.section_id=?
            AND g.status='accepted' LIMIT 1""", (aid, section_id)).fetchone()

    def _run_sheets(self, unit, asked):
        from .gradescope_sync import WAIT
        aid = unit["assignment_id"]
        if "bubble" not in self._run_parts(unit):
            return self._run_took(unit, {})
        if self._declined(asked, "sheets"):
            return self._run_took(unit, {"declined": True})
        request_id = self._run_request(unit, "sheets")
        if self._run_earlier(unit, "frq_gradescope_sheet_sends", request_id, lambda found: {"send_id": found}):
            return
        busy = self._run_busy(aid)
        if busy:
            return self._run_wait(unit, busy, OWN_WAIT)
        faculty = self._sheet_faculty(aid)
        if faculty["live"]:
            return self._run_wait(unit, LIVE[1][1], OWN_WAIT)
        requested, notes = {}, []
        for section_id in sorted({s["section_id"] for s in faculty["sheets"] if s["section_id"] and s["enrollment_id"]}, key=lambda s: str(faculty["numbers"].get(s, ""))):
            if not (faculty["targets"].get(section_id) or {}).get("container_id"):
                # Never to an exam Faculty would only propose: the instructor chooses a section's bubble-sheet exam once.
                requested[section_id] = {"leave_out": True}
                notes.append(f"Section {faculty['numbers'].get(section_id, '')}: no Gradescope bubble-sheet exam is linked for it yet. Send its sheets once from the Gradescope panel.")
        view = self._sheet_view(aid, faculty)
        plan = self._sheet_compose(faculty, requested, self._session_now(), view)
        notes += [f"Section {s['section']}: {s['problem']}" for s in view["sections"] if s.get("problem")]
        if not plan["uploads"]:
            return self._run_took(unit, {"notes": notes[:20]} if notes else {})
        fingerprint = view["fingerprint"]
        command = digest({"assignment_id": aid, "request_id": request_id, "fingerprint": fingerprint, "automatic": unit["id"], "kind": "sheets",
                          **({"published": sorted(plan["published"])} if plan["published"] else {})})
        did = {"sheets": len(plan["uploads"]), "skipped": view["summary"].get("sheets", {}).get("skipped", 0), **({"notes": notes[:20]} if notes else {})}
        try:
            self._record_sheet_send(aid, plan, request_id, command, fingerprint, actor="automatic",
                                    automatic=self._run_automatic(unit, **({"published": True} if plan["published"] else {})),
                                    after=lambda db, send_id: self._run_move(db, unit, {"send_id": send_id, **did}))
        except sqlite3.IntegrityError:
            self._run_wait(unit, "Another send recorded some of these meanwhile.", WAIT)
        except Conflict as error:
            self._run_wait(unit, str(error.description), WAIT)

    def _run_written(self, unit, asked):
        from .gradescope_sync import WAIT
        aid = unit["assignment_id"]
        if "frq" not in self._run_parts(unit):
            return self._run_took(unit, {})
        if self._declined(asked, "written"):
            return self._run_took(unit, {"declined": True})
        request_id = self._run_request(unit, "written")
        if self._run_earlier(unit, "frq_gradescope_sends", request_id, lambda found: {"send_id": found}):
            return
        busy = self._run_busy(aid)
        if busy:
            return self._run_wait(unit, busy, OWN_WAIT)
        faculty = self._faculty(aid)
        if faculty["live"]:
            return self._run_wait(unit, LIVE[2][1], OWN_WAIT)
        requested, notes = {}, []
        with self.rosters.lock(), connect(self.path) as db:
            for section_id, number in sorted({(p["section_id"], p["section"]) for p in faculty["people"]}, key=lambda s: str(s[1])):
                target = faculty["targets"].get(section_id) or {}
                if not target.get("course_id"):
                    why = "its Gradescope course is not linked yet."
                elif not target.get("container_id"):
                    # Never to an assignment Faculty would only propose: what it did not make or the instructor did not choose.
                    why = "no Gradescope assignment is linked for it. Send it once from the Gradescope panel."
                elif self._papers_only(db, aid, section_id):
                    why = "only papers were sent to Gradescope for it, so its grades are left to Gradescope."
                else:
                    continue
                requested[section_id] = {"leave_out": True}
                notes.append(f"Section {number}: {why}")
        view = self._send_view(aid, faculty)
        plan = self._compose(aid, faculty, requested, self._session_now(), view)
        notes += [f"Section {s['section']}: {s['problem']}" for s in view["sections"] if s.get("problem")]
        if not plan["uploads"] and not plan["grades"]:
            return self._run_took(unit, {"notes": notes[:20]} if notes else {})
        fingerprint = view["fingerprint"]
        command = digest({"assignment_id": aid, "request_id": request_id, "fingerprint": fingerprint, "automatic": unit["id"],
                          **({"published": sorted(plan["published"])} if plan["published"] else {})})
        counts = view["summary"]
        did = {"papers": len(plan["uploads"]), "grades": len(plan["grades"]), "skipped": counts.get("papers", {}).get("skipped", 0),
               "refused": counts.get("grades", {}).get("refused", 0), **({"notes": notes[:20]} if notes else {})}
        try:
            self._record_send(aid, plan, request_id, command, fingerprint, timestamp(), uid(), actor="automatic",
                              automatic=self._run_automatic(unit, **({"published": True} if plan["published"] else {})),
                              after=lambda db, send_id: self._run_move(db, unit, {"send_id": send_id, **did}))
        except sqlite3.IntegrityError:
            self._run_wait(unit, "Another send recorded some of these meanwhile.", WAIT)
        except Conflict as error:
            self._run_wait(unit, str(error.description), WAIT)

    # --- publish what is clean --------------------------------------------------------------------------------------------------
    def _mc_behind(self, db, aid):
        """`running` while Gradescope's scores are being read, `behind` when some linked section's were last read before its latest
        sheet went there (or never), else None. Catalog only."""
        reads, failed, open_row = self._mc_reads(db, aid)
        if open_row or db.execute("""SELECT 1 FROM frq_gradescope_mc_corrections WHERE assignment_id=? AND status IN ('pending','sending','uncertain')
                LIMIT 1""", (aid,)).fetchone():
            return "running"  # a read, or a correction of Gradescope's scores (MC CORRECTIONS), which reads them again once it lands
        for target in self._mc_targets(db, self.frq.plan(db, aid)):
            sent = db.execute("SELECT MAX(updated_at) FROM frq_gradescope_sheet_uploads WHERE assignment_id=? AND section_id=? AND status='accepted'",
                              (aid, target["section_id"])).fetchone()[0]
            looked = max((reads.get(target["section_id"]) or {}).get("at") or "", next((f["at"] for f in failed if f["section_id"] == target["section_id"]), ""))
            if sent and looked < sent:
                return "behind"
        return None

    def _own_trouble(self, db, unit, outcome):
        """{(part, section_id): reason} for what Faculty's own sends of this run did not get through, or did not send at all."""
        found, counted = {}, {}
        sends = {"written": [(outcome.get("written") or {}).get("send_id")], "sheets": [(outcome.get("sheets") or {}).get("send_id")]}
        if unit["part"] == "late":
            sends = {"written": [], "sheets": []}
            for row in db.execute("SELECT section_id,kind,status,error,send_id FROM frq_gradescope_automatic_sends WHERE certification_id=?", (unit["certification_id"],)):
                if row["status"] == "recorded":
                    sends[row["kind"]].append(row["send_id"])
                elif row["status"] == "skipped" and row["error"] not in QUIET_SKIPS:
                    found[(row["kind"], row["section_id"])] = f"Faculty did not send the late paper: {row['error']}"
        for send_id in filter(None, sends["written"]):
            for section_id, count in db.execute("""SELECT section_id,COUNT(*) FROM frq_gradescope_uploads WHERE send_id=? AND status!='accepted' GROUP BY section_id
                    UNION ALL SELECT r.section_id,COUNT(*) FROM frq_gradescope_grades g JOIN frq_responses r ON r.id=g.response_id
                    WHERE g.send_id=? AND g.status!='accepted' GROUP BY r.section_id""", (send_id, send_id)):
                counted[("written", section_id)] = counted.get(("written", section_id), 0) + count
        for send_id in filter(None, sends["sheets"]):
            for section_id, count in db.execute("SELECT section_id,COUNT(*) FROM frq_gradescope_sheet_uploads WHERE send_id=? AND status!='accepted' GROUP BY section_id",
                                                (send_id,)):
                counted[("sheets", section_id)] = counted.get(("sheets", section_id), 0) + count
        for key, count in counted.items():
            what = plural(count, "sheet") if key[0] == "sheets" else plural(count, "paper or grade", "papers or grades")
            found.setdefault(key, f"{what} of Faculty's own send did not go through. Use Retry in the Gradescope panel, then Publish grades.")
        return found

    def _mc_sections(self, db, aid, bubble):
        """{section_id: counts | {"failed": why}}: the latest read of each linked section compared with Faculty's sheets ONE SECTION
        AT A TIME (`gradescope_mc.compare`), so a section is held for its own sheets only."""
        from .gradescope_mc import compare
        reads, failed, _ = self._mc_reads(db, aid)
        members = {r[1]: r[0] for r in db.execute("""SELECT m.section_id,m.enrollment_id FROM roster_memberships m JOIN course_sections s ON s.id=m.section_id
            WHERE s.term_id=?""", (self.frq.plan(db, aid)["term_id"],))}
        uploaded = {r[0]: r[1] for r in db.execute("""SELECT enrollment_id,sheet_id FROM frq_gradescope_sheet_uploads WHERE assignment_id=? AND status='accepted'
            ORDER BY created_at,rowid""", (aid,))}
        found = {f["section_id"]: {"failed": f["error"] or "the read failed"} for f in failed}
        for section_id, read in reads.items():
            found[section_id] = compare((bubble or {}).get("sheets") or [], {section_id: read}, members, uploaded)["counts"]
        return found

    def _run_holds(self, unit, asked, outcome, rows, sets, shares):
        """({row key: why Faculty does not publish it}, the keys that are simply not this run's, how many bubble sheets Gradescope
        has not scored yet). HOLD THAT SECTION, above."""
        from .gradescope_publish import percent
        aid, late = unit["assignment_id"], unit["part"] == "late"
        mine = set(asked.get("rows") or [])
        open_rows = [r for r in rows if not r["published"] and not r["left_out"]]
        hidden = {r["key"]: {v["letter"] for v in r["versions"] if not v["published"]} for r in open_rows}  # what publishing would show
        with self.rosters.lock():
            responses = self.frq.responses(aid) if any(r["part"] == "written" for r in open_rows) else []
            sheets = [r for r in open_rows if r["part"] == "sheets" and not late]
            bubble = self.frq._bubble_view(aid)[1] if sheets else None
            with connect(self.path) as db:
                written = self.coverage_detail(db, aid, responses)["students"] if responses else {}
                # The version each student's paper is in on Gradescope, from Faculty's own accepted upload.
                versions = {f"{r[0]}:{r[1]}": r[2] or "" for r in db.execute("""SELECT section_id,enrollment_id,exam_version FROM frq_gradescope_uploads
                    WHERE assignment_id=? AND status='accepted' ORDER BY created_at,rowid""", (aid,))}
                never = self.sheet_coverage_detail(db, aid, self.frq.plan(db, aid))["never"] if sheets else {}
                compared = self._mc_sections(db, aid, bubble) if sheets else {}
                trouble = self._own_trouble(db, unit, outcome)
        # A late paper: only the version set it went to, where that set itself is published and the paper's own version is not, with
        # every section that shares the set. A set the instructor has not published, or a paper on a published version, needs nothing.
        wanted = {(r["course_id"], r["target_id"]) for r in open_rows if r["part"] == "written" and sets.get(r["key"])
                  and {versions[m] for m in mine if m.startswith(f"{r['section_id']}:") and m in versions} & hidden[r["key"]]}
        held, quiet, unscored = {}, set(), 0
        for row in open_rows:
            key, part, section_id = row["key"], row["part"], row["section_id"]
            if late and (part != "written" or (row["course_id"], row["target_id"]) not in wanted):
                quiet.add(key)
                continue
            if not row["course_id"] or not row["target_id"] or (row["kind"] in ("assignment", "assignment_container") and not row["submissions"]):
                quiet.add(key)  # nothing of it is on Gradescope (not linked, or no paper there): nothing to publish; the sends said why
                continue
            share = shares.get(key)
            if row["problem"]:
                held[key] = row["problem"]
            elif share is not None and share < 100:
                held[key] = f"Gradescope shows it {percent(share)}% graded: students would see the questions nobody graded there without a score."
            elif part == "written" and share is None:
                held[key] = "Gradescope does not say how much of it is graded, so Faculty cannot tell that every paper there is."
            elif part == "written":
                students = {r: s for r, s in written.items() if r.startswith(f"{section_id}:")}
                if late:
                    # "Publish All Grades" shows EVERY version not yet published: each paper in one of them counts, not only the late one.
                    students = {r: s for r, s in students.items() if r in mine or versions.get(r) in hidden[key]}
                off = sum(s["grades"]["changed"] + s["grades"]["not_sent"] for s in students.values() if s["paper"])
                if off:
                    held[key] = (f"{plural(off, 'final grade')} of papers on Gradescope {'is' if off == 1 else 'are'} not there as Faculty has "
                                 f"{'it' if off == 1 else 'them'}.")
                elif late and any(m.startswith(f"{section_id}:") and m not in versions for m in mine):
                    held[key] = "The late paper is not on Gradescope."
            else:
                other, counts = sum(1 for r in never if r.startswith(f"{section_id}:")), compared.get(section_id)
                unscored += int((counts or {}).get("not_graded") or 0)
                if other:
                    held[key] = f"Gradescope has another sheet than Faculty's for {plural(other, 'student')}."
                elif counts is None or "failed" in counts or not (counts["compared"] or counts["not_graded"] or counts["faculty_unscored"]):
                    held[key] = "Gradescope's scores of the bubble sheets could not be compared with your key. Use Check MC in the Gradescope panel."
                elif counts["differ"]:
                    held[key] = f"Gradescope scored {plural(counts['differ'], 'bubble sheet')} differently from your key."
                elif counts["not_graded"]:
                    held[key] = f"Gradescope has not scored {plural(counts['not_graded'], 'bubble sheet')} yet. Use Check MC in the Gradescope panel once it has."
                elif counts["faculty_unscored"]:
                    held[key] = f"Gradescope scored {plural(counts['faculty_unscored'], 'bubble sheet')} that Faculty has no score for."
            if key not in held and (part, section_id) in trouble:
                held[key] = trouble[(part, section_id)]
        # Sections sharing one Gradescope assignment are published together, so they are held together.
        for row in open_rows:
            if row["key"] in held or row["key"] in quiet:
                continue
            partner = next((o for o in open_rows if o["key"] in held and (o["part"], o["course_id"], o["target_id"]) == (row["part"], row["course_id"], row["target_id"])), None)
            if partner:
                held[row["key"]] = f"It shares its Gradescope assignment with section {partner['section']}, which is held."
        return held, quiet, unscored

    def _run_publish(self, unit, asked):
        from .gradescope_publish import NOT_LINKED
        from .gradescope_sync import WAIT
        aid, outcome = unit["assignment_id"], json.loads(unit["outcome_json"] or "{}")
        request_id = self._run_request(unit, "publish")
        if self._run_earlier(unit, "frq_gradescope_publications", request_id, lambda found: {"publication_id": found}):
            return
        busy = self._run_busy(aid)
        if busy:
            return self._run_wait(unit, busy, OWN_WAIT)
        asked_mc = int(outcome.get("mc_asked") or 0)
        if unit["part"] == "certified" and "bubble" in self._run_parts(unit):
            # Gradescope scores the sheets with its own key: the comparison with Faculty's must be as new as the sheets there.
            with self.rosters.lock(), connect(self.path) as db:
                db.execute("BEGIN IMMEDIATE")
                behind = self._mc_behind(db, aid)
                if behind == "behind" and not asked_mc:
                    self._mc_queue(db, aid, None, "automatic")
            if behind == "running" or (behind == "behind" and not asked_mc):
                self.wake.set()
                return self._run_wait(unit, MC_READING, OWN_WAIT, note={"mc_asked": 1} if behind == "behind" else None)
        session = self._session_now()

        def read(leave=()):
            """The publish plan, or None when something of the plan started going to Gradescope while it was read."""
            found = self._publish_plan(aid, {"leave_out": sorted(leave)} if leave else {}, session=session)
            moving = found["view"]["running"] or [p for p in found["view"]["problems"] if p != NOT_LINKED] or self._run_busy(aid)
            return None if moving else found
        first = read()
        if first is None:
            return self._run_wait(unit, self._run_busy(aid) or LIVE[3][1], OWN_WAIT)
        if first["view"]["problems"]:
            return self._run_took(unit, {})  # nothing is linked on Gradescope: the steps before said why
        held, quiet, unscored = self._run_holds(unit, asked, outcome, first["view"]["rows"], first["sets"], first["shares"])
        if unscored and asked_mc < MC_TRIES:
            # Gradescope had not scored every sheet when its scores were read (it takes a while after an upload): read again later.
            with self.rosters.lock(), connect(self.path) as db:
                db.execute("BEGIN IMMEDIATE")
                self._mc_queue(db, aid, None, "automatic")
            self.wake.set()
            return self._run_wait(unit, MC_READING, WAIT, note={"mc_asked": asked_mc + 1})
        said = [{"section": r["section"], "part": r["part"], "reason": held[r["key"]]} for r in first["view"]["rows"] if r["key"] in held]
        leave = sorted(held.keys() | quiet)
        planned = read(leave) if leave else first
        if planned is None:
            return self._run_wait(unit, self._run_busy(aid) or LIVE[3][1], OWN_WAIT)
        if planned["view"]["problems"] or not planned["units"]:
            return self._run_took(unit, {"held": said} if said else {})
        fingerprint = planned["view"]["fingerprint"]
        command = digest({"assignment_id": aid, "request_id": request_id, "fingerprint": fingerprint, "leave_out": leave, "automatic": unit["id"]})
        did = {"sections": sorted({number for u in planned["units"] for number in u["numbers"]}), **({"held": said} if said else {})}
        try:
            self._record_publication(aid, planned, request_id, command, fingerprint, leave, actor="automatic",
                                     automatic=self._run_automatic(unit, **({"held": said} if said else {})),
                                     after=lambda db, publication_id: self._run_move(db, unit, {"publication_id": publication_id, **did}))
        except Conflict as error:
            self._run_wait(unit, str(error.description), WAIT)

    # --- what the pages show (catalog only) -------------------------------------------------------------------------------------
    def after_runs(self, db, aid):
        """The plan's latest run of each kind, newest first, for Certification and Export and the Gradescope panel: where it is, what
        it did, what it holds and why (section numbers and reasons only). A held section, a note or a stop is shown until a later
        publication of the plan finishes (the instructor's own, after fixing it)."""
        found, seen = [], set()
        for row in db.execute("SELECT * FROM frq_gradescope_automatic_runs WHERE assignment_id=? ORDER BY created_at DESC,rowid DESC LIMIT 20", (aid,)):
            if row["kind"] in seen:
                continue
            seen.add(row["kind"])
            outcome = json.loads(row["outcome_json"] or "{}")
            done = row["status"] == "done"
            publish = outcome.get("publish") or {}
            answered = bool(done and db.execute("""SELECT 1 FROM frq_gradescope_publications WHERE assignment_id=? AND status='complete' AND created_at>? AND id!=?
                LIMIT 1""", (aid, row["updated_at"], publish.get("publication_id") or "")).fetchone())
            entry = {"id": row["id"], "kind": row["kind"], "certification_id": row["certification_id"], "state": "done" if done else "working",
                     "at": row["updated_at"]}
            if not done:
                entry["doing"] = DOING[row["step"]]
                if row["error"]:
                    entry["waiting"] = row["error"]
            sent = {key: (outcome.get(part) or {}).get(key, 0) for part, key in (("written", "papers"), ("written", "grades"), ("sheets", "sheets"))}
            if any(sent.values()):
                entry["sent"] = sent
            if (outcome.get("create") or {}).get("creation_id"):
                entry["made"] = True
            if publish.get("publication_id"):
                units = [dict(u) for u in db.execute("SELECT sections_json,status,error FROM frq_gradescope_publication_units WHERE send_id=?", (publish["publication_id"],))]
                entry["published"] = sorted({n for u in units if u["status"] in ("accepted", "present") for n in json.loads(u["sections_json"])})
                entry["publishing"] = sorted({n for u in units if u["status"] in ("pending", "sending", "uncertain") for n in json.loads(u["sections_json"])})
                failed = [{"sections": json.loads(u["sections_json"]), "reason": u["error"]} for u in units if u["status"] == "failed"]
                if failed and not answered:
                    entry["failed"] = failed
            if not answered:
                notes = [n for part in ("create", "sheets", "written") for n in (outcome.get(part) or {}).get("notes") or []]
                for key, value in (("held", publish.get("held")), ("notes", notes), ("stopped", outcome.get("stopped"))):
                    if value:
                        entry[key] = value
            found.append(entry)
        return found
