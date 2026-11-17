"""`/api/assistant` (AGENTS.md "Faculty assistant"). Writes pass the app's same-origin checks like every other route.

- Sessions and tool calls: `manage_assistant.py mcp` (for Claude Code/desktop and the CLI engines) opens a session and calls tools
  with its token. No tool can reach these routes (policy `never`), so nothing the assistant does can approve its own card.
- Actions: the instructor's Approve cards and the log. Approve and decline come only from the instructor's page.
"""
import io
import json
from functools import partial
from flask import Blueprint, jsonify, request, send_file

from request_limits import json_object

from .tools import mcp_tools


def register(app, service):
    api = Blueprint("assistant", __name__, url_prefix="/api/assistant")

    body = partial(json_object, silent=True)

    @api.get("/tools")
    def tools():
        return jsonify(tools=mcp_tools(service.tools))

    @api.post("/sessions")
    def open_session():
        data = body()
        session = service.open_session(data.get("engine", "external"), data.get("label", ""))
        return jsonify(id=session["id"], token=session["token"]), 201

    @api.delete("/sessions/<session_id>")
    def close_session(session_id):
        service.session(session_id, request.headers.get("X-Assistant-Token"))
        service.close_session(session_id)
        return jsonify(closed=True)

    @api.post("/sessions/<session_id>/tools/<name>")
    def call_tool(session_id, name):
        session = service.session(session_id, request.headers.get("X-Assistant-Token"))
        data = body()
        text, event = service.run_tool(session, name, data.get("arguments") or {})
        pictures = event.pop("pictures", [])  # for the model only (the proxy's image content); the panel keeps the routes
        if service.conversations is not None:
            service.conversations.tool_event(session_id, event)
        return jsonify(text=text, ok=event.get("ok", True), event=event, images=pictures)

    @api.get("/actions")
    def actions():
        before = request.args.get("before", type=int)
        return jsonify(**service.actions(pending_only=request.args.get("pending") == "1", before=before), batches=service.batches())

    @api.get("/actions/<action_id>")
    def action(action_id):
        # One card's record, read-only (REGRADES "APPROVE ALL": an approval whose answer the page lost is settled by reading it).
        return jsonify(action=service._view(*service.action(action_id)))

    @api.post("/actions/<action_id>/approve")
    def approve(action_id):
        data = body()
        seen = data.get("seen")
        if type(seen) is not int:
            raise ValueError("Name the card you approved (seen).")
        try:
            result = service.approve(action_id, seen)
        finally:
            service.conversations.resolved()
        return jsonify(result)

    @api.post("/actions/<action_id>/decline")
    def decline(action_id):
        body()
        result = service.decline(action_id)
        service.conversations.resolved()
        return jsonify(result)

    @api.post("/batches/<ident>/approve")
    def approve_batch(ident):
        result = service.approve_batch(ident, body().get("seen"))
        service.conversations.resolved()
        return jsonify(result)

    @api.post("/batches/<ident>/decline")
    def decline_batch(ident):
        body()
        result = service.decline_batch(ident)
        service.conversations.resolved()
        return jsonify(result)

    @api.get("/workspace")
    def workspace():
        from .metrics import read
        return jsonify(**service.store.listing(), **service.watches.listing(), **service.artifacts.listing(), **service.workflows.listing(), metrics=read(service))

    @api.get('/readiness/<event_id>')
    def readiness(event_id):
        return jsonify(service.workflows.observe('readiness', event_id))

    @api.get('/reconciliation/<term_id>')
    def reconciliation(term_id):
        return jsonify(service.workflows.observe('reconciliation', term_id))

    @api.get('/workflows')
    def workflows():
        return jsonify(service.workflows.listing())

    @api.get('/metrics')
    def metrics():
        from .metrics import read
        return jsonify(read(service))

    @api.get('/automations')
    def automations():
        from .automation import inventory
        return jsonify(inventory(service))

    @api.get('/blockers')
    def blockers():
        from .automation import blockers as read
        return jsonify(read(service, request.args.get('plan_id'), request.args.get('job_id')))

    @api.get('/workflows/options')
    def workflow_options():
        return jsonify(service.workflows.options())

    @api.post('/workflows/preview')
    def workflow_preview():
        return jsonify(service.workflows.preview(body()))

    @api.post('/workflows')
    def workflow_create():
        return jsonify(service.workflows.create(body())), 201

    @api.post('/workflows/<ident>')
    def workflow_update(ident):
        return jsonify(service.workflows.update(ident, body()))

    @api.post('/preparations')
    def preparation_create():
        return jsonify(service.workflows.prepare(body())), 202

    @api.post('/preparations/<ident>/stop')
    def preparation_stop(ident):
        body()
        return jsonify(service.workflows.stop(ident))

    @api.post('/preparations/<ident>/continue')
    def preparation_continue(ident):
        return jsonify(service.workflows.continue_plan(ident, body()))

    @api.post("/conversations/<ident>/save")
    def save_conversation(ident):
        conversation = service.conversations.get(ident)
        result = service.store.save(conversation, body())
        conversation.continue_if_ready()
        return jsonify(result)

    @api.post("/saved/<ident>/open")
    def open_saved(ident):
        body()
        return jsonify(service.conversations.reopen(ident))

    @api.delete("/saved/<ident>")
    def delete_saved(ident):
        body()
        service.conversations.delete_saved(ident)
        return jsonify(removed=True)

    @api.get("/memories")
    def memories():
        return jsonify(memories=service.store.memories())

    @api.post("/memories")
    def memory_create():
        return jsonify(service.store.memory(body())), 201

    @api.post("/memories/<ident>")
    def memory_update(ident):
        return jsonify(service.store.memory(body(), ident))

    @api.delete("/memories/<ident>")
    def memory_delete(ident):
        return jsonify(service.store.forget_memory(ident, body().get("revision")))

    # PAST-WORK: the Assistant's recorded actions nearest a described situation, what was carried out first. A read
    # (`policy.GET_OWN`): it changes nothing, and nothing it returns is kept as a memory.
    @api.get("/past-work")
    def past_work():
        return jsonify(service.past_work.find(request.args.get("q", ""), request.args.get("limit", type=int)))

    # ASSISTANT MEMORY: the instructor's list, and their own changes to it. No tool reaches these (policy `never`).
    @api.get("/memory")
    def memory_list():
        return jsonify(service.memory.listing())

    # ASSISTANT-MEMORY "ONE PAGE": the Memory page's switches and numbers, the instructor's alone (policy `never`, as every
    # route of the Assistant's own is).
    @api.get("/memory-settings")
    def memory_settings():
        return jsonify(service.memory.settings())

    @api.post("/memory-settings")
    def memory_settings_save():
        return jsonify(service.memory.configure(body()))

    @api.get("/memory/<ident>")
    def memory_history(ident):
        return jsonify(service.memory.history(ident))

    @api.get("/memory-pictures/<ident>")
    def memory_picture(ident):
        """A picture kept with a memory (ASSISTANT-MEMORY pictures): the instructor's page, the viewer and `fetch_files`."""
        media, content = service.memory.picture(ident)
        response = send_file(io.BytesIO(content), mimetype=media, max_age=0)
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        return response

    @api.post("/memory/<ident>/<operation>")
    def memory_change(ident, operation):
        data = body()
        if operation == "undo":
            result = service.memory.undo(ident)
            # The chat that showed "Remembered" learns it was taken back, when it is still open.
            chat = data.get("conversation_id")
            if isinstance(chat, str):
                try:
                    found = service.conversations.get(chat)
                    found.emit("memory", action="undone", memory={"id": ident}, restored=result["restored"])
                    found.persist()
                except Exception:  # noqa: BLE001 - a chat Faculty forgot has no note to update
                    pass
            return jsonify(result)
        if operation == "remove":
            return jsonify(service.memory.remove(ident, data.get("revision")))
        if operation == "restore":
            return jsonify(service.memory.restore(ident))
        if operation == "erase":
            return jsonify(service.memory.erase(ident))
        if operation == "as-typed":
            result = service.memory.as_typed(ident)
            # The chat that showed the rewording learns the instructor's own words are back, when it is still open.
            chat = data.get("conversation_id")
            if isinstance(chat, str):
                try:
                    found = service.conversations.get(chat)
                    found.emit("memory", action="as_typed", memory={"id": ident})
                    found.persist()
                except Exception:  # noqa: BLE001 - a chat Faculty forgot has no note to update
                    pass
            return jsonify(result)
        raise ValueError("Choose undo, remove, restore, erase or as-typed.")

    @api.post("/conversations/<ident>/questions/<question_id>")
    def answer_question(ident, question_id):
        conversation = service.conversations.get(ident)
        conversation.answer(question_id, body().get("answers"))
        return jsonify(conversation.view())

    @api.get("/watches")
    def watches():
        return jsonify(service.watches.listing())

    @api.post("/watches/preview")
    def watch_preview():
        return jsonify(service.watches.preview(body()))

    @api.post("/watches")
    def watch_create():
        return jsonify(service.watches.create(body())), 201

    @api.post("/watches/<ident>")
    def watch_update(ident):
        return jsonify(service.watches.update(ident, body()))

    @api.delete("/watches/<ident>")
    def watch_delete(ident):
        return jsonify(service.watches.update(ident, body(), delete=True))

    @api.post("/notifications/read")
    def notifications_read():
        return jsonify(service.watches.mark_read(body().get("ids")))

    @api.get("/artifacts")
    def artifacts():
        return jsonify(service.artifacts.listing(request.args.get("level"), request.args.get("owner_id")))

    @api.post('/materials/preview')
    def material_preview():
        return jsonify(service.materials.preview(body()))

    @api.post('/materials')
    def material_create():
        return jsonify(service.materials.create(body())), 201

    @api.post("/artifacts/preview")
    def artifact_preview():
        return jsonify(service.artifacts.preview(body()))

    @api.post("/artifacts")
    def artifact_create():
        return jsonify(service.artifacts.create(body())), 201

    @api.get("/artifacts/<ident>/file")
    def artifact_file(ident):
        meta, content = service.artifacts.read(ident)
        response = send_file(io.BytesIO(content), mimetype=meta["media_type"], as_attachment=True,
                             download_name=meta["filename"], etag=meta["sha256"], max_age=0)
        response.headers["Cache-Control"] = "no-store"
        return response

    # Live conversations; optional saved snapshots are workspace-owned catalog records.
    @api.get("/status")
    def status():
        return jsonify(service.conversations.status())

    @api.post("/conversations")
    def start():
        data = body()
        return jsonify(service.conversations.start(data.get("page"), data.get("history"))), 201

    @api.get("/conversations/<conversation_id>")
    def conversation(conversation_id):
        try:
            after = int(request.args.get("after", "0"))
        except ValueError:
            raise ValueError("after is a whole number.") from None
        return jsonify(service.conversations.get(conversation_id).view(after))

    @api.post("/conversations/<conversation_id>/messages")
    def send(conversation_id):
        data = body()
        try:
            after = int(data.get("after") or 0)
        except (TypeError, ValueError, OverflowError):
            raise ValueError("after is a whole number.") from None
        found = service.conversations.get(conversation_id)
        if 'review_only' in data and type(data['review_only']) is not bool:
            raise ValueError('ASSISTANT: review_only must be a boolean.')
        found.send(data.get("text"), data.get("page"), data.get("view"), capture_id=data.get('capture_id'), review_only=data.get('review_only', False),
                   review_context=data.get('review_context'), files=data.get('files'))
        # ASSISTANT MEMORY: only here, where the text is what the instructor typed in the chat. Fixed prompts, continuations and
        # preparations go through `send` elsewhere and are never considered.
        from .conversations import Conversation
        if type(found) is Conversation and isinstance(data.get("text"), str) and data["text"].strip():
            service.memory.capture_later(found, data["text"].strip(), getattr(found, '_tool_attempt', ''), data.get('files'))
        return jsonify(found.view(after)), 202

    # ATTACH IN CHAT (ASSISTANT, 2026-10-09): files the instructor gives the Assistant from the chat's paperclip. Kept a day as
    # workspace scratch (assistant/uploads.py); each is read by its own route, the way any Faculty file is looked at or attached.
    @api.post('/uploads')
    def upload_create():
        from .uploads import MAX_FILE
        parts = request.files.getlist('file')
        if set(request.files) - {'file'} or not parts:
            raise ValueError('Attach one or more files.')
        return jsonify(files=service.uploads.save([(part.filename or '', part.mimetype or '', part.stream.read(MAX_FILE + 1)) for part in parts])), 201

    @api.get('/uploads/<ident>')
    def upload_file(ident):
        found, content = service.uploads.read(ident)
        response = send_file(io.BytesIO(content), mimetype=found['type'], download_name=found['name'], max_age=0)
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        return response

    @api.post('/conversations/<conversation_id>/captures')
    def capture_upload(conversation_id):
        from .captures import MAX_BYTES
        found = service.conversations.get(conversation_id)
        if request.is_json:
            return jsonify(found.attach_capture(None, body())), 202
        if set(request.files) != {'image'} or set(request.form) != {'metadata'} or len(request.files.getlist('image')) != 1:
            raise ValueError('Supply one selected image and its metadata.')
        if len(request.form['metadata']) > 512 * 1024:
            raise ValueError('Capture metadata is too large.')
        try:
            metadata = json.loads(request.form['metadata'])
        except ValueError:
            raise ValueError('Supply valid capture metadata.') from None
        content = request.files['image'].stream.read(MAX_BYTES + 1)
        return jsonify(found.attach_capture(content, metadata)), 202

    @api.get('/conversations/<conversation_id>/captures/<capture_id>')
    def capture_image(conversation_id, capture_id):
        from werkzeug.exceptions import Gone
        found = service.conversations.get(conversation_id)
        with found._lock:
            capture = found.capture
            if not capture or capture['id'] != capture_id or not capture['content']:
                raise Gone('This selected image expired. Select the area again.')
            return send_file(io.BytesIO(capture['content']), mimetype=capture['media'],
                             download_name='selected-area.' + ('png' if capture['media'] == 'image/png' else 'jpg'))

    @api.post("/conversations/<conversation_id>/stop")
    def stop(conversation_id):
        body()
        found = service.conversations.get(conversation_id)
        found.stop()
        return jsonify(found.view())

    @api.post("/conversations/<conversation_id>/close")
    def close(conversation_id):
        body()
        service.conversations.close(conversation_id)
        return jsonify(closed=True)

    # Changing Faculty itself (assistant/coding.py). Starting is reached through an Approve card or the instructor's click; follow-ups,
    # Stop, Discard and Apply only from the instructor's page (policy `never` for the assistant).
    @api.post("/code-tasks")
    def code_task_start():
        return jsonify(service.coding.start(body())), 201

    @api.get("/code-tasks")
    def code_tasks():
        return jsonify(service.coding.listing())

    @api.get("/code-tasks/<task_id>")
    def code_task(task_id):
        return jsonify(service.coding.detail(task_id))

    @api.post("/code-tasks/<task_id>/follow-up")
    def code_task_follow_up(task_id):
        return jsonify(service.coding.follow_up(task_id, body())), 202

    @api.post("/code-tasks/<task_id>/stop")
    def code_task_stop(task_id):
        body()
        return jsonify(service.coding.stop(task_id))

    @api.post("/code-tasks/<task_id>/discard")
    def code_task_discard(task_id):
        body()
        return jsonify(service.coding.discard(task_id))

    @api.get("/restart-check")
    def restart_check():
        return jsonify(service.coding.restart_check())

    @api.post("/code-tasks/<task_id>/apply")
    def code_task_apply(task_id):
        return jsonify(service.coding.apply(task_id, body()))

    app.register_blueprint(api)
