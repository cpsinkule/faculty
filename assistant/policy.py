"""What the assistant may do with each write route (requirement, 2026-09-29; AGENTS.md "Faculty assistant").

Every write route of the app is one of five tiers, decided by its Flask endpoint name (and, for a few, by what the request
asks):

- `read`: a POST that writes nothing (a preview, a search, a lookup). The assistant runs it like a GET.
- `auto`: the changes the instructor chose to let the assistant make by itself: Faculty's own calendar events, a section's TA
  contacts, content drafts (Faculty's copy of the Canvas modules and pages, changes asked of a quiz draft, rubric line
  comments), gradebook setup limited to columns, their names and due dates, housekeeping (retrying failed work, never on
  an assignment whose grades were ever certified), preparing a review the instructor asked for (a follow-up's suggestions
  prepared again, REDO FROM CHAT) and joining a student's conversations into one follow-up or keeping them separate again
  (CONNECT A MESSAGE). A request that asks for more than its category allows is `approve`.
- `approve`: everything else, and every route not named here (so a route added later is safe by default). The assistant
  prepares the request; the page's own preview runs when there is one; the instructor's click on the Approve card submits it,
  adding the `confirmation` value and the preview's fingerprint only then. The assistant never supplies a confirmation.
- `page`: flows whose page asks the instructor to acknowledge items one by one (certifying, Gradescope sends that may write to
  published exams, publishing grades on Gradescope, confirming a seating chart with its issues). The assistant opens the page; it does not propose them.
- `never`: sign-in windows, connections and credentials, Inbox transports and access keys, file uploads (the assistant has no
  files), compute settings (which hold the assistant's own model) and the assistant's own routes.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, replace

TIERS = ("read", "auto", "approve", "page", "never")


@dataclass(frozen=True)
class Rule:
    tier: str
    reason: str = ""
    category: str = ""               # the instructor's category for an `auto` rule
    preview: str | None = None       # endpoint that previews this write and writes nothing
    preview_method: str = "POST"
    confirmation: str | None = None  # supplied at the instructor's approval only
    fingerprint: tuple = ("fingerprint", "fingerprint")  # (field in the preview's answer, field of the write)
    request_id: bool = False         # the route refuses a write without a request ID (REQUEST_IDS): Faculty supplies one
    check: object = field(default=None, compare=False)   # (context, request) -> reason to escalate to approve, or None
    result_waits: bool = False       # what it starts waits for the instructor's approval on its own page (NO CARD FOR A REDO)


READ = {
    "student_support.watch_preview": "Previews one explicit, local follow-up watch.",
    "student_support.join_preview": "Shows what joining two of a student's conversations into one follow-up would record.",
    "absences.search": "Finds students by UIN digits.",
    "recordings.search": "Finds what was said in kept recordings by its words, or by a description of it (RECORDINGS); writes nothing.",
    "absences.department_makeup_preview": "Shows the department makeup exam request a card would send (reads the department's form; writes nothing).",
    "student_notes.search": "Finds students by UIN digits.",
    "student_support.start_now_preview": "Previews clearing follow-ups through a fixed instant.",
    "canvas.check": "Checks that Faculty's Canvas session still works.",
    "scheduling.class_plan_preview": "Checks a change to a term's class schedule as saving it would, writing nothing.",
    "canvas.content_preview": "Previews a push of the Canvas copy.",
    "canvas.message_preview": "Shows who a Canvas message to chosen sections would reach.",
    "canvas.announcement_preview": "Shows the Canvas courses an announcement to chosen sections would be posted in.",
    "canvas.section_content_preview": "Previews a direct push to one section's Canvas course.",
    "documents.copy_names_students": "Searches a section roster.",
    "documents.lookup": "Resolves a printed label.",
    "documents.sequence_preview": "Previews grouping a scan into papers.",
    "documents.unlabeled_students": "Searches a roster for a first page.",
    "bubble_sheets.students": "Searches a roster for a bubble sheet.",
    "frq_certification.adjust_preview": "Previews a line added to every grade.",
    "frq_certification.preview": "Previews a certification.",
    "frq_certification.search": "Finds students by UIN digits.",
    "frq_gradescope.create_preview": "Previews creating the Gradescope assignment.",
    "frq_gradescope.preview": "Previews a Gradescope send.",
    "frq_gradescope.publish_preview": "Previews publishing grades on Gradescope.",
    "frq_gradescope.sheet_preview": "Previews a bubble-sheet upload.",
    "frq_regrades.reply_preview": "Reads a regrade request on Gradescope and shows what a reply would do.",
    "frq_regrade_panel.decide_preview": "Reads Gradescope and Canvas and shows what a regrade decision would change.",
    "frq_regrade_panel.apply_preview": "Reads Gradescope and Canvas and shows what Apply would change.",
    "frq_grading.preview": "Previews a grading batch.",
    "frq_grading.preview_score": "Scores a selection without saving it.",
    "gmail_mail.preview": "Shows the exact message a send would make.",
    "gradebook.canvas_sync_preview": "Shows what Sync with Canvas would send, take and hold.",
    "gradebook.iclicker_preview": "Previews an import from iClicker.",
    "gradebook.canvas_cell_preview": "Previews sending exactly one student's grade to Canvas.",
    "gradebook.canvas_quiz_grade_preview": "Previews setting one student's grade on a quiz Canvas grades, in Canvas.",
    "gradebook.pull_preview": "Previews pulling grades into a column.",
}

NEVER = {
    "student_support.watch_update": "Instructor's own pause, resume or removal of a follow-up watch",
    # RECORDINGS "A MEMORY" / ASSISTANT-MEMORY "SAID IN A RECORDING": what a meeting said is kept only by the instructor's own
    # press of Remember on the recording's page, in words they may edit first. The Assistant keeps no memory, here as anywhere.
    "recordings.remember": "Instructor's own press of Remember on a recording's page",
    # RECORDINGS "Memos (step 6)" / ASSISTANT "A DICTATED REQUEST": what a memo asks is put to the Assistant by Faculty's own
    # reading of it, once, or by the instructor's press on the memo's page. The Assistant never sends itself a request out of
    # a transcript, and background work starts no other background work.
    "recordings.ask": "Instructor's own press of Ask the Assistant on a memo's page",
    # RECORDINGS "ONLY WHAT IS FACULTY'S IS KEPT": not keeping a recording erases it, and being asked again about what was not
    # kept brings recordings back to be decided. Both are the instructor's decision about what Faculty holds, never the Assistant's.
    "recordings.discard": "Instructor's own press of Don't keep on a recording",
    "recordings.ask_again": "Instructor's own press of Ask me again on the Recordings page",
    # Sign-in windows and connections: passwords and Duo stay between the instructor and the page.
    **{f"accommodations.aim_{name}": "AIM sign-in window" for name in ("answer", "close", "credentials", "export", "input", "record", "show", "start")},
    # SEND TO AIM opens AIM's sign-in window and writes to AIM: the instructor's own click (SCHEDULING "SEND TO AIM").
    "scheduling.aim_send_start": "AIM sign-in window",
    **{f"canvas_signin.signin_{name}": "Canvas sign-in window" for name in ("answer", "close", "credentials", "input", "show", "start")},
    **{f"department_signin.signin_{name}": "Google sign-in window" for name in ("answer", "close", "credentials", "input", "show", "start")},
    "department_signin.forget_session": "Forgetting Faculty's Google sign-in is the instructor's own action.",
    "canvas.forget": "Faculty's Canvas session",
    "gmail_mail.connect": "Google connection",
    "google_calendar.connect": "Google connection",
    "response_cards.gmail_connect": "Google connection",
    "response_cards.gmail_disconnect": "Google connection",
    # Inbox transports and access: other adapters' doors, and keys/codes that grant access.
    "inbox.command": "Inbox command adapter",
    "inbox.email": "Inbox email transport",
    "inbox.submit": "Inbox submission transport",
    "inbox.resolve_code": "Inbox submission codes",
    "inbox.new_code": "Inbox access codes",
    "inbox.new_key": "Inbox API keys",
    "inbox.revoke": "Inbox access",
    # Compute settings hold the connections and the assistant's own model.
    "frq_grading.save_settings": "Compute settings",
    "frq_grading.probe_endpoint": "Compute settings",
    "documents.fixture": "Test fixture",
    "absences.draft_mail": "Instructor's email prefill control",
    # JEV (docs/decisions/jev.md): the instructor's own controls; each spends a Jev request or clears a wellbeing concern.
    "jev_routing.palette_route": "Instructor's Ctrl K palette",
    "gmail_mail.check": "Instructor's check before sending",
    "canvas.message_check": "Instructor's check before sending",
    "student_support.wellbeing_reviewed": "Instructor's review of a possible wellbeing concern",
    "jev.retry_failed": "Instructor's Retry all failed of student triage",
    "student_support.written": "Instructor's own record of mail they wrote from a follow-up",
    "student_support.suggestion_request": "Instructor's follow-up revision, retry or clarification answer",
    **{f"student_support.suggestion_{name}": "Instructor's own follow-up suggestion control"
       for name in ("approve", "replace", "edit", "undo", "note", "prepare", "mute", "pause")},
    "absences.attach_mail": "Instructor's email documentation upload",
    # File uploads: the assistant has no files to send.
    **{name: "File upload" for name in (
        "accommodations.preview", "accommodations.submit", "assignment_templates.upload", "documents.compile_version",
        "frq_grading.upload_rubric", "frq_grading.upload_source", "gradebook.import_preview", "gradebook.import_apply",
        "inbox.upload_scan", "quiz_drafts.create", "rooms.create", "rooms.replace_chart", "section_roster",
        "student_photos.preview", "student_photos.import_photos", "course_notes.upload", "absences.upload", "question_bank.upload",
        "question_bank.add_picture", "picture_search.like_upload", "canvas.content_file_add")},
}

PAGE = {
    "frq_regrade_panel.apply": "Applying a regrade may replace published grades on Gradescope; review its acknowledgements on the regrade page.",
    "frq_certification.certify": "Certifying asks you to acknowledge each check and each Gradescope decision on Certification and Export.",
    "frq_gradescope.send": "A Gradescope send may need your confirmation of published exams, or of grades it replaces there, in its own dialog.",
    "frq_gradescope.sheet_send": "A Gradescope bubble-sheet send may need your confirmation of published exams in its own dialog.",
    "frq_gradescope.publish": "Publishing grades on Gradescope shows students their grades; you choose and confirm it in its own dialog on Certification and Export.",
    "seating.confirm": "Confirming a seating chart reviews its issues and card wording in the chart's own dialog.",
}

APPROVE = {
    "scheduling.use": Rule("approve", preview="scheduling.use_preview", preview_method="GET", confirmation="use"),
    "scheduling.undo": Rule("approve", confirmation="undo"),
    # A change the class schedule would refuse (two rows of one title, a stale revision) never becomes a card (2026-10-28).
    "scheduling.class_plan_save": Rule("approve", preview="scheduling.class_plan_preview"),
    "canvas.content_office_hours_put": Rule("approve", confirmation="put"),
    "gradebook.canvas_cell_send": Rule("approve", preview="gradebook.canvas_cell_preview", confirmation="send-cell"),
    "gradebook.canvas_quiz_grade": Rule("approve", preview="gradebook.canvas_quiz_grade_preview", confirmation="set-quiz-grade"),
    "quiz_drafts.rebuild": Rule("approve", confirmation="rebuild"),
    "quiz_drafts.scrap": Rule("approve", confirmation="scrap"),
    "student_support.start_now": Rule("approve", preview="student_support.start_now_preview", confirmation="start-now"),
    "frq_regrades.reply": Rule("approve", preview="frq_regrades.reply_preview", confirmation="reply"),
    # REGRADES "FOLLOW-UP" (2026-10-07): keep or change the grade and answer, carried everywhere by the same Apply as Return's.
    "frq_regrade_panel.decide": Rule("approve", preview="frq_regrade_panel.decide_preview", confirmation="decide"),
    "gmail_mail.send": Rule("approve", preview="gmail_mail.preview", confirmation="send"),
    "student_support.watch_create": Rule("approve", preview="student_support.watch_preview"),
    "gmail_mail.trash": Rule("approve", confirmation="trash"),
    "gradebook.pull": Rule("approve", preview="gradebook.pull_preview"),
    "gradebook.iclicker_apply": Rule("approve", preview="gradebook.iclicker_preview"),
    "gradebook.canvas_sync": Rule("approve", preview="gradebook.canvas_sync_preview", confirmation="sync"),
    # POSTING GRADES (requirement, 2026-10-08): a column's standing Post or Hide
    # in Canvas, carried out by the next sync, or back to its category's rule.
    "gradebook.canvas_posting_choice": Rule("approve", confirmation="choose"),
    "canvas.content_push": Rule("approve", preview="canvas.content_preview", confirmation="push"),
    "canvas.section_content_push": Rule("approve", preview="canvas.section_content_preview", confirmation="push"),
    "canvas.message_send": Rule("approve", preview="canvas.message_preview", confirmation="send"),
    # MAIL-SEND-LATER (2026-11-07, requirement: the Assistant schedules "through its Approve card"): the card shows the message and
    # its time; only the instructor's Approve keeps it, and the web process sends it then.
    "gmail_mail.schedule": Rule("approve", preview="gmail_mail.preview", confirmation="schedule"),
    "canvas.message_schedule": Rule("approve", preview="canvas.message_preview", confirmation="schedule"),
    "scheduled_sends.send_now": Rule("approve", confirmation="send"),
    # ANNOUNCEMENTS (requirement, 2026-10-15): drafted by the assistant, posted only from the instructor's Approve.
    "canvas.announcement_post": Rule("approve", preview="canvas.announcement_preview", confirmation="post"),
    "frq_certification.adjust": Rule("approve", preview="frq_certification.adjust_preview", confirmation="adjust"),
    "frq_gradescope.create": Rule("approve", preview="frq_gradescope.create_preview", confirmation="create"),
    "frq_grading.create_job": Rule("approve", preview="frq_grading.preview"),
    "frq_grading.reset_job": Rule("approve", preview="frq_grading.reset_outlook", preview_method="GET", confirmation="reset"),
    "frq_grading.reset_flag_resolution": Rule("approve", preview="frq_grading.flag_reset_preview", preview_method="GET", confirmation="reset"),
    "frq_grading.restore_certified": Rule("approve", confirmation="restore"),
    "google_calendar.remove": Rule("approve", confirmation="remove"),
    "course_notes.remove": Rule("approve", confirmation="remove"),
    "inbox.combine_scans": Rule("approve", confirmation="combine"),
    "quiz_drafts.approve": Rule("approve", confirmation="approve"),
    "seating.delete": Rule("approve", confirmation="delete"),
    "seating.gmail_send": Rule("approve", preview="seating.gmail_send_preview", preview_method="GET"),
    "seating_student_mail.send": Rule("approve", preview="seating_student_mail.preview", preview_method="GET"),
    "documents.sequence_apply": Rule("approve", preview="documents.sequence_preview"),
    # ABSENCES DEPARTMENT MAKEUP EXAMS (requirement, 2026-10-22: a Follow-up card they approve): the Math department's makeup exam
    # request form, sent to Google only from the instructor's Approve.
    "absences.department_makeup": Rule("approve", preview="absences.department_makeup_preview", confirmation="submit"),
    # SCHEDULING "DR REQUEST EMAILS" (requirement, 2026-11-01): one DR quiz's accessible copy uploaded to AIM, started only by the instructor's Approve, whose page then
    # shows AIM's sign-in window for their Duo. Send to AIM itself stays NEVER.
    "scheduling.aim_upload": Rule("approve", confirmation="upload"),
    "exams.apply_request": Rule("approve", preview="exams.preview", preview_method="GET", fingerprint=("preview_hash", "preview_hash")),
}

# Every write route the assistant may prepare (approve, or auto: a follow-up card) whose handler refuses a request without a
# request ID. The page's preview never sees one, so a card the model made without "$new_request_id" failed only at the
# instructor's Approve (2026-10-07: a regrade Keep card, "A decision needs a request ID."). Faculty puts a fresh one there instead
# (AssistantService._write). The instructor asked for every such card (2026-10-07); the list comes from a survey of the handlers,
# and tests check each name is a write route. Inbox's start-over is the one `inbox.change_job` operation that needs one.
REQUEST_IDS = frozenset({
    "student_support.watch_create",
    "absences.attach_message", "absences.create", "absences.department_form", "absences.department_makeup", "absences.document_event",
    "absences.makeup_card", "absences.makeup_hold", "absences.makeup_missed",
    "absences.makeup_setting", "absences.revise",
    "assistant.artifact_create", "assistant.code_task_start", "assistant.watch_create",
    "canvas.content_file_place", "canvas.content_office_hours_put", "canvas.content_placement", "canvas.content_push", "canvas.content_save",
    "canvas.content_start", "canvas.message_schedule", "canvas.message_send", "canvas.section_content_push", "canvas.announcement_post",
    "course_notes.remove", "course_notes.restore",
    "documents.batch", "documents.email_export", "documents.student_copy", "documents.printer_send", "documents.rename_assignment", "dr_quiz_takers.save",
    "exam_day.save", "exams.decision_card", "exams.feedback_cards", "exams.release", "exams.request_cards",
    "frq_certification.adjust", "frq_certification.queue_audit", "frq_gradescope.create",
    "frq_grading.accept_rubric_generation", "frq_grading.associate_scan", "frq_grading.check_rubric_quality",
    "frq_grading.compile_rubric", "frq_grading.create_job", "frq_grading.create_makeup", "frq_grading.queue_flag_resolution",
    "frq_grading.queue_rubric_generation", "frq_grading.read_answer_keys", "frq_grading.regrade", "frq_grading.reset_job", "frq_grading.reset_flag_resolution",
    "frq_grading.resolve_rubric_concerns", "frq_grading.restore_certified", "frq_grading.save_bubble_key",
    "frq_regrade_panel.decide", "frq_regrades.mark", "frq_regrades.reply",
    "gmail_mail.archive", "gmail_mail.schedule", "gmail_mail.send", "gmail_mail.trash", "gmail_mail.unarchive", "gmail_mail.untrash",
    "google_calendar.remove", "google_calendar.settings",
    "gradebook.canvas_cell_send", "gradebook.canvas_posting_choice", "gradebook.canvas_quiz_grade", "gradebook.canvas_skip", "gradebook.canvas_sync",
    "gradebook.iclicker_apply", "gradebook.iclicker_credit", "gradebook.iclicker_credit_withdraw", "gradebook.link_assignment", "gradebook.pull", "gradebook.save_setup", "gradebook.write_scores",
    "inbox.combine_scans", "jev.retry",
    "question_bank.accept", "question_bank.add_node", "question_bank.add_question", "question_bank.classify",
    "question_bank.cut_picture", "question_bank.link_versions", "question_bank.read_again", "question_bank.remove_picture",
    "question_bank.restore", "question_bank.restore_picture", "question_bank.retire", "question_bank.retry_run",
    "question_bank.revise_node", "question_bank.revise_question", "question_bank.set_printed", "question_bank.tag",
    "quiz_drafts.approve", "quiz_drafts.rebuild", "quiz_drafts.revise",
    "response_cards.control", "response_cards.issue",
    "scheduling.aim_prescheduling_mark", "scheduling.aim_prescheduling_retry", "scheduling.aim_prescheduling_unmark", "scheduling.aim_send_retry",
    "scheduling.aim_upload", "scheduling.class_plan_link", "scheduling.class_plan_save", "scheduling.dr_quiz_date", "scheduling.save", "scheduling.undo", "scheduling.use",
    "seating.gmail_send", "seating.seat_updates", "seating_student_mail.send",
    "section_roster_drop", "section_roster_restore",
    "student_notes.change", "student_notes.create", "student_notes.revise",
    "recordings.card", "recordings.cards", "recordings.coordinator", "recordings.file", "recordings.item", "recordings.read", "recordings.speakers",
    "recordings.students",
    "student_support.create", "student_support.join", "student_support.revise", "student_support.save_meeting_hours",
    "student_support.save_policy", "student_support.separate",
    "student_support.start_now", "student_support.suggestion_redo",
    "teaching_assistants.save", "workspace_calendar.create",
})

RETRY_OPERATIONS = {"retry"}
INBOX_CONFIRMATIONS = {"delete": "delete", "start-over": "start over"}


# Checks for `auto` rules: each returns why the request is more than its category allows (then it waits for approval), or None.

def _comments_only(context, request):
    body = request["body"]
    if body.get("lines"):
        return "It changes rubric wording or points, which regrades the question."
    if not isinstance(body.get("comments"), dict) or not body["comments"]:
        return "It changes more than line comments."
    return None


def _setup_columns_only(context, request):
    """Adding or renaming columns and setting due dates; nothing that changes how existing grades count."""
    body = request["body"]
    setup = body.get("setup")
    if not isinstance(setup, dict):
        return "It is not a setup."
    current = context.gradebook_setup(request["view_args"].get("term_id"))
    if current is None:
        return "The gradebook's current setup could not be read."
    for key, value in setup.items():
        if key in ("items",):
            continue
        if key == "categories":
            if _plain(value) != _plain(current.get("categories")):
                return "It changes the categories."
            continue
        if key in ("revision", "created_at", "actor"):
            continue
        if _plain(value) != _plain(current.get(key)):
            return f"It changes the gradebook's {key}."
    if "items" not in setup:
        return None
    items = setup["items"]
    if not isinstance(items, list):
        return "Its columns are not a list."
    existing = {item["id"]: item for item in current.get("items", []) if isinstance(item, dict) and item.get("id")}
    seen = set()
    for item in items:
        if not isinstance(item, dict):
            return "A column is not a set of fields."
        if item.get("id"):
            old = existing.get(item["id"])
            if old is None:
                return "It names a column the gradebook does not have."
            seen.add(item["id"])
            for key, value in item.items():
                if key in ("title", "due_date"):
                    continue
                if _plain(value) != _plain(old.get(key)):
                    return f"It changes a column's {key}."
        else:
            if item.get("assignment_id") or item.get("link") or item.get("replaces_lowest"):
                return "A new column is linked to grading or replaces a score."
    if set(existing) - seen:
        return "It removes a column."
    return None


def _plain(value):
    return json.dumps(value, sort_keys=True, default=str)


def _never_certified(context, request):
    aid = request["view_args"].get("aid")
    jid = request["view_args"].get("jid")
    if jid and not aid:
        aid = context.job_assignment(jid)
    assignments = [aid] if aid else context.retry_assignments(request["endpoint"], request["view_args"])
    if any(context.ever_certified(ident) for ident in assignments):
        return "Its assignment's grades were certified: ask before touching it (ASSISTANT-CERTIFIED)."
    return None


def _replaces_pending(context, request):
    """A requested preparation that would withdraw the instructor's unsubmitted cards of the same target is theirs to approve
    (2026-10-09 review: an assistant-started preparation silently replaced them). This narrows the automatic tier."""
    body = request["body"]
    workflows = getattr(context.service, "workflows", None)
    count = workflows.replaces(body.get("kind"), body.get("target")) if workflows is not None else 0
    if count:
        return (f"This replaces {count} change{'s' if count != 1 else ''} still waiting for your review in the current "
                "preparation; approve to prepare it again.")
    return None


AUTO = {
    # REDO FROM CHAT (requirement, 2026-10-09): the chat prepares a follow-up's suggestions again through the
    # follow-up's own preparation, never with cards of its own. Every step it prepares still waits on the Follow-up page.
    # NO CARD FOR A REDO (requirement, 2026-10-10): it starts
    # at once also when it replaces steps still waiting there, which until then made it an Approve card.
    # STILL NO CARD (requirement, 2026-10-12): the tool-use
    # check had made every redo since then a card all the same, as uncertain. `result_waits`: an uncertain, spent or unavailable
    # check and the corrective pass leave it automatic; a check that finds a clear mismatch still holds it (service, tool_guidance).
    "student_support.suggestion_redo": Rule("auto", category="Requested review preparation", result_waits=True,
                                            reason="You asked for it; every step it prepares waits for your review on the Follow-up page."),
    # CONNECT A MESSAGE (requirement, 2026-10-10): what they ask for in the chat is recorded at once. Nothing
    # is sent, the follow-up's status stays, and each undoes the other. A follow-up preparation never does either (followups.py).
    "student_support.join": Rule("auto", category="Follow-up conversations"),
    "student_support.separate": Rule("auto", category="Follow-up conversations"),
    "workspace_calendar.create": Rule("auto", category="Calendar and TA contacts"),
    "workspace_calendar.update": Rule("auto", category="Calendar and TA contacts"),
    "teaching_assistants.save": Rule("auto", category="Calendar and TA contacts"),
    "canvas.content_save": Rule("auto", category="Content drafts"),
    "canvas.content_placement": Rule("auto", category="Content drafts"),
    # canvas FILES, FROM THE ASSISTANT (requirement, 2026-10-10): a file they attached in the chat is kept and added to a module of Faculty's copy. A draft; the push that
    # sends it to Canvas stays an Approve card.
    "canvas.content_file_place": Rule("auto", category="Content drafts"),
    "canvas.content_start": Rule("auto", category="Content drafts"),
    "quiz_drafts.revise": Rule("auto", category="Content drafts"),
    "frq_grading.edit_rubric": Rule("auto", category="Content drafts", check=_comments_only),
    "gradebook.save_setup": Rule("auto", category="Gradebook setup", check=_setup_columns_only),
    "frq_grading.retry": Rule("auto", category="Housekeeping", check=_never_certified),
    "frq_grading.retry_location": Rule("auto", category="Housekeeping", check=_never_certified),
    "frq_grading.retry_all_locations": Rule("auto", category="Housekeeping", check=_never_certified),
    "frq_grading.retry_preparation": Rule("auto", category="Housekeeping", check=_never_certified),
    # QUIZZES "A COPY BY EMAIL" (requirement, 2026-11-08): one student's own stamped copy of an assignment, made at once so a reply can attach it. Nothing leaves Faculty: the
    # email that carries it stays an Approve card.
    "documents.student_copy": Rule("auto", category="A student's own copy"),
    "documents.decode_retry": Rule("auto", category="Housekeeping", check=_never_certified),
    "documents.alias_ocr_retry": Rule("auto", category="Housekeeping", check=_never_certified),
    "documents.version_sort_retry": Rule("auto", category="Housekeeping", check=_never_certified),
}


def base_rule(endpoint, view_args=None):
    """The rule for an endpoint before looking at the request's body."""
    view_args = view_args or {}
    rule = _route_rule(endpoint, view_args)
    if endpoint in REQUEST_IDS or (endpoint == "inbox.change_job" and view_args.get("operation") == "start-over"):
        rule = replace(rule, request_id=True)
    return rule


def _route_rule(endpoint, view_args):
    if endpoint in ("assistant.watch_preview", "assistant.artifact_preview", "assistant.workflow_preview", "assistant.material_preview"):
        return Rule("read", reason="A read-only preview (ASSISTANT-WATCHES / ASSISTANT-ARTIFACTS).")
    if endpoint in ("assistant.watch_create", "assistant.artifact_create"):
        return Rule("approve", preview=endpoint.replace("_create", "_preview"), reason="Review the destination and content before saving.")
    if endpoint == 'assistant.material_create':
        return Rule('approve', preview='assistant.material_preview', request_id=True,
                    reason='Review the material and its teaching destination before importing.')
    if endpoint == 'assistant.workflow_create':
        return Rule('approve', preview='assistant.workflow_preview', request_id=True,
                    reason='Enable automatic preparation for this exact target; every resulting change still needs review.')
    if endpoint == 'assistant.preparation_create':
        return Rule('auto', category='Requested review preparation', request_id=True, check=_replaces_pending,
                    reason='The instructor requested analysis; every resulting teaching change still needs approval.')
    if endpoint == 'assistant.preparation_continue':
        return Rule('auto', category='Requested review preparation', request_id=True)
    if endpoint == "assistant.code_task_start":
        # Handing a change of Faculty itself to a coding agent is always the instructor's decision (assistant/coding.py).
        return Rule("approve", reason="A coding agent will change Faculty's code in its own copy; nothing reaches Faculty until you Apply.")
    if not endpoint or endpoint.startswith("assistant."):
        return Rule("never", reason="The assistant's own routes")
    if endpoint in NEVER:
        return Rule("never", reason=NEVER[endpoint])
    if endpoint in READ:
        return Rule("read", reason=READ[endpoint])
    if endpoint in PAGE:
        return Rule("page", reason=PAGE[endpoint])
    if endpoint == "inbox.change_job":
        operation = view_args.get("operation")
        if operation in RETRY_OPERATIONS:
            return Rule("auto", category="Housekeeping", check=_never_certified)
        return Rule("approve", confirmation=INBOX_CONFIRMATIONS.get(operation))
    if endpoint in AUTO:
        return AUTO[endpoint]
    return APPROVE.get(endpoint) or Rule("approve")


def classify(endpoint, method, view_args, body, context):
    """The rule for this exact request. An `auto` request that asks for more than its category is `approve`, with the reason."""
    rule = base_rule(endpoint, view_args)
    if rule.tier == "auto" and rule.check:
        reason = rule.check(context, {"endpoint": endpoint, "method": method, "view_args": view_args or {}, "body": body or {}})
        if reason:
            return replace(Rule("approve", request_id=rule.request_id), reason=reason)
    return rule


# Secrets never go to the model: credentials, not student information.
SECRET_KEY = re.compile(r"(^|_)(token|tokens|secret|secrets|password|passwords|cookie|cookies|api_key|apikey|authorization|credential|credentials|refresh_token|access_token)$", re.I)
SECRET_COLUMN = re.compile(r"token|secret|password|cookie|api_key|credential", re.I)

# GET routes the assistant does not read as data: sign-in windows, callbacks, access links, photos and whole-workspace exports. The
# files among them that the instructor may see are fetched with `fetch_files` instead (FILE_BLOCKED below).
GET_BLOCKED = {
    "accommodations.aim_captures", "accommodations.aim_file", "accommodations.aim_frame", "accommodations.aim_saved", "accommodations.aim_status",
    "canvas_signin.signin_frame", "canvas_signin.signin_saved", "canvas_signin.signin_status",
    "department_signin.signin_frame", "department_signin.signin_saved", "department_signin.signin_status",
    "response_cards.gmail_callback", "response_cards.link", "student_photos.photo", "export",
    "index", "static", "versioned_asset",
    # The communications record's kept files (original messages, attachments, seat chart PDFs) are downloads, not JSON.
    "communications.original", "communications.attachment", "communications.seat_chart",
    # An absence's documentation (doctor's notes and the like) is a download, not JSON.
    "absences.document",
    # A file kept for a Canvas module item (canvas FILES) is a download, not JSON.
    "canvas.content_file",
    # A PDF read for the question bank is a download, not JSON; so are a question's pictures and a PDF's page pictures.
    "question_bank.upload_file", "question_bank.picture", "question_bank.page_picture",
    # A recording's kept audio is played by its own page, never read as data and never fetched as a file (RECORDINGS): the
    # Assistant answers with the recording, speaker, time and words, and the page plays that moment.
    "recordings.audio",
}
# Files (requirement, 2026-09-30): `fetch_files` shows what any other GET route serves (scans, papers, crops, PDFs, attachments, documentation, photos) in the
# instructor's drawer, and sends pictures and text to the model when it asks to look. Never: sign-in windows, callbacks, access
# links, the workspace export and the app's own shell.
FILE_BLOCKED = GET_BLOCKED - {"student_photos.photo", "communications.original", "communications.attachment",
                              "communications.seat_chart", "absences.document", "question_bank.picture", "question_bank.page_picture"}
# Shown to the instructor, never sent to the model: a face tells the model nothing about the question.
SHOW_ONLY = {"student_photos.photo"}
# The assistant's own routes it may read: how the coding tasks it proposed are going.
GET_OWN = {"assistant.code_tasks", "assistant.code_task"}
GET_OWN |= {"assistant.memories", "assistant.watches", "assistant.artifacts"}
GET_OWN |= {'assistant.workflows', 'assistant.workflow_options', 'assistant.readiness', 'assistant.reconciliation', 'assistant.metrics'}
GET_OWN |= {'assistant.automations', 'assistant.blockers'}
# PAST-WORK (requirement, 2026-10-10): what the Assistant itself proposed
# or did before, found by describing a situation. The action log's own route (`assistant.actions`) stays the instructor's.
GET_OWN |= {'assistant.past_work'}
FILE_OWN = {"assistant.artifact_file", "assistant.upload_file", "assistant.memory_picture"}  # ATTACH IN CHAT: a file the instructor attached in the chat
# Query parameters a read must not pass because they record something (revealing a hidden grade counts as seeing it).
GET_DROPPED_ARGS = {"frq_grading.response": {"reveal"}}
