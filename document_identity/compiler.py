"""Reproducible pdfLaTeX builds with no network, shell escape, or catalog access."""
import json
import os
from pathlib import Path, PurePosixPath
import resource
import subprocess
import tempfile
import hashlib
import shutil

from .process import sandbox_command

def sha(content):
    return hashlib.sha256(content).hexdigest()

TEMPLATES = Path(__file__).parent / "templates"
EPOCH = "1789257600"  # Fixed build epoch; actual registration time is catalog metadata.


class CompileError(ValueError):
    """A build that failed, with its log when pdfLaTeX wrote one (a drafted quiz sends the error back to its model to fix)."""

    def __init__(self, message, log=b""):
        super().__init__(message)
        self.log = log


def compile_sources(sources, *, toolchain_root=None, entrypoint="main.tex"):
    if not isinstance(sources, dict) or not isinstance(entrypoint, str) or entrypoint not in sources or len(sources) > 100:
        raise ValueError("Upload the LaTeX entrypoint and at most 99 supporting source files.")
    if PurePosixPath(entrypoint).suffix.lower() not in (".tex", ".latex"):
        raise ValueError("The LaTeX entrypoint must be a .tex or .latex file.")
    total = 0
    for name, content in sources.items():
        if not isinstance(name, str):
            raise ValueError("Source filenames must be text.")
        path = PurePosixPath(name)
        if (path.is_absolute() or str(path) != name or any(p in (".", "..", "") for p in name.split("/"))
                or "\\" in name or len(name) > 200 or path.suffix.lower() not in (".tex", ".latex", ".sty", ".png", ".jpg", ".jpeg", ".pdf")
                or not isinstance(content, bytes)):
            raise ValueError("Sources must be safe relative .tex, .sty, PDF, PNG, or JPEG filenames.")
        total += len(content)
        if path.parts[0] == "results" or any(parent.as_posix() in sources for parent in path.parents if parent != PurePosixPath('.')):
            raise ValueError("Source filenames conflict with another file or the compiler's results directory.")
    if total > 25 * 1024**2:
        raise ValueError("Source inputs exceed 25 MB.")
    retained = {**sources, "facultyassignment.cls": (TEMPLATES / "facultyassignment.cls").read_bytes()}
    with tempfile.TemporaryDirectory(prefix="faculty-latex-") as temporary:
        root = Path(temporary)
        for name, content in retained.items():
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        (root / "source-files.json").write_text(json.dumps(list(retained)))
        (root / "entrypoint.json").write_text(json.dumps(entrypoint))
        results = root / "results"
        results.mkdir()
        env = {"PATH": "/usr/bin:/bin", "HOME": "/work", "TEXMFOUTPUT": "/work", "TEXMFVAR": "/work/texmf-var",
               "openin_any": "p", "openout_any": "p", "SOURCE_DATE_EPOCH": EPOCH, "FORCE_SOURCE_DATE": "1", "TZ": "UTC"}
        toolchain = toolchain_root if toolchain_root is not None else os.environ.get("FACULTY_TEXLIVE_ROOT")
        if toolchain:
            tree = "/toolchain/usr/share/texlive/texmf-dist"
            env.update(FACULTY_ISOLATED_TEXLIVE="/toolchain", LD_LIBRARY_PATH="/toolchain/usr/lib/x86_64-linux-gnu",
                       TEXMFCNF=tree + "/web2c", TEXMF=tree, TEXINPUTS=".:" + tree + "/tex//",
                       TEXFORMATS="/toolchain/formats", TFMFONTS=tree + "/fonts/tfm//",
                       T1FONTS=tree + "/fonts/type1//", TEXFONTMAPS="/toolchain/formats:" + tree + "/fonts/map//")
        command = sandbox_command(root, ["/venv/bin/python", "-m", "document_identity.compiler", "--child"], compiler=True, toolchain_root=toolchain)
        try:
            result = subprocess.run(command, env=env, cwd=TEMPLATES.parent.parent, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL, timeout=70)
        except subprocess.TimeoutExpired as error:
            raise ValueError("LaTeX compilation exceeded its time limit.") from error
        if result.returncode or not (results / "main.pdf").is_file():
            log = (results / "build.log").read_bytes() if (results / "build.log").is_file() else b""
            raise CompileError("Isolated LaTeX compilation failed. Check the class, source, or toolchain installation.", log)
        pdf = (results / "main.pdf").read_bytes()
        # Log and compiler provenance are immutable inputs alongside source.
        retained["build.log"] = (results / "build.log").read_bytes()
        details = json.loads((results / "toolchain.json").read_text())
        details.update(shell_escape=False, network=False, source_date_epoch=EPOCH,
                       class_sha256=sha(retained["facultyassignment.cls"]), passes=2,
                       limits={"wall_seconds": 70, "cpu_seconds": 60, "address_space_mb": 1024, "file_mb": 30, "scratch_mb": 128})
        return pdf, retained, details


def child():
    resource.setrlimit(resource.RLIMIT_AS, (1024**3, 1024**3))
    resource.setrlimit(resource.RLIMIT_CPU, (60, 60))
    resource.setrlimit(resource.RLIMIT_FSIZE, (30 * 1024**2, 30 * 1024**2))
    resource.setrlimit(resource.RLIMIT_NPROC, (32, 32))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    root = Path("/work")
    for name in json.loads(Path("/inputs/source-files.json").read_text()):
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(Path("/inputs") / name, target)
    executable = os.environ.get("FACULTY_ISOLATED_TEXLIVE", "") + "/usr/bin/pdflatex"
    toolchain = subprocess.check_output([executable, "--version"], timeout=5).decode().splitlines()[0]
    entrypoint = json.loads(Path("/inputs/entrypoint.json").read_text())
    try:
        with (root / "build.log").open("wb") as log:
            for _ in range(2):
                subprocess.run([executable, "-no-shell-escape", "-halt-on-error", "-interaction=nonstopmode", "-recorder", "-jobname=main", "./" + entrypoint],
                               cwd=root, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=30)
    finally:
        shutil.copyfile(root / "build.log", "/results/build.log")
    shutil.copyfile(root / "main.pdf", "/results/main.pdf")
    Path("/results/toolchain.json").write_text(json.dumps({"engine": toolchain}))


if __name__ == "__main__":
    import sys
    if sys.argv[1:] == ["--child"]:
        child()
    elif sys.argv[1:] == ["--fixtures"]:
        destination = Path(__file__).parent / "fixtures"
        for paper in ("letter", "a4"):
            for compact in (False, True):
                name = paper + ("-corners" if compact else "")
                options = paper + "paper" + (",compactcorners" if compact else ",pagecode")
                source = (TEMPLATES / "example.tex").read_text().replace("letterpaper", options).encode()
                pdf, retained, details = compile_sources({"main.tex": source})
                (destination / f"{name}.tex").write_bytes(source)
                (destination / f"{name}.pdf").write_bytes(pdf)
                (destination / f"{name}.build.log").write_bytes(retained["build.log"])
                (destination / f"{name}.build.json").write_text(json.dumps(details, indent=2) + "\n")
    else:
        raise SystemExit("Use --fixtures to rebuild the checked-in demonstration PDFs.")
