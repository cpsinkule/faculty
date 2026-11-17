"""Workspace-owned HTTP body policy, shared by every Faculty route.

ACCESS: the application applies this only after its transport and same-origin checks.
Limits describe wire payloads; each owning service still validates its own fields.
Endpoint exceptions take precedence over blueprint defaults. No application data is kept.
"""
from dataclasses import dataclass

from flask import jsonify, request

from roster_import import MAX_UPLOAD
from teach_inbox.protocol import MAX_SCAN_FILE

KIB = 1024
MIB = 1024 * KIB


@dataclass(frozen=True)
class BodyPolicy:
    limit: int
    error: str
    parts: int | None = None
    multipart: bool = False

    def apply(self):
        request.max_content_length = self.limit
        if self.parts is not None:
            request.max_form_parts = self.parts
        accepted = request.mimetype == "multipart/form-data" if self.multipart else request.is_json
        if not accepted:
            return jsonify(error=self.error), 415
        return None


JSON = "Send JSON with Content-Type: application/json."
ENDPOINT_POLICIES = {}
for endpoints, policy in (
    (("inbox.upload_scan",), BodyPolicy(MAX_SCAN_FILE + 64 * KIB, "Upload a scanned PDF using multipart/form-data.", 1, True)),
    (("frq_grading.upload_source", "frq_grading.upload_rubric"), BodyPolicy(10 * MIB + 64 * KIB, "Upload rubric sources using multipart/form-data.", 4, True)),
    (("quiz_drafts.create",), BodyPolicy(61 * MIB, "Upload quiz material using multipart/form-data.", 24, True)),
    # Replacement previews carry each existing comment and rubric selection back with the fingerprint.
    (("frq_gradescope.send",), BodyPolicy(16 * MIB, "Send JSON with Content-Type: application/json.")),
    (("course_notes.upload",), BodyPolicy(101 * MIB, "Upload the notes using multipart/form-data.", 3, True)),
    (("question_bank.upload",), BodyPolicy(26 * MIB, "Upload the PDF using multipart/form-data.", 3, True)),
    (("question_bank.add_picture",), BodyPolicy(11 * MIB, "Upload the picture using multipart/form-data.", 4, True)),
    (("picture_search.like_upload",), BodyPolicy(26 * MIB, "Send the picture using multipart/form-data.", 2, True)),
    (("absences.upload",), BodyPolicy(21 * MIB, "Upload the documentation using multipart/form-data.", 3, True)),
    (("student_photos.preview", "student_photos.import_photos"), BodyPolicy(31 * MIB, "Upload the photo roster using multipart/form-data.", 5, True)),
    (("assignment_templates.upload",), BodyPolicy(26 * MIB, "Upload templates using multipart/form-data.", 104, True)),
    (("documents.compile_version",), BodyPolicy(26 * MIB, "Upload LaTeX sources using multipart/form-data.", 102, True)),
    (("section_roster",), BodyPolicy(MAX_UPLOAD + 64 * KIB, "Upload a roster file using multipart/form-data.", multipart=True)),
    (("accommodations.preview", "accommodations.submit"), BodyPolicy(MAX_UPLOAD + 64 * KIB, "Upload an accommodation export using multipart/form-data.", multipart=True)),
    (("rooms.create", "rooms.replace_chart"), BodyPolicy(MAX_UPLOAD + MIB + 64 * KIB, "Upload a seating chart using multipart/form-data.", multipart=True)),
    (("gradebook.import_preview", "gradebook.import_apply"), BodyPolicy(MAX_UPLOAD + 64 * KIB, "Upload a grade file using multipart/form-data.", 6, True)),
    (("canvas.section_content_preview", "canvas.section_content_push"), BodyPolicy(16 * MIB, "Send the course's modules, pages and assignments as JSON.")),
    (("canvas.content_save",), BodyPolicy(16 * MIB, "Send the modules and pages as JSON.")),
    (("canvas.content_file_add",), BodyPolicy(101 * MIB, "Upload the file using multipart/form-data.", 3, True)),
    (("canvas.message_preview", "canvas.message_send", "canvas.message_schedule"), BodyPolicy(256 * KIB, "Send the message as JSON.")),
    (("canvas.announcement_preview", "canvas.announcement_post"), BodyPolicy(256 * KIB, "Send the announcement as JSON.")),
    (("canvas.content_preview", "canvas.content_push"), BodyPolicy(512 * KIB, JSON)),
    (("assistant.upload_create",), BodyPolicy(60 * MIB, "Attach files as multipart/form-data.", 10, True)),
):
    ENDPOINT_POLICIES.update(dict.fromkeys(endpoints, policy))
del endpoints, policy

BLUEPRINT_POLICIES = {
    "frq_certification": BodyPolicy(MIB, "Send certification requests as JSON."),
    "quiz_drafts": BodyPolicy(256 * KIB, JSON),
    "frq_grading": BodyPolicy(2 * MIB, "Send grading requests as JSON."),
    "question_bank": BodyPolicy(256 * KIB, JSON),
    "assignment_templates": BodyPolicy(512 * KIB, "Send the problem map as JSON."),
    "gradebook": BodyPolicy(2 * MIB, "Send gradebook changes as JSON."),
    "assistant": BodyPolicy(MIB, JSON),
    "response_cards": BodyPolicy(128 * KIB, JSON),
    "exams": BodyPolicy(128 * KIB, JSON),
}
DEFAULT_POLICY = BodyPolicy(32 * KIB, JSON)
CANVAS_ATTACHMENTS = BodyPolicy(26 * MIB, "", 12, True)
MAIL_ATTACHMENTS = BodyPolicy(26 * MIB, "", 12, True)
CAPTURE_JSON = BodyPolicy(512 * KIB, "Supply selected content or one selected image and its metadata.", 2)
CAPTURE_MULTIPART = BodyPolicy(5 * MIB + 512 * KIB, CAPTURE_JSON.error, 2, True)


def protect_body():
    """Apply the owning route's exact size, multipart-part and content-type limits."""
    endpoint = request.endpoint
    if endpoint == "inbox.submit" and request.method == "POST":
        return None  # The authenticated Inbox protocol owns its payload.
    if endpoint == "assistant.capture_upload":
        return (CAPTURE_JSON if request.is_json else CAPTURE_MULTIPART).apply()
    if request.mimetype == "multipart/form-data":
        if endpoint in {"canvas.message_preview", "canvas.message_send"}:
            return CANVAS_ATTACHMENTS.apply()
        if endpoint in {"gmail_mail.preview", "gmail_mail.send"}:
            return MAIL_ATTACHMENTS.apply()
    policy = ENDPOINT_POLICIES.get(endpoint) or BLUEPRINT_POLICIES.get(request.blueprint)
    if policy is not None:
        return policy.apply()
    request.max_content_length = DEFAULT_POLICY.limit
    # Preserve the default policy's size-before-content-type error order.
    if request.content_length and request.content_length > DEFAULT_POLICY.limit:
        return jsonify(error="The request is too large."), 413
    return DEFAULT_POLICY.apply()


def json_object(message="Supply a JSON object.", *, silent=False):
    """A JSON object body; each route keeps its own `silent` flag and message (a blueprint's `body` alias fixes them)."""
    value = request.get_json(silent=silent)
    if not isinstance(value, dict):
        raise ValueError(message)
    return value
