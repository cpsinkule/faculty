#!/usr/bin/env python3
"""Run Faculty checks with synthetic data, offline dependencies, and NVMe scratch."""
import argparse
import ast
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parent
QUICK = ("test_app", "test_course_codes", "test_course_groups", "test_catalog_migrations", "test_storage_restore", "test_manage_checks")
DEFAULT_BROWSERS = ("home", "smoke", "course_pages", "storage", "theme")


def scratch_root(value):
    root = Path(value).expanduser()
    if not root.is_absolute() or Path("/mnt") not in root.resolve().parents:
        raise ValueError("Checks need an absolute scratch directory on a /mnt drive; root-disk scratch is refused.")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    return root.resolve()


# Deployment configuration and every cloud credential: inference hosts, aggregators and GPU providers. An endpoint
# names its own key variable, so anything that looks like a key or token is stripped as well. Checks stay offline.
CREDENTIAL_PREFIXES = ("FACULTY_", "NEBIUS_", "MATHPIX_", "OPENROUTER_", "DEEPINFRA_", "FIREWORKS_", "NOVITA_", "TOGETHER_", "GROQ_", "HYPERBOLIC_",
                       "OPENAI_", "ANTHROPIC_", "GEMINI_", "GOOGLE_", "MISTRAL_", "MODAL_", "RUNPOD_", "LAMBDA_", "VAST_", "HF_", "HUGGING", "GRADESCOPE_")
CREDENTIAL_SUFFIXES = ("_API_KEY", "_API_TOKEN", "_TOKEN", "_TOKEN_ID", "_TOKEN_SECRET", "_SECRET", "_SECRET_KEY", "_APP_KEY", "_PASSWORD")


def environment(work):
    """No inherited deployment configuration, mail or cloud credentials, or live catalog."""
    env = {key: value for key, value in os.environ.items() if not key.startswith(CREDENTIAL_PREFIXES) and not key.endswith(CREDENTIAL_SUFFIXES)}
    env.pop("FACULTY_PRINTER_URI", None)  # checks never reach the office printer (DOCUMENTS "SEND TO PRINTER"); tests use a fake
    for name in ("tmp", "cache", "fallback-data"):
        (work / name).mkdir(mode=0o700, exist_ok=True)
    env.update(TMPDIR=str(work / "tmp"), TMP=str(work / "tmp"), TEMP=str(work / "tmp"),
               XDG_CACHE_HOME=str(work / "cache"), PYTHONPYCACHEPREFIX=str(work / "cache" / "python"),
               PYTHONDONTWRITEBYTECODE="1", PYTHONPATH=os.pathsep.join((str(ROOT), str(ROOT / "tests"))),
               NODE_COMPILE_CACHE=str(work / "cache" / "node"), HF_HOME=str(work / "cache" / "huggingface"),
               DATA_DIR=str(work / "fallback-data"), FACULTY_STORAGE_CONFIG=str(work / "unused-storage.json"),
               OPENBLAS_NUM_THREADS="1", OMP_NUM_THREADS="1", PYTHONUNBUFFERED="1",
               # The subscription connections run the signed-in `claude`, `codex` and `agy` binaries; checks never reach them (tests use fakes).
               FACULTY_CLAUDE_CLI=str(work / "no-claude-binary"), FACULTY_CODEX_CLI=str(work / "no-codex-binary"),
               FACULTY_ANTIGRAVITY_CLI=str(work / "no-agy-binary"))
    # Preserve installed, read-only runtimes before changing cache locations.
    browser_cache = Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache"))) / "ms-playwright"
    if "PLAYWRIGHT_BROWSERS_PATH" not in env and browser_cache.is_dir():
        env["PLAYWRIGHT_BROWSERS_PATH"] = str(browser_cache)
    browser_libs = ROOT / ".tools" / "browser-libs" / "usr" / "lib" / "x86_64-linux-gnu"
    if browser_libs.is_dir():
        env["LD_LIBRARY_PATH"] = os.pathsep.join(filter(None, (str(browser_libs), env.get("LD_LIBRARY_PATH"))))
    toolchain = Path(os.environ.get("FACULTY_TEXLIVE_ROOT", str(ROOT / ".tools" / "texlive")))
    if (toolchain / "usr" / "bin" / "pdflatex").is_file():
        env["FACULTY_TEXLIVE_ROOT"] = str(toolchain)
    return env


def python_files():
    yield from ROOT.glob("*.py")
    for folder in sorted(ROOT.iterdir()):
        if folder.is_dir() and not folder.name.startswith(".") and folder.name not in {"cloudflare", "data", "test-results", "__pycache__"}:
            yield from folder.rglob("*.py")


def python_syntax():
    files = list(python_files())
    for path in files:
        ast.parse(path.read_bytes(), filename=str(path))
    print(f"Python syntax: {len(files)} files passed.", flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("suite", nargs="?", default="quick", choices=("quick", "full", "browser", "restore"))
    parser.add_argument("--scratch-root", default=os.environ.get("FACULTY_CHECKS_SCRATCH", "/mnt/nvme/Teaching/tmp/checks"))
    parser.add_argument("--keep", action="store_true", help="Keep this run's logs/screenshots on NVMe even when every check passes.")
    parser.add_argument("--browser", action="append", dest="browsers", metavar="NAME", help="Browser script name, e.g. theme; repeat to select multiple (browser suite only).")
    parser.add_argument("--all-browsers", action="store_true", help="Run every isolated browser script (browser suite only).")
    args = parser.parse_args(argv)
    if args.suite != "browser" and (args.browsers or args.all_browsers):
        parser.error("--browser and --all-browsers require the browser suite")
    if args.browsers and args.all_browsers:
        parser.error("Choose --browser names or --all-browsers")
    browser_paths = []
    if args.suite == "browser":
        names = args.browsers or DEFAULT_BROWSERS
        if args.all_browsers:
            browser_paths = sorted((ROOT / "tests").glob("browser_*.py"))
        else:
            for name in names:
                if not name.replace("_", "").isalnum():
                    parser.error(f"Invalid browser check: {name}")
                path = ROOT / "tests" / f"browser_{name}.py"
                if not path.is_file():
                    parser.error(f"Unknown browser check: {name}")
                browser_paths.append(path)
    try:
        base = scratch_root(args.scratch_root)
    except (ValueError, OSError) as error:
        parser.error(str(error))
    work = Path(tempfile.mkdtemp(prefix=f"{args.suite}-", dir=base))
    env = environment(work)
    python = str(ROOT / ".venv" / "bin" / "python") if (ROOT / ".venv" / "bin" / "python").is_file() else sys.executable
    node = str(ROOT / ".tools" / "node22" / "bin" / "node") if (ROOT / ".tools" / "node22" / "bin" / "node").is_file() else shutil.which("node")
    results = []
    print(f"Faculty {args.suite} checks · synthetic workspace: {work}\nPython: {python}", flush=True)

    def run(label, command):
        started = time.monotonic()
        print(f"\n{label}", flush=True)
        logfile = work / (f"{len(results) + 1:02d}-" + label.lower().replace(" ", "-").replace("/", "-") + ".log")
        try:
            with logfile.open("w") as log:
                process = subprocess.Popen(command, cwd=work, env=env, text=True, stdin=subprocess.DEVNULL,
                                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
                for line in process.stdout:
                    print(line, end="", flush=True)
                    log.write(line)
                    log.flush()
                code = process.wait()
        except OSError as error:
            print(f"Cannot run {label}: {error}", file=sys.stderr)
            code = 1
        results.append((label, code))
        print(f"{'PASS' if code == 0 else 'FAIL'} {label} ({time.monotonic() - started:.1f}s)", flush=True)
        return code == 0

    successful = False
    try:
        if args.suite in {"quick", "full", "browser"}:
            try:
                python_syntax()
            except (SyntaxError, OSError) as error:
                print(error, file=sys.stderr)
                results.append(("Python syntax", 1))
            if node:
                # Node's module parser checks browser modules without changing
                # package-wide module settings or installing a bundler.
                source = "\n".join([
                    "const fs = require('node:fs'); const cp = require('node:child_process');",
                    "const path = require('node:path'); const root = process.argv[1]; let failed = false;",
                    "for (const name of fs.readdirSync(root).filter(n => n.endsWith('.js'))) {",
                    " const result = cp.spawnSync(process.execPath, ['--input-type=module', '--check'], {input:fs.readFileSync(path.join(root,name)),encoding:'utf8'});",
                    " if (result.status !== 0) { failed = true; console.error(name, result.stderr || result.error); }",
                    "} if (failed) process.exit(1); console.log('Browser JavaScript syntax passed.');",
                ])
                run("JavaScript syntax", [node, "-e", source, str(ROOT / "static")])
                test_files = sorted((ROOT / "tests").glob("test_*.mjs"))
                if test_files:
                    run("Frontend unit tests", [node, "--test", *map(str, test_files)])
                config = ROOT / "tsconfig.frontend.json"
                candidates = (ROOT / "node_modules" / "typescript" / "bin" / "tsc",
                              ROOT / "cloudflare" / "teach-inbox" / "node_modules" / "typescript" / "bin" / "tsc",
                              ROOT / "cloudflare" / "response-cards" / "node_modules" / "typescript" / "bin" / "tsc")
                compiler = next((path for path in candidates if path.is_file()), None)
                command = [node, str(compiler)] if compiler else ([shutil.which("tsc")] if shutil.which("tsc") else None)
                if config.is_file() and command:
                    run("Frontend typecheck", [*command, "--project", str(config)])
                else:
                    print("SKIP frontend typecheck: no local TypeScript compiler/configuration; nothing was installed.", flush=True)
            else:
                print("SKIP frontend checks: Node is not installed.", flush=True)
        if args.suite == "quick":
            modules = [*QUICK, "test_web_assets", "test_workspace_overview",
                       "test_calendar_service", "test_workspace_calendar", "test_calendar_protection", "test_google_calendar", "test_gmail_mail", "test_student_photos", "test_communications", "test_recordings", "test_recordings_page", "test_recordings_reading", "test_recordings_student_meetings", "test_recordings_classes", "test_recordings_memos",
                       "test_assignment_templates", "test_exam_templates",
                       "test_gradebook", "test_gradebook_compute", "test_gradebook_imports"]
            run("Quick API and recovery tests", [python, "-B", "-m", "unittest", *modules, "-q"])
        elif args.suite == "full":
            run("Full Python suite", [python, "-B", "-m", "unittest", "discover", "-s", str(ROOT / "tests"), "-v"])
        elif args.suite == "restore":
            run("Isolated backup restore exercise", [python, "-B", "-m", "unittest", "test_storage_restore", "-v"])
        else:
            preflight = "from playwright.sync_api import sync_playwright\nwith sync_playwright() as p:\n browser=p.chromium.launch(headless=True)\n browser.close()\nprint('Installed Chromium and system libraries are available.')"
            if run("Browser dependency check", [python, "-B", "-c", preflight]):
                for path in browser_paths:
                    run(path.stem.replace("_", " "), [python, "-B", str(path)])
            else:
                print("Browser checks need the existing Playwright package, Chromium, and system libraries; nothing was downloaded.", file=sys.stderr)
        successful = bool(results) and all(code == 0 for _, code in results)
        failed = [label for label, code in results if code]
        print(f"\n{len(results) - len(failed)}/{len(results)} check groups passed." + (f" Failed: {', '.join(failed)}." if failed else ""), flush=True)
        return 0 if successful else 1
    finally:
        if args.keep or not successful:
            print(f"Logs and any screenshots: {work}", flush=True)
        else:
            shutil.rmtree(work)


if __name__ == "__main__":
    raise SystemExit(main())
