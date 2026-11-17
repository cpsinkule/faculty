"""Explicit gateway authentication; email From headers never authorize commands."""
import hashlib
import hmac
import json
import os
import re
import time
from dataclasses import dataclass, field

from werkzeug.exceptions import Forbidden, ServiceUnavailable


def token_hash(value):
    return hashlib.sha256(value.encode()).hexdigest()


@dataclass
class EmailConfig:
    webhook_secret: str = ""
    command_secret: str = ""
    allowed_senders: set = field(default_factory=set)
    routes: dict = field(default_factory=dict)
    # Workspace-level submission capabilities, separate from authored commands.
    # Keys hash exact destinations. Public short aliases require review_only.
    intake_routes: dict = field(default_factory=dict)

    @classmethod
    def environment(cls):
        routes = json.loads(os.environ.get("TEACH_INBOX_EMAIL_ROUTES", "{}"))
        if not isinstance(routes, dict) or any(not isinstance(key, str) or not isinstance(value, str) for key, value in routes.items()):
            raise ValueError("TEACH_INBOX_EMAIL_ROUTES must map recipient addresses to actions.")
        intake = json.loads(os.environ.get("TEACH_INBOX_EMAIL_INTAKE", "{}"))
        if not isinstance(intake, dict):
            raise ValueError("TEACH_INBOX_EMAIL_INTAKE must map address hashes to intake policies.")
        for digest, item in intake.items():
            if (not re.fullmatch(r"[a-f0-9]{64}", digest) or not isinstance(item, dict)
                    or set(item) - {"label", "senders", "review_only"}
                    or not {"label", "senders"} <= set(item)
                    or ("review_only" in item and type(item["review_only"]) is not bool)
                    or not isinstance(item["label"], str)
                    or not 1 <= len(item["label"]) <= 80 or not isinstance(item["senders"], list)
                    or not item["senders"] or any(not isinstance(s, str) or not re.fullmatch(r"[^\s@]+@[^\s@]+", s) for s in item["senders"])):
                raise ValueError("Each intake policy needs an address hash, label, and nonempty sender list.")
            item["senders"] = [s.lower() for s in item["senders"]]
        return cls(os.environ.get("TEACH_INBOX_WEBHOOK_SECRET", ""),
                   os.environ.get("TEACH_INBOX_EMAIL_COMMAND_SECRET", ""),
                   {value.strip().lower() for value in os.environ.get("TEACH_INBOX_EMAIL_SENDERS", "").split(",") if value.strip()},
                   {key.lower(): value for key, value in routes.items()}, intake)

    @property
    def enabled(self):
        return len(self.webhook_secret) >= 32 and (bool(self.intake_routes) or self.commands_enabled)

    @property
    def commands_enabled(self):
        return len(self.command_secret) >= 32 and bool(self.allowed_senders and self.routes)

    def intake_policy(self, recipient):
        return self.intake_routes.get(token_hash(recipient.lower()))

    def intake_allowed(self, policy, sender, envelope_sender):
        # Sender checks are filters, not identity proof. Public aliases additionally
        # require an instructor workspace review before any handler can execute.
        return bool(policy and sender.lower() in policy["senders"] and
                    envelope_sender.lower() in policy["senders"])

    def verify_webhook(self, raw, headers):
        if not self.enabled:
            raise ServiceUnavailable("Email delivery is not configured. Use the Teaching Inbox upload or API.")
        stamp, delivery = headers.get("X-Teaching-Timestamp", ""), headers.get("X-Teaching-Delivery", "")
        signature = headers.get("X-Teaching-Signature", "")
        if not re.fullmatch(r"[0-9]{10,12}", stamp) or abs(time.time() - int(stamp)) > 300:
            raise Forbidden("Invalid or expired email gateway signature.")
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,160}", delivery):
            raise Forbidden("A valid signed delivery ID is required.")
        expected = hmac.new(self.webhook_secret.encode(), stamp.encode() + b"." + delivery.encode() + b"." + raw, hashlib.sha256).hexdigest()
        if not re.fullmatch(r"[0-9a-f]{64}", signature) or not hmac.compare_digest(signature, expected):
            raise Forbidden("Invalid email gateway signature.")
        return delivery

    def sender_allowed(self, sender, secret):
        return self.commands_enabled and sender.lower() in self.allowed_senders and isinstance(secret, str) and hmac.compare_digest(secret.encode(), self.command_secret.encode())
