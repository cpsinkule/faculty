"""JEV: status is a read; explicit conversation retries remain ordinary guarded writes."""
from flask import Blueprint, jsonify, request


def register(app, service, triage):
    api = Blueprint('jev', __name__, url_prefix='/api/jev')

    @api.get('/status')
    def status():
        return jsonify(service.status())

    @api.post('/triage/retry')
    def retry():
        data = request.get_json()
        if not isinstance(data, dict):
            raise ValueError('Supply the conversation to retry.')
        return jsonify(triage.retry(data))

    # JEV HOST FAILURES (2026-10-09): "Retry all failed", the instructor's own click (assistant/policy.py NEVER).
    @api.post('/triage/retry-failed')
    def retry_failed():
        data = request.get_json()
        if not isinstance(data, dict):
            raise ValueError('Supply the retry request ID.')
        return jsonify(triage.retry_failed(data))

    app.register_blueprint(api)
