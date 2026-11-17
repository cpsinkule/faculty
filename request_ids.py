"""The shape of a request ID, the idempotency key a page sends with a write (every service keeps its own receipts by it)."""
import re

REQUEST = re.compile(r"[A-Za-z0-9._:-]{1,100}")


def valid_request_id(value):
    if not isinstance(value, str) or not REQUEST.fullmatch(value):
        raise ValueError("Send a request ID.")
    return value
