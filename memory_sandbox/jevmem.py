"""Jev-Mem (github.com/libingzheren/Jev-Mem, MIT) at its pinned commit, on the same cases as the flat pipeline.

It runs only in its child (`children/jevmem_child.py`): the sandbox venv, a bubblewrap jail, the checkout read-only. This
module builds what the child is fed, starts it, and turns what it returns into the stories.jsonl line the report reads.

What Jev-Mem is given that the flat pipeline has to work out, so the comparison is generous to it:
- owners: one graph per term context, fed only what is in scope there (Jev-Mem has none of its own);
- no quoted mail, scan or tool text, as for every arm.
What it does not do, by design, and the report says so: it keeps every observation unless `admission_enabled`, it never
replaces a memory (it links contradictions), and it returns its nearest evidence without judging each item's relevance.
"""
import json
import os
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from jev.client import MODEL as JEV_MODEL

from . import runtime
from .embedders import MODELS

# name -> (admission step on, embedder). 'shipped' is config/jev_mem.json with the overrides below.
CONFIGS = {'shipped-minilm': (False, 'minilm'), 'shipped-eg2': (False, 'eg2'),
           'admission-minilm': (True, 'minilm'), 'admission-eg2': (True, 'eg2')}
# JEV: one attempt per request, never a resend; the three-attempt deadline of the shipped configuration becomes one timeout.
OVERRIDES = {'max_retries': 0, 'timeout_seconds': 9.0, 'write_enabled': True, 'read_enabled': True}
TOP = 12


def graphs(world, variant='raw'):
    """{term: {'observations': [{id, text, at}], 'queries': [{id, text}]}}: for each term context, the neutral memories in
    scope, then the instructor messages in scope in timeline order, then the past-work summaries."""
    found = {}
    for term, row in world.terms.items():
        owners = ('workspace', row['course'], term)
        observations = [{'id': item['id'], 'text': item['text'], 'at': f'2026-08-20T09:{index % 60:02d}:00+00:00'}
                        for index, item in enumerate(world.distractors.values()) if item['owner'] in owners]
        for event in world.timeline():
            owner = event['expect']['owner']
            if (owner in owners) if owner else (event['term'] == term):
                observations.append({'id': event['id'], 'text': world.statement(event, variant), 'at': event['at']})
        observations += [{'id': episode['id'], 'text': episode['summary'], 'at': episode['on'] + 'T18:00:00+00:00'}
                         for episode in world.episodes.values() if episode['worth'] and episode['owner'] in owners]
        queries = [{'id': query['id'], 'text': query['text']} for query in world.queries.values() if query['term'] == term]
        found[term] = {'observations': observations, 'queries': queries}
    return found


def selftest(folder, say=print):
    """Jev-Mem's own offline tests and mock demo, in the jail with no network and no key. Returns (tests exit, demo exit)."""
    work = Path(folder) / 'jevmem-selftest'
    if work.exists():
        shutil.rmtree(work)
    environment = {'PYTHONPATH': '/jevmem', 'TYPESAFE_API_KEY': ''}
    # From the checkout's own root, as its tests expect (they read config/ by relative path); the checkout stays read-only.
    tests = runtime.run_child(['/venv/bin/python', '-m', 'pytest', '-q', '-p', 'no:cacheprovider', 'tests'],
                              work, environment=environment, timeout=1800, cwd='/jevmem')
    demo = runtime.run_child(['/venv/bin/python', '-c', 'import sys; sys.path.insert(0, "/jevmem"); sys.argv = ["demo", "--cache-dir", "/work/demo"]; '
                              'from jev_mem.demo import main; main()'], work, environment=environment, timeout=600)
    tail = (work / 'child.out').read_text(errors='replace').strip().splitlines()
    say('Jev-Mem tests: ' + (next((line for line in reversed(tail) if ' passed' in line or ' failed' in line or 'error' in line), 'no summary line')))
    say(f'Jev-Mem tests exit {tests}; mock demo exit {demo}')
    return tests, demo


def _job(world, name, live, budget, variant):
    admission, model = CONFIGS[name]
    spec = MODELS[model]
    # Jev-Mem has one `encode` for requests and observations alike, so a model with two prompts gets its document prompt.
    encoder = {'kind': 'st', 'repo': spec['repo'], 'revision': spec['revision'], 'prompt': spec['prompts']['document'],
               'text_only': spec['text_only']} if live else {'kind': 'mock'}
    return {'config_file': '/jevmem/config/jev_mem.json', 'overrides': {**OVERRIDES, 'admission_enabled': admission, 'jev_mock': not live},
            'encoder': encoder, 'graphs': graphs(world, variant), 'top_k': TOP, 'budget': budget}


def _audit(work):
    """Requests Jev-Mem sent, fallbacks it took and tokens it used, from its own decision log."""
    counts = {'sent': 0, 'cached': 0, 'fallbacks': 0, 'tokens': 0, 'rejected': 0, 'by_operation': {}}
    for path in sorted(Path(work).glob('graph-*/decisions.jsonl')):
        for line in path.read_text().splitlines():
            record = json.loads(line)
            if record['event'] == 'jev_decision':
                if record.get('source') == 'cache':
                    counts['cached'] += 1
                    continue
                counts['sent'] += 1
                counts['by_operation'][record['operation']] = counts['by_operation'].get(record['operation'], 0) + 1
                counts['tokens'] += sum(int(value) for key, value in (record.get('usage') or {}).items()
                                        if 'input' in key and isinstance(value, (int, float)))
            elif record['event'] == 'jev_fallback':
                counts['fallbacks'] += 1
            elif record['event'] == 'memory_rejected':
                counts['rejected'] += 1
    return counts


def line(world, name, results, audit, variant, live):
    """The child's results in the shape of a stories.jsonl line: one write per instructor message (kept when its own term's
    graph kept it), and the first twelve memories each request got back."""
    admission, model = CONFIGS[name]
    stored = {term: set(graph['stored']) for term, graph in results['graphs'].items()}
    writes = []
    for event in world.timeline():
        kept = event['id'] in stored.get(event['term'], ())
        writes.append({'event': event['id'], 'action': 'save' if kept else 'skip', 'target': '', 'kind': None,
                       'scope': event['expect']['scope'] if kept else None, 'named_in_code': '', 'p': {}, 'fallback': '', 'calls': []})
    reads = []
    for term, graph in results['graphs'].items():
        for ident, found in graph['queries'].items():
            ids = [i for i in found['ids'] if i]
            reads.append({'query': ident, 'visible': graph['nodes'], 'ranked': ids[:TOP], 'candidates': ids, 'passed': ids[:5],
                          'injected': ids[:5], 'use': {}, 'fallback': 'jev' if found.get('fallbacks') else '', 'calls': found.get('jev_calls', 0),
                          'chars': found.get('chars', 0), 'seconds': found.get('seconds'), 'stopping': found.get('stopping', ''),
                          'error': found.get('error', '')})
    messages = sum(len(graph['stored']) + len(graph['rejected']) + len(graph['failed']) for graph in results['graphs'].values())
    read_calls = sum(result['calls'] for result in reads)
    return {'arm': 'jevmem', 'config': name, 'mode': 'jev' if live else 'scripted', 'embedder': model if live else 'mock', 'variant': variant,
            'gate_set': 'shipped', 'gates': {}, 'student_policy': 'none', 'writes': writes,
            'final': {event['id']: 'current' for event in world.timeline() if event['id'] in stored.get(event['term'], ())},
            'reads': {'own': reads}, 'scope_given': True, 'requests': audit,
            'write_requests': audit['sent'] - read_calls, 'observations': messages,
            'failed': sum(len(graph['failed']) for graph in results['graphs'].values()),
            'build_seconds': round(sum(graph['build_seconds'] for graph in results['graphs'].values()), 1)}


def run(folder, world, live=False, budget=0, wanted=(), held=None, variant='raw', say=print):
    names = [name for name in CONFIGS if not wanted or name in wanted]
    folder = Path(folder)
    marker = folder / 'answers-from'
    kind = 'live' if live else 'scripted'
    if marker.exists() and marker.read_text().strip() != kind:
        raise SystemExit(f'This run folder holds {marker.read_text().strip()} answers. Use another --run name.')
    marker.write_text(kind + '\n')
    tests, demo = selftest(folder, say)
    if tests or demo:
        say('Jev-Mem did not pass its own offline checks here; it is not run on the cases.')
        return 2
    estimate = sum(len(graph['observations']) * 3 + len(graph['queries']) * 16 for graph in graphs(world, variant).values())
    if live:
        if not (held or {}).get('TYPESAFE_API_KEY'):
            raise SystemExit('No TYPESAFE_API_KEY for the sandbox.')
        if budget < 1:
            raise SystemExit(f'A live Jev-Mem configuration sends at most about {estimate} requests; {len(names)} are selected. '
                             'Pass --budget-calls N to allow it.')
        say(f'live: {len(names)} configurations, at most about {estimate} requests each; this command may send {budget}')
    path = folder / 'stories.jsonl'
    done = {json.loads(text).get('config') for text in (path.read_text().splitlines() if path.exists() else [])
            if text and json.loads(text)['arm'] == 'jevmem' and json.loads(text)['variant'] == variant}
    names = [name for name in names if name not in done]  # a finished configuration is never asked again
    if not names:
        say('Every selected configuration is already in this run folder.')
        return 0
    if live and budget < len(names):
        raise SystemExit('Allow at least one request per selected configuration, or select fewer configurations.')
    lock, status = threading.Lock(), {}

    def one(name):
        work = folder / f'jevmem-{name}-{variant}'
        if work.exists():
            shutil.rmtree(work)
        work.mkdir(parents=True)
        (work / 'job.json').write_text(json.dumps(_job(world, name, live, budget // len(names) if live else 0, variant), ensure_ascii=False))
        environment = {'TYPESAFE_API_KEY': os.environ['TYPESAFE_API_KEY'], 'TYPESAFE_DEFAULT_MODEL': JEV_MODEL} if live else {'TYPESAFE_API_KEY': ''}
        code = runtime.run_child(['/venv/bin/python', '/children/jevmem_child.py'], work, network=live, environment=environment, timeout=6 * 3600)
        (work / 'job.json').unlink()  # the job holds the case texts; ids and numbers stay
        if not (work / 'finished').exists():
            status[name] = (work / 'stopped').read_text().strip() if (work / 'stopped').exists() else f'stopped with exit {code}'
            return
        found = line(world, name, json.loads((work / 'results.json').read_text()), _audit(work), variant, live)
        with lock, path.open('a') as out:
            out.write(json.dumps(found, ensure_ascii=False, sort_keys=True) + '\n')
        status[name] = f"{found['requests']['sent']} requests, {found['requests']['fallbacks']} fallbacks, {found['build_seconds']} s building"

    with ThreadPoolExecutor(max_workers=len(names)) as pool:
        list(pool.map(one, names))
    for name in names:
        say(f'{name}: {status[name]}')
    return 0 if all('requests' in status[name] for name in names) else 3
