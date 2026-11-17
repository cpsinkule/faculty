"""Outbound student mail for Response Cards: invitations, submission confirmations and exam reminders.

Mail is queued in the catalog and sent by the Inbox worker through the selected
provider: the connected Gmail by default (FACULTY_OUTBOUND_EMAIL_PROVIDER; auto
prefers a connected Gmail), or the signed Cloudflare relay that transports print
exports. The relay only carries mail; it cannot pick recipients, cards, or
content. An unconfirmed delivery is never resent automatically, and no student
address or link is ever logged.
"""
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import html
import json
import math
import os
import re
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit, urlunsplit
import uuid

from werkzeug.exceptions import Conflict, HTTPException, NotFound, ServiceUnavailable

from course_storage import timestamp
from rosters import connect
from .gmail import DeliveryError, GmailConnection, NoRedirect  # imported here by seating_student_mail.py, attachments.py and tests
from .schema import unchecked_dropped

DEFAULT_REPLY_TO = "instructor@tamu.edu"
SIGNATURE = "Professor Sinkule"
ADDRESS = re.compile(r"[A-Za-z0-9!#$%&'*+/=?^_`{|}~.-]+@[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z]{2,63}\Z")
MAX_TEXT, MAX_HTML = 20000, 40000
UIN_MARK = "[UIN confirmed]"


def valid_address(value):
    return isinstance(value, str) and value == value.strip() and len(value) <= 254 and ".." not in value and bool(ADDRESS.fullmatch(value))


def twelve_hour(value):
    """19:05 → 7:05 PM; anything else is returned unchanged."""
    match = re.fullmatch(r"([01]\d|2[0-3]):([0-5]\d)(?::[0-5]\d)?", str(value or ""))
    if not match:
        return value
    hour, minute = int(match.group(1)), match.group(2)
    return f"{hour % 12 or 12}:{minute} {'PM' if hour >= 12 else 'AM'}"


def local_time(value):
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
        try:
            from scheduling.clock import CAMPUS_ZONE
            moment = moment.astimezone(CAMPUS_ZONE)
            zone = moment.strftime("%Z")
        except Exception:  # pragma: no cover - missing tz database
            moment = moment.astimezone(timezone.utc)
            zone = "UTC"
        return f"{moment.strftime('%B')} {moment.day}, {moment.year} at {moment.strftime('%I:%M %p').lstrip('0')} {zone}"
    except (ValueError, AttributeError):
        return value or "an unknown time"


@dataclass
class MailConfig:
    url: str = ""
    secret: str = field(default="", repr=False)
    reply_to: str = DEFAULT_REPLY_TO
    provider: str = "auto"

    @classmethod
    def environment(cls):
        # The print-export relay also carries plain messages, at /message beside /send.
        relay = os.environ.get("FACULTY_PRINT_EMAIL_URL", "")
        url = ""
        try:
            parts = urlsplit(relay)
            if parts.path == "/send":
                url = urlunsplit((parts.scheme, parts.netloc, "/message", "", ""))
        except ValueError:
            url = ""
        reply_to = os.environ.get("FACULTY_MAIL_REPLY_TO", "").strip() or DEFAULT_REPLY_TO
        return cls(url, os.environ.get("FACULTY_PRINT_EMAIL_SECRET", ""), reply_to if valid_address(reply_to) else DEFAULT_REPLY_TO,
                   os.environ.get("FACULTY_OUTBOUND_EMAIL_PROVIDER", "auto").strip().lower())

    @property
    def enabled(self):
        try:
            parsed = urlsplit(self.url)
            return bool(parsed.scheme == "https" and parsed.hostname and parsed.path == "/message" and not parsed.username
                        and not parsed.password and not parsed.query and not parsed.fragment and len(self.secret) >= 32)
        except ValueError:
            return False


RELAY_PAUSE = 300
# RESPONSE-CARDS "REMINDERS BEFORE THE EXAM": days before a seating chart's exam date, and the campus hour from which they go out.
REMINDER_DAYS, REMINDER_HOUR = (3, 2, 1), 9
MAX_RATE_LIMIT_WAIT = 86400


class RelayRateLimit(DeliveryError):
    """The signed relay explicitly rejected the message before acceptance."""
    def __init__(self, delay):
        if type(delay) is not int or not 1 <= delay <= MAX_RATE_LIMIT_WAIT:
            raise ValueError("Invalid relay retry interval")
        super().__init__("The email service has temporarily paused sending; queued mail will wait before another attempt.",
                         unavailable=True, retry_after=delay)


def rate_limit_wait(error):
    """Accept only the relay's exact known-not-accepted rate-limit contract."""
    def unique_object(pairs):
        result = dict(pairs)
        if len(result) != len(pairs):
            raise ValueError("Duplicate response fields")
        return result

    try:
        raw = error.read(16385)
        if len(raw) > 16384:
            return 0
        result = json.loads(raw, object_pairs_hook=unique_object)
        if not isinstance(result, dict) or set(result) != {"error", "retry_after_seconds"} or result["error"] != "rate_limited":
            return 0
        delay = result["retry_after_seconds"]
        if type(delay) is not int or not 1 <= delay <= MAX_RATE_LIMIT_WAIT:
            return 0
        retry_after = error.headers.get_all("Retry-After", [])
        if len(retry_after) != 1 or retry_after[0].strip() != str(delay):
            return 0
        return delay
    except Exception:
        return 0


def paragraphs(text):
    return "".join(f"<p>{html.escape(block).replace(chr(10), '<br>')}</p>" for block in text.split("\n\n") if block.strip())


def linked_paragraphs(text, link):
    return paragraphs(text).replace(html.escape(link), f'<a href="{html.escape(link, quote=True)}">{html.escape(link)}</a>', 1)


def provider_pauses(db):
    """Share durable provider quotas across response cards and student PDFs."""
    tables = ['response_card_mail']
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='seating_student_mail'").fetchone():
        tables.append('seating_student_mail')
    result = {}
    for table in tables:
        for row in db.execute(f"""SELECT CASE WHEN provider IN ('resend','relay') THEN 'relay' ELSE provider END AS transport,
                 MAX(not_before) AS deadline FROM {table} WHERE status='pending' GROUP BY transport"""):
            result[row['transport']] = max(result.get(row['transport'], 0), row['deadline'])
    return result


class CardMailer:
    def __init__(self, service, config=None, gmail=None):
        self.service = service
        self.rosters, self.path = service.rosters, service.path
        self.config = config or MailConfig.environment()
        self.gmail = gmail or GmailConnection.environment(SIGNATURE)
        self.transport = self._post  # replaced by tests; never sends otherwise

    @property
    def provider(self):
        """Workspace configuration selects the transport; auto retains the legacy preference."""
        if self.config.provider in ("resend", "relay"):
            return self.config.provider if self.config.enabled else None
        if self.config.provider not in ("auto", "gmail"):
            return None
        if self.gmail.connected:
            return "gmail"
        if self.config.provider == "gmail":
            return None
        return "relay" if self.config.enabled else None

    @property
    def enabled(self):
        return self.provider is not None

    def status(self):
        provider = self.provider
        return {"provider": provider, "reply_to": self.config.reply_to, "relay": self.config.enabled, "gmail": self.gmail.status(),
                "sender": f"{SIGNATURE} <{self.gmail.email}>" if provider == "gmail" else
                          (f"{SIGNATURE} <exam@example.com>" if provider else None)}

    def resolve_provider(self, mail_provider="default"):
        """Validate this send's choice without changing the workspace default."""
        if mail_provider not in ("default", "gmail"):
            raise ValueError("Choose default or gmail for mail_provider.")
        if self.config.provider not in ("auto", "gmail", "relay", "resend"):
            raise ServiceUnavailable("The workspace email provider is not configured correctly.")
        if mail_provider == "gmail":
            if not self.gmail.connected:
                raise ServiceUnavailable("Connect Gmail in Response Cards before choosing Gmail delivery.")
            return "gmail"
        return self.provider

    def _require_provider(self, mail_provider):
        provider = self.resolve_provider(mail_provider)
        if provider is None:
            raise ServiceUnavailable("Email sending is not configured, so cards cannot be emailed.")
        return provider

    # -- Composition ---------------------------------------------------------
    def _context(self, db, card):
        issue = db.execute("SELECT * FROM response_card_issues WHERE id=?", (card["issue_id"],)).fetchone()
        term = db.execute("SELECT code,name FROM course_terms WHERE id=?", (issue["term_id"],)).fetchone()
        assignment = db.execute("SELECT title FROM assignments WHERE id=?", (issue["assignment_id"],)).fetchone() if issue["assignment_id"] else None
        return {"code": term["code"], "course_name": term["name"] or "", "assignment": assignment["title"] if assignment else "",
                "link": f"{card['published_url']}/r/{card['token']}" if card["published_url"] else "",
                "schema": json.loads(card["snapshot_json"])["schema"], "context": json.loads(issue["context_json"]),
                "issued_at": issue["created_at"]}

    def invitation(self, db, card, replacement=False, resend=False):
        # Keep update intent on every route (issue send, individual resend, and
        # Gmail export/send), even when the caller did not supply replacements.
        template = db.execute("""SELECT t.name FROM response_card_issues i
            JOIN response_card_versions v ON v.id=i.version_id
            JOIN response_card_templates t ON t.id=v.template_id WHERE i.id=?""", (card['issue_id'],)).fetchone()
        replacement = replacement or bool(template and template['name'] == 'Updated exam seat assignment (Faculty)')
        if not replacement and db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='seating_card_updates'").fetchone():
            replacement = bool(db.execute("""SELECT 1 FROM seating_card_updates u WHERE u.card_id=? AND EXISTS (
                SELECT 1 FROM response_card_invalidations x JOIN response_card_recipients r ON r.id=x.card_id
                JOIN response_card_issues i ON i.id=r.issue_id
                WHERE i.seating_chart_id=u.chart_id AND r.enrollment_id=u.enrollment_id)""", (card['id'],)).fetchone())
        c = self._context(db, card)
        course = c["code"] + (f" ({c['course_name']})" if c["course_name"] and c["course_name"] != c["code"] else "")
        if c["context"].get("makeup_offer_id"):
            return self._booking_invitation(db, card, c, course, resend)
        subject = f"IMPORTANT: Information about your upcoming {c['assignment']} for {c['code']}" if c["assignment"] else f"IMPORTANT: A response card for {c['code']}"
        if replacement:
            subject = "UPDATED: " + subject.split(": ", 1)[1]
        about = f"your upcoming {c['assignment']} for {course}" if c["assignment"] else f"{course}"
        changed = ("The information changed since your earlier response card; this new card replaces it, "
                   "and the earlier link no longer works. ") if replacement else ""
        if resend:
            changed += "This is a second copy of an earlier email, sent again in case the first did not reach you; the link is the same. "
        # Invitations carry the private card link, never a roster-name greeting.
        text = ("Hello,\n\n"
                f"{changed}Important information about {about} is waiting for you on a short response card. "
                "You must open it, read it carefully, and complete every item on it:\n\n"
                f"{c['link']}\n\n"
                "Completing the card takes about a minute. The link is personal to you, so please do not forward it. "
                "Once you submit the card, you will receive an email confirming what you entered.\n\n"
                "If you have questions, reply to this email.\n\n"
                f"{SIGNATURE}")
        body = linked_paragraphs(text, c["link"])
        return {"subject": subject[:300], "text": text, "html": body}

    def _booking_invitation(self, db, card, c, course, resend):
        """A quiz makeup booking card's invitation (RESPONSE-CARDS "ISSUE CHOICES"; requirement, 2026-10-06).
        A card after an earlier booking card for the same student and quiz ("Send a new card") is UPDATED and says it replaces that
        one. It never mentions the card's "None of these work" choice (ASSISTANT: a reply asks which time works, so the student does
        not feel invited to reject them)."""
        earlier = db.execute("""SELECT 1 FROM response_card_issues i JOIN response_card_recipients r ON r.issue_id=i.id
            WHERE i.id!=? AND i.created_at<? AND json_extract(i.context_json,'$.makeup_offer_id') IS NOT NULL
            AND json_extract(i.context_json,'$.exam_event_id')=? AND r.section_id=? AND r.enrollment_id=?""",
                             (card["issue_id"], c["issued_at"], c["context"].get("exam_event_id"), card["section_id"], card["enrollment_id"])).fetchone()
        quiz = c["assignment"] or "quiz"
        subject = f"{'UPDATED' if earlier else 'IMPORTANT'}: Book your {quiz} makeup for {c['code']}"
        changed = "This card offers new times and replaces the one sent earlier. " if earlier else ""
        if resend:
            changed += "This is a second copy of an earlier email, sent again in case the first did not reach you; the link is the same. "
        text = ("Hello,\n\n"
                f"{changed}You missed {quiz} for {course} with an excused absence, so you will take a makeup. "
                "Choose when on this short response card:\n\n"
                f"{c['link']}\n\n"
                "The card closes when its last time begins, so choose soon. It takes about a minute. "
                "The link is personal to you, so please do not forward it. "
                "Once you submit the card, you will receive an email confirming your choice.\n\n"
                "If you have questions, reply to this email.\n\n"
                f"{SIGNATURE}")
        body = linked_paragraphs(text, c["link"])
        return {"subject": subject[:300], "text": text, "html": body}

    def confirmation(self, db, card, submission):
        c = self._context(db, card)
        course = c["code"]
        subject = f"Confirmed: your responses for {c['assignment']} ({course})" if c["assignment"] else f"Confirmed: your response card for {course}"
        submission = unchecked_dropped(submission)  # RESPONSE-CARDS "DR LOCATION IS NOT CHECKED"
        answers, checks = submission.get("answers") or {}, submission.get("verification") or {}
        items, flagged = [], False
        for item in c["schema"].get("fields", []):
            if item.get("type") == "static" or item["id"] not in answers:
                continue
            value = answers[item["id"]]
            if (item.get("verification") or {}).get("mode") == "uin-digest" or value == UIN_MARK:
                shown = "confirmed"
            elif isinstance(value, bool):
                shown = "Yes" if value else "No"
            elif isinstance(value, list):
                shown = ", ".join(str(v) for v in value) or "(none)"
            elif item.get("type") == "time":
                shown = str(twelve_hour(value))
            else:
                shown = str(value)
            check = checks.get(item["id"])
            matched = check.get("matched") if isinstance(check, dict) else (True if check is None else bool(check))
            note = ""
            if matched is False:
                flagged = True
                note = " (differs from our records)"
            label = " ".join(str(item.get("label") or item["id"]).split())
            if label.endswith("."):
                label = label[:-1]
            items.append(f"- {label}{'' if label.endswith((':', '?')) else ':'} {shown}{note}")
        when = local_time(submission.get("submitted_at", ""))
        what = f"{c['assignment']} ({course})" if c["assignment"] else course
        text = f"Hello,\n\nThis confirms the response card you submitted on {when} for {what}.\n\n"
        if c["schema"].get("description"):
            text += f"The card stated:\n\n{c['schema']['description']}\n\n"
        text += "You confirmed:\n\n" + ("\n".join(items) if items else "- (no items)") + "\n\n"
        if flagged:
            text += ("Note: one or more of your answers differ from our records (marked above). "
                     f"{SIGNATURE} will review them and contact you if anything needs to change.\n\n")
        text += f"If any of this is wrong, reply to this email right away.\n\n{SIGNATURE}"
        return {"subject": subject[:300], "text": text, "html": paragraphs(text)}

    def reminder(self, db, card, days, exam_date):
        """A reminder to a student whose seating card still awaits their response (RESPONSE-CARDS "REMINDERS BEFORE THE EXAM")."""
        c = self._context(db, card)
        course = c["code"] + (f" ({c['course_name']})" if c["course_name"] and c["course_name"] != c["code"] else "")
        exam = c["assignment"] or "exam"
        day = datetime.strptime(exam_date, "%Y-%m-%d")
        when = "tomorrow" if days == 1 else f"in {days} days"
        subject = f"REMINDER: Complete your {exam} response card for {c['code']}"
        text = ("Hello,\n\n"
                f"Your {exam} for {course} is {when}, on {day.strftime('%A, %B')} {day.day}. "
                "You have not completed your response card yet. It tells you your exam room and seat. "
                "Please open it, read it carefully, and complete every item:\n\n"
                f"{c['link']}\n\n"
                "It takes about a minute. The link is personal to you, so please do not forward it. "
                "Once you submit the card, you will receive an email confirming what you entered"
                f"{', and these reminders will stop' if days > 1 else ''}.\n\n"
                "If you have questions, reply to this email.\n\n"
                f"{SIGNATURE}")
        body = linked_paragraphs(text, c["link"])
        return {"subject": subject[:300], "text": text, "html": body}

    # -- Queueing ------------------------------------------------------------
    def _queue(self, db, card, kind, submission_id, message, now, provider=None, gmail_sender=None):
        # Each envelope belongs to its existing card and retains the transport
        # chosen when it was queued, independent of later workspace settings.
        if db.execute('SELECT 1 FROM response_card_invalidations WHERE card_id=?', (card['id'],)).fetchone():
            raise Conflict('This card was replaced after a seat change and cannot be emailed.')
        provider = provider or self.provider or ({"auto": "relay", "gmail": "gmail", "relay": "relay", "resend": "resend"}.get(self.config.provider, ""))
        gmail_sender = (self.gmail.email if gmail_sender is None else gmail_sender) if provider == "gmail" else ""
        db.execute("""INSERT INTO response_card_mail(id,card_id,kind,submission_id,recipient,subject,text_body,html_body,status,provider,gmail_sender,created_at,updated_at)
                      VALUES(?,?,?,?,?,?,?,?,'pending',?,?,?,?)""",
                   (str(uuid.uuid4()), card["id"], kind, submission_id, card["email"], message["subject"],
                    message["text"][:MAX_TEXT], message["html"][:MAX_HTML], provider, gmail_sender, now, now))

    def _validate_exam_context(self, db, issue_id, card=None):
        """Revalidate released feedback/decisions before distributing their link.

        The caller holds the lifecycle lock, with no catalog write transaction:
        grading can need verified roster files or a pending storage restoration.
        No personalized values or credentials are returned or logged.
        """
        workflow = getattr(self.service, "exam_workflow", None)
        if workflow is None:
            return False
        row = db.execute("SELECT context_json FROM response_card_issues WHERE id=?", (issue_id,)).fetchone()
        if row is None:
            return False
        context = json.loads(row["context_json"])
        if not context.get("exam_event_id"):
            return False
        workflow.issue_context(db, context)
        recipients = [card] if card is not None else [dict(r) for r in db.execute(
            "SELECT section_id,enrollment_id FROM response_card_recipients WHERE issue_id=?", (issue_id,))]
        for recipient in recipients:
            workflow.issue_context(db, context, recipient)
        return True

    def queue_invitations(self, issue_id, replacements=(), mail_provider="default"):
        """Queue one invitation per published card that has none yet. Returns queued and no-address counts.

        `replacements` lists enrollment IDs whose earlier card this issue supersedes;
        their message says so and is marked UPDATED.
        """
        queued, no_email = 0, 0
        replacements = set(replacements)
        with self.rosters.lock(), connect(self.path) as db:
            provider = self._require_provider(mail_provider)
            self._validate_exam_context(db, issue_id)
            rows = db.execute("""SELECT r.* FROM response_card_recipients r WHERE r.issue_id=? AND r.publish_status='published'
                                 AND NOT EXISTS (SELECT 1 FROM response_card_mail m WHERE m.card_id=r.id AND m.kind='invitation') ORDER BY r.id""",
                              (issue_id,)).fetchall()
            cards = []
            for row in rows:
                card = dict(row)
                if self.awaiting_response(db, card):
                    continue
                self.service.validate_card_access(db, card)
                cards.append(card)
            db.execute("BEGIN IMMEDIATE")
            now = timestamp()
            for card in cards:
                if not valid_address(card["email"]):
                    db.execute("UPDATE response_card_recipients SET delivery_status='no_email',updated_at=? WHERE id=?", (now, card["id"]))
                    no_email += 1
                    continue
                self._queue(db, card, "invitation", "", self.invitation(db, card, card["enrollment_id"] in replacements), now, provider)
                db.execute("UPDATE response_card_recipients SET delivery_status='queued',updated_at=? WHERE id=?", (now, card["id"]))
                queued += 1
        return {"queued": queued, "no_email": no_email}

    @staticmethod
    def awaiting_response(db, card):
        """Why one card's link no longer serves a response, or "" while the card still awaits one."""
        if card["publish_status"] != "published":
            return "This card is not published yet."
        if db.execute('SELECT 1 FROM response_card_invalidations WHERE card_id=?', (card['id'],)).fetchone():
            return "This card was replaced after a seat change; use its replacement card."
        if card["status"] == "revoked":
            return "This card was revoked; re-confirm the seating chart to issue a new one."
        if card["status"] in ("submitted", "verified", "needs_review"):
            return "This student has already responded to the card."
        issue = db.execute("SELECT policy,due_at,expires_at FROM response_card_issues WHERE id=?", (card["issue_id"],)).fetchone()
        cutoff = issue["expires_at"] or (issue["due_at"] if issue["policy"] == "until_due" else None) if issue else None
        if card["status"] == "expired" or (cutoff and datetime.fromisoformat(cutoff.replace("Z", "+00:00")) <= datetime.now(timezone.utc)):
            return "This card has expired, so its link no longer works."
        return ""

    def resend_block(self, db, card):
        """Why one card's invitation cannot be emailed again right now, or "" when it can.

        Only a published card that is still awaiting a response, has a roster
        address, and has no invitation already waiting qualifies. Nothing here
        resends on its own; the instructor asks per card.
        """
        if card["publish_status"] != "published":
            return "Publish this card before emailing it."
        reason = self.awaiting_response(db, card)
        if reason:
            return reason
        if not valid_address(card["email"]):
            return "No email address on the roster for this student; share the link another way."
        if db.execute("SELECT 1 FROM response_card_mail WHERE card_id=? AND kind='invitation' AND status IN ('pending','sending')", (card["id"],)).fetchone():
            return "An email for this card is already waiting to send."
        return ""

    def queue_resend(self, card_id, mail_provider="default"):
        """Queue one more copy of a pending card's invitation at the instructor's request; the Inbox worker sends it."""
        with self.rosters.lock(), connect(self.path) as db:
            provider = self._require_provider(mail_provider)
            row = db.execute("SELECT * FROM response_card_recipients WHERE id=?", (card_id,)).fetchone()
            if row is None:
                raise NotFound("This response card no longer exists.")
            card = dict(row)
            self.service.validate_card_access(db, card)
            self._validate_exam_context(db, card["issue_id"], card)
            db.execute("BEGIN IMMEDIATE")
            reason = self.resend_block(db, card)
            if reason:
                raise Conflict(reason)
            earlier = db.execute("SELECT COUNT(*) FROM response_card_mail WHERE card_id=? AND kind='invitation'", (card["id"],)).fetchone()[0]
            now = timestamp()
            self._queue(db, card, "invitation", "", self.invitation(db, card, resend=earlier > 0), now, provider)
            db.execute("UPDATE response_card_recipients SET delivery_status='queued',updated_at=? WHERE id=?", (now, card["id"]))
        return {"card_id": card["id"], "delivery_status": "queued", "copies": earlier + 1}

    def queue_confirmations(self):
        """Queue a confirmation for every accepted submission that has none yet."""
        queued = 0
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute("""SELECT s.id AS submission_id, s.submission_json, r.* FROM response_card_submissions s
                                 JOIN response_card_recipients r ON r.id=s.card_id
                                 WHERE NOT EXISTS (SELECT 1 FROM response_card_mail m WHERE m.card_id=r.id AND m.kind='confirmation' AND m.submission_id=s.id)
                                 AND NOT EXISTS (SELECT 1 FROM response_card_invalidations v WHERE v.card_id=r.id)
                                 ORDER BY s.submitted_at, s.id""").fetchall()
            now = timestamp()
            for row in rows:
                card = dict(row)
                submission = json.loads(card.pop("submission_json"))
                submission_id = card.pop("submission_id")
                if not valid_address(card["email"]):
                    db.execute("""INSERT INTO response_card_mail(id,card_id,kind,submission_id,recipient,subject,text_body,html_body,status,error,created_at,updated_at)
                                  VALUES(?,?,'confirmation',?,'','','','','failed','No email address on the roster.',?,?)""",
                               (str(uuid.uuid4()), card["id"], submission_id, now, now))
                    continue
                self._queue(db, card, "confirmation", submission_id, self.confirmation(db, card, submission), now)
                queued += 1
        return {"queued": queued}

    def queue_reminders(self, now=None):
        """RESPONSE-CARDS "REMINDERS BEFORE THE EXAM" (requirement, 2026-10-15): from 9:00 am campus time on each of those days, every card issued from
        a seating chart with that exam date that still awaits a response, and whose invitation was sent more than 12 hours earlier,
        gets one reminder, through the transport its invitation used (Gmail while it is still connected). A day missed entirely is
        not made up, and a reminder is never queued twice (a unique key per card and day)."""
        from scheduling.clock import CAMPUS_ZONE
        moment = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        local = moment.astimezone(CAMPUS_ZONE)
        if local.hour < REMINDER_HOUR:
            return {"queued": 0, "no_email": 0}
        dates = {(local.date() + timedelta(days=days)).isoformat(): days for days in REMINDER_DAYS}
        recent = (moment - timedelta(hours=12)).isoformat()
        queued = no_email = 0
        with self.rosters.lock(), connect(self.path) as db:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='seating_charts'").fetchone():
                return {"queued": 0, "no_email": 0}
            rows = [dict(row) for row in db.execute(f"""SELECT r.*, c.exam_date FROM response_card_recipients r
                JOIN response_card_issues i ON i.id=r.issue_id JOIN seating_charts c ON c.id=i.seating_chart_id
                WHERE c.exam_date IN ({','.join('?' * len(dates))}) AND r.publish_status='published' AND r.status IN ('issued','opened','started')
                AND NOT EXISTS(SELECT 1 FROM response_card_invalidations x WHERE x.card_id=r.id)
                AND EXISTS(SELECT 1 FROM response_card_mail m WHERE m.card_id=r.id AND m.kind='invitation' AND m.status='accepted')
                AND NOT EXISTS(SELECT 1 FROM response_card_mail m WHERE m.card_id=r.id AND m.kind='invitation' AND m.created_at>?)
                ORDER BY r.id""", (*dates, recent))]
            db.execute("BEGIN IMMEDIATE")
            now_text = timestamp()
            for card in rows:
                exam_date = card.pop("exam_date")
                days = dates[exam_date]
                if db.execute("SELECT 1 FROM response_card_mail WHERE card_id=? AND kind='reminder' AND submission_id=?", (card["id"], str(days))).fetchone():
                    continue
                if self.awaiting_response(db, card):
                    continue
                if not valid_address(card["email"]):
                    no_email += 1
                    continue
                used = db.execute("""SELECT provider,gmail_sender FROM response_card_mail WHERE card_id=? AND kind='invitation' AND status='accepted'
                    ORDER BY created_at DESC, id DESC LIMIT 1""", (card["id"],)).fetchone()
                provider = used["provider"] if used and used["provider"] else self.provider
                sender = (used["gmail_sender"] or self.gmail.email) if used and provider == "gmail" else ""
                if provider is None:
                    continue
                try:
                    # RESPONSE-CARDS: reminders keep the invitation's transport and Gmail account.
                    self._check_delivery_provider(provider, sender)
                except DeliveryError:
                    continue
                self._queue(db, card, "reminder", str(days), self.reminder(db, card, days, exam_date), now_text, provider, sender)
                queued += 1
        return {"queued": queued, "no_email": no_email}

    # -- Sending -------------------------------------------------------------
    def process_one(self):
        """Send one pending envelope, serialized with seat changes and revocations."""
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            now = timestamp()
            interrupted = [dict(row) for row in db.execute("SELECT * FROM response_card_mail WHERE status='sending'")]
            for job in interrupted:
                accepted = bool(job["provider_receipt_json"])
                self._finish(db, job, "accepted" if accepted else "unconfirmed", "" if accepted else "Sending was interrupted; delivery is unconfirmed.")
            # Older installations did not retain the chosen provider. Bind
            # their pending envelopes once, when a default becomes available.
            if self.provider:
                db.execute("UPDATE response_card_mail SET provider=?,gmail_sender=? WHERE status='pending' AND provider=''",
                           (self.provider, self.gmail.email if self.provider == "gmail" else ""))
            # A quota holds only its own transport. Resend and relay share the
            # same endpoint; Gmail has a separate queue pause. Deadlines survive
            # worker restarts and apply to new mail for that transport too.
            pauses = provider_pauses(db)
            instant = time.time()
            ready = [provider for provider in ("gmail", "resend", "relay")
                     if instant >= pauses.get("relay" if provider in ("resend", "relay") else provider, 0)]
            row = db.execute("SELECT * FROM response_card_mail WHERE status='pending' AND provider IN (" +
                             ",".join("?" for _ in ready) + ") ORDER BY created_at,id LIMIT 1", ready).fetchone() if ready else None
            if row is None:
                return bool(interrupted)
            job = dict(row)
            db.execute("UPDATE response_card_mail SET status='sending',attempts=attempts+1,not_before=0,updated_at=? WHERE id=?", (now, job["id"]))
            db.commit()
            try:
                card = db.execute("SELECT * FROM response_card_recipients WHERE id=?", (job["card_id"],)).fetchone()
                try:
                    if card is None:
                        raise Conflict("This response card no longer exists.")
                    card = dict(card)
                    # Historical submissions are retained, but an obsolete seat
                    # must not be announced by a late invitation or receipt.
                    if db.execute('SELECT 1 FROM response_card_invalidations WHERE card_id=?', (card['id'],)).fetchone():
                        raise Conflict('This card was replaced after a seat change.')
                    if job['kind'] == 'reminder' and self.awaiting_response(db, card):
                        # The student answered (or the card closed) after the reminder was queued: it is no longer needed.
                        db.execute("BEGIN IMMEDIATE")
                        self._finish(db, job, "cancelled", "Reminder not needed: " + self.awaiting_response(db, card))
                        return True
                    if job['kind'] in ('invitation', 'reminder'):
                        self.service.validate_card_access(db, card)
                        reason = self.awaiting_response(db, card)
                        if reason:
                            raise Conflict(reason)
                    self._validate_exam_context(db, card['issue_id'], card)
                except Exception as error:
                    detail = error.description if isinstance(error, HTTPException) else "The current exam records could not be verified."
                    db.execute("BEGIN IMMEDIATE")
                    self._finish(db, job, "failed", "Exam notice was not sent: " + detail)
                    return True
                self._check_delivery_provider(job["provider"], job["gmail_sender"])
                receipt = self.transport(job)
            except DeliveryError as error:
                if error.unavailable:
                    # Only a definite non-send is eligible for automatic retry.
                    not_before = math.ceil(time.time()) + (error.retry_after or RELAY_PAUSE)
                    db.execute("UPDATE response_card_mail SET status='pending',error=?,not_before=?,updated_at=? WHERE id=?",
                               (str(error)[:2000], not_before, timestamp(), job["id"]))
                    return False
                self._finish(db, job, "unconfirmed" if error.uncertain else "failed", str(error))
            except Exception:
                self._finish(db, job, "unconfirmed", "Delivery could not be confirmed.")
            else:
                db.execute("UPDATE response_card_mail SET provider_receipt_json=?,updated_at=? WHERE id=?", (json.dumps(receipt), timestamp(), job["id"]))
                db.commit()  # Recovery sees accepted delivery even if completion is interrupted.
                self._finish(db, job, "accepted", "")
        return True

    @staticmethod
    def _finish(db, job, status, error):
        now = timestamp()
        db.execute("UPDATE response_card_mail SET status=?,error=?,updated_at=? WHERE id=?", (status, error[:2000], now, job["id"]))
        if job["kind"] == "invitation":
            delivery = {"accepted": "sent", "failed": "failed", "unconfirmed": "unconfirmed"}[status]
            db.execute("UPDATE response_card_recipients SET delivery_status=?,updated_at=? WHERE id=?", (delivery, now, job["card_id"]))

    def _post(self, job):
        payload = {"mail_id": job["id"], "recipient": job["recipient"], "subject": job["subject"],
                   "text": job["text_body"], "html": job["html_body"], "reply_to": self.config.reply_to}
        return self.deliver(payload, provider=job["provider"], gmail_sender=job["gmail_sender"])

    def _check_delivery_provider(self, provider, gmail_sender=""):
        if self.config.provider not in ("auto", "gmail", "relay", "resend"):
            raise DeliveryError("The workspace email provider is not configured correctly.", unavailable=True)
        if provider == "gmail":
            if not self.gmail.connected:
                raise DeliveryError("Reconnect Gmail in Response Cards; this message is waiting for Gmail.", unavailable=True)
            if gmail_sender and self.gmail.email.casefold() != gmail_sender.casefold():
                raise DeliveryError("Reconnect the Gmail account chosen for this message before sending.", unavailable=True)
        elif provider not in ("relay", "resend") or not self.config.enabled:
            raise DeliveryError("Email sending is not configured for this message's selected provider.", unavailable=True)

    def deliver(self, payload, provider=None, gmail_sender=""):
        """Send through the envelope's provider, or the default for an immediate test."""
        provider = self.provider if provider is None else provider
        self._check_delivery_provider(provider, gmail_sender)
        if provider == "gmail":
            return self.gmail.send(payload, expected_email=gmail_sender or self.gmail.email)
        if provider in ("relay", "resend"):
            return self.relay(payload)
        raise DeliveryError("Email sending is not configured.", unavailable=True)

    def relay(self, payload):
        """Sign and post one message envelope to the Cloudflare relay. Returns the provider receipt."""
        body = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        stamp = str(int(time.time()))
        signature = hmac.new(self.config.secret.encode(), stamp.encode() + b"." + body, hashlib.sha256).hexdigest()
        request = urllib.request.Request(self.config.url, data=body, headers={
            "Content-Type": "application/json", "User-Agent": "Faculty-Response-Cards/1.0",
            "X-Faculty-Timestamp": stamp, "X-Faculty-Signature": signature}, method="POST")
        try:
            with urllib.request.build_opener(NoRedirect()).open(request, timeout=45) as response:
                raw = response.read(16385)
                if response.status != 202 or len(raw) > 16384:
                    raise ValueError("Invalid delivery response")
                receipt = json.loads(raw)
                if (receipt.get("mail_id") != payload["mail_id"] or receipt.get("status") != "accepted"
                        or not isinstance(receipt.get("message_id"), str) or not 1 <= len(receipt["message_id"]) <= 1000):
                    raise ValueError("Invalid delivery acknowledgement")
                return {key: receipt[key] for key in ("mail_id", "status", "message_id")}
        except urllib.error.HTTPError as error:
            if error.code == 404:
                raise DeliveryError("The mail relay has no message route yet; deploy the updated print-export Worker.", unavailable=True) from None
            if error.code == 429:
                delay = rate_limit_wait(error)
                if delay:
                    raise RelayRateLimit(delay) from None
            definite = error.code in (400, 401, 403, 413, 429)
            raise DeliveryError("The email service rejected this message. Check the address or the sending configuration." if definite else
                                "Delivery could not be confirmed.", not definite) from None
        except Exception:
            raise DeliveryError("Delivery could not be confirmed.", True) from None

    def send_test(self, recipient, mail_provider="default"):
        """Send one plain test message right now, outside the card queue (instructor addresses only)."""
        if not valid_address(recipient):
            raise ValueError("Enter one valid email address.")
        provider = self._require_provider(mail_provider)
        text = ("This is a test message from Faculty's response card mailer. No student was emailed.\n\n"
                f"Sent {local_time(timestamp())}.\n\n{SIGNATURE}")
        return self.deliver({"mail_id": str(uuid.uuid4()), "recipient": recipient, "subject": "Faculty response card mail test",
                             "text": text, "html": paragraphs(text), "reply_to": self.config.reply_to}, provider=provider,
                            gmail_sender=self.gmail.email if provider == "gmail" else "")

    def history(self, db, card_id):
        return [dict(row) for row in db.execute("SELECT kind,submission_id,status,error,attempts,not_before,provider,gmail_sender,created_at,updated_at FROM response_card_mail WHERE card_id=? ORDER BY created_at,id", (card_id,))]
