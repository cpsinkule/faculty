"""The Faculty assistant (requirement, 2026-09-29; AGENTS.md "Faculty assistant"): an AI agent that answers questions about what
Faculty holds and works through the app. This is the one place every tool call lands, from the in-app panel's engines and from
outside MCP clients (`manage_assistant.py mcp`), so the rules are applied once:

- Reads go through the app's own GET routes in-process (`transport`), plus read-only documents, source and catalog queries.
- Writes follow `policy`: previews run at once; the instructor's chosen categories run at once and are logged; everything else
  becomes an Approve card. The instructor's click runs the page's own preview again, adds the `confirmation` and the preview's
  fingerprint, and submits the ordinary route. The assistant never supplies a confirmation, and no tool reaches approval.

Owner: WORKSPACE. `assistant_actions` and their events are append-only catalog records (nightly catalog backup; left out of the
generic export, since request bodies can hold student information). Sessions live in this process; conversations are retained
only when the instructor explicitly saves them (store.py, ASSISTANT-RETENTION).
"""
from __future__ import annotations

import contextlib
import json
import re
import secrets
import subprocess
import threading
import time
import uuid
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from werkzeug.exceptions import Conflict, Gone, HTTPException, NotFound

from canonical_json import ascii_digest
from catalog_migrations import apply_sql, migration_transaction
from course_storage import timestamp
from rosters import connect

from . import policy, tools as toolkit
from .documents import PRIMER
from .lanes import Lanes
from .outcomes import Outcomes, RECEIPTS, link, of_view, settled
from .transport import Transport, render, shrink, clean

PLACEHOLDER = "$new_request_id"
PENDING = ("proposed", "previewed")
PENDING_SQL = "(" + ",".join(f"'{kind}'" for kind in PENDING) + ")"  # ASSISTANT-APPROVALS: what "waiting" means, in SQL
EXPIRY = timedelta(hours=24)
SLOW_APPROVAL = 5  # seconds: an approval that takes longer says in the log where its time went
SESSION_IDLE = 12 * 3600
MAX_BODY = 512 * 1024
# ASSISTANT-BATCHES: a group of different changes, each card reviewed in it. ONE CARD FOR ONE CHANGE (requirement, 2026-10-09): cards of one route, the same
# change for each record, group up to SAME_CHANGE_LIMIT and are drawn as one summary card with Approve all.
BATCH_LIMIT = 20
SAME_CHANGE_LIMIT = 200
# GROUP BY DEFAULT (CANVAS "MESSAGES TO CHOSEN SECTIONS"; requirement, 2026-10-13): that box is the instructor's. The Assistant's tick stands only when one of their own latest messages (at most
# three, as the tool-use checks read the request) asks for it in these words; otherwise Faculty unticks it and says so.
INDIVIDUAL_ASKED = re.compile(r"\bindividual(?:ly)?\b|\bseparately\b|\bseparate (?:messages?|conversations?|threads?)\b"
                              r"|\bown (?:message|conversation|thread)\b|\bone[- ]by[- ]one\b|\bprivately\b", re.I)
# APPOINTMENTS ARE NOT CONVERSATIONS (2026-10-16, an approved appointment for Dana's "after 1 PM" email failed at Approve with
# "Appointments cannot be linked to a conversation." because the model gave it the email's `source_key`): an appointment is the
# student's own record (TEACHING-WORKFLOWS "APPOINTMENTS"), never a conversation's, so Faculty drops the key before the card is made.
APPOINTMENT_SOURCE_REMOVED = ("`source_key` was removed: an appointment belongs to the student, never to a conversation. Do not "
                              "add it again; the reply in this conversation still says the time.")
INDIVIDUAL_REMOVED = ("`individual` was removed: this goes as one group conversation in each Canvas course, the instructor's "
                      "default. Do not prepare it again or ask about it; the card says how it goes. Each student gets their own "
                      "conversation only when the instructor's own message asks for individual or separate messages.")


def _now():
    return datetime.now(timezone.utc)


def _latest_kind(card):
    """SQL for the kind of an action's newest event; `card` is the alias of assistant_actions (the events alias `e` is fixed)."""
    return f"(SELECT kind FROM assistant_action_events e WHERE e.action_id={card}.id ORDER BY e.id DESC LIMIT 1)"


def _went_through(view):
    """A card the instructor approved that succeeded, or that Faculty is still carrying out (a Canvas push or message, a regrade's
    Apply: already approved, never submitted again); a Mail send only once Mail accepted it (one not confirmed still stops a run)."""
    return not view['pending'] and of_view(view) in ('succeeded', 'started')


def _digest(value):
    return ascii_digest(value, default=str)


def _substitute(value, request_id):
    if isinstance(value, dict):
        return {k: _substitute(v, request_id) for k, v in value.items()}
    if isinstance(value, list):
        return [_substitute(v, request_id) for v in value]
    return request_id if value == PLACEHOLDER else value


def _is_uuid(value):
    """A request ID as Faculty makes them (every route that needs one accepts it), not the model's own wording of one."""
    try:
        return isinstance(value, str) and str(uuid.UUID(value)) == value
    except ValueError:
        return False


class Context:
    """What a policy check may look at: catalog facts and the gradebook's current setup, read through the ordinary routes."""

    def __init__(self, service):
        self.service = service

    def gradebook_setup(self, term_id):
        if not term_id:
            return None
        status, data, _ = self.service.transport.request("GET", f"/api/gradebook/terms/{term_id}")
        return data.get("setup") if status == 200 and isinstance(data, dict) else None

    def job_assignment(self, jid):
        with connect(self.service.database) as db:
            row = db.execute("SELECT assignment_id FROM frq_jobs WHERE id=?", (jid,)).fetchone()
        return row["assignment_id"] if row else None

    def retry_assignments(self, endpoint, args):
        """Resolve retry ownership without touching a payload (ASSISTANT-CERTIFIED)."""
        with connect(self.service.database) as db:
            if endpoint == "frq_grading.retry_preparation":
                row = db.execute("SELECT assignment_id FROM frq_preparations WHERE id=?", (args.get("identifier"),)).fetchone()
                return [row[0]] if row else []
            message_id = args.get("message_id")
            if endpoint == "documents.alias_ocr_retry":
                row = db.execute("SELECT message_id FROM scan_pages WHERE id=?", (args.get("scan_id"),)).fetchone()
                message_id = row[0] if row else None
            elif endpoint == "inbox.change_job":
                row = db.execute("SELECT message_id FROM inbox_jobs WHERE id=?", (args.get("identifier"),)).fetchone()
                message_id = row[0] if row else None
            if not message_id:
                return []
            # Include every historical explicit association and every identified paper in this scan. A relink must not
            # quietly make a retry on a previously certified assessment automatic again.
            rows = db.execute("SELECT DISTINCT assignment_id FROM frq_scan_links WHERE message_id=?", (message_id,)).fetchall()
            rows += db.execute("""SELECT DISTINCT f.id FROM scan_pages p JOIN page_stamps s ON s.id=p.page_stamp_id
                JOIN printed_copies c ON c.id=s.printed_copy_id JOIN print_batches b ON b.id=c.print_batch_id
                JOIN assignment_versions v ON v.id=b.assignment_version_id
                JOIN exam_events e ON e.assignment_id=v.assignment_id AND e.term_id=b.term_id
                JOIN frq_assignments f ON f.event_id=e.id
                WHERE p.message_id=?""", (message_id,)).fetchall()
            for table in ("bubble_runs", "scan_unlabeled_runs"):
                rows += db.execute(f"""SELECT DISTINCT f.id FROM {table} r
                    JOIN exam_events e ON e.assignment_id=r.assignment_id AND e.term_id=r.term_id
                    JOIN frq_assignments f ON f.event_id=e.id WHERE r.message_id=?""", (message_id,)).fetchall()
            return [r[0] for r in rows]

    def ever_certified(self, aid):
        with connect(self.service.database) as db:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='frq_certifications'").fetchone():
                return False
            return bool(db.execute("SELECT 1 FROM frq_certifications WHERE assignment_id=? LIMIT 1", (aid,)).fetchone())


class ToolContext:
    def __init__(self, service, session):
        self.service, self.session = service, session
        self.files, self.pictures = [], []  # what `fetch_files` shows the instructor, and the pictures it sends the model

    def get(self, path, query=None):
        status, data, note = self.service.transport.get(path, query)
        if note:
            return {"status": status, "note": note}
        if status >= 400:
            return {"status": status, "error": (data or {}).get("error") if isinstance(data, dict) else None}
        return data

    def write(self, method, path, body, summary, page=None, assumption=None):
        return self.service.write(self.session, method, path, body, summary, page, assumption)



def _assumption(value):
    """ASSUME, DON'T ASK (requirement, 2026-10-04: when unsure, decide from the term policies and flag the guess): what the
    assistant assumed on a card, {text, alternatives (at most 3)}, bounded; None when nothing usable was given."""
    if not isinstance(value, dict) or not isinstance(value.get("text"), str) or not value["text"].strip():
        return None
    others = value.get("alternatives") if isinstance(value.get("alternatives"), list) else []
    return {"text": " ".join(value["text"].split())[:300],
            "alternatives": [" ".join(str(item).split())[:120] for item in others if str(item).strip()][:3]}

# Migrations that make a table again, which SQLite cannot change in place (catalog_migrations.migration_transaction `rebuild`).
# 015: ASSISTANT-MEMORY "SAID IN A RECORDING" adds `recording` to the sources a memory revision may have.
REBUILDS = {"015_memory_said_in_a_recording": ("assistant_memory_revisions",)}


class AssistantService:
    def __init__(self, app, rosters):
        self.app, self.rosters = app, rosters
        self.database = rosters.path
        self.transport = Transport(app)
        self.context = Context(self)
        self.tools = toolkit.registry()
        self._sessions, self._lock, self._approve_lock = {}, threading.Lock(), threading.RLock()
        self._approvals, self._approvals_lock = 0, threading.Lock()  # Approve clicks being carried out (or waiting their turn): the restart check waits for them
        self.lanes = Lanes()  # APPROVALS SIDE BY SIDE: what an approval holds while it runs (assistant/lanes.py)
        self._sources = None
        self.conversations = None  # set by the panel's engines (assistant/conversations.py)
        self.coding = None  # changes of Faculty itself (assistant/coding.py), set by create_app
        self.followups = None  # FollowupSuggestions (assistant/followups.py), set by create_app
        migrations = sorted((Path(__file__).parent / "migrations").glob("*.sql"))
        with rosters.lock(), connect(self.database) as db:
            applied = set()
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'").fetchone():
                applied = {row[0] for row in db.execute("SELECT version FROM schema_migrations WHERE namespace='assistant'")}
            # A migration that makes a table again (REBUILDS) runs with foreign keys suspended and checked, only while it is pending.
            rebuilt = tuple(table for migration in migrations if migration.stem not in applied for table in REBUILDS.get(migration.stem, ()))
            with migration_transaction(db, rebuild=rebuilt):
                for migration in migrations:
                    apply_sql(db, "assistant", migration.stem, migration.read_text(encoding="utf-8"))
        from .store import AssistantStore
        from .watches import Watches
        from .artifacts import Artifacts
        self.store = AssistantStore(self)
        from .memory import AssistantMemory
        self.memory = AssistantMemory(self)  # ASSISTANT MEMORY: kept from the instructor's own chat messages, with history
        from .uploads import ChatUploads
        self.uploads = ChatUploads()  # ATTACH IN CHAT: files the instructor gives the Assistant, kept a day
        self.watches = Watches(self)
        self.artifacts = Artifacts(self)
        from .workflows import Workflows
        self.workflows = Workflows(self)
        from .materials import Materials
        self.materials = Materials(self)

    # Sessions: who is asking (the panel's conversation or an outside MCP client). In memory only. ---------------------------

    def open_session(self, engine="external", label=""):
        session = {"id": uuid.uuid4().hex, "token": secrets.token_urlsafe(32), "engine": str(engine)[:40] or "external",
                   "label": str(label)[:120], "used": time.monotonic()}
        with self._lock:
            now = time.monotonic()
            for key in [k for k, s in self._sessions.items() if now - s["used"] > SESSION_IDLE]:
                del self._sessions[key]
            self._sessions[session["id"]] = session
        return session

    def session(self, session_id, token):
        with self._lock:
            session = self._sessions.get(session_id)
        if not session or not isinstance(token, str) or not secrets.compare_digest(session["token"], token):
            raise Gone("This assistant session ended. Start a new one.")
        session["used"] = time.monotonic()
        return session

    def close_session(self, session_id):
        with self._lock:
            self._sessions.pop(session_id, None)

    # Tools ------------------------------------------------------------------------------------------------------------------

    def run_tool(self, session, name, args):
        """(text for the model, event for the panel). Errors come back as text the model can act on, never as exceptions. The event
        of `fetch_files` names the files shown (`files`) and carries the pictures for the model (`pictures`, base64): the caller
        takes those out (the MCP route and the hosted loop), and the conversation keeps only the routes."""
        # ABSENCES / ASSISTANT: email prefill sessions have no tools, even if an engine invents a call.
        if session.get("tools_disabled"):
            return json.dumps({"error": session.get('tools_disabled_reason', "This draft can only extract absence details; tools are disabled.")}), {"tool": name, "label": name, "ok": False}
        tool = self.tools.get(name)
        # ASSISTANT "Selected-area conversations": the first analysis runs only tools defined as reads (`Tool.read`).
        if session.get('_capture_analysis_only') and not (tool and tool.read):
            return json.dumps({'error': 'ASSISTANT: the initial selected-area analysis is read-only. Wait for an instructor follow-up.'}), {'tool': name, 'label': name, 'ok': False}
        if tool is None:
            return json.dumps({"error": f"No tool {name}."}), {"tool": name, "label": name, "ok": False}
        args = args if isinstance(args, dict) else {}
        label = tool.label(args) if tool.label else name
        try:
            guidance = session.get('_tool_guidance')
            if guidance and name != 'tool_use_advice':
                held = guidance.before_tool(name, args, tool.read)
                if held:
                    return render(held), {'tool': name, 'label': label, 'ok': False}
            context = ToolContext(self, session)
            from .evidence import capture, record
            with capture(include_text="_jev_sources" in session or bool(guidance and guidance.active)) as evidence:
                result = tool.handler(context, args)
                # The primer's note (documents.PRIMER) is no evidence of anything the answer says: it is not a source.
                if name in ("read_docs", "read_source", "catalog_sql") and not (name == "read_docs" and args.get("name") == PRIMER):
                    provenance = "Catalog query" if name == "catalog_sql" else str(args.get("name") or args.get("path") or "Documentation index")
                    record(provenance, result)
                    evidence[-1]["kind"] = "catalog" if name == "catalog_sql" else "repository"
                    evidence[-1]["read"] = {k: args[k] for k in ("query", "limit", "section", "start", "lines", "offset") if k in args}
                    if isinstance(result, dict) and result.get("fingerprint"):
                        evidence[-1]["versions"]["document_sha256"] = result["fingerprint"]
            if guidance and name != 'tool_use_advice':
                result = guidance.after_tool(name, args, result, tool.read, images=bool(context.pictures))
            ok = not (isinstance(result, dict) and (result.get("error") or isinstance(result.get('status'), int) and result['status'] >= 400))
            for source in evidence:
                text = source.pop("_jev_text", "")
                truncated = source.pop("_jev_truncated", False)
                if ok and not session.get("tools_disabled") and "_jev_sources" in session:
                    observed = session["_jev_sources"]
                    if len(observed) < 12:
                        observed.append({**source, "text": text, "truncated": truncated})
                    else:
                        session["_jev_omitted"] = True
            event = {"tool": name, "label": label, "ok": ok}
            if name == "open_page" and isinstance(result, dict):
                event["link"] = result
            if isinstance(result, dict) and result.get("action_id"):
                event["action"] = {k: result.get(k) for k in ("action_id", "outcome") if result.get(k)}
            if evidence:
                event["evidence"] = evidence
                if isinstance(result, dict):
                    result = {**result, "_evidence": evidence}
            if context.files:
                event["files"] = context.files
            if context.pictures:
                event["pictures"] = context.pictures
            if name == "fetch_files" and isinstance(result, dict):
                event["ok"] = not any("error" in entry for entry in result.get("files", []))
            source = tool.source(args) if tool.source else None
            return render(result, source=source, composed=tool.source is None), event
        except (ValueError, KeyError, TypeError) as error:
            message = str(error) if isinstance(error, ValueError) else f"Missing or wrong argument: {error}"
            if session.get('_tool_guidance') and name != 'tool_use_advice':
                try:
                    session['_tool_guidance'].after_tool(name, args, {'error': message}, tool.read)
                except Exception:
                    session['_tool_guidance'].failed = True
                    self.app.logger.warning('Assistant tool-use advice could not record a failed tool.')
            return json.dumps({"error": message}), {"tool": name, "label": label, "ok": False}
        except HTTPException as error:
            # Owner guards (workflows.guard, followups.guard) answer an expected refusal with instructor-readable text: the model
            # gets that text, and no traceback is logged.
            message = error.description or type(error).__name__
            if session.get('_tool_guidance') and name != 'tool_use_advice':
                try:
                    session['_tool_guidance'].after_tool(name, args, {'error': message}, tool.read)
                except Exception:
                    session['_tool_guidance'].failed = True
            return json.dumps({"error": message}), {"tool": name, "label": label, "ok": False}
        except Exception as error:  # a tool must never take the conversation down
            self.app.logger.exception("Assistant tool %s failed", name)
            return json.dumps({"error": f"The tool failed: {type(error).__name__}."}), {"tool": name, "label": label, "ok": False}

    def source_files(self):
        if self._sources is None:
            root = toolkit.ROOT
            try:
                listed = subprocess.run(["git", "ls-files"], cwd=root, capture_output=True, text=True, timeout=20, check=True).stdout.splitlines()
            except (OSError, subprocess.SubprocessError):
                listed = [str(p.relative_to(root)) for p in root.rglob("*") if p.is_file() and ".git" not in p.parts]
            # Never through a symbolic link (2026-10-09 review): a link could reach a credentials file or the live data.
            self._sources = sorted(name for name in listed if Path(name).suffix in toolkit.SOURCE_SUFFIXES and toolkit.plain_source(root, name))
        return self._sources

    def route_catalog(self, words=""):
        words = [w.lower() for w in str(words or "").split()]
        routes = []
        for rule in self.app.url_map.iter_rules():
            if not rule.rule.startswith("/api/"):
                continue
            text = f"{rule.rule} {rule.endpoint}".lower()
            if words and not all(w in text for w in words):
                continue
            for method in sorted(set(rule.methods) - {"HEAD", "OPTIONS"}):
                if method == "GET":
                    # "file": served files read only through fetch_files (shown to the instructor, or looked at).
                    tier = "never" if rule.endpoint in policy.FILE_BLOCKED else "file" if rule.endpoint in policy.GET_BLOCKED else "read"
                    routes.append({"method": method, "path": rule.rule, "endpoint": rule.endpoint, "assistant": tier})
                    continue
                found = policy.base_rule(rule.endpoint, {"operation": "retry"} if "<operation>" in rule.rule else {})
                entry = {"method": method, "path": rule.rule, "endpoint": rule.endpoint, "assistant": found.tier}
                if found.category:
                    entry["category"] = found.category
                if found.preview:
                    entry["preview"] = found.preview
                if found.reason and found.tier in ("never", "page"):
                    entry["why"] = found.reason
                routes.append(entry)
        routes.sort(key=lambda r: (r["path"], r["method"]))
        return {"routes": routes, "tiers": {"read": "runs at once (a route answering with a file: fetch it with fetch_files)",
                                            "file": "a file: fetch_files only (shown to the instructor, or looked at)",
                                            "auto": "made at once (instructor's chosen categories)",
                                            "approve": "Approve card for the instructor", "page": "open the page instead",
                                            "never": "not available to the assistant"}}

    # Writes -----------------------------------------------------------------------------------------------------------------

    def write(self, session, method, path, body, summary, page=None, assumption=None):
        # A follow-up preparation's writes are serialized with Approve under `_approve_lock` (taken inside `_write`, after the
        # turn's guidance lock: only the conversation's own thread takes that one).
        guidance = session.get('_tool_guidance')
        with guidance.lock if guidance else contextlib.nullcontext():
            if guidance:
                prior = guidance.prior_write(method, path, body)
                if prior:
                    return prior
            result = self._write(session, method, path, body, summary, page, assumption)
            if guidance:
                guidance.record_write(method, path, body, result)
            return result

    def _write(self, session, method, path, body, summary, page=None, assumption=None):
        """A follow-up preparation's validation, duplicate check, guard and recording run under `_approve_lock`; the advisory
        tool-use preflight (a network call) runs between those two locked steps, so Approve never waits behind it (2026-10-09
        review). Re-entrant: callers that already hold the lock keep it throughout."""
        serial = self._approve_lock if session.get("_followup_run") else contextlib.nullcontext()
        with serial:
            answered, checked = self._check_write(session, method, path, body, summary, assumption)
        if answered is not None:  # refused, read, or already recorded
            return answered
        endpoint, rule, body = checked['endpoint'], checked['rule'], checked['body']
        guidance = session.get('_tool_guidance')
        advice = {}
        # ASSISTANT "CONTINUATIONS": scheduling a preparation's next stage is never a teaching action; code validates it.
        if guidance and endpoint != 'assistant.preparation_continue':
            advice = guidance.before_write(endpoint, checked['method'], checked['path'], body, automatic=rule.tier == 'auto',
                                           demotes=not (rule.tier == 'auto' and rule.result_waits))
        with serial:
            if session.get("_followup_run") and guidance:
                # The lock was released for the preflight: an exact proposal recorded meanwhile is not made twice.
                duplicate = self.followups.duplicate(session, endpoint, checked['path'], body)
                if duplicate:
                    return duplicate
            return self._record_write(session, checked, advice, page, assumption)

    def _check_write(self, session, method, path, body, summary, assumption):
        """(an answer that ends the write, None) or (None, what `_record_write` needs)."""
        if session.get('_capture_analysis_only'):
            raise ValueError('ASSISTANT: the initial selected-area analysis cannot make or propose changes.')
        method = str(method or "").upper()
        if method not in ("POST", "PUT", "PATCH", "DELETE"):
            raise ValueError("Writes use POST, PUT, PATCH or DELETE; read with faculty_get.")
        if not isinstance(body, dict):
            raise ValueError("The body must be a JSON object.")
        if len(json.dumps(body, default=str)) > MAX_BODY:
            raise ValueError("The body is too large.")
        summary = " ".join(str(summary or "").split())[:2000]
        if not summary:
            raise ValueError("Say in summary what this change does and why.")
        dropped = "confirmation" in body
        body = _substitute({k: v for k, v in body.items() if k != "confirmation"}, str(uuid.uuid4()))
        path = str(path or "")
        endpoint, args = self.transport.match(method, path.split("?")[0])
        rule = policy.classify(endpoint, method, args, body, self.context)
        if endpoint in ('assistant.preparation_create', 'student_support.suggestion_redo') and (session.get('_workflow_run') or session.get('_followup_run')):
            raise ValueError('ASSISTANT: background work cannot start another preparation.')
        if endpoint == 'assistant.preparation_continue' and args.get('ident') != session.get('_workflow_run'):
            raise ValueError('ASSISTANT: only the current preparation can schedule its next stage.')
        if endpoint == 'assistant.preparation_continue':
            # Checked before the step is recorded: a refused continuation must not stay behind as a failed action that blocks
            # this stage's approvals and its rule (workflows.teaching).
            try:
                self.workflows.validate_continuation(args['ident'], body)
            except Conflict as error:
                raise ValueError(error.description) from None
        if rule.request_id and not _is_uuid(body.get("request_id")):
            body["request_id"] = str(uuid.uuid4())  # policy.REQUEST_IDS: refused without one, and only at the instructor's Approve
        unlinked = endpoint == "student_support.create" and body.get("kind") == "appointment" and "source_key" in body
        if unlinked:
            del body["source_key"]  # APPOINTMENTS ARE NOT CONVERSATIONS: the service refuses one, and only at the instructor's Approve
        unticked = endpoint in ("canvas.message_send", "canvas.message_schedule") and body.get("individual") is True and not self._asked_individual(session)
        if unticked:
            body["individual"] = False  # GROUP BY DEFAULT (INDIVIDUAL_ASKED): the review and the card are of the group send
        # A continuation is scheduling, never a teaching action: no proposal, review or corrective-pass demotion. NO CARD FOR A
        # REDO: a corrective pass still finishing what the instructor asked makes no card of a request whose result waits anyway.
        if rule.tier == 'auto' and endpoint != 'assistant.preparation_continue' and (session.get('_proposal_only') or
                session.get('_review_only') or (session.get('_tool_followthrough') and not rule.result_waits)):
            rule = replace(rule, tier="approve", reason="This review prepares changes for your approval." if session.get('_review_only') or session.get('_tool_followthrough')
                           else "Every background preparation waits for your approval.")
        if session.get('_workflow_run') and rule.tier in ('auto', 'approve'):
            duplicate = self.workflows.check_write(session['_workflow_run'], endpoint, path, body)
            if duplicate:
                return duplicate, None
        if session.get("_followup_run"):
            rule = self.followups.validate_write(session, endpoint, args, body, rule, assumed=bool(_assumption(assumption)))
            if rule.tier in ("approve", "auto"):  # DONE FOR YOU: an automatic record is never made twice either
                duplicate = self.followups.duplicate(session, endpoint, path, body)
                if duplicate:
                    return duplicate, None
        note = " ".join(filter(None, ("The confirmation field was removed: only the instructor confirms." if dropped else "",
                                      INDIVIDUAL_REMOVED if unticked else "", APPOINTMENT_SOURCE_REMOVED if unlinked else "")))
        if rule.tier == "never":
            return {"outcome": "refused", "error": f"The assistant cannot use this route ({rule.reason})."}, None
        if rule.tier == "page":
            return {"outcome": "use_the_page", "error": rule.reason + " Use open_page to take the instructor there."}, None
        if rule.tier == "read":
            status, data, file_note = self.transport.request(method, path, body)
            return {"outcome": "done" if status < 400 else "failed", "status": status, "result": data, "note": file_note or note}, None
        return None, {'endpoint': endpoint, 'args': args, 'rule': rule, 'method': method, 'path': path, 'body': body,
                'summary': summary, 'note': note}

    def _asked_individual(self, session):
        """Whether the instructor's own latest messages in this conversation ask for individual Canvas messages (INDIVIDUAL_ASKED).
        A session with no conversation, a follow-up or a preparation has none of their words: the message goes as a group."""
        conversation = self.conversations.by_session(session["id"]) if self.conversations is not None else None
        from .conversations import typed
        said = [typed(text) for who, text in list(getattr(conversation, "transcript", ())) if who == "Instructor"][-3:]
        return any(INDIVIDUAL_ASKED.search(text) for text in said)

    def _record_write(self, session, checked, advice, page=None, assumption=None):
        endpoint, args, rule, method, path, body, summary, note = (checked[k] for k in (
            'endpoint', 'args', 'rule', 'method', 'path', 'body', 'summary', 'note'))
        if advice:
            if advice.get('hold'):
                return {'outcome': 'needs_context', 'error': 'ASSISTANT: ' + advice['detail'],
                        'suggested_tools': advice.get('suggested_tools', [])}
            if advice.get('review') and rule.tier == 'auto':
                rule = replace(rule, tier='approve', reason=advice['detail'])
        page = page if isinstance(page, str) and page.startswith("#") and len(page) <= 500 else None
        if session.get("_followup_run"):
            page = "#follow-ups"
        preview = self._preview(rule, endpoint, args, body) if rule.tier == "approve" else None
        if preview and preview["status"] >= 400:
            # The page's own preview refused it: the request needs fixing (or something is not connected). No card is made.
            return {"outcome": "refused_by_preview", "status": preview["status"], "error": preview.get("error"),
                    "note": "Nothing was prepared. Fix the request, or tell the instructor what is missing."}
        if session.get("_followup_run"):
            self.followups.guard(session["_followup_run"])
        if session.get('_workflow_run'):
            self.workflows.guard(session['_workflow_run'])  # only as a card is recorded, never for reads (2026-10-09 review)
        action_id = self._record_action(session, rule.tier, endpoint, method, path, body, summary)
        from .followups import AUTOMATIC_LATER
        if rule.tier == "auto" and rule.category == AUTOMATIC_LATER:
            # DONE FOR YOU: the follow-up's own status runs after its round's reply is sent (followups.run_deferred), never now.
            self._event(action_id, "proposed", "assistant", {"deferred": True, "page": page, "reason": rule.reason})
            return {"outcome": "scheduled", "action_id": action_id, "note": rule.reason}
        if rule.tier == "auto":
            status, data, file_note = self.transport.request(method, path, body)
            ok = status < 400
            self._event(action_id, "succeeded" if ok else "failed", "assistant",
                        {"status": status, "category": rule.category, "result": self._brief(data), "page": page})
            return {"outcome": "done" if ok else "failed", "action_id": action_id, "status": status,
                    "category": rule.category, "result": data, "note": note}
        # An instructor's own edit of a follow-up card is recorded as theirs (EDIT IN PLACE, assistant/followups.py edit).
        self._event(action_id, "proposed", session.get("_actor", "assistant"), {"preview": preview, "page": page, "reason": rule.reason,
                                                         "confirmation": rule.confirmation,
                                                         **({"assumption": assumed} if (assumed := _assumption(assumption)) else {})})
        if self.conversations is not None:
            conversation = self.conversations.by_session(session["id"])
            if conversation is not None:
                conversation.wait_for_action(action_id)
        answer = {"outcome": "waiting_for_approval", "action_id": action_id,
                  "note": "Nothing has been done yet. The instructor sees an Approve card; tell them what it does and wait." +
                  (" " + note if note else "")}
        if rule.reason:
            answer["why_approval"] = rule.reason
        if preview:
            answer["preview"] = {"status": preview["status"], "result": preview["data"], "error": preview.get("error")}
        return answer

    def batch(self, session, action_ids, title, made=False):
        """Group existing exact requests; grouping grants no approval (ASSISTANT-BATCHES). Up to BATCH_LIMIT different changes, or
        up to SAME_CHANGE_LIMIT cards of one route (ONE CARD FOR ONE CHANGE). A card already in a group moves to this one, so groups
        made in parts can be joined into one. `made` (ASSISTANT "SEVERAL AT ONCE"): the receipts one tool call made itself, all
        of one route, are grouped whatever their state (done at once, refused, or waiting), so the chat draws one card for them.
        The model's `group_actions` never passes it."""
        if not isinstance(action_ids, list) or not all(isinstance(a, str) for a in action_ids) \
                or not 2 <= len(action_ids) <= SAME_CHANGE_LIMIT or len(set(action_ids)) != len(action_ids):
            raise ValueError(f"Group 2 to {SAME_CHANGE_LIMIT} different pending action IDs "
                             f"(up to {BATCH_LIMIT} when they are different kinds of change).")
        title = str(title or "Related changes").strip()[:200]
        marks = ",".join("?" * len(action_ids))
        with self._approve_lock, self.rosters.lock(), connect(self.database) as db:
            rows = {r["id"]: r for r in db.execute(f"""SELECT a.id,a.session_id,a.tier,a.endpoint,
                COALESCE({_latest_kind('a')},'proposed') AS state
                FROM assistant_actions a WHERE a.id IN ({marks})""", action_ids)}
            for aid in action_ids:
                row = rows.get(aid)
                if not row or row["session_id"] != session["id"] or not made and (row["tier"] != "approve" or row["state"] not in PENDING):
                    raise ValueError("Group only pending actions from this conversation.")
            same = len({row["endpoint"] for row in rows.values()}) == 1
            if made and not same:
                raise ValueError("Receipts grouped as made are of one route.")
            if not same and len(action_ids) > BATCH_LIMIT:
                raise ValueError(f"Group up to {BATCH_LIMIT} different kinds of change, or up to {SAME_CHANGE_LIMIT} cards of one "
                                 "route (the same change for each record).")
            ident = uuid.uuid4().hex
            db.execute(f"DELETE FROM assistant_batch_members WHERE action_id IN ({marks})", action_ids)
            db.execute("INSERT INTO assistant_action_batches VALUES(?,?,?,?)", (ident, session["id"], title, timestamp()))
            db.executemany("INSERT INTO assistant_batch_members VALUES(?,?,?)", [(ident, aid, pos) for pos, aid in enumerate(action_ids)])
        return {"batch_id": ident, "title": title, "action_ids": action_ids, "outcome": "waiting_for_approval",
                "note": "The instructor sees one summary card with Approve all." if same else "The instructor reviews each card of the group."}

    def batches(self):
        """Groups with a card still waiting, and those made in the last day (a finished one-route group is still drawn as one card
        in the chat). `route`: the endpoint every card shares (ONE CARD FOR ONE CHANGE), else None. `states`: cards by latest state."""
        cutoff = (_now() - EXPIRY).isoformat()
        with connect(self.database) as db:
            rows = db.execute(f"""SELECT b.id,b.title,m.action_id,a.endpoint,
                  COALESCE({_latest_kind('a')},'proposed') AS state
                FROM assistant_action_batches b JOIN assistant_batch_members m ON m.batch_id=b.id
                JOIN assistant_actions a ON a.id=m.action_id
                WHERE b.created_at >= ? OR EXISTS(SELECT 1 FROM assistant_batch_members p JOIN assistant_actions w ON w.id=p.action_id
                  WHERE p.batch_id=b.id AND {_latest_kind('w')} IN {PENDING_SQL}) ORDER BY b.created_at,m.position""", (cutoff,)).fetchall()
        batches, routes = {}, {}
        for row in rows:
            batch = batches.setdefault(row["id"], {"id": row["id"], "title": row["title"], "action_ids": [], "states": {}, "pending": 0})
            batch["action_ids"].append(row["action_id"])
            batch["states"][row["state"]] = batch["states"].get(row["state"], 0) + 1
            batch["pending"] += row["state"] in PENDING
            routes.setdefault(row["id"], set()).add(row["endpoint"])
        for ident, batch in batches.items():
            batch["route"] = next(iter(routes[ident])) if len(routes[ident]) == 1 else None
        return list(batches.values())

    def decline_batch(self, ident):
        """Decline all on a group's card: every card of it still waiting is declined; finished ones keep their receipts."""
        with self._approve_lock:
            with connect(self.database) as db:
                members = [r[0] for r in db.execute("SELECT action_id FROM assistant_batch_members WHERE batch_id=? ORDER BY position", (ident,))]
            if not members:
                raise NotFound("That group of changes was not found.")
            declined = []
            for aid in members:
                row, events = self.action(aid)
                if row["tier"] == "approve" and events and events[-1]["kind"] in PENDING:
                    self._event(aid, "declined", "instructor", {"batch": ident})
                    declined.append(aid)
        count = len(declined)
        return {"declined": declined, "message": f"Declined {count} change{'s' if count != 1 else ''}." if count
                else "Nothing in this group was still waiting."}

    @contextlib.contextmanager
    def _approving(self):
        """Counts an Approve while it runs, preview and Faculty's own route included: a restart in between (03:28:57Z, 2026-10-28,
        a regrade decision's approval) cut it before anything was recorded, and the card stayed "Approving" in the page."""
        with self._approvals_lock:
            self._approvals += 1
        try:
            yield
        finally:
            with self._approvals_lock:
                self._approvals -= 1

    def approving(self):
        """How many Approve clicks are being carried out now (`GET /api/assistant/restart-check` names them "approvals")."""
        return self._approvals

    def approve_batch(self, ident, seen):
        """Preflight the entire selection, then execute in order; stop on the first non-success. Never roll back or retry."""
        if not isinstance(seen, dict) or not seen or any(type(v) is not int for v in seen.values()):
            raise ValueError("Name each selected card and the version reviewed.")
        # The members are fixed when the batch is made; reading them needs no lock, and an approval takes its lanes first.
        with connect(self.database) as db:
            members = [r[0] for r in db.execute("SELECT action_id FROM assistant_batch_members WHERE batch_id=? ORDER BY position", (ident,))]
        if not members or set(seen) - set(members):
            raise ValueError("Select cards from this batch.")
        return self.approve_selected([aid for aid in members if aid in seen], seen)

    def _unchanged(self, action_id, seen):
        """Under the approval lock: the card still waits as the instructor saw it (nothing declined, expired, replaced or approved
        it while its preview ran outside the lock). Its row and events."""
        row, events = self.action(action_id)
        if row["tier"] != "approve" or not events or events[-1]["kind"] not in PENDING:
            raise Conflict("This card is no longer waiting.")
        if seen != events[-1]["id"]:
            raise Conflict("This card changed since you looked at it. Look again, then approve.")
        return row, events

    def _previewed(self, action_id, seen, detail):
        """A new preview recorded on a card only while it still waits as it was (a Decline meanwhile is never undone by it)."""
        with self._approve_lock:
            self._unchanged(action_id, seen)
            self._event(action_id, "previewed", "faculty", detail)

    def preview_again(self, action_id):
        """A waiting card's preview read again; when what it would do changed, the card shows the new one, as its Approve would have
        (ABSENCES "DEPARTMENT MAKEUP EXAMS" › THE TERM'S RULES). Submits nothing. True when the card changed."""
        row, events = self.action(action_id)
        view = self._view(row, events)
        endpoint, args = self.transport.match(row["method"], row["path"].split("?")[0])
        rule = policy.base_rule(endpoint, args)
        if not view["pending"] or endpoint != row["endpoint"] or not rule.preview:
            return False
        preview = self._preview(rule, endpoint, args, json.loads(row["body_json"]))
        if not preview or preview["status"] >= 400 or not preview["fingerprint"] \
                or preview["fingerprint"] == (view["preview"] or {}).get("fingerprint"):
            return False
        try:
            self._previewed(action_id, view["seen"], {"preview": preview, "page": view["page"]})
        except Conflict:  # approved, declined or expired meanwhile
            return False
        return True

    def approve_selected(self, selected, seen):
        """The instructor's reviewed cards, in this order (a batch, or one follow-up's Approve all): every card preflighted first,
        then each submitted, stopping at the first that does not succeed. A Mail send whose delivery is not confirmed stops it too,
        so nothing after an uncertain send goes out. Never rolled back or retried. APPROVALS SIDE BY SIDE: the cards' lanes are
        held throughout; the approval lock only for the checks, never for the previews (network reads)."""
        if not isinstance(seen, dict) or not seen or any(type(v) is not int for v in seen.values()):
            raise ValueError("Name each selected card and the version reviewed.")
        with self._approving(), self.lanes.hold(self.lanes.of(self.database, selected)), self.workflows.approving():
            checked = []
            with self._approve_lock:
                # ALREADY APPROVED (requirement, 2026-10-06): cards that already went through are named as such. All of them: nothing to do. Some: nothing is submitted
                # and the rest are reviewed again, as for any other change.
                done = [aid for aid in selected if _went_through(self._view(*self.action(aid)))]
                if done and len(done) == len(selected):
                    return {"results": [], "remaining": [], "stale": False, "already": done,
                            "message": "Already approved. Nothing was submitted again."}
                if done:
                    raise Conflict(f"{len(done)} of these steps {'was' if len(done) == 1 else 'were'} already approved. "
                                   "Review the steps that still wait; nothing was submitted.")
                for aid in selected:
                    row, events = self.action(aid)
                    view = self._view(row, events)
                    if not view["pending"] or seen[aid] != view["seen"] or row["created_at"] < (_now() - EXPIRY).isoformat():
                        raise Conflict("A selected card changed or expired. Review the batch again; nothing was submitted.")
                    if self.followups:
                        self.followups.guard_action(aid)
                    self.workflows.guard_action(aid)
                    endpoint, args = self.transport.match(row["method"], row["path"].split("?")[0])
                    rule = policy.base_rule(endpoint, args)
                    if endpoint != row["endpoint"] or rule.tier not in ("approve", "auto"):
                        raise Conflict("A selected route changed. Nothing was submitted.")
                    checked.append((aid, row, view, endpoint, args, rule))
            stale = []
            for aid, row, view, endpoint, args, rule in checked:
                if rule.preview:
                    preview = self._preview(rule, endpoint, args, json.loads(row["body_json"]))
                    shown = view["preview"]
                    if not preview or preview["status"] >= 400 or not preview["fingerprint"] or not shown or preview["fingerprint"] != shown["fingerprint"]:
                        try:
                            self._previewed(aid, seen[aid], {"preview": preview, "page": view["page"]})
                        except Conflict:
                            raise Conflict("A selected card changed or expired. Review the batch again; nothing was submitted.") from None
                        stale.append(aid)
            if stale:
                return {"stale": True, "changed": stale, "results": [], "message": "Review the updated cards. Nothing was submitted."}
            # ASSISTANT-FOLLOWUP-SUGGESTIONS: previews may take network time; recheck every source before the first submission.
            if self.followups:
                with self._approve_lock:
                    self.followups.guard_selection(selected)
                    for aid in selected:
                        self.followups.guard_action(aid)
            results = []
            for aid in selected:
                try:
                    result = self.approve(aid, seen[aid])
                except Exception as error:
                    result = {"error": str(error), "action": self._view(*self.action(aid))}
                results.append({"id": aid, **result})
                if result.get("stale") or result.get("error") or result.get("status", 500) >= 400:
                    break
                if (result.get("action") or {}).get("endpoint") == "gmail_mail.send" and (result.get("result") or {}).get("status") != "accepted":
                    break
        return {"results": results, "remaining": selected[len(results):], "stale": False,
                "message": "Each submitted request has its own receipt. Unsubmitted cards still wait for review."}

    def _preview(self, rule, endpoint, view_args, body):
        """The page's own preview of this write (writes nothing), or None when the route has none."""
        if not rule.preview:
            return None
        preview_rule = next(iter(self.app.url_map.iter_rules(rule.preview)), None)
        if preview_rule is None:
            return None
        values = {k: v for k, v in view_args.items() if k in preview_rule.arguments}
        sent = {k: v for k, v in body.items() if k not in ("request_id", "confirmation", rule.fingerprint[1])}
        if rule.preview_method == "GET":
            query = {k: (str(v).lower() if isinstance(v, bool) else v) for k, v in sent.items() if isinstance(v, (str, int, float, bool))}
            status, data, note = self.transport.request("GET", self.transport.build(rule.preview, values, query))
        else:
            status, data, note = self.transport.request("POST", self.transport.build(rule.preview, values), sent)
        found = data.get(rule.fingerprint[0]) if isinstance(data, dict) else None
        shown = shrink(clean(data)) if data is not None else None
        if endpoint in ("gmail_mail.send", "gmail_mail.schedule") and isinstance(data, dict) and isinstance(data.get("message"), dict):
            # MAIL / ASSISTANT: the instructor reviews the complete message, not the tool response's string excerpt.
            shown["message"] = clean(data["message"])
        if endpoint in ("canvas.message_send", "canvas.message_schedule") and isinstance(data, dict) and isinstance(data.get("body"), str):
            # CANVAS MESSAGE CARD: the card draws the message as it would go, so its words are never the excerpt.
            shown["body"] = data["body"]
        if endpoint == "canvas.announcement_post" and isinstance(data, dict) and isinstance(data.get("message"), str):
            # ANNOUNCEMENTS: the card shows the announcement as it would be posted, never an excerpt of it.
            shown["message"] = data["message"]
        return {"status": status, "data": shown, "fingerprint": found,
                "error": (data or {}).get("error") if status >= 400 and isinstance(data, dict) else (note or None)}

    def _record_action(self, session, tier, endpoint, method, path, body, summary):
        action_id = uuid.uuid4().hex
        with self.rosters.lock(), connect(self.database) as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("INSERT INTO assistant_actions VALUES(?,?,?,?,?,?,?,?,?,?)",
                       (action_id, session["id"], session["engine"], tier, endpoint, method, path,
                        json.dumps(body, ensure_ascii=False, default=str), summary, timestamp()))
            if session.get("_followup_run"):
                self.followups.add_action(db, session, action_id, endpoint, path, body)
            if session.get('_workflow_run'):
                self.workflows.recording(db, session['_workflow_run'])  # a Stop recorded first refuses it
                db.execute('INSERT INTO assistant_preparation_actions VALUES(?,?)', (session['_workflow_run'], action_id))
            db.commit()
        return action_id

    def _event(self, action_id, kind, actor, detail):
        with connect(self.database) as db:
            db.execute("BEGIN IMMEDIATE")
            cursor = db.execute("INSERT INTO assistant_action_events(action_id,kind,actor,detail_json,created_at) VALUES(?,?,?,?,?)",
                                (action_id, kind, actor, json.dumps(detail, ensure_ascii=False, default=str), timestamp()))
            db.commit()
            return cursor.lastrowid

    @staticmethod
    def _brief(data):
        data = clean(data)
        text = json.dumps(data, ensure_ascii=False, default=str)
        # A result too long to keep whole still names what its receipts are found by (`outcomes.link`).
        return json.loads(text) if len(text) <= 4000 else {"summary": text[:4000] + "...", **link(data)}

    # The instructor's side ---------------------------------------------------------------------------------------------------

    def actions(self, limit=50, pending_only=False, before=None):
        """Approve cards still waiting (expiring those older than a day) and the recent log."""
        self._expire()
        with connect(self.database) as db:
            # Waiting cards are independent of history pagination (ASSISTANT-APPROVALS).
            waiting = db.execute(f"""SELECT a.rowid AS position,a.* FROM assistant_actions a WHERE a.tier='approve'
                AND {_latest_kind('a')} IN {PENDING_SQL} ORDER BY a.rowid DESC""").fetchall()
            limit = min(200, max(1, limit))
            history = [] if pending_only else db.execute(f"""SELECT a.rowid AS position,a.* FROM assistant_actions a
                WHERE (? IS NULL OR a.rowid < ?) AND NOT (a.tier='approve' AND COALESCE({_latest_kind('a')},'')
                IN {PENDING_SQL}) ORDER BY a.rowid DESC LIMIT ?""", (before, before, limit + 1)).fetchall()
            more = len(history) > limit
            history = history[:limit]
            rows = [*waiting, *history]
            events = {}
            if rows:
                marks = ",".join("?" * len(rows))
                for event in db.execute(f"SELECT * FROM assistant_action_events WHERE action_id IN ({marks}) ORDER BY id",
                                        [r["id"] for r in rows]):
                    events.setdefault(event["action_id"], []).append(event)
        return {"pending": [self._view(row, events.get(row["id"], [])) for row in waiting],
                "recent": [self._view(row, events.get(row["id"], [])) for row in history],
                "next_before": history[-1]["position"] if more else None}

    def action(self, action_id):
        with connect(self.database) as db:
            row = db.execute("SELECT * FROM assistant_actions WHERE id=?", (action_id,)).fetchone()
            if not row:
                raise NotFound("That assistant action was not found.")
            events = db.execute("SELECT * FROM assistant_action_events WHERE action_id=? ORDER BY id", (action_id,)).fetchall()
        return row, events

    def _view(self, row, events):
        latest = events[-1] if events else None
        preview, page, reason, confirmation, assumption = None, None, "", None, None
        for event in events:
            detail = json.loads(event["detail_json"])
            if event["kind"] in PENDING:
                preview = detail.get("preview")
                page = detail.get("page", page)
                reason = detail.get("reason", reason) or reason
                confirmation = detail.get("confirmation", confirmation)
                assumption = detail.get("assumption", assumption)
            elif event["kind"] in ("succeeded", "failed") and row["tier"] == "auto":
                page = detail.get("page", page)
        status = latest["kind"] if latest else "proposed"
        recorded = Outcomes().read(row['endpoint'], status, latest['detail_json'] if latest else None)
        outcome = recorded
        # A later local receipt can settle or unsettle what the event recorded; an accepted send is final and is not read again.
        if status == 'succeeded' and row['endpoint'] in RECEIPTS and not settled(row['endpoint'], recorded):
            with connect(self.database) as db:
                outcome = Outcomes(self, db).read(row['endpoint'], status, latest['detail_json'])
        return {"id": row["id"], "tier": row["tier"], "endpoint": row["endpoint"], "method": row["method"], "path": row["path"],
                "body": json.loads(row["body_json"]), "summary": row["summary"], "engine": row["engine"],
                "created_at": row["created_at"], "status": status, "pending": row["tier"] == "approve" and status in PENDING,
                "seen": latest["id"] if latest else None, "preview": preview, "page": page, "reason": reason,
                "confirmation": confirmation, **({"assumption": assumption} if assumption else {}),
                **({'outcome': outcome} if outcome != status or outcome != recorded else {}),
                "events": [{"id": e["id"], "kind": e["kind"], "actor": e["actor"], "at": e["created_at"],
                            "detail": json.loads(e["detail_json"]) if e["kind"] in ("succeeded", "failed", "declined", "expired") else None}
                           for e in events]}

    def _expire(self):
        cutoff = (_now() - EXPIRY).isoformat()
        query = f"""SELECT a.id FROM assistant_actions a WHERE a.tier='approve' AND a.created_at < ?
            AND {_latest_kind('a')} IN {PENDING_SQL}"""
        with connect(self.database) as db:
            if not db.execute(query, (cutoff,)).fetchone():
                return  # nothing to expire: a poll never waits for an Approve that is sending
        # The pending check and its expiry receipt must serialize with Approve and Decline, so a completed choice cannot
        # acquire a later expiry event (ASSISTANT-APPROVALS); the check is repeated under the lock.
        with self._approve_lock:
            with connect(self.database) as db:
                stale = db.execute(query, (cutoff,)).fetchall()
            for row in stale:
                self._event(row["id"], "expired", "faculty", {"after_hours": int(EXPIRY.total_seconds() // 3600)})

    def decline(self, action_id):
        with self._approve_lock:
            row, events = self.action(action_id)
            if row["tier"] != "approve" or not events or events[-1]["kind"] not in PENDING:
                raise Conflict("This card is no longer waiting.")
            self._event(action_id, "declined", "instructor", {})
        return self._view(*self.action(action_id))

    def approve(self, action_id, seen):
        """The instructor's click. Runs the page's preview again: when it changed, the card shows the new one and waits for another
        click. Otherwise submits the ordinary route with the preview's fingerprint and the confirmation, and records the outcome.
        APPROVALS SIDE BY SIDE (assistant/lanes.py): the card's lanes are held throughout; the approval lock only while the card is
        checked and while "approved" is recorded (rechecked there, so a Decline during the preview stands), never during the
        preview, the submission or what follows it, so another student's follow-up is approved meanwhile."""
        began = time.monotonic()
        spent = {}
        with self._approving(), self.lanes.hold(self.lanes.of(self.database, [action_id])), self.workflows.approving():
            spent["waited"] = time.monotonic() - began
            with self._approve_lock:
                row, events = self._unchanged(action_id, seen)
                if self.followups:
                    self.followups.guard_action(action_id)
                self.workflows.guard_action(action_id)
                if row["created_at"] < (_now() - EXPIRY).isoformat():
                    self._event(action_id, "expired", "faculty", {"after_hours": int(EXPIRY.total_seconds() // 3600)})
                    raise Conflict("This card expired. Ask the assistant again.")
                method, path, body = row["method"], row["path"], json.loads(row["body_json"])
                endpoint, args = self.transport.match(method, path.split("?")[0])
                if endpoint != row["endpoint"]:
                    raise Conflict("This route changed since the card was made. Ask the assistant again.")
                rule = policy.base_rule(endpoint, args)
                if rule.tier not in ("approve", "auto"):
                    raise Conflict("The assistant may no longer prepare this change.")
            shown = json.loads(events[-1]["detail_json"]).get("preview")
            mark = time.monotonic()
            if rule.preview:
                preview = self._preview(rule, endpoint, args, body)
                if preview["status"] >= 400 or preview["fingerprint"] is None:
                    self._previewed(action_id, seen, {"preview": preview, "page": self._page(events)})
                    return {"stale": True, "action": self._view(*self.action(action_id)),
                            "message": "The preview did not come back: " + str(preview.get("error") or preview["status"])}
                if not shown or preview["fingerprint"] != shown.get("fingerprint"):
                    self._previewed(action_id, seen, {"preview": preview, "page": self._page(events)})
                    return {"stale": True, "action": self._view(*self.action(action_id)),
                            "message": "What this would do changed since the card was made. Look at it again."}
                body[rule.fingerprint[1]] = preview["fingerprint"]
            if rule.confirmation:
                body["confirmation"] = rule.confirmation
            spent["preview"] = time.monotonic() - mark
            mark = time.monotonic()
            with self._approve_lock:
                self._unchanged(action_id, seen)
                if self.followups:
                    self.followups.guard_action(action_id, submitting=True)
                self.workflows.guard_action(action_id, submitting=True)
                self._event(action_id, "approved", "instructor", {"sent_sha256": _digest(body)})
            spent["checked"] = time.monotonic() - mark
            mark = time.monotonic()
            try:
                status, data, note = self.transport.request(method, path, body)
            except Exception as error:  # recorded, never retried by itself
                self.app.logger.exception("Approved assistant action %s failed", action_id)
                self._event(action_id, "failed", "faculty", {"error": f"{type(error).__name__}"})
                raise
            self._event(action_id, "succeeded" if status < 400 else "failed", "faculty",
                        {"status": status, "result": self._brief(data), "note": note or None})
            spent["submitted"] = time.monotonic() - mark
            mark = time.monotonic()
            try:
                self.workflows.after_action(action_id)
            except Exception:
                self.app.logger.exception('Preparation completion could not be observed')
            if self.followups:
                try:  # PLAN COMPLETION: scheduled tracking waits for every preceding step.
                    self.followups.after_reply(action_id)
                except Exception:
                    self.app.logger.exception("Scheduled follow-up records after %s could not run", action_id)
            spent["after"] = time.monotonic() - mark
        if time.monotonic() - began > SLOW_APPROVAL:
            # Said in the log only when slow: where an approval's seconds went, for the next "it takes a long time".
            self.app.logger.warning("Approving %s (%s) took %.1f s: %s", action_id[:8], row["endpoint"], time.monotonic() - began,
                                    ", ".join(f"{k} {v:.1f} s" for k, v in spent.items()))
        return {"stale": False, "action": self._view(*self.action(action_id)), "status": status, "result": data}

    @staticmethod
    def _page(events):
        for event in reversed(events):
            detail = json.loads(event["detail_json"])
            if detail.get("page"):
                return detail["page"]
        return None
