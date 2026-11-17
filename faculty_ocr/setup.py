"""Install the optional runtime and pinned, safetensors-only model assets."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from .config import MODEL_ID, MODEL_REVISION, ROOT, runtime_root


def file_hash(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def download():
    from huggingface_hub import snapshot_download
    root = runtime_root()
    model = root / "model" / MODEL_REVISION
    snapshot_download(MODEL_ID, revision=MODEL_REVISION, local_dir=model,
                      allow_patterns=["*.json", "*.jinja", "*.safetensors", "README.md"])
    files = {str(p.relative_to(model)): {"size": p.stat().st_size, "sha256": file_hash(p)}
             for p in sorted(model.iterdir()) if p.is_file()}
    required = {"model.safetensors", "config.json", "preprocessor_config.json", "tokenizer.json", "tokenizer_config.json", "chat_template.jinja"}
    if not required.issubset(files):
        raise RuntimeError("The pinned model download is incomplete.")
    lock = Path(__file__).with_name("model-lock.json")
    if lock.exists():
        expected = json.loads(lock.read_text())
        if expected["revision"] != MODEL_REVISION or files != expected["files"]:
            raise RuntimeError("Downloaded assets differ from the pinned model checksums.")
    record = {"model_id": MODEL_ID, "revision": MODEL_REVISION, "model_directory": str(model),
              "python_base": sys.base_prefix, "files": files, "remote_code": False}
    temporary = root / "model-manifest.json.tmp"
    temporary.write_text(json.dumps(record, indent=2) + "\n")
    temporary.replace(root / "model-manifest.json")
    print(json.dumps({"model_id": MODEL_ID, "revision": MODEL_REVISION,
                      "files": len(files), "bytes": sum(f["size"] for f in files.values())}))


def main():
    os.umask(0o077)
    if sys.argv[1:] == ["--download-only"]:
        download()
        return
    if sys.argv[1:]:
        raise SystemExit("Usage: python -m faculty_ocr.setup")
    uv = shutil.which("uv")
    if not uv:
        raise SystemExit("Install uv first, then rerun this setup command.")
    root = runtime_root()
    if root.parts[:2] != ("/", "mnt"):
        raise SystemExit("The OCR runtime belongs on a /mnt drive (STORAGE): set FACULTY_OCR_RUNTIME to a folder there.")
    root.mkdir(parents=True, exist_ok=True)
    # uv's and Hugging Face's caches stay with the runtime on NVMe, never in the home folder.
    environment = {**os.environ, "UV_CACHE_DIR": str(root / "uv-cache"), "HF_HOME": str(root / "hf-cache"), "XDG_CACHE_HOME": str(root / "cache")}
    python = root / "venv" / "bin" / "python"
    if not python.exists():
        subprocess.run([uv, "venv", "--python", sys.executable, str(root / "venv")], check=True, env=environment)
    lock = Path(__file__).with_name("requirements.lock")
    requirements = lock if lock.exists() else Path(__file__).with_name("requirements.txt")
    subprocess.run([uv, "pip", "install", "--python", str(python), "--index-strategy", "unsafe-best-match",
                    "-r", str(requirements)], check=True, env=environment)
    subprocess.run([str(python), "-m", "faculty_ocr.setup", "--download-only"], cwd=ROOT, check=True, env=environment)


if __name__ == "__main__":
    main()
