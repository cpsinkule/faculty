"""The embedding worker: one long-lived, offline process that keeps the pinned model loaded and answers "which of these texts
are nearest this one" over a Unix socket. It runs in the runtime's own venv inside a bubblewrap jail with no network
(`launch.py`); nothing here imports Faculty beyond this package, and no text it is sent is written anywhere.

One request per connection, one JSON line each way:
    {"op": "nearest", "query": str, "texts": [str, ...]} -> {"order": [index, ...], "scores": [float, ...]}  nearest first
        with "wait": seconds, a request that meets a lent card is held until the card is back, at most that long
    {"op": "vectors", "role": "query"|"document", "texts": [str, ...]} -> {"vectors": base64 float32, "count", "dimension"}
    {"op": "picture_vectors", "pictures": [base64 PNG or JPEG, ...]}    -> the same, one unit vector per picture, and "pieces"
    {"op": "status"}                                      -> {"model_id", "revision", "device", "dimension", "cached", "lent"}
    {"op": "lend", "seconds": number}                     -> {"lent": bool}  the card is given up for at most that long
    {"op": "take_back"}                                   -> {"lent": false} the model is on the card again
A kept text's vector is cached by its hash (texts never change under a revision), so a request costs one query embedding.
A picture is decoded here, inside the jail, and nowhere else: whatever a student's file holds, it meets no network and no
writable folder but the socket's. Pictures and their vectors are never cached or written by the worker.
While the card is lent (EMBEDDING "LENDS THE GPU": the local grading model needs all of it) the model waits in main memory and
every request but `status` is refused (the refusal marked `"lent": true`), so Faculty's keywords stand in until the worker has
taken the card back.
"""
import base64
import gc
import hashlib
import io
import json
import os
import socketserver
import threading
import time
from collections import OrderedDict
from pathlib import Path

import numpy as np

from .config import (CACHE, DIMENSION, GPU_FRACTION, MAX_CHARS, MAX_LEND, MAX_PICTURES, MAX_REQUEST, MAX_TEXTS, MAX_VECTOR_TEXTS,
                     MAX_WAIT, MODEL_ID, MODEL_REVISION, PICTURE_BYTES, PICTURE_PIECES, PICTURE_PIXELS, PICTURE_SIDE, PROMPTS)

BATCH = 64
# Taking the card back: the borrower has answered but may still be unloading, so the card is watched this long for room, this
# often; with none, the worker stays off it and tries again on a request at least this much later.
RETURN_WAIT, RETURN_STEP, RETRY = 15, .25, 5
ROOM = 512 * 1024**2  # free memory the card must have beyond the model's own before the model goes back on it


class Refused(ValueError):
    """A request the worker will not answer; the words go back to the client."""


class BadPicture(Refused):
    """The picture itself is the trouble (not a picture, the wrong kind, too large): asking again cannot help, and the
    reply says so (`"picture": true`), so whoever keeps an index sets that one picture aside instead of waiting."""


class Lent(Refused):
    """The card is lent, which is no request's fault, and the reply says so (`"lent": true`): the worker answers a status
    request all the while, so whoever keeps an index must not take this for a picture that silenced it."""


class Busy(RuntimeError):
    """The card has no room for the model yet."""


class Gpu:
    """Where the model sits. `leave()` moves it to main memory and hands the card's memory back (all but this process's own
    CUDA context); `enter()` puts it on the card again, and only when the card has room: a model that does not fit is never
    left half moved, and another program's work on the card is never squeezed by the attempt."""
    def __init__(self, model, torch, room=ROOM):
        self.model, self.torch, self.room, self.held = model, torch, room, 0

    def leave(self):
        self.held = max(self.held, self.torch.cuda.memory_allocated())
        self.model.to("cpu")
        gc.collect()
        self.torch.cuda.empty_cache()

    def enter(self):
        free, _ = self.torch.cuda.mem_get_info()
        if free < self.held + self.room:
            raise Busy("The card has no room for the embedding model yet.")
        try:
            self.model.to("cuda")
        except Exception:
            self.leave()  # whole in main memory again until the next try
            raise


def decoded(data):
    """A picture as the model takes it: PNG or JPEG only, bounded in pixels, RGB, its longest side scaled down."""
    from PIL import Image
    Image.MAX_IMAGE_PIXELS = PICTURE_PIXELS
    try:
        with Image.open(io.BytesIO(data)) as source:
            if source.format not in ("PNG", "JPEG"):
                raise BadPicture("Send PNG or JPEG pictures.")
            if source.width * source.height > PICTURE_PIXELS:
                raise BadPicture("A picture is too large.")
            image = source.convert("RGB")
    except Refused:
        raise
    except Exception:  # noqa: BLE001 - a file that is not a picture, or one too large to decode
        raise BadPicture("A picture could not be read.") from None
    image.thumbnail((PICTURE_SIDE, PICTURE_SIDE))
    return image


def packed(matrix):
    matrix = np.ascontiguousarray(matrix, dtype=np.float32)
    return {"vectors": base64.b64encode(matrix.tobytes()).decode(), "count": int(matrix.shape[0]), "dimension": int(matrix.shape[1]) if matrix.size else DIMENSION}


class Index:
    """Vectors of kept texts by hash, the oldest dropped past `limit`. `encode(texts, role)` returns unit float32 rows;
    `encode_pictures(images)` the same for decoded pictures (None in a worker with no picture part). `gpu` is the model's
    place on the card (None for a worker on the CPU, which has nothing to lend); `lent_until` is when a loan of the card runs
    out by itself (0 while the model is on it)."""
    def __init__(self, encode, limit=CACHE, encode_pictures=None, gpu=None):
        self.encode, self.limit, self.encode_pictures, self.gpu = encode, limit, encode_pictures, gpu
        self.vectors = OrderedDict()
        self.lock = threading.Lock()
        self.returned = threading.Condition(self.lock)  # told whenever the card comes back, for the requests held for it
        self.lent_until = 0.0

    def lend(self, seconds):
        """Give the card up for at most `seconds`, after the request in hand. A second loan only moves the end."""
        if self.gpu is None:
            return False
        with self.lock:
            try:
                if not self.lent_until:
                    self.gpu.leave()
                self.lent_until = time.monotonic() + seconds
            except Exception:  # noqa: BLE001 - whatever stopped the move, a later request puts the model whole on the card again
                self.lent_until = time.monotonic() + RETRY
                raise Refused("The embedding worker could not give up the GPU.") from None
        return True

    def take_back(self):
        """End the loan now, watching the card for room for at most RETURN_WAIT seconds."""
        deadline = time.monotonic() + RETURN_WAIT
        with self.lock:
            while self.lent_until:
                try:
                    self._back()
                except Refused:
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(RETURN_STEP)

    def _back(self):
        try:
            self.gpu.enter()
        except Exception:  # noqa: BLE001 - no room yet, or the move failed: off the card, and tried again later
            self.lent_until = time.monotonic() + RETRY
            raise Lent("The GPU is still in use; the embedding worker takes it back shortly.") from None
        self.lent_until = 0.0
        self.returned.notify_all()

    def _ready(self, wait=0):
        """Under the lock, before anything is embedded: a loan still running refuses the request, and one that ran out without
        being returned (its borrower stopped) ends here. A request that said it would `wait` is held instead (the lock let
        go meanwhile) until the card is back or that many seconds have passed, so it is answered between two loans."""
        deadline = time.monotonic() + wait
        while self.lent_until:
            now = time.monotonic()
            if now >= self.lent_until:
                try:
                    self._back()
                except Lent:
                    if time.monotonic() >= deadline:
                        raise
                continue
            if now >= deadline:
                raise Lent("The GPU is lent to the local grading model; ask again shortly.")
            self.returned.wait(min(deadline, self.lent_until) - now)

    @staticmethod
    def key(text):
        return hashlib.sha256(text.encode()).digest()

    def documents(self, texts, wait=0):
        keys = [self.key(text) for text in texts]
        with self.lock:
            self._ready(wait)
            missing = list({key: text for key, text in zip(keys, texts) if key not in self.vectors}.items())
            for start in range(0, len(missing), BATCH):
                part = missing[start:start + BATCH]
                for (key, _), vector in zip(part, self.encode([text for _, text in part], "document")):
                    self.vectors[key] = np.asarray(vector, dtype=np.float32)
            for key in keys:
                self.vectors.move_to_end(key)
            matrix = np.stack([self.vectors[key] for key in keys]) if keys else np.zeros((0, DIMENSION), dtype=np.float32)
            while len(self.vectors) > self.limit:
                self.vectors.popitem(last=False)
        return matrix

    def nearest(self, query, texts, wait=0):
        began = time.monotonic()
        matrix = self.documents(texts, wait)
        with self.lock:
            self._ready(max(0, wait - (time.monotonic() - began)))
            asked = np.asarray(self.encode([query], "query")[0], dtype=np.float32)
        scores = matrix @ asked
        order = sorted(range(len(texts)), key=lambda index: (-float(scores[index]), index))
        return order, [round(float(scores[index]), 4) for index in order]

    def texts(self, texts, role):
        with self.lock:
            self._ready()
            return self.encode(texts, role)

    def pictures(self, pictures):
        if self.encode_pictures is None:
            raise Refused("This worker embeds no pictures.")
        images = [decoded(data) for data in pictures]
        with self.lock:
            self._ready()
            # One at a time: the picture part's memory is per picture, and the worker's share of the GPU is small.
            return np.concatenate([self.encode_pictures([image]) for image in images]) if images else np.zeros((0, DIMENSION), dtype=np.float32)


def answer(index, request, about):
    """The reply to one decoded request. `about` is what `status` reports."""
    if not isinstance(request, dict):
        raise Refused("Send one JSON object.")
    if request.get("op") == "status":
        return {**about, "cached": len(index.vectors), "lent": bool(index.lent_until)}
    if request.get("op") == "lend":
        seconds = request.get("seconds")
        if type(seconds) not in (int, float) or not 1 <= seconds <= MAX_LEND:
            raise Refused(f"Lend the GPU for 1 to {MAX_LEND} seconds.")
        return {"lent": index.lend(seconds)}
    if request.get("op") == "take_back":
        index.take_back()
        return {"lent": False}
    if request.get("op") == "vectors":
        texts, role = request.get("texts"), request.get("role")
        if not isinstance(role, str) or role not in PROMPTS or not isinstance(texts, list) or not texts or any(not isinstance(text, str) or not text.strip() for text in texts):
            raise Refused("Send a role and a list of texts.")
        if len(texts) > MAX_VECTOR_TEXTS or any(len(text) > MAX_CHARS for text in texts):
            raise Refused(f"At most {MAX_VECTOR_TEXTS} texts of {MAX_CHARS} characters.")
        return packed(index.texts(texts, role))
    if request.get("op") == "picture_vectors":
        pictures = request.get("pictures")
        if not isinstance(pictures, list) or not pictures or len(pictures) > MAX_PICTURES or any(not isinstance(item, str) for item in pictures):
            raise Refused(f"Send one to {MAX_PICTURES} pictures.")
        try:
            raw = [base64.b64decode(item, validate=True) for item in pictures]
        except ValueError:
            raise BadPicture("A picture could not be read.") from None
        if any(len(data) > PICTURE_BYTES for data in raw):
            raise BadPicture("A picture is too large.")
        # How finely these were seen goes back with them: Faculty keeps a picture's vector under that setting's mark.
        return {**packed(index.pictures(raw)), "pieces": PICTURE_PIECES}
    if request.get("op") != "nearest":
        raise Refused("Unknown operation.")
    query, texts = request.get("query"), request.get("texts")
    if not isinstance(query, str) or not query.strip() or not isinstance(texts, list) or any(not isinstance(text, str) for text in texts):
        raise Refused("Send a query and a list of texts.")
    if len(texts) > MAX_TEXTS or len(query) > MAX_CHARS or any(len(text) > MAX_CHARS for text in texts):
        raise Refused(f"At most {MAX_TEXTS} texts of {MAX_CHARS} characters.")
    wait = request.get("wait", 0)
    if type(wait) not in (int, float) or not 0 <= wait <= MAX_WAIT:
        raise Refused(f"Wait for a lent GPU for at most {MAX_WAIT} seconds.")
    order, scores = index.nearest(query, texts, wait)
    return {"order": order, "scores": scores}


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        try:
            line = self.rfile.readline(MAX_REQUEST + 1)
            if len(line) > MAX_REQUEST:
                raise Refused("The request is too large.")
            reply = answer(self.server.index, json.loads(line), self.server.about)
        except Refused as refused:
            reply = {"error": str(refused), **({"picture": True} if isinstance(refused, BadPicture) else {}),
                     **({"lent": True} if isinstance(refused, Lent) else {})}
        except Exception as failure:  # noqa: BLE001 - one bad request never stops the worker; no text is echoed
            reply = {"error": "The embedding worker could not answer: " + type(failure).__name__}
        try:
            self.wfile.write(json.dumps(reply).encode() + b"\n")
        except OSError:
            pass  # the client stopped waiting; what was embedded stays cached for its next request


class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, path, encode, about, limit=CACHE, encode_pictures=None, gpu=None):
        path = Path(path)
        if path.is_socket() or path.exists():
            path.unlink()
        previous = os.umask(0o177)  # the socket is the instructor's alone
        try:
            super().__init__(str(path), Handler)
        finally:
            os.umask(previous)
        self.index, self.about = Index(encode, limit, encode_pictures, gpu), about


def verified(folder, lock):
    """Every locked file is present with its size and SHA-256; the worker loads nothing else."""
    for name, expected in lock["files"].items():
        path = Path(folder) / name
        if not path.is_file() or path.stat().st_size != expected["size"]:
            return False
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1 << 20), b""):
                digest.update(block)
        if digest.hexdigest() != expected["sha256"]:
            return False
    return True


def load(folder, wanted="auto"):
    """(encode, encode_pictures, about, gpu) for the pinned model: text and pictures, float32, on the GPU when it works and is
    wanted. `gpu` is the model's place on the card, which the worker can lend (None on the CPU)."""
    import torch
    from sentence_transformers import SentenceTransformer
    if wanted == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("CUDA was requested but is unavailable.")
    device = "cuda" if wanted != "cpu" and torch.cuda.is_available() else "cpu"
    if device == "cuda":
        torch.cuda.set_per_process_memory_fraction(GPU_FRACTION)
    else:
        torch.set_num_threads(4)
    # Text and pictures; the audio tower is never built. FLOAT32 ONLY (requirement, 2026-10-10): the weights are converted on load whatever
    # precision the file stores them in, nothing here uses autocast or half precision, and a model with any other
    # parameter type does not start.
    model = SentenceTransformer(str(folder), device=device, local_files_only=True, trust_remote_code=False,
                                config_kwargs={"audio_config": None}).float().eval()
    precisions = {str(parameter.dtype).removeprefix("torch.") for parameter in model.parameters()}
    if precisions != {"float32"}:
        raise RuntimeError("The embedding model must run in float32; it loaded as " + ", ".join(sorted(precisions)) + ".")
    for name in PROMPTS.values():
        if name not in (model.prompts or {}):
            raise RuntimeError(f"The pinned model defines no prompt named {name}.")

    def encode(texts, role):
        return model.encode(list(texts), prompt_name=PROMPTS[role], batch_size=BATCH, normalize_embeddings=True,
                            convert_to_numpy=True, show_progress_bar=False).astype(np.float32)

    def encode_pictures(images):
        # PICTURES AT 560 PIECES: the model's own setting for how finely it sees a picture, asked for with each call.
        return model.encode(list(images), batch_size=1, normalize_embeddings=True, convert_to_numpy=True, show_progress_bar=False,
                            processing_kwargs={"image": {"max_soft_tokens": PICTURE_PIECES}}).astype(np.float32)
    first = encode(["ready"], "query")
    dimension = int(first.shape[1])
    if dimension != DIMENSION or str(first.dtype) != "float32":
        raise RuntimeError(f"The pinned model gave {dimension} dimensions of {first.dtype}, not {DIMENSION} of float32.")
    about = {"model_id": MODEL_ID, "revision": MODEL_REVISION, "device": device, "dimension": dimension, "precision": "float32",
             "pictures": True, "picture_pieces": PICTURE_PIECES, "parameters": int(sum(parameter.numel() for parameter in model.parameters())),
             "gpu": torch.cuda.get_device_name(0) if device == "cuda" else None, "network": False}
    return encode, encode_pictures, about, Gpu(model, torch) if device == "cuda" else None


def main():
    folder = Path(os.environ.get("FACULTY_EMBEDDING_MODEL", "/model"))
    lock = json.loads(Path(__file__).with_name("model-lock.json").read_text())
    if lock["revision"] != MODEL_REVISION or not verified(folder, lock):
        raise SystemExit("The embedding model is missing or differs from model-lock.json. Run python -m faculty_embedding.setup.")
    encode, encode_pictures, about, gpu = load(folder, os.environ.get("FACULTY_EMBEDDING_DEVICE", "auto"))
    server = Server(os.environ.get("FACULTY_EMBEDDING_LISTEN", "/run-embedding/embedding.sock"), encode, about, encode_pictures=encode_pictures,
                    gpu=gpu)
    print(json.dumps({"ready": True, **about}), flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
