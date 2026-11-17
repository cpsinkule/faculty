"""Private administration only; student routes live exclusively on Cloudflare."""
from functools import partial
from html import escape

from flask import Blueprint, jsonify, redirect, request

from request_limits import json_object

from .gmail import RETURNS, DeliveryError


def register(app, service):
    api = Blueprint('response_cards', __name__, url_prefix='/api/response-cards')

    body = partial(json_object, "Send a JSON object.")

    def gmail_page(message, purpose='response-cards'):
        # One callback serves every sign-in (Google has one registered redirect): the page names the one that failed.
        what, back, label = ('Google Calendar', RETURNS['calendar'], 'Back to the Calendar') if purpose == 'calendar' else \
            ('Google Drive', RETURNS['drive'], 'Back to Follow-up') if purpose == 'drive' else \
            ('Gmail', RETURNS['mail'], 'Back to Mail') if purpose in ('mail', 'mail-manage') else \
            ('Gmail', RETURNS['response-cards'], 'Back to Response Cards')
        return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><title>{what} connection</title></head>'
                f'<body><main><h1>{what} was not connected</h1><p>{escape(message)}</p>'
                f'<p><a href="{back}">{label}</a></p></main></body></html>'), 400, {'Content-Type': 'text/html; charset=utf-8'}

    @api.get('/mail')
    def mail_status():
        return jsonify(service.mail.status())

    @api.post('/mail/test')
    def mail_test():
        mail_provider = body().get('mail_provider', 'default')
        service.mail.resolve_provider(mail_provider)
        recipient = service.mail.gmail.email if mail_provider == 'gmail' else service.mail.config.reply_to
        try:
            return jsonify(recipient=recipient, receipt=service.mail.send_test(recipient, mail_provider=mail_provider))
        except DeliveryError as error:
            return jsonify(error=str(error)), 503

    @api.post('/gmail/connect')
    def gmail_connect():
        body()
        return jsonify(url=service.mail.gmail.start(service.mail.config.reply_to))

    @api.get('/gmail/callback')
    def gmail_callback():
        # Google's redirect carries no workspace header; the stored state binds it to a sign-in this app started. The Calendar's
        # Connect (google_calendar/) uses this same registered redirect and returns to the Calendar.
        purpose = service.mail.gmail.pending_purpose()
        if request.args.get('error'):
            return gmail_page(f"Google refused the sign-in: {request.args['error'][:200]}", purpose)
        try:
            result = service.mail.gmail.finish(request.args.get('state'), request.args.get('code'))
        except ValueError as error:
            return gmail_page(str(error), purpose)
        if result.get('purpose') == 'drive' and app.extensions.get('communications'):
            # LINKED DRIVE FILES (COMMUNICATIONS): the files that waited on this permission are fetched in a Gmail round asked for now.
            app.extensions['communications'].request({'channels': ['gmail']})
        return redirect(RETURNS.get(result.get('purpose'), RETURNS['response-cards']))

    @api.post('/gmail/disconnect')
    def gmail_disconnect():
        body()
        return jsonify(service.mail.gmail.disconnect())

    @api.get('/context')
    def context():
        return jsonify(service.context(request.args.get('term_id')))

    @api.get('/templates')
    def templates():
        return jsonify(templates=service.templates(request.args.get('definition_id')))

    @api.post('/templates')
    def create():
        return jsonify(service.save_template(body())), 201

    @api.get('/templates/<key>')
    def template(key):
        return jsonify(service.template(key))

    @api.put('/templates/<key>')
    def update(key):
        return jsonify(service.save_template(body(), key))

    @api.post('/templates/<key>/versions')
    def freeze(key):
        return jsonify(service.freeze(key, body().get('version'))), 201

    @api.get('/issues')
    def issues():
        return jsonify(issues=service.issues(request.args.get('term_id')))

    @api.post('/issues')
    def issue():
        return jsonify(service.issue(body())), 201

    @api.get('/attention')
    def attention():
        return jsonify(service.attention())

    @api.get('/issues/<key>')
    def detail(key):
        return jsonify(service.get_issue(key))

    @api.post('/issues/<key>/publish')
    def publish(key):
        body()
        return jsonify(service.publish(key))

    @api.post('/issues/<key>/send')
    def send(key):
        return jsonify(service.send_invitations(key, mail_provider=body().get('mail_provider', 'default')))

    @api.post('/sync')
    def sync():
        body()
        return jsonify(service.sync())

    @api.post('/cards/<key>/control')
    def control(key):
        return jsonify(service.control(key, body()))

    @api.get('/cards/<key>/link')
    def link(key):
        return jsonify(service.link(key))

    @api.post('/cards/<key>/resend')
    def resend(key):
        return jsonify(service.resend(key, mail_provider=body().get('mail_provider', 'default')))

    app.register_blueprint(api)
