"""Scripted stand-ins for every model (no network): deterministic answers from a hash of the request, in Jev's answer shape.
Used by the synthetic checks and by `--fake` dry runs that exercise every case's code without spending a request."""
import hashlib

from jev.service import encoded


def _unit(*parts):
    return int(hashlib.sha256(encoded(list(parts)).encode()).hexdigest()[:8], 16) / 0xFFFFFFFF


def answers(state, questions, salt=''):
    found = {}
    for name, asked in questions.items():
        u = _unit(salt, state, name)
        if asked['type'] == 'noul':
            found[name] = {'type': 'noul', 'noul': round(u, 2)}
        elif asked['type'] == 'choice':
            options = list(asked['criteria'])
            top = options[int(u * len(options)) % len(options)]
            rest = (1 - .7) / max(1, len(options) - 1) if len(options) > 1 else 0
            probs = {o: (.7 if o == top else rest) if len(options) > 1 else 1.0 for o in options}
            n = len(options)
            found[name] = {'type': 'choice', 'choice': top, 'probabilities': probs,
                           'confidence': 1.0 if n == 1 else (max(probs.values()) - 1 / n) / (1 - 1 / n)}
        else:
            levels = [str(i) for i in range(len(asked['criteria']))]
            pick = int(u * len(levels)) % len(levels)
            probs = {level: 1.0 if i == pick else 0.0 for i, level in enumerate(levels)}
            found[name] = {'type': 'score', 'score': float(pick), 'probabilities': probs, 'confidence': 1.0}
    return found


def model(salt):
    def ask(state, questions, images):
        return {'model': 'fake-' + salt, 'answers': answers(state, questions, salt), 'usage': {'input_tokens': 100, 'output_tokens': 0},
                'gaps': {}}
    return ask
