"""Vectors for the sandbox. The parent process never loads a model: a real model's vectors are computed once by its child
(`children/embed_worker.py`, in the sandbox's own venv) and read here from a content-addressed cache.

A cache row is keyed by (model, revision, prompt, text sha256), so it is immutable: a new model, revision or prompt is a new
row, never a changed one. Truncated sizes (EmbeddingGemma's Matryoshka 256) are cut from the full vector and renormalised.
"""
import hashlib
import json
import re
import sqlite3
import threading

import numpy as np

# Pinned at the hosts' APIs on 2026-10-10. `prompts` are sentence-transformers prompt names; the child refuses a name the
# loaded model does not define.
MODELS = {
    'eg2': {'repo': 'google/embeddinggemma-2', 'revision': '914f7f89142e33e77833254d9c9b90c3cef7303b', 'dim': 768,
            'prompts': {'query': 'SearchQuery', 'document': 'Document'}, 'text_only': True},
    'minilm': {'repo': 'sentence-transformers/all-MiniLM-L6-v2', 'revision': '1110a243fdf4706b3f48f1d95db1a4f5529b4d41', 'dim': 384,
               'prompts': {'query': '', 'document': ''}, 'text_only': False},
}
# name -> (model, dimension kept)
EMBEDDERS = {'eg2-768': ('eg2', 768), 'eg2-256': ('eg2', 256), 'minilm': ('minilm', 384)}


class EmbedderDown(RuntimeError):
    """The embedder cannot answer now. A caller falls back to keyword search; it never waits or fails the request."""


def text_key(model, role, text):
    spec = MODELS[model]
    return hashlib.sha256(json.dumps([spec['repo'], spec['revision'], spec['prompts'][role], text], ensure_ascii=False).encode()).hexdigest()


def _unit(matrix):
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    return matrix / np.where(norms == 0, 1, norms)


class HashEmbedder:
    """A stand-in with no model: each word adds to a few fixed coordinates, so texts sharing words are close. Deterministic;
    used by the synthetic checks and by scripted (non-`--live`) runs, which default to `--embedders hash,none`."""
    name, dim = 'hash', 256

    def vectors(self, texts, role='document'):
        matrix = np.zeros((len(texts), self.dim), dtype=np.float32)
        for row, text in enumerate(texts):
            for word in re.findall(r'[a-z0-9]{3,}', text.lower()):
                digest = hashlib.sha256(word.encode()).digest()
                for offset in (0, 4, 8):
                    matrix[row, int.from_bytes(digest[offset:offset + 4], 'big') % self.dim] += 1 if digest[offset + 12] % 2 else -1
        return _unit(matrix)


class Down:
    name, dim = 'down', 0

    def vectors(self, texts, role='document'):
        raise EmbedderDown('The embedder did not answer.')


class VectorCache:
    def __init__(self, path):
        self.path = path
        self._lock = threading.Lock()
        with self._open() as db:
            db.execute('CREATE TABLE IF NOT EXISTS vectors(key TEXT PRIMARY KEY, model TEXT NOT NULL, dim INTEGER NOT NULL, '
                       'vector BLOB NOT NULL) WITHOUT ROWID')

    def _open(self):
        return sqlite3.connect(self.path, timeout=30)

    def missing(self, keys):
        found = set()
        with self._lock, self._open() as db:
            for start in range(0, len(keys), 500):
                part = keys[start:start + 500]
                found.update(row[0] for row in db.execute(f"SELECT key FROM vectors WHERE key IN ({','.join('?' * len(part))})", part))
        return [key for key in keys if key not in found]

    def put(self, model, rows):
        """rows: [(key, float32 vector)]. A key already kept is left as it is."""
        with self._lock, self._open() as db:
            db.executemany('INSERT OR IGNORE INTO vectors VALUES(?,?,?,?)',
                           [(key, model, int(vector.shape[0]), np.asarray(vector, dtype=np.float32).tobytes()) for key, vector in rows])

    def get(self, keys):
        found = {}
        with self._lock, self._open() as db:
            for start in range(0, len(keys), 500):
                part = keys[start:start + 500]
                for key, blob in db.execute(f"SELECT key,vector FROM vectors WHERE key IN ({','.join('?' * len(part))})", part):
                    found[key] = np.frombuffer(blob, dtype=np.float32)
        return found


class Cached:
    """A real model's vectors, from the cache. A text the child never embedded is an error here: run `embed` first."""
    def __init__(self, name, cache):
        self.name = name
        self.model, self.dim = EMBEDDERS[name]
        self.cache = cache
        self._memo = {}

    def vectors(self, texts, role='document'):
        keys = [text_key(self.model, role, text) for text in texts]
        wanted = [key for key in dict.fromkeys(keys) if key not in self._memo]
        if wanted:
            found = self.cache.get(wanted)
            absent = [key for key in wanted if key not in found]
            if absent:
                raise KeyError(f'{len(absent)} texts have no {self.model} vector yet: run the embed command first.')
            for key, vector in found.items():
                cut = np.array(vector[:self.dim], dtype=np.float32)
                self._memo[key] = cut / (np.linalg.norm(cut) or 1)
        if not keys:
            return np.zeros((0, self.dim), dtype=np.float32)
        return np.stack([self._memo[key] for key in keys])


def nearest(embedder, query, texts, role='query'):
    """Indexes of `texts` by cosine similarity to `query`, nearest first, with their similarities."""
    if not texts:
        return [], []
    matrix = embedder.vectors(texts, 'document')
    scores = matrix @ embedder.vectors([query], role)[0]
    order = sorted(range(len(texts)), key=lambda i: (-float(scores[i]), i))
    return order, [float(scores[i]) for i in order]
