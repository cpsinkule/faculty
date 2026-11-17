"""The sandbox's own runtime on /mnt: a venv, pinned model weights and the pinned Jev-Mem checkout, none of it in Faculty's
venv or checkout. Children run under bubblewrap with no network (the live Jev-Mem child excepted), no home folder, no
Faculty checkout and no catalog; only their run folder is writable.

`fetch(download=True)` is the only step that downloads. Everything else verifies what is there against the lock
(memory_sandbox/model-lock.json: revision, size and SHA-256 of each weight file) and refuses to run otherwise.
"""
import hashlib
import json
import os
import subprocess
from pathlib import Path

from . import safety
from .embedders import MODELS

HERE = Path(__file__).resolve().parent
LOCK = HERE / 'model-lock.json'
JEVMEM = {'repo': 'https://github.com/libingzheren/Jev-Mem.git', 'commit': '7ab0c73c6d8f4f611ad252c1e6ba8083f8df0e44'}
# What a child may see of the environment. Never a key, unless a caller adds one for a child that needs it.
ENVIRONMENT = {'PATH': '/venv/bin:' + os.defpath, 'PYTHONDONTWRITEBYTECODE': '1', 'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1',
               'HF_HUB_DISABLE_TELEMETRY': '1', 'TOKENIZERS_PARALLELISM': 'false', 'HF_HOME': '/hf', 'XDG_CACHE_HOME': '/tmp/cache',
               'HOME': '/tmp/home', 'CUDA_VISIBLE_DEVICES': '', 'LANG': 'C.UTF-8'}


class RuntimeMissing(RuntimeError):
    pass


def root():
    return safety.RUNTIME


def paths():
    base = root()
    return {'venv': base / 'venv', 'hf': base / 'hf', 'jevmem': base / 'jev-mem' / 'src', 'uv': base / 'uv-cache',
            'vectors': base / 'vectors.sqlite3'}


def sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for block in iter(lambda: handle.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


def snapshot(model):
    """The folder a pinned model revision was downloaded to, in the Hugging Face cache layout."""
    spec = MODELS[model]
    return paths()['hf'] / 'hub' / ('models--' + spec['repo'].replace('/', '--')) / 'snapshots' / spec['revision']


def verify(model):
    """The lock's weight file is present with the locked size and SHA-256; returns its path."""
    lock = json.loads(LOCK.read_text())[model]
    weights = snapshot(model) / lock['file']
    if not weights.is_file():
        raise RuntimeMissing(f'{model}: weights are not downloaded. Run: manage_memory_sandbox.py fetch --download')
    if weights.stat().st_size != lock['size'] or sha256(weights) != lock['sha256']:
        raise RuntimeMissing(f'{model}: weights do not match the lock.')
    return weights


def bwrap(arguments, work, network=False, environment=None, cwd='/work'):
    """argv for a child: the venv, model cache and Jev-Mem checkout read-only, `work` writable at /work, nothing else of
    this machine's data. The interpreter the venv was built on is bound where the venv expects it."""
    found = paths()
    command = ['bwrap', '--unshare-all', *(['--share-net'] if network else []), '--die-with-parent', '--new-session',
               '--ro-bind', '/usr', '/usr', '--ro-bind', '/lib', '/lib', '--ro-bind', '/lib64', '/lib64',
               '--ro-bind', '/etc/ssl', '/etc/ssl', '--ro-bind', '/etc/resolv.conf', '/etc/resolv.conf',
               '--proc', '/proc', '--dev', '/dev', '--tmpfs', '/tmp',
               '--ro-bind', str(found['venv']), '/venv', '--ro-bind', str(found['hf']), '/hf',
               '--ro-bind', str(HERE / 'children'), '/children', '--bind', str(work), '/work']
    if found['jevmem'].exists():
        command += ['--ro-bind', str(found['jevmem']), '/jevmem']
    base = (found['venv'] / 'bin' / 'python').resolve()
    managed = root() / 'python'
    if base.is_relative_to(managed.resolve()):
        # uv reaches its Python through a version symlink beside it, so the whole folder is bound where it is.
        command += ['--ro-bind', str(managed), str(managed)]
    elif not base.is_relative_to('/usr'):
        home = base.parents[1]
        command += ['--ro-bind', str(home), str(home)]
    command += ['--chdir', cwd, '--clearenv']
    for name, value in {**ENVIRONMENT, **(environment or {})}.items():
        command += ['--setenv', name, value]
    return command + ['--', *arguments]


def run_child(arguments, work, network=False, environment=None, timeout=3600, cwd='/work'):
    """Run one child to its end; its stdout and stderr go to files in `work`. Keys in `environment` never reach argv."""
    work = Path(work)
    work.mkdir(parents=True, exist_ok=True)
    with (work / 'child.out').open('ab') as out, (work / 'child.err').open('ab') as err:
        return subprocess.run(bwrap(arguments, work, network, environment, cwd), stdout=out, stderr=err, timeout=timeout,
                              env={'PATH': os.defpath}, check=False).returncode


def _uv(arguments, environment):
    return subprocess.run(['uv', *arguments], env=environment, check=True)


def fetch(download=False, say=print):
    """What is present, verified. With `download`: build the venv from requirements.txt (CPU torch), freeze it to
    requirements.lock, download the pinned model revisions, and clone Jev-Mem at its pinned commit, read-only."""
    found = paths()
    if download:
        root().mkdir(parents=True, exist_ok=True, mode=0o700)
        # A Python of the sandbox's own under /mnt, so a child's jail binds nothing from a home folder.
        environment = {**os.environ, 'UV_CACHE_DIR': str(found['uv']), 'HF_HOME': str(found['hf']), 'XDG_CACHE_HOME': str(root() / 'cache'),
                       'UV_PYTHON_INSTALL_DIR': str(root() / 'python'), 'UV_PYTHON_PREFERENCE': 'only-managed'}
        if not (found['venv'] / 'bin' / 'python').exists():
            _uv(['venv', '--python', '3.12', str(found['venv'])], environment)
        lock = HERE / 'requirements.lock'
        source = lock if lock.exists() else HERE / 'requirements.txt'
        _uv(['pip', 'install', '--python', str(found['venv'] / 'bin' / 'python'), '--index-strategy', 'unsafe-best-match',
             '--extra-index-url', 'https://download.pytorch.org/whl/cpu', '-r', str(source)], environment)
        if not lock.exists():
            frozen = subprocess.run(['uv', 'pip', 'freeze', '--python', str(found['venv'] / 'bin' / 'python')], env=environment,
                                    check=True, capture_output=True, text=True).stdout
            lock.write_text(frozen)
        for model, spec in MODELS.items():
            say(f"downloading {spec['repo']} at {spec['revision'][:12]}")
            subprocess.run([str(found['venv'] / 'bin' / 'python'), str(HERE / 'children' / 'fetch_model.py'), spec['repo'], spec['revision']],
                           env={**environment, 'HF_HUB_DISABLE_TELEMETRY': '1'}, check=True)
        if not found['jevmem'].exists():
            found['jevmem'].parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(['git', 'clone', '--no-checkout', JEVMEM['repo'], str(found['jevmem'])], check=True,
                           env={**os.environ, 'GIT_TERMINAL_PROMPT': '0'})
            subprocess.run(['git', '-C', str(found['jevmem']), '-c', 'advice.detachedHead=false', 'checkout', JEVMEM['commit']], check=True)
            subprocess.run(['chmod', '-R', 'a-w', str(found['jevmem'])], check=True)
    state = {'venv': (found['venv'] / 'bin' / 'python').exists()}
    for model in MODELS:
        try:
            verify(model)
            state[model] = 'verified'
        except (RuntimeMissing, OSError, KeyError) as why:
            state[model] = str(why)
    if found['jevmem'].exists():
        head = subprocess.run(['git', '-C', str(found['jevmem']), 'rev-parse', 'HEAD'], capture_output=True, text=True).stdout.strip()
        state['jevmem'] = 'pinned' if head == JEVMEM['commit'] else f'at {head[:12]}, not the pinned commit'
    else:
        state['jevmem'] = 'not cloned'
    return state


AUDIT = (('network', r'\b(requests|httpx|urllib|aiohttp|socket|openai|typesafe)\b'), ('process', r'\b(subprocess|os\.system|os\.popen|pty)\b'),
         ('unpickling', r'\b(pickle|joblib|torch\.load|np\.load|marshal)\b'), ('environment', r'\b(load_dotenv|os\.environ|getenv)\b'),
         ('downloads', r'\b(from_pretrained|snapshot_download|hf_hub_download|SentenceTransformer\()'))


def audit(folder):
    """Where the pinned Jev-Mem source touches the network, other processes, pickles, the environment or downloads: one line
    per place, for a person to read before anything of it runs."""
    import re
    source = paths()['jevmem']
    if not source.exists():
        raise RuntimeMissing('Jev-Mem is not cloned.')
    lines = []
    for path in sorted(source.rglob('*.py')):
        if '.git' in path.parts:
            continue
        for number, text in enumerate(path.read_text(errors='replace').splitlines(), 1):
            for kind, pattern in AUDIT:
                if re.search(pattern, text):
                    lines.append(f'{kind}\t{path.relative_to(source)}:{number}\t{text.strip()[:160]}')
    out = Path(folder) / 'audit.txt'
    out.write_text('\n'.join(lines) + '\n')
    return out, len(lines)
