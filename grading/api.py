"""Private instructor routes; browser access follows the existing network boundary."""
import io

from flask import Blueprint, Response, jsonify, request, send_file

from request_limits import json_object


def register(app, service):
    api = Blueprint("frq_grading", __name__, url_prefix="/api/grading")

    body = json_object

    @api.get("")
    def overview():
        view = request.args.get("view", "overview")
        if view not in ("overview", "navigation"):
            raise ValueError("Choose the grading overview or navigation view.")
        return jsonify(service.overview(navigation=view == "navigation", include_health=request.args.get("health") != "0"))

    @api.get("/catalog")
    def catalog():
        return jsonify(exams=service.exams.list(), **service.exams.catalog())

    @api.get("/scans/<message_id>")
    def scan_context(message_id):
        return jsonify(service.scan_context(message_id))

    @api.post("/scans/<message_id>/associate")
    def associate_scan(message_id):
        return jsonify(service.associate_scan(message_id, body()))

    @api.post("/scans/<message_id>/review")
    def review_scan(message_id):
        return jsonify(service.review_scan(message_id, body()))

    @api.get("/settings")
    def settings():
        return jsonify(service.settings())

    @api.put("/settings")
    def save_settings():
        return jsonify(service.save_settings(body()))

    @api.get("/health")
    def health():
        return jsonify(service.health())

    @api.get("/endpoints")
    def endpoints():
        return jsonify(service.endpoints())

    @api.get("/model-catalog")
    def model_catalog():
        refresh = request.args.get("refresh", "0")
        if refresh not in ("0", "1"):
            raise ValueError("Refresh must be 0 or 1.")
        return jsonify(service.model_catalog.listing(refresh=refresh == "1", provider=request.args.get("provider", "openrouter")))

    @api.get("/model-catalog/hosts")
    def model_hosts():
        return jsonify(service.model_catalog.hosts(request.args.get("provider", ""), request.args.get("model", "")))

    @api.post("/endpoints/<name>/probe")
    def probe_endpoint(name):
        body()
        return jsonify(service.probe_endpoint(name)), 202

    @api.post("/assignments")
    def create_assignment():
        # Set up grading now (requirement, 2026-10-02): the instructor's explicit action makes the plan before any paper; asking again
        # for an exam record that has one returns it (200).
        from request_actor import current
        plan, created = service.set_up_grading(body(), current())
        return jsonify(plan), 201 if created else 200

    @api.get("/assignments/<aid>")
    def assignment(aid):
        return jsonify(service.assignment(aid, include_activity=request.args.get('include') == 'activity'))

    @api.get("/assignments/<aid>/bubble-sheets")
    def bubble_grading(aid):
        return jsonify(service.bubble_grading(aid))

    @api.post("/assignments/<aid>/bubble-sheets/key")
    def save_bubble_key(aid):
        return jsonify(service.save_bubble_key(aid, body())), 201

    @api.get("/assignments/<aid>/bubble-sheets/scores.csv")
    def bubble_scores(aid):
        content, name = service.bubble_scores_csv(aid)
        return send_file(io.BytesIO(content), mimetype="text/csv", as_attachment=True, download_name=name)

    @api.get("/assignments/<aid>/bubble-sheets/analysis")
    def bubble_analysis(aid):
        return jsonify(service.bubble_analysis(aid))

    @api.get("/assignments/<aid>/bubble-sheets/analysis.csv")
    def bubble_analysis_csv(aid):
        content, name = service.bubble_analysis_csv(aid)
        return send_file(io.BytesIO(content), mimetype="text/csv", as_attachment=True, download_name=name)

    @api.get('/assignments/<aid>/templates')
    def assignment_templates(aid):
        return jsonify(service.assignment_templates(aid))

    @api.post('/assignments/<aid>/templates/prepare')
    def prepare_assignment_template(aid):
        return jsonify(service.prepare_assignment_template(aid, body())), 201

    @api.post('/assignments/<aid>/templates/confirm')
    def confirm_assignment_templates(aid):
        return jsonify(service.confirm_assignment_templates(aid, body()))

    @api.post("/assignments/<aid>/questions")
    def question(aid):
        return jsonify(service.save_question(aid, body())), 201

    @api.post("/assignments/<aid>/questions/<qid>/rubrics")
    def rubric(aid, qid):
        return jsonify(service.import_rubric(aid, qid, body())), 201

    @api.get("/assignments/<aid>/questions/<qid>/rubrics")
    def rubric_history(aid, qid):
        return jsonify(rubrics=service.rubric_history(aid, qid))

    @api.post("/assignments/<aid>/questions/<qid>/compile")
    def compile_rubric(aid, qid):
        return jsonify(service.compile_rubric(aid, qid, body())), 202

    @api.post("/assignments/<aid>/questions/<qid>/locations/confirm")
    def confirm_location(aid, qid):
        return jsonify(service.confirm_location(aid, qid, body()))

    @api.post("/assignments/<aid>/locations/confirm-all")
    def confirm_all_locations(aid):
        return jsonify(service.confirm_all_locations(aid, body()))

    @api.post("/assignments/<aid>/questions/<qid>/locations/retry")
    def retry_location(aid, qid):
        return jsonify(service.retry_location(aid, qid, body()))

    @api.get("/assignments/<aid>/local-work")
    def local_work(aid):
        return jsonify(service.local_work(aid))

    @api.post("/assignments/<aid>/locations/retry-all")
    def retry_all_locations(aid):
        return jsonify(service.retry_all_locations(aid, body()))

    @api.post("/assignments/<aid>/questions/<qid>/locations/template")
    def select_template(aid, qid):
        return jsonify(service.select_location_template(aid, qid, body()))

    @api.get("/preparations/<identifier>")
    def preparation(identifier):
        return jsonify(service.preparation(identifier))

    @api.post("/preparations/<identifier>/retry")
    def retry_preparation(identifier):
        body()
        return jsonify(service.retry_preparation(identifier))

    @api.post("/assignments/<aid>/questions/<qid>/rubric-lines")
    def correct_rubric_line(aid, qid):
        return jsonify(service.correct_rubric_line(aid, qid, body())), 201

    @api.post("/assignments/<aid>/questions/<qid>/rubric-edits")
    def edit_rubric(aid, qid):
        return jsonify(service.edit_rubric(aid, qid, body())), 201

    @api.post("/assignments/<aid>/rubric-review-ready")
    def confirm_rubric_review(aid):
        confirmed = service.confirm_rubric_review(aid, body())
        # ACCEPTING A DRAFT READIES IT (requirement, 2026-10-11): after the instructor's own confirmation (a drafted rubric they
        # edited, say) the rubric check of a plan with accepted drafts may start by itself; the answer then shows it. Asked here,
        # never inside `confirm_rubric_review`: a quiz's and a makeup's approval keep their own check requests.
        return jsonify(service.assignment(aid) if service.rubric_review_follows(aid) == '' else confirmed)

    @api.post("/assignments/<aid>/questions/<qid>/approve-rubric")
    def approve_rubric(aid, qid):
        return jsonify(service.approve_rubric(aid, qid, body()))

    @api.post("/assignments/<aid>/sources")
    def upload_source(aid):
        upload = request.files.get("file")
        if not upload:
            raise ValueError("Choose a rubric source file.")
        content = upload.read(10 * 1024**2 + 1)
        if len(content) > 10 * 1024**2:
            raise ValueError("Rubric sources must be at most 10 MiB.")
        return jsonify(service.upload_source(aid, upload.filename, content, request.form.get("request_id"))), 201

    @api.post("/assignments/<aid>/rubric-uploads")
    def upload_rubric(aid):
        upload = request.files.get("file")
        if upload is None or not upload.filename:
            raise ValueError("Choose a Gradescope rubric CSV.")
        content = upload.read(10 * 1024 * 1024 + 1)
        return jsonify(service.upload_rubric(aid, upload.filename, content, request.form)), 201

    @api.post('/assignments/<aid>/rubric-drafts')
    def queue_rubric_generation(aid):
        return jsonify(service.queue_rubric_generation(aid, body())), 202

    @api.get('/assignments/<aid>/rubric-drafts/<gid>')
    def rubric_generation(aid, gid):
        return jsonify(service.rubric_generation(aid, gid))

    @api.post('/assignments/<aid>/rubric-drafts/<gid>/cancel')
    def cancel_rubric_generation(aid, gid):
        body()
        return jsonify(service.cancel_rubric_generation(aid, gid))

    @api.post('/assignments/<aid>/rubric-drafts/<gid>/accept')
    def accept_rubric_generation(aid, gid):
        return jsonify(service.accept_rubric_generation(aid, gid, body())), 201

    @api.get('/assignments/<aid>/rubric-links')
    def rubric_links(aid):
        return jsonify(service.rubric_links(aid))

    @api.post('/assignments/<aid>/rubric-links')
    def set_rubric_links(aid):
        return jsonify(service.set_rubric_links(aid, body()))

    @api.get('/assignments/<aid>/rubric-choices')
    def rubric_choices(aid):
        return jsonify(service.rubric_choices(aid))

    @api.post('/assignments/<aid>/rubric-choices')
    def set_rubric_choices(aid):
        return jsonify(service.set_rubric_choices(aid, body()))

    # GRADING "GRADED FOR PARTICIPATION" (requirement, 2026-11-01): full credit for everyone with a paper, the rubric lines kept.
    @api.get('/assignments/<aid>/participation')
    def participation(aid):
        return jsonify(service.participation(aid))

    @api.put('/assignments/<aid>/participation')
    def set_participation(aid):
        return jsonify(service.set_participation(aid, body()))

    @api.post('/assignments/<aid>/rubric-quality/fixes')
    def undo_rubric_fix(aid):
        return jsonify(service.undo_rubric_fix(aid, body()))

    # AN EDIT ONLY THE INSTRUCTOR CAN MAKE (requirement, 2026-10-29): the points change a decision proposes, approved with one click.
    @api.post('/assignments/<aid>/rubric-quality/edits')
    def apply_rubric_edit(aid):
        return jsonify(service.apply_rubric_edit(aid, body()))

    @api.get('/assignments/<aid>/rubric-quality')
    def rubric_quality(aid):
        return jsonify(service.rubric_quality(aid))

    @api.put('/assignments/<aid>/rubric-quality')
    def rubric_quality_options(aid):
        return jsonify(service.set_rubric_quality(aid, body()))

    @api.post('/assignments/<aid>/rubric-quality/check')
    def check_rubric_quality(aid):
        return jsonify(service.queue_rubric_quality(aid, body())), 202

    @api.post('/assignments/<aid>/rubric-quality/resolve')
    def resolve_rubric_concerns(aid):
        return jsonify(service.resolve_rubric_concerns(aid, body())), 202

    @api.post('/assignments/<aid>/rubric-quality/cancel')
    def cancel_rubric_quality(aid):
        return jsonify(service.cancel_rubric_quality(aid, body()))

    @api.post('/assignments/<aid>/rubric-quality/confirm')
    def confirm_rubric_quality(aid):
        return jsonify(service.confirm_rubric_quality(aid, body()))

    @api.post('/assignments/<aid>/rubric-quality/clarify')
    def clarify_rubric_question(aid):
        return jsonify(service.clarify_question(aid, body()))

    @api.post("/assignments/<aid>/answer-keys/read")
    def read_answer_keys(aid):
        return jsonify(service.queue_answer_key_reading(aid, body())), 202

    @api.post("/assignments/<aid>/answer-key")
    def save_answer_key(aid):
        return jsonify(service.save_answer_key(aid, body()))

    @api.get("/assignments/<aid>/answer-groups")
    def answer_groups(aid):
        return jsonify(service.answer_groups(aid, request.args.get("question_id")))

    @api.post("/assignments/<aid>/approve-group")
    def approve_group(aid):
        return jsonify(service.approve_group(aid, body()))

    @api.get("/assignments/<aid>/proposal-gallery")
    def proposal_gallery(aid):
        return jsonify(service.proposal_gallery(aid, request.args.get("question_id"), request.args.get("group"), request.args.get("job_id"),
                                                request.args.get("only") or None))

    @api.post("/assignments/<aid>/proposal-gallery/approve")
    def approve_proposal_group(aid):
        return jsonify(service.approve_proposal_group(aid, body()))

    @api.get("/assignments/<aid>/paper-versions")
    def paper_versions(aid):
        return jsonify(service.paper_versions(aid))

    @api.post("/assignments/<aid>/paper-versions/confirm")
    def confirm_paper_version(aid):
        return jsonify(service.confirm_paper_version(aid, {**body(), "actor": "workspace"}))

    # Makeup versions drafted by a model (GRADING "MAKEUP VERSIONS", grading/makeup_drafts.py): revising, stopping, scrapping, the
    # compiled files and approving go through the quiz-draft routes (/api/quiz-drafts/<draft>/...), since a makeup draft is a quiz draft.
    @api.get("/assignments/<aid>/makeups")
    def makeups(aid):
        return jsonify(service.makeup_options(aid))

    @api.post("/assignments/<aid>/makeups")
    def create_makeup(aid):
        return jsonify(service.create_makeup_draft(aid, body())), 201

    @api.post("/assignments/<aid>/exam-versions/add")
    def add_exam_version(aid):
        return jsonify(service.change_added_version(aid, body(), True))

    @api.post("/assignments/<aid>/exam-versions/remove")
    def remove_exam_version(aid):
        return jsonify(service.change_added_version(aid, body(), False))

    @api.post("/assignments/<aid>/exam-version")
    def assign_exam_version(aid):
        return jsonify(service.assign_exam_version(aid, body()))

    @api.get("/assignments/<aid>/acceptance")
    def acceptance_report(aid):
        return jsonify(service.acceptance_report(aid, request.args.get("question_id"), request.args.get("scope") or None))

    @api.get("/assignments/<aid>/acceptance/queue")
    def acceptance_queue(aid):
        return jsonify(service.acceptance_queue(aid, request.args.get("question_id") or None))

    @api.post("/assignments/<aid>/questions/<qid>/acceptance/enable")
    def enable_acceptance(aid, qid):
        return jsonify(service.enable_acceptance(aid, qid, body()))

    @api.post("/assignments/<aid>/questions/<qid>/acceptance/disable")
    def disable_acceptance(aid, qid):
        return jsonify(service.disable_acceptance(aid, qid, body()))

    @api.post("/assignments/<aid>/preview")
    def preview(aid):
        return jsonify(service.preview(aid, body()))

    @api.post("/assignments/<aid>/jobs")
    def create_job(aid):
        return jsonify(service.create_job(aid, body())), 201

    @api.get("/jobs/<jid>")
    def job(jid):
        # An exam-sized batch lists only the responses that need attention unless every one is asked for.
        return jsonify(service.job(jid, task_limit=None if request.args.get("tasks") == "all" else 200))

    @api.post("/jobs/<jid>/restore-certified")
    def restore_certified(jid):
        # Puts back the certified grades this batch replaced (`restore_certified`). The confirmation is checked here, not only in a dialog.
        data = body()
        if data.get("confirmation") != "restore":
            raise ValueError('Confirm with "restore" to put back the certified grades this batch replaced.')
        return jsonify(service.restore_certified(jid, data.get("request_id")))

    @api.post("/jobs/<jid>/cancel")
    def cancel(jid):
        # `seen`: the status the batch card showed, so a click that lands after the batch finished is refused (GRADING).
        seen = body().get("seen")
        return jsonify(service.cancel_job(jid, seen=seen if isinstance(seen, str) else None))

    @api.get("/jobs/<jid>/cloud-preview")
    def cloud_preview(jid):
        return jsonify(service.cloud_preview(jid))

    @api.post("/jobs/<jid>/retry")
    def retry(jid):
        body()
        return jsonify(service.retry_job(jid))

    @api.get("/jobs/<jid>/reset")
    def reset_outlook(jid):
        return jsonify(service.reset_outlook(jid))

    @api.post("/jobs/<jid>/reset")
    def reset_job(jid):
        return jsonify(service.reset_job(jid, body()))

    @api.post("/jobs/<jid>/reset/restore")
    def restore_job_reset(jid):
        return jsonify(service.restore_job_reset(jid, body()))

    @api.get("/assignments/<aid>/responses")
    def responses(aid):
        return jsonify(responses=service.responses(aid, request.args.get("question_id")))

    @api.get("/assignments/<aid>/review-queue")
    def review_queue(aid):
        return jsonify(service.review_queue(aid, request.args.get("job_id"), request.args.get("question_id") or None,
                                            review_details=request.args.get("details") == "review"))

    # Flag review (grading/flag_resolution.py): the rubric model rules on the held grades of each question, 25 at a time with their crops.
    # Queuing writes only the review's own record; the grades it settles are scored again by an ordinary batch of the plan.
    @api.get("/assignments/<aid>/flag-resolution")
    def flag_resolution(aid):
        return jsonify(service.flag_resolution(aid))

    @api.post("/assignments/<aid>/flag-resolution")
    def queue_flag_resolution(aid):
        return jsonify(service.queue_flag_resolution(aid, body()))

    @api.post("/assignments/<aid>/flag-resolution/<rid>/cancel")
    def cancel_flag_resolution(aid, rid):
        return jsonify(service.cancel_flag_resolution(aid, rid))

    @api.get("/assignments/<aid>/flag-resolution/<rid>/reset")
    def flag_reset_preview(aid, rid):
        return jsonify(service.flag_reset_preview(aid, rid))

    @api.post("/assignments/<aid>/flag-resolution/<rid>/reset")
    def reset_flag_resolution(aid, rid):
        return jsonify(service.reset_flag_resolution(aid, rid, body()))

    @api.get("/assignments/<aid>/metrics")
    def metrics(aid):
        return jsonify(service.metrics(aid))

    @api.get("/responses/<rid>")
    def response(rid):
        return jsonify(service.response(rid, request.args.get("reveal") == "1", review_details=request.args.get("details") == "review"))

    @api.post("/responses/<rid>/review")
    def review(rid):
        return jsonify(service.save_review(rid, body()))

    @api.post("/responses/<rid>/regrade")
    def regrade(rid):
        return jsonify(service.regrade_response(rid, body())), 201

    @api.post("/responses/<rid>/score")
    def preview_score(rid):
        return jsonify(service.preview_score(rid, body()))

    @api.post("/assignments/<aid>/approve")
    def approve_many(aid):
        return jsonify(responses=service.approve_many(aid, body()))

    @api.post("/assignments/<aid>/export")
    def export(aid):
        content, media, name = service.export(aid, body().get("kind", "audit"))
        if hasattr(content, "read"):
            return download(content, media, name)
        return Response(content, mimetype=media, headers={"Content-Disposition": f'attachment; filename="{name}"'})

    def download(stream, media, name):
        from werkzeug.wsgi import wrap_file
        response = Response(wrap_file(request.environ, stream), mimetype=media, headers={"Content-Disposition": f'attachment; filename="{name}"'})
        response.call_on_close(stream.close)
        return response

    # Graded work saved for training (`training.py`): what is saved, and a zip of it. Reading only; nothing is published.
    @api.get("/assignments/<aid>/training")
    def training_status(aid):
        return jsonify(service.training_status(aid))

    @api.get("/assignments/<aid>/training.zip")
    def training_download(aid):
        return download(*service.training_export(aid))

    @api.get("/training.zip")
    def training_download_all():
        return download(*service.training_export())

    app.register_blueprint(api)
