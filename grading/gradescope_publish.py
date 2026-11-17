"""Publishing grades on Gradescope from Certification and Export (requirement, 2026-10-02). AGENTS.md "Sending grades to Gradescope" is the rule book (PUBLISHING GRADES);
`gradescope_web` is the transport; the sender's thread does the work.

Until then Faculty never published anything, and it still publishes only on this explicit request. For a term grading plan, a
PREVIEW (POST, writes nothing anywhere) reads Faculty (each section's linked Gradescope assignment, written and bubble sheets, what
is not on Gradescope yet from Faculty's own receipts, and whether the grades are certified) and Gradescope (each linked course's
assignments list: published or not, its versions, Gradescope's own graded share) and lists per section what would be published and
why anything would not; the instructor may leave rows out. PUBLISH names its fingerprint with a request ID and
`confirmation: "publish"` (checked on the server; 409 when anything changed) and records the approval with one unit per Gradescope
assignment (sections sharing one assignment share its unit). The sender publishes a unit with Gradescope's own Review Grades form
(an exam with versions as its version set: "Publish All Grades"), reading the assignments list before (already published:
`present`, nothing written) and after (published: `accepted`; not: `failed`, for the instructor's Retry). A request whose answer
was lost is `uncertain` and settled by reading only, never by publishing again.

Publishing lets students see their results; Gradescope emails nobody for it (its Email Students is a separate button Faculty never
presses). Faculty never unpublishes. It never publishes while a send of the plan runs, and a send or a creation waits while a
publication runs: a send writing onto an assignment published under it would stop at its published check.
"""
import json
import re
import sqlite3

from werkzeug.exceptions import Conflict, NotFound

from course_storage import timestamp
from rosters import connect

from . import gradescope_web as gw
from .models import digest, uid

PUBLISH_CONFIRMATION = "publish"
PUBLISH_RULE = "gradescope-publish-v1"
PUBLISHING = "Faculty is publishing this assignment's grades on Gradescope. Send again when that has finished (it takes seconds)."
NOT_LINKED = "No section of this assignment has a Gradescope assignment linked yet: send to Gradescope first."
KEY = re.compile(r"(written|sheets):[^\s:]{1,100}")


def left_out(data):
    """The preview rows ("written:<section id>", "sheets:<section id>") the instructor left out of this publication."""
    value = data.get("leave_out", []) if isinstance(data, dict) else []
    if not isinstance(value, list) or not all(isinstance(v, str) and KEY.fullmatch(v) for v in value):
        raise ValueError("Name the rows left out as a list of their keys.")
    return sorted(set(value))


def state(rows, target_id):
    """What the assignments list says of one linked assignment or version set: None when it is gone, else its row, its versions and
    whether students see its grades (the set and EVERY version listed under it published; a bubble-sheet exam's versions are not
    listed, so its own row decides)."""
    target = next((r for r in rows if r["id"] == str(target_id)), None)
    if target is None:
        return None
    versions = sorted((r for r in rows if r["parent"] == target["id"]), key=lambda r: (r["version"], r["id"]))
    return {"target": target, "versions": versions, "published": bool(target["published"]) and all(r["published"] for r in versions),
            "partly": not target["published"] and any(r["published"] for r in versions)}


def graded_share(found):
    """Gradescope's own "% Graded" of an assignment or version set (`state`'s answer): its row's number, else the least among its
    versions that hold a paper; None when the list does not say (GRADESCOPE "AFTER CERTIFYING": Faculty then cannot tell that a
    written assignment is fully graded, and does not publish it by itself)."""
    own = found["target"]["graded"]
    if own is not None:
        return own
    parts = [v["graded"] for v in found["versions"] if int(v["submissions"] or 0)]
    return min(parts) if parts and all(p is not None for p in parts) else None


def percent(share):
    """Gradescope's graded share as the page says it: one decimal (its download gives 99.8547567175018), never rounded up to 100 or
    down to 0, so a share short of 100% never reads as fully graded (requirement, 2026-10-28)."""
    shown = round(share, 1)
    if share < 100 and shown >= 100:
        shown = 99.9
    elif share > 0 and shown <= 0:
        shown = 0.1
    return f"{shown:g}"


def warnings_for(certification, coverage, sheets):
    """Why publishing now may show students something unfinished (never a refusal: the instructor decides)."""
    found = []
    certified = (certification or {}).get("state")
    if certified == "none":
        found.append("These grades are not certified yet.")
    elif certified == "changed":
        found.append("Grades changed since they were certified; certify again first if the gradebook should match.")
    papers, grades = (coverage or {}).get("papers") or {}, (coverage or {}).get("grades") or {}
    missing = int(papers.get("total") or 0) - int(papers.get("sent") or 0)
    if missing > 0:
        found.append(f"{missing} {'paper is' if missing == 1 else 'papers are'} not uploaded to Gradescope by Faculty yet.")
    unsent = int(grades.get("not_sent") or 0) + int(grades.get("changed") or 0)
    if unsent > 0:
        found.append(f"{unsent} final {'grade is' if unsent == 1 else 'grades are'} not on Gradescope as Faculty has {'it' if unsent == 1 else 'them'} "
                     "(not sent, or changed since sent): students would see what Gradescope has now.")
    if sheets:
        left = int((sheets.get("sheets") or {}).get("matched") or 0) - int((sheets.get("sheets") or {}).get("sent") or 0)
        if left > 0:
            found.append(f"{left} matched bubble {'sheet is' if left == 1 else 'sheets are'} not uploaded to Gradescope yet.")
    return found


class PublishMixin:
    # --- what the panel shows without contacting Gradescope ----------------------------------------------------------------
    def publish_status(self, aid):
        with self.rosters.lock(), connect(self.path) as db:
            self.frq.plan(db, aid)
            publications = self._publications(db, aid)
        return {"assignment_id": aid, **self.settings.status(), "publications": publications}

    @staticmethod
    def _publishing(db, aid):
        """The publication of this plan still running, if any (a send or a creation waits for it)."""
        row = db.execute("SELECT id FROM frq_gradescope_publications WHERE assignment_id=? AND status IN ('pending','running')", (aid,)).fetchone()
        return row[0] if row else None

    @staticmethod
    def _sending(db, aid):
        """A send of this plan (written grades or bubble sheets) still running, if any (a publication waits for it)."""
        return db.execute("""SELECT 1 FROM frq_gradescope_sends WHERE assignment_id=? AND status IN ('pending','running')
            UNION ALL SELECT 1 FROM frq_gradescope_sheet_sends WHERE assignment_id=? AND status IN ('pending','running')""", (aid, aid)).fetchone()

    @staticmethod
    def _creating(db, aid):
        """The creation of this plan on Gradescope still running, if any (a publication waits for it)."""
        row = db.execute("SELECT id FROM frq_gradescope_creations WHERE assignment_id=? AND status IN ('pending','running')", (aid,)).fetchone()
        return row[0] if row else None

    def _publications(self, db, aid):
        """The latest publications of the plan, with where each Gradescope assignment is (for the panel)."""
        found = []
        for row in db.execute("SELECT * FROM frq_gradescope_publications WHERE assignment_id=? ORDER BY created_at DESC,rowid DESC LIMIT 3", (aid,)).fetchall():
            units = [{"id": u["id"], "part": u["part"], "sections": json.loads(u["sections_json"]), "course": u["course_title"], "title": u["target_title"],
                      "status": u["status"], "error": u["error"]}
                     for u in db.execute("SELECT * FROM frq_gradescope_publication_units WHERE send_id=? ORDER BY part DESC,course_title,target_title,rowid", (row["id"],))]
            found.append({"id": row["id"], "status": row["status"], "created_at": row["created_at"], "updated_at": row["updated_at"],
                          "plan": json.loads(row["plan_json"]), "units": units})
        return found

    # --- what would be published --------------------------------------------------------------------------------------------
    def _publish_plan(self, aid, data, session=None):
        """`session` (GRADESCOPE "AFTER CERTIFYING"): the sender's own, when Faculty plans a publication by itself; it is not closed
        here and a Gradescope that cannot be read is raised as it is (the sender waits on a busy one or a refused sign-in itself).
        `sets` then says, per row, whether the assignment or version set is published at all (its own row, or any version: a
        version added later is not, and Gradescope then shows the set itself as unpublished), and `shares` Gradescope's graded
        share of it (`graded_share`)."""
        leave = set(left_out(data))
        with self.rosters.lock(), connect(self.path) as db:
            plan = self.frq.plan(db, aid)
            linked = [("written", t) for t in self._targets(db, plan)] + [("sheets", t) for t in self._sheet_targets(db, plan)]
            running = self._publishing(db, aid)
            sending = self._sending(db, aid)
            creating = self._creating(db, aid)
        # Faculty's side, from its own records: whether the grades are certified, and what of them is not on Gradescope yet.
        certification = ((self.frq.assignment(aid).get("workflow") or {}).get("certification") or {})
        coverage = self.status(aid)["coverage"]
        sheets = self.sheet_status(aid)["coverage"] if any(part == "sheets" for part, _ in linked) else None
        view = {"assignment_id": aid, **self.settings.status(), "rule": PUBLISH_RULE, "rows": [], "problems": [],
                "warnings": warnings_for(certification, coverage, sheets), "certification": certification.get("state") or "none",
                "coverage": coverage, "sheets": sheets, "running": running, "publish": 0, "fingerprint": None, "ready": False}
        if sending:
            view["problems"].append("A send to Gradescope is still running for this assignment. Publish when it has finished: grades it "
                                    "writes after publishing would wait for your confirmation.")
        if creating:
            view["problems"].append("Faculty is still making this assignment on Gradescope.")
        if not view["configured"]:
            return {"view": view, "units": []}
        rows = []
        for part, target in linked:
            key = f"{part}:{target['section_id']}"
            row = {"key": key, "part": part, "section_id": target["section_id"], "section": target["section"], "course_id": target["course_id"],
                   "course": target["course_title"], "target_id": target["container_id"], "title": target["container_title"], "kind": "",
                   "versions": [], "submissions": 0, "graded": None, "published": False, "partly": False, "left_out": key in leave,
                   "problem": "", "warning": ""}
            if not target["course_id"]:
                row["problem"] = "This section has no Gradescope course linked yet."
            elif not target["container_id"]:
                row["problem"] = ("No Gradescope assignment is linked for this section yet: send to Gradescope (or upload papers only) first."
                                  if part == "written" else "No Gradescope bubble-sheet exam is linked for this section yet: send its sheets first.")
            rows.append(row)
        if not any(r["course_id"] and r["target_id"] for r in rows):
            view["problems"].append(NOT_LINKED)
        own, session = session is None, session or self.session_factory()
        sets, shares = {}, {}
        try:
            courses = {c["id"]: c for c in session.courses()} if rows else {}
            listed = {}
            for row in rows:
                if row["problem"] or row["left_out"]:
                    continue  # a row left out is not read, as a section left out of a send
                course = courses.get(row["course_id"])
                if not course or gw.forbidden(course["short"], course["name"], row["course"]):
                    row["problem"] = "That Gradescope course is not one of your courses (or is one Faculty never touches)."
                    continue
                if row["course_id"] not in listed:
                    listed[row["course_id"]] = session.assignments(row["course_id"])
                found = state(listed[row["course_id"]], row["target_id"])
                if not found:
                    row["problem"] = f"“{row['title']}” is gone from {row['course']} on Gradescope."
                    continue
                target = found["target"]
                # Gradescope shows a version set as unpublished while ANY version of it is (seen on the real site 2026-10-12: Quiz 4
                # with Versions A to D published and E added later), so a set counts as published when its row or a version is.
                sets[row["key"]] = bool(target["published"]) or any(v["published"] for v in found["versions"])
                shares[row["key"]] = graded_share(found)
                row.update(title=target["title"], kind=target["kind"], submissions=int(target["submissions"] or 0), graded=target["graded"],
                           published=found["published"], partly=found["partly"],
                           versions=[{"letter": gw.normalized(v["version"]).replace("version", "").strip().upper() or v["title"], "published": v["published"]}
                                     for v in found["versions"]])
                if target["kind"] not in ("assignment", "assignment_container"):
                    row["problem"] = "Faculty publishes only an assignment or an exam with versions."
                elif not found["published"] and not row["submissions"]:
                    row["problem"] = "It has no submissions on Gradescope: nothing to publish."
                elif not found["published"] and target["graded"] is not None and target["graded"] < 100:
                    row["warning"] = (f"Gradescope shows it {percent(target['graded'])}% graded: students would see the questions nobody graded "
                                      "there without a score.")
        except gw.GradescopeError as error:
            if not own:
                raise
            raise Conflict(f"Gradescope could not be read: {error}") from None
        finally:
            if own:
                session.close()
        # One unit per Gradescope assignment; sections sharing one are published together, so leaving one of them out is refused.
        units = {}
        for row in rows:
            if row["problem"] or row["published"] or not row["target_id"]:
                continue
            units.setdefault((row["part"], row["course_id"], row["target_id"]), []).append(row)
        planned = []
        for (part, course_id, target_id), shared in sorted(units.items()):
            kept = [r for r in shared if not r["left_out"]]
            if not kept:
                continue
            if len(kept) != len(shared):
                for r in kept:
                    r["problem"] = (f"Section{'s' if len(shared) - len(kept) > 1 else ''} {', '.join(str(s['section']) for s in shared if s['left_out'])} "
                                    "share this Gradescope assignment and are left out: publishing it shows their grades too. Leave all of them out or none.")
                continue
            first = kept[0]
            planned.append({"part": part, "course_id": course_id, "course_title": first["course"], "course_name": courses[course_id]["name"],
                            "target_type": first["kind"], "target_id": target_id, "target_title": first["title"],
                            "sections": sorted(r["section_id"] for r in kept), "numbers": sorted(str(r["section"]) for r in kept)})
        view["rows"] = rows
        view["publish"] = len(planned)
        view["ready"] = bool(planned) and not view["problems"] and not view["running"]
        # Gradescope's graded share and submission counts are shown, not pinned: a TA grading there meanwhile does not make it stale.
        pinned = [{k: r[k] for k in ("key", "course_id", "target_id", "kind", "published", "partly", "left_out", "problem")} | {"versions": r["versions"]} for r in rows]
        view["fingerprint"] = digest({"rule": PUBLISH_RULE, "rows": pinned, "units": planned, "problems": view["problems"], "warnings": view["warnings"],
                                      "certification": view["certification"]})
        return {"view": view, "units": planned, "sets": sets, "shares": shares}

    def publish_preview(self, aid, data=None):
        return self._publish_plan(aid, data or {})["view"]

    def publish(self, aid, data):
        from .gradescope_sync import request_head  # imported here: that module mixes this one in
        request_id, fingerprint = request_head(data, PUBLISH_CONFIRMATION, no_id="Publishing needs a request ID from the preview.",
                                               not_confirmed="Confirm publishing: students see their grades on Gradescope once it is done, so nothing is published without it.")
        leave = left_out(data)
        command = digest({"assignment_id": aid, "request_id": request_id, "fingerprint": fingerprint, "leave_out": leave})
        with self.rosters.lock(), connect(self.path) as db:
            earlier = db.execute("SELECT * FROM frq_gradescope_publications WHERE request_id=?", (request_id,)).fetchone()
        if earlier:
            if earlier["request_sha256"] != command or earlier["assignment_id"] != aid:
                raise Conflict("That request ID was already used for something else.")
            return self._publication_receipt(earlier)
        planned = self._publish_plan(aid, data)  # everything read again, Gradescope included
        view = planned["view"]
        if view["fingerprint"] != fingerprint:
            raise Conflict("Something changed since the preview, in Faculty or on Gradescope. Look at the new preview and publish again.")
        if view["running"]:
            raise Conflict("Faculty is still publishing this assignment's grades on Gradescope.")
        if view["problems"] or not planned["units"]:
            raise Conflict("There is nothing Faculty can publish on Gradescope for this assignment now.")
        return self._record_publication(aid, planned, request_id, command, fingerprint, leave)

    def _record_publication(self, aid, planned, request_id, command, fingerprint, leave, actor="workspace", automatic=None, after=None):
        """The approved publication and its units, in one transaction (`publish`'s, and Faculty's own after a certification: `actor`
        automatic, `automatic` what its card says, `after` what is written with it; GRADESCOPE "AFTER CERTIFYING")."""
        view = planned["view"]
        now, publication_id = timestamp(), uid()
        try:
            with self.rosters.lock(), connect(self.path) as db:
                db.execute("BEGIN IMMEDIATE")
                earlier = db.execute("SELECT * FROM frq_gradescope_publications WHERE request_id=?", (request_id,)).fetchone()
                if earlier:
                    if earlier["request_sha256"] != command:
                        raise Conflict("That request ID was already used for something else.")
                    return self._publication_receipt(earlier)
                if self._sending(db, aid):
                    raise Conflict("A send to Gradescope started for this assignment. Publish when it has finished.")
                # GRADESCOPE: recheck creation in the same transaction that records publishing.
                if self._creating(db, aid):
                    raise Conflict("Faculty is still making this assignment on Gradescope.")
                summary = {"leave_out": leave, "warnings": view["warnings"], "certification": view["certification"],
                           "units": [{k: u[k] for k in ("part", "course_title", "target_title", "numbers")} for u in planned["units"]],
                           **({"automatic": automatic} if automatic else {})}
                db.execute("INSERT INTO frq_gradescope_publications VALUES(?,?,?,?,?,?,'pending',?,?,?)",
                           (publication_id, aid, request_id, command, fingerprint, json.dumps(summary, sort_keys=True), actor, now, now))
                for unit in planned["units"]:
                    db.execute("""INSERT INTO frq_gradescope_publication_units(id,send_id,assignment_id,part,course_id,course_title,target_type,target_id,
                        target_title,sections_json,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,'pending',?,?)""",
                        (uid(), publication_id, aid, unit["part"], unit["course_id"], unit["course_title"], unit["target_type"], unit["target_id"],
                         unit["target_title"], json.dumps(unit["numbers"]), now, now))
                self.frq.event(db, aid, "gradescope_publish", {"publication_id": publication_id, "request_id": request_id, "fingerprint": fingerprint,
                                                               "units": [[u["part"], u["course_id"], u["target_id"]] for u in planned["units"]],
                                                               "leave_out": leave, "certification": view["certification"],
                                                               **({"automatic": automatic} if automatic else {})}, actor)
                if after:
                    after(db, publication_id)
                row = db.execute("SELECT * FROM frq_gradescope_publications WHERE id=?", (publication_id,)).fetchone()
        except sqlite3.IntegrityError:
            raise Conflict("Faculty is already publishing this assignment's grades on Gradescope. Look at the new preview.") from None
        self.wake.set()
        return self._publication_receipt(row)

    def publish_cancel(self, aid, publication_id):
        """Stops a publication: nothing still waiting is published. What Gradescope already published stays so (Faculty never unpublishes)."""
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._publication_row(db, aid, publication_id)
            if row["status"] in ("pending", "running"):
                now = timestamp()
                db.execute("UPDATE frq_gradescope_publication_units SET status='cancelled',updated_at=? WHERE send_id=? AND status IN ('pending','failed')",
                           (now, publication_id))
                db.execute("UPDATE frq_gradescope_publications SET status='cancelled',updated_at=? WHERE id=?", (now, publication_id))
                self.frq.event(db, aid, "gradescope_publish_cancelled", {"publication_id": publication_id})
            row = db.execute("SELECT * FROM frq_gradescope_publications WHERE id=?", (publication_id,)).fetchone()
        return self._publication_receipt(row)

    def publish_retry(self, aid, publication_id):
        """The instructor's explicit Retry of the assignments Faculty could not publish (never one whose outcome is still being read)."""
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._publication_row(db, aid, publication_id)
            if row["status"] == "cancelled":
                raise Conflict("A cancelled publication is not retried. Preview and publish again.")
            if db.execute("SELECT 1 FROM frq_gradescope_publications WHERE assignment_id=? AND id!=? AND status IN ('pending','running')",
                          (aid, publication_id)).fetchone():
                raise Conflict("Faculty is publishing this assignment's grades in another request; let it finish first.")
            if self._sending(db, aid):
                raise Conflict("A send to Gradescope is still running for this assignment. Retry when it has finished.")
            if self._creating(db, aid):
                raise Conflict("Faculty is still making this assignment on Gradescope.")
            now = timestamp()
            retried = db.execute("UPDATE frq_gradescope_publication_units SET status='pending',not_before=NULL,error='',updated_at=? WHERE send_id=? AND status='failed'",
                                 (now, publication_id)).rowcount
            if not retried:
                raise Conflict("Nothing in this publication failed.")
            db.execute("UPDATE frq_gradescope_publications SET status='running',updated_at=? WHERE id=?", (now, publication_id))
            self.frq.event(db, aid, "gradescope_publish_retried", {"publication_id": publication_id, "units": retried})
            row = db.execute("SELECT * FROM frq_gradescope_publications WHERE id=?", (publication_id,)).fetchone()
        self.wake.set()
        return self._publication_receipt(row)

    @staticmethod
    def _publication_row(db, aid, publication_id):
        row = db.execute("SELECT * FROM frq_gradescope_publications WHERE id=? AND assignment_id=?", (publication_id, aid)).fetchone()
        if not row:
            raise NotFound("That publication was not found for this assignment.")
        return row

    @staticmethod
    def _publication_receipt(row):
        return {"publication_id": row["id"], "request_id": row["request_id"], "status": row["status"], "created_at": row["created_at"],
                "plan": json.loads(row["plan_json"])}

    # --- the sender's side: one Gradescope assignment at a time --------------------------------------------------------------
    def _settle_publications(self, db, now):
        for row in db.execute("SELECT id,assignment_id FROM frq_gradescope_publications WHERE status IN ('pending','running')").fetchall():
            if not db.execute("SELECT 1 FROM frq_gradescope_publication_units WHERE send_id=? AND status IN ('pending','sending','uncertain')", (row["id"],)).fetchone():
                db.execute("UPDATE frq_gradescope_publications SET status='complete',updated_at=? WHERE id=?", (now, row["id"]))
                self.frq.event(db, row["assignment_id"], "gradescope_publish_complete", {"publication_id": row["id"]})

    @staticmethod
    def _publication_read(session, unit):
        """What Gradescope shows of the unit's assignment now (a read), shaped for its receipt."""
        found = state(session.assignments(unit["course_id"]), unit["target_id"])
        if not found:
            return None
        return {"published": found["published"], "title": found["target"]["title"], "submissions": int(found["target"]["submissions"] or 0),
                "versions": {v["version"] or v["title"]: v["published"] for v in found["versions"]}}

    def _publish_unit(self, unit):
        if unit["status"] == "uncertain":
            return self._resolve_publication(unit)
        if not self._claim(unit):
            return
        kind = "publications"
        if gw.forbidden(unit["course_title"]):
            self._set(kind, unit["id"], "failed", error="That is the course Faculty never touches (Math 140 for Instructors).")
            return
        with self.rosters.lock(), connect(self.path) as db:
            sending = self._sending(db, unit["assignment_id"])
        if sending:
            self._set(kind, unit["id"], "failed", error="A send to Gradescope for this assignment is running; nothing was published. Retry when it has finished.")
            return
        session = self._session_now()
        before = self._publication_read(session, unit)  # a read: failing here sent nothing (the sender defers it)
        if before is None:
            self._set(kind, unit["id"], "failed", error=f"“{unit['target_title']}” is gone from {unit['course_title']} on Gradescope; nothing was published.")
            return
        if before["published"]:
            self._settled(unit["id"])
            if unit.get("requested_at"):  # Faculty's own earlier request (answered busy) landed after all: nothing is sent again
                self._set(kind, unit["id"], "accepted", receipt={**before, "at": timestamp()})
            else:
                self._set(kind, unit["id"], "present", receipt={**before, "at": timestamp()},
                          error="Its grades were already published on Gradescope; Faculty sent nothing.")
            return
        with self.rosters.lock(), connect(self.path) as db:  # the request about to leave, recorded before it does
            db.execute("UPDATE frq_gradescope_publication_units SET requested_at=?,updated_at=? WHERE id=?", (timestamp(), timestamp(), unit["id"]))
        busy, answer = None, None
        try:
            answer = session.publish(unit["course_id"], unit["target_type"], unit["target_id"])
        except gw.PublishRefused as error:
            self._set(kind, unit["id"], "failed", error=str(error))
            return
        except gw.GradescopeBusy as error:
            if error.outcome != "unknown":  # 429 or 503: Gradescope did not do it; it goes again after the wait (reading first)
                self._busy(unit, error)
                return
            busy, answer = error, error.status  # 502 or 504: it may have been done, so it is read back first
        except gw.GradescopeError as error:
            if error.outcome != "unknown":
                self._defer(unit, str(error))
                return
        try:
            after = self._publication_read(session, unit)
        except gw.GradescopeError as error:
            from .gradescope_sync import later  # the sender's own clock (imported here: that module mixes this one in)
            wait = {"before": later(self._pause(error))} if isinstance(error, gw.GradescopeBusy) else {}
            self._set(kind, unit["id"], "uncertain", error=f"Gradescope did not answer when Faculty checked the publishing ({error}); it will look again.", **wait)
            return
        if after and after["published"]:
            self._settled(unit["id"])
            self._set(kind, unit["id"], "accepted", receipt={**after, "at": timestamp()})
        elif busy:
            self._busy({**unit, "status": "sending"}, busy, f"Gradescope {gw.BUSY[busy.status]} and did not publish.")
        else:
            self._set(kind, unit["id"], "failed", error=f"Gradescope does not show these grades published (it answered {answer or 'nothing'}). Retry to publish again.")

    def _resolve_publication(self, unit):
        """A publication whose outcome is unknown: Gradescope is READ, never asked again, to settle it."""
        found = self._publication_read(self._session_now(), unit)
        if found and found["published"]:
            self._settled(unit["id"])
            self._set("publications", unit["id"], "accepted", receipt={**found, "at": timestamp()})
        else:
            self._set("publications", unit["id"], "failed", error=("Gradescope does not show these grades published; the request did not land. Retry to publish."
                                                                  if found else f"“{unit['target_title']}” is gone from {unit['course_title']} on Gradescope."))
