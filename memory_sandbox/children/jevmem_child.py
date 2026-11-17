"""Jev-Mem at its pinned commit, on the sandbox's cases. Runs in the sandbox venv under bubblewrap with the checkout read-only
at /jevmem; reads /work/job.json, writes /work/results.json after each graph. Nothing of Faculty is imported.

One graph per term context, because Jev-Mem has no owners: each graph is fed, in order, the instructor messages in scope
there, then asked that context's requests through `QueryEngine.query` (retrieval only: no System-Two model is called).

Deviations from the shipped configuration, all in job["overrides"] and listed in the report: one attempt per request
(max_retries 0: JEV forbids resends), the pinned Jev model, and no System Two.
"""
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

job = json.loads(Path('/work/job.json').read_text())
os.environ.setdefault('OMP_NUM_THREADS', '4')
sys.path.insert(0, '/jevmem')

import numpy as np  # noqa: E402

from memory import jev_client  # noqa: E402
from memory.jev_mem_config import JevMemConfig  # noqa: E402
from memory.memory_builder import MemoryBuilder  # noqa: E402
from memory.mock_encoder import MockEncoder  # noqa: E402
from memory.query_engine import QueryEngine  # noqa: E402
from memory.trg_memory import TemporalResonanceGraphMemory  # noqa: E402
from memory.vector_db import NumpyVectorDB  # noqa: E402

sent = {'calls': 0}
limit = int(job.get('budget') or 0)
_consume = jev_client.CallBudget.consume


def consume(self):
    """Every request Jev-Mem is about to send passes here: count it, and stop the child at the budget."""
    if limit and sent['calls'] >= limit:
        Path('/work/stopped').write_text(f"budget of {limit} requests reached\n")
        raise SystemExit(3)
    sent['calls'] += 1
    return _consume(self)


jev_client.CallBudget.consume = consume


class Encoder:
    """A pinned sentence-transformers model behind the interface Jev-Mem's encoder has: `.dimension`, `.encode(text or list)`.
    Jev-Mem encodes requests and observations with the same call, so one prompt serves both."""
    def __init__(self, spec):
        import torch
        from sentence_transformers import SentenceTransformer
        torch.set_num_threads(4)
        folder = Path(os.environ['HF_HOME']) / 'hub' / ('models--' + spec['repo'].replace('/', '--')) / 'snapshots' / spec['revision']
        options = {'device': 'cpu', 'local_files_only': True, 'trust_remote_code': False}
        if spec.get('text_only'):
            options['config_kwargs'] = {'vision_config': None, 'audio_config': None}
        self.model = SentenceTransformer(str(folder), **options).float()
        self.prompt = spec.get('prompt') or ''
        self.dimension = int(self.encode('x').shape[0])

    def encode(self, texts):
        single = isinstance(texts, str)
        found = self.model.encode([texts] if single else list(texts), normalize_embeddings=True, convert_to_numpy=True,
                                  show_progress_bar=False, **({'prompt_name': self.prompt} if self.prompt else {})).astype(np.float32)
        return found[0] if single else found


encoder = MockEncoder() if job['encoder']['kind'] == 'mock' else Encoder(job['encoder'])
results = {'graphs': {}, 'dimension': int(encoder.dimension)}
for name, graph in job['graphs'].items():
    folder = Path('/work') / ('graph-' + name)
    folder.mkdir(exist_ok=True)
    config = JevMemConfig.load(job['config_file'], **{**job['overrides'], 'audit_path': str(folder / 'decisions.jsonl')})
    trg = TemporalResonanceGraphMemory(vector_db=NumpyVectorDB(encoder.dimension), encoder=encoder, llm_backend=None)
    builder = MemoryBuilder(str(folder / 'cache'), jev_config=config, trg_memory=trg, llm_enabled=False)
    started = time.monotonic()
    stored, rejected, failed = [], [], []
    for item in graph['observations']:
        try:
            node = builder.build(item['text'], timestamp=datetime.fromisoformat(item['at']), metadata={'case_id': item['id'], 'source': 'sandbox'})
        except Exception as why:  # noqa: BLE001 - one observation failing is recorded; the graph goes on
            failed.append([item['id'], type(why).__name__])
            continue
        (stored if node else rejected).append(item['id'])
    built = time.monotonic() - started
    engine = QueryEngine(trg, builder.node_index, jev_config=config, jev_client=builder.jev)
    queries = {}
    for query in graph['queries']:
        begun = time.monotonic()
        try:
            context, _ = engine.query(query['text'], top_k=int(job.get('top_k') or 12))
        except Exception as why:  # noqa: BLE001
            queries[query['id']] = {'ids': [], 'chars': 0, 'error': type(why).__name__, 'seconds': time.monotonic() - begun}
            continue
        nodes = list(context.anchor_nodes)
        meta = context.metadata
        queries[query['id']] = {'ids': [node.attributes.get('case_id', '') for node in nodes],
                                'chars': sum(len(node.content_narrative) for node in nodes[:5]),
                                'jev_calls': meta.get('jev_calls', 0), 'stopping': meta.get('stopping_decision', ''),
                                'depth': meta.get('retrieval_depth', 0), 'nodes_visited': meta.get('nodes_visited', 0),
                                'fallbacks': len(meta.get('fallback_events') or []), 'seconds': time.monotonic() - begun}
    results['graphs'][name] = {'stored': stored, 'rejected': rejected, 'failed': failed, 'build_seconds': built, 'queries': queries,
                               'nodes': len(trg.graph_db.nodes), 'links': int(trg.stats.get('links_created', 0))}
    results['requests'] = sent['calls']
    Path('/work/results.json').write_text(json.dumps(results))
    builder.jev.close()
Path('/work/finished').write_text('ok\n')
