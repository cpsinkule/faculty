"""Transport adapters. All accepted commands enter InboxService.submit()."""
import base64
import binascii
import io
import json
import hashlib
from datetime import datetime

import qrcode
import qrcode.image.svg
from flask import Blueprint, g, jsonify, request, send_file
from werkzeug.exceptions import Conflict, Forbidden

from rosters import connect
from course_storage import timestamp
from .protocol import MAX_COMMAND, MAX_FILE, MAX_FILES, MAX_SCAN_FILE, normalize_command, parse_command, parse_email
from .service import encoded, sha

WORKSPACE_ACTOR = "workspace"


def _request_actor():
    # The instructor's workspace unless authenticate_transport named a bearer API key (INBOX, ACCESS: only the workspace actor
    # deletes, restores, starts over, approves, uploads or combines scans).
    return getattr(g, "inbox_actor", WORKSPACE_ACTOR)


def register(app, service):
    api = Blueprint("inbox", __name__, url_prefix="/api/inbox")

    @api.get("/catalog")
    def catalog():
        return jsonify(service.catalog())

    @api.get("/course-groups/<identifier>/resources")
    def resources(identifier):
        number = request.args.get("section")
        if number is not None:
            try:
                number = int(number)
            except ValueError:
                raise ValueError("Section must be a whole number.")
        return jsonify(service.resources(identifier, number))

    @api.post("/command")
    def command():
        value = request.get_json()
        if not isinstance(value, dict) or set(value) != {"action", "target", "metadata_text"}:
            raise ValueError("Supply an action, target, and optional metadata text.")
        metadata = parse_command(value["metadata_text"]) if value["metadata_text"] else {}
        return jsonify(normalize_command({"action": value["action"], "target": value["target"], "metadata": metadata}))

    @api.get("/jobs")
    def jobs():
        try:
            offset = int(request.args.get("offset", "0"))
        except ValueError:
            raise ValueError("Page offset must be a nonnegative whole number.")
        if not 0 <= offset <= 1000000:
            raise ValueError("Page offset is out of range.")
        # kind, needs and assignment are additive (INBOX "SCANS AND SUBMISSIONS"): a read without them answers as it always did.
        return jsonify(service.listing(request.args.get("status") or None, offset, query=request.args.get("q", ""), files=request.args.get("files"),
                                       kind=request.args.get("kind") or None, needs=request.args.get("needs") == "1",
                                       assignment=request.args.get("assignment") or None))

    @api.get("/jobs/<identifier>")
    def job(identifier):
        # A strong ETag over the exact body: a job page polling an unchanged job (2 MB for a section scan) gets a 304 and no body.
        # Cache-Control stays no-store, so nothing is kept by the browser's cache.
        response = jsonify(service.get(identifier))
        response.add_etag()
        return response.make_conditional(request)

    @api.post("/jobs/<identifier>/<operation>")
    def change_job(identifier, operation):
        value = request.get_json()
        if not isinstance(value, dict):
            raise ValueError("Supply a job version and optional command.")
        actor = _request_actor()
        if operation == "delete":
            return jsonify(service.remove(identifier, value.get("version"), value.get("confirmation"), value.get("reason", ""), actor))
        if operation == "restore":
            return jsonify(service.restore(identifier, actor))
        if operation == "start-over":
            if set(value) - {"request_id", "confirmation", "keep"}:
                raise ValueError("Supply request_id, confirmation and keep.")
            return jsonify(service.start_over(identifier, value.get("request_id"), value.get("confirmation"), value.get("keep", True), actor)), 201
        return jsonify(service.transition(identifier, operation, value.get("version"), value.get("command"), actor))

    @api.post("/submit")
    def submit():
        files = []
        code = request.headers.get("X-Teaching-Submission-Code")
        problem = ""
        if request.mimetype == "multipart/form-data":
            text = request.form.get("command", "{}" if code else "")
            if len(text.encode()) > MAX_COMMAND:
                raise ValueError("The command must be at most 16 KB.")
            original = text.encode()
            try:
                command = parse_command(text)
            except ValueError as error:
                command, problem = {}, str(error)
            uploads = request.files.getlist("files")
            if len(uploads) > MAX_FILES or any(key != "files" for key in request.files):
                raise ValueError("Use the files field for up to 20 attachments.")
            for upload in uploads:
                files.append({"filename": upload.filename, "content": upload.read(MAX_FILE + 1)})
            key = request.headers.get("Idempotency-Key") or request.form.get("idempotency_key")
        elif request.is_json:
            value = request.get_json()
            if not isinstance(value, dict) or set(value) - {"command", "attachments", "idempotency_key"}:
                raise ValueError("Supply command, attachments, and an optional idempotency_key.")
            command = value.get("command", {})
            original = encoded(command).encode()
            if isinstance(command, str):
                try:
                    command = parse_command(command)
                except ValueError as error:
                    command, problem = {}, str(error)
            attachments = value.get("attachments", [])
            if not isinstance(attachments, list) or len(attachments) > MAX_FILES:
                raise ValueError("Supply a list of up to 20 attachments.")
            for attachment in attachments:
                if not isinstance(attachment, dict) or set(attachment) != {"filename", "content_base64"}:
                    raise ValueError("Attachments need filename and content_base64 fields.")
                try:
                    content = base64.b64decode(attachment["content_base64"], validate=True)
                except (ValueError, TypeError, binascii.Error) as error:
                    raise ValueError("An attachment has invalid base64 content.") from error
                files.append({"filename": attachment["filename"], "content": content})
            key = request.headers.get("Idempotency-Key") or value.get("idempotency_key")
        else:
            raise ValueError("Submit JSON or multipart form data.")
        actor = _request_actor()
        job = service.submit(command, files, source="api" if actor != WORKSPACE_ACTOR else "web", sender=actor,
                             original=original, problem=problem, idempotency_key=key, submission_code=code)
        if code:
            return jsonify({key: job[key] for key in ("id", "status", "received_at")}), 202
        return jsonify(job), 202

    @api.post("/scans")
    def upload_scan():
        # The instructor's own upload of a scanner PDF, the web twin of `manage_inbox.py scan`: no command text, review
        # stays required, and one arrival per file keyed by its content, so sending the same file again opens its scan.
        if _request_actor() != WORKSPACE_ACTOR or request.headers.get("X-Teaching-Submission-Code"):
            raise Forbidden("Upload scans from the instructor workspace.")
        uploads = request.files.getlist("file")
        if len(uploads) != 1 or set(request.files) != {"file"} or set(request.form):
            raise ValueError("Upload one scanned PDF per request, in the file field.")
        content, started = uploads[0].read(MAX_SCAN_FILE + 1), timestamp()
        try:
            job = service.submit({"action": "scan.receive"}, [{"filename": uploads[0].filename, "content": content}], source="web",
                                 sender="workspace", idempotency_key=f"scan:{sha(content)}", instructor_review_required=True, large_scans=True)
        except Conflict as error:
            # The same bytes under another file name: the content key already names an arrival with the first name.
            raise Conflict("This PDF is already in the Inbox under another file name. Open that scan instead.") from error
        return jsonify({**job, "existing": job["received_at"] < started}), 202

    @api.post("/scans/combine")
    def combine_scans():
        value = request.get_json()
        if not isinstance(value, dict) or set(value) != {"job_ids", "request_id", "confirmation"}:
            raise ValueError("Supply job_ids, request_id and confirmation.")
        return jsonify(service.combine_scans(value["job_ids"], value["request_id"], value["confirmation"],
                                             _request_actor())), 201

    @api.post("/email")
    def email():
        # The app before-request hook already verified the exact signed gateway body.
        value = request.get_json()
        basic = {"raw_email_base64", "recipient"}
        version_two = isinstance(value, dict) and type(value.get("schema_version")) is int and value.get("schema_version") == 2
        expected = basic | {"schema_version", "sender", "received_at"} if version_two else basic
        if (not isinstance(value, dict) or set(value) != expected or not isinstance(value["recipient"], str)
                or not 1 <= len(value["recipient"]) <= 254):
            raise ValueError("Supply a supported, exact signed email envelope.")
        if version_two:
            if not isinstance(value["sender"], str) or not 1 <= len(value["sender"]) <= 254 or not isinstance(value["received_at"], str):
                raise ValueError("The gateway envelope sender and received_at must be text.")
            try:
                if datetime.fromisoformat(value["received_at"].replace("Z", "+00:00")).utcoffset() is None:
                    raise ValueError()
            except ValueError as error:
                raise ValueError("The gateway received_at needs an ISO timestamp with a timezone.") from error
        try:
            raw = base64.b64decode(value["raw_email_base64"], validate=True)
        except (ValueError, TypeError, binascii.Error) as error:
            raise ValueError("The raw email is not valid base64.") from error
        command, files, sender, problem, authenticated, unread = {}, [], "unverified", "", False, ""
        intake_policy = service.email.intake_policy(value["recipient"])
        try:
            routes = {value["recipient"].lower(): "scan.receive"} if intake_policy else service.email.routes
            parsed = parse_email(raw, value["recipient"], routes, intake=bool(intake_policy))
            command, files, sender = parsed["command"], parsed["files"], parsed["sender"]
            if intake_policy:
                authenticated = version_two and service.email.intake_allowed(intake_policy, sender, value["sender"])
                problem = "Scan received. Page identities will be inspected and attached for instructor review; no teaching action runs automatically."
            else:
                authenticated = service.email.sender_allowed(sender, parsed["command_token"])
        except (ValueError, LookupError) as error:
            # Refused before any sender check ran: the job says why, never "Sender authentication failed" (SCANNER-INTAKE
            # "A SCAN EMAIL'S PDF MAY BE AS LARGE AS THE EMAIL").
            problem = unread = str(error)
        transport = {"schema_version": 2 if version_two else 1,
                     "body_sha256": hashlib.sha256(request.get_data(cache=True)).hexdigest(),
                     "recipient_sha256": hashlib.sha256(value["recipient"].lower().encode()).hexdigest(),
                     "envelope_sender": value.get("sender", ""), "received_at": value.get("received_at", ""),
                     "intake_policy": intake_policy["label"] if intake_policy else "authored command",
                     "review_only": bool(intake_policy and intake_policy.get("review_only"))}
        job = service.submit(command, files, source="email", sender=sender, original=raw, authenticated=authenticated,
                             problem=problem, idempotency_key=g.inbox_delivery, transport=transport,
                             instructor_review_required=transport["review_only"], unread=unread, intake=bool(intake_policy))
        receipt = {key: job[key] for key in ("id", "status", "received_at")}
        if version_two:
            receipt.update(delivery_id=g.inbox_delivery, body_sha256=transport["body_sha256"])
        return jsonify(receipt), 202

    @api.get("/artifacts/<identifier>/download")
    def artifact(identifier):
        name, content = service.read_artifact(identifier)
        return send_file(io.BytesIO(content), mimetype="application/octet-stream", as_attachment=True, download_name=name)

    @api.get("/messages/<identifier>/original")
    def original(identifier):
        name, content = service.read_original(identifier)
        return send_file(io.BytesIO(content), mimetype="application/octet-stream", as_attachment=True, download_name=name)

    @api.get("/access")
    def access():
        with connect(service.path) as db:
            codes = [dict(row) for row in db.execute("SELECT id,label,created_at,expires_at,used_message_id,revoked FROM inbox_codes ORDER BY created_at DESC LIMIT 100")]
            keys = [dict(row) for row in db.execute("SELECT id,label,created_at,revoked FROM inbox_api_keys ORDER BY created_at DESC LIMIT 100")]
        return jsonify(codes=codes, keys=keys, email_configured=service.email.enabled)

    @api.post("/codes")
    def new_code():
        value = request.get_json()
        if not isinstance(value, dict):
            raise ValueError("Supply a command, label, and optional lifetime in hours.")
        result = service.create_code(value.get("command"), value.get("label"), value.get("hours", 168))
        qr = qrcode.make(result["token"], image_factory=qrcode.image.svg.SvgPathFillImage)
        result["qr_svg"] = qr.to_string(encoding="unicode")
        return jsonify(result), 201

    @api.post("/codes/resolve")
    def resolve_code():
        value = request.get_json()
        with connect(service.path) as db:
            row = service.code(db, value.get("token") if isinstance(value, dict) else None)
        return jsonify(label=row["label"], command=json.loads(row["command_json"]), expires_at=row["expires_at"])

    @api.post("/keys")
    def new_key():
        value = request.get_json()
        return jsonify(service.create_api_key(value.get("label") if isinstance(value, dict) else None)), 201

    @api.post("/access/<kind>/<identifier>/revoke")
    def revoke(kind, identifier):
        if kind not in ("codes", "keys"):
            raise ValueError("Choose a submission code or API key.")
        table = "inbox_codes" if kind == "codes" else "inbox_api_keys"
        with service.rosters.lock(), connect(service.path) as db:
            db.execute(f"UPDATE {table} SET revoked=1 WHERE id=?", (identifier,))
        return jsonify(revoked=True)

    app.register_blueprint(api)


def authenticate_transport(service):
    """Return True only for authenticated machine ingress, before browser CSRF rules."""
    if request.endpoint == "inbox.email":
        g.inbox_delivery = service.email.verify_webhook(request.get_data(cache=True), request.headers)
        return True
    if not (request.endpoint or "").startswith("inbox."):
        return False
    authorization = request.headers.get("Authorization", "")
    code = request.headers.get("X-Teaching-Submission-Code")
    if authorization and code:
        raise Forbidden("Use one Inbox authentication method per request.")
    if code:
        if request.endpoint != "inbox.submit" or request.method != "POST":
            raise Forbidden("Submission codes authorize only their bound upload.")
        with connect(service.path) as db:
            service.code(db, code, permit_used=True)
        return True
    if authorization:
        if not authorization.startswith("Bearer "):
            raise Forbidden("Use an Inbox bearer API key.")
        g.inbox_actor = service.api_identity(authorization[7:])
        if request.endpoint in {"inbox.new_key", "inbox.new_code", "inbox.revoke", "inbox.resolve_code", "inbox.access"}:
            raise Forbidden("Manage API keys and submission codes in the instructor workspace.")
        if request.endpoint in {"inbox.upload_scan", "inbox.combine_scans"}:
            raise Forbidden("Upload and combine scans from the instructor workspace.")
        return True
    return False
