"""Run tests/browser_*.py in parallel, each in its own isolated scratch directory (manage_checks.environment).

Usage: python3 browser_suite.py [--jobs 6] [--root REPO] [--scratch /mnt/...] [--retry-failed] [name ...]
Names are browser_<name>.py stems (theme, navigation, ...); none means every script. Each script gets manage_checks'
offline environment (no credentials, no live catalog, NVMe scratch, bundled browser libraries and TeX root). Prints
PASS/FAIL per script with its duration and, for failures, the log's last lines; logs stay under the scratch root.
--retry-failed runs each failure once more on its own (to tell timing races from real failures).
This is the parallel equivalent of `python3 manage_checks.py browser --all-browsers --keep` (which runs serially).
"""
import argparse
import concurrent.futures
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--jobs", type=int, default=6)
parser.add_argument("--root", default=str(Path(__file__).resolve().parents[2]))
parser.add_argument("--scratch", default=os.environ.get("FACULTY_CHECKS_SCRATCH", "/mnt/nvme/Teaching/tmp/checks"))
parser.add_argument("--retry-failed", action="store_true")
parser.add_argument("--timeout", type=int, default=1800)
parser.add_argument("names", nargs="*")
args = parser.parse_args()

ROOT = Path(args.root).resolve()
sys.path.insert(0, str(ROOT))
import manage_checks  # noqa: E402

assert manage_checks.ROOT == ROOT, manage_checks.ROOT
base = manage_checks.scratch_root(args.scratch)
run_dir = Path(tempfile.mkdtemp(prefix="suite-", dir=base))
python = str(ROOT / ".venv" / "bin" / "python")
scripts = ([ROOT / "tests" / f"browser_{n}.py" for n in args.names] if args.names
           else sorted((ROOT / "tests").glob("browser_*.py")))
for s in scripts:
    if not s.is_file():
        sys.exit(f"missing {s}")


def run(script, attempt=1):
    work = Path(tempfile.mkdtemp(prefix=f"{script.stem}-{attempt}-", dir=run_dir))
    env = manage_checks.environment(work)
    log = work / "log.txt"
    started = time.monotonic()
    with log.open("w") as out:
        try:
            code = subprocess.call([python, "-B", str(script)], cwd=work, env=env, stdin=subprocess.DEVNULL,
                                   stdout=out, stderr=subprocess.STDOUT, timeout=args.timeout)
        except subprocess.TimeoutExpired:
            code = "timeout"
    return script.stem, code, time.monotonic() - started, log


def tail(log, n=25):
    lines = log.read_text(errors="replace").splitlines()
    return "\n".join("      " + line for line in lines[-n:])


print(f"{len(scripts)} scripts · {args.jobs} at a time · logs under {run_dir}", flush=True)
results = {}
with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
    for name, code, seconds, log in pool.map(run, scripts):
        results[name] = (code, seconds, log)
        print(f"{'PASS' if code == 0 else 'FAIL'} {name} ({seconds:.0f}s)", flush=True)
failed = [n for n, (code, _, _) in results.items() if code != 0]
if args.retry_failed and failed:
    print(f"\nRetrying {len(failed)} failures one at a time", flush=True)
    for name in failed:
        _, code, seconds, log = run(ROOT / "tests" / f"{name}.py", attempt=2)
        print(f"{'PASS' if code == 0 else 'FAIL'} {name} on retry ({seconds:.0f}s)", flush=True)
        results[name] = (code, seconds, log) if code != 0 else ("flaky (passed on retry)", seconds, log)
for name in sorted(results):
    code, _, log = results[name]
    if code != 0 and not str(code).startswith("flaky"):
        print(f"\n--- {name}: {code} · {log}\n{tail(log)}")
passed = sum(1 for code, _, _ in results.values() if code == 0)
flaky = [n for n, r in results.items() if isinstance(r[0], str) and r[0].startswith("flaky")]
hard = [n for n, r in results.items() if r[0] != 0 and n not in flaky]
print(f"\n{passed}/{len(results)} passed first time. Flaky (passed on retry): {', '.join(flaky) or 'none'}."
      f" Failed: {', '.join(hard) or 'none'}")
