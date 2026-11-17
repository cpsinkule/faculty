"""Sign in to Google for the department's makeup exam form (ABSENCES "DEPARTMENT MAKEUP EXAMS" › GOOGLE SIGN-IN; instructor,
2026-10-22: the form refused Faculty's response with 401, as it takes responses only from a signed-in TAMU Google account; offered a
pre-filled link to submit themselves or a sign-in kept in Faculty, they chose "keep a google sign in in faculty").

The AIM sign-in window (`aim_import.AimBrowser`) signing in to Google instead, as Canvas's does (`canvas/signin.py`): the private
browser starts at Google's sign-in for instructor@tamu.edu (`makeup_form.FORM_EMAIL`, typed by Faculty), Google hands over to TAMU's
sign-in, where the driver goes through the NetID, password and Duo steps exactly as for AIM (the saved Gradescope sign-in; Duo is
relayed, never answered for the instructor), and a Google page Faculty does not know is shown to the instructor. Once the form
opens signed in as that account, the browser's cookies for the form's site are KEPT in one 0600 file (`session_file`) and the
browser and its profile are discarded. Faculty then sends those cookies only to docs.google.com, only for the department's form
(read and submit), and keeps those Google renews. "Forget" deletes the file only. Nothing else of the sign-in is written or logged."""
from __future__ import annotations

import json
import logging
import os
import stat
import threading
import time
import uuid
from urllib.parse import quote, urlsplit

from flask import Blueprint, jsonify

import aim_import as aim
from private_files import write_private_json

from . import makeup_form

log = logging.getLogger(__name__)
SESSION_ENV = "FACULTY_GOOGLE_FORMS_SESSION_FILE"
CANVAS_ENV = "FACULTY_CANVAS_SESSION_FILE"  # without its own setting, the file sits beside Canvas's kept session
FILE_NAME = "google-forms-session.json"
FILE_LIMIT = 256 * 1024
SITE = "https://docs.google.com/forms/"
_LOCK = threading.RLock()


def session_file():
    """Where the kept Google session goes: FACULTY_GOOGLE_FORMS_SESSION_FILE, else beside Canvas's kept session; '' when neither."""
    named = os.environ.get(SESSION_ENV, "").strip()
    if named:
        return named if os.path.isabs(named) else ""
    canvas = os.environ.get(CANVAS_ENV, "").strip()
    return os.path.join(os.path.dirname(canvas), FILE_NAME) if canvas and os.path.isabs(canvas) else ""


def load():
    """{cookies, email, saved_at, sign_in} of the kept session, or None when there is none (or it is not a private file of this
    account: never used then)."""
    path = session_file()
    if not path:
        return None
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        return None
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077 or info.st_size > FILE_LIMIT:
            return None
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            raw = handle.read(FILE_LIMIT + 1)
    finally:
        os.close(descriptor)
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(data, dict) or not isinstance(data.get("cookies"), dict) \
            or not all(isinstance(k, str) and isinstance(v, str) for k, v in data["cookies"].items()):
        return None
    return data


def save(cookies, email, sign_in=None, saved_at=None):
    """Writes the kept session atomically, 0600, beside where it goes (never through a link)."""
    path = session_file()
    if not path:
        raise ValueError("Faculty has no place to keep a Google sign-in (FACULTY_GOOGLE_FORMS_SESSION_FILE).")
    kept = {"cookies": {k: v for k, v in sorted(cookies.items()) if k and isinstance(v, str)}, "email": email,
            "saved_at": saved_at or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "sign_in": sign_in or uuid.uuid4().hex}
    directory = os.path.dirname(path)
    os.makedirs(directory, mode=0o700, exist_ok=True)
    with _LOCK:
        write_private_json(path, kept, ".google-forms-session-")
    return kept


def renew(sign_in, changed):
    """Keeps the cookies Google renewed in an answer, only while the file still holds the sign-in they came with. Best effort."""
    if not changed:
        return
    try:
        with _LOCK:
            kept = load()
            if not kept or kept.get("sign_in") != sign_in:
                return
            save({**kept["cookies"], **changed}, kept.get("email"), sign_in=sign_in, saved_at=kept.get("saved_at"))
    except Exception as error:  # noqa: BLE001 - a session that could not be kept never turns what Google did into a failure
        log.info("The renewed Google cookies were not kept (%s)", type(error).__name__)


def forget():
    path = session_file()
    if path:
        with _LOCK:
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass


# SIGN-IN ENDED (2026-10-23: the sign-in kept on 2026-10-22 16:49Z no longer opened the form signed in a day later, while the
# page still said "signed in"): what Google last served the form to with the kept sign-in, from the last read of the form (a
# preview, a send or the page's own check). Process memory of an outside fact, one entry, replaced by each read.
_SEEN = {"sign_in": None, "served_to": None, "at": None}


def observed(sign_in, served_to):
    _SEEN.update(sign_in=sign_in, served_to=served_to, at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))


def status():
    """Whether Faculty keeps a Google sign-in for the form, as whom and since when, and whether Google still honoured it the last
    time the form was read (`ended`, with `checked_at`)."""
    kept = load()
    seen = _SEEN if kept and _SEEN["sign_in"] == kept.get("sign_in") else {}
    ended = bool(seen) and seen.get("served_to") != makeup_form.FORM_EMAIL
    return {"configured": bool(session_file()), "signed_in": bool(kept) and not ended, "ended": ended, "email": (kept or {}).get("email"),
            "saved_at": (kept or {}).get("saved_at"), "checked_at": seen.get("at"), "form_email": makeup_form.FORM_EMAIL}


def signed_in_as(page):
    """The Google account a form page was served to ('' when none): the page's own `data-user-email-address`."""
    import re
    found = re.search(r'data-user-email-address="([^"]*)"', page or "")
    return (found.group(1) if found else "").strip().casefold()


class GoogleBrowser(aim.AimBrowser):
    TARGET, NAME, FINAL = "google", "Google", ("done",)
    ENDED = "This Google sign-in has ended. Choose Sign in to Google to start again."
    FORM_URL = ""  # the term's form link the sign-in ends on (set per start)

    def __init__(self, portal, netid=None, password=None):
        self._checked = 0.0
        super().__init__(portal, netid, password, kinds=())

    def _classify(self, url, text, inputs):
        parts = urlsplit(url)
        host, path = (parts.hostname or "").lower(), parts.path.lower()
        words = " ".join((text or "").split()).lower()
        if host == "docs.google.com" and "/forms/" in path:
            return "google"
        if host == "accounts.google.com":
            if any(field.get("type") == "email" or field.get("name") == "identifier" for field in inputs):
                return "google_email"
            if not words or "loading" in words:
                return "working"
            return "unknown"  # Google's own questions (choose an account, verify it's you): shown to the instructor
        return aim.classify(url, text, inputs, "https://aim.invalid/")  # TAMU's, Microsoft's and Duo's steps, as for AIM

    def _on_google_email(self, frame, text, inputs, now):
        if self._ready("google_email", now):
            self._fill(frame, 'input[type="email"]:visible, input[name="identifier"]:visible', makeup_form.FORM_EMAIL)
            return None
        return False if now - self._since > aim.STEP_WAIT else None

    def _on_google(self, frame, text, inputs, now):
        """The form opened: kept only when Google serves it to instructor@tamu.edu. Asked at most every two seconds."""
        if now - self._checked < 2:
            return
        self._checked = now
        try:
            answer = self._context.request.get(self.portal_form, max_redirects=0, timeout=30000)
            page = answer.text() if answer.status == 200 else ""
        except Exception as error:
            log.info("The form did not answer yet (%s)", type(error).__name__)
            return
        who = signed_in_as(page)
        if who != makeup_form.FORM_EMAIL:
            self._set(show_page=True, message=(f"Google signed in as {who}, not {makeup_form.FORM_EMAIL}. Switch accounts on the page below."
                                               if who else "Google has not signed in yet. Continue on the page below."))
            return
        cookies = {c["name"]: c["value"] for c in self._context.cookies([SITE])}
        save(cookies, who)
        with self._lock:
            self.state, self.place, self.finished = "done", f"Signed in to Google as {who}", time.monotonic()
            self.result = {"user": who}
        self._closing.set()  # the browser and its profile go now; the kept session is the only thing kept

    @property
    def portal_form(self):
        return self.FORM_URL or "https://docs.google.com/forms/"

    def status(self):
        found = super().status()
        found["user"] = (self.result or {}).get("user", "") if self.state == "done" else ""
        return found


def start_url(form_url):
    """Google's sign-in for instructor@tamu.edu, continuing to the form."""
    return ("https://accounts.google.com/ServiceLogin?service=wise&passive=false&Email=" + quote(makeup_form.FORM_EMAIL)
            + "&continue=" + quote(form_url, safe=""))


def register(app, form_url):
    """`/api/absences/department-makeups/google…`: the sign-in window's routes for this browser (one sign-in at a time; blueprint
    `department_signin`, kept from the Assistant as Canvas's are), the kept sign-in's status and Forget. `form_url()` is the current
    term's form link (the page the sign-in ends on)."""
    browser = type("FormBrowser", (GoogleBrowser,), {})
    sessions = app.extensions.setdefault("department_google_sessions", aim.AimSessions(browser, GoogleBrowser.ENDED))
    api = Blueprint("department_signin", __name__, url_prefix="/api/absences/department-makeups/google")

    def portal():
        url = form_url()
        if not url:
            raise ValueError("Save this term's department makeup form link first.")
        browser.FORM_URL = url
        return start_url(url)

    aim.sign_in_routes(api, "/signin", sessions, portal)

    @api.get("")
    def status_read():
        from flask import request
        url = form_url()
        if request.args.get("check") == "1" and url and load():
            try:
                makeup_form.MakeupForm().read(url)  # records what Google served the form to (`observed`)
            except makeup_form.FormError:
                pass
        return jsonify(status())

    @api.post("/forget")
    def forget_session():
        forget()
        return jsonify(status())

    app.register_blueprint(api)
