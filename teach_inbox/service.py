"""Durable intake, queue transitions, immutable originals, and retry-safe dispatch."""
import hashlib
import json
import logging
import os
import secrets
import threading
import uuid
from contextlib import contextmanager, nullcontext
from datetime import datetime, timedelta, timezone
from pathlib import Path

from werkzeug.exceptions import Conflict, Forbidden, NotFound

from canonical_json import encoded_strict as encoded
from certified_archive import certified_messages, facts as archive_facts
from course_groups import group_courses
from course_storage import StorageError, atomic_json, fsync_dir, digest_file, timestamp, PENDING, live_offerings
from rosters import connect, term_key
from .auth import EmailConfig, token_hash
from .handlers import HANDLERS
from .protocol import ACTIONS, MAX_FILE, MAX_FILES, MAX_INTAKE_FILE, VERSION, normalize_command, validate_files
from .storage import InboxStorage, initialize, resolve, scope

LOG = logging.getLogger(__name__)
WORKER_LOCK = "827fc9da-1752-5b07-9ed0-3662eebae7e7"


def sha(content):
    return hashlib.sha256(content).hexdigest()


def write_bytes(path, content):
    with path.open("xb") as handle:
        os.chmod(path, 0o600)
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    fsync_dir(path.parent)


def requires_approval(command):
    action = command.get("action") if isinstance(command, dict) else None
    metadata = command.get("metadata", {}) if isinstance(command, dict) else {}
    return (ACTIONS.get(action, {}).get("approval", False) if isinstance(action, str) else False) or (isinstance(metadata, dict) and metadata.get("publish") is True)


# The owner-less workspace actions and the label each one's messages carry ("Inbox · <label>").
WORKSPACE_LABELS = {"accommodations.sync": "Current roster accommodations", "accommodations.exams.sync": "DR exam bookings",
                    "aim.form_capture": "AIM form capture"}


# SQL for "this message was not removed from the Inbox"; `{0}` is the column holding the message id. Anything that looks across
# submissions (what was seen in a scan, what conflicts with what, who is already matched) must apply it.
PRESENT = "NOT EXISTS(SELECT 1 FROM inbox_deletions gone WHERE gone.message_id={0} AND gone.restored_at IS NULL)"

# A scan's grading assignment as the Inbox list names it (_scan_plans, _scan_groups): the scan's latest association, filtered by `WHERE `.
_SCAN_ASSIGNMENT = """SELECT l.message_id,g.id plan_id,e.title,e.assignment_id,e.term_id,t.code,t.term,t.term_other,t.year FROM frq_scan_links l
                JOIN frq_assignments g ON g.id=l.assignment_id JOIN exam_events e ON e.id=g.event_id LEFT JOIN course_terms t ON t.id=e.term_id
                WHERE """
_SCAN_LATEST = "l.id=(SELECT MAX(n.id) FROM frq_scan_links n WHERE n.message_id=l.message_id)"


def _scan_assignment(row):
    # The term tells apart two terms' "Exam 1" in History (grading's plan() reads the same columns). assignment_id and term_id
    # (additive) let the group header open the assignment's Collect (INBOX, 2026-10-18).
    term = f"{row['term_other'] if row['term'] == 'Other' else row['term']} {row['year']}" if row["year"] is not None else ""
    return {"plan_id": row["plan_id"], "title": row["title"], "code": row["code"] or "", "term": term,
            "assignment_id": row["assignment_id"], "term_id": row["term_id"]}


class InboxService:
    def __init__(self, rosters, email_config=None):
        self.rosters = rosters
        self.path = rosters.path
        self.storage = rosters.storage
        self.email = email_config or EmailConfig.environment()
        self.wake = threading.Event()
        # Scheduling is process-local and changes no retained job/approval state.
        # A restart begins at intake; an active worker gives every queue a turn.
        self._work_queue_cursor = 0
        with self.rosters.lock():
            initialize(self.path)
        if self.storage:
            self.storage.inbox_storage = InboxStorage(self.storage.config, self.storage.enforce_mounts)

    def event(self, db, job_id, kind, detail):
        db.execute("INSERT INTO inbox_events (job_id,at,kind,detail,software_version) VALUES (?,?,?,?,?)",
                   (job_id, timestamp(), kind, detail, VERSION))

    def classify(self, db, value, files):
        command = normalize_command(value)
        if command["action"] in ("accommodations.sync", "accommodations.exams.sync"):
            from accommodations import validate_metadata
            if command["target"] is not None or any(key in command for key in ("course", "term", "term_other", "year", "section")):
                raise ValueError("A mixed-course accommodation export uses its previewed current rosters, not one course destination.")
            validate_metadata(command["metadata"])
            if len(files) != 1 or Path(files[0]["filename"]).suffix.lower() != ".xlsx":
                raise ValueError("Accommodation import requires exactly one XLSX export.")
            return command, None, ""
        if command["action"] == "aim.form_capture":
            if command["target"] is not None or any(key in command for key in ("course", "term", "term_other", "year", "section")):
                raise ValueError("An AIM form capture belongs to the workspace, not one course destination.")
            metadata = command["metadata"]
            if (set(metadata) != {"page", "controls", "buttons", "dropped"} or not isinstance(metadata["page"], str) or len(metadata["page"]) > 200
                    or any(type(metadata[key]) is not int or metadata[key] < 0 for key in ("controls", "buttons", "dropped"))):
                raise ValueError("An AIM form capture's metadata is its page and its control, button and left-out counts.")
            if len(files) != 1 or Path(files[0]["filename"]).suffix.lower() != ".json":
                raise ValueError("An AIM form capture is exactly one JSON file.")
            return command, None, ""
        owner = resolve(db, command)
        command = {"schema_version": 1, "action": command["action"], "target": {"level": owner["level"], "id": owner["id"]}, "metadata": command["metadata"]}
        action = ACTIONS.get(command["action"])
        if not action:
            return command, owner, "This action is not in the handler registry. Review the command."
        if owner["level"] not in action["scopes"]:
            return command, owner, "This action needs a " + " or ".join(action["scopes"]) + " destination."
        if command["action"] == "roster.sync" and (len(files) != 1 or Path(files[0]["filename"]).suffix.lower() not in (".csv", ".xlsx")):
            return command, owner, "Roster import requires exactly one CSV or XLSX file."
        if command["action"] == "document.archive" and not files:
            return command, owner, "Document archiving requires at least one attachment."
        if command["action"] not in HANDLERS:
            return command, owner, "Submission retained. This processor has not been implemented yet."
        return command, owner, ""

    def message(self, db, identifier):
        row = db.execute("SELECT * FROM inbox_messages WHERE id=?", (identifier,)).fetchone()
        if not row:
            raise NotFound("Inbox submission not found.")
        return dict(row)

    @contextmanager
    def files(self, message, write=False):
        # Caller holds the common lifecycle lock; all open handles close here.
        if self.storage:
            manager = self.storage.inbox_storage
            if message["storage_status"] in PENDING:
                manager.reconcile_course(message["id"])
            with manager.course_files(message["id"], write=write) as path:
                yield path
        else:
            path = self.path.parent / "inbox" / message["id"] / "data"
            if path.resolve() != path or not path.is_dir():
                raise StorageError("The inbox payload is missing or redirected.")
            yield path

    def _publish(self, message, original, files):
        if self.storage:
            self.storage.check_nvme()
            stage = self.storage.nvme / "staging" / f"inbox-{message['id']}"
        else:
            root = self.path.parent / "inbox"
            root.mkdir(mode=0o700, exist_ok=True)
            if root.resolve() != root:
                raise StorageError("The inbox directory is redirected.")
            stage = root / f".staging-{message['id']}"
        stage.mkdir(mode=0o700)
        data = stage / "data"
        data.mkdir(mode=0o700)
        (data / "inputs").mkdir(mode=0o700)
        write_bytes(data / message["original_path"], original)
        if digest_file(data / message["original_path"])["sha256"] != message["original_sha256"]:
            raise StorageError("The original submission failed its NVMe write verification.")
        for file in files:
            write_bytes(data / file["path"], file["content"])
            if digest_file(data / file["path"])["sha256"] != file["sha256"]:
                raise StorageError("An uploaded attachment failed its write verification.")
        atomic_json(stage / "course.json", InboxStorage.metadata(message))
        fsync_dir(data)
        if self.storage:
            final = self.storage.inbox_storage.hot_path(message["id"])
            os.rename(stage, final)
            fsync_dir(final.parent)
            message.update(storage_initialized=1, storage_hot_required=1,
                           storage_status="active" if message["is_current"] else "staged")
        else:
            final = stage.with_name(message["id"])
            os.rename(stage, final)
            fsync_dir(final.parent)

    def submit(self, value, files, *, source="web", sender="workspace", idempotency_key=None,
               original=None, authenticated=True, problem="", submission_code=None, transport=None,
               instructor_review_required=False, large_scans=False, unread="", intake=False):
        # unread: why an email was refused before its sender could be checked (too large, malformed); it stays unauthenticated.
        # intake: an email to a scanner intake address, whose attachments may be up to MAX_INTAKE_FILE (SCANNER-INTAKE
        # "A SCAN EMAIL'S PDF MAY BE AS LARGE AS THE EMAIL").
        if source not in ("web", "api", "cli", "email", "code"):
            raise ValueError("Unknown inbox source.")
        if intake and source != "email":
            raise ValueError("Only an email to a scanner intake address takes its larger attachments.")
        if large_scans and (source not in ("web", "cli") or not isinstance(value, dict) or value.get("action") != "scan.receive"
                            or submission_code or not instructor_review_required):
            raise ValueError("Large scans are retained only as scan.receive submissions from the instructor's workspace, for review.")
        validate_files(files, large_scans=large_scans, max_file=MAX_INTAKE_FILE if intake else MAX_FILE)
        if original is None:
            original = encoded(value).encode()
        if len(original) > 35 * 1024 * 1024:
            raise ValueError("The original submission exceeds 35 MB.")
        key = idempotency_key or str(uuid.uuid4())
        if not isinstance(key, str) or not 1 <= len(key) <= 160:
            raise ValueError("Idempotency keys must be between 1 and 160 characters.")
        digest = sha(original + encoded(value).encode() + encoded([(file["filename"], sha(file["content"])) for file in files]).encode())
        if transport is not None:
            digest = sha(original + transport["body_sha256"].encode())
        code_row = None
        with self.rosters.lock():
            with connect(self.path) as db:
                if submission_code:
                    # Used codes may repeat only their original identical, idempotent request.
                    code_row = self.code(db, submission_code, permit_used=True)
                    source, sender = "code", code_row["id"]
                    bound = json.loads(code_row["command_json"])
                    if value and normalize_command(value) != bound:
                        raise Forbidden("This submission code fixes the command and destination; they cannot be overridden.")
                    value = bound
                dedupe = encoded([source, "gateway" if source == "email" else sender, key])
                existing = db.execute("SELECT id,fingerprint FROM inbox_messages WHERE dedupe_key=?", (dedupe,)).fetchone()
                if existing:
                    if existing["fingerprint"] != digest:
                        raise Conflict("This idempotency key was already used for a different submission.")
                    return self.get_by_message(existing["id"])
                if code_row and code_row["used_message_id"]:
                    raise Conflict("This one-time submission code has already been used.")
                owner = None
                command = value
                if instructor_review_required:
                    problem = "Scan received at the short address. Inspect its pages in the instructor workspace; instructor review remains required."
                if not problem and authenticated:
                    try:
                        command, owner, problem = self.classify(db, value, files)
                    except ValueError as error:
                        problem = str(error)
                if not authenticated:
                    problem = (f"{unread} This email could not be read, so this submission cannot be approved or executed." if unread
                               else "Sender authentication failed. This submission cannot be approved or executed.")
                needs_approval = not problem and requires_approval(command)
            received = timestamp()
            message_id, job_id = str(uuid.uuid4()), str(uuid.uuid4())
            message = {"id": message_id, "source": source, "sender": sender[:254], "received_at": received,
                       "software_version": VERSION, "authenticated": int(authenticated), "dedupe_key": dedupe,
                       "instructor_review_required": int(instructor_review_required),
                       "fingerprint": digest, "original_path": "original.eml" if source == "email" else "original.json", "original_sha256": sha(original),
                       "scope_level": owner["level"] if owner else None, "scope_id": owner["id"] if owner else None,
                       "definition_id": owner["definition_id"] if owner else None, "term_id": owner["term_id"] if owner else None,
                       "section_id": owner["section_id"] if owner else None, "name": f"Inbox · {owner['label']}" if owner else f"Inbox · {WORKSPACE_LABELS[command['action']]}" if isinstance(command, dict) and isinstance(command.get("action"), str) and command["action"] in WORKSPACE_LABELS else "Inbox · Unclassified submission",
                       "code": owner["code"] if owner else "", "term": owner["term"] if owner else "",
                       "term_other": owner["term_other"] if owner else "", "year": owner["year"] if owner else None,
                       "sections": encoded(owner["sections"] if owner else []), "is_current": int(owner["is_current"]) if owner else 0,
                       "created_at": received, "updated_at": received, "storage_status": "", "storage_snapshot": "",
                       "storage_verified_at": "", "storage_initialized": 0}
            artifacts = []
            for file in files:
                identifier = str(uuid.uuid4())
                artifacts.append({**file, "id": identifier, "path": f"inputs/{identifier}.bin", "sha256": sha(file["content"]), "size": len(file["content"])})
            self._publish(message, original, artifacts)
            state = "needs_review" if problem or needs_approval else "pending"
            error = problem or ("Explicit instructor approval is required before execution." if needs_approval else "")
            with connect(self.path) as db:
                db.execute("BEGIN IMMEDIATE")
                db.execute(f"INSERT INTO inbox_messages ({','.join(message)}) VALUES ({','.join('?' for _ in message)})", tuple(message.values()))
                db.execute("INSERT INTO inbox_jobs (id,message_id,action,status,parameters_json,error,created_at) VALUES (?,?,?,?,?,?,?)",
                           (job_id, message_id, command["action"][:100] if isinstance(command, dict) and isinstance(command.get("action"), str) else "unclassified", state, encoded(command), error, received))
                for file in artifacts:
                    db.execute("INSERT INTO inbox_artifacts VALUES (?,?,?,?,?,?,?,?,?)",
                               (file["id"], message_id, job_id, file["filename"], file["path"], "input", file["size"], file["sha256"], received))
                self.event(db, job_id, "received", f"Original and {len(files)} attachment(s) retained from {source}.")
                if transport is not None:
                    self.event(db, job_id, "gateway", encoded(transport))
                self.event(db, job_id, state, error or "Validated and queued for a registered worker.")
                if code_row:
                    db.execute("UPDATE inbox_codes SET used_message_id=? WHERE id=?", (message_id, code_row["id"]))
                if isinstance(command, dict) and command.get("action") == "scan.receive" and hasattr(self, "document_scans"):
                    db.execute("INSERT INTO document_decode_jobs(message_id,status,created_at,updated_at) VALUES(?,'pending',?,?)", (message_id, received, received))
            self.wake.set()
            if self.storage:
                self.storage.wake.set()
            return self.get(job_id)

    def get_by_message(self, identifier):
        with connect(self.path) as db:
            job = db.execute("SELECT id FROM inbox_jobs WHERE message_id=?", (identifier,)).fetchone()
        return self.get(job["id"])

    def get(self, identifier, db=None, events=True, roles=None):
        # `db`: the caller's open catalog connection (the listing reads fifty jobs; a connection each re-read the schema every time).
        # `events=False` leaves out the audit list and `roles` limits the files to that role: the Inbox list shows neither.
        with nullcontext(db) if db is not None else connect(self.path) as db:
            row = db.execute("SELECT * FROM inbox_jobs WHERE id=?", (identifier,)).fetchone()
            if not row:
                raise NotFound("Inbox job not found.")
            job = dict(row)
            job["parameters"] = json.loads(job.pop("parameters_json"))
            job["result"] = json.loads(job.pop("result_json"))
            message = self.message(db, job["message_id"])
            job.update(source=message["source"], sender=message["sender"], received_at=message["received_at"],
                       authenticated=bool(message["authenticated"]), software_version=message["software_version"],
                       instructor_review_required=bool(message["instructor_review_required"]),
                       scope_label=message["name"].removeprefix("Inbox · "), storage_status=message["storage_status"], storage_error=message["storage_error"])
            gone = db.execute("SELECT actor,reason,created_at FROM inbox_deletions WHERE message_id=? AND restored_at IS NULL", (job["message_id"],)).fetchone()
            job["deleted"] = dict(gone) if gone else None
            job["artifacts"] = [dict(row) for row in db.execute("SELECT id,filename,role,size,sha256,created_at FROM inbox_artifacts WHERE job_id=?"
                                                                  + (" AND role=?" if roles else "") + " ORDER BY created_at,id", (identifier, *([roles] if roles else [])))]
            if events:
                job["events"] = [dict(row) for row in db.execute("SELECT at,kind,detail,software_version FROM inbox_events WHERE job_id=? ORDER BY id", (identifier,))]
            job["handler_available"] = job["action"] in HANDLERS
            job["requires_approval"] = requires_approval(job["parameters"]) if isinstance(job["parameters"], dict) and "action" in job["parameters"] and "metadata" in job["parameters"] else False
            return job

    def catalog(self):
        with connect(self.path) as db:
            targets = []
            for table, level in (("course_definitions", "course"), ("course_terms", "term"), ("course_sections", "section")):
                for row in db.execute(f"SELECT id FROM {table}").fetchall():
                    try:
                        target = scope(db, {"level": level, "id": row["id"]})
                        targets.append({key: target[key] for key in ("level", "id", "label", "is_current")})
                    except ValueError:
                        continue
            return {"targets": sorted(targets, key=lambda item: item["label"]),
                    "actions": [{"name": name, "implemented": name in HANDLERS, **data} for name, data in ACTIONS.items()],
                    "email_configured": self.email.enabled, "software_version": VERSION}

    # How a scan's own summary files it (ScanService.overview's state): nothing left to do, the worker still busy, or
    # something for the instructor.
    SCAN_FILING = {"done": "completed", "wait": "running", "todo": "needs_review", "check": "needs_review"}

    def filed(self, db, rows, scans="all", memo=None):
        """Where the Inbox lists each job: {job_id: (listed_as, scan overview or None)}. A view, never a retained status.

        `rows` carry id, status, action, message_id and `gone` (the job's message has a live deletion; PRESENT's negation).
        A retained scan keeps `needs_review` for good: automation never clears the instructor review gate, and this does not
        either. So a scan is filed under the tab its own summary speaks for: nothing left to do with Completed, the worker still
        busy with Running, something for the instructor with Needs review. Deleted submissions are filed only as `deleted`.
        `scans="all"` attaches the overview of every present scan (the Inbox list shows each one); `scans="review"` computes
        it only where it decides the filing, a present scan stored as needs_review (Home, which must stay cheap). Nothing is
        written: the derived state never reaches inbox_jobs.status or instructor_review_required.
        """
        result, memo = {}, {} if memo is None else memo  # memo: one read's booklet versions, shared by every scan (PERFORMANCE)
        for row in rows:
            overview = None
            if (row["action"] == "scan.receive" and not row["gone"] and hasattr(self, "document_scans")
                    and (scans == "all" or row["status"] == "needs_review")):
                overview = self.document_scans.overview(db, row["message_id"], memo=memo)
            if row["gone"]:
                listed = "deleted"
            elif overview is not None and row["status"] == "needs_review":
                listed = self.SCAN_FILING[overview["state"]]
            else:
                listed = row["status"]
            result[row["id"]] = (listed, overview)
        return result

    # INBOX "SCANS AND SUBMISSIONS" (requirement, 2026-10-18): the list's `kind` keeps scans (Current, History) apart from the other
    # command submissions (their own Submissions tab). Views only; nothing stored changes.
    KINDS = ("scan", "command", "all")

    def listing(self, status=None, offset=0, query="", files=None, kind=None, needs=False, assignment=None):
        # This workspace view reads existing message owners and catalog metadata;
        # search never opens their retained payloads or creates a resource owner.
        # files="input" lists each job's input files only, which is all the Inbox list shows; the default lists every file.
        # kind ("scan", "command" or "all"), needs (only what is filed as needing the instructor) and assignment (a grading plan's
        # scans) are additive: a read without `kind` answers exactly as before. With `kind`, the body adds `kind_counts` (each
        # kind's counts, for the tabs) and `assignments` (the grading assignments the listed scans are filed under, for the filter).
        if status and status not in ("pending", "running", "completed", "failed", "needs_review", "cancelled", "deleted", "history", "current", "archived"):
            raise ValueError("Unknown job status.")
        if not isinstance(query, str) or len(query.strip()) > 200:
            raise ValueError("Inbox search must be at most 200 characters.")
        if kind is not None and kind not in self.KINDS:
            raise ValueError("Unknown submission kind.")
        if assignment is not None and (not isinstance(assignment, str) or len(assignment) > 200):
            raise ValueError("Unknown assignment.")
        terms = query.casefold().split()
        with connect(self.path) as db:
            source = " FROM inbox_jobs j"
            filters, parameters = [], []
            linked = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name IN ('frq_scan_links','frq_assignments','exam_events')")} == {"frq_scan_links", "frq_assignments", "exam_events"}
            if terms:
                source += " JOIN inbox_messages m ON m.id=j.message_id"
                db.create_function("inbox_casefold", 1, str.casefold, deterministic=True)
                db.create_function("inbox_action_label", 1, lambda action: ACTIONS.get(action, {}).get("label", ""), deterministic=True)
                fields = ("j.id", "j.action", "inbox_action_label(j.action)", "m.name", "m.sender", "m.source")
                match = " OR ".join(f"instr(inbox_casefold({field}), ?)>0" for field in fields)
                # EXISTS keeps one job per result even when several files match.
                match += " OR EXISTS (SELECT 1 FROM inbox_artifacts a WHERE a.job_id=j.id AND instr(inbox_casefold(a.filename), ?)>0)"
                # A scan is also found by the title of the grading assignment it is filed under (its latest association, as the
                # list's group header names it). The scan's own row, so nothing here looks across submissions. Only reads that ask
                # for `kind` (the Inbox's own list) match it, so a read without `kind` answers as before.
                by_title = linked and kind is not None
                if by_title:
                    match += """ OR EXISTS (SELECT 1 FROM frq_scan_links l JOIN frq_assignments g ON g.id=l.assignment_id JOIN exam_events e ON e.id=g.event_id
                        WHERE l.message_id=j.message_id AND l.id=(SELECT MAX(n.id) FROM frq_scan_links n WHERE n.message_id=j.message_id)
                        AND instr(inbox_casefold(e.title), ?)>0)"""
                for term in terms:
                    filters.append("(" + match + ")")
                    parameters.extend([term] * (len(fields) + 1 + by_title))
            where = " WHERE " + " AND ".join(filters) if filters else ""
            listed = db.execute("SELECT j.id,j.status,j.action,j.message_id,NOT " + self.PRESENT.format("j.message_id") + " gone" + source + where + " ORDER BY j.created_at DESC,j.id DESC", parameters).fetchall()
            filing = self.filed(db, listed)
            overviews = {identifier: overview for identifier, (_, overview) in filing.items() if overview is not None}
            # Archived (a view, certified_archive.py): a finished scan whose assessment's grades are certified leaves History for the
            # Archived tab. Anything unfinished stays in Current whatever its assessment.
            archive = certified_messages(db, {row["message_id"] for row in listed if not row["gone"] and filing[row["id"]][0] == "completed"
                                              and row["action"] == "scan.receive"})
            archived = {row["id"]: archive[row["message_id"]] for row in listed if row["message_id"] in archive and not row["gone"]
                        and filing[row["id"]][0] == "completed" and row["action"] == "scan.receive"}
            shown = lambda row: "archived" if row["id"] in archived else filing[row["id"]][0]
            extra = {}
            if kind is not None:
                # Each scan's grading assignment (latest association, one read for the list), for the filter and its choices.
                plans = self._scan_plans(db, {row["message_id"] for row in listed if row["action"] == "scan.receive" and not row["gone"]}) if linked else {}
                is_scan = lambda row: row["action"] == "scan.receive"
                choices = {}
                for row in listed:
                    plan = plans.get(row["message_id"]) if is_scan(row) and not row["gone"] else None
                    if plan:
                        choices.setdefault(plan["plan_id"], {**plan, "scans": 0})["scans"] += 1
                if assignment is not None:
                    listed = [row for row in listed if not is_scan(row) or (plans.get(row["message_id"]) or {}).get("plan_id") == assignment]
                kind_counts = {"scan": {}, "command": {}}
                for row in listed:
                    bucket = kind_counts["scan" if is_scan(row) else "command"]
                    bucket[shown(row)] = bucket.get(shown(row), 0) + 1
                if kind != "all":
                    listed = [row for row in listed if is_scan(row) == (kind == "scan")]
                extra = {"kind_counts": kind_counts,
                         "assignments": sorted(choices.values(), key=lambda item: (item["title"].casefold(), item["term"], str(item["plan_id"])))}
            counts = {}
            for row in listed:
                counts[shown(row)] = counts.get(shown(row), 0) + 1
            # History is a workspace view over completed/reviewed and cancelled work, not a new retained status.
            # Use the same derived scan state and deletion boundary before pagination. Unfiltered API reads stay compatible.
            # Current is every unfinished submission in one view, each tile saying its own state (requirement, 2026-09-24): what
            # needs the instructor first (failed, then needs review), then the worker's (running, then queued), newest first in each.
            current = ("failed", "needs_review", "running", "pending")
            selected = {"completed", "cancelled"} if status == "history" else set(current) if status == "current" else {status}
            # A job listed by its own stored status ("completed") includes archived scans, as it always did.
            if status == "completed":
                selected.add("archived")
            chosen = [row for row in listed if shown(row) in selected or (not status and not row["gone"])]
            if needs:
                # "Needs you": only what the Inbox files as waiting for the instructor (the status strip's "N need you").
                chosen = [row for row in chosen if shown(row) == "needs_review"]
            if status == "current":
                chosen.sort(key=lambda row: current.index(shown(row)))
            elif not status and kind == "command":
                # The Submissions tab: unfinished first (in Current's order), then the rest, newest first in each.
                chosen.sort(key=lambda row: current.index(shown(row)) if shown(row) in current else len(current))
            total, rows = len(chosen), chosen[offset:offset + 51]
            worker = db.execute("SELECT heartbeat_at FROM inbox_worker WHERE id=1").fetchone()
            jobs = [self.get(row["id"], db=db, events=False, roles="input" if files == "input" else None) for row in rows[:50]]
            # Only the Inbox's own read (files="input") carries the grouping fields; the default body stays as it was.
            groups = self._scan_groups(db, [row["message_id"] for row in rows[:50] if row["id"] in overviews]) if files == "input" else {}
        for job, row in zip(jobs, rows):
            if job["id"] in overviews:
                # Listed by what it holds and what is left to do, not by its command. On files="input" the list groups scans of one
                # assignment by the additive `assignment`, `pages` and `papers` (_scan_groups); the overview's own fields are unchanged.
                job["scan"], job["listed_as"] = {**overviews[job["id"]], **groups.get(row["message_id"], {})}, filing[row["id"]][0]
            if job["id"] in archived:
                job["archived"] = archive_facts(archived[job["id"]])
        return {"jobs": jobs, "counts": counts, "total": total, "has_more": len(rows) > 50, "offset": offset,
                "worker_heartbeat": worker[0] if worker else None, "email_configured": self.email.enabled, **extra}

    @staticmethod
    def _scan_plans(db, message_ids):
        """Each scan's grading assignment from its latest association ({message_id: {plan_id, title, code, term, assignment_id,
        term_id}}), in one read for the whole list. Each row is the scan's own association: nothing looks across submissions."""
        if not message_ids:
            return {}
        result = {}
        for row in db.execute(_SCAN_ASSIGNMENT + _SCAN_LATEST):
            if row["message_id"] in message_ids:
                result[row["message_id"]] = _scan_assignment(row)
        return result

    @staticmethod
    def _scan_groups(db, message_ids):
        """What the Inbox list groups the scans of one page by (an exam's copier scans fold into one row per assignment): each
        scan's grading assignment, from its latest association as `scan_status` reads it ({plan_id, title, code, term}: the
        event's title, its term's course code and "Fall 2026", or None), and how many pages and labeled papers (distinct
        printed copies) it holds. Two reads for the whole page, never one per scan; a view only. Each count is of the scan's
        own pages, so nothing here looks across submissions."""
        if not message_ids:
            return {}
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name IN ('frq_scan_links','scan_pages')")}
        marks = ",".join("?" * len(message_ids))
        result = {identifier: {"assignment": None, "pages": 0, "papers": 0} for identifier in message_ids}
        if "frq_scan_links" in tables:
            for row in db.execute(f"{_SCAN_ASSIGNMENT}l.message_id IN ({marks}) AND {_SCAN_LATEST}", message_ids):
                result[row["message_id"]]["assignment"] = _scan_assignment(row)
        if "scan_pages" in tables:
            for row in db.execute(f"""SELECT sp.message_id,COUNT(*) pages,COUNT(DISTINCT p.printed_copy_id) papers FROM scan_pages sp
                    LEFT JOIN page_stamps p ON p.id=sp.page_stamp_id WHERE sp.message_id IN ({marks}) GROUP BY sp.message_id""", message_ids):
                result[row["message_id"]].update(pages=row["pages"], papers=row["papers"])
        return result

    def read_artifact(self, identifier):
        with self.rosters.lock(), connect(self.path) as db:
            artifact = db.execute("SELECT * FROM inbox_artifacts WHERE id=?", (identifier,)).fetchone()
            if not artifact:
                raise NotFound("Inbox artifact not found.")
            message = self.message(db, artifact["message_id"])
            with self.files(message) as path:
                target = path / artifact["path"]
                if target.resolve() != target or not target.is_file():
                    raise StorageError("The retained artifact is missing or redirected.")
                content = target.read_bytes()
                if sha(content) != artifact["sha256"]:
                    raise StorageError("The retained artifact failed its checksum verification.")
                return artifact["filename"], content

    def resources(self, group_id, number=None):
        """Read-only projection of archived documents at their actual owning level."""
        with self.rosters.lock(), connect(self.path) as db:
            groups = group_courses(live_offerings(db))
            group = next((item for item in groups if item["id"] == group_id), None)
            if not group or (number is not None and number not in group["sections"]):
                raise NotFound("Course or section not found.")
            term = db.execute("SELECT id FROM course_terms WHERE definition_id=? AND term=? AND term_other=? AND year=?", term_key(group)).fetchone()
            section = db.execute("SELECT id FROM course_sections WHERE term_id=? AND number=?", (term["id"], number)).fetchone() if term and number is not None else None
            owners = {"course": group["definition_id"], "term": term["id"] if term else None, "section": section["id"] if section else None}
            result = {level: [] for level in owners}
            for level, owner in owners.items():
                if owner:
                    result[level] = [dict(row) for row in db.execute(f"""SELECT a.id,a.filename,a.size,a.created_at,a.job_id
                        FROM inbox_artifacts a JOIN inbox_jobs j ON j.id=a.job_id
                        JOIN inbox_messages m ON m.id=a.message_id
                        WHERE m.scope_level=? AND m.scope_id=? AND j.action='document.archive'
                          AND j.status='completed' AND a.role='input' AND {PRESENT.format('m.id')}
                        ORDER BY a.created_at DESC,a.id""", (level, owner))]
            self._resource_context(db, [row for rows in result.values() for row in rows])
            return result

    @staticmethod
    def _resource_context(db, rows):
        """Name the assignment, exam version and question a retained rubric source belongs to (additive, read-only).

        A rubric upload keeps its CSV as a course document; the question revision that cites it
        (frq_rubric_revisions.source_artifact_id) says what it is. Documents nothing cites stay plain."""
        if not rows or not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='frq_rubric_revisions'").fetchone():
            return
        marks = ",".join("?" for _ in rows)
        found = {}
        for item in db.execute(f"""SELECT r.source_artifact_id AS artifact, a.id AS assignment_id, a.title AS assignment_title,
                q.exam_version, q.question_key,
                (SELECT json_extract(qr.content_json,'$.title') FROM frq_question_revisions qr WHERE qr.question_id=q.id
                 ORDER BY qr.revision DESC LIMIT 1) AS question_title
              FROM frq_rubric_revisions r JOIN frq_questions q ON q.id=r.question_id
              JOIN assignment_versions v ON v.id=q.assignment_version_id JOIN assignments a ON a.id=v.assignment_id
              WHERE r.source_artifact_id IN ({marks}) ORDER BY r.created_at, r.id""", [row["id"] for row in rows]):
            found.setdefault(item["artifact"], item)
        for row in rows:
            item = found.get(row["id"])
            if item:
                row.update(assignment_id=item["assignment_id"], assignment_title=item["assignment_title"],
                           exam_version=item["exam_version"] or "", question=item["question_title"] or item["question_key"])

    def read_original(self, identifier):
        with self.rosters.lock(), connect(self.path) as db:
            message = self.message(db, identifier)
            with self.files(message) as path:
                target = path / message["original_path"]
                if target.resolve() != target:
                    raise StorageError("The original message is redirected.")
                content = target.read_bytes()
                if sha(content) != message["original_sha256"]:
                    raise StorageError("The original submission failed its checksum verification.")
                return message["original_path"], content

    def code(self, db, token, permit_used=False):
        if not isinstance(token, str) or not token.startswith("ti:") or len(token) > 100:
            raise Forbidden("Invalid submission code.")
        row = db.execute("SELECT * FROM inbox_codes WHERE token_hash=?", (token_hash(token),)).fetchone()
        if not row or row["revoked"] or row["expires_at"] <= timestamp():
            raise Forbidden("This submission code is invalid, revoked, or expired.")
        if row["used_message_id"] and not permit_used:
            raise Conflict("This one-time submission code has already been used.")
        return dict(row)

    def create_code(self, value, label, hours=168):
        if not isinstance(label, str) or not 1 <= len(label.strip()) <= 100 or type(hours) is not int or not 1 <= hours <= 2160:
            raise ValueError("Supply a label (up to 100 characters) and a lifetime of 1–2160 hours.")
        with self.rosters.lock(), connect(self.path) as db:
            command = normalize_command(value)
            if command["action"] in ("accommodations.sync", "accommodations.exams.sync"):
                raise ValueError("These imports require a current-roster preview; use the Accommodations page in Faculty.")
            if command["action"] == "aim.form_capture":
                raise ValueError("AIM form captures are recorded in Faculty's AIM window only.")
            owner = resolve(db, command)
            command = {"schema_version": 1, "action": command["action"], "target": {"level": owner["level"], "id": owner["id"]}, "metadata": command["metadata"]}
            action = ACTIONS.get(command["action"])
            if not action or command["action"] not in HANDLERS or owner["level"] not in action["scopes"] or requires_approval(command):
                raise ValueError("Submission codes support available actions that do not require instructor approval.")
            token, identifier = "ti:" + secrets.token_urlsafe(24), str(uuid.uuid4())
            expiry = (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()
            db.execute("INSERT INTO inbox_codes (id,token_hash,label,command_json,created_at,expires_at) VALUES (?,?,?,?,?,?)",
                       (identifier, token_hash(token), label.strip(), encoded(command), timestamp(), expiry))
        return {"id": identifier, "token": token, "label": label.strip(), "expires_at": expiry, "command": command}

    def create_api_key(self, label):
        if not isinstance(label, str) or not 1 <= len(label.strip()) <= 100:
            raise ValueError("Supply an API key label of 1–100 characters.")
        token, identifier = "tik_" + secrets.token_urlsafe(32), str(uuid.uuid4())
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("INSERT INTO inbox_api_keys (id,token_hash,label,created_at) VALUES (?,?,?,?)", (identifier, token_hash(token), label.strip(), timestamp()))
        return {"id": identifier, "token": token, "label": label.strip()}

    def api_identity(self, token):
        if not token or len(token) > 200:
            raise Forbidden("Invalid Inbox API key.")
        with connect(self.path) as db:
            row = db.execute("SELECT id FROM inbox_api_keys WHERE token_hash=? AND revoked=0", (token_hash(token),)).fetchone()
            if not row:
                raise Forbidden("Invalid or revoked Inbox API key.")
            return row["id"]

    PRESENT = PRESENT

    def remove(self, identifier, version, confirmation, reason="", actor="workspace"):
        """Take a submission out of the Inbox: hidden from every list, and nothing it holds counts anywhere any more.

        Nothing is erased. The original, the artifacts, the audit trail and any
        data its processor already applied are retained, and `restore` brings it
        back. The instructor must confirm in so many words, checked here and not
        only in the dialog; API keys cannot remove anything.
        """
        if actor != "workspace":
            raise Forbidden("Submissions are removed from the instructor workspace only.")
        if confirmation != "delete":
            raise ValueError('Confirm by sending confirmation "delete".')
        if not isinstance(reason, str) or len(reason) > 500:
            raise ValueError("The reason must be 500 characters or fewer.")
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM inbox_jobs WHERE id=?", (identifier,)).fetchone()
            if not row:
                raise NotFound("Inbox job not found.")
            if type(version) is not int or version != row["version"]:
                raise Conflict("This job changed. Refresh it before trying again.")
            if row["status"] in ("pending", "running"):
                raise Conflict("This job is queued or running. Cancel it, or wait for it to finish, before deleting it.")
            if db.execute("SELECT 1 FROM inbox_deletions WHERE message_id=? AND restored_at IS NULL", (row["message_id"],)).fetchone():
                raise Conflict("This submission was already deleted from the Inbox.")
            if hasattr(self, "document_scans"):
                self.document_scans.before_removal(db, row["message_id"])
            self.tombstone(db, identifier, row["message_id"], actor, reason.strip())
        return self.get(identifier)

    def tombstone(self, db, identifier, message_id, actor, reason):
        """The recorded, reversible tombstone itself, for `remove` and for a scan the worker separated by assignment (INBOX "SEPARATING
        A SCAN", actor `automatic`). The caller holds the lifecycle lock and an open transaction and has made every check."""
        db.execute("INSERT INTO inbox_deletions(message_id,actor,reason,created_at) VALUES(?,?,?,?)", (message_id, actor, reason, timestamp()))
        self.event(db, identifier, "deleted_from_inbox", json.dumps({"actor": actor, "reason": reason, "retained": True, "restorable": True}))
        if hasattr(self, "document_scans"):
            self.document_scans.visibility_changed(db, message_id)

    def _scan_to_replace(self, db, identifier, repeated, verb, only):
        """A scan that start_over or combine_scans takes in, refused as delete refuses: its job row (with the message's received_at),
        `gone` (deleted from the Inbox) and its input `files`. `verb` ("started over", "combined") ends each refusal, `only` is the
        refusal for a job that is not a scan. A deleted scan is let through only on a `repeated` request."""
        row = db.execute("""SELECT j.id,j.status,j.action,j.version,j.message_id,m.received_at,m.authenticated FROM inbox_jobs j
                            JOIN inbox_messages m ON m.id=j.message_id WHERE j.id=?""", (identifier,)).fetchone()
        if not row:
            raise NotFound("Inbox job not found.")
        if row["action"] != "scan.receive":
            raise ValueError(only)
        if not row["authenticated"]:
            raise Conflict(f"A scan whose sender failed authentication cannot be {verb}.")
        gone = db.execute("SELECT 1 FROM inbox_deletions WHERE message_id=? AND restored_at IS NULL", (row["message_id"],)).fetchone()
        if gone and not repeated:
            raise Conflict(f"A deleted scan cannot be {verb}. Restore it first.")
        if not gone:
            if row["status"] in ("pending", "running"):
                raise Conflict(f"A scan that is queued or running cannot be {verb} yet.")
            if hasattr(self, "document_scans"):
                self.document_scans.before_removal(db, row["message_id"])
        files = [dict(item) for item in db.execute("""SELECT id,filename,sha256,size FROM inbox_artifacts WHERE message_id=? AND role='input'
                                                       ORDER BY created_at,id""", (row["message_id"],))]
        if not files:
            raise ValueError(f"A scan without scanned files cannot be {verb}.")
        return {**dict(row), "gone": bool(gone), "files": files}

    def start_over(self, identifier, request_id, confirmation, keep=True, actor="workspace"):
        """Run a scan through its pipeline again from the start (requirement, 2026-09-24).

        A new scan holds the same PDFs, byte for byte, each its own attachment, and is inspected afresh like any arrival. The old scan
        moves to Deleted with the ordinary reversible tombstone, its reason naming the new one; everything done on it stays with it
        and restoring it brings it back as it was. With `keep`, the choices made on it (`document_scans.start_over_plan`: its
        assignment and sections, and the search for papers without labels with its sides, versions and automatic registration) are
        recorded on the new scan and applied once it is inspected (`document_scans.apply_start_over`); its results are never carried.
        Anything delete refuses (a reviewed submission, work under way) refuses this before anything is made. The request ID keys the
        new scan: repeating the same request finishes an interrupted start-over.
        """
        if actor != "workspace":
            raise Forbidden("Scans are started over from the instructor workspace only.")
        if confirmation != "start over":
            raise ValueError('Confirm by sending confirmation "start over".')
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
            raise ValueError("A request ID of at most 128 characters is required.")
        if type(keep) is not bool:
            raise ValueError("Say whether the scan's assignment and reading settings are kept.")
        key = f"restart:{request_id}"
        with self.rosters.lock(), connect(self.path) as db:
            repeated = db.execute("SELECT 1 FROM inbox_messages WHERE dedupe_key=?", (encoded(["web", actor, key]),)).fetchone()
            row = self._scan_to_replace(db, identifier, repeated, "started over", "Only a scan can be started over.")
            gone, files = row["gone"], row["files"]
            carry = self.document_scans.start_over_plan(db, row["message_id"]) if keep and hasattr(self, "document_scans") else {}
        attachments = []
        for file in files:
            filename, content = self.read_artifact(file["id"])
            attachments.append({"filename": filename, "content": content})
        job = self.submit({"action": "scan.receive"}, attachments, source="web", sender=actor, idempotency_key=key,
                          instructor_review_required=True, large_scans=True)
        with self.rosters.lock(), connect(self.path) as db:
            if not db.execute("SELECT 1 FROM inbox_events WHERE job_id=? AND kind='scan_started_over'", (job["id"],)).fetchone():
                self.event(db, job["id"], "scan_started_over", encoded({"actor": actor, "request_id": request_id, "keep": keep, "carry": carry,
                           "source": {"job_id": row["id"], "message_id": row["message_id"],
                                      "files": [{field: file[field] for field in ("filename", "sha256", "size")} for file in files]}}))
        if not gone:
            try:
                self.remove(row["id"], self.get(row["id"])["version"], "delete", f"Started over as scan {job['id']}.", actor)
            except (Conflict, ValueError) as error:
                raise Conflict(f"The new scan was made, but the old one could not be moved to Deleted: {error} Send the same request again to finish.") from error
        return self.get(job["id"])

    def combine_scans(self, job_ids, request_id, confirmation, actor="workspace"):
        """Make one scan of several (requirement, 2026-09-24): a scanner run that came out as more than one PDF, or a late
        paper scanned on its own, uploaded and listed separately.

        The new scan holds every source PDF, byte for byte, as its own attachment, so each file is still read on its own
        and no paper runs from one into the next. It is inspected afresh and needs review like any other scan; work done
        on the sources stays with them. The sources are deleted from the Inbox with the ordinary reversible tombstone,
        their reason naming the new scan, so nothing is erased and restoring one brings it back as it was. The request ID
        keys the new scan: repeating the same request finishes it instead of making another.
        """
        if actor != "workspace":
            raise Forbidden("Scans are combined from the instructor workspace only.")
        if confirmation != "combine":
            raise ValueError('Confirm by sending confirmation "combine".')
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
            raise ValueError("A request ID of at most 128 characters is required.")
        if (not isinstance(job_ids, list) or not 2 <= len(job_ids) <= MAX_FILES or any(not isinstance(item, str) for item in job_ids)
                or len(set(job_ids)) != len(job_ids)):
            raise ValueError(f"Choose between 2 and {MAX_FILES} different scans to combine.")
        key = f"combine:{request_id}"
        with self.rosters.lock(), connect(self.path) as db:
            # A repeated request may find its sources already deleted by its first attempt.
            repeated = db.execute("SELECT 1 FROM inbox_messages WHERE dedupe_key=?", (encoded(["web", actor, key]),)).fetchone()
            sources = []
            for identifier in job_ids:
                sources.append(self._scan_to_replace(db, identifier, repeated, "combined", "Only scans can be combined."))
        # The record lists the sources in the order they arrived. Each file stays its own attachment, read on its own.
        sources.sort(key=lambda source: (source["received_at"], source["id"]))
        attachments = []
        for source in sources:
            for file in source["files"]:
                filename, content = self.read_artifact(file["id"])
                attachments.append({"filename": filename, "content": content})
        job = self.submit({"action": "scan.receive"}, attachments, source="web", sender=actor, idempotency_key=key,
                          instructor_review_required=True, large_scans=True)
        with self.rosters.lock(), connect(self.path) as db:
            if not db.execute("SELECT 1 FROM inbox_events WHERE job_id=? AND kind='scans_combined'", (job["id"],)).fetchone():
                self.event(db, job["id"], "scans_combined", encoded({"actor": actor, "request_id": request_id, "sources": [
                    {"job_id": source["id"], "message_id": source["message_id"], "received_at": source["received_at"],
                     "files": [{field: file[field] for field in ("filename", "sha256", "size")} for file in source["files"]]} for source in sources]}))
        reason = f"Combined with {len(sources) - 1} other scan{'s' if len(sources) > 2 else ''} into scan {job['id']}."
        for source in sources:
            if source["gone"]:
                continue
            try:
                self.remove(source["id"], self.get(source["id"])["version"], "delete", reason, actor)
            except (Conflict, ValueError) as error:
                raise Conflict(f"The combined scan was made, but {source['files'][0]['filename']} could not be moved to Deleted: {error}"
                               " Send the same request again to finish.") from error
        return self.get(job["id"])

    def restore(self, identifier, actor="workspace"):
        if actor != "workspace":
            raise Forbidden("Submissions are restored from the instructor workspace only.")
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM inbox_jobs WHERE id=?", (identifier,)).fetchone()
            if not row:
                raise NotFound("Inbox job not found.")
            changed = db.execute("UPDATE inbox_deletions SET restored_at=?,restored_by=? WHERE message_id=? AND restored_at IS NULL", (timestamp(), actor, row["message_id"])).rowcount
            if not changed:
                raise Conflict("This submission is not deleted.")
            self.event(db, identifier, "restored_to_inbox", json.dumps({"actor": actor}))
            if hasattr(self, "document_scans"):
                self.document_scans.visibility_changed(db, row["message_id"])
        return self.get(identifier)

    def transition(self, identifier, operation, version, value=None, actor="workspace"):
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM inbox_jobs WHERE id=?", (identifier,)).fetchone()
            if not row:
                raise NotFound("Inbox job not found.")
            if db.execute("SELECT 1 FROM inbox_deletions WHERE message_id=? AND restored_at IS NULL", (row["message_id"],)).fetchone():
                raise Conflict("Restore this submission to the Inbox before changing it.")
            if type(version) is not int or version != row["version"]:
                raise Conflict("This job changed. Refresh it before trying again.")
            if row["status"] in ("running", "completed"):
                raise Conflict("Running or completed jobs cannot be changed.")
            message = self.message(db, row["message_id"])
            if not message["authenticated"] and operation != "cancel":
                raise Forbidden("Authentication failures cannot be approved or retried. Submit a new authenticated command.")
            # The worker's automatic pass reviews only a scan whose overview has nothing left to check (`grading.intake.auto_review_one`).
            reviewer = actor == "workspace" or (actor == "automatic" and row["action"] == "scan.receive")
            if message["instructor_review_required"] and operation != "cancel" and (not reviewer or operation != "review"):
                raise Forbidden("This scan requires a review in the instructor workspace before it can be queued.")
            if operation == "cancel":
                db.execute("UPDATE inbox_jobs SET status='cancelled',error='',approved_hash=NULL,version=version+1 WHERE id=?", (identifier,))
                self.event(db, identifier, "cancelled", "Cancelled by instructor. Originals and artifacts remain retained.")
            else:
                if operation not in ("review", "retry", "approve"):
                    raise ValueError("Unknown job operation.")
                if operation == "review" and row["attempts"]:
                    raise Conflict("This job has already attempted execution. Retry its original command or submit a new job to change it.")
                if operation == "approve" and actor != "workspace":
                    raise Forbidden("Consequential jobs require a separate approval in the instructor workspace.")
                files = [dict(item) for item in db.execute("SELECT filename FROM inbox_artifacts WHERE job_id=? AND role='input'", (identifier,))]
                command, owner, problem = self.classify(db, value if operation == "review" else json.loads(row["parameters_json"]), files)
                approved = operation == "approve"
                if approved and (problem or not requires_approval(command)):
                    raise Conflict(problem or "This action does not require approval. Use Retry to queue it.")
                if message["storage_status"] in PENDING:
                    raise Conflict("Inbox storage is being moved. Wait for it to finish before changing this job.")
                if owner is None:
                    owner = {"level":None,"id":None,"definition_id":None,"term_id":None,"section_id":None,
                             "label":WORKSPACE_LABELS[command["action"]],"code":"","term":"","term_other":"","year":None,"sections":[],"is_current":False}
                desired = owner["is_current"]
                storage_status = message["storage_status"]
                if self.storage and bool(message["is_current"]) != desired:
                    storage_status = "restore_pending" if desired else "archive_pending"
                elif self.storage:
                    storage_status = "sync_pending" if desired else "archive_pending"
                db.execute("""UPDATE inbox_messages SET scope_level=?,scope_id=?,definition_id=?,term_id=?,section_id=?,
                    name=?,code=?,term=?,term_other=?,year=?,sections=?,is_current=?,storage_status=?,updated_at=?,version=version+1 WHERE id=?""",
                    (owner["level"], owner["id"], owner["definition_id"], owner["term_id"], owner["section_id"],
                     "Inbox · " + owner["label"], owner["code"], owner["term"], owner["term_other"], owner["year"],
                     encoded(owner["sections"]), desired, storage_status, timestamp(), message["id"]))
                digest = sha(encoded(command).encode()) if approved else None
                waiting = problem or (requires_approval(command) and not approved)
                status = "needs_review" if waiting else "pending"
                error = problem or ("Explicit instructor approval is required before execution." if waiting else "")
                db.execute("""UPDATE inbox_jobs SET action=?,parameters_json=?,status=?,error=?,approved_hash=?,
                    approved_at=?,approved_by=?,completed_at=NULL,version=version+1 WHERE id=?""",
                    (command["action"], encoded(command), status, error, digest,
                     timestamp() if approved else None, actor if approved else None, identifier))
                if operation == "review":
                    db.execute("UPDATE inbox_messages SET instructor_review_required=0 WHERE id=?", (message["id"],))
                    self.event(db, identifier, "reviewed", encoded({"by": actor, "previous_command": json.loads(row["parameters_json"]), "command": command}))
                self.event(db, identifier, "approved" if approved else status, error or f"{operation.capitalize()} requested by {actor}.")
                reviewed_scan = operation == "review" and command["action"] == "scan.receive"
        # After the review is committed: exams labeled on the first page only become reviewed papers.
        scans = getattr(self, "document_scans", None)
        if operation != "cancel" and reviewed_scan and scans is not None:
            try:
                scans.sequence.assemble_reviewed(row["message_id"], "inbox-review")
            except Exception:
                # The review is already committed and stands. Papers left unassembled keep the per-copy assembly review.
                LOG.exception("Reviewed scan %s: its first-page-labeled exams could not be assembled", row["message_id"])
                with self.rosters.lock(), connect(self.path) as db:
                    self.event(db, identifier, "first_page_papers_not_assembled", "The review is saved. Assemble these papers from the scan's review.")
        self.wake.set()
        if self.storage:
            self.storage.wake.set()
        return self.get(identifier)

    @contextmanager
    def worker_lock(self):
        if self.storage:
            with self.storage.lock(WORKER_LOCK, blocking=False):
                yield
        else:
            import fcntl
            descriptor = os.open(self.path.parent / ".inbox-worker.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            try:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError as error:
                    raise StorageError("Another Inbox worker is already executing a job.") from error
                yield
            finally:
                os.close(descriptor)

    def _receipt(self, job, result=None):
        identifier = str(uuid.uuid5(uuid.UUID(job["id"]), "result"))
        relative = f"outputs/{identifier}.json"
        command_hash = sha(encoded(job["parameters"]).encode())
        with self.rosters.lock():
            with connect(self.path) as db:
                message = self.message(db, job["message_id"])
            with self.files(message, write=result is not None) as path:
                file = path / relative
                if file.resolve() != file:
                    raise StorageError("The job receipt path is redirected.")
                if file.exists():
                    content = file.read_bytes()
                    try:
                        receipt = json.loads(content)
                        if receipt["job_id"] != job["id"] or receipt["command_hash"] != command_hash or not isinstance(receipt["result"], dict):
                            raise ValueError("Receipt mismatch")
                    except (ValueError, KeyError, TypeError) as error:
                        raise StorageError("The saved result receipt does not match this job. It was retained for review.") from error
                    result = receipt["result"]
                elif result is None:
                    return None
                else:
                    file.parent.mkdir(mode=0o700, exist_ok=True)
                    atomic_json(file, {"job_id": job["id"], "command_hash": command_hash, "result": result, "software_version": VERSION})
                    content = file.read_bytes()
            # The NVMe file is flushed before the catalog points at it. HDD
            # publication belongs to the coordinated nightly backup.
            with connect(self.path) as db:
                existing = db.execute("SELECT sha256 FROM inbox_artifacts WHERE id=?", (identifier,)).fetchone()
                if existing and existing[0] != sha(content):
                    raise StorageError("The job result failed its recorded checksum.")
                db.execute("INSERT OR IGNORE INTO inbox_artifacts VALUES (?,?,?,?,?,?,?,?,?)",
                    (identifier, message["id"], job["id"], "result.json", relative, "output", len(content), sha(content), timestamp()))
            return result

    def process_one(self):
        """One worker owns execution. Interrupted runs require an explicit retry."""
        with self.worker_lock():
            with self.rosters.lock(), connect(self.path) as db:
                db.execute("BEGIN IMMEDIATE")
                db.execute("INSERT INTO inbox_worker VALUES (1,?) ON CONFLICT(id) DO UPDATE SET heartbeat_at=excluded.heartbeat_at", (timestamp(),))
                for row in db.execute("SELECT id FROM inbox_jobs WHERE status='running'").fetchall():
                    db.execute("UPDATE inbox_jobs SET status='needs_review',error=?,approved_hash=NULL,version=version+1 WHERE id=?",
                               ("Worker stopped during execution. Inspect the retained result and explicitly retry when ready.", row["id"]))
                    self.event(db, row["id"], "interrupted", "The prior worker exited. No automatic replay was performed.")
                row = db.execute(f"""SELECT j.id,m.instructor_review_required FROM inbox_jobs j
                    JOIN inbox_messages m ON m.id=j.message_id
                    WHERE j.status='pending' AND {PRESENT.format('m.id')} ORDER BY j.created_at,j.id LIMIT 1""").fetchone()
                if not row:
                    return False
                identifier = row["id"]
                if row["instructor_review_required"]:
                    error = "This scan requires a review in the instructor workspace before execution."
                    db.execute("UPDATE inbox_jobs SET status='needs_review',error=?,version=version+1 WHERE id=?", (error, identifier))
                    self.event(db, identifier, "needs_review", error)
                    return True  # No execution attempt: the instructor can still review it.
                db.execute("UPDATE inbox_jobs SET status='running',started_at=?,attempts=attempts+1,error='',version=version+1 WHERE id=?", (timestamp(), identifier))
                self.event(db, identifier, "running", "Worker claimed this job.")
            job = self.get(identifier)
            try:
                with connect(self.path) as db:
                    command, owner, problem = self.classify(db, job["parameters"], [file for file in job["artifacts"] if file["role"] == "input"])
                if not job["authenticated"]:
                    problem = "Submission authentication is not valid. Execution refused."
                if job["instructor_review_required"]:
                    problem = "This scan requires a review in the instructor workspace before execution."
                if requires_approval(command) and job["approved_hash"] != sha(encoded(command).encode()):
                    problem = "A separate instructor approval of this exact command is required."
                if problem:
                    self._finish(identifier, "needs_review", problem)
                    return True
                self.read_original(job["message_id"])
                result = self._receipt(job)
                if result is None:
                    files = []
                    for artifact in job["artifacts"]:
                        if artifact["role"] == "input":
                            name, content = self.read_artifact(artifact["id"])
                            files.append({**artifact, "filename": name, "content": content})
                    result = HANDLERS[command["action"]](self, job, files)
                    if not isinstance(result, dict):
                        raise ValueError("The processor did not return a structured result.")
                    result = self._receipt(job, result)
                self._finish(identifier, "completed", "", result)
            except (ValueError, StorageError, OSError) as error:
                self._finish(identifier, "failed", str(error))
            except Exception:
                LOG.exception("Teaching Inbox job %s failed", identifier)
                self._finish(identifier, "failed", "The processor stopped unexpectedly. Check the worker log before retrying.")
            return True

    def _finish(self, identifier, status, error, result=None):
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("UPDATE inbox_jobs SET status=?,error=?,result_json=?,completed_at=?,version=version+1 WHERE id=?",
                       (status, error[:2000], encoded(result or {}), timestamp() if status == "completed" else None, identifier))
            self.event(db, identifier, status, error[:2000] or (result or {}).get("message", "Processor completed."))

    def work_once(self):
        """Give each independent queue a bounded turn, skipping empty queues.

        Every continuously ready queue runs within one rotation, even while
        intake or revocations remain busy. Processors retain their own claiming,
        execution locks, prerequisites, approval checks and durable receipts.
        This is turn fairness, not preemption of a running PDF/OCR operation.
        """
        with self.worker_lock(), self.rosters.lock(), connect(self.path) as db:
            db.execute("INSERT INTO inbox_worker VALUES (1,?) ON CONFLICT(id) DO UPDATE SET heartbeat_at=excluded.heartbeat_at", (timestamp(),))

        def stage(owner, method="process_one"):
            return getattr(owner, method, None) if owner is not None else None

        cards = getattr(self, "response_cards", None)

        def revoke():
            with self.worker_lock():
                return bool(cards.reconcile_invalidations(limit=1)["complete"])

        def mail(processor):
            def send_one():
                with self.worker_lock():
                    # An independent revocation slot cannot guarantee ordering
                    # when a turn begins at mail. Retry a bounded old link here
                    # too; senders still validate their exact current context.
                    # A completion must not consume the mail slot indefinitely.
                    changed = bool(cards.reconcile_invalidations(limit=1)["complete"]) if cards else False
                    return bool(processor()) or changed
            return send_one if processor else None

        scans = getattr(self, "document_scans", None)
        queues = (
            self.process_one,
            stage(getattr(self, "documents", None), "build_one"),
            stage(getattr(self, "print_email_exports", None)),
            revoke if cards else None,
            mail(stage(getattr(cards, "mail", None))),
            mail(stage(getattr(self, "student_chart_mail", None))),
            stage(scans),
            stage(getattr(scans, "separation", None)),  # INBOX "SEPARATING A SCAN": one scan naming two assignments a turn
            stage(getattr(scans, "alias_ocr", None)),
            stage(getattr(scans, "version_sort", None)),
            stage(getattr(scans, "unlabeled", None)),
            stage(getattr(scans, "photocopies", None)),  # QUIZZES "PHOTOCOPIED STAMPS"
            stage(getattr(scans, "copy_names", None)),
            stage(getattr(scans, "assignment_finder", None)),
            stage(getattr(self, "bubble_sheets", None)),
            stage(getattr(self, "frq_grading", None)),
            stage(getattr(self, "assistant_watches", None)),
            stage(getattr(self, "assistant_workflows", None)),
            stage(getattr(self, "question_bank", None)),
            stage(getattr(self, "student_triage", None)),
            stage(getattr(self, "followup_suggestions", None)),
            stage(getattr(self, "absences", None), "expire_one"),  # ABSENCES DECISION DEADLINE
            stage(getattr(self, "department_makeups", None)),  # ABSENCES DEPARTMENT MAKEUP EXAMS: form receipts from Gmail
            stage(getattr(self, "makeup_booking", None)),  # ABSENCES MAKEUP BOOKING
            stage(getattr(self, "dr_quiz_notices", None)),  # SCHEDULING DR QUIZ NOTICES: one student's email a turn
            stage(getattr(self, "dr_requests", None)),  # SCHEDULING DR REQUEST EMAILS: request emails, one Follow-up a turn
            stage(getattr(self, "picture_search", None)),  # PICTURE-SEARCH: one page's vector a turn
            stage(getattr(self, "scan_folder", None)),  # SCANNER-INTAKE "SCANNER FOLDER": one settled PDF a turn
            stage(getattr(self, "printer_sends", None)),  # DOCUMENTS "SEND TO PRINTER": one approved send a turn
        )
        for _ in queues:
            index = self._work_queue_cursor % len(queues)
            # Advance before calling so a failing processor cannot monopolize
            # subsequent worker turns either.
            self._work_queue_cursor = (index + 1) % len(queues)
            processor = queues[index]
            if processor is not None and processor():
                return True
        return False

    def worker(self, stop=None):
        stop = stop or threading.Event()
        # Cloud grading runs beside this loop, in parallel and under its own lock: the bounded, one-component
        # turns below exist for the single local GPU and must not throttle requests to a cloud host.
        cloud = getattr(getattr(self, "frq_grading", None), "cloud_worker", None)
        if cloud:
            threading.Thread(target=cloud, args=(stop,), name="frq-cloud-dispatcher", daemon=True).start()
        # Sending grades to Gradescope is network work too: its own thread and lock, never this loop's execution lock.
        sender = getattr(getattr(self, "gradescope", None), "worker", None)
        if sender:
            threading.Thread(target=sender, args=(stop,), name="gradescope-sender", daemon=True).start()
        # Google Calendar sync: a round every two minutes, network work under its own lock (google_calendar/service.py).
        calendar = getattr(getattr(self, "google_calendar", None), "worker", None)
        if calendar:
            threading.Thread(target=calendar, args=(stop,), name="google-calendar-sync", daemon=True).start()
        # The communications record: capture rounds of Gmail, Canvas and Gradescope, network reads under their own lock.
        capture = getattr(getattr(self, "communications", None), "worker", None)
        if capture:
            threading.Thread(target=capture, args=(stop,), name="communications-capture", daemon=True).start()
        # Recordings (RECORDINGS "NO PUSH, SO A POLL"): Plaud listed every ten minutes, what is filed as Faculty's fetched; network
        # reads under their own lock, never this loop's execution lock.
        recordings = getattr(getattr(self, "recordings", None), "worker", None)
        if recordings:
            threading.Thread(target=recordings, args=(stop,), name="recordings-capture", daemon=True).start()
        while not stop.is_set():
            try:
                worked = self.work_once()
            except Exception:
                LOG.exception("Teaching Inbox worker could not access its queue")
                worked = False
            if not worked:
                stop.wait(2)
