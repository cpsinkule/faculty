"""Install the embedding runtime on NVMe: its own Python and venv, and the pinned, safetensors-only model.

    python -m faculty_embedding.setup            # the venv from requirements.lock (or requirements.txt), then the model
    python -m faculty_embedding.setup --status   # what is installed, checksums verified; downloads nothing

The only step that downloads. Assets that differ from model-lock.json are refused; remote model code is never enabled.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from .config import MODEL_ID, MODEL_REVISION, ROOT, runtime_root

PATTERNS = ["*.json", "*.jinja", "*.safetensors", "tokenizer.model", "1_Pooling/*", "2_Normalize/*"]


def file_hash(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def found_files(model):
    return {str(p.relative_to(model)): {"size": p.stat().st_size, "sha256": file_hash(p)}
            for p in sorted(model.rglob("*")) if p.is_file() and ".cache" not in p.parts}


def download():
    """Runs in the runtime's venv: fetch the pinned revision, hold it to the lock, record where it is."""
    from huggingface_hub import snapshot_download
    root = runtime_root()
    model = root / "model" / MODEL_REVISION
    snapshot_download(MODEL_ID, revision=MODEL_REVISION, local_dir=model, allow_patterns=PATTERNS)
    files = found_files(model)
    expected = json.loads(Path(__file__).with_name("model-lock.json").read_text())
    if expected["revision"] != MODEL_REVISION or files != expected["files"]:
        raise RuntimeError("Downloaded assets differ from the pinned model checksums.")
    record = {"model_id": MODEL_ID, "revision": MODEL_REVISION, "model_directory": str(model),
              "python_base": sys.base_prefix, "files": files, "remote_code": False}
    temporary = root / "model-manifest.json.tmp"
    temporary.write_text(json.dumps(record, indent=2) + "\n")
    temporary.replace(root / "model-manifest.json")
    print(json.dumps({"model_id": MODEL_ID, "revision": MODEL_REVISION, "files": len(files), "bytes": sum(f["size"] for f in files.values())}))


def status():
    root = runtime_root()
    try:
        record = json.loads((root / "model-manifest.json").read_text())
        expected = json.loads(Path(__file__).with_name("model-lock.json").read_text())
        verified = found_files(Path(record["model_directory"])) == expected["files"]
    except (OSError, ValueError, KeyError):
        return {"installed": False}
    return {"installed": True, "revision": record["revision"], "checksums_verified": verified,
            "venv": (root / "venv" / "bin" / "python").exists(), "runtime": str(root)}


def main():
    os.umask(0o077)
    if sys.argv[1:] == ["--download-only"]:
        download()
        return
    if sys.argv[1:] == ["--status"]:
        print(json.dumps(status(), indent=1))
        return
    if sys.argv[1:]:
        raise SystemExit("Usage: python -m faculty_embedding.setup [--status]")
    uv = shutil.which("uv")
    if not uv:
        raise SystemExit("Install uv first, then rerun this setup command.")
    root = runtime_root()
    if root.parts[:2] != ("/", "mnt"):
        raise SystemExit("The embedding runtime belongs on a /mnt drive (STORAGE).")
    root.mkdir(parents=True, exist_ok=True)
    # A Python of the runtime's own, so the worker's jail binds nothing from a home folder; uv's caches stay on NVMe too.
    environment = {**os.environ, "UV_CACHE_DIR": str(root / "uv-cache"), "UV_PYTHON_INSTALL_DIR": str(root / "python"),
                   "UV_PYTHON_PREFERENCE": "only-managed", "HF_HOME": str(root / "hf-cache"), "XDG_CACHE_HOME": str(root / "cache")}
    python = root / "venv" / "bin" / "python"
    if not python.exists():
        subprocess.run([uv, "venv", "--python", "3.12", str(root / "venv")], check=True, env=environment)
    lock = Path(__file__).with_name("requirements.lock")
    requirements = lock if lock.exists() else Path(__file__).with_name("requirements.txt")
    subprocess.run([uv, "pip", "install", "--python", str(python), "--index-strategy", "unsafe-best-match",
                    "--extra-index-url", "https://download.pytorch.org/whl/cu128", "-r", str(requirements)], check=True, env=environment)
    if not lock.exists():
        frozen = subprocess.run([uv, "pip", "freeze", "--python", str(python)], check=True, capture_output=True, text=True, env=environment).stdout
        lock.write_text("--extra-index-url https://download.pytorch.org/whl/cu128\n" + frozen)
    subprocess.run([str(python), "-m", "faculty_embedding.setup", "--download-only"], cwd=ROOT, check=True, env=environment)


if __name__ == "__main__":
    main()
