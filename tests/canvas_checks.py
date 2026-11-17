"""The Canvas browser scripts' shared set-up: the app with Faculty's kept Canvas session, MATH 140 with Sections 507 and 508 and a
roster each, and both sections linked to the courses of a loopback FakeCanvas. Not a check itself (no `browser_` prefix)."""
import io
import os

from app import create_app
from browser_course_pages import seed_course
from canvas import web
from rosters import connect
from test_rosters import STUDENTS, csv_bytes
from test_storage import HEADERS

CAROL = "222333444"


def canvas_linked_app(directory, fake, course_a, course_b):
    """The app, its test client, the course group, the term and the term's gradebook route, once Section 507 (Alice and Bob) is linked to
    `course_a` and Section 508 (Carol) to `course_b`."""
    app = create_app(os.path.join(directory, "data"))
    client = app.test_client()
    client.get("/").close()
    settings = web.Settings(session_file=os.path.join(directory, "config", "canvas-session.json"), base=fake.url)
    app.extensions["canvas"].settings = settings
    web.save(settings, fake.session(), user={"name": "Pat Instructor"})  # Faculty's kept Canvas session
    seed_course(client, code="MATH 140", sections=[507, 508], multiple_sections=True)
    group = client.get("/api/state").json["course_groups"][0]
    rows = [*STUDENTS, ["", "Example, Carol", CAROL, "Registered", "MATH", "U1", "", "Y", "", "", "", "carol@example.test"]]
    for number, chosen in ((507, rows[:2]), (508, rows[2:])):
        answer = client.post(f"/api/course-groups/{group['id']}/sections/{number}/roster",
                             data={"file": (io.BytesIO(csv_bytes(chosen)), "roster.csv")}, headers=HEADERS)
        assert answer.status_code == 201, answer.json
    with connect(app.config["DATABASE"]) as db:
        sections = {r["number"]: r["id"] for r in db.execute("SELECT id,number FROM course_sections")}
    for number, course in ((507, course_a), (508, course_b)):
        answer = client.post(f"/api/canvas/sections/{sections[number]}", json={"course_id": course, "base": ""}, headers=HEADERS)
        assert answer.status_code == 200, answer.json
    term = client.get("/api/gradebook").json["terms"][0]["id"]
    base = f"/api/gradebook/terms/{term}"
    return app, client, group, term, base
