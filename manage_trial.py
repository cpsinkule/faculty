#!/usr/bin/env python3
"""Start the cloud-grading trial instance: this checkout's code on port 3200, against a COPY of the preview catalog.

    python3 manage_trial.py                 # web app + Inbox worker (with the parallel cloud dispatcher)
    python3 manage_trial.py --refresh-copy  # first replace the copy with a fresh one of the preview (discards the trial's work)

It never opens the live catalog or the instructor's preview: the data directory must be under
/mnt/nvme/Teaching/tmp, the resolved database path is verified before anything is served, and ports 3000
(live) and 3100 (preview) are refused. Provider keys are read from the server's grading key file into the
child processes' environment, as the deployed units do with EnvironmentFile; no value is ever printed.
With --gradescope (and only then) the children may sign in to Gradescope with the saved sign-in, for a sandbox rehearsal.
Stop it with Ctrl+C; both children are stopped by PID.
"""
import argparse
import os
from pathlib import Path
import re
import shutil
import signal
import sqlite3
import subprocess
import sys
import time
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent
SCRATCH = Path("/mnt/nvme/Teaching/tmp")
DEFAULT = SCRATCH / "cloud-grading-3200"
PREVIEW = Path("/mnt/nvme/datamgmt/faculty-preview/data")
KEYS = [Path.home() / ".config" / "faculty" / "grading.env",
        # The Vercel AI Gateway key lives in its own file, as the deployed units also load it.
        Path.home() / ".config" / "faculty" / "ai-gateway.env"]


# The one FACULTY_ setting the trial is given (requirement, 2026-09-25): the local model that reads answer
# areas on blank templates, so the trial's Answer areas step works. It is loopback only and sends nothing outward. It is the GPU
# live's worker also uses, and the two workers do not coordinate: a reading on both at once can run out of GPU memory and fail
# until it is retried.
LOCAL = ("FACULTY_GRADING_VERIFIER_URL",)


def key_environment():
    """Grading keys of the server's key file, for the children only. Missing file: cloud roles simply report not configured.

    Only what grading reads is passed on (endpoint key variables, Mathpix, and the loopback local model in `LOCAL`). No other
    setting named FACULTY_ ever reaches the trial: no mail relay, Gmail sender or response-card Worker, so the copy's worker can
    grade and can do nothing outward.
    """
    found = {}
    for keyfile in KEYS:
        if not keyfile.is_file():
            continue
        for line in keyfile.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                name, value = line.split("=", 1)
                name = name.removeprefix("export ").strip()
                value = value.strip().strip("'\"")
                if re.fullmatch(r"[A-Z][A-Z0-9_]*_API_KEY|MATHPIX_[A-Z0-9_]+", name) and not name.startswith("FACULTY_"):
                    found[name] = value
                elif name in LOCAL and urlsplit(value).hostname in ("127.0.0.1", "localhost", "::1"):
                    found[name] = value
    return found


def refresh(data):
    """A consistent copy: SQLite's backup API for the catalog, then the payload folders it references."""
    if data.exists():
        shutil.rmtree(data)
    data.mkdir(parents=True, mode=0o700)
    source = sqlite3.connect(f"file:{PREVIEW / 'faculty.sqlite3'}?mode=ro", uri=True)
    target = sqlite3.connect(data / "faculty.sqlite3")
    with target:
        source.backup(target)
    if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
        raise SystemExit("The copied catalog failed its integrity check.")
    source.close()
    target.close()
    for folder in ("definitions", "sections", "terms", "inbox"):
        if (PREVIEW / folder).is_dir():
            shutil.copytree(PREVIEW / folder, data / folder, symlinks=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=3200)
    parser.add_argument("--data-dir", type=Path, default=DEFAULT / "data")
    parser.add_argument("--refresh-copy", action="store_true", help="Replace the copy with a fresh one of the preview catalog. Discards the trial's own work.")
    parser.add_argument("--no-worker", action="store_true", help="Serve the web app only.")
    parser.add_argument("--gradescope", action="store_true", help="Let the trial sign in to Gradescope with the saved sign-in "
                        "(~/.config/faculty/gradescope.json), for a rehearsal in a sandbox course. Off by default: the trial otherwise sends nothing outward.")
    args = parser.parse_args()
    data = args.data_dir.expanduser().resolve()
    if SCRATCH not in data.parents:
        raise SystemExit(f"The trial instance only runs on a copy under {SCRATCH}; refusing {data}.")
    if args.port in (3000, 3001, 3100):
        raise SystemExit("Ports 3000, 3001 and 3100 belong to the live service, the email listener and the preview.")
    if args.refresh_copy:
        refresh(data)
    if not (data / "faculty.sqlite3").is_file():
        raise SystemExit(f"No catalog copy at {data}. Run with --refresh-copy to make one from the preview.")
    scratch = data.parent / "tmp"
    scratch.mkdir(mode=0o700, exist_ok=True)
    env = {key: value for key, value in os.environ.items() if not key.startswith("FACULTY_")}
    env.update(key_environment())
    env.update(DATA_DIR=str(data), HOST="127.0.0.1", PORT=str(args.port), TMPDIR=str(scratch), PYTHONDONTWRITEBYTECODE="1",
               FACULTY_STORAGE_CONFIG=str(data.parent / "no-storage-configuration.json"))
    env.pop("FACULTY_REQUIRE_STORAGE", None)
    if args.gradescope:
        # Sending grades to Gradescope (AGENTS.md), only when asked for: the PATH of the saved sign-in, checked as the app checks it.
        saved = Path.home() / ".config" / "faculty" / "gradescope.json"
        try:
            info = saved.lstat()
        except OSError:
            raise SystemExit(f"--gradescope needs the saved sign-in {saved}; run save-gradescope.py first.") from None
        if not saved.is_file() or saved.is_symlink() or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise SystemExit(f"--gradescope refuses {saved}: it must be a file of this account with mode 0600.")
        env["FACULTY_GRADESCOPE_CREDENTIALS_FILE"] = str(saved)
    # Verify what the application would open BEFORE serving it: a copy started beside a storage configuration once opened the live catalog.
    python = str(ROOT / ".venv" / "bin" / "python") if (ROOT / ".venv" / "bin" / "python").is_file() else sys.executable
    resolved = subprocess.run([python, "-c", "from app import create_app; print(create_app().config['DATABASE'])"], cwd=ROOT, env=env, capture_output=True, text=True)
    opened = Path(resolved.stdout.strip().splitlines()[-1]).resolve() if resolved.returncode == 0 and resolved.stdout.strip() else None
    if opened != data / "faculty.sqlite3":
        raise SystemExit(f"Refusing to serve: the application would open {opened or resolved.stderr.strip()[-400:]}, not the trial copy.")
    print(f"Trial catalog verified: {opened}", flush=True)
    children = [subprocess.Popen([python, str(ROOT / "app.py")], cwd=ROOT, env=env)]
    if not args.no_worker:
        children.append(subprocess.Popen([python, str(ROOT / "manage_inbox.py"), "worker"], cwd=ROOT, env=env))
    print(f"Cloud grading trial instance: http://127.0.0.1:{args.port}/#grading  (Ctrl+C stops it)", flush=True)

    def stop(*_):
        for child in children:
            if child.poll() is None:
                child.send_signal(signal.SIGTERM)
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    try:
        while all(child.poll() is None for child in children):
            time.sleep(1)
    finally:
        stop()
        for child in children:
            try:
                child.wait(timeout=20)
            except subprocess.TimeoutExpired:
                child.kill()
    return max((child.returncode or 0) for child in children)


if __name__ == "__main__":
    raise SystemExit(main())
