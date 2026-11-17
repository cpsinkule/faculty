"""Compute vectors with one pinned model, offline, on CPU. Runs in the sandbox venv under bubblewrap; reads /work/job.json,
writes /work/vectors.npy and /work/done.json. With "probe" in the job it measures the model instead.

job.json: {"repo", "revision", "prompts": {"query": name, "document": name}, "text_only": bool, "threads": n,
           "texts": {"query": [...], "document": [...]}, "probe": bool}
"""
import json
import os
import resource
import statistics
import sys
import time
from pathlib import Path

job = json.loads(Path('/work/job.json').read_text())
threads = int(job.get('threads') or 8)
os.environ.setdefault('OMP_NUM_THREADS', str(threads))

import numpy as np  # noqa: E402
import torch  # noqa: E402
from sentence_transformers import SentenceTransformer  # noqa: E402

torch.set_num_threads(threads)
folder = Path(os.environ['HF_HOME']) / 'hub' / ('models--' + job['repo'].replace('/', '--')) / 'snapshots' / job['revision']
started = time.monotonic()
options = {'device': 'cpu', 'local_files_only': True, 'trust_remote_code': False}
if job.get('text_only'):
    # The model card's text-only load: the vision and audio towers are never built.
    options['config_kwargs'] = {'vision_config': None, 'audio_config': None}
try:
    model = SentenceTransformer(str(folder), **options)
except Exception as first:  # noqa: BLE001 - a build that rejects the text-only option still loads the whole model
    if not job.get('text_only'):
        raise
    options.pop('config_kwargs')
    model = SentenceTransformer(str(folder), **options)
    print(f'text-only load refused ({type(first).__name__}); loaded the whole model', file=sys.stderr)
model = model.float()
load_seconds = time.monotonic() - started
known = set(getattr(model, 'prompts', {}) or {})
for role, name in job['prompts'].items():
    if name and name not in known:
        raise SystemExit(f'The model defines no prompt named {name!r}; it defines {sorted(known)}.')


def encode(texts, role, batch=32):
    name = job['prompts'][role]
    return model.encode(list(texts), batch_size=batch, normalize_embeddings=True, convert_to_numpy=True, show_progress_bar=False,
                        **({'prompt_name': name} if name else {})).astype(np.float32)


def timed(texts, role, repeat):
    seconds = []
    for _ in range(repeat):
        begun = time.perf_counter()
        encode(texts, role)
        seconds.append((time.perf_counter() - begun) * 1000)
    seconds.sort()
    return statistics.median(seconds), seconds[min(len(seconds) - 1, int(len(seconds) * .95))]


done = {'load_seconds': load_seconds, 'dimension': int(model.encode(['x'], convert_to_numpy=True).shape[1]), 'prompts': sorted(known),
        'parameters': int(sum(p.numel() for p in model.parameters())), 'torch': torch.__version__}
if job.get('probe'):
    begun = time.perf_counter()
    first = encode(['How long should the quiz be?'], 'query')
    done['first_ms'] = (time.perf_counter() - begun) * 1000
    done['query_p50_ms'], done['query_p95_ms'] = timed(['How many versions should the next quiz have?'], 'query', 40)
    done['document_p50_ms'], _ = timed(['Keep student replies under about 200 words unless asked for more, and sign them plainly.'], 'document', 20)
    many = [f'Memory number {n}: keep student replies short and sign them plainly.' for n in range(32)]
    done['batch32_ms'], _ = timed(many, 'document', 5)
    long_text = ' '.join(['The instructor prefers plain sentences in every reply to a student.'] * 160)
    done['long_ms'], _ = timed([long_text], 'document', 3)
    by_threads = {}
    for count in (1, 4, 8):
        torch.set_num_threads(count)
        by_threads[str(count)] = timed(['How many versions should the next quiz have?'], 'query', 20)[0]
    done['query_ms_by_threads'] = by_threads
    torch.set_num_threads(threads)
    again = encode(['How long should the quiz be?'], 'query')
    done['deterministic'] = bool(np.array_equal(first, again))
    done['finite'] = bool(np.isfinite(first).all())
    done['norm'] = float(np.linalg.norm(first[0]))
else:
    vectors, order = [], []
    for role in ('query', 'document'):
        texts = job['texts'].get(role) or []
        if texts:
            vectors.append(encode(texts, role))
            order.append([role, len(texts)])
    np.save('/work/vectors.npy', np.concatenate(vectors) if vectors else np.zeros((0, done['dimension']), dtype=np.float32))
    done['order'] = order
done['rss_mb'] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
Path('/work/done.json').write_text(json.dumps(done))
