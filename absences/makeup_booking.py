"""Quiz makeups booked by card (ABSENCES "MAKEUP BOOKING"; requirement, 2026-10-04)
MAKEUP HOLDS (requirement, 2026-10-06): an absence's quiz makeups can be
held until the instructor releases them (a check-back day flags it), one hold for every makeup the absence owes; while held no card
goes out, an unanswered one is withdrawn and no time is offered; a release (or a card sent by hand) lets the next card go out. The
follow-up assistant may place a hold by itself, shown with Undo (assistant/followups.py DONE FOR YOU), but never for a known return
date (A KNOWN RETURN, 2026-11-09: the card's times already start from it, so its card goes out at excusal; `known_return`).

Everything runs as one bounded worker turn (`process_one`, teach_inbox work_once), each step through the owners' own services:
1. a submitted booking card → the student seated in the makeup sitting of the time they picked, found or made through the Exams page's
   own path (`ExamService.save_sitting` / `update_participant`; their expectation and grades stay), or their request for another
   time kept for the instructor; once per submission (`absence_makeup_picks`). A request for another time comes back to the
   instructor as a follow-up of the student's (BACK TO FOLLOW-UP, 2026-11-12: "it should actually land back in followup for me to
   review, if not, it will just sit in the absence page unnoticed"), once per answer, and Faculty resolves it once the request
   is answered outside it (a makeup sitting, a new card, the makeup no longer owed); once the instructor took that follow-up up
   (saved Waiting on student, To do or Follow up later) its Makeups row reads "In Follow-up" and is not flagged (TAKEN UP IN
   FOLLOW-UP, 2026-11-12: "I thought I handled this one in followup already");
   A delivered card nobody answered, whose times have passed, waits one more business day for the student to write; with no word
   from them its makeup is recorded missed, a 0 (NO ANSWER, THEN A 0, 2026-11-12: "wait one more day after that status for the
   student to reach out, if the student doesn't reach out then the assignment gets recorded as a 0 and then makeup card revoked
   and cleared from the makeup owed page"), through the Makeups view's own "Missed makeup, record 0" (`makeup_missed`); never over
   a score above 0 the student holds for the quiz in the gradebook, and taken back by Faculty once one turns up after it (A SCORE
   STANDS, 2026-11-14: "we need to make sure that 0 doesn't overwrite a positive score for that assignment");
2. an unanswered card whose makeup is no longer owed (paper received, decision changed, student dropped or placed on a makeup sitting
   by hand) or that a newer card replaced → revoked (`ResponseCardService.control`);
3. a quiz that owes a makeup and has neither a makeup version nor a makeup draft → one makeup draft (`create_makeup_draft`, request
   ID `auto-makeup:<plan>`, so a draft the instructor scrapped is never drafted again by itself); an approved makeup version whose
   rubric review could not be requested at approval (another was running) → that review, once nothing runs;
4. an owed makeup of a term whose cards are on, with no card yet → an offer (the times, `absence_makeup_offers`), then its card
   issued, published and emailed; an offer whose card is not out yet is carried on the same way (retries after a failure);
5. a student seated by their pick → at once, one print job of the newest approved makeup version, its copy printed for them with
   their name filled in on the paper (`DocumentService.create_batch` `fill_names`, 2026-10-18; whom it was made for also kept in
   `absence_makeup_copies`), attached to the sitting; while no
   makeup version is approved it waits, and
   is made as soon as one is (requirement, 2026-10-12);
6. that print job, once ready, → sent to the home printer (DOCUMENTS "SEND TO PRINTER" › AUTOMATIC MAKEUP PRINTS; instructor,
   2026-10-18: "I want the app to automatically print booked makeups"), once; a send the printer refused or never answered is
   tried again until the sitting's day ends, an unconfirmed one never.

Quizzes only: an exam record with a seating chart is an exam (EXAMS), and exams stay manual. Nothing here grades, excuses, certifies or
writes the gradebook (ABSENCES NO-GRADEBOOK). Lock order as everywhere: the lifecycle lock, then one `BEGIN IMMEDIATE`."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import time
import uuid
from decimal import Decimal
from scheduling.clock import CAMPUS_ZONE, business_days_after

from werkzeug.exceptions import Conflict, HTTPException, NotFound

from course_storage import timestamp
from rosters import connect
from scheduling.feed import google_gap
from scheduling.service import availability

from . import makeup_slots as slots
from .makeup_length import student_minutes
from .makeups import RECEIVED
from .service import _has, return_date, return_dates

LOG = logging.getLogger(__name__)
TIME_ZONE = CAMPUS_ZONE
TEMPLATE_NAME = "Quiz makeup booking (Faculty)"
TEMPLATE = {
    "title": "Book your {{assignment.name}} makeup",
    "description": ("You missed {{assignment.name}} on {{makeup.given}} with an excused absence. Choose when you will take the makeup. "
                    "It must be done by {{makeup.due}}."),
    "confirmation_message": "Thank you. Your choice is saved, and you will get an email confirming it.",
    "fields": [
        {"id": slots.FIELD, "type": "radio", "label": "When will you take the makeup?", "required": True, "options": [slots.OTHER]},
        {"id": slots.OTHER_FIELD, "type": "long_text", "label": "When could you come instead? Your instructor will reply.", "required": True,
         "show_if": {"field": slots.FIELD, "equals": slots.OTHER}},
    ],
}
AUTO_DRAFT = "auto-makeup:"  # request ID of a makeup draft started by the worker
COPIES = "makeup-copies:"  # request IDs of the copies printed for a sitting's booked students
NO_ANSWER_DAYS = 3  # business days after a card before an unanswered one is flagged
IDLE = 30  # seconds between the worker's looks when it found nothing to do
EXTEND = 21  # calendar days "Send a new card" looks ahead for times once none is left before the due date (A MISSED MAKEUP)
RETRY = (60, 300, 1800)  # seconds before trying one failing item again (the last repeats)
PRINT_RETRY = 900  # seconds after a refused or unanswered send before a makeup copy is sent to the printer again
ZERO_WAIT = 1  # business days after an unanswered card's last day that the student still has to write before the 0 (NO ANSWER, THEN A 0)
NO_ANSWER = "makeup-no-answer:"  # request IDs of the makeups recorded missed for an unanswered card, one per card
SCORED = "makeup-no-answer-scored:"  # request IDs of Faculty taking back such a 0 once a score above 0 turned up (A SCORE STANDS)
ASKED = "makeup-other-time:"  # request IDs of the follow-ups opened for a card answered "I need another time" (BACK TO FOLLOW-UP)
# TAKEN UP IN FOLLOW-UP: the saved statuses of such a follow-up that carry the request, in the Follow-up page's own words
# (static/student-support.js SUPPORT_STATES), as the Makeups row's line.
TAKEN = {"waiting": "Waiting on student since {day}", "todo": "To do since {day}", "snoozed": "Follow up later, set {day}"}


def campus_now():
    """The campus wall-clock time now (a naive datetime, compared with campus dates and times as written)."""
    return dt.datetime.now(TIME_ZONE).replace(tzinfo=None)


def campus_day(stamp):
    """The campus date of a stored UTC timestamp."""
    return dt.datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone(TIME_ZONE).date()


def sitting_label(option):
    day = dt.date.fromisoformat(option["date"])
    start, end = slots.clock(option["time"]), slots.clock(option["end_time"])
    when = slots.span(start, end) if option["kind"] == "office_hours" else slots.twelve(start)
    return f"Makeup · {slots.day_label(day)} · {when}"


def _key(row):
    return (row["event_id"], row["section_id"], row["enrollment_id"], row["absence_id"])


def known_return(db, absence_id, reason=""):
    """MAKEUP HOLDS › A KNOWN RETURN: the day the student can return (YYYY-MM-DD) as the absence's latest details name it, else as a
    proposed hold's `reason` names it (FROM THEIR RETURN, `return_date`), or ''. The assistant never holds such an absence."""
    row = db.execute("SELECT details, last_date FROM absence_revisions WHERE absence_id=? ORDER BY revision DESC LIMIT 1",
                     (str(absence_id),)).fetchone()
    if not row:
        return ""
    return return_date(row["details"], row["last_date"]) or return_date(reason, row["last_date"])



def _days(offered):
    """How many days an offer's times fall on (NEXT TWO DAYS: a card offers `slots.DAYS` of them when it can)."""
    return len({option["date"] for option in offered["options"]})


class MakeupBooking:
    def __init__(self, absences, exams, grading, cards, calendar=None, google=None):
        self.absences, self.exams, self.grading, self.cards = absences, exams, grading, cards
        self.rosters, self.path = absences.rosters, absences.path
        self.calendar, self.google = calendar, google
        self.followups = None  # FollowupSuggestions: BACK TO FOLLOW-UP; set by the app
        self._asked_settled = set()  # request IDs of the follow-ups (ASKED) Faculty has nothing more to do with; process memory
        self._idle_until = 0.0
        self._failures = {}  # item key → (failures, monotonic time it may be tried again); process memory
        self.problems = {}  # plan ID → why its makeup could not be drafted automatically; process memory, shown on the Makeups view

    # -- Settings --------------------------------------------------------------------------------------------------------------
    @staticmethod
    def _settings(db, term_ids=None):
        """{term ID: {cards, revision}} of the terms named (every term with a setting when None); a term never set is off."""
        if not _has(db, "absence_makeup_settings"):
            return {}
        found = {row["term_id"]: {"cards": bool(row["cards"]), "revision": row["revision"]} for row in db.execute(
            """SELECT * FROM absence_makeup_settings s WHERE revision=(SELECT MAX(revision) FROM absence_makeup_settings x
               WHERE x.term_id=s.term_id)""")}
        if term_ids is None:
            return found
        return {term: found.get(term, {"cards": False, "revision": 0}) for term in term_ids}

    def set_cards(self, term_id, data, actor="workspace"):
        """Turn a term's automatic booking cards on or off (`cards`), against the revision the caller read (`revision`)."""
        request_id = data.get("request_id")
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 100:
            raise ValueError("Send a request ID.")
        if not isinstance(data.get("cards"), bool):
            raise ValueError("Say whether booking cards go out automatically.")
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            if not db.execute("SELECT 1 FROM course_terms WHERE id=? AND deleted_at IS NULL", (term_id,)).fetchone():
                raise NotFound("Term not found.")
            prior = db.execute("SELECT * FROM absence_makeup_settings WHERE request_id=?", (request_id,)).fetchone()
            if prior:
                if prior["term_id"] != term_id or bool(prior["cards"]) != data["cards"]:
                    raise Conflict("This request ID already belongs to another change.")
                return self._settings(db, [term_id])[term_id]
            current = self._settings(db, [term_id])[term_id]
            if data.get("revision") != current["revision"]:
                raise Conflict("The booking setting changed. Reload the Makeups view.")
            if current["cards"] != data["cards"]:
                db.execute("INSERT INTO absence_makeup_settings VALUES(?,?,?,?,?,?)",
                           (term_id, current["revision"] + 1, int(data["cards"]), request_id, actor, timestamp()))
            settings = self._settings(db, [term_id])[term_id]
        self._idle_until = 0.0
        return settings

    # -- Reading ---------------------------------------------------------------------------------------------------------------
    @staticmethod
    def quizzes(db):
        """The exam records that are quizzes: none of their term's seating charts is for their assignment (exams are seated)."""
        if not _has(db, "exam_events"):
            return set()
        charts = "AND NOT EXISTS(SELECT 1 FROM seating_charts c WHERE c.term_id=e.term_id AND c.assignment_id=e.assignment_id)" \
            if _has(db, "seating_charts") else ""
        return {row[0] for row in db.execute(f"SELECT e.id FROM exam_events e WHERE 1=1 {charts}")}

    @staticmethod
    def _offers(db):
        """{(event, section, enrollment, absence): [offers, oldest first]}."""
        found = {}
        if not _has(db, "absence_makeup_offers"):
            return found
        for row in db.execute("SELECT * FROM absence_makeup_offers ORDER BY attempt"):
            offer = dict(row)
            offer["options"] = json.loads(offer.pop("options_json"))
            found.setdefault(_key(offer), []).append(offer)
        return found

    @staticmethod
    def _cards(db):
        """{offer ID: its card} (each offer has one card, for one student), with the card's state."""
        if not _has(db, "response_card_issues", "response_card_recipients"):
            return {}
        return {row["offer_id"]: dict(row) for row in db.execute(
            """SELECT json_extract(i.context_json,'$.makeup_offer_id') offer_id,i.id issue_id,r.id card_id,r.status,r.publish_status,
                      r.delivery_status,r.current_submission_id,r.error,r.created_at,i.expires_at,
                      EXISTS(SELECT 1 FROM response_card_invalidations x WHERE x.card_id=r.id) invalidated,
                      EXISTS(SELECT 1 FROM response_card_mail m WHERE m.card_id=r.id AND m.kind='invitation') invited
               FROM response_card_issues i JOIN response_card_recipients r ON r.issue_id=i.id
               WHERE json_extract(i.context_json,'$.makeup_offer_id') IS NOT NULL""")}

    @staticmethod
    def _picks(db):
        """{offer ID: its latest pick}."""
        if not _has(db, "absence_makeup_picks"):
            return {}
        return {row["offer_id"]: dict(row) for row in db.execute("SELECT * FROM absence_makeup_picks ORDER BY created_at, rowid")}

    @staticmethod
    def _holds(db):
        """{absence ID: its latest hold revision} of every absence ever held (MAKEUP HOLDS); a released one has `held` 0."""
        if not _has(db, "absence_makeup_holds"):
            return {}
        return {row["absence_id"]: dict(row) for row in db.execute(
            """SELECT * FROM absence_makeup_holds h WHERE revision=(SELECT MAX(revision) FROM absence_makeup_holds x
               WHERE x.absence_id=h.absence_id)""")}

    @staticmethod
    def _held(holds, row):
        """The hold standing over this row's absence, or None."""
        hold = holds.get(row["absence_id"])
        return hold if hold and hold["held"] else None

    @staticmethod
    def _hold_view(hold):
        """An absence's hold as the API shows it ({held: false, revision: 0} when never held)."""
        if not hold:
            return {"held": False, "revision": 0}
        view = {"held": bool(hold["held"]), "revision": hold["revision"], "actor": hold["actor"], "at": hold["created_at"]}
        if hold["held"]:
            view.update(reason=hold["reason"], check_back=hold["check_back"],
                        check_back_day=slots.day_label(dt.date.fromisoformat(hold["check_back"])) if hold["check_back"] else None)
        return view

    def _reoffer(self, offer, card, hold):
        """MAKEUP HOLDS: True when the latest card was withdrawn, or never went out before its times passed, and the instructor
        released a hold after it was made: a new card then goes out the way a first one does, so a hold never leaves a makeup
        without one."""
        if not hold or hold["held"] or hold["created_at"] < offer["created_at"] or (card and card["current_submission_id"]):
            return False
        if card and (card["status"] == "revoked" or card["invalidated"]):
            return True
        return (not card or card["publish_status"] != "published" or not card["invited"]) and self._passed(offer)

    def _owed(self, db):
        from .makeups import owed
        return owed(db, self.exams)

    @staticmethod
    def _current(offers):
        return offers[-1] if offers else None

    def _plan_versions(self, db, plan_ids):
        """{plan: {state, letter, draft_id, automatic, problem}}: where each quiz's makeup version stands (GRADING "MAKEUP VERSIONS")."""
        found = {}
        if self.grading is None or not _has(db, "quiz_drafts"):
            return found
        for plan in plan_ids:
            rows = self.grading._makeup_rows(db, plan)
            summaries = [self.grading._quiz_draft_summary(db, row) for row in rows]
            approved = [s for s in summaries if s["state"] == "approved"]
            open_ = next((s for s in summaries if s["state"] in ("drafting", "ready", "approving", "failed")), None)
            chosen = approved[0] if approved else open_
            state = chosen["state"] if chosen else "none"
            found[plan] = {"state": state, "letter": chosen["makeup"]["letter"] if chosen else None, "draft_id": chosen["id"] if chosen else None,
                           "automatic": any(row["request_id"].startswith(AUTO_DRAFT) for row in rows),
                           "scrapped": any(s["state"] == "scrapped" for s in summaries) and not chosen,
                           "problem": self.problems.get(plan, "") if state == "none" else ""}
        return found

    def _rubrics(self, db, plan, letter, status=None):
        """'final' when every question of the makeup version stands under a confirmed rubric finalization, 'reviewing' while a rubric
        review runs, else 'to_finalize' (finalizing stays the instructor's, department policy)."""
        try:
            status = status or self.grading.rubric_quality_status(db, plan, details=True)
        except (NotFound, ValueError):
            return "to_finalize"
        questions = [q for q in status["context"]["questions"] if q["exam_version"] == letter]
        standing = status.get("standing") or {}
        if questions and all((standing.get(q["question_id"]) or {}).get("rubric_id") == q["rubric_id"]
                             and not standing[q["question_id"]].get("open") for q in questions):
            return "final"
        if status.get("check") and status["check"]["status"] in ("pending", "running"):
            return "reviewing"
        return "to_finalize"

    def annotate(self, db, rows, *, details=True):
        """Each owed row's booking (`row["booking"]`: None for an exam, an undecided absence, or a row of a term without cards and no
        card), and the versions of the quizzes listed. Catalog reads only. Returns {plan: version}."""
        quizzes = self.quizzes(db)
        settings = self._settings(db, sorted({row["term_id"] for row in rows}))
        offers, cards, picks, holds = self._offers(db), self._cards(db), self._picks(db), self._holds(db)
        returns = return_dates(db, [row["absence_id"] for row in rows if row["event_id"] in quizzes and row["state"] == "owed"])
        now = campus_now()
        today = now.date()
        plans = sorted({row["plan_id"] for row in rows if row["plan_id"] and row["event_id"] in quizzes and row["state"] == "owed"})
        versions = self._plan_versions(db, plans)
        if details:
            for plan in plans:
                version = versions[plan]
                if version["state"] == "approved":
                    version["rubrics"] = self._rubrics(db, plan, version["letter"])
        copies = self._copied(db)
        unchecked = []  # whether Google can vouch for a card's times, read once and only when a card is about to go out
        taken = None  # the follow-ups of "I need another time" answers the instructor took up, read once and only for such a row
        for row in rows:
            row["booking"] = None
            if row["event_id"] not in quizzes:
                continue
            row["hold"] = self._hold_view(holds.get(row["absence_id"]))  # MAKEUP HOLDS: an undecided absence may be held too
            if row["state"] != "owed":
                continue
            history = offers.get(_key(row), [])
            offer = self._current(history)
            on = settings.get(row["term_id"], {}).get("cards", False)
            hold = holds.get(row["absence_id"])
            due = self._due(row, history, returns.get(row["absence_id"]))
            booking = {"cards": on, "due": due, "due_day": slots.day_label(dt.date.fromisoformat(due)),
                       "attempts": len(history), "flag": False}
            if offer:
                card, pick = cards.get(offer["id"]), picks.get(offer["id"])
                booking["offer"] = {"id": offer["id"], "attempt": offer["attempt"], "week": offer["week"], "options": [o["text"] for o in offer["options"]],
                                    "created_at": offer["created_at"]}
                booking["card"] = {k: card[k] for k in ("card_id", "issue_id", "status", "publish_status", "delivery_status", "error")} if card else None
                if pick:
                    booking["pick"] = {"outcome": pick["outcome"], "choice": pick["choice"], "detail": pick["detail"], "created_at": pick["created_at"]}
            waiting = None
            renew = bool(offer) and self._reoffer(offer, cards.get(offer["id"]), hold)
            if on and (not offer or renew) and not row.get("scheduled") and not (hold and hold["held"]):
                if not unchecked:  # a card about to go out says when it waits (SCHEDULING "GOOGLE CHECKED")
                    unchecked.append(google_gap(self.google))
                waiting = unchecked[0]
            state, label = self._state(row, None if renew else offer, None if renew else offer and cards.get(offer["id"]),
                                       None if renew else offer and picks.get(offer["id"]), on, today, waiting, hold)
            if state == "no_answer" and not renew:
                zero = self._zero(db, offer, cards.get(offer["id"]), today)  # NO ANSWER, THEN A 0: when, or what holds it back
                label += f" · {zero['label']}" if zero else ""
            elif state == "expired" and not renew and _has(db, "absence_makeups_missed") and db.execute(
                    "SELECT 1 FROM absence_makeups_missed WHERE request_id=?", (SCORED + offer["id"],)).fetchone():
                score = self._score(db, offer["event_id"], offer["enrollment_id"])  # A SCORE STANDS: why the row came back
                label += f" · its 0 was taken back: they have {score} for it in the gradebook" if score else " · its 0 was taken back"
            followup = None
            if state == "other_time":  # TAKEN UP IN FOLLOW-UP: the row follows the follow-up the answer opened
                taken = self._asked_taken(db) if taken is None else taken
                followup = taken.get(self._asked_request(picks[offer["id"]]["submission_id"]))
                if followup:
                    booking["followup"] = followup
            booking.update(state=state, label=label, flag=state in FLAGS and not followup)
            if state == "booked" and row.get("scheduled"):
                copy = copies.get((row["scheduled"]["sitting_id"], row["enrollment_id"]))
                booking["copy"] = copy
            row["booking"] = booking
        return versions

    def _state(self, row, offer, card, pick, on, today, waiting=None, hold=None):
        """(state, label) of one owed quiz makeup."""
        scheduled = row.get("scheduled")
        if pick and pick["outcome"] == "seated":
            if not scheduled:
                return "unseated", "Picked a time but is on no makeup sitting now"
            return self._seated(scheduled, today, dated=False)
        if hold and hold["held"] and not scheduled:
            # MAKEUP HOLDS: no card and no time until the instructor releases it; flagged from its check-back day.
            reason = hold["reason"] or "Until you release it"
            back = dt.date.fromisoformat(hold["check_back"]) if hold["check_back"] else None
            if back and back <= today:
                return "check_back", f"Check back {'today' if back == today else 'since ' + slots.day_label(back)}: {reason}"
            return "held", reason + (f" · check back {slots.day_label(back)}" if back else "")
        # A student who asked for another time (or whose pick could not be booked) and whom you then seated by hand is settled: the
        # row reads as any makeup sitting set by hand (requirement, 2026-10-16, of a student seated at the time he asked for).
        if pick and not scheduled:
            if pick["outcome"] == "other_time":
                return "other_time", "Needs another time"
            return "stale", "Could not book the time picked"
        if scheduled:  # BOOKED BY HAND (requirement, 2026-10-16): a dated sitting set by hand reads as a booked pick
            return self._seated(scheduled, today, dated=True)
        if offer:
            if card and (card["status"] == "revoked" or card["invalidated"]):
                return "expired", "Card withdrawn" + ("" if card["invited"] else " before it went out")
            if (not card or card["publish_status"] != "published" or not card["invited"]) and self._passed(offer):
                return "no_answer", "The card's times passed before it went out"
            if not card or card["publish_status"] != "published" or not card["invited"]:
                if card and card["delivery_status"] == "no_email":
                    return "no_email", "No email address for the card"
                if not self._gmail_ready():
                    return "sending", "Card waits for Gmail: reconnect Gmail in Response Cards"
                return "sending", "Card going out" + (f" (retrying: {card['error']})" if card and card.get("error") else "")
            if card["delivery_status"] in ("failed", "unconfirmed"):
                return "no_email", "Card email not delivered"
            sent = campus_day(offer["created_at"])
            if self._passed(offer):
                return "no_answer", f"No answer; the card's times passed (sent {slots.day_label(sent)})"
            if today > business_days_after(sent, NO_ANSWER_DAYS):
                return "no_answer", f"No answer since {slots.day_label(sent)}"
            return "sent", f"Card sent {slots.day_label(sent)}"
        if on and waiting:  # SCHEDULING "GOOGLE CHECKED": goes out by itself once Google is read again
            return "waiting", waiting
        if on:
            return "queued", "Card goes out shortly"
        return "manual", ""

    def _zero(self, db, offer, card, today):
        """NO ANSWER, THEN A 0 (requirement, 2026-11-12): where an unanswered card stands on its way to a 0, {due, label}, or None
        when the rule leaves it alone: a card that never reached the student (not out before its times passed, not delivered), one
        whose times have not all passed (it can still be answered), or one whose 0 Faculty already recorded once (the instructor's
        Undo stands). `due` once the student has had `ZERO_WAIT` business day after the card's last day, has sent no message since
        the card went out, and Gmail and Canvas have both been read since that day ended. `offer` is the row's current one, read
        as "No answer" by `_state`."""
        if (not card or card["publish_status"] != "published" or not card["invited"] or card["delivery_status"] in ("failed", "unconfirmed")
                or card["status"] == "revoked" or card["invalidated"] or card["current_submission_id"] or not self._passed(offer)):
            return None
        if _has(db, "absence_makeups_missed") and db.execute("SELECT 1 FROM absence_makeups_missed WHERE request_id=?",
                                                             (NO_ANSWER + offer["id"],)).fetchone():
            return None
        score = self._score(db, offer["event_id"], offer["enrollment_id"])
        if score:  # A SCORE STANDS (2026-11-14): a 0 is never recorded over a score they have
            return {"due": False, "label": f"they have {score} for it in the gradebook, so no 0 is recorded"}
        if self._wrote(db, offer):
            return {"due": False, "label": "they wrote to you since the card, so no 0 is recorded by itself"}
        after = business_days_after(dt.date.fromisoformat(max(option["date"] for option in offer["options"])), ZERO_WAIT)
        if today <= after:
            return {"due": False, "label": f"a 0 is recorded after {slots.day_label(after)} unless they write to you"}
        if not self._mail_read(db, after):
            return {"due": False, "label": "a 0 is recorded once Gmail and Canvas have been read"}
        return {"due": True, "label": "a 0 is being recorded"}

    @staticmethod
    def _wrote(db, offer):
        """True when the communications record holds a message the student sent (Gmail or Canvas) since this card's offer was made."""
        if not _has(db, "comm_links", "comm_revisions"):
            return False
        since = dt.datetime.fromisoformat(offer["created_at"].replace("Z", "+00:00"))
        for row in db.execute("""SELECT r.sent_at FROM comm_links l JOIN comm_messages m ON m.id=l.message_id AND m.channel!='gradescope_regrade'
                JOIN comm_revisions r ON r.message_id=m.id AND r.revision=(SELECT MAX(revision) FROM comm_revisions WHERE message_id=m.id)
                WHERE l.enrollment_id=? AND l.role='author' AND r.author_role='student'""", (offer["enrollment_id"],)):
            try:
                sent = dt.datetime.fromisoformat(row["sent_at"].replace("Z", "+00:00"))
            except ValueError:
                continue
            if (sent if sent.tzinfo else sent.replace(tzinfo=dt.timezone.utc)) >= since:
                return True
        return False

    @staticmethod
    def _score(db, event_id, enrollment_id, memo=None):
        """A SCORE STANDS (requirement, 2026-11-14): the highest score above 0 the student holds
        for the exam record's assignment in the term's gradebook (a cell's latest revision, in any section of theirs, of a column of
        that assignment that is not archived; `totals._answered`), as written there, else None. `memo` keeps one read's columns per
        exam record. A read; nothing is written."""
        from gradebook.service import _setup
        from .totals import _answered
        if not _has(db, "gradebook_scores", "gradebook_setups"):
            return None
        memo = {} if memo is None else memo
        if event_id not in memo:
            event = db.execute("SELECT term_id, assignment_id FROM exam_events WHERE id=?", (event_id,)).fetchone()
            items = []
            if event and event["assignment_id"]:
                setup, _ = _setup(db, event["term_id"])
                items = [item["id"] for item in setup.get("items", []) if item.get("assignment_id") == event["assignment_id"] and not item.get("archived")]
            memo[event_id] = (event["term_id"] if event else None, items)
        term_id, items, best = *memo[event_id], None
        for item_id in items:
            for row in db.execute("""SELECT g.status, g.points FROM gradebook_scores g WHERE g.term_id=? AND g.item_id=? AND g.enrollment_id=?
                    AND g.seq=(SELECT MAX(x.seq) FROM gradebook_scores x WHERE x.term_id=g.term_id AND x.item_id=g.item_id
                    AND x.section_id=g.section_id AND x.enrollment_id=g.enrollment_id)""", (term_id, item_id, enrollment_id)):
                if _answered(row) and (best is None or Decimal(str(row["points"])) > Decimal(str(best))):
                    best = row["points"]
        return best

    @staticmethod
    def _mail_read(db, after):
        """True when Gmail and Canvas were each read (a successful capture round) since campus day `after` ended, so a message the
        student sent by then is in the record."""
        if not _has(db, "comm_capture_state"):
            return False
        ended = dt.datetime.combine(after + dt.timedelta(days=1), dt.time.min, tzinfo=TIME_ZONE)
        read = {row["channel"]: row["last_success_at"] for row in db.execute("SELECT channel, last_success_at FROM comm_capture_state")}
        try:
            return all(read.get(channel) and dt.datetime.fromisoformat(read[channel].replace("Z", "+00:00")) >= ended
                       for channel in ("gmail", "canvas"))
        except (ValueError, TypeError):
            return False

    @staticmethod
    def _seated(scheduled, today, *, dated):
        """(state, label) of a student on a makeup sitting: Booked until its day passes, then Paper not in. A sitting set by hand
        with no date (`dated`) stays "scheduled", which reads as before."""
        day = dt.date.fromisoformat(scheduled["date"]) if scheduled.get("date") else None
        if dated and day is None:
            return "scheduled", ""
        if day and day < today:
            return "paper_not_in", f"No paper yet from the makeup on {scheduled['day']}"
        clock = slots.clock(scheduled.get("time"))
        when = " · ".join(filter(None, [scheduled.get("day"), slots.twelve(clock) if clock is not None else scheduled.get("time"),
                                        scheduled.get("location")])) or scheduled["label"]
        return "booked", f"Booked: {when}"

    def _gmail_ready(self):
        """Whether booking cards can be emailed now (they go only through the instructor's Gmail, GMAIL above)."""
        try:
            return bool(self.cards.mail.gmail.connected)
        except Exception:  # an unreadable connection is not ready; the row says so instead of failing the read
            return False

    # The follow-up says so (requirement, 2026-11-03): ABSENCES "MAKEUP BOOKING" › ON THE FOLLOW-UP. Catalog reads only.
    def gmail_address(self):
        """The Gmail address booking cards go out from, or '' when it cannot be read."""
        try:
            return str(self.cards.mail.gmail.email or "")
        except Exception:  # a word on a card; the send itself reports a bad connection
            return ""

    def excuse_cards(self, db, section_id, enrollment_id, first, last, absence_id=None):
        """The titles of the quizzes whose booking card goes out by itself once an absence of this student over these dates is
        excused: `_offer_one`'s own test, read before the decision. [] when the term's cards are off or the absence is held."""
        from .makeups import _dropped, covering, missed
        from exam_workflow.given_dates import given_dates
        term = db.execute("SELECT term_id FROM course_sections WHERE id=?", (section_id,)).fetchone()
        member = (section_id, enrollment_id)
        if not term or not self._settings(db, [term[0]])[term[0]]["cards"] or member in _dropped(db):
            return []
        if absence_id and self._held(self._holds(db), {"absence_id": absence_id}):
            return []
        quizzes, closed, found = self.quizzes(db), missed(db), []
        for event in db.execute("""SELECT e.* FROM exam_events e JOIN exam_event_sections x ON x.event_id=e.id
                WHERE e.term_id=? AND x.section_id=? ORDER BY e.created_at, e.id""", (term[0], section_id)).fetchall():
            event = dict(event)
            if event["id"] not in quizzes or (event["id"], *member) in closed:
                continue
            day = given_dates(db, event["id"], term[0], event["assignment_id"], {member}, own_makeup=False).get(member)
            if not day or not first <= day <= last:
                continue
            person = self.exams._participant_paper(db, event, section_id, enrollment_id)
            if person and person["collection"]["status"] in RECEIVED:
                continue
            sitting = person and person.get("sitting_id") and db.execute("SELECT kind FROM exam_sittings WHERE id=?", (person["sitting_id"],)).fetchone()
            if sitting and sitting["kind"] == "makeup":
                continue
            claimed = covering(db, term[0], event["id"], event["assignment_id"], {member}).get(member)
            if claimed and claimed["state"] == "owed":  # another excused absence already owes it: its card is that absence's
                continue
            if absence_id and self._offers(db).get((event["id"], section_id, enrollment_id, absence_id)):
                continue
            found.append(event["title"])
        return found

    def followup_cards(self, db, enrollment_ids):
        """{enrollment ID: [{event_id, title, state, flag, text}]}: each quiz makeup of these students, what its booking card has done
        or will do, in the follow-up's words. A term whose cards are off, and exams, say nothing here."""
        from .makeups import owed
        wanted = sorted(set(enrollment_ids))
        if not wanted or not _has(db, "absence_makeup_offers"):
            return {}
        members = {(row[0], row[1]) for row in db.execute(
            "SELECT section_id, enrollment_id FROM roster_memberships WHERE enrollment_id IN (SELECT value FROM json_each(?))", (json.dumps(wanted),))}
        rows = owed(db, self.exams, members=members)
        if not rows:
            return {}
        self.annotate(db, rows, details=False)
        settings = self._settings(db, sorted({row["term_id"] for row in rows}))
        cards = {b["card"]["card_id"] for b in (row.get("booking") for row in rows) if b and b.get("card")}
        mailed = {row["card_id"]: dict(row) for row in db.execute(
            """SELECT card_id, gmail_sender, MAX(updated_at) updated_at FROM response_card_mail WHERE kind='invitation' AND status='accepted'
               AND card_id IN (SELECT value FROM json_each(?)) GROUP BY card_id""", (json.dumps(sorted(cards)),))}
        found = {}
        for row in rows:
            text = self._followup_text(row, settings.get(row["term_id"], {}).get("cards"), mailed)
            if text:
                booking = row.get("booking") or {}
                found.setdefault(row["enrollment_id"], []).append({"event_id": row["event_id"], "title": row["title"], "state": booking.get("state") or row["state"],
                                                                   "flag": bool(booking.get("flag")), "text": f"{row['title']} makeup: {text}"})
        return found

    def _followup_text(self, row, on, mailed):
        """One quiz makeup's line for the follow-up, or '' when there is nothing to say."""
        hold, booking = row.get("hold") or {}, row.get("booking")
        if row["state"] == "if_excused":
            if not on or "hold" not in row or row.get("scheduled"):  # `hold` is only set on a quiz's row
                return ""
            return "no booking card goes out while it is on hold" if hold.get("held") else "Faculty emails them a booking card by itself once the absence is excused"
        if not booking or row["state"] != "owed":
            return ""
        state, label = booking.get("state"), booking.get("label") or ""
        if state == "manual":
            return ""
        if state == "queued":
            return "booking card goes out shortly, by itself, from your Gmail"
        if state == "held":
            return f"on hold, no booking card until you release it: {label}"
        if state == "sent":
            card = booking.get("card") or {}
            sent = mailed.get(card.get("card_id"))
            if sent:
                at = dt.datetime.fromisoformat(sent["updated_at"].replace("Z", "+00:00")).astimezone(TIME_ZONE)
                sender = f" from {sent['gmail_sender']}" if sent["gmail_sender"] else ""
                label = f"booking card emailed {slots.day_label(at.date())}, {slots.twelve(at.hour * 60 + at.minute)}{sender}"
            return f"{label}; {'opened, no time picked yet' if card.get('status') == 'opened' else 'no answer yet'}"
        if state == "other_time" and (booking.get("pick") or {}).get("choice"):
            return f"{label}: “{' '.join(booking['pick']['choice'].split())[:300]}”"  # BACK TO FOLLOW-UP: their words
        return label

    @staticmethod
    def _copied(db):
        """{(sitting, enrollment): {batch_id, status}} of the copies made for booked students: the unnamed ones by their record
        (`absence_makeup_copies`, UNNAMED 2026-10-16), the named ones made before by their copy. A job the instructor superseded
        reads `superseded`: it has no file left, so the row offers none (the Makeups row's Print PDF, 2026-10-12)."""
        if not _has(db, "print_batches", "printed_copies"):
            return {}
        rows = list(db.execute(
            """SELECT b.id,b.request_id,b.status,b.superseded_at,c.enrollment_id FROM print_batches b JOIN printed_copies c ON c.print_batch_id=b.id
               WHERE b.request_id LIKE ? AND b.status!='cancelled' AND c.enrollment_id IS NOT NULL""", (COPIES + "%",)))
        if _has(db, "absence_makeup_copies"):
            rows += db.execute("""SELECT b.id,b.request_id,b.status,b.superseded_at,m.enrollment_id FROM absence_makeup_copies m
                JOIN print_batches b ON b.id=m.print_batch_id WHERE b.status!='cancelled'""").fetchall()
        printed = {}
        if _has(db, "print_printer_sends", "print_printer_send_batches"):
            # The latest Print at Home of each job (AUTOMATIC MAKEUP PRINTS), for the row to say whether the copy printed.
            for send in db.execute("""SELECT l.batch_id,s.status FROM print_printer_send_batches l JOIN print_printer_sends s ON s.id=l.send_id
                    ORDER BY s.created_at,s.id"""):
                printed[send["batch_id"]] = send["status"]
        return {(row["request_id"].split(":")[1], row["enrollment_id"]): {"batch_id": row["id"], "status": "superseded" if row["superseded_at"] else row["status"],
                                                                          **({"printed": printed[row["id"]]} if row["id"] in printed else {})}
                for row in rows}

    def attention(self, db, rows):
        """Home's makeups card for terms whose booking cards are on (None when no current term has them on): what needs the
        instructor, counted. A term whose cards are off keeps the old count: every makeup owed."""
        settings = self._settings(db, sorted({row["term_id"] for row in rows}))
        if not any(value["cards"] for value in settings.values()):
            return None
        versions = self.annotate(db, rows)
        parts = {"flags": 0, "owed": 0, "finalize": 0, "print": 0}  # a version to approve has its own card, on the Assignments badge
        today = campus_now().date()
        for row in rows:
            if row["state"] != "owed":
                continue
            booking = row.get("booking")
            if not settings.get(row["term_id"], {}).get("cards"):
                parts["owed"] += 1
            elif booking and booking["flag"]:
                parts["flags"] += 1
            elif booking and booking["state"] == "booked" and booking.get("copy") and row.get("scheduled") and row["scheduled"].get("date") \
                    and booking["copy"].get("printed") not in ("accepted", "pending", "sending") and dt.date.fromisoformat(row["scheduled"]["date"]) <= today + dt.timedelta(days=1):
                parts["print"] += 1
        for version in versions.values():
            parts["finalize"] += version["state"] == "approved" and version.get("rubrics") == "to_finalize"
        return parts

    # -- Card context (ResponseCardService.issue) ------------------------------------------------------------------------------
    def issue_context(self, db, normalized, student):
        """The variables and options of one booking card, from its offer: the student's own times and dates."""
        offer = db.execute("SELECT * FROM absence_makeup_offers WHERE id=?", (normalized["makeup_offer_id"],)).fetchone()
        if (not offer or offer["event_id"] != normalized.get("exam_event_id") or offer["term_id"] != normalized["term_id"]
                or (offer["section_id"], offer["enrollment_id"]) != (student["section_id"], student["enrollment_id"])):
            raise ValueError("A makeup booking card goes only to the student its offer is for.")
        return {"variables": {"makeup.given": slots.long_day(offer["given_date"]), "makeup.due": slots.long_day(offer["due_date"])},
                "choices": {slots.FIELD: slots.choices(json.loads(offer["options_json"]))}}

    # -- Times -----------------------------------------------------------------------------------------------------------------
    def calendar_events(self, db, start, end):
        """The calendar as the Calendar page reads it over [start, end]: Faculty's sources, its own events and the Google events."""
        from scheduling.feed import assemble
        # The Google copy is a convenience here: the times are still checked against Faculty's own calendar when it cannot be read.
        return assemble(db, start, end, calendar=self.calendar, google=self.google,
                        on_google_error=lambda: LOG.exception("Google calendar events could not be read for makeup times"))

    @staticmethod
    def _makeup_sittings(db):
        """Calendar row IDs of every makeup sitting: several students may share a time, so these never make a time busy."""
        return {f"exam:{row[0]}" for row in db.execute("SELECT id FROM exam_sittings WHERE kind='makeup'")} if _has(db, "exam_sittings") else set()

    @staticmethod
    def _due(row, history, returns=None):
        """The day the makeup must be done by: the policy's (makeup_slots.due_date, counted from the student's return when that is
        later), or a later one a card moved it to (A MISSED MAKEUP: "Send a new card" after the due date passed)."""
        return max([slots.due_date(row["last_date"], returns)] + [offer["due_date"] for offer in history if offer.get("due_date")])

    @staticmethod
    def returns(db, row):
        """The day the student can return, as the absence's details name it (FROM THEIR RETURN, absences/service.py
        `return_date`), or None: a follow-up's row with no absence recorded has none."""
        return return_dates(db, [row["absence_id"]]).get(row["absence_id"]) if row.get("absence_id") else None

    def times(self, db, row, *, after=None, now=None, due=None):
        """What a card for this owed row would offer now (makeup_slots.offer); `after` is the last day an earlier card offered, `due`
        a due date to search to instead of the makeup's own (`_due`). Never before the student's return (`returns`), nor before now."""
        now = now or campus_now()
        given = row["given_date"]
        returns = self.returns(db, row)
        # A follow-up asks about a day with no absence recorded yet: no card was ever sent for it, so the policy's due date stands.
        due = due or self._due(row, self._offers(db).get(_key(row), []) if row.get("absence_id") and row.get("event_id") else [], returns)
        start = min(now.date(), dt.date.fromisoformat(given)).isoformat()
        end = (dt.date.fromisoformat(due) + dt.timedelta(days=1)).isoformat()
        if end < start:
            end = start
        if (dt.date.fromisoformat(end) - dt.date.fromisoformat(start)).days > 365:
            start = (dt.date.fromisoformat(end) - dt.timedelta(days=365)).isoformat()
        hours = availability(db, row.get("term_id"))  # the Term schedule's while the term uses it, else the legacy meeting hours
        return slots.offer(given, row["first_date"], row["last_date"], self.calendar_events(db, start, end), now, after=after,
                           exclude=self._makeup_sittings(db), windows=hours["windows"], rooms=hours["makeup_rooms"],
                           minutes=student_minutes(db, row.get("section_id"), row.get("enrollment_id")), due=due, returns=returns)

    def plan(self, held=None):
        """The dry run: for each owed quiz makeup without a card or a makeup sitting, the times its card would offer now. Writes nothing.
        A held one (MAKEUP HOLDS) gets no card: it goes in `held` when a list is given ({absence_id, event_id, section_id,
        enrollment_id, title, section, reason, check_back})."""
        with self.rosters.lock(), connect(self.path) as db:
            rows = self._owed(db)
            quizzes, offers, holds = self.quizzes(db), self._offers(db), self._holds(db)
            found = []
            for row in rows:
                if row["event_id"] not in quizzes or row["state"] != "owed" or row.get("scheduled") or offers.get(_key(row)):
                    continue
                hold = self._held(holds, row)
                if hold:
                    if held is not None:
                        held.append({**{key: row[key] for key in ("absence_id", "event_id", "section_id", "enrollment_id", "title", "section")},
                                     "reason": hold["reason"], "check_back": hold["check_back"]})
                    continue
                offered = self.times(db, row)
                found.append({"absence_id": row["absence_id"], "event_id": row["event_id"], "section_id": row["section_id"],
                              "enrollment_id": row["enrollment_id"], "title": row["title"], "section": row["section"],
                              "week": offered["week"], "due": offered["due"], "options": [o["text"] for o in offered["options"]]})
        return found

    # -- Explicit: a new card ----------------------------------------------------------------------------------------------------
    def new_card(self, data, actor="workspace"):
        """The instructor's "Send a new card" (or "Send booking card" while the term's cards are off): the next offer for one owed
        quiz makeup, from the day after the last card's last day; the worker sends it. Any earlier unanswered card is then revoked."""
        request_id = data.get("request_id")
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 60:
            raise ValueError("Send a request ID.")
        wanted = tuple(data.get(key) for key in ("event_id", "section_id", "enrollment_id", "absence_id"))
        if not all(isinstance(value, str) and value for value in wanted):
            raise ValueError("Name the makeup to send a card for.")
        with self.rosters.lock(), connect(self.path) as db:
            prior = db.execute("SELECT * FROM absence_makeup_offers WHERE request_id=?", ("new-card:" + request_id,)).fetchone()
            if prior:
                if _key(prior) != wanted:
                    raise Conflict("This request ID already belongs to another card.")
                return self._offer_view(prior)
            row = next((r for r in self._owed(db) if _key(r) == wanted and r["state"] == "owed"), None)
            if row is None:
                raise Conflict("This makeup is no longer owed.")
            if row["event_id"] not in self.quizzes(db):
                raise ValueError("Booking cards are for quizzes; schedule an exam's makeup on the Exams page.")
            # SCHEDULING "GOOGLE CHECKED" (requirement, 2026-10-06): a card
            # you send offers no time Faculty could not check against your Google calendar either. Refused with the reason, nothing
            # recorded; the automatic first card waits instead (_offer_one).
            unchecked = google_gap(self.google)
            if unchecked:
                raise Conflict(f"{unchecked}, so this card's times cannot be checked against it. No card was sent; try again once "
                               "Faculty has read it.")
            history = self._offers(db).get(wanted, [])
            hold = self._holds(db).get(row["absence_id"])
            last = self._cards(db).get(history[-1]["id"]) if history else None
            # MAKEUP HOLDS: after a hold, or after a card withdrawn unanswered (the student never had a card to pick from), the
            # next card starts from today, not after the withdrawn card's days.
            withdrawn = bool(last and (last["status"] == "revoked" or last["invalidated"]) and not last["current_submission_id"])
            after = None if not history or withdrawn or (hold and (hold["held"] or hold["created_at"] >= history[-1]["created_at"])) \
                else max(o["date"] for o in history[-1]["options"])
            # A MISSED MAKEUP (requirement, 2026-10-18): a card you send offers the next two days with times even past
            # the due date, which then moves to the card's last day; her missed sitting is let go so the new card can go out.
            # ALWAYS TWO DAYS (requirement, 2026-11-12): a card you send offers two days whenever two can be found, so the search goes past the due date not only
            # when nothing is left before it but also when one day is, and the due date then moves to the card's last day.
            offered = self.times(db, row, after=after)
            if _days(offered) < slots.DAYS:
                start = max(campus_now().date(), dt.date.fromisoformat(after) + dt.timedelta(days=1) if after else campus_now().date())
                returns = self.returns(db, row)
                start = max(start, dt.date.fromisoformat(returns)) if returns else start
                longer = self.times(db, row, after=after, due=(start + dt.timedelta(days=EXTEND)).isoformat())
                if _days(longer) > _days(offered):
                    longer["due"] = max(offered["due"], max(option["date"] for option in longer["options"]))
                    offered = longer
            if not offered["options"]:
                raise Conflict(f"No office-hours or meeting-hours time is open in the next {EXTEND} days. Schedule it by hand.")
            missed = row.get("scheduled")
            if missed and missed.get("date") and dt.date.fromisoformat(missed["date"]) < campus_now().date():
                self.exams.update_participant(row["event_id"], row["section_id"], row["enrollment_id"],
                                              {"version": row["participant_version"], "sitting_id": None})
            if hold and hold["held"]:  # sending a card by hand releases the hold (requirement, 2026-10-06)
                self._record_hold(db, row["absence_id"], hold, False, "", None, "new-card-release:" + request_id, actor)
            offer = self._record_offer(db, row, offered, len(history) + 1, "new-card:" + request_id, actor)
        self._idle_until = 0.0
        self.absences_wake()
        return self._offer_view(offer)

    # -- Explicit: holds (MAKEUP HOLDS) ----------------------------------------------------------------------------------------------
    def hold(self, absence_id, data, actor="workspace"):
        """Hold an absence's quiz makeups (`held` true) until the instructor releases them (`held` false): while held no booking
        card goes out, an unanswered one is withdrawn, and a follow-up offers no makeup time. `reason` says what it waits on (a
        few words), `check_back` the campus day it is flagged to the instructor; `revision` is the hold the caller read (0: never
        held), required to release. Holding what is already held the same way, or releasing what is not held, records nothing."""
        request_id = data.get("request_id")
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 100:
            raise ValueError("Send a request ID.")
        held, revision = data.get("held"), data.get("revision")
        if not isinstance(held, bool):
            raise ValueError("Say whether this absence's makeups are held.")
        if revision is not None and type(revision) is not int:
            raise ValueError("The hold's revision is a whole number.")
        if not held and revision is None:
            raise ValueError("Name the hold you release (its revision).")
        reason, check_back = data.get("reason") or "", data.get("check_back") or None
        if not isinstance(reason, str) or len(" ".join(reason.split())) > 300:
            raise ValueError("Say what the hold waits on in 300 characters or fewer.")
        reason = " ".join(reason.split()) if held else ""
        if check_back is not None:
            try:
                if not isinstance(check_back, str) or dt.date.fromisoformat(check_back).isoformat() != check_back:
                    raise ValueError
            except ValueError:
                raise ValueError("The check-back day is a date (YYYY-MM-DD).") from None
        check_back = check_back if held else None
        with self.rosters.lock(), connect(self.path) as db:
            latest = db.execute("""SELECT r.status FROM absences a JOIN absence_revisions r ON r.absence_id=a.id WHERE a.id=?
                                   ORDER BY r.revision DESC LIMIT 1""", (str(absence_id),)).fetchone()
            if not latest:
                raise NotFound("That absence was not found.")
            prior = db.execute("SELECT * FROM absence_makeup_holds WHERE request_id=?", (request_id,)).fetchone()
            if prior:
                if (prior["absence_id"], bool(prior["held"]), prior["reason"], prior["check_back"]) != (absence_id, held, reason, check_back):
                    raise Conflict("This request ID already belongs to another change.")
                return self._hold_view(self._holds(db).get(absence_id))
            current = self._holds(db).get(absence_id)
            if revision is not None and revision != (current["revision"] if current else 0):
                raise Conflict("This absence's makeup hold changed since you read it. Reload the Makeups view.")
            if held and latest["status"] == "withdrawn":
                raise Conflict("This absence was withdrawn: it owes no makeup.")
            # A KNOWN RETURN (requirement, 2026-11-09): the card's times already start from it.
            back = held and actor == "assistant" and known_return(db, absence_id, reason)
            if back:
                raise Conflict(f"ABSENCES MAKEUP HOLDS › A KNOWN RETURN: the student can return on {slots.day_label(dt.date.fromisoformat(back))}, "
                               "and the booking card's times already start then, so its makeups are not held: the card goes out once the "
                               "absence is excused.")
            if held != bool(current and current["held"]) or (held and (current["reason"], current["check_back"]) != (reason, check_back)):
                self._record_hold(db, absence_id, current, held, reason, check_back, request_id, actor)
            view = self._hold_view(self._holds(db).get(absence_id))
        self._idle_until = 0.0
        self.absences_wake()
        return view

    @staticmethod
    def _record_hold(db, absence_id, current, held, reason, check_back, request_id, actor):
        """One hold revision, in its own transaction; the caller holds the lifecycle lock."""
        db.execute("BEGIN IMMEDIATE")
        db.execute("INSERT INTO absence_makeup_holds VALUES(?,?,?,?,?,?,?,?)",
                   (absence_id, (current["revision"] if current else 0) + 1, int(held), reason, check_back, request_id, actor, timestamp()))
        db.commit()

    def absences_wake(self):
        inbox = getattr(self, "inbox", None)
        if inbox is not None:
            inbox.wake.set()

    @staticmethod
    def _offer_view(offer):
        offer = dict(offer)
        return {"id": offer["id"], "attempt": offer["attempt"], "week": offer["week"], "due": offer["due_date"],
                "options": [o["text"] for o in json.loads(offer["options_json"])]}

    def _record_offer(self, db, row, offered, attempt, request_id, actor):
        """One offer (the times a card will carry), in its own transaction; the caller holds the lifecycle lock."""
        revision = db.execute("SELECT MAX(revision) FROM absence_revisions WHERE absence_id=?", (row["absence_id"],)).fetchone()[0]
        db.execute("BEGIN IMMEDIATE")
        offer_id = str(uuid.uuid4())
        db.execute("INSERT INTO absence_makeup_offers VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   (offer_id, row["term_id"], row["event_id"], row["section_id"], row["enrollment_id"], row["absence_id"], revision, attempt,
                    row["given_date"], offered["due"], offered["week"], json.dumps(offered["options"], ensure_ascii=False), request_id, actor, timestamp()))
        db.commit()
        return db.execute("SELECT * FROM absence_makeup_offers WHERE id=?", (offer_id,)).fetchone()

    # -- The worker turn -------------------------------------------------------------------------------------------------------
    def _ready(self, key):
        failed = self._failures.get(key)
        return not failed or time.monotonic() >= failed[1]

    def _failed(self, key, error, quiet=False):
        count = self._failures.get(key, (0, 0))[0] + 1
        self._failures[key] = (count, time.monotonic() + RETRY[min(count, len(RETRY)) - 1])
        (LOG.debug if quiet else LOG.warning)("Makeup booking: %s failed (%s): %s", key[0], count, getattr(error, "description", None) or error)

    def process_one(self):
        """One bounded turn: the first step with something to do does one item. True when it did."""
        if time.monotonic() < self._idle_until:
            return False
        with connect(self.path) as db:
            if not _has(db, "absence_makeup_offers", "exam_events"):
                return False
            rows = self._owed(db)
        for step in (self._pick_one, self._followup_one, self._zero_one, self._revoke_one, self._draft_one, self._review_one, self._deliver_one, self._offer_one, self._copies_one, self._print_one):
            try:
                if step(rows):
                    return True
            except Exception:  # one step's failure never stops the others; its item waits (RETRY) and is tried again
                LOG.exception("Makeup booking step %s failed", step.__name__)
        self._idle_until = time.monotonic() + IDLE
        return False

    # 1. Picks ------------------------------------------------------------------------------------------------------------------
    def _pick_one(self, rows):
        with connect(self.path) as db:
            if not _has(db, "response_card_submissions"):
                return False
            pending = db.execute("""SELECT s.id submission_id,s.card_id,s.submission_json,json_extract(i.context_json,'$.makeup_offer_id') offer_id
                FROM response_card_submissions s JOIN response_card_recipients r ON r.id=s.card_id JOIN response_card_issues i ON i.id=r.issue_id
                WHERE json_extract(i.context_json,'$.makeup_offer_id') IS NOT NULL AND r.current_submission_id=s.id
                AND NOT EXISTS(SELECT 1 FROM absence_makeup_picks p WHERE p.submission_id=s.id) ORDER BY s.received_at,s.id""").fetchall()
        for item in pending:
            key = ("pick", item["submission_id"])
            if not self._ready(key):
                continue
            try:
                self._seat(item, rows)
            except Exception as error:  # a stale exam version, say: tried again after a wait, and recorded as failed after five
                self._failed(key, error)
                if self._failures[key][0] >= 5:
                    self._record_pick(item, "failed", "", None, str(getattr(error, "description", None) or error))
                continue
            return True
        return False

    def _record_pick(self, item, outcome, choice, sitting_id, detail=""):
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("INSERT OR IGNORE INTO absence_makeup_picks VALUES(?,?,?,?,?,?,?,?)",
                       (item["submission_id"], item["offer_id"], item["card_id"], outcome, choice[:4000], sitting_id, detail[:2000], timestamp()))

    def _seat(self, item, rows):
        # ABSENCES MAKEUP BOOKING: recheck under the lifecycle lock; the worker's earlier read can be stale.
        with self.rosters.lock():
            with connect(self.path) as db:
                current_rows = self._owed(db)
            return self._seat_current(item, current_rows)

    def _seat_current(self, item, rows):
        """The student seated in the makeup sitting of the time they picked (EXAMS exception, 2026-10-04), or their request for
        another time kept. Rechecked first: the makeup is still owed."""
        with connect(self.path) as db:
            offer = db.execute("SELECT * FROM absence_makeup_offers WHERE id=?", (item["offer_id"],)).fetchone()
        answers = json.loads(item["submission_json"]).get("answers") or {}
        choice = answers.get(slots.FIELD)
        if choice == slots.OTHER:
            words = answers.get(slots.OTHER_FIELD)
            self._record_pick(item, "other_time", " ".join(str(words or "").split()) if not isinstance(words, str) else words.strip(), None)
            return
        option = next((o for o in json.loads(offer["options_json"]) if o["text"] == choice), None)
        if option is None:
            self._record_pick(item, "failed", str(choice or "")[:200], None, "The answer is not one of the card's times.")
            return
        row = next((r for r in rows if _key(r) == _key(offer) and r["state"] == "owed"), None)
        if row is None:
            self._record_pick(item, "stale", choice, None, "No longer owed: the paper came back, the absence is no longer excused, "
                                                            "or the student left the section.")
            return
        event = self.exams.get(offer["event_id"])
        member = (offer["section_id"], offer["enrollment_id"])
        person = next((p for p in event["participants"] if (p["section_id"], p["enrollment_id"]) == member), None)
        if person is None:
            event = self.exams.sync_roster(event["id"], {"version": event["version"]})
            person = next((p for p in event["participants"] if (p["section_id"], p["enrollment_id"]) == member), None)
            if person is None:
                raise NotFound("The student is not on the quiz's exam record.")
        sitting = next((s for s in event["sittings"] if s["kind"] == "makeup" and (s["date"], s["time"], s["location"])
                        == (option["date"], option["time"], option["location"])), None)
        if sitting is None:
            before = {s["id"] for s in event["sittings"]}
            event = self.exams.save_sitting(event["id"], {"version": event["version"], "kind": "makeup", "label": sitting_label(option),
                                                         "date": option["date"], "time": option["time"], "location": option["location"],
                                                         "participants": [{"section_id": member[0], "enrollment_id": member[1], "version": person["version"]}]})
            sitting = next(s for s in event["sittings"] if s["id"] not in before)
        elif person["sitting_id"] != sitting["id"]:
            self.exams.update_participant(event["id"], member[0], member[1], {"version": person["version"], "sitting_id": sitting["id"]})
        self._record_pick(item, "seated", choice, sitting["id"])

    # 1b. Another time, back to Follow-up -------------------------------------------------------------------------------------------
    @staticmethod
    def _asked_request(submission_id):
        return ASKED + hashlib.sha256(submission_id.encode()).hexdigest()[:40]

    @staticmethod
    def _asked_answered(row, offer, history):
        """How a request for another time was answered, in a line, or '' while it still stands (`_state`'s "other_time")."""
        if row is None:
            return "The makeup is no longer owed."
        if offer is not history[-1]:
            return "A new booking card went out."
        if row.get("scheduled"):
            return f"They are on a makeup sitting: {row['scheduled']['label']}."
        return ""

    @staticmethod
    def _asked_taken(db):
        """TAKEN UP IN FOLLOW-UP (requirement, 2026-11-12): {request ID: {id, state, label}} of the follow-ups BACK TO FOLLOW-UP opened that
        the instructor took up and left open (Waiting on student, To do, Follow up later), with the day it has stood so since.
        Follow-up carries those (a Waiting one comes back to Needs me by itself, TEACHING-WORKFLOWS CHECK BACK), so their Makeups
        row is not flagged. One still Needs my reply, or resolved while the request stands, is not here: the row keeps its flag."""
        if not _has(db, "student_support_requests", "student_support_revisions"):
            return {}
        saved = {}
        for row in db.execute("""SELECT q.request_id, q.item_id, r.content_json, r.created_at FROM student_support_requests q
                JOIN student_support_revisions r ON r.item_id=q.item_id WHERE q.request_id LIKE ? ORDER BY q.item_id, r.revision""", (ASKED + "%",)):
            state = json.loads(row["content_json"]).get("state")
            last = saved.get(row["request_id"])
            saved[row["request_id"]] = {"id": row["item_id"], "state": state,  # `since`: the first save of the state it has now
                                        "since": last["since"] if last and last["state"] == state else row["created_at"]}
        return {request: {"id": item["id"], "state": item["state"],
                          "label": TAKEN[item["state"]].format(day=slots.day_label(campus_day(item["since"])))}
                for request, item in saved.items() if item["state"] in TAKEN}

    def _asked_followup(self, row, offer, pick, due):
        """The follow-up of a card answered "I need another time": their words, the days the card offered and the due date."""
        words = " ".join(pick["choice"].split())[:1500]
        days = sorted({option["date"] for option in offer["options"]})
        details = (f"On the {row['title']} makeup booking card they answered that none of its times work "
                   f"({slots.day_label(campus_day(pick['created_at']))})" + (f": “{words}”." if words else ", and named no other time.")
                   + f" The card offered {' and '.join(slots.day_label(dt.date.fromisoformat(day)) for day in days)}."
                   + f" The makeup is due by {slots.day_label(dt.date.fromisoformat(due))}.")
        return {"title": f"{row['title']} makeup: needs another time", "details": details,
                "next": "Seat them at a time that works, or send a new card"}

    def asked(self, db, item_id):
        """[{event_id, section_id, at}] of the card answers a follow-up was opened for (BACK TO FOLLOW-UP): the quiz and when the
        student answered, for its preparation's facts. [] for any other follow-up."""
        if not item_id or not _has(db, "absence_makeup_picks", "student_support_requests"):
            return []
        requests = {row[0] for row in db.execute("SELECT request_id FROM student_support_requests WHERE item_id=? AND request_id LIKE ?",
                                                 (item_id, ASKED + "%"))}
        if not requests:
            return []
        return [{"event_id": row["event_id"], "section_id": row["section_id"], "at": row["created_at"]} for row in db.execute(
            """SELECT p.submission_id,p.created_at,o.event_id,o.section_id FROM absence_makeup_picks p JOIN absence_makeup_offers o
               ON o.id=p.offer_id WHERE p.outcome='other_time' ORDER BY p.created_at,p.rowid""")
                if self._asked_request(row["submission_id"]) in requests]

    def _followup_one(self, rows):
        """BACK TO FOLLOW-UP (requirement, 2026-11-12): a card
        answered "None of these work: I need another time" opens a follow-up of the student's, Needs my reply, with their words,
        once per answer (request ID `makeup-other-time:<hash of the submission>`), while the request stands (owed, no newer card,
        no makeup sitting) and its absence is not held. Once it is answered outside the follow-up (they are seated by hand, a new
        card went out, the makeup is no longer owed), Faculty resolves the follow-up, unless the instructor saved it or approved
        one of its steps since: then it is theirs to settle."""
        if self.followups is None:
            return False
        live = {_key(r): r for r in rows if r["state"] == "owed"}
        with connect(self.path) as db:
            if not _has(db, "student_support_requests"):
                return False
            asked = [pick for pick in self._picks(db).values() if pick["outcome"] == "other_time"
                     and self._asked_request(pick["submission_id"]) not in self._asked_settled]
            if not asked:
                return False
            opened = {row[0]: row[1] for row in db.execute("SELECT request_id, item_id FROM student_support_requests WHERE request_id LIKE ?",
                                                           (ASKED + "%",))}
            offers, holds = self._offers(db), self._holds(db)
            found = {offer["id"]: (key, history, offer) for key, history in offers.items() for offer in history}
            todo = []
            for pick in asked:
                key, history, offer = found[pick["offer_id"]]
                request, row = self._asked_request(pick["submission_id"]), live.get(key)
                answered = self._asked_answered(row, offer, history)
                if request in opened:
                    if not answered:
                        continue
                    if self.followups.untouched(db, opened[request]):
                        todo.append((request, pick, None, opened[request], answered))
                    else:
                        self._asked_settled.add(request)  # resolved, or the instructor's to settle: never looked at again
                elif not answered and not self._held(holds, row):
                    due = self._due(row, history, self.returns(db, row))
                    todo.append((request, pick, self._asked_followup(row, offer, pick, due), offer["enrollment_id"], ""))
        for request, pick, followup, target, answered in todo:
            item = ("asked", pick["submission_id"])
            if not self._ready(item):
                continue
            try:
                if followup:
                    self.followups.faculty_request(target, request, followup)
                else:
                    self.followups.resolve_faculty_followup(target, f"{answered} (Faculty, {campus_now().date().isoformat()})")
                    self._asked_settled.add(request)
            except Exception as error:
                self._failed(item, error)
                continue
            return True
        return False

    # 1c. No answer, then a 0 ---------------------------------------------------------------------------------------------------------
    def _zero_one(self, rows):
        """NO ANSWER, THEN A 0 (requirement, 2026-11-12): an owed quiz makeup whose card reads No answer and is due
        its 0 (`_zero`) is recorded missed the way "Missed makeup, record 0" records it (`AbsenceService.makeup_missed`, actor
        `automatic`, request `makeup-no-answer:<offer>`): owed no more, off the Makeups view, its empty cell a 0; the card is then
        withdrawn by the next step. Once per card. A SCORE STANDS (2026-11-14): first, such a 0 still standing whose student now
        holds a score above 0 for the quiz in the gradebook (`_score`) is taken back (`missed` false, actor `automatic`, request
        `makeup-no-answer-scored:<offer>`), so the next Certify never writes it over that score; the instructor's own 0 is theirs."""
        if self._unzero_one():
            return True
        today = campus_now().date()
        with connect(self.path) as db:
            quizzes, offers, cards, picks, holds = self.quizzes(db), self._offers(db), self._cards(db), self._picks(db), self._holds(db)
            due = []
            for row in rows:
                history = offers.get(_key(row)) if row["state"] == "owed" and row["event_id"] in quizzes else None
                if not history:
                    continue
                offer, hold = history[-1], holds.get(row["absence_id"])
                card = cards.get(offer["id"])
                if self._reoffer(offer, card, hold) or self._state(row, offer, card, picks.get(offer["id"]), True, today, None, hold)[0] != "no_answer":
                    continue
                if (self._zero(db, offer, card, today) or {}).get("due"):
                    due.append((row, offer))
        for row, offer in due:
            item = ("zero", offer["id"])
            if not self._ready(item):
                continue
            try:
                self.absences.makeup_missed({"request_id": NO_ANSWER + offer["id"], "missed": True, "event_id": row["event_id"],
                                             "section_id": row["section_id"], "enrollment_id": row["enrollment_id"]}, actor="automatic")
            except Exception as error:
                self._failed(item, error)
                continue
            return True
        return False

    def _unzero_one(self):
        """A SCORE STANDS: take back one 0 Faculty recorded for an unanswered card (its latest revision, a current term's) once the
        student holds a score above 0 for the quiz in the gradebook. True when it did."""
        with connect(self.path) as db:
            if not _has(db, "absence_makeups_missed", "course_terms"):
                return False
            standing = db.execute("""SELECT m.event_id, m.section_id, m.enrollment_id, m.request_id FROM absence_makeups_missed m
                JOIN exam_events e ON e.id=m.event_id JOIN course_terms t ON t.id=e.term_id AND t.is_current=1 AND t.deleted_at IS NULL
                WHERE m.missed=1 AND m.actor='automatic' AND substr(m.request_id, 1, ?)=? AND m.revision=(SELECT MAX(x.revision)
                FROM absence_makeups_missed x WHERE x.event_id=m.event_id AND x.section_id=m.section_id AND x.enrollment_id=m.enrollment_id)
                ORDER BY m.created_at, m.request_id""", (len(NO_ANSWER), NO_ANSWER)).fetchall()
            memo = {}
            found = [row for row in standing if self._score(db, row["event_id"], row["enrollment_id"], memo)]
        for row in found:
            item = ("unzero", row["request_id"])
            if not self._ready(item):
                continue
            try:
                self.absences.makeup_missed({"request_id": SCORED + row["request_id"][len(NO_ANSWER):], "missed": False, "event_id": row["event_id"],
                                             "section_id": row["section_id"], "enrollment_id": row["enrollment_id"]}, actor="automatic")
            except Exception as error:
                self._failed(item, error)
                continue
            return True
        return False

    # 2. Revocations --------------------------------------------------------------------------------------------------------------
    def _revoke_one(self, rows):
        live = {_key(r): r for r in rows if r["state"] == "owed"}
        with connect(self.path) as db:
            offers, cards, holds = self._offers(db), self._cards(db), self._holds(db)
        for key, history in offers.items():
            for offer in history:
                card = cards.get(offer["id"])
                if (not card or card["publish_status"] != "published" or card["current_submission_id"] or card["status"] == "revoked"
                        or card["invalidated"]):
                    continue
                row = live.get(key)
                if row is not None and offer is history[-1] and not row.get("scheduled") and not self._held(holds, row):
                    continue  # still the card that stands for an owed, unscheduled makeup that is not held (MAKEUP HOLDS)
                item = ("revoke", card["card_id"])
                if not self._ready(item):
                    continue
                try:
                    self.cards.control(card["card_id"], {"action": "revoke", "request_id": f"makeup-revoke-{card['card_id']}"})
                except Exception as error:
                    self._failed(item, error)
                    continue
                return True
        return False

    # 3. The makeup version -------------------------------------------------------------------------------------------------------
    def _draft_one(self, rows):
        if self.grading is None:
            return False
        with connect(self.path) as db:
            quizzes = self.quizzes(db)
            plans = sorted({r["plan_id"] for r in rows if r["state"] == "owed" and r["plan_id"] and r["event_id"] in quizzes and not r["makeup_versions"]})
            todo = []
            for plan in plans:
                if db.execute("SELECT 1 FROM quiz_drafts WHERE request_id=?", (AUTO_DRAFT + plan,)).fetchone():
                    continue  # drafted once by itself: a scrapped one is the instructor's choice
                if any(not db.execute("SELECT 1 FROM quiz_draft_scraps WHERE draft_id=?", (row["id"],)).fetchone()
                       for row in self.grading._makeup_rows(db, plan)):
                    continue  # a makeup draft of theirs is open or approved
                todo.append(plan)
        for plan in todo:
            item = ("draft", plan)
            if not self._ready(item):
                continue
            try:
                self.grading.create_makeup_draft(plan, {"request_id": AUTO_DRAFT + plan})
            except Exception as error:  # no drafting model chosen, a single-version quiz, no rubrics yet …: shown, tried again later
                self.problems[plan] = str(getattr(error, "description", None) or error) if isinstance(error, (ValueError, HTTPException)) \
                    else "The makeup version could not be drafted; Faculty will try again."
                self._failed(item, error)
                continue
            self.problems.pop(plan, None)
            return True
        return False

    def _review_one(self, rows):
        """A current quiz's newest approved makeup version whose rubrics wait for a review nobody could request at approval (another
        was running then): the rubric review requested once nothing runs, once per rubric set (request `makeup-review:<plan>:<input>`).
        Never while the rubrics are not all confirmed for review, and never again over a review of the same rubrics (done, failed or
        cancelled: the instructor's). Finalizing stays theirs."""
        if self.grading is None:
            return False
        from .makeups import _makeup_versions
        with connect(self.path) as db:
            quizzes, todo = self.quizzes(db), []
            current = {row[0]: row[1] for row in db.execute("""SELECT g.id,g.event_id FROM frq_assignments g JOIN exam_events e ON e.id=g.event_id
                JOIN course_terms t ON t.id=e.term_id WHERE t.is_current=1 AND t.deleted_at IS NULL""")} if _has(db, "frq_assignments") else {}
            for plan, letters in sorted(_makeup_versions(db).items()):
                if current.get(plan) not in quizzes or not self._ready(("review", plan)):
                    continue
                try:
                    status = self.grading.rubric_quality_status(db, plan, details=True)
                except (NotFound, ValueError):
                    continue
                check = status.get("check")
                if (self._rubrics(db, plan, letters[-1]["letter"], status) == "final" or status["missing"] or status["unconfirmed"]
                        or (check and (check["status"] in ("pending", "running") or check.get("current")))):
                    continue
                todo.append((plan, status["input_sha256"]))
        for plan, input_sha256 in todo:
            try:
                self.grading.queue_rubric_quality(plan, {"request_id": f"makeup-review:{plan}:{input_sha256[:40]}", "input_sha256": input_sha256})
            except Exception as error:
                self._failed(("review", plan), error)
                continue
            return True
        return False

    # 4. Cards ----------------------------------------------------------------------------------------------------------------
    def _template_version(self, definition_id):
        """The frozen version of the course's booking-card template, made the first time (an ordinary course template the
        instructor may edit; each card keeps the version it was issued with)."""
        with connect(self.path) as db:
            row = db.execute("SELECT id,version FROM response_card_templates WHERE definition_id=? AND name=? ORDER BY created_at,id LIMIT 1",
                             (definition_id, TEMPLATE_NAME)).fetchone()
        if row is None:
            made = self.cards.save_template({"name": TEMPLATE_NAME, "definition_id": definition_id, "schema": TEMPLATE})
            row = {"id": made["id"], "version": made["version"]}
        return self.cards.freeze(row["id"], row["version"])["id"]

    def _deliver(self, offer):
        """Issue, publish and email one offer's card (each step idempotent, so a retry carries on where it stopped)."""
        with connect(self.path) as db:
            event = db.execute("SELECT e.*,t.definition_id FROM exam_events e JOIN course_terms t ON t.id=e.term_id WHERE e.id=?",
                               (offer["event_id"],)).fetchone()
            prior = db.execute("SELECT version_id FROM response_card_issues WHERE request_id=?", ("makeup-card-" + offer["id"],)).fetchone()
            participant = db.execute("SELECT 1 FROM exam_participants WHERE event_id=? AND section_id=? AND enrollment_id=?",
                                     (offer["event_id"], offer["section_id"], offer["enrollment_id"])).fetchone()
        if not participant:
            current = self.exams.get(offer["event_id"])
            self.exams.sync_roster(offer["event_id"], {"version": current["version"]})
        options = offer["options"] if "options" in offer else json.loads(offer["options_json"])
        last = max(dt.datetime.fromisoformat(f"{o['date']}T{o['time']}") for o in options)
        expires = last.replace(tzinfo=TIME_ZONE).astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")
        issue = self.cards.issue({"request_id": "makeup-card-" + offer["id"], "version_id": prior["version_id"] if prior else self._template_version(event["definition_id"]),
                                  "term_id": offer["term_id"], "section_ids": [offer["section_id"]], "enrollment_ids": [offer["enrollment_id"]],
                                  "assignment_id": event["assignment_id"], "exam_event_id": offer["event_id"], "makeup_offer_id": offer["id"],
                                  "policy": "once", "expires_at": expires})
        if any(card["publish_status"] != "published" for card in issue["recipients"]):
            self.cards.publish(issue["id"])
        # GMAIL (requirement, 2026-10-06): from the instructor's own address, never the relay;
        # while Gmail is not connected the card waits (retried) and its row says so.
        self.cards.mail.queue_invitations(issue["id"], mail_provider="gmail")

    @staticmethod
    def _passed(offer):
        """Every time the offer names has started."""
        options = offer["options"] if "options" in offer else json.loads(offer["options_json"])
        latest = max(dt.datetime.fromisoformat(f"{o['date']}T{o['time']}") for o in options)
        return latest <= campus_now()

    def _deliver_one(self, rows):
        """An offer whose card is not out yet (a failed or interrupted delivery, or a new card the instructor asked for)."""
        live = {_key(r): r for r in rows if r["state"] == "owed"}
        with connect(self.path) as db:
            offers, cards, holds = self._offers(db), self._cards(db), self._holds(db)
        for key, history in offers.items():
            offer, row = history[-1], live.get(key)
            card = cards.get(offer["id"])
            if row is None or row.get("scheduled") or self._held(holds, row) or (card and (card["publish_status"] == "published" and (card["invited"] or card["delivery_status"] == "no_email")
                                                                 or card["status"] == "revoked" or card["invalidated"] or card["current_submission_id"])):
                continue
            if self._passed(offer):
                continue  # its times passed before it went out: flagged, and "Send a new card" offers new ones
            item = ("deliver", offer["id"])
            if not self._ready(item):
                continue
            try:
                self._deliver(offer)
            except Exception as error:
                self._failed(item, error)
                continue
            return True
        return False

    def _offer_one(self, rows):
        """An owed quiz makeup of a term whose cards are on, with no card yet and no makeup sitting: its first offer and card. Not
        while its absence is held; once released, a card the hold withdrew or kept back is followed by a new one (MAKEUP HOLDS)."""
        unchecked = None
        with self.rosters.lock(), connect(self.path) as db:
            settings = self._settings(db, sorted({r["term_id"] for r in rows}))
            quizzes, offers, holds = self.quizzes(db), self._offers(db), self._holds(db)
            cards = None
            for row in rows:
                if (row["state"] != "owed" or row["event_id"] not in quizzes or row.get("scheduled")
                        or not settings.get(row["term_id"], {}).get("cards") or self._held(holds, row)):
                    continue
                history, hold = offers.get(_key(row), []), holds.get(row["absence_id"])
                if history:
                    cards = self._cards(db) if cards is None else cards
                    if not self._reoffer(history[-1], cards.get(history[-1]["id"]), hold):
                        continue
                item = ("offer",) + _key(row)
                if not self._ready(item):
                    continue
                # SCHEDULING "GOOGLE CHECKED" (2026-10-05): a card offers no time Faculty could not check against the instructor's
                # Google calendar; it waits (no back-off: it goes out on the first turn after Google is read again).
                if unchecked is None:
                    unchecked = google_gap(self.google) or ""
                if unchecked:
                    continue
                offered = self.times(db, row)
                if not offered["options"]:
                    self._failed(item, ValueError("no time to offer"), quiet=True)  # looked at again later: the calendar may change
                    continue
                # A card after a released hold is named by that release, so each release makes at most one.
                after_hold = [f"release-{hold['revision']}"] if history else []
                request = "makeup-offer:" + hashlib.sha256("|".join([*_key(row), *after_hold]).encode()).hexdigest()[:40]
                offer = self._record_offer(db, row, offered, len(history) + 1, request, "automatic")
                break
            else:
                return False
        try:
            self._deliver(dict(offer))
        except Exception as error:
            self._failed(("deliver", offer["id"]), error)
        return True

    # 5. Copies -----------------------------------------------------------------------------------------------------------------
    def _copies_one(self, rows):
        """The named copy of each booked student (named again with their name filled in, 2026-10-18), made as soon as they are seated by their pick (or by hand on a dated sitting) and the
        quiz has an approved makeup version (COPIES, 2026-10-12): whoever of a sitting has none yet shares one print job."""
        now = campus_now()
        live = {(r["event_id"], r["section_id"], r["enrollment_id"]): r for r in rows if r["state"] == "owed"}
        with connect(self.path) as db:
            if not _has(db, "absence_makeup_picks", "exam_sittings"):
                return False
            booked = db.execute("""SELECT p.sitting_id,o.event_id,o.section_id,o.enrollment_id,o.term_id,s.date,s.time FROM absence_makeup_picks p
                JOIN absence_makeup_offers o ON o.id=p.offer_id JOIN exam_sittings s ON s.id=p.sitting_id
                JOIN exam_participants x ON x.event_id=o.event_id AND x.section_id=o.section_id AND x.enrollment_id=o.enrollment_id AND x.sitting_id=p.sitting_id
                WHERE p.outcome='seated' AND s.date>=?""", (now.date().isoformat(),)).fetchall()
            copied = self._copied(db)
            booked = [dict(b) for b in booked]
            # BOOKED BY HAND (requirement, 2026-10-16): a quiz makeup seated by hand on a dated sitting, in a term whose cards are on,
            # gets its copy too, unless that sitting already has a print job you made yourself.
            quizzes, settings = self.quizzes(db), self._settings(db, sorted({r["term_id"] for r in live.values()}))
            picked = {(b["sitting_id"], b["enrollment_id"]) for b in booked}
            printed = {r[0] for r in db.execute("""SELECT l.sitting_id FROM exam_event_batches l JOIN print_batches b ON b.id=l.batch_id
                WHERE l.sitting_id IS NOT NULL AND b.status!='cancelled' AND b.request_id NOT LIKE ?""", (COPIES + "%",))}
            for r in live.values():
                on = r.get("scheduled") or {}
                if (r["event_id"] in quizzes and settings.get(r["term_id"], {}).get("cards") and on.get("date", "") >= now.date().isoformat()
                        and (on["sitting_id"], r["enrollment_id"]) not in picked and on["sitting_id"] not in printed):
                    booked.append({"sitting_id": on["sitting_id"], "event_id": r["event_id"], "section_id": r["section_id"],
                                   "enrollment_id": r["enrollment_id"], "term_id": r["term_id"]})
            due = {}
            for b in booked:
                row = live.get((b["event_id"], b["section_id"], b["enrollment_id"]))
                if row is None or (b["sitting_id"], b["enrollment_id"]) in copied:
                    continue
                due.setdefault((b["sitting_id"], b["event_id"], b["term_id"], row["plan_id"]), []).append((b["section_id"], b["enrollment_id"]))
            # The first sitting whose quiz has an approved makeup version (until one is approved the copies wait: the group's
            # version line says what the version waits on, and each booked row that its copy waits for it).
            found = None
            for (sitting, event, term, plan), students in sorted(due.items()):
                version_id = self._version_id(db, plan) if plan else None
                if version_id and self._ready(("copies", sitting)):
                    found = (sitting, event, term, version_id, sorted(students))
                    break
        if found is None:
            return False
        sitting, event, term, version_id, students = found
        try:
            # NAMED AGAIN, NAME FILLED IN (requirement, 2026-10-18): each copy is printed for its student, whose name is printed on the paper's "Name" line
            # (DOCUMENTS "NAME FILLED IN"); whom the job was made for is still recorded here so none is printed twice.
            request = f"{COPIES}{sitting}:n" + hashlib.sha256("|".join(e for _, e in students).encode()).hexdigest()[:16]
            batch = self.exams.documents.create_batch(version_id, None, len(students), request, students=[e for _, e in students],
                                                      term_id=term, section_ids=sorted({s for s, _ in students}), actor="automatic",
                                                      fill_names=True)
            with self.rosters.lock(), connect(self.path) as db:
                db.executemany("INSERT OR IGNORE INTO absence_makeup_copies(sitting_id,enrollment_id,print_batch_id,created_at) VALUES(?,?,?,?)",
                               [(sitting, e, batch["id"], timestamp()) for _, e in students])
            current = self.exams.get(event)
            if not any(b["id"] == batch["id"] for b in current.get("batches", [])):
                self.exams.attach_batch(event, {"version": current["version"], "batch_id": batch["id"], "sitting_id": sitting})
        except Exception as error:
            self._failed(("copies", sitting), error)
            return False
        return True

    def _print_one(self, rows):
        """A booked student's makeup print job, once ready, sent to the home printer (AUTOMATIC MAKEUP PRINTS, 2026-10-18), whole
        and once: the job of a sitting not yet over, current, never sent before, or last refused or unanswered (nothing printed)
        at least PRINT_RETRY ago. An unconfirmed send is never repeated, nor one the instructor sent; nothing is sent while no
        printer is set up."""
        sends = getattr(self.exams.documents, "printer_sends", None)
        if sends is None or sends.config.printer() is None:
            return False
        today = campus_now().date().isoformat()
        with connect(self.path) as db:
            if not _has(db, "absence_makeup_copies", "print_printer_sends", "exam_sittings"):
                return False
            jobs = db.execute("""SELECT DISTINCT b.id,b.output_sha256,b.created_at FROM absence_makeup_copies m JOIN print_batches b ON b.id=m.print_batch_id
                JOIN exam_sittings t ON t.id=m.sitting_id WHERE b.status='ready' AND b.superseded_at IS NULL AND t.date>=? ORDER BY b.created_at,b.id""",
                              (today,)).fetchall()
            for job in jobs:
                tried = db.execute("""SELECT s.status,s.updated_at FROM print_printer_send_batches l JOIN print_printer_sends s ON s.id=l.send_id
                    WHERE l.batch_id=? ORDER BY s.created_at,s.id""", (job["id"],)).fetchall()
                if any(t["status"] != "failed" for t in tried):
                    continue
                if tried and (dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(tried[-1]["updated_at"])).total_seconds() < PRINT_RETRY:
                    continue
                key = ("print", job["id"])
                if not self._ready(key):
                    continue
                break
            else:
                return False
        try:
            # One request ID per attempt, so a retry after a refusal is a new send and a lost answer to this one is not.
            request = str(uuid.uuid5(uuid.NAMESPACE_URL, f"faculty:makeup-print:{job['id']}:{len(tried)}"))
            facts = sends.options("job", [job["id"]])
            sides = "two-sided-long-edge" if facts["two_sided_allowed"] else "one-sided"
            sends.submit("job", [job["id"]], [{"id": job["id"], "output_sha256": job["output_sha256"]}], sides, request, actor="automatic")
        except Exception as error:
            self._failed(key, error)
            return False
        return True

    def _version_id(self, db, plan):
        """The assignment version of the plan's newest approved makeup, or None while none is approved."""
        from .makeups import _makeup_versions
        letters = _makeup_versions(db).get(plan) or []
        if not letters:
            return None
        source = self.grading.plan(db, plan)["source_assignment_id"]
        row = db.execute("SELECT id FROM assignment_versions WHERE assignment_id=? AND label=?", (source, letters[-1]["letter"])).fetchone()
        return row[0] if row else None


FLAGS = {"no_answer", "other_time", "stale", "unseated", "paper_not_in", "no_email", "expired", "check_back"}
