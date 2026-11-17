"""Canonical JSON text and its SHA-256, behind idempotency keys, receipts and approval fingerprints.

Stored hashes are compared on a retry, so the bytes of each flavour never change. `encoded` and `encoded_strict` write UTF-8
text (ensure_ascii=False); `encoded_strict` also refuses NaN and Infinity. Never merge the flavours: NaN is written by one and
refused by the other, and non-ASCII text hashes differently from the ASCII-escaped flavour (`ascii_digest`).
"""

import hashlib
import json


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def encoded_strict(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def digest(value):
    return hashlib.sha256(encoded(value).encode()).hexdigest()


def ascii_digest(value, default=None):
    """SHA-256 of canonical JSON with ensure_ascii left True: non-ASCII text is \\u-escaped. Stored fingerprints and approvals
    (department-makeup request hashes, preview fingerprints, google_calendar content_sha256) were computed this way, so keep it
    apart from `digest`. `default=str` is what lets dates, Decimals and Paths through; None refuses them."""
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=default).encode()).hexdigest()


def digest_strict(value):
    """Bytes are hashed as they are, anything else through `encoded_strict`."""
    return hashlib.sha256(value if isinstance(value, bytes) else encoded_strict(value).encode()).hexdigest()
