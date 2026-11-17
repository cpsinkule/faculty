"""Focused workspace tools; no tool reaches instructor answers or approvals."""
from .tools import Tool, S, _schema


def question(context, args):
    conversation = context.service.conversations.by_session(context.session["id"]) if context.service.conversations else None
    if conversation is None:
        return {"outcome": "ask_in_client", "questions": args.get("questions"),
                "note": "Ask the instructor in this client, then continue after their answer. This does not approve writes."}
    return conversation.ask(args.get("questions"))


def workspace_tools():
    from .tool_guidance import advice
    return [
        Tool('tool_use_advice', 'Read advisory tool recommendations and checks for the current task. External MCP clients: supply the instructor request to start bounded guidance for this session. This never authorizes a change.',
             _schema({'request': S}), advice, read=True),
        Tool("ask_question", "Ask one to three clarification questions in the Faculty drawer, then end the turn and wait. "
             "Options are optional; the instructor can always write an answer. Answers never approve actions.",
             _schema({"questions": {"type": "array", "items": _schema({"title": S, "options": {"type": "array", "items": S}}, ("title",))}}, ("questions",)),
             question, label=lambda a: "Asked for your input"),
        Tool("group_actions", "Group pending actions from this conversation for joint instructor review: 2 to 20 different kinds "
             "of change, or up to 200 cards of ONE route (the same change for each of many records, such as one grade change per "
             "student), which the instructor sees as ONE summary card with Approve all. When you prepare the same change for "
             "several records, make every card, then group them ALL in one call (a card already in a group moves to the new one). "
             "Prepare each exact request first. This only groups cards; it approves or executes nothing.",
             _schema({"title": S, "action_ids": {"type": "array", "items": S}}, ("title", "action_ids")),
             lambda c, a: c.service.batch(c.session, a.get("action_ids"), a.get("title")), label=lambda a: "Grouped changes for review"),
        Tool("create_watch", "Propose a read-only watch for a scan (Inbox job ID), grading plan or exam. "
             "condition is changed, finished, needs_attention or ready. Only when the instructor requests it, prepare_review also prepares review cards on a matching transition. Never sends mail or changes the work.",
             _schema({"title": S, "kind": {"type": "string", "enum": ["scan", "grading", "exam"]}, "target": S,
                      "prepare_review": {"type": "boolean"},
                      "condition": {"type": "string", "enum": ["changed", "finished", "needs_attention", "ready"]}}, ("title", "kind", "target", "condition")),
             lambda c, a: c.write("POST", "/api/assistant/watches", {**a, "request_id": "$new_request_id"}, "Watch: " + a["title"]),
             label=lambda a: "Prepared a watch"),
        Tool("create_artifact", "Propose a new teaching file under its correct retained owner: definition (course-wide), term or section. "
             "Use the permanent owner ID, title, filename (.md .txt .tex .csv .json .pdf), and text content. PDF renders plain paragraphs; .tex retains reusable LaTeX. "
             "The instructor reviews and approves before saving; no publishing, sending, grade changes or file replacement.",
             _schema({"level": {"type": "string", "enum": ["definition", "term", "section"]}, "owner_id": S, "title": S, "filename": S, "content": S},
                     ("level", "owner_id", "title", "filename", "content")),
             lambda c, a: c.write("POST", "/api/assistant/artifacts", {**a, "request_id": "$new_request_id"}, "Create teaching file: " + a["title"]),
             label=lambda a: "Prepared a teaching file"),
        Tool("saved_preferences", "Read only the instructor's explicitly saved preferences. Never store an inferred preference or override tool permissions.",
             _schema({}), lambda c, a: c.get("/api/assistant/memories"), label=lambda a: "Read saved preferences", read=True),
        Tool("watches", "Read saved watches and their latest observed state and notifications.", _schema({}),
             lambda c, a: c.get("/api/assistant/watches"), label=lambda a: "Read watches", read=True),
        Tool("artifacts", "List generated teaching files, optionally for one permanent owner.", _schema({"level": S, "owner_id": S}),
             lambda c, a: c.get("/api/assistant/artifacts", a), label=lambda a: "Read teaching files", read=True),
    ]
