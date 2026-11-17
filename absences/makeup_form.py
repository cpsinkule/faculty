"""The Math department's makeup exam request form, a public Google Form (ABSENCES "DEPARTMENT MAKEUP EXAMS"; instructor,
2026-10-22: "Would it be possible to automate this google form. It is required to fill out for students that have excused absences
for exams"; asked, they chose a Follow-up card they approve, "make sure it records my tamu email on the form").

Three things, nothing else: read the form (its questions, the department's makeup sessions and each question's choices), submit
one filled-in response, and read the receipt Google emails for a response. Every call goes to docs.google.com only and follows no
redirect. The form takes a response only from a signed-in TAMU Google account (2026-10-22: Faculty's first response was refused
with 401), so every call carries the kept Google sign-in (absences/google_signin.py) and a response is sent only when the form
was served to instructor@tamu.edu. `MakeupForm.http` and `.session` are replaced by tests
(tests/test_department_makeups.py); nothing else reaches Google.

The form is read by its questions' TITLES, never by position: a question Faculty does not know that the form REQUIRES, or a
question Faculty answers that is gone, stops a submission (`FormChanged`) rather than guessing. The email the form records is
the instructor's TAMU address, `FORM_EMAIL` (requirement, 2026-10-22), typed into the form's own Email field."""
from __future__ import annotations

import datetime as dt
import html
import json
import re
import socket
import urllib.error
import urllib.parse
import urllib.request

HOST = "docs.google.com"
FORM_EMAIL = "instructor@tamu.edu"  # 
INSTRUCTOR = ("Sinkule", "Christopher")  # (last, first) as they enter it on the form
RECEIPT_SENDER = "forms-receipts-noreply@google.com"
USER_AGENT = "Faculty/1.0 (instructor's makeup exam requests)"
MAX_BODY = 4_000_000
URL = re.compile(r"https://docs\.google\.com/forms/d/e/([A-Za-z0-9_-]{20,120})/(?:viewform|formResponse)(?:\?[^#\s]*)?")

# The questions Faculty answers, by key: the start of each title, compared without case, punctuation or extra spaces.
QUESTIONS = {
    "instructor": "you must be the instructor of the course",
    "last": "instructors last name",
    "first": "instructors first name",
    "course": "math course number",
    "section": "section number",
    "student_last": "students last name",
    "student_first": "students first name",
    "uin": "students uin",
    "session": "select the date this student will take",
    "time": "time allotted",
    "scantron": "scantron type",
    "documentation": "attach excuse",
    "calculator": "information about calculators",
    "reset": "reset calculator memory",
    "instructions": "any special instructions",
}
# What the instructor chooses (the card's dropdowns); the rest Faculty fills from its records.
RULES = ("time", "scantron", "documentation", "calculator", "reset")
LABELS = {"session": "Makeup session", "time": "Time allotted", "scantron": "Scantron", "documentation": "Documentation attached",
          "calculator": "Calculators", "reset": "Reset calculator memory", "instructions": "Special instructions"}
OTHER = "__other_option__"
TYPES = {0: "short", 1: "paragraph", 2: "radio", 3: "dropdown", 4: "checkbox", 5: "scale", 6: "text", 7: "grid", 8: "section", 9: "date",
         10: "time", 11: "image", 12: "video", 13: "file"}
SESSION = re.compile(r"^\s*(?:[A-Za-z]+day),\s*([A-Za-z]+ \d{1,2}, \d{4})\s+(\d{1,2}:\d{2}\s*[AP]M)\s*-\s*(\d{1,2}:\d{2}\s*[AP]M)\s*;\s*([^;]+?)\s*(?:;\s*(.*))?$", re.I)
MINUTES = re.compile(r"^\s*(\d+)\s*(min|minutes|hours?)\s*$", re.I)


class FormError(ValueError):
    """The form could not be read or a response could not be made. `uncertain`: Google may have recorded it (never assumed)."""

    def __init__(self, message, uncertain=False):
        super().__init__(message)
        self.uncertain = uncertain


class FormChanged(FormError):
    """The department changed the form in a way Faculty does not know how to answer."""


def _words(text):
    return " ".join(re.sub(r"[^a-z0-9 ]+", "", str(text or "").casefold().replace("’", "").replace("'", "")).split())


def form_id(url):
    """The form's public ID from its link, or ValueError."""
    found = URL.fullmatch(str(url or "").strip())
    if not found:
        raise ValueError("Paste the form's link: https://docs.google.com/forms/d/e/…/viewform.")
    return found.group(1)


def session_parts(text):
    """{date, start, end, place, note} of one of the form's session choices ("Monday, October 26, 2026 7:30 PM-9:30 PM; BLOC 169"),
    or None when it does not read as one. Campus wall clock, as written."""
    found = SESSION.match(str(text or ""))
    if not found:
        return None
    try:
        day = dt.datetime.strptime(found.group(1), "%B %d, %Y").date()
        start = dt.datetime.strptime(" ".join(found.group(2).upper().split()), "%I:%M %p").time()
        end = dt.datetime.strptime(" ".join(found.group(3).upper().split()), "%I:%M %p").time()
    except ValueError:
        return None
    return {"date": day.isoformat(), "start": start.strftime("%H:%M"), "end": end.strftime("%H:%M"), "place": found.group(4).strip(),
            "note": (found.group(5) or "").strip()}


def option_minutes(text):
    """75 for "75 min", 120 for "2 hours"; None for anything else."""
    found = MINUTES.match(str(text or ""))
    if not found:
        return None
    return int(found.group(1)) * (60 if found.group(2).casefold().startswith("hour") else 1)


def http_text(method, url, data=None, timeout=30, cookies=None):
    """One HTTPS call to the form's host: (status, text, {cookie: value} it set). HTTP error statuses are returned; connection
    failures propagate. `cookies` (the kept Google sign-in) go to docs.google.com only."""
    if urllib.parse.urlsplit(url).hostname != HOST:
        raise FormError("Faculty only talks to docs.google.com for the makeup form.")

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None

    headers = {"User-Agent": USER_AGENT}
    if cookies:
        headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in cookies.items())
    body = None
    if data is not None:
        body = urllib.parse.urlencode(data, doseq=True).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=timeout) as response:
            raw, status, sent = response.read(MAX_BODY), response.status, response.headers.get_all("Set-Cookie") or []
    except urllib.error.HTTPError as error:
        raw, status, sent = error.read(MAX_BODY), error.code, (error.headers.get_all("Set-Cookie") if error.headers else None) or []
    renewed = {}
    for line in sent:
        name, _, rest = line.partition("=")
        value = rest.split(";", 1)[0]
        if name.strip() and value and "expires=thu, 01 jan 1970" not in line.casefold():
            renewed[name.strip()] = value
    return status, raw.decode("utf-8", "replace"), renewed


def _timed_out(error):
    return isinstance(error, (TimeoutError, socket.timeout)) or isinstance(getattr(error, "reason", None), (TimeoutError, socket.timeout))


HIDDEN = re.compile(r'<input type="hidden" name="(fbzx|partialResponse|fvv|tag|token|pageHistory)" value="([^"]*)"')


def hidden_fields(page):
    """The form's own keys a page carries, which each response hands on to the next page."""
    return {k: html.unescape(v) for k, v in HIDDEN.findall(page)}


def parse(page):
    """The form as Faculty answers it, from its public page: {title, confirmation, questions: {key: {entry, title, kind, required,
    options, other}}, page_history, hidden: {fbzx, partialResponse, fvv, tag, token, pageHistory (those the page carries)}}.
    FormChanged when it no longer reads."""
    found = re.search(r"FB_PUBLIC_LOAD_DATA_\s*=\s*(.*?);\s*</script>", page, re.S)
    if not found:
        raise FormChanged("The form's page no longer reads as a Google Form.")
    try:
        data = json.loads(found.group(1))
        info, items = data[1], data[1][1]
    except (ValueError, IndexError, TypeError) as error:
        raise FormChanged("The form's page no longer reads as a Google Form.") from error
    questions, unknown, sections, page_of, page_number = {}, [], {}, {}, 0
    for item in items or []:
        if not isinstance(item, list) or len(item) < 4:
            continue
        kind = TYPES.get(item[3], str(item[3]))
        if kind == "section":
            page_number += 1
            sections[item[0]] = page_number
            continue
        entries = item[4] if len(item) > 4 and isinstance(item[4], list) else []
        if not entries:
            continue  # a text block or picture asks nothing
        entry = entries[0]
        title = str(item[1] or "").strip()
        key = next((k for k, start in QUESTIONS.items() if _words(title).startswith(start)), None)
        options = [str(o[0]) for o in (entry[1] or []) if isinstance(o, list) and o and str(o[0])] if len(entry) > 1 else []
        other = any(isinstance(o, list) and len(o) > 4 and o[4] == 1 for o in (entry[1] or [])) if len(entry) > 1 else False
        targets = {str(o[0]): o[2] for o in (entry[1] or []) if isinstance(o, list) and len(o) > 2 and o[2]} if len(entry) > 1 else {}
        question = {"entry": str(entry[0]), "title": title, "kind": kind, "page": page_number, "required": bool(len(entry) > 2 and entry[2]),
                    "options": options, "other": other, "targets": targets}
        page_of[str(entry[0])] = page_number
        if key and key not in questions:
            questions[key] = question
        elif question["required"]:
            unknown.append(title)
    if unknown:
        raise FormChanged("The form now asks something Faculty does not know how to answer: " + "; ".join(unknown[:3]) + ".")
    missing = [key for key in QUESTIONS if key not in questions and key != "instructions"]
    if missing:
        raise FormChanged("The form no longer asks: " + ", ".join(LABELS.get(k, k) for k in missing) + ".")
    for key in RULES + ("session",):
        if questions[key]["kind"] not in ("radio", "dropdown") or not questions[key]["options"]:
            raise FormChanged(f"The form's question “{questions[key]['title']}” is no longer a choice.")
    if "Yes" not in questions["instructor"]["options"]:
        raise FormChanged("The form's instructor question no longer has a Yes.")
    # The pages a response passes through: the first, then the section the instructor's Yes leads to (the department's form sends
    # anyone else to a page that ends it).
    target = questions["instructor"]["targets"].get("Yes")
    pages = [0] + ([sections[target]] if target in sections else sorted({page_of[q["entry"]] for q in questions.values()} - {0}))
    hidden = hidden_fields(page)
    if "fbzx" not in hidden:
        raise FormChanged("The form's page no longer carries its response key.")
    confirmation = ""
    try:
        confirmation = str(info[2][0] or "") if isinstance(info[2], list) else ""
    except (IndexError, TypeError):
        pass
    title = str(info[8] or data[3] or "").strip() if len(info) > 8 else str(data[3] or "").strip()
    return {"title": title, "confirmation": confirmation, "questions": questions,
            "page_history": ",".join(str(p) for p in pages), "pages": pages, "hidden": hidden}


def fields(form, answers):
    """The POST body of one response: every answer by its entry, the pages it passed, the form's own keys and the email to record.
    `answers` maps QUESTIONS keys to the exact text to give; a choice outside a question's options goes in its Other box when it
    has one, else ValueError."""
    return {**form["hidden"], "emailAddress": FORM_EMAIL, "pageHistory": form["page_history"], **entries(form, answers)}


def entries(form, answers):
    """The entries of `answers` (QUESTIONS keys → the exact text to give), checked against the form (`fields`)."""
    body = {}
    for key, value in answers.items():
        question = form["questions"].get(key)
        if question is None:
            if key == "instructions" and not value:
                continue
            raise ValueError(f"The form does not ask {LABELS.get(key, key)}.")
        value = str(value or "").strip()
        if not value:
            if question["required"]:
                raise ValueError(f"Choose {LABELS.get(key, question['title'])}.")
            continue
        name = f"entry.{question['entry']}"
        if question["kind"] in ("radio", "dropdown") and value not in question["options"]:
            if not question["other"]:
                raise ValueError(f"“{value}” is not one of the form's choices for {LABELS.get(key, question['title'])}.")
            body[name], body[name + ".other_option_response"] = OTHER, value
        else:
            body[name] = value
        if question["kind"] in ("radio", "dropdown", "checkbox"):
            body[name + "_sentinel"] = ""
    return body


class MakeupForm:
    """One department form, read and answered over HTTPS as the signed-in TAMU Google account (module docstring; GOOGLE SIGN-IN:
    the form takes a response only from a signed-in account, 2026-10-22). `session` returns the kept sign-in or None, `renew` keeps
    what Google renewed; both are google_signin's, replaced by tests."""

    SIGN_IN = ("Faculty's Google sign-in for the makeup form has ended (Google no longer opens the form signed in). Choose Sign "
               "in to Google on Absences › Makeups owed, then send it again. Nothing was sent.")

    def __init__(self):
        from . import google_signin
        self.http = http_text
        self.session, self.renew = google_signin.load, google_signin.renew

    def _call(self, method, url, data=None, timeout=30):
        kept = self.session() or {}
        status, page, renewed = self.http(method, url, data=data, timeout=timeout, cookies=kept.get("cookies") or None)
        if kept and renewed:
            self.renew(kept.get("sign_in"), renewed)
        return status, page, kept

    def read(self, url):
        """The form as `parse` reads it, with its `url`, `id` and `signed_in` (the Google account it was served to, '' for none).
        FormError when it cannot be read now."""
        from .google_signin import signed_in_as
        form = form_id(url)
        try:
            status, page, kept = self._call("GET", f"https://{HOST}/forms/d/e/{form}/viewform", timeout=30)
        except (urllib.error.URLError, OSError) as error:
            raise FormError("Google Forms did not answer; try again in a minute.") from error
        if status in (301, 302, 303, 307, 308, 401, 403):
            if kept:
                from .google_signin import observed
                observed(kept.get("sign_in"), "")
            raise FormError(self.SIGN_IN)
        if status == 404:
            raise FormChanged("The form's link no longer works: it was closed or replaced.")
        if status != 200:
            raise FormError(f"Google Forms answered {status}; try again in a minute.")
        if re.search(r"no longer accepting responses|closedform", page, re.I):
            raise FormChanged("The form is no longer accepting responses.")
        if kept:
            from .google_signin import observed
            observed(kept.get("sign_in"), signed_in_as(page))  # SIGN-IN ENDED: what the page says, as Google last saw it
        return {**parse(page), "id": form, "url": f"https://{HOST}/forms/d/e/{form}/viewform", "signed_in": signed_in_as(page)}

    def submit(self, form, answers):
        """Submits one response, page by page as the form's own Next and Submit do (PAGE BY PAGE, 2026-10-22: the whole response
        posted at once was refused, 400 naming nothing; each page's answer hands its token and the answers so far to the next).
        Returns the confirmation Google showed. FormError: not recorded (`uncertain` False) or perhaps recorded (`uncertain` True:
        no answer, a timeout or a server error after the final request left). A page before the last records nothing."""
        if form.get("signed_in") != FORM_EMAIL:
            raise FormError(self.SIGN_IN)
        fields(form, answers)  # every answer checked before anything is sent
        pages, hidden = form.get("pages") or [0], dict(form["hidden"])
        url = f"https://{HOST}/forms/d/e/{form['id']}/formResponse"
        for page in pages[:-1]:
            step = {**hidden, **entries(form, {k: v for k, v in answers.items() if form["questions"].get(k, {}).get("page") == page}),
                    "continue": "1", **({"emailAddress": FORM_EMAIL} if page == 0 else {})}
            try:
                status, shown, _ = self._call("POST", url, data=step, timeout=60)
            except (urllib.error.URLError, OSError) as error:
                raise FormError("Google Forms did not answer; nothing was recorded. Send it again.") from error
            if status in (401, 403):
                raise FormError(self.SIGN_IN)
            hidden = hidden_fields(shown)
            if status != 200 or not hidden.get("pageHistory", "").endswith(str(pages[pages.index(page) + 1])):
                found = rejected(shown)
                raise FormError("Google Forms did not accept the response" + (": " + "; ".join(found) if found else
                                f" on its page {page + 1} and named no question ({status})") + ". Nothing was recorded.")
        last = pages[-1]
        body = {**hidden, **entries(form, {k: v for k, v in answers.items() if form["questions"].get(k, {}).get("page") == last})}
        try:
            status, page, _ = self._call("POST", url, data=body, timeout=60)
        except (urllib.error.URLError, OSError) as error:
            # A refused connection never reached Google; anything after the request left (a timeout, a reset) may have been recorded.
            sent = _timed_out(error) or not isinstance(getattr(error, "reason", error), ConnectionRefusedError)
            raise FormError("Google Forms did not answer, so whether it was recorded is not known.", uncertain=sent) from error
        if status >= 500:
            raise FormError(f"Google Forms answered {status}, so whether it was recorded is not known.", uncertain=True)
        if status in (401, 403):
            raise FormError(self.SIGN_IN)  # Google refused the sign-in: nothing was recorded
        if status == 400 and "FB_PUBLIC_LOAD_DATA_" in page:
            # The form shown again with what it did not accept (2026-10-22: Sandra Vance's request, "refused (400)", said nothing more).
            found = rejected(page)
            raise FormError("Google Forms did not accept the response" + (": " + "; ".join(found) if found else
                                                                          " and named no question (400)") + ". Nothing was recorded.")
        if status != 200:
            raise FormError(f"Google Forms refused the response ({status}).")
        if "freebirdFormviewerViewResponseConfirmationMessage" in page or (form["confirmation"] and _words(form["confirmation"])[:60] in _words(html.unescape(re.sub(r"<[^>]+>", " ", page)))) \
                or re.search(r"Your response has been recorded", page):
            return form["confirmation"] or "Your response has been recorded."
        if "FB_PUBLIC_LOAD_DATA_" in page:
            raise FormError("Google Forms showed the form again instead of recording it (an answer it did not accept).")
        raise FormError("Google Forms' answer did not say the response was recorded, so whether it was is not known.", uncertain=True)


ERROR_WORDS = re.compile(r"required question|must|valid|enter|only|between|characters|number", re.I)


def rejected(page):
    """["<question>: <what Google said>"] for each question a re-shown form marks (its error line under the question), the
    Email box included."""
    found = []
    email = re.search(r'data-user-email-address=.{0,4000}?role="alert"[^>]*>(.*?)</div>', page, re.S)
    said = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", email.group(1))).split()) if email else ""
    if said:
        found.append(f"Email: {said}")
    for block in page.split('data-params="%.@.[')[1:]:
        title = html.unescape(block.split("&quot;")[1]).strip() if "&quot;" in block[:400] else ""
        options = set(re.findall(r'data-value="([^"]*)"', block))
        for text in re.findall(r'<span class="[^"]*">([^<]{3,200})</span></div>', block):
            text = " ".join(html.unescape(text).split())
            if text and text not in options and text != title and ERROR_WORDS.search(text):
                found.append(f"{title}: {text}")
                break
    return found[:5]


def receipt_answers(markup):
    """{question title: answer} from a Google Forms receipt email ("Thanks for filling out this form"): a typed answer as written, a
    choice as the one ticked (aria-checked="true"), a dropdown as shown. {} when it does not read as a receipt."""
    text = str(markup or "")
    if "Thanks for filling out this form" not in text:
        return {}
    answers = {}
    for block in re.split(r"<h2\b", text)[1:]:
        title_html, _, rest = block.partition("</h2>")
        title = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", "<" + title_html)).replace("*", " ").split())
        if not title:
            continue
        checked = re.findall(r'role="(?:radio|checkbox)" aria-checked="true" aria-label="([^"]*)"', rest)
        if checked:
            answers[title] = "; ".join(html.unescape(c).strip() for c in checked)
            continue
        if 'role="radio"' in rest or 'role="checkbox"' in rest:
            answers[title] = ""
            continue
        shown = re.search(r'<div dir="auto" style="white-space: pre-wrap;[^"]*">(.*?)</div>', rest, re.S) or \
            re.search(r'<td><div style="[^"]*min-width: 100px[^"]*">(.*?)</div></td>', rest, re.S)
        answers[title] = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", shown.group(1))).split()) if shown else ""
    recorded = re.search(r"Your email \(<b>([^<]+)</b>\) was recorded", text)
    if recorded:
        answers["Email"] = recorded.group(1).strip()
    return answers


def receipt_fields(answers):
    """A receipt's answers by QUESTIONS key ({} for one that is not this form's)."""
    found = {}
    for title, value in answers.items():
        key = "email" if title == "Email" else next((k for k, start in QUESTIONS.items() if _words(title).startswith(start)), None)
        if key and key not in found:
            found[key] = value
    return found if {"uin", "session", "section"} <= set(found) else {}
