"""Download one pinned model revision into HF_HOME. Run by `fetch --download` in the sandbox venv; the only child with
network besides the live Jev-Mem run. Prints each file's size and SHA-256 for the lock."""
import hashlib
import json
import sys
from pathlib import Path

from huggingface_hub import snapshot_download

repo, revision = sys.argv[1], sys.argv[2]
folder = Path(snapshot_download(repo, revision=revision,
                                allow_patterns=['*.json', '*.txt', '*.jinja', '*.safetensors', '*.model', '1_Pooling/*', '2_Dense/*', '3_Dense/*',
                                                '2_Normalize/*', 'tokenizer*', 'sentencepiece*']))
found = {}
for path in sorted(folder.rglob('*')):
    if path.is_file() and path.suffix in ('.safetensors', '.model'):
        digest = hashlib.sha256()
        with path.open('rb') as handle:
            for block in iter(lambda: handle.read(1 << 20), b''):
                digest.update(block)
        found[str(path.relative_to(folder))] = {'size': path.stat().st_size, 'sha256': digest.hexdigest()}
print(json.dumps({'repo': repo, 'revision': revision, 'folder': str(folder), 'files': found}))
