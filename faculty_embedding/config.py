"""Workspace-wide embedding model assets; these own no teaching record (EMBEDDING, docs/decisions/embedding.md)."""
import os
from pathlib import Path

MODEL_ID = "google/embeddinggemma-2"
MODEL_REVISION = "914f7f89142e33e77833254d9c9b90c3cef7303b"
DIMENSION = 768
# sentence-transformers prompt names of the pinned model: a request and a kept text are embedded differently.
PROMPTS = {"query": "SearchQuery", "document": "Document"}
ROOT = Path(__file__).resolve().parents[1]
# One request: at most this many texts of this many characters, in this many bytes; the worker keeps this many vectors.
MAX_TEXTS, MAX_CHARS, MAX_REQUEST, CACHE = 2000, 8000, 24 * 1024**2, 20000
# Vectors handed back to Faculty (the picture index keeps them): at most this many texts or pictures in one request. A
# picture is PNG or JPEG of at most this many bytes and pixels, and is scaled to this longest side before it is embedded.
MAX_VECTOR_TEXTS, MAX_PICTURES, PICTURE_BYTES, PICTURE_PIXELS, PICTURE_SIDE = 64, 4, 12 * 1024**2, 60_000_000, 1568
# How finely the model sees a picture: the pieces ("soft tokens") it is cut into, one of the model's own 70, 140, 280, 560
# and 1120 (EMBEDDING "PICTURES AT 560 PIECES": requirement, 2026-10-11). A Letter page is then seen at about 1,000 by 1,290 pixels, which PICTURE_SIDE must not undercut. A vector
# made at another setting is another model's as far as comparing goes, so PICTURES is the mark picture vectors are kept under.
PICTURE_PIECES = 560
PICTURES = f"{MODEL_ID}@{MODEL_REVISION}#{PICTURE_PIECES}"
# The share of the GPU the worker may ever take (the model itself is about 1.8 GB of 11). Measured 2026-10-11: local OCR's
# largest read fits beside it; the local grading model does not, so the worker lends it the whole card (`lend`).
GPU_FRACTION = .25
# The longest one loan of the card lasts, in seconds, before the worker takes it back by itself: longer than the longest
# request Faculty makes of a local model (GRADING's `timeout`, at most 600).
MAX_LEND = 900
# The longest a request may ask to be held for a lent card instead of being refused (`"wait"`), in seconds.
MAX_WAIT = 120


def runtime_root():
    """The venv, the model and the socket: on NVMe, never the root disk (STORAGE)."""
    return Path(os.environ.get("FACULTY_EMBEDDING_RUNTIME", "/mnt/nvme/Teaching/runtime/embedding")).expanduser().resolve()


def socket_path():
    """Where the worker listens. Faculty asks it only when FACULTY_EMBEDDING_SOCKET names it, so a check never reaches it."""
    return Path(os.environ.get("FACULTY_EMBEDDING_SOCKET") or runtime_root() / "run" / "embedding.sock")


def device():
    """'auto' (the GPU when it works, else the CPU), 'cuda' or 'cpu'."""
    wanted = os.environ.get("FACULTY_EMBEDDING_DEVICE", "auto")
    return wanted if wanted in ("auto", "cuda", "cpu") else "auto"
