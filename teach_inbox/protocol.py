"""Versioned command schema and deterministic, bounded JSON/YAML/email parsing."""
import json
import math
import re
from email import policy
from email.parser import BytesParser
from email.utils import getaddresses

import yaml

VERSION = "teach-inbox/1"
MAX_FILES = 20
MAX_FILE = 10 * 1024 * 1024
MAX_TOTAL = 25 * 1024 * 1024
MAX_REQUEST = 48 * 1024 * 1024
# An email to a scanner intake address may carry a PDF as large as the whole 25 MiB the gateway takes (Cloudflare's own
# limit, MAX_RAW in cloudflare/teach-inbox): SCANNER-INTAKE "A SCAN EMAIL'S PDF MAY BE AS LARGE AS THE EMAIL".
MAX_INTAKE_FILE = MAX_TOTAL
# A department scanner saves a whole section's exams as one PDF (hundreds of pages at 300 DPI). Only the
# instructor's own private adapters may retain files this large, and only as scan.receive PDFs.
MAX_SCAN_FILE = 512 * 1024 * 1024
MAX_SCAN_TOTAL = 1024 * 1024 * 1024
# A scanner's PDF is compressed images; a long run of zero bytes in one is a hole left by a damaged copy or
# transfer (the pages stored there render black or not at all), never scanned content.
DAMAGE_RUN = 64 * 1024
MAX_COMMAND = 16384

# Declaration is separate from implementation. A declared action without a handler
# can be retained/reviewed but can never be dispatched or marked complete.
ACTIONS = {
    "scan.receive": {"label": "Review incoming scans", "scopes": ["course", "term", "section"]},
    "document.archive": {"label": "Archive documents", "scopes": ["course", "term", "section"]},
    "roster.sync": {"label": "Import a roster", "scopes": ["section"]},
    "accommodations.sync": {"label": "Import accommodations", "scopes": ["workspace"]},
    "accommodations.exams.sync": {"label": "Import DR exam bookings", "scopes": ["workspace"]},
    "aim.form_capture": {"label": "Keep AIM's form structure", "scopes": ["workspace"]},
    "quiz.create": {"label": "Create a quiz", "scopes": ["term", "section"]},
    "quiz.ingest": {"label": "Ingest a quiz", "scopes": ["term", "section"]},
    "quiz.solution.ingest": {"label": "Ingest quiz solutions", "scopes": ["term", "section"]},
    "quiz.publish": {"label": "Publish a quiz", "scopes": ["term", "section"], "approval": True},
    "exam.ingest": {"label": "Ingest an exam", "scopes": ["term", "section"]},
    "exam.archive": {"label": "Archive an exam", "scopes": ["term", "section"]},
    "exam.release": {"label": "Release an exam", "scopes": ["term", "section"], "approval": True},
    "grade.import": {"label": "Import grades", "scopes": ["section"], "approval": True},
    "grade.export": {"label": "Export grades", "scopes": ["term", "section"]},
    "grade.report": {"label": "Create a grade report", "scopes": ["term", "section"]},
    "grades.publish": {"label": "Publish grades", "scopes": ["section"], "approval": True},
    "grades.modify": {"label": "Modify grades", "scopes": ["section"], "approval": True},
    "document.convert": {"label": "Convert documents", "scopes": ["course", "term", "section"]},
    "announcement.draft": {"label": "Draft an announcement", "scopes": ["course", "term", "section"]},
}


class CommandLoader(yaml.SafeLoader):
    def compose_node(self, parent, index):
        if self.check_event(yaml.AliasEvent) or self.peek_event().anchor:
            raise ValueError("Command YAML cannot contain anchors or aliases.")
        return super().compose_node(parent, index)

    def construct_mapping(self, node, deep=False):
        result = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if not isinstance(key, str) or key in result:
                raise ValueError("Command keys must be unique text names.")
            result[key] = self.construct_object(value_node, deep=deep)
        return result


def json_value(value, depth=0):
    if depth > 8:
        raise ValueError("Command metadata is nested too deeply.")
    if value is None or type(value) in (str, bool, int):
        return
    if type(value) is float and math.isfinite(value):
        return
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        for item in value.values():
            json_value(item, depth + 1)
        return
    if isinstance(value, list):
        for item in value:
            json_value(item, depth + 1)
        return
    raise ValueError("Commands accept only JSON-compatible values; quote dates as text.")


def parse_command(text):
    if not isinstance(text, str) or len(text.encode()) > MAX_COMMAND:
        raise ValueError("The command must be at most 16 KB of JSON or YAML.")
    try:
        value = yaml.load(text, Loader=CommandLoader)
        json_value(value)
        if not isinstance(value, dict):
            raise ValueError("A command must be a JSON or YAML object.")
        return value
    except (yaml.YAMLError, RecursionError) as error:
        raise ValueError("The command is not valid JSON or safe YAML.") from error


def normalize_command(value):
    json_value(value)
    if not isinstance(value, dict) or len(json.dumps(value).encode()) > MAX_COMMAND:
        raise ValueError("Supply a command object of 16 KB or less.")
    if type(value.get("schema_version", 1)) is not int or value.get("schema_version", 1) != 1:
        raise ValueError("This command schema version is not supported.")
    allowed = {"schema_version", "action", "target", "course", "term", "term_other", "year", "section", "metadata", "quiz", "version", "publish"}
    if set(value) - allowed:
        raise ValueError("Unknown command fields: " + ", ".join(sorted(set(value) - allowed)))
    action = value.get("action", "")
    if not isinstance(action, str) or not re.fullmatch(r"[a-z][a-z0-9]*(?:\.[a-z][a-z0-9_]*){1,4}", action) or len(action) > 100:
        raise ValueError("Choose an action from the Teaching Inbox registry.")
    metadata = value.get("metadata", {})
    if not isinstance(metadata, dict):
        raise ValueError("Metadata must be an object.")
    metadata = dict(metadata)
    for key in ("quiz", "version", "publish"):
        if key in value:
            if key in metadata and metadata[key] != value[key]:
                raise ValueError(f"Conflicting {key} fields.")
            metadata[key] = value[key]
    if "quiz" in metadata and (type(metadata["quiz"]) is not int or not 1 <= metadata["quiz"] <= 100000):
        raise ValueError("Quiz number must be a positive whole number.")
    if "version" in metadata and (not isinstance(metadata["version"], str) or not 1 <= len(metadata["version"].strip()) <= 40):
        raise ValueError("Quiz version must be nonempty text of at most 40 characters.")
    if "publish" in metadata and type(metadata["publish"]) is not bool:
        raise ValueError("Publish must be true or false.")
    target = value.get("target")
    if target is not None and (not isinstance(target, dict) or set(target) != {"level", "id"} or target["level"] not in ("course", "term", "section") or not isinstance(target["id"], str)):
        raise ValueError("Target needs an owning level (course, term, or section) and its permanent ID.")
    context = {key: value[key] for key in ("course", "term", "term_other", "year", "section") if key in value}
    if target is not None and context:
        raise ValueError("Use either a permanent target or course/term/section fields, not both.")
    return {"schema_version": 1, "action": action, "target": target, "metadata": metadata, **context}


def file_name(name):
    if not isinstance(name, str):
        raise ValueError("Each attachment needs a filename.")
    name = name.replace("\\", "/").rsplit("/", 1)[-1].strip()
    if not name or len(name) > 200 or any(ord(char) < 32 for char in name):
        raise ValueError("Attachment filenames must be nonempty text of at most 200 characters.")
    return name


def validate_files(files, *, large_scans=False, max_file=MAX_FILE):
    if large_scans:
        if not files or len(files) > MAX_FILES or sum(len(item["content"]) for item in files) > MAX_SCAN_TOTAL:
            raise ValueError("Submit 1 to 20 scanned PDFs totaling 1 GB or less.")
        for item in files:
            item["filename"] = file_name(item["filename"])
            if len(item["content"]) > MAX_SCAN_FILE or not item["content"].startswith(b"%PDF-"):
                raise ValueError("Each large scan must be a PDF of at most 512 MB.")
            hole = re.search(rb"\x00{%d,}" % DAMAGE_RUN, item["content"])
            if hole:
                raise ValueError(f"{item['filename']} looks damaged in transfer: {(hole.end() - hole.start()) // 1024:,} KB of empty bytes start at byte "
                                 f"{hole.start():,}, so the pages stored there cannot be read. Compare its SHA-256 with the original and send it again.")
        return
    if len(files) > MAX_FILES or sum(len(item["content"]) for item in files) > MAX_TOTAL:
        raise ValueError("Submit at most 20 attachments totaling 25 MB or less.")
    for item in files:
        item["filename"] = file_name(item["filename"])
        if not item["content"] or len(item["content"]) > max_file:
            raise ValueError(f"Each attachment must be nonempty and at most {max_file // (1024 * 1024)} MB.")


def parse_email(raw, recipient, routes, *, intake=False):
    """Parse explicit front matter and a strict subject grammar; never infer prose."""
    if len(raw) > 35 * 1024 * 1024:
        raise ValueError("The raw email exceeds 35 MB.")
    mail = BytesParser(policy=policy.default).parsebytes(raw)
    addresses = getaddresses(mail.get_all("From", []))
    if len(addresses) != 1 or not addresses[0][1]:
        raise ValueError("An email must contain exactly one sender.")
    sender = addresses[0][1].lower()
    subject = str(mail.get("Subject", ""))[:500]
    action = routes.get(recipient.lower())
    if not action:
        raise ValueError("The envelope recipient has no configured Teaching Inbox route.")
    files, body = [], ""
    for index, part in enumerate(mail.walk()):
        if index > 100:
            raise ValueError("The message contains too many MIME parts.")
        if part.is_multipart():
            continue
        content = part.get_payload(decode=True) or b""
        if part.get_filename() or part.get_content_disposition() == "attachment":
            files.append({"filename": part.get_filename() or f"attachment-{index}", "content": content})
        elif part.get_content_type() == "text/plain" and not body:
            body = content.decode(part.get_content_charset() or "utf-8", errors="replace")
    validate_files(files, max_file=MAX_INTAKE_FILE if intake else MAX_FILE)
    if intake:
        # Scanner bodies, subjects and filenames never select executable actions.
        return {"command": {"action": "scan.receive", "metadata": {}}, "files": files,
                "sender": sender, "subject": subject, "command_token": ""}
    command = {}
    stripped = body.lstrip("\ufeff\r\n ")
    if stripped.startswith("---\n") or stripped.startswith("---\r\n"):
        lines = stripped.splitlines()
        end = next((index for index, line in enumerate(lines[1:], 1) if line == "---"), None)
        if end is None:
            raise ValueError("The email command block needs a closing --- line.")
        command = parse_command("\n".join(lines[1:end]))
    # This secret authorizes the instructor command, independently of spoofable From.
    token = command.pop("command_token", "")
    if command.get("action", action) != action:
        raise ValueError("The email command action conflicts with its recipient route.")
    command["action"] = action
    match = re.fullmatch(r"([A-Za-z]+\s*[0-9]+-[0-9]+)(?:\s+(Quiz|Exam|Roster)(?:\s+([0-9]+))?(?:\s+([A-Za-z0-9]+))?)?", subject.strip(), re.I)
    if match:
        if "target" not in command:
            command.setdefault("course", match[1])
        if match[2] and match[2].lower() == "quiz":
            if match[3]:
                command.setdefault("quiz", int(match[3]))
            if match[4]:
                command.setdefault("version", match[4])
    return {"command": command, "files": files, "sender": sender, "subject": subject, "command_token": token}
