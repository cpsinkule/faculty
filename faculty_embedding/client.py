"""Faculty's side of the embedding worker: ask which kept texts are nearest a request, and never wait long or fail for it.

Any trouble (no worker, a slow answer, a refusal) is `EmbeddingUnavailable`; the caller falls back to keywords
(ASSISTANT-MEMORY). Faculty asks only when FACULTY_EMBEDDING_SOCKET names the socket (the service's drop-in sets it), so a
check, a script or a second copy of the app never reaches the live worker by default.

`gpu_lent` is the other thing Faculty asks: the whole card for one request to the local grading model (EMBEDDING "LENDS THE
GPU"). It never fails its caller either: with no worker named, none running or one that refuses, the caller just goes ahead.
"""
import json
import os
import socket
from contextlib import contextmanager

from .config import DIMENSION, MAX_CHARS, MAX_REQUEST, MAX_TEXTS, PICTURE_PIECES, socket_path

TIMEOUT = 1.5  # seconds for one answer; a cold cache may take longer once, and that request uses keywords
# Seconds to wait for the card to change hands: the worker finishes the request in hand before it moves off, and watches the
# card for room (worker.RETURN_WAIT) before it moves back on.
LEND_WAIT, RETURN_WAIT = 30, 25


class EmbeddingUnavailable(RuntimeError):
    pass


class PictureRefused(EmbeddingUnavailable):
    """The worker answered, and the trouble is the picture itself (unreadable, the wrong kind, too large)."""


class CardLent(EmbeddingUnavailable):
    """The worker answered, and the trouble is nobody's: it has lent the GPU to the local grading model and will take it back.
    It answers a status request all the while, so a caller that keeps an index waits and blames no picture."""


class OtherSetting(EmbeddingUnavailable):
    """The worker answered, and sees pictures at another setting than this code keeps their vectors under (a worker started
    before the setting changed). Kept, its vector would be compared with vectors it cannot be compared with, so it is not
    taken: a caller that keeps an index waits, as for a lent card, and blames no picture."""


def configured():
    return bool(os.environ.get("FACULTY_EMBEDDING_SOCKET"))


def ask(payload, timeout=TIMEOUT, path=None):
    """One request to the worker; its decoded reply."""
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as link:
            link.settimeout(timeout)
            link.connect(str(path or socket_path()))
            link.sendall(json.dumps(payload).encode() + b"\n")
            chunks, size = [], 0
            while True:
                chunk = link.recv(65536)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_REQUEST:
                    raise ValueError("reply exceeds limit")
                chunks.append(chunk)
                if chunk.endswith(b"\n"):
                    break
        reply = json.loads(b"".join(chunks))
    except (OSError, ValueError) as failure:
        raise EmbeddingUnavailable("The embedding worker did not answer: " + type(failure).__name__) from None
    if not isinstance(reply, dict):
        raise EmbeddingUnavailable("The embedding worker sent no answer.")
    if reply.get("error"):
        kind = PictureRefused if reply.get("picture") is True else CardLent if reply.get("lent") is True else EmbeddingUnavailable
        raise kind(str(reply["error"]))
    return reply


def nearest(query, texts, timeout=TIMEOUT, path=None, wait=0):
    """Indexes of `texts` nearest `query` first. `wait`: seconds this request may be held by a worker that has lent the GPU
    (EMBEDDING "LENDS THE GPU") before it is refused as `CardLent`; only work nobody is watching asks for that."""
    if not texts:
        return []
    if len(texts) > MAX_TEXTS:
        raise EmbeddingUnavailable("Too many texts for one request.")
    reply = ask({"op": "nearest", "query": query[:MAX_CHARS], "texts": [text[:MAX_CHARS] for text in texts], **({"wait": wait} if wait else {})},
                timeout + wait, path)
    order = reply.get("order")
    if not isinstance(order, list) or any(type(index) is not int for index in order) or sorted(order) != list(range(len(texts))):
        raise EmbeddingUnavailable("The embedding worker sent an incomplete order.")
    return order


def _unpacked(reply, expected):
    import base64
    import numpy as np
    try:
        matrix = np.frombuffer(base64.b64decode(reply["vectors"]), dtype=np.float32).reshape(reply["count"], reply["dimension"])
    except (KeyError, TypeError, ValueError):
        raise EmbeddingUnavailable("The embedding worker sent no vectors.") from None
    if matrix.shape != (expected, DIMENSION) or not np.isfinite(matrix).all():
        raise EmbeddingUnavailable("The embedding worker sent incomplete vectors.")
    return matrix


def text_vectors(texts, role="query", timeout=TIMEOUT, path=None):
    """Unit float32 vectors of `texts`, one row each: a request ('query') or kept text ('document')."""
    return _unpacked(ask({"op": "vectors", "role": role, "texts": [text[:MAX_CHARS] for text in texts]}, timeout, path), len(texts))


def picture_vectors(pictures, timeout=20, path=None):
    """Unit float32 vectors of PNG or JPEG `pictures` (bytes), one row each. The worker decodes them, in its jail."""
    import base64
    reply = ask({"op": "picture_vectors", "pictures": [base64.b64encode(data).decode() for data in pictures]}, timeout, path)
    if reply.get("pieces") != PICTURE_PIECES:
        raise OtherSetting("The embedding worker sees pictures at another setting. Restart it.")
    return _unpacked(reply, len(pictures))


def status(timeout=TIMEOUT, path=None):
    return ask({"op": "status"}, timeout, path)


def lend(seconds, timeout=LEND_WAIT, path=None):
    """Ask the worker to give the GPU up for at most `seconds`. True when it did; False when it holds none (a CPU worker)."""
    return ask({"op": "lend", "seconds": seconds}, timeout, path).get("lent") is True


def take_back(timeout=RETURN_WAIT, path=None):
    """Tell the worker its loan is over. A worker that finds no room on the card yet answers with a refusal and takes the
    card back by itself later."""
    ask({"op": "take_back"}, timeout, path)


@contextmanager
def gpu_lent(seconds, path=None):
    """Around one request to a local model that needs the whole card: the embedding worker moves off the GPU first and takes
    it back afterwards, whatever became of the request. `seconds` outlasts the request, so a caller that stops midway costs
    the worker no more than that. Yields whether the card was lent."""
    lent = False
    if path or configured():
        try:
            lent = lend(seconds, path=path)
        except EmbeddingUnavailable:
            pass  # no worker, or one that would not: the request goes ahead as it always did
    try:
        yield lent
    finally:
        if lent:
            try:
                take_back(path=path)
            except EmbeddingUnavailable:
                pass  # the worker takes the card back by itself: on a later request, or when the loan runs out


class Embedder:
    """What `AssistantMemory.embedder` holds: `nearest(query, texts)` -> indexes, nearest first, or EmbeddingUnavailable."""
    def nearest(self, query, texts, wait=0):
        return nearest(query, texts, wait=wait)
