"""Seed a synthetic Faculty workspace and serve it on a fixed port, for design screenshots.

Usage: python3 demo_serve.py PORT [STATE_DIR]
Writes STATE_DIR/routes-PORT.txt with the seeded ids (group, term, plan, room, chart, job, event) that
app_shots.py reads. Synthetic data only (the browser tests' fixtures); nothing leaves the process.
Needs the repo's Python requirements and Bubblewrap (the scan fixtures are inspected in the document sandbox).
"""
import io
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(REPO), str(REPO / "tests")]
os.chdir(REPO)
from waitress.server import create_server  # noqa: E402
from app import create_app  # noqa: E402
import browser_gradebook  # noqa: E402
from test_bubble_sheets import HEADERS  # noqa: E402
from test_seating import fixture  # noqa: E402

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
STATE = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(tempfile.gettempdir()) / "faculty-demo"
STATE.mkdir(parents=True, exist_ok=True)
directory = tempfile.mkdtemp(dir=STATE)
app = create_app(directory)
client = app.test_client()
data = browser_gradebook.seed(app, client)
client.post("/api/rooms", data={"name": "HECC 105", "file": (io.BytesIO(fixture("HECC 105")), "HECC 105.xlsx")}, headers=HEADERS)
room = client.get("/api/rooms").json[0]
catalog = client.get("/api/seating/catalog").json
created = client.post("/api/seating", json={"assignment_id": data["assignment"]["id"],
    "section_ids": [s["id"] for s in catalog["sections"]], "room_id": room["id"], "exam_version_count": 2}, headers=HEADERS)
state = client.get("/api/state").json
jobs = client.get("/api/inbox/jobs").json
jobs = jobs.get("jobs", jobs.get("items", [])) if isinstance(jobs, dict) else jobs
scan = next((j for j in jobs if j.get("action") == "scan.receive"), jobs[0] if jobs else {})
exams = client.get("/api/exams").json
exams = exams.get("exams", []) if isinstance(exams, dict) else exams
(STATE / f"routes-{PORT}.txt").write_text("\n".join([
    f"group={state['course_groups'][0]['id']}", f"term={data['term']}", f"plan={data['plan']}", f"room={room['id']}",
    f"chart={created.json.get('id', '') if created.status_code == 201 else ''}", f"job={scan.get('id', '')}",
    f"event={exams[0]['id'] if exams else ''}"]))
server = create_server(app, host="127.0.0.1", port=PORT)
print("serving", PORT, flush=True)
server.run()
