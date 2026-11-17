"""The parent's side of the embedding children: which texts need vectors, running the worker in its jail, and the two
measurements that need vectors only (the probe, and where the right lesson ranks). The parent never imports a model."""
import json
import shutil
import time
from pathlib import Path

import numpy as np

from . import runtime
from .cases import PADS
from .components import lesson_text
from .embedders import MODELS, Cached, HashEmbedder, VectorCache, nearest, text_key
from .pipeline import keyword_order


def texts(world):
    """Every text the cases can ask a vector for, by the role it is embedded in."""
    documents = {text for event in world.events.values() for text in (event['text'], event['say']) if text}
    documents |= {row['text'] for row in (*world.distractors.values(), *world.padding(max(PADS)))}
    documents |= {episode['summary'] for episode in world.episodes.values()}
    documents |= {lesson_text(lesson) for lesson in world.lessons.values()}
    queries = {query['text'] for query in world.queries.values()} | {request['text'] for request in world.requests.values()}
    return {'query': sorted(queries), 'document': sorted(documents)}


def _job(model, extra):
    spec = MODELS[model]
    return {'repo': spec['repo'], 'revision': spec['revision'], 'prompts': spec['prompts'], 'text_only': spec['text_only'],
            'threads': 8, **extra}


def _child(work, job, timeout=3600):
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    (work / 'job.json').write_text(json.dumps(job, ensure_ascii=False))
    code = runtime.run_child(['/venv/bin/python', '/children/embed_worker.py'], work, timeout=timeout)
    if code or not (work / 'done.json').exists():
        tail = (work / 'child.err').read_text(errors='replace').strip().splitlines()[-3:]
        raise RuntimeError(f'The embedding child stopped ({code}): ' + ' | '.join(tail))
    return json.loads((work / 'done.json').read_text())


def embed(folder, world, models=tuple(MODELS), say=print):
    """Vectors for every case text the cache does not hold yet, one child per model."""
    cache = VectorCache(runtime.paths()['vectors'])
    wanted = texts(world)
    for model in models:
        runtime.verify(model)
        missing = {}
        for role, role_texts in wanted.items():
            keys = [text_key(model, role, text) for text in role_texts]
            absent = set(cache.missing(keys))
            missing[role] = [text for text, key in zip(role_texts, keys) if key in absent]
        count = sum(map(len, missing.values()))
        if not count:
            say(f'{model}: every vector is cached')
            continue
        work = Path(folder) / f'embed-{model}'
        done = _child(work, _job(model, {'texts': missing}))
        vectors = np.load(work / 'vectors.npy')
        rows, offset = [], 0
        for role, length in done['order']:
            rows += [(text_key(model, role, text), vectors[offset + index]) for index, text in enumerate(missing[role])]
            offset += length
        cache.put(model, rows)
        (work / 'vectors.npy').unlink()
        say(f"{model}: {count} vectors of {done['dimension']} dimensions in {done['load_seconds']:.1f} s load")
    return cache


def probe(folder, say=print):
    """Load time, latency, memory and determinism of each pinned model on this machine's CPU, and how long numpy takes to
    compare one query with every kept vector. Writes probe.json."""
    found = {'models': {}, 'search': {}}
    for model in MODELS:
        try:
            weights = runtime.verify(model)
            done = _child(Path(folder) / f'probe-{model}', _job(model, {'probe': True}), timeout=1800)
            done['disk_mb'] = sum(p.stat().st_size for p in runtime.snapshot(model).rglob('*') if p.is_file()) / 1e6
            done['weights_mb'] = weights.stat().st_size / 1e6
            found['models'][model] = done
            say(f"{model}: load {done['load_seconds']:.1f} s, query p50 {done['query_p50_ms']:.0f} ms, {done['rss_mb']:.0f} MB")
        except Exception as why:  # noqa: BLE001 - one model failing is a finding, not the end of the probe
            found['models'][model] = {'error': f'{type(why).__name__}: {str(why)[:300]}'}
            say(f'{model}: {found["models"][model]["error"]}')
    generator = np.random.default_rng(7)
    for rows in (500, 5000, 50000):
        matrix = generator.standard_normal((rows, 768), dtype=np.float32)
        matrix /= np.linalg.norm(matrix, axis=1, keepdims=True)
        query = matrix[0]
        begun = time.perf_counter()
        for _ in range(20):
            np.argpartition(-(matrix @ query), 12)[:12]
        found['search'][str(rows)] = (time.perf_counter() - begun) / 20 * 1000
    (Path(folder) / 'probe.json').write_text(json.dumps(found, indent=1))
    return found


def embedders(names):
    """{name: embedder}: 'none' is no embedder at all (keyword search only); the rest read the vector cache."""
    found = {}
    cache = None
    for name in names:
        if name == 'none':
            found[name] = None
        elif name == 'hash':
            found[name] = HashEmbedder()
        else:
            if not runtime.paths()['vectors'].exists():
                raise SystemExit(f'No vectors for {name} yet. Run: manage_memory_sandbox.py fetch --download, then embed.')
            cache = cache or VectorCache(runtime.paths()['vectors'])
            found[name] = Cached(name, cache)
    return found


def lessons_recall(world, found):
    """For each student request with a matching lesson: is that lesson among the first 1, 4 and 8 candidates? By today's
    keyword rule, by each embedder, and by an embedder joined with the keyword picks."""
    lessons = list(world.lessons.values())
    documents = [lesson_text(lesson) for lesson in lessons]
    requests = [request for request in world.requests.values() if request['expect']]
    result = {}

    def score(name, ranking):
        hits = {depth: 0 for depth in (1, 4, 8)}
        for request in requests:
            order = [lessons[i]['id'] for i in ranking(request['text'])]
            for depth in hits:
                hits[depth] += all(ident in order[:depth] for ident in request['expect'])
        result[name] = {str(depth): count for depth, count in hits.items()} | {'of': len(requests)}
    score('keywords (today)', lambda text: keyword_order(text, documents))
    for name, embedder in found.items():
        if embedder is None:
            continue
        score(name, lambda text, e=embedder: nearest(e, text, documents)[0])
        score(name + ' + keywords', lambda text, e=embedder: _joined(nearest(e, text, documents)[0], keyword_order(text, documents)))
    return result


def _joined(first, second, keep=4):
    """The embedder's order with the keyword rule's first picks moved into the first `keep` places, so none is lost."""
    front = first[:keep - min(2, len(second))] + [index for index in second[:2] if index not in first[:keep - min(2, len(second))]]
    return front + [index for index in first if index not in front]
