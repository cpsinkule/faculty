"""Start the embedding worker in its jail: `python -m faculty_embedding.launch` (deploy/faculty-embedding.service).

The worker sees the runtime's venv, the pinned model and this package read-only, the GPU's device nodes, and one writable
folder for its socket. No network, no home folder, no Faculty checkout beyond this package, no catalog, no credentials.
"""
import json
import os
import sys
from pathlib import Path

from .config import device, runtime_root

ENVIRONMENT = {"PATH": "/venv/bin:" + os.defpath, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": "/app", "HF_HUB_OFFLINE": "1",
               "TRANSFORMERS_OFFLINE": "1", "HF_HUB_DISABLE_TELEMETRY": "1", "TOKENIZERS_PARALLELISM": "false",
               "HF_HOME": "/tmp/huggingface", "XDG_CACHE_HOME": "/tmp/cache", "HOME": "/tmp/home", "LANG": "C.UTF-8",
               "FACULTY_EMBEDDING_MODEL": "/model", "FACULTY_EMBEDDING_LISTEN": "/run-embedding/embedding.sock"}


class NotInstalled(RuntimeError):
    pass


def manifest(root):
    try:
        record = json.loads((root / "model-manifest.json").read_text())
        model = Path(record["model_directory"])
        if not (model / "model.safetensors").is_file() or not (root / "venv" / "bin" / "python").exists():
            raise ValueError("incomplete")
        return record, model
    except (OSError, KeyError, ValueError, TypeError) as cause:
        raise NotInstalled("The embedding runtime is not installed. Run python -m faculty_embedding.setup.") from cause


def command(root, record, model, wanted="auto"):
    """The bubblewrap argv of the worker."""
    run = root / "run"
    found = ["bwrap", "--unshare-all", "--die-with-parent", "--new-session",
             "--ro-bind", "/usr", "/usr", "--ro-bind", "/lib", "/lib", "--ro-bind", "/lib64", "/lib64",
             "--proc", "/proc", "--ro-bind", "/sys", "/sys", "--dev", "/dev", "--tmpfs", "/tmp",
             "--ro-bind", str(Path(__file__).parent), "/app/faculty_embedding",
             "--ro-bind", str(root / "venv"), "/venv", "--ro-bind", str(model), "/model",
             "--bind", str(run), "/run-embedding", "--chdir", "/app"]
    base = Path(record["python_base"]).resolve()
    if not base.is_relative_to("/usr"):
        # uv reaches its Python through a version symlink beside it, so the folder that holds both is bound where it is.
        home = base.parent if (root / "python") in base.parents else base
        found += ["--ro-bind", str(home), str(home)]
    if wanted != "cpu":
        for path in sorted(Path("/dev").glob("nvidia*")):
            found += ["--dev-bind", str(path), str(path)]
    found.append("--clearenv")
    for name, value in {**ENVIRONMENT, "FACULTY_EMBEDDING_DEVICE": wanted}.items():
        found += ["--setenv", name, value]
    return found + ["--", "/venv/bin/python", "-m", "faculty_embedding.worker"]


def main():
    root = runtime_root()
    record, model = manifest(root)
    (root / "run").mkdir(mode=0o700, exist_ok=True)
    argv = command(root, record, model, device())
    os.execvpe(argv[0], argv, {"PATH": os.defpath})


if __name__ == "__main__":
    try:
        main()
    except NotInstalled as missing:
        sys.exit(str(missing))
