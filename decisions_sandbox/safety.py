"""Where the sandbox may write, which credentials it holds, and which catalog it may never open writable."""
import os
from pathlib import Path
import re
import tempfile

SCRATCH = Path('/mnt/nvme/Teaching/tmp/decisions-sandbox')
LIVE_CATALOG = Path('/mnt/nvme/Teaching/catalog/faculty.sqlite3')
KEY_FILES = (Path.home() / '.config' / 'faculty' / 'grading.env', Path.home() / '.config' / 'faculty' / 'ai-gateway.env')
KEYS = ('TYPESAFE_API_KEY', 'OPENAI_API_KEY', 'SUBQ_API_KEY', 'OPENROUTER_API_KEY', 'GEMINI_API_KEY')
# As manage_checks.py strips them: deployment configuration and every credential-looking variable.
PREFIXES = ('FACULTY_', 'NEBIUS_', 'MATHPIX_', 'OPENROUTER_', 'DEEPINFRA_', 'FIREWORKS_', 'NOVITA_', 'TOGETHER_', 'GROQ_',
            'HYPERBOLIC_', 'OPENAI_', 'ANTHROPIC_', 'GEMINI_', 'GOOGLE_', 'MISTRAL_', 'MODAL_', 'RUNPOD_', 'LAMBDA_', 'VAST_', 'HF_',
            'HUGGING', 'GRADESCOPE_', 'AI_GATEWAY', 'VERCEL_', 'TYPESAFE_', 'SUBQ_')
SUFFIXES = ('_API_KEY', '_API_TOKEN', '_TOKEN', '_TOKEN_ID', '_TOKEN_SECRET', '_SECRET', '_SECRET_KEY', '_APP_KEY', '_PASSWORD')


def run_folder(name, scratch=None):
    """A new 0700 folder for one run, only under SCRATCH (on /mnt), or under another sandbox's own `scratch` there."""
    if not re.fullmatch(r'[A-Za-z0-9._-]{1,80}', name):
        raise ValueError('A run name is letters, digits, dot, dash and underscore.')
    scratch = SCRATCH if scratch is None else Path(scratch)
    if scratch.resolve().parts[:2] != ('/', 'mnt'):
        raise ValueError('Sandbox scratch stays on /mnt.')
    scratch.mkdir(parents=True, exist_ok=True, mode=0o700)
    folder = (scratch / name).resolve()
    if scratch.resolve() not in folder.parents:
        raise ValueError('The sandbox writes only under ' + str(scratch))
    folder.mkdir(mode=0o700, exist_ok=True)
    os.chmod(folder, 0o700)
    for sub in ('tmp', 'no-app'):
        (folder / sub).mkdir(mode=0o700, exist_ok=True)
    return folder


def scrub_environment(folder):
    """No deployment settings or credentials but the keys of the models this sandbox compares, read from the server's key files."""
    for name in list(os.environ):
        if name.startswith(PREFIXES) or name.endswith(SUFFIXES):
            del os.environ[name]
    found = {}
    for path in KEY_FILES:
        if not path.is_file():
            continue
        for line in path.read_text().splitlines():
            name, sep, value = line.strip().partition('=')
            name = name.removeprefix('export ').strip()
            if sep and name in KEYS and value.strip().strip('\'"'):
                found[name] = value.strip().strip('\'"')
    os.environ.update(found)
    os.environ.update(TMPDIR=str(folder / 'tmp'), TMP=str(folder / 'tmp'), TEMP=str(folder / 'tmp'),
                      DATA_DIR=str(folder / 'no-app'), FACULTY_STORAGE_CONFIG=str(folder / 'missing-storage.json'),
                      FACULTY_JEV_ENABLED='1')
    tempfile.tempdir = None
    return {name: name in found for name in KEYS}


def refuse_live(path):
    """Any path the sandbox opens for writing must not be (or resolve to) the live catalog."""
    if Path(path).resolve() == LIVE_CATALOG.resolve():
        raise ValueError('The sandbox never opens the live catalog for writing.')
