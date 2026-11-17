"""Import rosters from Howdy (requirement, 2026-11-01). Howdy, TAMU's portal, has no export address: its Instructor/Associate › My Courses page reads each class
roster as JSON from Howdy's own API (POST /api/class-rosters {crn, termCode}, with a Bearer token the page holds) and its
"Export Roster to CSV" button builds the file in the browser from those rows (mapped 2026-11-01 in the instructor's Chrome).

The AIM sign-in window (`aim_import.AimBrowser`) signs in to Howdy instead: the saved NetID sign-in, Microsoft and Duo exactly as
for AIM. Howdy sits behind Cloudflare, which never lets a headless browser through, so this one runs an ordinary Chromium on a
private virtual display (`aim_import.virtual_display`). Once Howdy shows its signed-in dashboard, the browser asks Howdy's API,
from Howdy's own page and with the token that page uses, for the instructor's courses of the term and each chosen section's roster,
then writes each roster as the CSV Howdy's own export makes (`roster_csv`). The window hands those files to Import rosters, which
sends each to the section's ordinary roster import: students are added and updated, nobody is removed, and the registrar's Q drop
and withdrawal marks drop as they do for an uploaded file.

Nothing is kept or written to Howdy: the password is held only until typed; the token and the rosters live in the browser and in
memory until the window fetches the files (KEEP at most); no token, cookie, row or file is logged."""
from __future__ import annotations

import csv
import io
import json
import logging
import re
import time
from urllib.parse import urlsplit

from flask import Blueprint, Response, current_app, jsonify, request
from werkzeug.exceptions import Gone, NotFound

import aim_import as aim
from course_groups import group_courses
from course_storage import live_offerings
from rosters import connect

log = logging.getLogger(__name__)

HOST = "howdyportal.tamu.edu"
PORTAL = f"https://{HOST}/uPortal/azureSignIn"  # Howdy's own "Sign In": straight to TAMU's Microsoft sign-in
DASHBOARD = "/uPortal/f/howdy-dashboard/normal/render.uP#MyCourses"  # Instructor/Associate › My Courses, on the portal's host
SEASONS = {"Spring": "1", "Summer": "2", "Fall": "3"}  # TAMU term codes: year, season, campus (1 College Station): Fall 2026 = 202631
CHECK_EVERY = 2.0    # seconds between asks whether Howdy's API answers this sign-in
TOKEN_WAIT = 20      # seconds the dashboard has to show a token before Faculty asks Howdy's userinfo for one
JWT = re.compile(r"[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")
CSV_TYPE = "text/csv; charset=utf-8"

# Howdy's "Export Roster to CSV" columns (2026-11-01, with Show Columns as it opens), each from its JSON field. Howdy's own
# headers break lines inside a cell; the roster import folds them to "Reg Status" and so on, as for an uploaded file. Howdy's
# "Status" column ("Minor" or empty) has no field Faculty has seen, so it is left out: a student's earlier value stays.
COLUMNS = (("Name", "FULL_NAME"), ("UIN", "UIN"), ("Reg\nStatus", "STVRSTS_DESC"), ("Major", "MAJOR"), ("Class", "CLASSIFICATION"),
           ("Degree\nCandidates", "DEGREE_CANDIDATE"), ("Midterm\nRequired", "MIDTERM_REQUIRED"), ("Midterm", "SFRSTCR_GRDE_CODE_MID"),
           ("Final", "SFRSTCR_GRDE_CODE"), ("Updated\nFinal", "UPDATED_FINAL_GRADE"), ("Email", "EMAIL_ADDRESS"))

# One call to Howdy's API from Howdy's own page (same origin, its cookies), as the page itself makes it. `token` is the page's.
FETCH = r"""async ({path, body, token, limit}) => {
  const headers = {"Accept": "application/json"};
  if (body !== null) headers["Content-Type"] = "application/json";
  if (token) headers["Authorization"] = "Bearer " + token;
  const answer = await fetch(path, {method: body === null ? "GET" : "POST", headers, credentials: "include", cache: "no-store",
                                    body: body === null ? undefined : JSON.stringify(body)});
  const text = await answer.text();
  return {status: answer.status, text: text.length > limit ? "" : text, size: text.length};
}"""


def course_code(code):
    """"MATH 140" (Faculty) and "MATH-140" (Howdy's COURSE without its section) as one key."""
    return "-".join(str(code or "").upper().replace("-", " ").split())


def term_codes(term, year):
    """Howdy's term code for a Faculty term at College Station, the campus Faculty's courses meet on; [] for "Other"."""
    season = SEASONS.get(term)
    return [f"{int(year)}{season}1"] if season and year else []


def records(found, key):
    """Every JSON object under `found` that has `key` (Howdy wraps some lists; this does not depend on how)."""
    stack, seen = [found], []
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            if key in item:
                seen.append(item)
            else:
                stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(reversed(item))
    return seen


def other_codes(found, prefix):
    """Term codes in Howdy's term list that start with `prefix` (year and season): another campus's, tried when College
    Station's lists none of the course."""
    codes = set()
    for item in records(found, "termCode") + records(found, "TERM_CODE") + records(found, "code"):
        for value in item.values():
            if isinstance(value, (str, int)) and re.fullmatch(r"\d{6}", str(value)) and str(value).startswith(prefix):
                codes.add(str(value))
    return sorted(codes)


def text(value):
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return " ".join(str(value).split()) if not isinstance(value, str) else value.strip()


def roster_csv(rows):
    """Howdy's roster rows as the CSV its own export writes (all fields quoted, CRLF), in Howdy's order."""
    out = io.StringIO()
    writer = csv.writer(out, quoting=csv.QUOTE_ALL, lineterminator="\r\n")
    writer.writerow([header for header, _ in COLUMNS])
    for row in rows:
        values = [text(row.get(field)) for _, field in COLUMNS]
        uin = values[1]
        if uin.isdigit() and len(uin) < 9:  # a UIN read as a number loses its leading zeros
            values[1] = uin.zfill(9)
        writer.writerow(values)
    return out.getvalue().encode("utf-8")


def section_of(course):
    """(subject-number, section) of a Howdy course row: "MATH-140-507" → ("MATH-140", 507); None when it does not read so."""
    match = re.fullmatch(r"\s*([A-Za-z]+)[-\s]*(\w+)[-\s]+(\d{1,4})\s*", str(course.get("COURSE") or ""))
    return (course_code(f"{match[1]} {match[2]}"), int(match[3])) if match else None


class HowdyBrowser(aim.AimBrowser):
    TARGET, NAME, FINAL, HEADLESS = "howdy", "Howdy", ("exported",), False
    ENDED = "This Howdy sign-in has ended. Choose Import from Howdy to start again."

    def __init__(self, portal, netid=None, password=None, plan=None):
        self._plan, self._token, self._checked, self._went, self._held = plan, "", 0.0, 0.0, False
        parts = urlsplit(portal)
        self.host, self.dashboard = parts.netloc, f"{parts.scheme}://{parts.netloc}{DASHBOARD}"
        super().__init__(portal, netid, password, kinds=())  # starts the browser's thread: everything it reads is set above

    def _opened(self, context):
        context.on("request", self._heard)

    def _heard(self, request):
        """The Bearer token Howdy's own page sends to Howdy's API: kept in memory for this browser only, never logged."""
        try:
            parts = urlsplit(request.url)
            found = request.headers.get("authorization", "")
        except Exception:
            return
        if parts.netloc == self.host and parts.path.startswith("/api/") and found.lower().startswith("bearer "):
            token = found[7:].strip()
            if JWT.fullmatch(token):
                self._token = token

    def _classify(self, url, text_, inputs):
        parts = urlsplit(url)
        words = " ".join((text_ or "").split()).lower()
        if re.search(r"performing security verification|verify you are human|just a moment", words):
            return "checking"  # Cloudflare's check of the browser: passes by itself; shown if it does not
        if parts.netloc == self.host:
            path = parts.path.lower()
            if "signin" in path or "login" in path or "/cas" in path:
                return "working"
            if re.search(r"\bsign out\b|\blog ?out\b|\bsign off\b", words):
                return "howdy"
            if re.search(r"\bsign in\b", words):
                return "guest"
            return "howdy" if words and not re.search(r"\bloading\b|please wait", words) else "working"
        if parts.hostname and parts.hostname.endswith("howdy.tamu.edu"):
            return "guest"  # Howdy's public home page: sign in from Howdy's portal instead
        return aim.classify(url, text_, inputs, "https://aim.invalid/")  # TAMU's, Microsoft's and Duo's steps, as for AIM

    def _on_checking(self, frame, text_, inputs, now):
        return False  # nothing to press: Cloudflare decides; after STEP_WAIT the window shows the page

    def _on_guest(self, frame, text_, inputs, now):
        """Howdy shows its signed-out page: go to its Sign In, at most twice (a sign-in that keeps ending here is shown)."""
        if self._went >= 2:
            return False
        if self._ready("guest", now):
            self._went += 1
            self._page().goto(self.portal, wait_until="commit", timeout=60000)

    def _on_howdy(self, frame, text_, inputs, now):
        if self._exported or self._held or now - self._checked < CHECK_EVERY:
            return
        self._checked = now
        page = self._page()
        if not self._token and "courses" not in self._did:
            self._did["courses"] = now  # once: My Courses, where the page asks Howdy's API with its token
            if "#mycourses" not in page.url.lower():
                page.goto(self.dashboard, wait_until="commit", timeout=60000)
            return
        if not self._token and now - self._did["courses"] > TOKEN_WAIT:
            self._token = self._userinfo(page)
        if not self._token:
            return
        self._exported = True
        self._read(page)

    def _call(self, page, path, body=None):
        """(status, parsed JSON or None) of one call to Howdy's API from its own page."""
        found = page.evaluate(FETCH, {"path": path, "body": body, "token": self._token, "limit": aim.LIMIT})
        if not isinstance(found, dict):
            return 0, None
        try:
            return int(found.get("status") or 0), json.loads(found.get("text") or "null")
        except ValueError:
            return int(found.get("status") or 0), None

    def _userinfo(self, page):
        """The token uPortal gives its own pages (GET /uPortal/api/v5-1/userinfo), when the page has not shown one."""
        try:
            found = page.evaluate(FETCH, {"path": "/uPortal/api/v5-1/userinfo", "body": None, "token": "", "limit": 100000})
        except Exception:
            return ""
        match = JWT.search(str((found or {}).get("text") or "")) if isinstance(found, dict) and found.get("status") == 200 else None
        return match[0] if match else ""

    def _read(self, page):
        plan = self._plan
        self._set(state="exporting", place="Reading rosters from Howdy…")
        files, problems, term = {}, [], ""
        try:
            if not plan:
                raise aim.Refused("Start Import from Howdy from a course's Import rosters.")
            courses, code = self._courses(page, plan)
            term = f"{plan['label']} ({code})" if code else plan["label"]
            stamp = time.strftime("%m-%d-%Y", time.localtime())
            for number in plan["sections"]:
                course = courses.get(number)
                name = f"{plan['code']} {number}"
                if not course:
                    problems.append({"kind": str(number), "message": f"Howdy lists no {name} among your {plan['label']} courses."})
                    continue
                status, rows = self._call(page, "/api/class-rosters", {"crn": str(course["CRN"]), "termCode": code})
                rows = records(rows, "UIN") if status == 200 else None
                if not rows:
                    problems.append({"kind": str(number), "message": f"Howdy did not give the roster of {name}" +
                                     (f" (it answered {status})." if status != 200 else " (no students).")})
                    continue
                content = roster_csv(rows)
                filename = f"{str(course['COURSE']).strip()}_{code}-{stamp}.csv"
                files[str(number)] = {"kind": str(number), "name": filename, "size": len(content), "content": content}
        except aim.Refused as error:
            problems.append({"kind": "", "message": str(error)})
        except Exception as error:
            problems.append({"kind": "", "message": f"Howdy's rosters could not be read ({type(error).__name__})."})
        with self._lock:
            if files:
                self.state, self.result, self.finished = "exported", {"term": term, "files": files, "problems": problems}, time.monotonic()
                self._closing.set()  # the browser, its profile and the token go now; the files wait KEEP for the window
            else:
                self.state, self.needs, self.place = "signed_in", "export", "Signed in to Howdy"
                self.message = " ".join(problem["message"] for problem in problems) or "Howdy returned no roster."
                self._exported, self._held = False, True  # read again only on the window's Try again
        log.info("Howdy rosters read: %s of %s sections", len(files), len((plan or {}).get("sections", [])))

    def _courses(self, page, plan):
        """{section number: Howdy course row} of the plan's course in its term, and the term code they are under."""
        codes = term_codes(plan["term"], plan["year"])
        tried = []
        for attempt in range(2):
            for code in codes:
                if code in tried:
                    continue
                tried.append(code)
                status, found = self._call(page, "/api/get-instructor-courses", {"termCode": code})
                if status in (401, 403):
                    raise aim.Refused("Howdy did not let Faculty read your courses. Close this window and try again.")
                mine = {}
                for course in records(found, "CRN") if status == 200 else []:
                    key = section_of(course)
                    if key and key[0] == course_code(plan["code"]) and key[1] in plan["sections"]:
                        mine.setdefault(key[1], course)
                if mine:
                    return mine, code
            if attempt or not codes:
                break
            status, found = self._call(page, "/api/terms")  # another campus of the same season, as Howdy lists them
            codes = other_codes(found, codes[0][:5]) if status == 200 else []
        if not tried:
            raise aim.Refused(f"Howdy has no term code for {plan['label']}.")
        return {}, tried[0]

    def export(self, kinds):
        """Try again after Howdy gave nothing: read the rosters once more on the page that is signed in."""
        if self.state != "signed_in":
            raise ValueError("Sign in to Howdy first.")
        self._set(needs="", message="")
        self._checked, self._held = 0.0, False
        return {"started": True}

    def file(self, kind):
        with self._lock:
            found = (self.result or {}).get("files", {}).get(kind)
        if not found:
            raise Gone("That roster is not here any more. Import from Howdy again.")
        return found

    def close(self):
        super().close()
        self._token = ""

    def status(self):
        found = super().status()
        found["files"] = [{**item, "section": item["kind"]} for item in found["files"]]
        return found


aim.PLACES.update({"howdy": "Signed in to Howdy", "guest": "Opening Howdy's sign-in…", "checking": "Howdy is checking the browser…"})


def plan_for(path, group_id):
    """What Import from Howdy reads for a course page: its code, term, year and sections (catalog only)."""
    with connect(path) as db:
        courses = live_offerings(db)
    group = next((group for group in group_courses(courses) if group["id"] == group_id), None)
    if not group:
        raise NotFound("This course no longer exists.")
    if not group["sections"]:
        raise ValueError("This course has no sections to import.")
    if not term_codes(group["term"], group["year"]):
        raise ValueError("Import from Howdy needs a Fall, Spring or Summer term and its year on this course.")
    return {"code": group["code"], "term": group["term"], "year": int(group["year"]), "sections": list(group["sections"]),
            "label": f"{group['term']} {group['year']}"}


def register(app, rosters):
    """`/api/howdy...`: the AIM window's routes for this browser, the start from a course page, and the roster files."""
    sessions = app.extensions.setdefault("howdy_sessions", aim.AimSessions(HowdyBrowser, HowdyBrowser.ENDED))
    api = Blueprint("howdy_import", __name__, url_prefix="/api")
    portal = lambda: current_app.config.get("HOWDY_PORTAL", PORTAL)  # noqa: E731
    aim.sign_in_routes(api, "/howdy", sessions, portal)

    @api.post("/course-groups/<group_id>/howdy-rosters")
    def howdy_rosters(group_id):
        """Import from Howdy for a course page: the saved sign-in (`saved`), a typed one, or none (sign in on the page)."""
        data = request.get_json(silent=True) or {}
        data = data if isinstance(data, dict) else {}
        plan = plan_for(rosters.path, group_id)
        netid = password = None
        if data.get("saved") is True:
            found = aim.saved_sign_in()
            if not found:
                raise ValueError("No sign-in is saved on the server. Type your NetID and password instead.")
            netid, password = found
        elif data.get("netid") or data.get("password"):
            found = aim.check_credentials(data)
            netid, password = found["netid"], found["password"]
        session = sessions.install(lambda: HowdyBrowser(portal(), netid, password, plan=plan))
        return jsonify(session.status()), 201

    @api.post("/howdy/<session_id>/export")
    def howdy_again(session_id):
        return jsonify(sessions.get(session_id).export(()))

    @api.get("/howdy/<session_id>/files/<kind>")
    def howdy_file(session_id, kind):
        found = sessions.get(session_id).file(kind)
        return Response(found["content"], mimetype=CSV_TYPE, headers={"Content-Disposition": f'attachment; filename="{found["name"]}"',
                                                                      "Cache-Control": "no-store"})

    app.register_blueprint(api)
    return sessions
