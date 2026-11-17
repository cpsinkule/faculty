"""POSTING GRADES (docs/decisions/canvas.md; requirement, 2026-10-06): a gradebook column's grades
posted (shown to students) or hidden in EVERY Canvas course the column goes to (Canvas's "Everyone"), only ever by a Sync with
Canvas, the instructor's or the morning one.

- THE CHOICE. Post or Hide in a column's menu (`choose`) records the instructor's standing choice, or takes it back to the
  category's rule (`gradebook_canvas_posting_choices`, TERM-owned, append-only). Nothing is asked of Canvas then: the grid shows
  what the next sync does (`marks`' `pending`). The assistant's choice is an Approve card (`confirmation: "choose"`).
- AT EVERY SYNC (`after_sync`), after its send: the posting steps the sync planned from its own read (gradebook/posting_policy.py,
  each column's choice or rule) are done course by course. A post is done in a course only when every grade this sync sent there
  for the column was accepted; a hide always. The courses' assignments are read once more, and only what is left is asked. Each
  column's post or hide is ONE `gradebook_canvas_posts` row with an `uncertain` receipt per course recorded BEFORE Canvas is asked;
  `run` asks Canvas (its GraphQL `postAssignmentGrades` / `hideAssignmentGrades`, `canvas.web.Client.post_grades` /
  `hide_grades`), follows Canvas's progress and READS BACK every submission's `posted_at`: the receipt is settled `accepted`
  (every grade shows, or none does), `failed` (Canvas refused, or said it failed) or `uncertain`. The morning sync never asks
  again by itself where the latest receipt is uncertain (posting_policy `unsure`).
- CONFIRMED BY A LATER READ (gradebook/canvas_confirm.py; requirement, 2026-10-10): a post withheld in a course because a grade
  the sync sent there was not confirmed says so in its errors (`waits` "grades", with the course and assignment). Once the morning's
  follow-up read shows every such grade in Canvas, `follow_posts` carries that post out there: ONE more `gradebook_canvas_posts`
  row per column and course (request ID `policy:<send>:<column>:post:<course>`, `progress.confirmed` naming the course), asked
  only where Canvas still hides a grade and only while the column should still be posted; never a second time.
- A RESTART WAITS for the posts after a sync (`canvas/stopping.py`, key "after:<send>").
- WHAT FACULTY LAST READ of an assignment's posting is kept (`gradebook_canvas_posting`), for the grid's "hidden in Canvas" mark:
  by every Sync with Canvas (its summary's read, in its own transaction, and each sent assignment's read-back), the morning import
  and every post or hide. A read that shows nothing new records nothing. Only the course's current students count (an active or
  invited enrollment): what Post or Hide can change.
- A column whose category posts by hand has its Canvas assignment switched to post by hand by the sync's send, before its grades
  (GradebookService._switch_policy); nothing here changes a posting policy.
"""
import json
import logging
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

from werkzeug.exceptions import Conflict, NotFound, ServiceUnavailable

from course_storage import timestamp
from rosters import connect

from . import blanks, canvas_export, common, posting_policy

LOG = logging.getLogger(__name__)
ACTIONS = ("post", "hide")
WAIT = 120  # seconds Canvas's post or hide may take before it is only read back
COURSES = 4  # Canvas courses asked at once
# A post withheld in a course whose grades of this sync Canvas did not confirm (its error's `waits` is "grades").
UNCONFIRMED = "Not posted here: some of its grades did not reach Canvas in this sync. Sync again."
counts = posting_policy.counts


def done(action, found):
    """Whether Canvas, read back, shows the action done: every submission posted, or none."""
    return found["unposted"] == 0 if action == "post" else found["posted"] == 0


def left(action, found):
    """How many grades the action would still change: hidden grades to post, or shown grades to hide."""
    return found["hidden"] if action == "post" else found["posted"]


# --- what Faculty last read (the grid's mark) -------------------------------------------------------------------------------

def where(svc, db, term_id, setup, links, canvas):
    """{(item id, course id): Canvas assignment row | {"problem"}} of every column: the To-Canvas half's own rule."""
    remembered = svc._remembered(db, term_id, "canvas", sync=True)
    copied = svc._copy_links(db, term_id)[0]
    return canvas_export.targets(setup, links, canvas, remembered, copied)


def read_rows(found, canvas):
    """[{item_id, course_id, assignment_id, manual, graded, hidden}] for each column's assignment whose submissions `canvas` (the
    shape `canvas_export.from_read` gives) holds."""
    rows, index = [], posting_policy.by_assignment(canvas)
    for (item_id, course), target in sorted(found.items()):
        if "problem" in target or not target.get("id") or course not in canvas:
            continue
        if "read" in canvas[course] and target["id"] not in canvas[course]["read"]:
            continue
        # An assignment read with no current students says nothing about its posting: recording graded=0 would take a POSTED
        # column out of `posted()` and stop ZERO ONCE DUE for every course of it, so Faculty's last read stands.
        submissions = index.get((course, target["id"]))
        if not submissions:
            continue
        seen = counts(submissions)
        rows.append({"item_id": item_id, "course_id": course, "assignment_id": target["id"], "manual": bool(target.get("post_manually")),
                     "graded": seen["graded"], "hidden": seen["hidden"]})
    return rows


def record(db, term_id, rows, source, read_at=None):
    """Keeps each row whose posting differs from what Faculty last read of that assignment (inside the caller's transaction)."""
    read_at = read_at or timestamp()
    kept = 0
    for row in rows:
        last = db.execute("""SELECT item_id,manual,graded,hidden FROM gradebook_canvas_posting WHERE term_id=? AND course_id=? AND assignment_id=?
            ORDER BY read_at DESC,rowid DESC LIMIT 1""", (term_id, row["course_id"], row["assignment_id"])).fetchone()
        if last and (last["item_id"], bool(last["manual"]), last["graded"], last["hidden"]) == (row["item_id"], row["manual"], row["graded"], row["hidden"]):
            continue
        db.execute("INSERT INTO gradebook_canvas_posting VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                   (str(uuid.uuid4()), term_id, row["item_id"], row["course_id"], row["assignment_id"], int(row["manual"]), row["graded"],
                    min(row["hidden"], row["graded"]), source, read_at, timestamp()))
        kept += 1
    return kept


def record_read(svc, term_id, links, read, source):
    """The morning run's read, kept for the grid's mark (best effort: never fails the run)."""
    from .service import _setup
    try:
        canvas = canvas_export.from_read(read)
        with svc.rosters.lock(), connect(svc.path) as db:
            db.execute("BEGIN IMMEDIATE")
            setup = _setup(db, term_id)[0]
            return record(db, term_id, read_rows(where(svc, db, term_id, setup, links, canvas), canvas), source)
    except Exception:  # noqa: BLE001 - only the mark depends on it
        LOG.exception("Canvas posting state could not be kept")
        return 0


def _latest_reads(db, term_id):
    """What Faculty last read of each Canvas assignment of the term's columns, oldest first (catalog only, one query)."""
    return db.execute("""SELECT p.* FROM gradebook_canvas_posting p WHERE p.term_id=? AND p.rowid=(SELECT x.rowid FROM gradebook_canvas_posting x
            WHERE x.term_id=p.term_id AND x.course_id=p.course_id AND x.assignment_id=p.assignment_id ORDER BY x.read_at DESC,x.rowid DESC LIMIT 1)
            ORDER BY p.read_at,p.rowid""", (term_id,)).fetchall()


def marks(db, term_id, policy=None, chosen=None):
    """The View's `canvas_posting`: {item id: {hidden, graded, courses, manual, read_at, source}} from the latest read of each Canvas
    assignment; with `policy` (`policy` below) each column's desired state (`policy`: state, why, manual, forced, rules) and what
    the next sync does by what Faculty last read (`pending`: ["post" | "hide" | "switch"], only when any), and with `chosen`
    (`choices`) the instructor's standing choice (`choice` "post" | "hide", `choice_id`), a column chosen but never read
    included."""
    found, reads = {}, _latest_reads(db, term_id)
    for row in reads:
        mark = found.setdefault(row["item_id"], {"hidden": 0, "graded": 0, "courses": 0, "manual": False, "read_at": None, "source": None})
        mark["hidden"] += row["hidden"]
        mark["graded"] += row["graded"]
        mark["courses"] += 1
        mark["manual"] = mark["manual"] or bool(row["manual"])
        mark["read_at"], mark["source"] = row["read_at"], row["source"]  # the latest read of the column's assignments
    if policy is None:
        return found
    for item_id, (choice, choice_id) in (chosen or {}).items():
        if item_id in policy and choice in ACTIONS:
            found.setdefault(item_id, {"hidden": 0, "graded": 0, "courses": 0, "manual": False, "read_at": None, "source": None})
            found[item_id].update({"choice": choice, "choice_id": choice_id})
    waiting = posting_policy.pending(policy, [{"item_id": r["item_id"], "manual": bool(r["manual"]), "graded": r["graded"], "hidden": r["hidden"]}
                                              for r in reads])
    for item_id, mark in found.items():
        want = policy.get(item_id)
        if want is None:
            continue
        mark["policy"] = {key: want[key] for key in ("state", "why", "manual", "switch", "forced", "rules")}
        if waiting.get(item_id):
            mark["pending"] = waiting[item_id]
    return found


def posted(db, term_id):
    """The columns whose grades students see in Canvas (GRADEBOOK "ZERO ONCE DUE", POSTED; requirement, 2026-10-08): in the latest read of EVERY Canvas assignment Faculty has read for the
    column, the assignment posts by hand, holds grades and hides none. Automatic posting is left out: Canvas shows each grade as it
    comes in, so it says nothing about the grading being done (catalog only, one query)."""
    shown, other = set(), set()
    for row in db.execute("""SELECT p.item_id,p.manual,p.graded,p.hidden FROM gradebook_canvas_posting p WHERE p.term_id=? AND p.rowid=(SELECT
            x.rowid FROM gradebook_canvas_posting x WHERE x.term_id=p.term_id AND x.course_id=p.course_id AND x.assignment_id=p.assignment_id
            ORDER BY x.read_at DESC,x.rowid DESC LIMIT 1)""", (term_id,)):
        (shown if row["manual"] and row["graded"] > 0 and row["hidden"] == 0 else other).add(row["item_id"])
    return frozenset(shown - other)


def _insert(db, post_id, term_id, item_id, action, send_id, request_id, request_sha, fingerprint, plan, progress, actor):
    """The approval and an uncertain receipt for every course, BEFORE Canvas is asked (inside the caller's transaction)."""
    db.execute("INSERT INTO gradebook_canvas_posts VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
               (post_id, term_id, item_id, action, send_id, request_id, request_sha, fingerprint, json.dumps(plan), "running",
                json.dumps(progress), actor, timestamp(), None))
    for step in plan:
        _receipt(db, post_id, term_id, item_id, step, "uncertain", None, None)


def _receipt(db, post_id, term_id, item_id, step, state, progress_id, seen):
    db.execute("INSERT INTO gradebook_canvas_posted VALUES(?,?,?,?,?,?,?,?,?,?)",
               (str(uuid.uuid4()), post_id, term_id, item_id, step["course_id"], step["assignment_id"], state, progress_id,
                json.dumps(seen or {}), timestamp()))


def run(svc, post_id, term_id, item_id, action, plan, progress):
    """Asks Canvas, course by course (at most COURSES at once), then reads every submission back and settles each receipt. A
    signed-out session ends the post as failed (what Canvas already took stays uncertain); the status always ends."""
    from canvas import web
    lock, outcome = threading.Lock(), set()

    def note(**changes):
        with lock:
            for key, value in changes.items():
                if key == "errors":
                    progress["errors"].extend(value)
                elif key == "signed_out":
                    progress["signed_out"] = True
                else:
                    progress[key] += value
            _progress(svc, post_id, progress)

    def course(step):
        answer, problem, finished = None, "", False
        try:
            with svc.canvas.client(patient=True) as client:
                try:
                    answer = client.post_grades(step["assignment_id"]) if action == "post" else client.hide_grades(step["assignment_id"])
                    deadline = time.monotonic() + WAIT
                    state = answer["state"]
                    while state not in ("completed", "failed") and time.monotonic() < deadline:
                        time.sleep(1)
                        state = str(client.progress(answer["id"]).get("workflow_state") or "")
                    if state == "failed":
                        finished, problem = True, "Canvas said it could not finish."
                    elif state == "completed":
                        finished = True
                except web.SignedOut:
                    raise
                except web.CanvasError as error:
                    problem = str(error)
                    # Canvas refusing the request itself (it answered, 4xx or a refusal in its answer) changed nothing.
                    finished = answer is None and error.refused
                seen = counts(client.assignment_submissions(step["course_id"], step["assignment_id"]).values())
        except web.SignedOut as error:
            outcome.add("failed")
            state = "failed" if answer is None else "uncertain"
            _settle(svc, post_id, term_id, item_id, step, state, answer, None)
            note(done=1, signed_out=True, errors=[{"label": "Canvas", "message": str(error)[:300]}], **{state: 1})
            return
        except Exception as error:  # noqa: BLE001 - the read-back failed: what Canvas did is not known
            outcome.add("failed")
            _settle(svc, post_id, term_id, item_id, step, "failed" if answer is None and finished else "uncertain", answer, None)
            note(done=1, errors=[{"label": _label(step["sections"]), "message": f"{problem or error}"[:300]}],
                 **{"failed" if answer is None and finished else "uncertain": 1})
            return
        state = "accepted" if done(action, seen) else "failed" if finished else "uncertain"
        if state == "failed":
            outcome.add("failed")
        _settle(svc, post_id, term_id, item_id, step, state, answer, seen)
        errors = [{"label": _label(step["sections"]), "message": problem[:300]}] if problem and state != "accepted" else []
        note(done=1, errors=errors, **{state: 1})

    try:
        with ThreadPoolExecutor(max_workers=min(COURSES, len(plan)) or 1, thread_name_prefix=threading.current_thread().name) as pool:
            list(pool.map(course, plan))
    except Exception as error:  # noqa: BLE001 - the post must end with a status
        outcome.add("failed")
        note(errors=[{"label": "Faculty", "message": f"{type(error).__name__}: {error}"[:300]}])
    finally:
        with lock:
            _progress(svc, post_id, progress, "failed" if "failed" in outcome else "done")


def _settle(svc, post_id, term_id, item_id, step, state, answer, seen):
    """The settling receipt and, when Canvas was read back, what it shows now (the grid's mark), in one transaction."""
    with svc.rosters.lock(), connect(svc.path) as db:
        db.execute("BEGIN IMMEDIATE")
        _receipt(db, post_id, term_id, item_id, step, state, (answer or {}).get("id"), seen)
        if seen is not None:
            action = db.execute("SELECT action FROM gradebook_canvas_posts WHERE id=?", (post_id,)).fetchone()[0]
            record(db, term_id, [{"item_id": item_id, "course_id": step["course_id"], "assignment_id": step["assignment_id"],
                                  "manual": bool(step.get("manual", True)), "graded": seen["graded"], "hidden": seen["hidden"]}], action)


def _progress(svc, post_id, progress, status=None):
    with connect(svc.path) as db:
        if status:
            db.execute("UPDATE gradebook_canvas_posts SET progress_json=?,status=?,finished_at=? WHERE id=? AND status='running'",
                       (json.dumps(progress), status, timestamp(), post_id))
        else:
            db.execute("UPDATE gradebook_canvas_posts SET progress_json=? WHERE id=? AND status='running'", (json.dumps(progress), post_id))


def view(svc, row):
    """A post as the page shows it; one left running by a process that no longer runs it ended `interrupted`, by the process that
    runs Canvas writes only (CanvasService.owner): another shows it as it is."""
    if row["status"] == "running" and svc.canvas.owner \
            and not (row["send_id"] and svc.canvas.writes.running(after_key(row["send_id"]))):
        with connect(svc.path) as db:
            db.execute("UPDATE gradebook_canvas_posts SET status='interrupted', finished_at=? WHERE id=? AND status='running'", (timestamp(), row["id"]))
            row = db.execute("SELECT * FROM gradebook_canvas_posts WHERE id=?", (row["id"],)).fetchone()
    return {"id": row["id"], "item_id": row["item_id"], "action": row["action"], "status": row["status"], "send_id": row["send_id"],
            "progress": json.loads(row["progress_json"] or "{}"), "created_at": row["created_at"], "finished_at": row["finished_at"]}


def status(svc, term_id, post_id):
    with connect(svc.path) as db:
        row = db.execute("SELECT * FROM gradebook_canvas_posts WHERE id=? AND term_id=?", (post_id, term_id)).fetchone()
    if not row:
        raise NotFound("That post was not found.")
    return view(svc, row)


def for_send(svc, db, send_id):
    """The posts a Sync with Canvas made after sending (its status read), oldest first."""
    return [view(svc, row) for row in db.execute("SELECT * FROM gradebook_canvas_posts WHERE send_id=? ORDER BY created_at,rowid", (send_id,))]


def outstanding(posts):
    """How many of a sync's posts and hides (`for_send`) still need the instructor, per column and action: one Canvas refused,
    failed or left unread, one cut off, one that could not be started, and one withheld in a course for unconfirmed grades (its
    errors' `waits`) until the follow-up carried it out there (`follow_posts`: a done row whose `progress.confirmed` names that
    course)."""
    made = {(post["item_id"], post["action"], post["progress"]["confirmed"].get("course_id")) for post in posts
            if post["progress"].get("confirmed") and post["status"] == "done"
            and not (post["progress"].get("failed") or post["progress"].get("uncertain"))}
    left = set()
    for post in posts:
        progress, key = post["progress"], (post["item_id"], post["action"])
        errors = progress.get("errors") or []
        withheld_here = [error for error in errors if error.get("waits") == "grades"]
        if progress.get("failed") or progress.get("uncertain") or post["status"] == "interrupted":
            left.add(key)
        elif post["status"] == "failed" and (not errors or len(withheld_here) != len(errors)):
            left.add(key)
        elif any((*key, error.get("course_id")) not in made for error in withheld_here):
            left.add(key)
    return len(left)


# --- the instructor's choice --------------------------------------------------------------------------------------------------

def choices(db, term_id):
    """{column ID: (its latest choice "post" | "hide" | "rule", that row's ID)} (catalog only, one query)."""
    found = {}
    for row in db.execute("SELECT id,item_id,choice FROM gradebook_canvas_posting_choices WHERE term_id=? ORDER BY created_at,rowid", (term_id,)):
        found[row["item_id"]] = (row["choice"], row["id"])
    return found


def policy(svc, db, term_id, setup, chosen=None):
    """Each live column's desired posting (gradebook/posting_policy.py) from the catalog as it is now: its choice, the campus date,
    which linked columns are certified, which columns iClicker's sessions go to."""
    from scheduling.clock import campus_today
    chosen = choices(db, term_id) if chosen is None else chosen
    return posting_policy.policies(setup, choices={item: choice for item, (choice, _) in chosen.items()}, today=campus_today().isoformat(),
                                   certified=blanks.certified_items(db, term_id, setup, every=True), iclicker=frozenset(svc._iclicker_items(db, term_id)))


def unsure(db, term_id):
    """{(column, course)} whose latest post, hide or posting-policy switch Faculty could not read back: the morning sync leaves them
    for the instructor's own Sync (AGENTS.md OUTSIDE SERVICES: an uncertain write is never sent again by itself)."""
    found = set()
    for table in ("gradebook_canvas_posted", "gradebook_canvas_policies"):
        latest = {}
        for row in db.execute(f"SELECT item_id,course_id,state FROM {table} WHERE term_id=? ORDER BY created_at,rowid", (term_id,)):
            latest[(row["item_id"], row["course_id"])] = row["state"]
        found |= {key for key, state in latest.items() if state == "uncertain"}
    return frozenset(found)


def choose(svc, term_id, data, actor="workspace"):
    """THE CHOICE: a column's grades posted (`choice` "post") or hidden ("hide") in Canvas from the next sync on, or back to its
    category's rule ("rule"). Nothing is asked of Canvas. `confirmation: "choose"` (the instructor's dialog, or their click on the
    assistant's Approve card), a `request_id` (8 to 128 characters) and, optionally, `base`: the choice the page showed (its ID,
    null for none), a different one now being a 409. Answers the gradebook (`section_id`: which view)."""
    from .service import _object, _request_id, _setup, _term
    data = data if isinstance(data, dict) else {}
    _object(data, ("item_id", "choice", "base", "request_id", "confirmation", "section_id"), "posting choice")
    item_id, choice = data.get("item_id"), data.get("choice")
    if not isinstance(item_id, str) or not 1 <= len(item_id) <= 100:
        raise ValueError("Choose a column of this gradebook.")
    if choice not in posting_policy.CHOICES:
        raise ValueError("Choose Post, Hide, or the category's rule.")
    if data.get("confirmation") != "choose":
        raise ValueError("Confirm the choice.")
    request_id = _request_id(data.get("request_id"))
    if len(request_id) < 8:
        raise ValueError("Request ID must be 8 to 128 characters.")
    request_sha = common.digest({"posting": item_id, "choice": choice})
    with svc.rosters.lock():
        context = svc._scoped(svc._context(term_id), data.get("section_id"))
        with connect(svc.path) as db:
            _term(db, term_id)
            old = db.execute("SELECT request_sha256 FROM gradebook_canvas_posting_choices WHERE term_id=? AND request_id=?",
                             (term_id, request_id)).fetchone()
            if old:
                if old[0] != request_sha:
                    raise Conflict("This request ID was used for a different change.")
                return svc._render(context)
            item = next((entry for entry in _setup(db, term_id)[0].get("items", []) if entry["id"] == item_id), None)
            if item is None:
                raise NotFound("This column is no longer in the gradebook.")
            if item.get("archived"):
                raise ValueError(f"“{item['title']}” is archived.")
            if item.get("canvas") == "off":
                raise ValueError(f"“{item['title']}” is kept off Canvas (Column settings).")
            current = choices(db, term_id).get(item_id)
            if "base" in data and data["base"] != (current[1] if current else None):
                raise Conflict("This column's posting was just changed in another window. Look again.")
            if (current[0] if current else "rule") != choice:
                db.execute("BEGIN IMMEDIATE")
                db.execute("INSERT INTO gradebook_canvas_posting_choices VALUES(?,?,?,?,?,?,?,?)",
                           (str(uuid.uuid4()), term_id, item_id, choice, request_id, request_sha, actor, timestamp()))
        return svc._render(context)


# --- at every Sync with Canvas: the posting steps after its send ----------------------------------------------------------------

def after_key(send_id):
    return f"after:{send_id}"


def after_sync(svc, send_id, term_id, batches, progress, found, actor, source="sync"):
    """A sync's send (`_run_send`: its posting-policy switches, then its grades), then what Canvas shows of each sent assignment kept
    for the grid's mark, then the sync's posts and hides (`found`: posting_policy.settle's steps; a step that waits is not done)."""
    try:
        switches = {}
        for step in found:
            if step["action"] == "switch" and not step["waits"] and not step["new"]:
                switches.setdefault(step["course_id"], []).append(step)
        svc._run_send(send_id, term_id, batches, progress, switches)
        rows = [{"item_id": b["item_id"], "course_id": b["course_id"], "assignment_id": b["assignment_id"], "manual": b["manual"],
                 **{k: b["posted"][k] for k in ("graded", "hidden")}} for b in batches if b.get("posted") and b.get("assignment_id") and "manual" in b]
        if rows:
            with svc.rosters.lock(), connect(svc.path) as db:
                db.execute("BEGIN IMMEDIATE")
                record(db, term_id, rows, source)
        _policy_posts(svc, send_id, term_id, batches, [s for s in found if s["action"] in ACTIONS and not s["waits"]], actor)
    except Exception:  # noqa: BLE001 - logged; the send and each post keep their own status
        LOG.exception("Posting after a sync failed")
    finally:
        svc.canvas.writes.end(after_key(send_id))


def _policy_posts(svc, send_id, term_id, batches, steps, actor):
    """The sync's posts and hides, one post record per column and action. A post is done in a course only when every grade this sync
    sent there for the column was accepted (otherwise said, not posted); the courses' assignments are read once more and only what
    is left is asked of Canvas."""
    if not steps:
        return
    made = {b["key"]: b.get("assignment_id") for b in batches}
    expected = {}
    for b in batches:
        expected[(b["item_id"], b["course_id"])] = expected.get((b["item_id"], b["course_id"]), 0) + len(b["values"])
    with connect(svc.path) as db:
        states = _sent_states(db, send_id)
    sections = _sections(svc, term_id)
    acting, refused = [], []
    for step in steps:
        step = {**step, "assignment_id": step["assignment_id"] or made.get(step["key"])}
        key = (step["item_id"], step["course_id"])
        if not step["assignment_id"]:
            refused.append((step, "Not done here: its Canvas assignment was not created in this sync.", {}))
        elif step["action"] == "post" and (len(states.get(key, [])) != expected.get(key, 0) or any(state != "accepted" for state in states.get(key, []))):
            # CONFIRMED BY A LATER READ: named by course, so the morning's follow-up posts here once Canvas shows those grades.
            refused.append((step, UNCONFIRMED, {"waits": "grades", "course_id": step["course_id"], "assignment_id": step["assignment_id"]}))
        else:
            acting.append(step)
    wanted = {}
    for step in acting:
        wanted.setdefault(step["course_id"], set()).add(step["assignment_id"])
    fresh, problem = {}, ""
    if wanted:
        try:
            fresh = svc.canvas.read_for_send(set(wanted), lambda course, _: wanted.get(course, set()))
        except Exception as error:  # noqa: BLE001 - recorded on each post: nothing was asked of Canvas
            problem = f"Not done: Canvas could not be read ({getattr(error, 'description', None) or error})."
    index = posting_policy.by_assignment(fresh)
    groups = {}
    for step in acting:
        group = groups.setdefault((step["item_id"], step["action"]), {"plan": [], "errors": [], "title": step["title"]})
        label = _label(sorted(sections.get(step["course_id"], [])))
        if problem:
            group["errors"].append({"label": label, "message": problem[:300]})
            continue
        row = next((a for a in (fresh.get(step["course_id"]) or {}).get("assignments", []) if a["id"] == step["assignment_id"]), None)
        if row is None:
            group["errors"].append({"label": label, "message": "Not done here: its Canvas assignment is no longer in Canvas."})
            continue
        seen = counts(index.get((step["course_id"], step["assignment_id"])) or [])
        if left(step["action"], seen):
            group["plan"].append({"course_id": step["course_id"], "assignment_id": step["assignment_id"], "name": row["name"],
                                  "sections": sorted(sections.get(step["course_id"], [])), "manual": bool(row.get("post_manually")),
                                  "before": seen, "why": step["why"]})
    for step, words, more in refused:
        group = groups.setdefault((step["item_id"], step["action"]), {"plan": [], "errors": [], "title": step["title"]})
        group["errors"].append({"label": _label(sorted(sections.get(step["course_id"], []))), "message": words, **more})
    for (item_id, action), group in sorted(groups.items()):
        plan, errors = group["plan"], group["errors"]
        if not plan and not errors:
            continue  # Canvas already shows it as the policy wants: nothing to record
        request_id = f"policy:{send_id}:{item_id}:{action}"[:160]
        progress = {"courses": len(plan), "done": 0, "accepted": 0, "failed": 0, "uncertain": 0, "errors": errors}
        post_id = str(uuid.uuid4())
        with svc.rosters.lock(), connect(svc.path) as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM gradebook_canvas_posts WHERE term_id=? AND request_id=?", (term_id, request_id)).fetchone():
                continue
            _insert(db, post_id, term_id, item_id, action, send_id, request_id, common.digest({"policy": send_id, "item": item_id, "action": action}),
                    "policy", plan, progress, actor)
            if not plan:
                db.execute("UPDATE gradebook_canvas_posts SET status='failed',finished_at=? WHERE id=?", (timestamp(), post_id))
        if plan:
            run(svc, post_id, term_id, item_id, action, plan, progress)  # a course refused beside them stays in its errors


def _sent_states(db, send_id):
    """{(column, course): [the latest receipt's state of each value the send sent there]} (a later read's `accepted` receipt is on
    the send too: canvas_confirm)."""
    states = {}
    for row in db.execute("""SELECT s.item_id,s.course_id,s.state FROM gradebook_canvas_sent s WHERE s.send_id=? AND s.rowid=(
            SELECT x.rowid FROM gradebook_canvas_sent x WHERE x.send_id=s.send_id AND x.item_id=s.item_id AND x.course_id=s.course_id
            AND x.canvas_user_id=s.canvas_user_id ORDER BY x.created_at DESC,x.rowid DESC LIMIT 1)""", (send_id,)):
        states.setdefault((row["item_id"], row["course_id"]), []).append(row["state"])
    return states


def _label(numbers):
    return f"Section {', '.join(map(str, numbers))}"


def _sections(svc, term_id):
    """{Canvas course: the section numbers linked to it} (a post's error and step name them)."""
    with connect(svc.path) as db:
        numbers = {row["id"]: row["number"] for row in db.execute("SELECT id,number FROM course_sections WHERE term_id=?", (term_id,))}
    sections = {}
    for section_id, link in svc._source_links("canvas", term_id).items():
        sections.setdefault(link["course_id"], []).append(numbers.get(section_id, ""))
    return sections


# --- CONFIRMED BY A LATER READ: the posts a sync withheld for unconfirmed grades -------------------------------------------------

def follow_id(send_id, item_id, course_id):
    """The request ID of the one follow-up post of a column in a course after a send (at most 160 characters)."""
    return f"policy:{send_id}:{item_id}:post"[:139] + f":{course_id}"


def withheld(db, send_id):
    """The posts a sync withheld in a course because a grade it sent there was not confirmed, which no follow-up has taken up yet:
    [{item_id, course_id, assignment_id}] (catalog only)."""
    rows = db.execute("SELECT item_id,request_id,progress_json FROM gradebook_canvas_posts WHERE send_id=? AND action='post' ORDER BY created_at,rowid",
                      (send_id,)).fetchall()
    taken, found = {row["request_id"] for row in rows}, {}
    for row in rows:
        for error in json.loads(row["progress_json"] or "{}").get("errors") or []:
            if error.get("waits") == "grades" and error.get("course_id") and error.get("assignment_id") \
                    and follow_id(send_id, row["item_id"], error["course_id"]) not in taken:
                found[(row["item_id"], error["course_id"])] = {"item_id": row["item_id"], "course_id": error["course_id"],
                                                               "assignment_id": error["assignment_id"]}
    return list(found.values())


def follow_posts(svc, send, term_id):
    """The withheld posts of `send` (its `gradebook_canvas_sends` row) carried out where every grade it sent for the column is
    now accepted: the assignments are read once more with no lock held, and each column and course gets ONE follow-up
    `gradebook_canvas_posts` row. Canvas is asked only where it still hides a grade and the column should still be posted (its
    choice or rule as it is now); otherwise the row says the post is done with nothing asked. A restart waits for it
    (`after:<send>`); a read Canvas refuses leaves it for the next round. Returns how many rows it made."""
    from .service import STOPPING_SYNC, _setup
    send_id = send["id"]
    expected = {}
    for batch in json.loads(send["plan_json"] or "[]"):
        expected[(batch["item_id"], batch["course_id"])] = expected.get((batch["item_id"], batch["course_id"]), 0) + len(batch["values"])
    with connect(svc.path) as db:
        states = _sent_states(db, send_id)
        ready = []
        for step in withheld(db, send_id):
            sent = states.get((step["item_id"], step["course_id"]), [])
            if sent and len(sent) == expected.get((step["item_id"], step["course_id"])) and all(state == "accepted" for state in sent):
                ready.append(step)
        want = policy(svc, db, term_id, _setup(db, term_id)[0]) if ready else {}
    if not ready:
        return 0
    try:
        svc.canvas.writes.begin(after_key(send_id), "grades posted in Canvas after a sync", STOPPING_SYNC)
    except ServiceUnavailable:
        return 0  # Faculty is stopping: the next process's round takes them up
    made = 0
    try:
        sections = _sections(svc, term_id)
        wanted = {}
        for step in ready:
            wanted.setdefault(step["course_id"], set()).add(step["assignment_id"])
        fresh = svc.canvas.read_for_send(set(wanted), lambda course, _: wanted.get(course, set()))
        index = posting_policy.by_assignment(fresh)
        for step in ready:
            item_id, course = step["item_id"], step["course_id"]
            numbers = sorted(sections.get(course, []))
            shown = next((a for a in fresh[course]["assignments"] if a["id"] == step["assignment_id"]), None)
            seen = counts(index.get((course, step["assignment_id"])) or [])
            plan, errors = [], []
            if shown is None:
                errors.append({"label": _label(numbers), "message": "Not done here: its Canvas assignment is no longer in Canvas."})
            elif (want.get(item_id) or {}).get("state") == "posted" and left("post", seen):
                plan.append({"course_id": course, "assignment_id": step["assignment_id"], "name": shown["name"], "sections": numbers,
                             "manual": bool(shown.get("post_manually")), "before": seen, "why": want[item_id]["why"]})
            progress = {"courses": len(plan), "done": 0, "accepted": 0, "failed": 0, "uncertain": 0, "errors": errors,
                        "confirmed": {"course_id": course, "assignment_id": step["assignment_id"]}}
            request_id, post_id = follow_id(send_id, item_id, course), str(uuid.uuid4())
            with svc.rosters.lock(), connect(svc.path) as db:
                db.execute("BEGIN IMMEDIATE")
                if db.execute("SELECT 1 FROM gradebook_canvas_posts WHERE term_id=? AND request_id=?", (term_id, request_id)).fetchone():
                    continue
                _insert(db, post_id, term_id, item_id, "post", send_id, request_id,
                        common.digest({"policy": send_id, "item": item_id, "action": "post", "course": course}), "confirmed", plan, progress, send["actor"])
                if not plan:
                    # Nothing to ask: Canvas already shows every grade (or the column is no longer to be posted); its assignment gone.
                    db.execute("UPDATE gradebook_canvas_posts SET status=?,finished_at=? WHERE id=?", ("failed" if errors else "done", timestamp(), post_id))
                    if shown is not None:
                        record(db, term_id, [{"item_id": item_id, "course_id": course, "assignment_id": step["assignment_id"],
                                              "manual": bool(shown.get("post_manually")), "graded": seen["graded"], "hidden": seen["hidden"]}], "nightly")
            made += 1
            if plan:
                run(svc, post_id, term_id, item_id, "post", plan, progress)
    except Exception:  # noqa: BLE001 - what was recorded stands; a post not started is taken up by the next round
        LOG.exception("The posts withheld for unconfirmed grades could not all be carried out")
    finally:
        svc.canvas.writes.end(after_key(send_id))
    return made
