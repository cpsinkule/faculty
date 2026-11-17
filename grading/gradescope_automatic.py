"""The automatic late-paper send (requirement, 2026-10-09). GRADESCOPE "AUTOMATIC LATE-PAPER
SEND", GRADING "LATE PAPERS CERTIFY THEMSELVES".

When Faculty certifies a late paper by itself (`certification_automatic.py`), its own transaction queues one row per kind in
`frq_gradescope_automatic_sends` (migration 045): the student's written paper with its grades (papers only where only papers were sent
for their section), and their bubble sheet. Here, in the sender thread (its sign-in pauses and busy waits hold), each row is prepared
as the send dialog prepares a send, limited to that one student (`_faculty`/`_compose` and `_sheet_faculty`/`_sheet_compose` with
`student`), against the section's LINKED Gradescope course and assignment only (a link Faculty would only propose is never made by
itself), and recorded as an ordinary send with actor `automatic`. A published assignment is written to as the instructor chose. From
then on its units are an ordinary send's: settled by reading Gradescope, never sent again by themselves, a failure retried only by the
instructor's Retry.

A row waits (never sends twice) while another send or a publication of the assignment runs (read first, from the catalog alone,
before the plan is read under the lifecycle lock), while a version the student's paper is in is being added on Gradescope (or is an
approved makeup version of this plan Faculty has not tried to add yet: `_makeup_next` adds it within a minute), or while Gradescope is
busy or refused the sign-in; it is skipped, with why, when the student cannot be sent this way (their section not linked, not on the
Gradescope roster, a page count that differs, nothing left to send), or after `GIVE_UP` of waiting. Skipped papers stay "not on
Gradescope" on Certification and Export, for the send dialog; the student's Gradescope cell there says why (`automatic_sends`), and
`automatic_status` gives the panel the waits, the skips and the makeup versions Faculty could not add (read only; nothing is retried).
"""
import json
import logging
import sqlite3
import time
import uuid
from datetime import datetime, timedelta, timezone

from werkzeug.exceptions import Conflict

from course_storage import timestamp
from rosters import connect

from . import gradescope_web as gw
from .models import digest, uid

LOG = logging.getLogger(__name__)
NAMESPACE = uuid.UUID("0b6f7c1e-2f43-4c51-9d0b-8a1d5e3f7a62")
GIVE_UP = timedelta(hours=6)  # a row still waiting this long after its certification is skipped, with why
MAKEUP_LOOK = 60  # seconds between two looks for a makeup version to add on Gradescope
# Skips that leave nothing for the instructor to do: the paper is there already, or nothing of it was left to send.
QUIET_SKIPS = ("Already on Gradescope.", "Nothing to send.")


class AutomaticSendMixin:
    def _auto_next(self, db, skip, ids, now):
        # The sender's unit `kind` is 'automatic'; the row's own kind (written or sheets) is its `part`.
        return db.execute(f"""SELECT 'automatic' kind,kind part,id,certification_id,assignment_id,section_id,enrollment_id,papers_only,status,send_id,
            attempts,not_before,error,actor,created_at,updated_at FROM frq_gradescope_automatic_sends WHERE status='pending' {skip}
            AND (not_before IS NULL OR not_before<=?) ORDER BY created_at,rowid LIMIT 1""", (*ids, now)).fetchone()

    def _auto_settle(self, unit, status, error="", delay=None, send_id=None):
        from .gradescope_sync import later
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("""UPDATE frq_gradescope_automatic_sends SET status=?,error=?,not_before=?,send_id=COALESCE(?,send_id),updated_at=?
                WHERE id=? AND status='sending'""", (status, error[:2000], later(delay) if delay else None, send_id, timestamp(), unit["id"]))

    def _auto_wait(self, unit, reason, delay):
        """Nothing was recorded: the row waits `delay` seconds, unless it has waited `GIVE_UP` already."""
        created = datetime.fromisoformat(unit["created_at"].replace("Z", "+00:00"))
        if datetime.now(timezone.utc) - created > GIVE_UP:
            self._auto_settle(unit, "skipped", f"Not sent: it waited too long ({reason})")
        else:
            self._auto_settle(unit, "pending", f"Waiting: {reason}", delay=delay)

    @staticmethod
    def _auto_recorded(db, unit_id, send_id):
        db.execute("UPDATE frq_gradescope_automatic_sends SET status='recorded',send_id=?,error='',not_before=NULL,updated_at=? WHERE id=? AND status='sending'",
                   (send_id, timestamp(), unit_id))

    def _auto_unit(self, unit):
        """One row: claimed, prepared and recorded, or left waiting or skipped. Never raises."""
        from .gradescope_sync import PAUSE, WAIT
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            claimed = db.execute("UPDATE frq_gradescope_automatic_sends SET status='sending',attempts=attempts+1,updated_at=? WHERE id=? AND status='pending'",
                                 (timestamp(), unit["id"])).rowcount
        if not claimed:
            return
        try:
            (self._auto_written if unit["part"] == "written" else self._auto_sheets)(unit)
        except gw.SignInRefused as error:
            self._paused = (time.monotonic() + PAUSE, self._sign_in_stamp())
            self._drop_session()
            self._auto_wait(unit, str(error), PAUSE)
        except gw.GradescopeBusy as error:
            delay = self._pause(error)
            self._auto_wait(unit, f"Gradescope is busy; Faculty tries again in {delay} s.", delay)
        except gw.GradescopeError as error:
            self._auto_wait(unit, f"Gradescope could not be read: {error}", WAIT)
        except Exception:
            LOG.exception("Preparing a late paper's Gradescope send stopped unexpectedly")
            self._auto_wait(unit, "Faculty stopped unexpectedly while preparing this send.", WAIT)

    def _auto_earlier(self, unit, table, request_id):
        with self.rosters.lock(), connect(self.path) as db:
            earlier = db.execute(f"SELECT id FROM {table} WHERE request_id=?", (request_id,)).fetchone()
            if earlier:
                self._auto_recorded(db, unit["id"], earlier[0])
        return bool(earlier)

    def _auto_section(self, unit, view, creating=False):
        """Why the student's section cannot be sent to by itself (None when it can): its course and assignment must be the ones
        linked, never proposed; a version still being added on Gradescope waits (`creating`)."""
        section = next((s for s in view["sections"] if s["section_id"] == unit["section_id"]), None)
        if section is None:
            return "skip", "Nothing of this student's is ready to send."
        if section.get("missing_versions") and creating:
            return "wait", "Faculty is still adding this version on Gradescope."
        if section.get("problem"):
            return "skip", section["problem"]
        if section.get("proposed") or not section.get("container_id"):
            return "skip", "This section's Gradescope course and assignment are not linked yet: send from the Gradescope panel."
        return None

    def _auto_busy(self, aid, table, running):
        """Why the row must wait before anything heavy is read (2026-10-09 review: each waiting row read the whole plan under the
        lifecycle lock every minute): another send of this kind, or a publication of the plan, still running. Catalog only, no lock;
        `_faculty`'s `live` and the send's own transaction stay the guards."""
        from .gradescope_publish import PUBLISHING
        with connect(self.path) as db:
            if db.execute(f"SELECT 1 FROM {table} WHERE assignment_id=? AND status IN ('pending','running') LIMIT 1", (aid,)).fetchone():
                return running
            return PUBLISHING if self._publishing(db, aid) else None

    def _versions_coming(self, db, aid, letters):
        """Whether one of these letters missing from the student's set is an approved makeup version of this plan Faculty has not tried
        to add by itself yet (no creation under its request ID, no `gradescope_version_not_added`): `_makeup_next` adds it within a
        minute, so the row waits for it rather than being skipped for good (2026-10-09 review)."""
        for plan_id, letter in self.frq.approved_makeup_versions(db) if letters else ():
            if plan_id != aid or letter not in letters:
                continue
            if db.execute("SELECT 1 FROM frq_gradescope_creations WHERE request_id=?", (self._version_request(aid, letter),)).fetchone():
                continue
            if db.execute("""SELECT 1 FROM frq_events WHERE assignment_id=? AND kind='gradescope_version_not_added' AND json_extract(detail_json,'$.letter')=?
                    LIMIT 1""", (aid, letter)).fetchone():
                continue
            return True
        return False

    def _auto_written(self, unit):
        from .gradescope_sync import WAIT
        aid, row = unit["assignment_id"], f"{unit['section_id']}:{unit['enrollment_id']}"
        request_id = str(uuid.uuid5(NAMESPACE, f"auto-gradescope:{unit['id']}"))
        if self._auto_earlier(unit, "frq_gradescope_sends", request_id):
            return
        if not self.settings.status().get("configured"):
            return self._auto_settle(unit, "skipped", "Gradescope is not set up.")
        busy = self._auto_busy(aid, "frq_gradescope_sends", "Another send to Gradescope is running for this assignment.")
        if busy:
            return self._auto_wait(unit, busy, WAIT)
        faculty = self._faculty(aid, student=(unit["section_id"], unit["enrollment_id"]))
        view = self._send_view(aid, faculty, unit["papers_only"])
        if not view["configured"]:
            return self._auto_settle(unit, "skipped", "Gradescope is not set up.")
        if faculty["live"]:
            return self._auto_wait(unit, "Another send to Gradescope is running for this assignment.", WAIT)
        plan = self._compose(aid, faculty, {}, self._session_now(), view, papers_only=bool(unit["papers_only"]), student=row)
        section = next((s for s in view["sections"] if s["section_id"] == unit["section_id"]), None) or {}
        with self.rosters.lock(), connect(self.path) as db:
            creating = bool(self._creating(db, aid)) \
                or self._versions_coming(db, aid, set(section.get("missing_versions") or []))
        blocked = self._auto_section(unit, view, creating)
        if blocked:
            return (self._auto_wait(unit, blocked[1], WAIT) if blocked[0] == "wait" else self._auto_settle(unit, "skipped", blocked[1]))
        if view["skipped"]:
            return self._auto_settle(unit, "skipped", view["skipped"][0]["reason"])
        if not plan["uploads"] and not plan["grades"]:
            return self._auto_settle(unit, "skipped", "Already on Gradescope." if view["summary"].get("papers", {}).get("already") else "Nothing to send.")
        self._auto_record(unit, aid, row, request_id, plan, WAIT)

    def _auto_record(self, unit, aid, row, request_id, plan, wait, sheets=False):
        fingerprint = plan["view"]["fingerprint"]
        command = digest({"assignment_id": aid, "request_id": request_id, "fingerprint": fingerprint, "automatic": unit["id"], **({"kind": "sheets"} if sheets else {}),
                          **({"published": sorted(plan["published"])} if plan["published"] else {})})
        unpublished = plan["view"].get("unpublished_version")  # the student's version of a published set is not published (its card says so)
        automatic = {"certification_id": unit["certification_id"], "row": row, "id": unit["id"], **({"published": True} if plan["published"] else {}),
                     **({"unpublished_version": unpublished} if unpublished else {})}
        after = lambda db, send_id: self._auto_recorded(db, unit["id"], send_id)  # noqa: E731
        try:
            if sheets:
                self._record_sheet_send(aid, plan, request_id, command, fingerprint, actor="automatic", automatic=automatic, after=after)
            else:
                self._record_send(aid, plan, request_id, command, fingerprint, timestamp(), uid(), actor="automatic", automatic=automatic, after=after)
        except sqlite3.IntegrityError:
            self._auto_wait(unit, "Another send recorded some of this meanwhile.", wait)
        except Conflict as error:
            self._auto_wait(unit, str(error.description), wait)

    def _auto_sheets(self, unit):
        from .gradescope_sync import WAIT
        aid, row = unit["assignment_id"], f"{unit['section_id']}:{unit['enrollment_id']}"
        request_id = str(uuid.uuid5(NAMESPACE, f"auto-gradescope-sheet:{unit['id']}"))
        if self._auto_earlier(unit, "frq_gradescope_sheet_sends", request_id):
            return
        if not self.settings.status().get("configured"):
            return self._auto_settle(unit, "skipped", "Gradescope is not set up.")
        busy = self._auto_busy(aid, "frq_gradescope_sheet_sends", "Another send of bubble sheets is running for this assignment.")
        if busy:
            return self._auto_wait(unit, busy, WAIT)
        faculty = self._sheet_faculty(aid, student=(unit["section_id"], unit["enrollment_id"]))
        view = self._sheet_view(aid, faculty)
        if not view["configured"]:
            return self._auto_settle(unit, "skipped", "Gradescope is not set up.")
        if faculty["live"]:
            return self._auto_wait(unit, "Another send of bubble sheets is running for this assignment.", WAIT)
        plan = self._sheet_compose(faculty, {}, self._session_now(), view, student=row)
        blocked = self._auto_section(unit, view)
        if blocked:
            return self._auto_settle(unit, "skipped", blocked[1])
        if view["skipped"]:
            return self._auto_settle(unit, "skipped", view["skipped"][0]["reason"])
        if not plan["uploads"]:
            return self._auto_settle(unit, "skipped", "Already on Gradescope." if view["summary"].get("sheets", {}).get("already") else "Nothing to send.")
        self._auto_record(unit, aid, row, request_id, plan, WAIT, sheets=True)

    # --- a makeup's version on Gradescope (GRADESCOPE "A VERSION THE SET LACKS", 2026-10-09) ------------------------------------
    def _makeup_next(self, db, ids):
        """At most once a minute: the first approved makeup version that is settled up to grading (`makeup_settled`) and that Faculty has
        neither added on Gradescope nor said why it could not, for a plan some section has already linked on Gradescope. Catalog only."""
        now = time.monotonic()
        if now - getattr(self, "_makeup_looked", -MAKEUP_LOOK) < MAKEUP_LOOK or not self.settings.status().get("configured"):
            return None
        self._makeup_looked = now
        for aid, letter in self.frq.approved_makeup_versions(db):
            key = f"{aid}:{letter}"
            if key in ids or db.execute("SELECT 1 FROM frq_gradescope_creations WHERE request_id=?", (self._version_request(aid, letter),)).fetchone():
                continue
            if db.execute("""SELECT 1 FROM frq_events WHERE assignment_id=? AND kind='gradescope_version_not_added' AND json_extract(detail_json,'$.letter')=?
                    LIMIT 1""", (aid, letter)).fetchone():
                continue  # one automatic attempt per version: the instructor's Create in the Gradescope panel does the rest
            if not any(t["container_id"] for t in self._targets(db, self.frq.plan(db, aid))):
                continue  # never sent to Gradescope from Faculty: there is no set to add it to
            if self.frq.makeup_settled(db, aid, letter):
                return {"kind": "makeup_versions", "id": key, "assignment_id": aid, "letter": letter}
        return None

    @staticmethod
    def _version_request(aid, letter):
        return str(uuid.uuid5(NAMESPACE, f"auto-version:{aid}:{letter}"))

    def _version_not_added(self, aid, letter, reasons):
        with self.rosters.lock(), connect(self.path) as db:
            self.frq.event(db, aid, "gradescope_version_not_added", {"letter": letter, "reasons": reasons[:20]}, "automatic")

    def _auto_version(self, unit):
        """Adds the settled makeup version to every section's linked Gradescope set that lacks it (the instructor's Create, made by
        itself: `_creation_plan` with `adding`, recorded with actor `automatic`). Never raises."""
        from .gradescope_sync import PAUSE
        aid, letter = unit["assignment_id"], unit["letter"]
        try:
            planned = self._creation_plan(aid, {}, makeup=letter, session=self._session_now())
        except gw.SignInRefused:
            self._paused = (time.monotonic() + PAUSE, self._sign_in_stamp())
            self._drop_session()
            return
        except gw.GradescopeBusy as error:
            self._pause(error)
            return
        except gw.GradescopeError:
            return  # read again on a later look
        except (Conflict, ValueError) as error:
            return self._version_not_added(aid, letter, [str(getattr(error, "description", error))])
        except Exception:
            LOG.exception("Adding a makeup version on Gradescope stopped unexpectedly")
            return
        view = planned["view"]
        if view["running"]:
            return  # something of this plan is being made on Gradescope: looked at again later
        from .gradescope_publish import PUBLISHING
        if PUBLISHING in view["problems"]:
            return  # a publication takes seconds: looked at again later, never recorded as a version not added
        skipped = [{"section": s["section"], "reason": s["reason"]} for s in view["sections"] if s["reason"]]
        if view["problems"] or not planned["units"]:
            return self._version_not_added(aid, letter, view["problems"] or [f"Section {s['section']}: {s['reason']}" for s in skipped] or ["Nothing to add."])
        request_id = self._version_request(aid, letter)
        command = digest({"assignment_id": aid, "request_id": request_id, "fingerprint": view["fingerprint"], "automatic": {"letter": letter}})
        try:
            self._record_creation(aid, planned, request_id, command, view["fingerprint"], actor="automatic", automatic={"letter": letter, "skipped": skipped})
        except Conflict:
            return  # another creation was recorded meanwhile: looked at again later

    def automatic_sends(self, db, aid):
        """The plan's automatic sends for the panels: {row: [{kind, status, error, send_id, created_at}]}, newest first (row keys only)."""
        found = {}
        for r in db.execute("""SELECT section_id,enrollment_id,kind,status,error,send_id,created_at FROM frq_gradescope_automatic_sends WHERE assignment_id=?
                ORDER BY created_at DESC,rowid DESC""", (aid,)):
            found.setdefault(f"{r['section_id']}:{r['enrollment_id']}", []).append({k: r[k] for k in ("kind", "status", "error", "send_id", "created_at")})
        return found

    @staticmethod
    def automatic_note(rows, uncovered=True):
        """What a student's Gradescope cell says about Faculty's own send of their late paper (newest row per kind), or None:
        {kind, status: waiting|skipped, reason}. A wait is said while the row waits; a skip only while the student is still not on
        Gradescope and only when it leaves the instructor something to do (never "Already on Gradescope.")."""
        newest = {}
        for r in rows or []:
            newest.setdefault(r["kind"], r)
        waiting = [r for r in newest.values() if r["status"] in ("pending", "sending")]
        if waiting:
            r = waiting[0]
            return {"kind": r["kind"], "status": "waiting", "reason": r["error"] or ""}
        skipped = [r for r in newest.values() if r["status"] == "skipped" and r["error"] not in QUIET_SKIPS]
        if skipped and uncovered:
            r = skipped[0]
            return {"kind": r["kind"], "status": "skipped", "reason": r["error"] or ""}
        return None

    @staticmethod
    def unpublished_versions(db, aid):
        """The letters of versions a late paper Faculty sent by itself went to while its set was published but the version was not
        (`plan_json.automatic.unpublished_version`), with no publication of the plan finished since: Certification and Export asks the
        instructor to Publish grades (their own; Faculty never publishes by itself). Catalog only."""
        found = set()
        for r in db.execute("""SELECT json_extract(plan_json,'$.automatic.unpublished_version'),created_at FROM frq_gradescope_sends WHERE assignment_id=? AND actor='automatic'
                AND status!='cancelled' AND json_extract(plan_json,'$.automatic.unpublished_version') IS NOT NULL""", (aid,)):
            if not db.execute("SELECT 1 FROM frq_gradescope_publications WHERE assignment_id=? AND status='complete' AND created_at>=? LIMIT 1", (aid, r[1])).fetchone():
                found.add(r[0])
        return sorted(found)

    def automatic_status(self, db, aid):
        """The Gradescope panel's read of what Faculty did not do by itself (GRADESCOPE "AUTOMATIC LATE-PAPER SEND" and "A VERSION THE
        SET LACKS"; 2026-10-09 review: both were recorded and never shown), only the keys that have something: `sends`, each waiting
        or skipped row (row keys only) whose student has no accepted paper of that kind since, and `versions_not_added`, the newest
        reason per makeup letter Faculty could not add and no creation of the plan has made since (a reason that only says the set has
        the version already is left out). Catalog only; nothing is retried."""
        sends = []
        for row, rows in self.automatic_sends(db, aid).items():
            section, _, enrollment = row.partition(":")
            for r in rows:
                if r["status"] not in ("pending", "sending", "skipped") or (r["status"] == "skipped" and r["error"] in QUIET_SKIPS):
                    continue
                table = "frq_gradescope_sheet_uploads" if r["kind"] == "sheets" else "frq_gradescope_uploads"
                if r["status"] == "skipped" and db.execute(f"""SELECT 1 FROM {table} WHERE assignment_id=? AND section_id=? AND enrollment_id=? AND status='accepted'
                        AND created_at>=? LIMIT 1""", (aid, section, enrollment, r["created_at"])).fetchone():
                    continue
                sends.append({"row": row, "kind": r["kind"], "status": "waiting" if r["status"] != "skipped" else "skipped", "reason": r["error"] or ""})
                break
        versions, made = {}, db.execute("SELECT MAX(created_at) FROM frq_gradescope_creations WHERE assignment_id=? AND status='complete'", (aid,)).fetchone()[0] or ""
        for r in db.execute("""SELECT detail_json,created_at FROM frq_events WHERE assignment_id=? AND kind='gradescope_version_not_added' ORDER BY id DESC""", (aid,)):
            detail = json.loads(r["detail_json"])
            letter = detail.get("letter")
            if not letter or letter in versions or r["created_at"] < made:
                continue
            reasons = [x for x in detail.get("reasons") or [] if "already has Version" not in x]
            versions[letter] = {"letter": letter, "reasons": reasons, "at": r["created_at"]} if reasons else None
        found = {"sends": sends, "versions_not_added": [v for _, v in sorted(versions.items()) if v]}
        return {k: v for k, v in found.items() if v}
