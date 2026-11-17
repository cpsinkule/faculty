"""Import from AIM (requirement, 2026-09-26). AIM, the Disability Resources portal, has no API and sits behind TAMU's
Microsoft sign-in with a Duo verified push. Faculty runs a private, headless browser on this server and signs in with
the NetID and password the instructor types into Faculty's own form (the instructor asked for this: "prompted for the
credentials through faculty's own interface and it handles everything behind the scenes ... and auto click the continue
button"). A driver recognizes each sign-in step from the page itself: it fills the NetID and password, relays Duo's
verification code to the instructor, answers "Stay signed in?" and Duo's "Is this your device?" with no, continues past
AIM's FERPA access-policy page (the form says that signing in is the instructor's acknowledgment), and repeats AIM's own
two Export buttons (ASP.NET postbacks) with the fields AIM's pages give it. A step it does not recognize opens the live
page in the window, where the instructor continues by clicking and typing into Chrome's screencast.

Nothing is kept: the password is held in memory only until it is typed into the sign-in page, and no password, keystroke,
cookie, picture or file is written or logged (the log gets the sequence of step names and hosts only). The browser and
its temporary profile are discarded after the export, on Cancel, after IDLE without the window watching, or after
LIFETIME. This window never writes to AIM; it only reads the two exports, and in the form capture window (AimCaptureBrowser)
it records the structure of the form the instructor opened (field names, labels, buttons; never what is in them). Send to AIM
(scheduling/aim_send.py) is the one writer, started by the instructor's own Send in this same window."""
from __future__ import annotations

import base64
import contextlib
import hashlib
import hmac
import html
import json
import logging
import os
import queue
import re
import secrets
import shutil
import subprocess
import threading
import time
import uuid
from concurrent.futures import Future, TimeoutError as FutureTimeout
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlencode, urljoin, urlsplit

from flask import Response, current_app, jsonify, request
from werkzeug.exceptions import Gone

from rosters import connect
from scheduling.clock import ZONE_NAME, campus_now

PORTAL = "https://cascade.accessiblelearning.com/tamu/instructor"
PREFIX = "ctl00$ctl00$ctl00$MainContent$MainContent$MainContent$"
# AIM's two exports: the page, its Export button's postback target, and what the export holds.
EXPORTS = {
    "accommodations": ("Overview.aspx", PREFIX + "PnlExport$BtnExport", "accommodation requests"),
    "exams": ("Testing.aspx?Step=Exams", PREFIX + "GFormUpload$BtnExportData", "upcoming DR exams"),
}
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
LIMIT = 10 * 1024 * 1024   # Faculty's importers take 10 MB or less
VIEW = {"width": 1100, "height": 760}
IDLE = 10 * 60             # seconds without the window polling before the browser is discarded
LIFETIME = 30 * 60         # the longest one sign-in stays open
KEEP = 10 * 60             # exported files wait this long for the window to fetch them
FRAME_EVERY = 0.3          # seconds between screenshots when Chrome's screencast is unavailable
WATCHING = 5               # the window counts as watching this long after its last read
WAIT = 1.5                 # the longest one picture request waits for a new picture
LOOK_EVERY = 0.4           # seconds between reads of which sign-in step the browser is on
STEP_WAIT = 12             # seconds a page Faculty does not recognize (or cannot act on) lasts before the window shows it
ACT_GAP = 4                # seconds before Faculty acts again on a step that has not moved on
CREDENTIAL_LIMIT = 256
KEYS = frozenset({"Enter", "Tab", "Shift+Tab", "Backspace", "Delete", "Escape", "ArrowUp", "ArrowDown", "ArrowLeft",
                  "ArrowRight", "Home", "End", "PageUp", "PageDown", "Control+a", "Control+c", "Control+x", "Control+z"})
TEXT_LIMIT = 2000
SIGNED_OUT = "AIM's sign-in has ended. Close this window and choose Import from AIM again."
POLICY = "AIM is showing its access policy page, so nothing could be exported. Close this window and try again."
BROWSER_ENV = {"PATH", "HOME", "USER", "LANG", "TMPDIR", "TZ", "FONTCONFIG_PATH", "FONTCONFIG_FILE", "LD_LIBRARY_PATH"}
BROWSER_LIBS = Path(__file__).resolve().parent / ".tools" / "browser-libs" / "usr" / "lib" / "x86_64-linux-gnu"

log = logging.getLogger("faculty.aim")


class Refused(Exception):
    """An export AIM did not give, with the reason in words for the instructor."""


class _Form(HTMLParser):
    """The first form of an AIM page: its action, the fields a browser would submit, and every control's name."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.action, self.fields, self.names = None, [], set()
        self._inside = self._done = False
        self._select = self._option = self._textarea = None
        self._options, self._text = [], []

    def handle_starttag(self, tag, attrs):
        a = {key: "" if value is None else value for key, value in attrs}
        if tag == "form":
            if not self._done and not self._inside:
                self._inside, self.action = True, a.get("action", "")
            return
        if not self._inside:
            return
        name, disabled = a.get("name", ""), "disabled" in a
        if name:
            self.names.add(name)
        if tag == "input":
            kind = a.get("type", "text").lower()
            if not name or disabled or kind in {"submit", "button", "image", "reset", "file"}:
                return
            if kind in {"checkbox", "radio"}:
                if "checked" in a:
                    self.fields.append((name, a.get("value", "on")))
                return
            self.fields.append((name, a.get("value", "")))
        elif tag == "select":
            self._select, self._options = (name, disabled, "multiple" in a), []
        elif tag == "option" and self._select:
            self._option = {"value": a.get("value"), "selected": "selected" in a, "text": []}
            self._options.append(self._option)
        elif tag == "textarea":
            self._textarea, self._text = (name, disabled), []

    def handle_data(self, data):
        if self._option is not None:
            self._option["text"].append(data)
        if self._textarea is not None:
            self._text.append(data)

    def handle_endtag(self, tag):
        if tag == "option":
            self._option = None
        elif tag == "select" and self._select:
            name, disabled, multiple = self._select
            chosen = [item for item in self._options if item["selected"]]
            chosen = chosen if multiple else (chosen[-1:] or self._options[:1])
            if name and not disabled:
                for item in chosen:
                    value = item["value"] if item["value"] is not None else " ".join("".join(item["text"]).split())
                    self.fields.append((name, value))
            self._select = self._option = None
        elif tag == "textarea" and self._textarea:
            name, disabled = self._textarea
            if name and not disabled:
                self.fields.append((name, "".join(self._text)))
            self._textarea = None
        elif tag == "form" and self._inside:
            self._inside, self._done = False, True


def parse_form(markup):
    form = _Form()
    form.feed(markup)
    form.close()
    return form


def postback_body(fields, target):
    """The fields with ASP.NET's postback target set to `target`, as a browser's __doPostBack would send them."""
    pairs = [(name, value) for name, value in fields if name not in ("__EVENTTARGET", "__EVENTARGUMENT")]
    return urlencode([("__EVENTTARGET", target), ("__EVENTARGUMENT", ""), *pairs])


def page_term(markup):
    """The term AIM's Overview page shows ("Term: Fall 2026"), or ""."""
    text = re.sub(r"<(script|style)\b.*?</\1>", " ", markup, flags=re.S | re.I)
    text = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", text)).split())
    found = re.search(r"Term:\s*([A-Za-z]+\s+\d{4})", text)
    return found.group(1) if found else ""


def filename(disposition, kind):
    """The download's own name when it is a plain .xlsx name, else aim-<kind>.xlsx."""
    value = disposition or ""
    star = re.search(r"filename\*\s*=\s*(?:UTF-8'')?([^;]+)", value, re.I)
    plain = re.search(r'filename\s*=\s*"?([^";]+)"?', value, re.I)
    name = (unquote(star.group(1).strip().strip('"')) if star else plain.group(1).strip() if plain else "")
    name = re.sub(r"[^\w .()-]", "_", re.split(r"[\\/]", name)[-1])[:120]
    return name if name.lower().endswith(".xlsx") else f"aim-{kind}.xlsx"


def export_one(client, base, kind):
    """One AIM export through `client` (the signed-in browser's request context): read the page fresh, post its Export
    button back with the page's own fields, and return the spreadsheet. Posts only to AIM's own host."""
    page, button, label = EXPORTS[kind]
    address, host = urljoin(base, page), urlsplit(base).netloc
    response = client.get(address, timeout=60000)
    landed = urlsplit(response.url)
    if landed.netloc != host or landed.scheme != urlsplit(base).scheme:
        raise Refused(SIGNED_OUT)  # sent on to Microsoft's sign-in
    if landed.path.lower().endswith("/login.aspx"):
        raise Refused(POLICY)
    if not response.ok:
        raise Refused(f"AIM answered {response.status} for its {label} page.")
    markup = response.text()
    form = parse_form(markup)
    if button not in form.names:
        raise Refused(f"AIM's {label} page has no Export button where Faculty expects one. Export it in AIM yourself "
                      "and use Import accommodations or Import DR exam bookings.")
    action = urljoin(response.url, form.action or address)
    if (urlsplit(action).scheme, urlsplit(action).netloc) != (landed.scheme, host):
        raise Refused(f"AIM's {label} page sends its form somewhere else; nothing was sent.")
    answer = client.post(action, data=postback_body(form.fields, button), max_redirects=0, timeout=120000,
                         headers={"Content-Type": "application/x-www-form-urlencoded", "Origin": f"{landed.scheme}://{host}", "Referer": response.url})
    content = answer.body()
    if not answer.ok or content[:4] != b"PK\x03\x04":
        raise Refused(f"AIM did not return a spreadsheet of {label}.")
    if len(content) > LIMIT:
        raise Refused(f"AIM's {label} export is larger than Faculty's 10 MB import limit.")
    return {"kind": kind, "name": filename(answer.headers.get("content-disposition"), kind), "size": len(content),
            "content": content, "term": page_term(markup) if kind == "accommodations" else ""}


def check_events(data):
    """The window's clicks and typing, checked before any reaches the browser. Errors never repeat what was typed."""
    events = data.get("events") if isinstance(data, dict) else None
    if not isinstance(events, list) or not 0 < len(events) <= 50:
        raise ValueError("Send between 1 and 50 input events.")
    checked = []
    for event in events:
        kind = event.get("type") if isinstance(event, dict) else None
        if kind in ("click", "wheel"):
            x, y = event.get("x"), event.get("y")
            if not all(isinstance(v, int) and not isinstance(v, bool) for v in (x, y)) or not (0 <= x < VIEW["width"] and 0 <= y < VIEW["height"]):
                raise ValueError("A click or scroll was outside the page.")
            item = {"type": kind, "x": x, "y": y}
            if kind == "wheel":
                dx, dy = event.get("dx", 0), event.get("dy", 0)
                if not all(isinstance(v, (int, float)) and not isinstance(v, bool) and abs(v) <= 5000 for v in (dx, dy)):
                    raise ValueError("A scroll was too large.")
                item.update(dx=dx, dy=dy)
            checked.append(item)
        elif kind == "key":
            if not isinstance(event.get("key"), str) or event["key"] not in KEYS:
                raise ValueError("That key cannot be sent to the page.")
            checked.append({"type": "key", "key": event["key"]})
        elif kind == "text":
            text = event.get("text")
            if not isinstance(text, str) or not 0 < len(text) <= TEXT_LIMIT or any(ord(c) < 32 or ord(c) == 127 for c in text):
                raise ValueError("Typed text must be 1 to 2,000 characters without line breaks.")
            checked.append({"type": "text", "text": text})
        elif kind in ("back", "home"):
            checked.append({"type": kind})
        else:
            raise ValueError("Unknown input event.")
    return checked



# What a page shows, read in one round trip: its visible text and visible fields (never their values).
SCAN = r"""() => {
  // Shown means on screen and not hidden, faded out or clipped away: sign-in pages keep earlier steps' fields in the page.
  const shown = (el) => {
    const box = el.getBoundingClientRect(), style = getComputedStyle(el);
    if (!(box.width > 1 && box.height > 1) || style.visibility === "hidden" || style.display === "none") return false;
    if (box.bottom <= 0 || box.right <= 0 || box.top >= innerHeight || box.left >= innerWidth) return false;
    if (el.checkVisibility && !el.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true })) return false;
    return !el.closest('[aria-hidden="true"], [hidden]');
  };
  const inputs = [...document.querySelectorAll("input")].filter(shown)
    .map((el) => ({ type: (el.type || "").toLowerCase(), name: (el.name || "").toLowerCase(), id: (el.id || "").toLowerCase(), auto: (el.autocomplete || "").toLowerCase() }))
    .filter((el) => !["hidden", "submit", "button", "image", "reset", "checkbox", "radio"].includes(el.type));
  return { text: document.body ? document.body.innerText.slice(0, 6000) : "", inputs };
}"""
NETID_DOMAIN = "tamu.edu"
NETID_FIELD = 'input[type="email"]:visible, input[name="loginfmt"]:visible, input[autocomplete="username"]:visible, input[name="username"]:visible'
PASSWORD_FIELD = 'input[type="password"]:visible'
PASSCODE_FIELD = 'input[name*="passcode" i]:visible, input[id*="passcode" i]:visible, input[autocomplete="one-time-code"]:visible'

# AIM FORM CAPTURE (AGENTS.md "ACCOMMODATIONS"): the structure of the form a page shows, read in one round trip from its
# main frame: headings, every field and every button by tag, type, name, id and label. It never reads what a field holds
# (no value, checked or selected state) nor the page's text; a select's option texts are the only texts inside a field.
# An element's words leave out the text of any select or textarea inside it: a <label> wrapping its field would otherwise
# carry a textarea's prefilled content or every option of a select.
FORM_SCAN = r"""() => {
  const words = (el) => {
    if (!el) return "";
    const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
    const parts = [];
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
      const inner = node.parentElement && node.parentElement.closest("select, textarea, script, style");
      if (!(inner && inner !== el && el.contains(inner))) parts.push(node.data);
    }
    return parts.join("");
  };
  const attr = (el, name) => el.getAttribute(name) || "";
  const label = (el) => (el.labels && el.labels.length ? words(el.labels[0]) : attr(el, "aria-label") || attr(el, "placeholder"));
  const kinds = ["submit", "button", "image", "reset"];
  const controls = [...document.querySelectorAll("input, select, textarea")].map((el) => ({
    tag: el.tagName, type: el.type || "", name: attr(el, "name"), id: el.id || "", label: label(el), required: !!el.required,
    options: el.tagName === "SELECT" ? [...el.options].map((option) => words(option)) : [],
  }));
  const buttons = [...document.querySelectorAll("button, input, a[href]")]
    .filter((el) => el.tagName === "BUTTON" || (el.tagName === "INPUT" && kinds.includes(el.type)) || (el.tagName === "A" && attr(el, "href").startsWith("javascript:__doPostBack")))
    .map((el) => ({ tag: el.tagName, type: el.tagName === "A" ? "" : el.type || "", name: attr(el, "name"), id: el.id || "",
                    text: words(el) || attr(el, "aria-label") || attr(el, "alt") }));
  return { url: location.href, headings: [...document.querySelectorAll("h1, h2, h3")].map(words), controls, buttons };
}"""
FORM_LIMITS = {"text": 200, "headings": 50, "controls": 400, "buttons": 100, "options": 300}


def _words(text):
    """A text's casefolded letter runs of two letters or more."""
    return {run for run in re.findall(r"[^\W\d_]+", text.casefold()) if len(run) >= 2}


def form_structure(raw, names):
    """What Faculty keeps of a FORM_SCAN: the page's path and the form's shape, with student data left out. A text is
    student data when it holds nine digits in a row or every word of one of `names` (either order: "Doe, Jane" is "Jane
    Doe"). Such an option is removed and any other such text becomes "", each counted once in `dropped`."""
    raw = raw if isinstance(raw, dict) else {}
    people = [found for found in (_words(name) for name in names if isinstance(name, str)) if found]
    dropped = 0

    def student(value):
        return bool(re.search(r"\d{9}", value)) or any(person <= _words(value) for person in people)

    def clean(value):
        return " ".join(value.split()) if isinstance(value, str) else ""

    def text(value):
        nonlocal dropped
        value = clean(value)
        if value and student(value):
            dropped += 1
            return ""
        return value[:FORM_LIMITS["text"]]

    def options(values):
        nonlocal dropped
        kept = []
        for value in (values[:FORM_LIMITS["options"]] if isinstance(values, list) else []):
            value = clean(value)
            if value and student(value):
                dropped += 1
            else:
                kept.append(value[:FORM_LIMITS["text"]])
        return kept

    def items(key, kind):
        found = raw.get(key)
        return [item for item in found if isinstance(item, kind)][:FORM_LIMITS[key]] if isinstance(found, list) else []

    def lower(value):
        return clean(value)[:FORM_LIMITS["text"]].lower()

    headings = [text(item) for item in items("headings", str)]
    controls = [{"tag": lower(item.get("tag")), "type": lower(item.get("type")), "name": text(item.get("name")), "id": text(item.get("id")),
                 "label": text(item.get("label")), "required": bool(item.get("required")), "options": options(item.get("options"))}
                for item in items("controls", dict)]
    buttons = [{"tag": lower(item.get("tag")), "type": lower(item.get("type")), "name": text(item.get("name")), "id": text(item.get("id")),
                "text": text(item.get("text"))} for item in items("buttons", dict)]
    url = raw.get("url")
    page = text(urlsplit(url).path if isinstance(url, str) else "")
    return {"schema": 1, "page": page, "headings": headings, "controls": controls, "buttons": buttons, "dropped": dropped}


def duo_code(text):
    """The verification code a Duo verified push shows (a line of digits alone), or ""."""
    found = re.search(r"(?m)^\s*(\d{3,8})\s*$", text or "")
    return found.group(1) if found else ""


def error_line(text):
    """The sign-in page's own complaint (a wrong password, an unknown NetID), for the instructor; or ""."""
    for line in (text or "").splitlines():
        line = " ".join(line.split())
        if re.search(r"incorrect|isn.t right|wrong|not (found|recognized)|couldn.t find|doesn.t exist|invalid|locked|enter a valid|too many", line, re.I):
            return line[:200]
    return ""


def classify(url, text, inputs, base):
    """Which sign-in step a page is, from its address, visible text and visible fields alone. Host names are not
    required (Microsoft, TAMU and Duo may move pages); only AIM's own pages are known by address."""
    parts, aim = urlsplit(url), urlsplit(base)
    host, path = (parts.hostname or "").lower(), parts.path.lower()
    words = " ".join((text or "").split()).lower()
    if parts.netloc == aim.netloc and path.endswith("/login.aspx"):
        return "policy"
    if parts.netloc == aim.netloc and path.startswith(aim.path.lower()) and path.endswith(".aspx"):
        return "aim"
    types = {field.get("type", "") for field in inputs}
    if "duosecurity.com" in host or re.search(r"\bduo (mobile|push|security)\b|secured by duo", words):
        if re.search(r"is this your device|trust this browser|remember (this|me)", words):
            return "duo_trust"
        if any("passcode" in field.get("name", "") or "passcode" in field.get("id", "") or field.get("auto") == "one-time-code" for field in inputs):
            return "duo_passcode"
        if re.search(r"timed out|request (was )?denied|was denied|push (was )?not (sent|delivered)|expired|couldn.t send", words):
            return "duo_problem"
        if duo_code(text):
            return "duo_code"
        if re.search(r"check for a duo push|push sent|sent a (duo )?push|pushed a login|approve", words):
            return "duo_push"
        if re.search(r"success|logging you in|redirecting", words):
            return "working"
        if re.search(r"other options|choose|send (me )?a push|duo push", words):
            return "duo_choose"
        return "unknown"
    if "stay signed in?" in words:
        return "stay_signed_in"
    if re.search(r"redirected to duo|verification required", words):
        return "duo_redirect"  # Microsoft's page before Duo (TAMU, 2026-09-26: "You will be redirected to Duo to verify your identity")
    if "password" in types:
        return "password"
    if "email" in types or any(field.get("name") in ("loginfmt", "username", "user", "netid") or field.get("auto") == "username" for field in inputs):
        return "netid"
    if not words or re.search(r"working on it|please wait|signing (you )?in|redirecting|loading", words):
        return "working"
    return "unknown"


def _plain(value, name, limit):
    if not isinstance(value, str) or not 0 < len(value) <= limit or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError(f"Enter your {name}.")
    return value


def check_credentials(data, need_netid=True):
    """NetID and password from Faculty's form, checked without ever repeating them in an error."""
    data = data if isinstance(data, dict) else {}
    netid = data.get("netid")
    netid = netid.strip() if isinstance(netid, str) else netid
    if netid or need_netid:
        netid = _plain(netid, "NetID", 100)
    return {"netid": netid or None, "password": _plain(data.get("password"), "password", CREDENTIAL_LIMIT)}


def netid_for(netid, inputs):
    """The NetID as the visible field wants it: NetID@tamu.edu in Microsoft's e-mail field, the bare NetID elsewhere."""
    email = any(field.get("type") == "email" or field.get("name") == "loginfmt" for field in inputs)
    if email:
        return netid if "@" in netid else f"{netid}@{NETID_DOMAIN}"
    return netid.split("@", 1)[0] if netid.lower().endswith(f"@{NETID_DOMAIN}") else netid


SAVED_ENV = "FACULTY_GRADESCOPE_CREDENTIALS_FILE"


def saved_sign_in():
    """(NetID, password) from the saved Gradescope sign-in, which is the instructor's NetID sign-in too (instructor,
    2026-09-26: "the credentials are actually the same as the gradescope credentials"); None when none is set up. Read
    with that file's own checks (a small 0600 file of this account, no links) at the moment a sign-in starts, never
    kept, logged or returned. A broken file raises ValueError naming the file, never its content."""
    path = os.environ.get(SAVED_ENV, "")
    if not path:
        return None
    from grading.gradescope_web import GradescopeError, load_credentials
    try:
        return load_credentials(path)
    except GradescopeError as error:
        raise ValueError(str(error)) from None


def check_answer(data):
    """The instructor's answer to Duo: send the push again, or use a passcode."""
    action = data.get("action") if isinstance(data, dict) else None
    if action == "resend":
        return "resend"
    if action == "passcode":
        code = data.get("code")
        if not isinstance(code, str) or not re.fullmatch(r"\d{6,10}", code.strip()):
            raise ValueError("A Duo passcode is 6 to 10 digits.")
        return ("passcode", code.strip())
    raise ValueError("Choose Send the push again or Use a passcode.")


@contextlib.contextmanager
def virtual_display(env, headless):
    """A private X display (Xvfb, on a display number it picks itself) for a browser that must not run headless, named in
    `env["DISPLAY"]` while it lasts; nothing when `headless`. No TCP listener: only this machine's own processes reach it. It also ends
    by itself when its browser closes (`-terminate`), so a process stopped mid-sign-in leaves no display behind."""
    if headless:
        yield
        return
    server = shutil.which("Xvfb")
    if not server:
        raise RuntimeError("Xvfb is not installed on the server")
    read, write = os.pipe()
    process = subprocess.Popen([server, "-displayfd", str(write), "-screen", "0", f"{VIEW['width']}x{VIEW['height'] + 140}x24",
                                "-nolisten", "tcp", "-terminate"], pass_fds=(write,), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, env={key: env[key] for key in ("PATH", "HOME") if key in env})
    os.close(write)
    try:
        with os.fdopen(read, "rb") as answer:
            number = answer.readline().strip()  # Xvfb writes its display number once it accepts connections
        if not number.isdigit():
            raise RuntimeError("Xvfb did not start")
        env["DISPLAY"] = f":{number.decode()}"
        yield
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


class AimBrowser:
    """One private browser signing in to AIM. Everything Playwright does happens on this object's own thread; requests
    only leave credentials, answers and input for it, and read its state and latest picture.

    Other sign-ins are subclasses: Canvas's (`canvas/signin.py`), Howdy's (`howdy_import.py`), the department Google sign-in's
    (`absences/google_signin.py`), Send to AIM's (`scheduling/aim_send.py`, `AimSendBrowser`) and the form-capture window
    (`AimCaptureBrowser`, below). TARGET names the step that means "signed in", `_classify` knows the site's own pages, ENDED
    and NAME word its messages, and `_on_<TARGET>` does what the site needs once signed in."""

    TARGET, NAME = "aim", "AIM"
    FINAL = ("exported",)  # the state a finished sign-in ends in (Canvas's is "done")
    # Howdy (`howdy_import.py`) answers a headless browser with Cloudflare's endless "security verification" page, so its sign-in runs
    # an ordinary Chromium on a private virtual display (`virtual_display`); every other site keeps the headless one.
    HEADLESS = True
    ENDED = "This sign-in has ended. Choose Import from AIM to start again."

    def __init__(self, portal, netid=None, password=None, kinds=tuple(EXPORTS)):
        self.id = secrets.token_urlsafe(24)
        self.portal = portal
        self.base = portal.rstrip("/") + "/"
        self.state, self.step, self.message, self.place = "starting", "starting", "", "Starting a private browser…"
        self.code, self.needs = "", ""
        self.show_page = netid is None  # without credentials the instructor signs in on the page itself
        self.frame, self.frame_no, self._digest = b"", 0, b""
        self.result, self.finished = None, None
        self.created = self.watched = time.monotonic()
        self._commands, self._lock, self._closing = queue.Queue(), threading.Lock(), threading.Event()
        self._changed = threading.Condition(self._lock)  # a new picture, or the end
        self._context = self._cdp = self._cast_page = None
        self._casting = True  # Chrome's screencast sends a picture as soon as the page changes; screenshots otherwise
        self._netid, self._password, self._intent = netid, password, None  # held only until typed; cleared on close
        self._user = netid or ""  # the NetID (not a secret): a page asking for it beside the password gets it again
        self._kinds, self._did, self._since, self._pushed, self._exported = list(kinds), {}, time.monotonic(), False, False
        self._steps = []
        threading.Thread(target=self._run, name="aim-browser", daemon=True).start()

    # --- the window's side (request threads) ---

    def status(self):
        with self._lock:
            files = [{"kind": f["kind"], "name": f["name"], "size": f["size"]} for f in (self.result or {}).get("files", {}).values()]
            return {"id": self.id, "state": self.state, "step": self.step, "message": self.message, "place": self.place,
                    "code": self.code, "needs": self.needs, "show_page": self.show_page, "frame": self.frame_no,
                    "width": VIEW["width"], "height": VIEW["height"], "term": (self.result or {}).get("term", ""),
                    "files": files, "problems": (self.result or {}).get("problems", [])}

    def watch(self):
        self.watched = time.monotonic()

    def picture(self, after=None, wait=WAIT):
        """(number, picture): the latest, or with `after` the first newer one within `wait` seconds (the window asks
        again at once, so a picture reaches it as soon as the page changes)."""
        self.watch()
        with self._changed:
            if after is not None:
                self._changed.wait_for(lambda: self.frame_no > after or self._closing.is_set(), timeout=wait)
            return self.frame_no, self.frame

    def _live(self):
        if self.state not in ("starting", "signing_in", "signed_in"):
            raise Gone(self.ENDED)
        self.watch()

    def send(self, events):
        self._live()
        for event in events:
            self._commands.put((lambda event=event: self._apply(event), None))

    def credentials(self, netid, password):
        """A NetID and password the instructor typed again after the sign-in page refused them."""
        self._live()
        with self._lock:
            if netid:
                self._netid, self._user = netid, netid
                self._did.pop("netid", None)
            self._password, self.needs, self.message = password, "", ""
            self._did.pop("password", None)

    def answer(self, intent):
        """Send Duo's push again, or use a passcode; the driver does it on the page it finds."""
        self._live()
        with self._lock:
            self._intent, self.needs, self.message = intent, "", ""

    def show(self):
        with self._lock:
            self.show_page = True

    def export(self, kinds):
        if self.state != "signed_in":
            raise ValueError("Sign in to AIM first.")
        self._set(needs="", message="")
        done = Future()
        self._commands.put((lambda: self._export(kinds), done))
        try:
            return done.result(timeout=180)
        except FutureTimeout:
            raise ValueError("AIM did not finish the export in time. Try again.")

    def file(self, kind):
        with self._lock:
            found = (self.result or {}).get("files", {}).get(kind)
        if not found:
            raise Gone("That export is not here any more. Import from AIM again.")
        return found

    def close(self):
        self._closing.set()
        with self._changed:
            if self.state not in (*self.FINAL, "failed"):
                self.state = "closed"
            self.result = None
            self._netid = self._password = self._intent = None
            self._changed.notify_all()

    def expired(self, now=None):
        """Exported files wait KEEP for the window; a sign-in that ended otherwise says so for a minute, then is gone."""
        if self.finished is None:
            return False
        now = time.monotonic() if now is None else now
        return now - self.finished > (KEEP if self.state in self.FINAL else 60)

    # --- the browser's side (its own thread) ---

    def _run(self):
        try:
            from playwright.sync_api import sync_playwright
            # The browser gets a plain environment: none of the service's mail, grading or gateway credentials.
            env = {key: value for key, value in os.environ.items() if key in BROWSER_ENV or key.startswith(("LC_", "XDG_"))}
            if BROWSER_LIBS.is_dir():
                env["LD_LIBRARY_PATH"] = os.pathsep.join(filter(None, (str(BROWSER_LIBS), env.get("LD_LIBRARY_PATH"))))
            with sync_playwright() as playwright, virtual_display(env, self.HEADLESS):
                browser = playwright.chromium.launch(headless=self.HEADLESS, env=env,
                                                     args=[] if self.HEADLESS else [f"--window-size={VIEW['width']},{VIEW['height'] + 140}"])
                try:
                    self._context = browser.new_context(viewport=VIEW, locale="en-US", timezone_id=ZONE_NAME,
                                                        accept_downloads=False, service_workers="block")
                    self._opened(self._context)
                    page = self._context.new_page()
                    self._set(state="signing_in", place=f"Opening {self.NAME}…")
                    page.goto(self.portal, wait_until="commit", timeout=60000)
                    self._loop()
                finally:
                    self._context = None
                    browser.close()
        except Exception:
            log.exception("The %s sign-in browser stopped", self.NAME)
            with self._lock:
                if self.state not in (*self.FINAL, "closed"):
                    self.state, self.message = "failed", "The private browser on the server stopped. Close this window and try again."
        finally:
            with self._lock:
                if self.state not in (*self.FINAL, "failed"):
                    self.state = "closed"
                if self.finished is None:
                    self.finished = time.monotonic()
                self._netid = self._password = self._intent = None
            if self._steps:
                log.warning("%s sign-in steps: %s", self.NAME, " > ".join(self._steps[-40:]))
            self._closing.set()
            with self._changed:
                self._changed.notify_all()
            while True:  # anything still waiting learns that the browser is gone
                try:
                    _, done = self._commands.get_nowait()
                except queue.Empty:
                    break
                if done is not None and done.set_running_or_notify_cancel():
                    done.set_exception(Gone("This sign-in has ended."))

    def _opened(self, context):
        """The browser context is ready, before the first page opens (Howdy listens to its requests here)."""

    def _loop(self):
        shot = looked = 0.0
        while not self._closing.is_set():
            now = time.monotonic()
            if now - self.created > LIFETIME or now - self.watched > IDLE:
                return
            try:
                action, done = self._commands.get_nowait()
            except queue.Empty:
                page = self._page()
                if now - looked >= LOOK_EVERY:
                    self._drive(page)
                    looked = time.monotonic()
                    continue
                if not self._casting and self.show_page and now - self.watched < WATCHING and now - shot >= FRAME_EVERY:
                    self._shoot(page)
                    shot = time.monotonic()
                else:
                    page.wait_for_timeout(20)  # lets Playwright deliver the page's events, screencast pictures included
                continue
            if done is not None and not done.set_running_or_notify_cancel():
                continue
            try:
                result = action()
            except Exception as error:
                if done is not None:
                    done.set_exception(error)
                else:  # one input that could not be applied (a page mid-navigation); never log what it was
                    log.info("An input to the %s sign-in browser was not applied (%s)", self.NAME, type(error).__name__)
                continue
            if done is not None:
                done.set_result(result)
            shot = 0.0  # show the effect at once

    def _page(self):
        pages = [page for page in self._context.pages if not page.is_closed()]
        if pages:
            return pages[-1]
        page = self._context.new_page()
        page.goto(self.portal, wait_until="commit", timeout=60000)
        return page

    # Pictures: Chrome's screencast of the page shown, screenshots only if it cannot start.

    def _cast(self, page):
        if page is self._cast_page or not self._casting:
            return
        try:
            if self._cdp is not None:
                try:
                    self._cdp.detach()
                except Exception:
                    pass
            self._cdp = self._context.new_cdp_session(page)
            self._cdp.on("Page.screencastFrame", self._framed)
            self._cdp.send("Page.startScreencast", {"format": "jpeg", "quality": 70, "maxWidth": VIEW["width"], "maxHeight": VIEW["height"]})
            self._cast_page = page
        except Exception as error:
            log.info("Screencast unavailable, using screenshots (%s)", type(error).__name__)
            self._casting, self._cdp = False, None

    def _framed(self, event):
        cdp = self._cdp
        try:
            if cdp is not None:
                cdp.send("Page.screencastFrameAck", {"sessionId": event["sessionId"]})
        except Exception:
            pass
        self._picture(base64.b64decode(event["data"]))

    def _picture(self, image):
        digest = hashlib.sha256(image).digest()
        with self._changed:
            if digest != self._digest:
                self._digest, self.frame, self.frame_no = digest, image, self.frame_no + 1
                self._changed.notify_all()

    def _shoot(self, page):
        try:
            self._picture(page.screenshot(type="jpeg", quality=70, timeout=5000, caret="initial"))
        except Exception:
            pass  # a page between documents; the next picture will do

    # The driver: one look at the page, then at most one action on it.

    def _scan(self, page):
        frames = [frame for frame in page.frames[1:] if "duosecurity.com" in (urlsplit(frame.url).hostname or "")]
        frame = frames[0] if frames else page.main_frame  # a Duo prompt in a frame, else the page itself
        try:
            seen = frame.evaluate(SCAN)
        except Exception:
            return None  # between documents
        return frame, frame.url, seen.get("text", ""), seen.get("inputs", [])

    def _drive(self, page):
        self._cast(page)
        seen = self._scan(page)
        if seen is None or self.state in ("exporting", *self.FINAL):
            return
        frame, url, text, inputs = seen
        step, now = self._classify(url, text, inputs), time.monotonic()
        if step != self.step:
            parts = urlsplit(url)  # the site and the path's first segment only: later segments can be session identifiers
            self._steps.append(f"{step}@{parts.hostname or ''}/{parts.path.strip('/').split('/')[0]}")
            self._since = now
            with self._lock:
                self.step = step
                if step not in ("netid", "password", "duo_code", "duo_push", "duo_problem", "duo_passcode", "duo_choose"):
                    self.code = ""
                if self.needs in ("netid", "password", "duo", "passcode") and step not in ("netid", "password", "duo_problem", "duo_passcode"):
                    self.needs, self.message = "", ""
                self.place = PLACES.get(step, "")
                self.state = "signed_in" if step == self.TARGET else "signing_in"
        acted = getattr(self, f"_on_{step}", lambda *_: True)(frame, text, inputs, now)
        if (step == "unknown" or acted is False) and now - self._since > STEP_WAIT and not self.show_page:
            self._set(show_page=True, message="Faculty doesn't recognize this step. Continue it on the page below; Faculty takes over again after it.")
            log.warning("%s sign-in shown to the instructor at: %s", self.NAME, " > ".join(self._steps[-12:]))

    def _classify(self, url, text, inputs):
        return classify(url, text, inputs, self.base)

    def _ready(self, step, now):
        """True when Faculty has not acted on this step within ACT_GAP (and records that it acts now)."""
        if now - self._did.get(step, -ACT_GAP) < ACT_GAP:
            return False
        self._did[step] = now
        return True

    def _press(self, frame, pattern):
        name = re.compile(pattern, re.I)
        for role in ("button", "link"):
            try:
                target = frame.get_by_role(role, name=name)
                if target.count():
                    target.first.click(timeout=3000)
                    return True
            except Exception:
                continue
        return False

    def _fill(self, frame, selector, value):
        field = frame.locator(selector).first
        field.fill(value, timeout=5000)
        field.press("Enter", timeout=5000)

    def _refused(self, step, text, now, needs):
        """A NetID or password the page is still asking for after Faculty entered it. Only the page's own complaint
        (a wrong password, an unknown NetID) asks the instructor again; a page that just does not move on is shown."""
        did = self._did.get(step)
        if did is None or self.needs:
            return None
        complaint = error_line(text)
        if complaint and now - did > 1.5:
            self._set(needs=needs, message=complaint)
            return None
        return False if now - did > STEP_WAIT else None

    def _on_netid(self, frame, text, inputs, now):
        with self._lock:
            netid, self._netid = self._netid, None
        if netid:
            self._fill(frame, NETID_FIELD, netid_for(netid, inputs))
            self._did["netid"] = now
        else:
            return self._refused("netid", text, now, "netid")

    def _on_password(self, frame, text, inputs, now):
        with self._lock:
            password, self._password = self._password, None
        if password:
            if self._user and any(field.get("type") != "password" for field in inputs):
                try:  # a page that asks for the NetID and the password together
                    frame.locator(NETID_FIELD).first.fill(netid_for(self._user, inputs), timeout=1000)
                except Exception:
                    pass
            self._fill(frame, PASSWORD_FIELD, password)
            self._did["password"] = now
        else:
            return self._refused("password", text, now, "password")

    def _on_duo_redirect(self, frame, text, inputs, now):
        return self._press(frame, r"^continue$") if self._ready("redirect", now) else None

    def _on_stay_signed_in(self, frame, text, inputs, now):
        return self._press(frame, r"^no$") if self._ready("stay", now) else None

    def _on_duo_trust(self, frame, text, inputs, now):
        return self._press(frame, r"^no\b|don.?t trust|not now") if self._ready("trust", now) else None

    def _on_policy(self, frame, text, inputs, now):
        return self._press(frame, r"continue to view student accommodations") if self._ready("policy", now) else None

    def _duo_intent(self, frame, now):
        """The instructor's answer, carried out on whichever Duo page is showing: a new push from the page's own
        resend button if it has one; otherwise (and for a passcode) from Duo's other options."""
        intent = self._intent
        if intent is None or not self._ready("intent", now):
            return
        if intent == "resend" and self._press(frame, r"send (me )?(a )?(new )?push|resend|try again"):
            self._intent = None
            return
        self._press(frame, r"other options")

    def _on_duo_code(self, frame, text, inputs, now):
        self._set(code=duo_code(text))
        self._duo_intent(frame, now)

    def _on_duo_push(self, frame, text, inputs, now):
        self._set(code="")
        self._duo_intent(frame, now)

    def _on_duo_problem(self, frame, text, inputs, now):
        line = next((" ".join(l.split()) for l in text.splitlines() if re.search(r"timed out|denied|not (sent|delivered)|expired|couldn.t send", l, re.I)), "")
        if not self._intent and not self.needs:
            self._set(code="", needs="duo", message=line[:200] or "Duo did not get an answer.")
        if self._intent == "resend" and self._ready("intent", now):
            if self._press(frame, r"try again|send (me )?(a )?(new )?push|resend"):
                self._intent = None
            else:
                self._press(frame, r"other options")

    def _on_duo_choose(self, frame, text, inputs, now):
        intent = self._intent
        if isinstance(intent, tuple):
            if self._ready("choose", now):
                self._press(frame, r"passcode")
        elif intent == "resend" or not self._pushed:
            if self._ready("choose", now) and self._press(frame, r"duo push|send (me )?(a )?push"):
                self._pushed, self._intent = True, None

    def _on_duo_passcode(self, frame, text, inputs, now):
        intent = self._intent
        if isinstance(intent, tuple):
            self._intent = None
            field = frame.locator(PASSCODE_FIELD).first
            field.fill(intent[1], timeout=5000)
            if not self._press(frame, r"^(verify|submit|log in|continue)"):  # one submission: a passcode works once
                field.press("Enter", timeout=5000)
            self._did["passcode"] = now
        elif not self.needs:
            self._set(needs="passcode", message=error_line(text) if "passcode" in self._did else "")

    def _on_aim(self, frame, text, inputs, now):
        if not self._exported:
            self._exported = True
            self._export(self._kinds)

    def _on_unknown(self, frame, text, inputs, now):
        return False

    def _apply(self, event):
        page, kind = self._page(), event["type"]
        if kind == "click":
            page.mouse.click(event["x"], event["y"])
        elif kind == "wheel":
            page.mouse.move(event["x"], event["y"])
            page.mouse.wheel(event["dx"], event["dy"])
        elif kind == "key":
            page.keyboard.press(event["key"])
        elif kind == "text":
            page.keyboard.type(event["text"])
        elif kind == "back":
            page.go_back(wait_until="commit", timeout=30000)
        elif kind == "home":
            page.goto(self.portal, wait_until="commit", timeout=60000)

    def _export(self, kinds):
        self._set(state="exporting", place="Exporting from AIM…")
        files, problems, term = {}, [], ""
        for kind in kinds:  # one at a time: AIM keeps one session per sign-in
            try:
                found = export_one(self._context.request, self.base, kind)
            except Refused as error:
                problems.append({"kind": kind, "message": str(error)})
                continue
            except Exception as error:
                problems.append({"kind": kind, "message": f"Could not export {EXPORTS[kind][2]} ({type(error).__name__})."})
                continue
            term = term or found.pop("term")
            found.pop("term", None)
            files[kind] = found
        with self._lock:
            if files:
                self.state, self.result, self.finished = "exported", {"term": term, "files": files, "problems": problems}, time.monotonic()
                self._closing.set()  # the browser and its profile go now; the files wait KEEP for the window
            else:
                self.state, self.needs, self.place = "signed_in", "export", PLACES["aim"]
                self.message = " ".join(problem["message"] for problem in problems) or "AIM returned nothing to import."
        return {"term": term, "problems": problems, "files": [{"kind": f["kind"], "name": f["name"], "size": f["size"]} for f in files.values()]}

    def _set(self, **values):
        with self._lock:
            for key, value in values.items():
                setattr(self, key, value)


PLACES = {"netid": "Signing in to TAMU…", "password": "Signing in to TAMU…", "working": "Signing in…",
          "stay_signed_in": "Signing in…", "duo_code": "Duo", "duo_push": "Duo", "duo_problem": "Duo", "duo_passcode": "Duo",
          "duo_choose": "Duo", "duo_trust": "Duo", "duo_redirect": "Duo", "policy": "Continuing past AIM's access policy…", "aim": "Signed in to AIM",
          "unknown": "A step Faculty doesn't recognize"}


class AimCaptureBrowser(AimBrowser):
    """AIM FORM CAPTURE (AGENTS.md "ACCOMMODATIONS"): a sign-in exactly like Import's that never exports. Once in AIM it
    shows the live page and stays signed in, so the instructor opens a form there; `form()` reads that form's structure
    when the instructor chooses Record this form. Faculty clicks, types and submits nothing in AIM."""

    capture = True

    def __init__(self, portal, netid=None, password=None):
        super().__init__(portal, netid, password, kinds=())

    def status(self):
        return {**super().status(), "capture": True}

    def export(self, kinds):
        raise ValueError("This window records AIM's form; it does not export.")

    def form(self):
        """FORM_SCAN of the page the instructor has open, read on the browser's thread; only from an AIM page."""
        if self.state != "signed_in":
            raise ValueError("Sign in to AIM first.")
        done = Future()
        self._commands.put((self._read_form, done))
        try:
            return done.result(timeout=30)
        except FutureTimeout:
            raise ValueError("AIM's page did not answer in time. Try again.")

    def _read_form(self):
        frame, aim = self._page().main_frame, urlsplit(self.base)

        def on_aim(url):
            parts = urlsplit(url or "")
            return (parts.scheme, parts.netloc) == (aim.scheme, aim.netloc) and parts.path.lower().startswith(aim.path.lower())
        if not on_aim(frame.url):
            raise ValueError("Open AIM's form in the page first.")
        found = frame.evaluate(FORM_SCAN)
        if not isinstance(found, dict) or not on_aim(found.get("url")):  # the page moved on while it was read
            raise ValueError("Open AIM's form in the page first.")
        return found

    def _on_aim(self, frame, text, inputs, now):
        if not self.show_page:
            self._set(show_page=True)


class AimSessions:
    """At most one sign-in of a kind (`browser`: AimBrowser, or the subclass for another site: Canvas's, Howdy's, Google's) at a
    time; starting another ends the earlier one.
    `capture` (AIM's: AimCaptureBrowser) is the form capture window, the same one sign-in at a time."""

    def __init__(self, browser=None, ended="This AIM sign-in has ended. Choose Import from AIM to start again.", capture=None):
        self._lock = threading.Lock()
        self._current = None
        self._browser, self._ended, self._capture = browser or AimBrowser, ended, capture

    def start(self, portal, netid=None, password=None, capture=False):
        if capture and self._capture is None:
            raise ValueError("This sign-in window does not record forms.")
        with self._lock:
            if self._current:
                self._current.close()
            self._current = (self._capture if capture else self._browser)(portal, netid, password)
            return self._current

    def install(self, make):
        """Start the sign-in `make()` builds (Send to AIM's, SCHEDULING "SEND TO AIM"), ending the earlier one as `start` does."""
        with self._lock:
            if self._current:
                self._current.close()
            self._current = make()
            return self._current

    def sending(self):
        """1 while a Send to AIM (SCHEDULING) is signing in or writing, else 0: the restart check waits for it."""
        with self._lock:
            current = self._current
        return int(bool(current and getattr(current, "sends", False) and current.state in ("starting", "signing_in", "signed_in", "exporting")))

    def get(self, session_id):
        with self._lock:
            current = self._current
            if current and current.expired():
                current.close()
                self._current = current = None
        if not current or not hmac.compare_digest(current.id, str(session_id)):
            raise Gone(self._ended)
        return current

    def close(self, session_id):
        with self._lock:
            current = self._current
            if current and hmac.compare_digest(current.id, str(session_id)):
                current.close()
                self._current = None


def register(app, api):
    """Routes on the accommodations blueprint. The window starts a sign-in with the instructor's NetID and password (or
    without them, to sign in on the page), polls its state, answers Duo, and fetches each export once before closing it.
    Bodies carrying credentials are never logged or repeated."""
    sessions = app.extensions.setdefault("aim_sessions", AimSessions(capture=AimCaptureBrowser))
    sign_in_routes(api, "/aim", sessions, lambda: current_app.config.get("AIM_PORTAL", PORTAL))

    @api.get("/aim/captures")
    def aim_captures():
        """The forms recorded so far, newest first: the aim.form_capture Inbox jobs not deleted in the Inbox."""
        inbox = current_app.extensions["inbox"]
        with connect(inbox.path) as db:
            rows = db.execute("SELECT j.id FROM inbox_jobs j JOIN inbox_messages m ON m.id=j.message_id WHERE j.action='aim.form_capture' AND "
                              + inbox.PRESENT.format("m.id") + " ORDER BY m.received_at DESC,j.id DESC LIMIT 50").fetchall()
            found = [capture_entry(inbox.get(row["id"], db, events=False, roles="input")) for row in rows]
        return jsonify(captures=[entry for entry in found if entry])

    @api.post("/aim/<session_id>/record")
    def aim_record(session_id):
        """Record this form: the structure of the AIM form the instructor has open, kept as a workspace Inbox artifact.
        No field value, cookie, picture or address query is kept or logged."""
        session = sessions.get(session_id)
        if getattr(session, "capture", False) is not True:
            raise ValueError("This window is not recording AIM's form.")
        raw = session.form()
        roster = current_app.extensions["student_notes"].students()
        kept = form_structure(raw, [student["name"] for group in roster["groups"] for student in group["students"]])
        metadata = {"page": kept["page"], "controls": len(kept["controls"]), "buttons": len(kept["buttons"]), "dropped": kept["dropped"]}
        name = f"aim-form-{campus_now():%Y%m%d-%H%M%S}.json"
        content = json.dumps(kept, indent=2, ensure_ascii=False).encode("utf-8")
        job = current_app.extensions["inbox"].submit({"action": "aim.form_capture", "metadata": metadata},
                                                     [{"filename": name, "content": content}], source="web", idempotency_key=str(uuid.uuid4()))
        log.info("AIM form recorded from %s", kept["page"])
        return jsonify(capture_entry(job)), 201

    @api.post("/aim/<session_id>/export")
    def aim_export(session_id):
        data = request.get_json(silent=True)
        kinds = data.get("kinds") if isinstance(data, dict) else None
        if not isinstance(kinds, list) or not kinds or any(not isinstance(kind, str) or kind not in EXPORTS for kind in kinds) or len(set(kinds)) != len(kinds):
            raise ValueError("Choose accommodations, DR exams or both.")
        return jsonify(sessions.get(session_id).export(kinds))

    @api.get("/aim/<session_id>/files/<kind>")
    def aim_file(session_id, kind):
        found = sessions.get(session_id).file(kind)
        return Response(found["content"], mimetype=XLSX, headers={"Content-Disposition": f'attachment; filename="{found["name"]}"'})


def capture_entry(job):
    """One recorded form as the AIM window lists it, or None for a job without its one JSON file."""
    files = [artifact for artifact in job["artifacts"] if artifact["role"] == "input"]
    metadata = job["parameters"].get("metadata") if isinstance(job["parameters"], dict) else None
    if len(files) != 1 or not isinstance(metadata, dict) or not {"page", "controls", "buttons", "dropped"} <= set(metadata):
        return None
    return {"id": files[0]["id"], "name": files[0]["filename"], **{key: metadata[key] for key in ("page", "controls", "buttons", "dropped")},
            "received_at": job["received_at"], "download": f"/api/inbox/artifacts/{files[0]['id']}/download"}


def sign_in_routes(api, prefix, sessions, portal):
    """The sign-in window's routes under `prefix` on `api` (AIM's, and those of Canvas `canvas/signin.py`, Howdy `howdy_import.py`
    and the department Google sign-in `absences/google_signin.py`): whether a sign-in is saved, start, status, typed credentials,
    Duo answers, the live page and close. `portal()` is where the browser starts."""
    name = prefix.strip("/")

    @api.get(f"{prefix}/saved", endpoint=f"{name}_saved")
    def aim_saved():
        """Whether a saved sign-in exists, and for which NetID (never the password)."""
        try:
            found = saved_sign_in()
        except ValueError as error:
            return jsonify(saved=False, netid="", problem=str(error))
        return jsonify(saved=bool(found), netid=found[0] if found else "", problem="")

    @api.post(prefix, endpoint=f"{name}_start")
    def aim_start():
        data = request.get_json(silent=True) or {}
        data = data if isinstance(data, dict) else {}
        if data.get("saved") is True:
            found = saved_sign_in()
            if not found:
                raise ValueError("No sign-in is saved on the server. Type your NetID and password instead.")
            netid, password = found
        elif data.get("netid") or data.get("password"):
            found = check_credentials(data)
            netid, password = found["netid"], found["password"]
        else:
            netid = password = None
        session = sessions.start(portal(), netid, password, capture=data.get("capture") is True)
        return jsonify(session.status()), 201

    @api.get(f"{prefix}/<session_id>", endpoint=f"{name}_status")
    def aim_status(session_id):
        session = sessions.get(session_id)
        session.watch()
        return jsonify(session.status())

    @api.post(f"{prefix}/<session_id>/credentials", endpoint=f"{name}_credentials")
    def aim_credentials(session_id):
        found = check_credentials(request.get_json(silent=True), need_netid=False)
        sessions.get(session_id).credentials(found["netid"], found["password"])
        return jsonify(accepted=True), 202

    @api.post(f"{prefix}/<session_id>/answer", endpoint=f"{name}_answer")
    def aim_answer(session_id):
        sessions.get(session_id).answer(check_answer(request.get_json(silent=True)))
        return jsonify(accepted=True), 202

    @api.post(f"{prefix}/<session_id>/show", endpoint=f"{name}_show")
    def aim_show(session_id):
        sessions.get(session_id).show()
        return jsonify(shown=True)

    @api.get(f"{prefix}/<session_id>/frame", endpoint=f"{name}_frame")
    def aim_frame(session_id):
        after = request.args.get("after", type=int)
        number, picture = sessions.get(session_id).picture(after)
        if not picture or (after is not None and number <= after):
            return Response(status=204, headers={"X-Aim-Frame": str(number)})
        return Response(picture, mimetype="image/jpeg", headers={"X-Aim-Frame": str(number)})

    @api.post(f"{prefix}/<session_id>/input", endpoint=f"{name}_input")
    def aim_input(session_id):
        events = check_events(request.get_json(silent=True))
        sessions.get(session_id).send(events)
        return jsonify(accepted=len(events)), 202

    @api.post(f"{prefix}/<session_id>/close", endpoint=f"{name}_close")
    def aim_close(session_id):
        sessions.close(session_id)
        return jsonify(closed=True)
