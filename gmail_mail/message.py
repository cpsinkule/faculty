"""Reading and writing mail without touching Google: a Gmail thread as the page shows it, and the message Faculty sends.

Everything read from Gmail is untrusted text: bodies become plain text (HTML is reduced to its text, never shown as HTML), control
characters go, lengths are bounded, and the page escapes what it draws. Nothing here keeps a copy of a message.
"""
import base64
import hashlib
import re
from datetime import datetime, timezone
from email import policy
from email.message import EmailMessage
from email.utils import formataddr, getaddresses
from html import unescape
from html.parser import HTMLParser

from canonical_json import digest as fingerprint  # noqa: F401 - the approved-send fingerprint (gmail_mail.service reads it as mail.fingerprint)
from response_cards.mail import valid_address

MAX_BODY = 60000
MAX_RECIPIENTS = 200
MAX_SUBJECT, MAX_TEXT = 300, 20000
CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
LINE_BREAKS = re.compile(r"[\r\n  ]")


def clean_text(value, limit=MAX_BODY):
    return CONTROL.sub("", str(value or "")).replace("\r\n", "\n").replace("\r", "\n")[:limit]


def header(payload, name):
    """The first header of a message part named `name`, case-insensitively, or ''."""
    for item in (payload or {}).get("headers") or []:
        if isinstance(item, dict) and str(item.get("name", "")).casefold() == name.casefold():
            return clean_text(item.get("value"), 2000).replace("\n", " ").strip()
    return ""


def people(value):
    """[{name, email}] from an address header; entries that are not addresses keep their text with an empty email."""
    found = []
    for name, address in getaddresses([value or ""]):
        if address and "@" in address:
            found.append({"name": clean_text(name, 200).strip(), "email": address.strip()})
        elif name or address:
            found.append({"name": clean_text(name or address, 200).strip(), "email": ""})
    return found[:50]


def _decode(data):
    try:
        return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", "replace")
    except (ValueError, TypeError):
        return ""


class _Text(HTMLParser):
    BLOCKS = {"p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "table"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "head"):
            self.skip += 1
        elif tag in self.BLOCKS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "head"):
            self.skip = max(0, self.skip - 1)
        elif tag in self.BLOCKS:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def html_text(markup):
    parser = _Text()
    try:
        parser.feed(markup)
        parser.close()
    except Exception:  # noqa: BLE001 - a page that will not parse is shown as the text it does give
        pass
    lines = [" ".join(line.split()) for line in "".join(parser.parts).split("\n")]
    collapsed, blank = [], 0
    for line in lines:
        blank = 0 if line else blank + 1
        if blank < 2:
            collapsed.append(line)
    return "\n".join(collapsed).strip()


def body_and_files(part):
    """(plain text, [attachments]) of one message payload: text/plain when there is any, else the text of the HTML."""
    plain, markup, files = [], [], []

    def walk(node):
        if not isinstance(node, dict):
            return
        mime, name = str(node.get("mimeType") or ""), str(node.get("filename") or "")
        body = node.get("body") or {}
        if name:
            # `part` names it for GET /api/mail/messages/<id>/parts/<part>, which fetches it from Gmail when it is opened.
            files.append({"name": clean_text(name, 200), "type": clean_text(mime, 100), "size": int(body.get("size") or 0),
                          "part": str(node.get("partId") or "")})
        elif mime == "text/plain" and body.get("data"):
            plain.append(_decode(body["data"]))
        elif mime == "text/html" and body.get("data"):
            markup.append(_decode(body["data"]))
        for child in node.get("parts") or []:
            walk(child)

    walk(part)
    text = "\n\n".join(plain).strip() or html_text("\n".join(markup))
    return clean_text(text), files[:30]


def instant(millis):
    try:
        return datetime.fromtimestamp(int(millis) / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError, OverflowError, OSError):
        return ""


def summary(thread, me=""):
    """A thread's row in the list, from its metadata: subject, who, when, how many messages, unread."""
    messages = [m for m in thread.get("messages") or [] if isinstance(m, dict)]
    if not messages:
        return {"id": str(thread.get("id") or ""), "subject": "(no messages)", "count": 0}
    first, last = messages[0], messages[-1]
    senders = []
    for message in messages:
        for person in people(header(message.get("payload"), "From")):
            label = "me" if person["email"].casefold() == me.casefold() and me else (person["name"] or person["email"] or "?")
            if label not in senders:
                senders.append(label)
    unread = any("UNREAD" in (m.get("labelIds") or []) for m in messages)
    # MAIL-SUMMARY: Gmail snippets carry HTML character references; decode once
    # before bounding plain text. The page still escapes it when rendering.
    snippet = unescape(str(last.get("snippet") or thread.get("snippet") or ""))
    return {"id": str(thread.get("id")), "subject": header(first.get("payload"), "Subject") or "(no subject)",
            "from": senders[:4], "snippet": clean_text(snippet, 300),
            "at": instant(last.get("internalDate")), "count": len(messages), "unread": unread,
            "sent": all("SENT" in (m.get("labelIds") or []) for m in messages)}


def read_thread(thread, me=""):
    """A whole thread for reading: every message with its people, time, text and attachment names, and whether it is in the Inbox."""
    messages = []
    for message in thread.get("messages") or []:
        if not isinstance(message, dict):
            continue
        payload = message.get("payload")
        text, files = body_and_files(payload)
        item = {"id": str(message.get("id") or ""), "from": people(header(payload, "From")), "to": people(header(payload, "To")),
                "cc": people(header(payload, "Cc")), "at": instant(message.get("internalDate")), "text": text, "files": files,
                "mine": bool(me) and any(p["email"].casefold() == me.casefold() for p in people(header(payload, "From"))),
                "unread": "UNREAD" in (message.get("labelIds") or [])}
        reply_to = people(header(payload, "Reply-To"))
        if reply_to:  # only then, so a thread without one reads as it always did (MAIL-REPLY-TO)
            item["reply_to"] = reply_to
        messages.append(item)
    first = (thread.get("messages") or [{}])[0]
    # In the Inbox while any of its messages is (Gmail shows the conversation there then); the page offers Archive or Move to Inbox.
    inbox = any(isinstance(m, dict) and "INBOX" in (m.get("labelIds") or []) for m in thread.get("messages") or [])
    return {"id": str(thread.get("id") or ""), "subject": header(first.get("payload"), "Subject") or "(no subject)", "messages": messages,
            "inbox": inbox}


def reply_context(thread, me):
    """What a reply to the thread needs: the message it answers, its Message-ID and References, who to write to, the subject.
    It answers the latest message not written by the instructor (else the latest one)."""
    messages = [m for m in thread.get("messages") or [] if isinstance(m, dict)]
    if not messages:
        raise ValueError("That thread has no messages to reply to.")

    def mine(message):
        return any(p["email"].casefold() == me.casefold() for p in people(header(message.get("payload"), "From")))

    target = next((m for m in reversed(messages) if not mine(m)), messages[-1])
    payload = target.get("payload")
    reply_to = [p["email"] for p in people(header(payload, "Reply-To")) if p["email"] and valid_address(p["email"])]
    recipients = people(header(payload, "Reply-To")) or people(header(payload, "From"))
    # MAIL-REPLY-TO: a sender that names a Reply-To (Canvas: From notifications@instructure.com, a no-reply address that drops mail
    # unanswered; Reply-To reply+…@notifications.canvaslms.com) is answered there, even when the page addressed its From.
    redirect = {}
    if reply_to and not mine(target):
        redirect = {p["email"].casefold(): reply_to for p in people(header(payload, "From"))
                    if p["email"] and p["email"].casefold() not in {a.casefold() for a in reply_to}}
    if mine(target):
        recipients = people(header(payload, "To"))
    subject = header(messages[0].get("payload"), "Subject") or ""
    message_id, references = header(payload, "Message-ID") or header(payload, "Message-Id"), header(payload, "References")
    return {"message_id": str(target.get("id") or ""), "rfc_id": message_id if re.fullmatch(r"<[^<>\s]{1,300}>", message_id) else "",
            "sender": [] if mine(target) else people(header(payload, "From")),
            "text": "" if mine(target) else body_and_files(payload)[0][:MAX_TEXT],
            "references": " ".join(part for part in references.split() if re.fullmatch(r"<[^<>\s]{1,300}>", part))[:1500],
            "to": [p["email"] for p in recipients if p["email"] and valid_address(p["email"])], "redirect": redirect,
            "subject": subject if subject.casefold().startswith("re:") else f"Re: {subject}".strip()}


CANVAS_SUBJECT = re.compile(r"^\s*Subject:\s*(\S.*?)\s*$", re.M)


def canvas_subject(text):
    """The subject the student gave their Canvas message: Canvas's notification text opens with a "Subject: …" line. '' when absent."""
    found = CANVAS_SUBJECT.search((text or "")[:2000])
    return " ".join(found.group(1).split())[:200] if found else ""


CANVAS_CONVERSATION = re.compile(r"https?://([A-Za-z0-9.-]+)(?::\d{1,5})?/conversations/(\d{1,20})\b")


def canvas_conversation(text, host):
    """The Canvas conversation a message notification links to ("…by clicking this link: https://canvas.tamu.edu/conversations/N"),
    on the Canvas host Faculty uses; '' when it names none, or several."""
    found = {number for at, number in CANVAS_CONVERSATION.findall(text or "") if host and at.casefold() == host.casefold()}
    return found.pop() if len(found) == 1 else ""


def canvas_plan(data, sender, thread_id, post):
    """A reply posted in a Canvas conversation (MAIL-CANVAS-POST): the words only, checked as an email's are; no addresses, files or
    Gmail headers. `post` names the conversation, the student it answers and their message, so the fingerprint covers them."""
    subject, text = data.get("subject"), data.get("body")
    if not isinstance(subject, str) or not isinstance(text, str):
        raise ValueError("Write a subject and a message.")
    subject = " ".join(LINE_BREAKS.sub(" ", subject).split())[:MAX_SUBJECT]
    text = CONTROL.sub("", text).replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        raise ValueError("Write the message.")
    if len(text) > MAX_TEXT:
        raise ValueError(f"The message can be at most {MAX_TEXT:,} characters.")
    return {"via": "canvas", "to": [], "cc": [], "bcc": [], "subject": subject, "text": text, "sender": sender, "thread_id": thread_id,
            "in_reply_to": "", "references": "", "canvas": dict(post)}


def redirect_reply(data, context):
    """The page's fields with any address the reply's sender asked not to be answered at replaced by its Reply-To (MAIL-REPLY-TO), and
    the replacements made as [(from, [reply-to…])]. A page opened before this rule (or anything else) that addressed the From is
    corrected here, where the server reads the thread itself."""
    redirect, made = context.get("redirect") or {}, []
    if not redirect:
        return data, made
    fields = dict(data)
    for field, label in (("to", "To"), ("cc", "Cc"), ("bcc", "Bcc")):
        if data.get(field) in (None, ""):
            continue
        kept = []
        for address in addresses(data.get(field), label):
            replacement = redirect.get(address.casefold())
            if replacement and (address, replacement) not in made:
                made.append((address, replacement))
            kept += [each for each in replacement or [address] if each.casefold() not in {k.casefold() for k in kept}]
        fields[field] = kept
    return fields, made


# -- Writing ---------------------------------------------------------------------------------------------------------------
def addresses(value, label):
    """A list of addresses from a list or from text separated by commas, semicolons or lines; each checked, duplicates dropped."""
    if value in (None, ""):
        return []
    if isinstance(value, str):
        value = [part for part in re.split(r"[,;\n]", value)]
    if not isinstance(value, list):
        raise ValueError(f"{label} must be a list of addresses.")
    kept, seen = [], set()
    for item in value:
        if not isinstance(item, str):
            raise ValueError(f"{label} must hold addresses only.")
        item = item.strip()
        if not item:
            continue
        match = re.fullmatch(r"[^<>]*<([^<>]+)>", item)
        address = match.group(1).strip() if match else item
        if not valid_address(address):
            raise ValueError(f"{label}: “{item[:80]}” does not look like an email address.")
        if address.casefold() not in seen:
            seen.add(address.casefold())
            kept.append(address)
    return kept


def plan(data, sender, reply=None, attachments=()):
    """The message exactly as it will go, from the page's fields (and the reply context the SERVER read, never the browser's).
    Returns {to, cc, bcc, subject, text, thread_id, in_reply_to, references, sender}, and `attachments` (each file's route, name,
    type, size and SHA-256, as the server read it) when there are any. Raises ValueError naming what is wrong."""
    to, cc, bcc = addresses(data.get("to"), "To"), addresses(data.get("cc"), "Cc"), addresses(data.get("bcc"), "Bcc")
    subject = data.get("subject")
    text = data.get("body")
    if not isinstance(subject, str) or not isinstance(text, str):
        raise ValueError("Write a subject and a message.")
    subject = " ".join(LINE_BREAKS.sub(" ", subject).split())
    text = CONTROL.sub("", text).replace("\r\n", "\n").replace("\r", "\n").strip()
    if not subject:
        raise ValueError("Give the message a subject.")
    if len(subject) > MAX_SUBJECT:
        raise ValueError(f"The subject can be at most {MAX_SUBJECT} characters.")
    if not text:
        raise ValueError("Write the message.")
    if len(text) > MAX_TEXT:
        raise ValueError(f"The message can be at most {MAX_TEXT:,} characters.")
    everyone = to + cc + bcc
    if not everyone:
        raise ValueError("Add at least one recipient.")
    if len({a.casefold() for a in everyone}) != len(everyone):
        raise ValueError("An address is listed more than once across To, Cc and Bcc.")
    if len(everyone) > MAX_RECIPIENTS:
        raise ValueError(f"A message can go to at most {MAX_RECIPIENTS} addresses.")
    message = {"to": to, "cc": cc, "bcc": bcc, "subject": subject, "text": text, "sender": sender,
               "thread_id": (reply or {}).get("thread_id", ""), "in_reply_to": (reply or {}).get("rfc_id", ""),
               "references": (reply or {}).get("references", "")}
    if attachments:  # only then, so a message without files keeps the fingerprint it always had
        message["attachments"] = [dict(item) for item in attachments]
    return message


COMPOSE_POLICY = policy.default.clone(refold_source="none")  # headers stored raw (the message IDs) keep the lines given them


def compose(message, sender_name, contents=()):
    """The RFC 822 bytes of a planned message. With only Bcc recipients the message is addressed to the sender, so nobody sees a list.
    `contents` are the attachments' bytes, in the order of `message["attachments"]`, each checked against its reviewed digest."""
    mail = EmailMessage(policy=COMPOSE_POLICY)
    mail["From"] = formataddr((sender_name, message["sender"]))
    mail["To"] = ", ".join(message["to"]) if message["to"] else message["sender"]
    if message["cc"]:
        mail["Cc"] = ", ".join(message["cc"])
    if message["bcc"]:
        mail["Bcc"] = ", ".join(message["bcc"])
    mail["Subject"] = message["subject"]
    if message["in_reply_to"]:
        # Message IDs go as written, one per line: folded by the email package, an ID longer than a line (Canvas's are) became an
        # RFC 2047 encoded word, which no mail program reads as a Message-ID. They were checked as <…> without spaces in reply_context.
        mail.set_raw("In-Reply-To", message["in_reply_to"])
        mail.set_raw("References", "\n ".join((message["references"] + " " + message["in_reply_to"]).split()))
    mail.set_content(message["text"])
    files = message.get("attachments") or []
    if len(files) != len(contents):
        raise ValueError("The attachments do not match the reviewed message.")
    for meta, content in zip(files, contents):
        if hashlib.sha256(content).hexdigest() != meta["sha256"]:
            raise ValueError(f"{meta['name']} changed since it was reviewed.")
        maintype, _, subtype = meta["type"].partition("/")
        if maintype == "message" or not subtype:  # a message/* part may not be base64 (RFC 2046): a kept .eml goes as a plain file
            maintype, subtype = "application", "octet-stream"
        mail.add_attachment(content, maintype=maintype, subtype=subtype, filename=meta["name"])
    return mail.as_bytes()
