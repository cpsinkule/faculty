"""Who made the request being served, for the records that name an actor (requirement, 2026-10-01). The instructor's own pages and tools are `workspace`. The in-app assistant reaches the app through
its routes in-process (assistant/transport.py, `app.test_client()`), and its requests carry the WSGI environ key ENVIRON_KEY,
which no HTTP client can set (a server puts request headers under HTTP_* keys only): those are `assistant`."""
from flask import has_request_context, request

ENVIRON_KEY = "faculty.actor"
ASSISTANT = "assistant"
WORKSPACE = "workspace"


def current():
    """`assistant` while serving one of the assistant's own requests, else `workspace` (also outside a request)."""
    if has_request_context() and request.environ.get(ENVIRON_KEY) == ASSISTANT:
        return ASSISTANT
    return WORKSPACE
