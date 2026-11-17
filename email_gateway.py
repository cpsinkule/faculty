"""Narrow public listener: only the signed email endpoint reaches Faculty.

The instructor app continues to run separately on its private listener. This
wrapper rejects other requests before Flask, including static files and APIs.
"""
import os

from app import create_app
from teach_inbox.protocol import MAX_REQUEST


class EmailOnly:
    def __init__(self, application):
        self.application = application

    def __call__(self, environ, start_response):
        if (environ.get("REQUEST_METHOD") != "POST" or
                environ.get("PATH_INFO") != "/api/inbox/email" or
                environ.get("QUERY_STRING")):
            start_response("404 Not Found", [("Content-Type", "text/plain"),
                           ("Content-Length", "10"), ("Cache-Control", "no-store")])
            return [b"Not found\n"]
        return self.application(environ, start_response)


if __name__ == "__main__":
    from waitress import serve
    serve(EmailOnly(create_app()), host="127.0.0.1",
          port=int(os.environ.get("EMAIL_GATEWAY_PORT", "3001")), threads=2,
          max_request_body_size=MAX_REQUEST, channel_timeout=60)
