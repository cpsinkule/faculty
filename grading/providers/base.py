from contextlib import contextmanager
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import http.client
import json
import socket
import subprocess
import ssl
import threading
import urllib.error
import urllib.request


# HTTP statuses that say only that something between Faculty and the model stopped waiting: 408 request timeout, 504 gateway
# timeout, and Cloudflare's 522 (the origin did not acknowledge) and 524 (the origin accepted and did not answer in time).
GATEWAY_TIMEOUTS = (408, 504, 522, 524)
STREAM_DURATION = "gateway_stream_timeout"  # Vercel AI Gateway's code for a stream it ended at its maximum duration.


class ProviderError(ValueError):
    """Safe public diagnostic; never includes provider bodies or credentials.

    `confirmed` says what is known about a request's outcome, which decides the cloud retry policy:
    True: the provider answered with an error for this request, or nothing was ever sent (refused,
    unresolvable). False: it was sent and nothing says whether it succeeded (dropped, timed out).
    None: not a transport outcome. `fixable` is False where a retry cannot help (no credits, bad key).
    `confirmation` is the provider's own status for the private call record, never for logs or the UI.
    """
    def __init__(self, message, *, raw=None, metadata=None, confirmed=None, status=None, retry_after=None, fixable=True, confirmation=None):
        super().__init__(message)
        self.raw, self.metadata = raw, metadata or {}
        self.confirmed, self.status, self.retry_after, self.fixable = confirmed, status, retry_after, fixable
        self.confirmation = confirmation or ({"status": status} if status else {})


class Cancelled(Exception):
    """The request's owner was cancelled (a batch, a rubric review, a rubric draft): its connection was closed, or it was never sent.
    Not a provider outcome, so no retry policy applies, and no optional step may swallow it as a failed opinion."""
    def __init__(self, message, *, sent=False):
        super().__init__(message)
        self.sent = sent


class Abort:
    """A request another thread may stop (requirement, 2026-09-23). A streamed answer is a blocking read with no timeout; shutting its socket ends that read at once, and a host that supports
    cancellation stops the model there. The socket is registered when the connection opens, in the thread that sends the request."""
    def __init__(self):
        self.stopped, self.sent, self.reason, self._sock, self._lock = False, False, "", None, threading.Lock()

    def attach(self, sock):
        with self._lock:
            self._sock, self.sent, stopped = sock, True, self.stopped
        if stopped:
            _shut(sock)

    def stop(self, reason):
        with self._lock:
            if self.stopped:
                return
            self.stopped, self.reason, sock = True, reason, self._sock
        if sock is not None:
            _shut(sock)

    def check(self):
        if self.stopped:
            raise Cancelled(self.reason, sent=self.sent)


def _shut(sock):
    if isinstance(sock, subprocess.Popen):
        # A Claude subscription request is a subprocess (`agent_sdk`), not a socket: it ends there, and so does the model's answer.
        try:
            sock.kill()
        except OSError:
            pass
        return
    try:
        # The plain socket's shutdown: an SSL socket's own would clear its state under the thread that is reading it.
        socket.socket.shutdown(sock, socket.SHUT_RDWR)
    except OSError:
        pass


_scope = threading.local()


@contextmanager
def abortable(abort):
    """Requests sent by this thread inside the block register their sockets with `abort`."""
    _scope.abort = abort
    try:
        yield
    finally:
        _scope.abort = None


def current_abort():
    return getattr(_scope, "abort", None)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


# A request sent without a timeout (hosted models, `cloud_call`) waits as long as the host keeps its connection open. TCP keepalive
# is what notices a connection that died silently (a dropped route or NAT entry): the host's own system answers these probes while
# its model is still writing, so a slow answer is never cut off, and a dead connection fails after about two minutes of silence.
KEEPALIVE = {"TCP_KEEPIDLE": 60, "TCP_KEEPINTVL": 15, "TCP_KEEPCNT": 4}


def keepalive(sock):
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
    for name, value in KEEPALIVE.items():
        if hasattr(socket, name):
            sock.setsockopt(socket.IPPROTO_TCP, getattr(socket, name), value)


class KeepAliveHTTPConnection(http.client.HTTPConnection):
    def connect(self):
        super().connect()
        keepalive(self.sock)
        if abort := current_abort():
            abort.attach(self.sock)


class KeepAliveHTTPSConnection(http.client.HTTPSConnection):
    def connect(self):
        super().connect()
        keepalive(self.sock)
        if abort := current_abort():
            abort.attach(self.sock)


class KeepAliveHTTPHandler(urllib.request.HTTPHandler):
    def http_open(self, req):
        return self.do_open(KeepAliveHTTPConnection, req)


class KeepAliveHTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, req):
        return self.do_open(KeepAliveHTTPSConnection, req, context=self._context)


def retry_after(headers):
    """Seconds from a Retry-After header (delta or HTTP date), bounded to an hour; None when absent or unreadable."""
    value = (headers.get("Retry-After") or "").strip() if headers else ""
    if not value:
        return None
    try:
        seconds = float(value)
    except ValueError:
        try:
            seconds = (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds()
        except (TypeError, ValueError):
            return None
    return min(3600.0, max(0.0, seconds))


def request_json(url, *, body=None, headers=None, timeout=180, content=None, limit=2 * 1024**2, raw=False):
    """`timeout` None waits for the answer however long the host takes to write it (see `KEEPALIVE`)."""
    if content is None:
        content = json.dumps(body, allow_nan=False).encode() if body is not None else None
    request = urllib.request.Request(url, data=content,
        headers={"Content-Type": "application/json", **(headers or {})})
    return _exchange(request, timeout, lambda response: _document(response, limit, raw))


def request_stream(url, *, body, headers=None, timeout=None, limit=8 * 1024**2, usage_option=True):
    """A chat completion asked for as a STREAM, then assembled into exactly the answer a non-streamed request returns.

    A gateway holding a non-streamed request open while the model writes may end it: Vercel's AI Gateway cuts non-streaming
    requests off at about 340 seconds and streaming ones at about 790 (seen 2026-09-23: 30 of 74 MiMo V2.6 Pro arbitrations of one
    trial batch died at 340 s, every resend too). A stream keeps the connection busy while the model writes. Nothing else changes:
    the request is the same apart from asking for a stream (with its usage, which carries the cost), and the answer is reassembled
    in the shape of a non-streamed completion, so parsing, receipts and costs read it as before. A host that ignores `stream` and
    answers with one JSON document is read as that document."""
    # `stream_options` asks an OpenAI-style host for the usage chunk. OpenRouter sends usage unasked, and under `require_parameters`
    # every field is a routing requirement, so it is not sent there (`usage_option` False).
    content = json.dumps({**body, "stream": True, **({"stream_options": {"include_usage": True}} if usage_option else {})}, allow_nan=False).encode()
    request = urllib.request.Request(url, data=content, method="POST",
        headers={"Content-Type": "application/json", "Accept": "text/event-stream", **(headers or {})})

    def read(response):
        if "text/event-stream" not in (response.headers.get("Content-Type") or ""):
            return _document(response, limit, False)
        return assemble_stream(response, limit)
    return _exchange(request, timeout, read)


def _document(response, limit, raw):
    payload = response.read(limit + 1)
    if len(payload) > limit:
        raise ProviderError("Provider output exceeds the limit.", confirmed=True, status=response.status)
    if raw:
        return payload
    value = json.loads(payload)
    if not isinstance(value, dict):
        raise ProviderError("Provider returned malformed JSON.", confirmed=True)
    return value


def assemble_stream(lines, limit=8 * 1024**2):
    """Server-sent chat completion chunks, joined into one completion: text and reasoning concatenated in order, reasoning details
    and other lists extended, the final finish reason and the usage (sent in the last chunk) kept, the first id and model kept.
    A stream that ends before the host finished its answer has an UNKNOWN outcome: the model may have run, and been billed.

    The limit applies to the ANSWER being assembled, as it does to a non-streamed document, never to the stream's packaging. A
    router repeats the chunk envelope (id, model, provider, finish fields) every few tokens and sends reasoning twice (as text and
    as details): OpenRouter's MiMo V2.6 Pro stream held about 375 bytes per three tokens, so counting the stream made 8 MB a hidden
    cap of about 70,000 tokens, and 12 calls of trial batch 5dee9aee (2026-09-23) were refused after 20 to 26 minutes of thinking
    and asked again from the start. One line beyond the limit is still refused before it is parsed."""
    top, message, finish, usage, size, done = {}, {}, None, None, 0, False
    calls, next_call_index = {}, 0
    for line in lines:
        if len(line) > limit:
            raise ProviderError("Provider output exceeds the limit.", confirmed=True)
        line = line.strip()
        if not line.startswith(b"data:"):
            continue  # Blank separators, ": keep-alive" comments, event and id fields.
        data = line[5:].strip()
        if data == b"[DONE]":
            done = True
            break
        chunk = json.loads(data)
        if not isinstance(chunk, dict):
            raise ProviderError("Provider returned malformed JSON.", confirmed=True)
        if chunk.get("error"):
            # The host answered for this request, with an error, after the stream began: a confirmed failure, like an error status.
            error = chunk["error"] if isinstance(chunk["error"], dict) else {"message": str(chunk["error"])}
            code = error.get("code") if type(error.get("code")) is int else None
            # A gateway that ended the stream at its own maximum duration (Vercel, about 13 minutes): the model was still writing. The
            # same request would most likely run as long again, and each attempt is billed, so it is not resent (requirement, 2026-09-27:
            # DeepSeek wrote 13 minutes on one Quiz 2 response it answers in 4 s elsewhere; a failure should show as failed and be retried
            # by the instructor). Like an answer cut off by its output budget, it stops at once.
            ran_out = error.get("code") == STREAM_DURATION or "maximum duration" in str(error.get("message") or "")
            raise ProviderError("The answer ran past the gateway's time limit before it finished; it is not resent automatically." if ran_out
                else f"The host reported an error while answering{f' (HTTP {code})' if code else ''}.", confirmed=code not in GATEWAY_TIMEOUTS,
                status=code, fixable=code not in (401, 402, 403) and not ran_out,
                confirmation={"status": code, **({"error": auth_detail(json.dumps({"error": error}))} if code in (401, 403) else {"body": json.dumps(error)[:2000]}),
                              **({"stream_duration": True} if ran_out else {})})
        for key, value in chunk.items():
            if key not in ("choices", "usage", "object") and value is not None:
                top.setdefault(key, value)
        if isinstance(chunk.get("usage"), dict):
            usage = chunk["usage"]
        for choice in chunk.get("choices") or []:
            if not isinstance(choice, dict) or choice.get("index", 0) != 0:
                continue
            for key, value in (choice.get("delta") or {}).items():
                if value is None:
                    continue
                size += len(value.encode()) if isinstance(value, str) else len(json.dumps(value).encode())
                if size > limit:
                    raise ProviderError("Provider output exceeds the limit.", confirmed=True)
                if isinstance(value, str) and key != "role":
                    message[key] = message.get(key, "") + value
                elif key == "tool_calls" and isinstance(value, list):
                    # A tool call arrives in pieces keyed by its index (the Faculty assistant's tool calling; grading sends no tools):
                    # the first piece names it, later ones continue its arguments.
                    message.setdefault("tool_calls", [])
                    for part in value:
                        if not isinstance(part, dict):
                            continue
                        index = part.get("index", next_call_index)
                        if type(index) is not int or index < 0:
                            raise ProviderError("Provider returned a malformed tool call index.", confirmed=True)
                        # A sparse provider index must not allocate a list of empty calls. Keep only actual calls, in their
                        # indexed order, so even a tiny malformed chunk cannot exhaust the worker's memory (PERFORMANCE).
                        next_call_index = max(next_call_index, index + 1)
                        call = calls.setdefault(index, {"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
                        call["id"] = part.get("id") or call["id"]
                        function = part.get("function") or {}
                        call["function"]["name"] = call["function"]["name"] or function.get("name") or ""
                        call["function"]["arguments"] += function.get("arguments") or ""
                elif isinstance(value, list):
                    message[key] = message.get(key, []) + value
                elif isinstance(value, dict):
                    message[key] = {**message.get(key, {}), **value}
                else:
                    message[key] = value
            if choice.get("finish_reason"):
                finish = choice["finish_reason"]
    if finish is None:
        raise ProviderError("The answer stream ended before the host finished it; the outcome of this request is unknown.", confirmed=False)
    if not done and usage is None:
        # Finished, yet cut before its closing usage: the answer is whole, its cost is not known.
        top["stream_note"] = "the stream closed before its usage chunk"
    if calls:
        message["tool_calls"] = [calls[index] for index in sorted(calls)]
    return {**top, "object": "chat.completion", "streamed": True,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "", **message}, "finish_reason": finish}], "usage": usage or {}}


def auth_detail(text):
    """OPENAI DECISIONS / credentials (2026-10-09): what a 401 or 403 body may keep in a receipt, the API's error `type` and `code`
    alone. The message is never kept: OpenAI's invalid-key 401 names the key in masked form."""
    try:
        error = json.loads(text).get("error")
    except (ValueError, AttributeError):
        return {}
    if not isinstance(error, dict):
        return {}
    return {key: str(error[key])[:80] for key in ("type", "code") if isinstance(error.get(key), (str, int)) and error.get(key) != ""}


def _exchange(request, timeout, read):
    """One request under the thread's `Abort`, if any: stopped before it leaves, nothing is sent; stopped while it runs, whatever
    the closed connection raised (an unfinished stream, a reset, a TLS error) is reported as the cancellation it was."""
    abort = current_abort()
    if abort is not None:
        abort.check()
    try:
        return _exchange_once(request, timeout, read)
    except Cancelled:
        raise
    except Exception:
        if abort is not None and abort.stopped:
            raise Cancelled(abort.reason, sent=abort.sent) from None
        raise


def _exchange_once(request, timeout, read):
    # No redirects or environment proxies here; prevents local-only calls from leaving the
    # host and credentials from following remote redirects. Retrying is the caller's policy.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect(), KeepAliveHTTPHandler(), KeepAliveHTTPSHandler())
    try:
        with opener.open(request, timeout=timeout) as response:
            return read(response)
    except urllib.error.HTTPError as error:
        # The provider answered for this exact request: a confirmed failure. 401/402/403 cannot be fixed by resending.
        # A TIMEOUT answered by a gateway is not the provider's answer: the request may have reached the model, run and been billed
        # behind it, so its outcome is unknown and it is resent only under the unknown-outcome policy (`unknown_resends`).
        message = "Authentication failed" if error.code in (401, 403) else "The account has no credits" if error.code == 402 else "Rate limited" if error.code == 429 \
            else "Endpoint timed out; the outcome of this request is unknown" if error.code in GATEWAY_TIMEOUTS else "Endpoint request failed"
        try:
            detail = error.read(2000).decode("utf-8", "replace")
        except OSError:
            detail = ""
        # A rejected credential's body may echo the key (masked): only its error type and code are kept (`auth_detail`).
        confirmation = {"status": error.code, "error": auth_detail(detail)} if error.code in (401, 403) else {"status": error.code, "body": detail}
        raise ProviderError(f"{message} (HTTP {error.code}).", confirmed=error.code not in GATEWAY_TIMEOUTS, status=error.code, retry_after=retry_after(error.headers),
            fixable=error.code not in (401, 402, 403), confirmation=confirmation) from None
    except urllib.error.URLError as error:
        reason = error.reason
        if isinstance(reason, (ConnectionRefusedError, socket.gaierror, ssl.SSLCertVerificationError)):
            # Refused or unresolvable before anything was sent: nothing can have been processed or charged.
            raise ProviderError("Endpoint refused the connection; nothing was sent.", confirmed=True, confirmation={"refused": type(reason).__name__}) from None
        raise ProviderError(f"Endpoint unavailable or timed out ({type(reason).__name__}); the outcome of this request is unknown.", confirmed=False) from None
    except (TimeoutError, socket.timeout, http.client.HTTPException, OSError) as error:
        # The exception's kind is kept: a connection the host closed (RemoteDisconnected) is not one that went silent (TimeoutError).
        raise ProviderError(f"Endpoint unavailable or timed out ({type(error).__name__}); the outcome of this request is unknown.", confirmed=False) from None
    except (json.JSONDecodeError, UnicodeError):
        raise ProviderError("Provider returned malformed JSON.", confirmed=True) from None
