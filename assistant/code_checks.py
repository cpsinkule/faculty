"""ASSISTANT: independent, workspace-owned checks of a coding task's exact commit."""
import hashlib
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from manage_checks import environment

# VERIFIED CODE APPLY (2026-10-09 review: a change to grading/, teach_inbox/ or scheduling/ ran only test_app, because the
# selection looked for tests/test_<package>.py). A changed file selects, in this order of priority:
#   0. the test files it changes;
#   1. its own tests: test_<family>_<stem>*, test_<stem>*, the test modules that import it (unless too many do to say which
#      matter), and the fixed companions below; for static/<name>.js, test_<name>*.mjs and the scripts that import it;
#   2. its package's whole family: every tests/test_<family>*.py.
# A package's families are its own name, or the names its tests actually use (FAMILIES). The selection is deterministic
# (priority, then name) and bounded: at most PYTHON_CAP modules, run CHUNK to a command, and SCRIPT_CAP Node scripts. A
# matching module beyond the caps is named in the receipt (`not_run`), never silently dropped.
FAMILIES = {
    "absences": ("absence", "makeup"),
    "bubble_sheets": ("bubble",),
    "canvas": ("canvas", "webassign", "jev_canvas"),
    "course_storage": ("storage",),
    "decisions_sandbox": ("decisions_sandbox", "openai_decisions"),
    "document_identity": ("document", "print", "template_conversion", "term_print"),
    "exam_workflow": ("exam",),
    "faculty_ocr": ("faculty_ocr", "alias_ocr", "version_sort"),
    "gmail_mail": ("gmail", "mail"),
    "google_calendar": ("google_calendar", "calendar", "workspace_calendar"),
    "gradebook": ("gradebook", "canvas_sync", "canvas_posting", "canvas_nightly", "posting_policy", "certified_archive"),
    "grading": ("grading", "frq", "gradescope", "certification", "regrade", "quiz", "pipeline", "quality_audit",
                "training", "drafting_tools", "teaching_review", "makeup_drafts", "agent_sdk", "antigravity"),
    "jev": ("jev", "followup_match"),
    "manage_storage": ("storage",),
    "question_bank": ("question_bank", "jev_bank"),
    "response_cards": ("response_card",),
    "room_pdf_sources": ("room",),
    "rosters": ("roster",),
    "student_support": ("student_support", "followup"),
    "teach_inbox": ("inbox", "pipeline", "scan", "assignment_finder", "unlabeled_exams", "sequence"),
}
PYTHON_CAP, SCRIPT_CAP, CHUNK = 40, 30, 10
# A module imported by more test modules than this (rosters, app) says nothing about which of them matter: its family decides.
IMPORTERS = 25


def _text(path, cache):
    if path not in cache:
        try:
            cache[path] = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            cache[path] = ""
    return cache[path]


def _matching(repo, pattern, suffix):
    return {path.stem if suffix == ".py" else f"tests/{path.name}" for path in (repo / "tests").glob(pattern + suffix)}


def _importers(repo, dotted, cache):
    """The test modules importing `dotted` (`import a.b`, `from a.b import …`, `from a import …b…`)."""
    parent, _, stem = dotted.rpartition(".")
    pattern = re.compile(rf"(?m)^\s*(?:from\s+{re.escape(dotted)}\s+import\b|import\s+{re.escape(dotted)}\b"
                         + (rf"|from\s+{re.escape(parent)}\s+import\s+[^\n]*\b{re.escape(stem)}\b" if parent else "") + ")")
    found = {path.stem for path in (repo / "tests").glob("test_*.py") if pattern.search(_text(path, cache))}
    return found if len(found) <= IMPORTERS else set()


def plan(repo, changed):
    """{'python', 'javascript', 'not_run'}: the modules and scripts that run, and those that matched beyond the caps."""
    repo = Path(repo)
    modules, scripts, texts = {}, {}, {}

    def add(target, names, tier):
        for name in names:
            target[name] = min(tier, target.get(name, tier))

    for name in changed:
        path = Path(name)
        stem = path.stem
        component = path.parts[0] if len(path.parts) > 1 else stem
        if component == "tests":
            if name.startswith("tests/test_") and path.suffix == ".py":
                add(modules, [stem], 0)
            elif name.startswith("tests/test_") and path.suffix == ".mjs":
                add(scripts, [name], 0)
            elif path.suffix == ".py":
                # A shared helper (a fake service, fixtures): the tests that import it.
                add(modules, _importers(repo, stem, texts), 1)
                if stem.startswith("fake_"):
                    add(modules, _matching(repo, f"test_{stem[5:]}*", ".py"), 1)
            continue
        if component == "static":
            add(scripts, _matching(repo, f"test_{stem.replace('-', '_')}*", ".mjs"), 1)
            if path.suffix == ".js":
                quoted = re.compile(rf"[/'\"]{re.escape(path.name)}['\"]")
                add(scripts, {f"tests/{p.name}" for p in (repo / "tests").glob("test_*.mjs") if quoted.search(_text(p, texts))}, 1)
            add(modules, ["test_design_language"], 1)
            continue
        families = FAMILIES.get(component, (component,))
        if path.suffix == ".py":
            if stem != "__init__":
                for family in dict.fromkeys((*families, component)):
                    if family != stem:
                        add(modules, _matching(repo, f"test_{family}_{stem}*", ".py"), 1)
                add(modules, _matching(repo, f"test_{stem}*", ".py"), 1)
            dotted = ".".join((*path.parts[:-1], *(() if stem == "__init__" else (stem,))))
            if dotted:
                add(modules, _importers(repo, dotted, texts), 1)
        if component == "assistant":
            add(modules, ("test_assistant", "test_assistant_workspace", "test_assistant_integration"), 1)
            if "followup" in name or "workflow" in name:
                add(modules, ("test_followup_completion", "test_followup_suggestions", "test_assistant_workflows"), 1)
            if "coding" in name or "code_checks" in name:
                add(modules, ["test_assistant_coding"], 1)
        if path.suffix == ".sql" or name in ("app.py", "rosters.py"):
            add(modules, ("test_app", "test_catalog_migrations"), 1)
        for family in families:
            add(modules, _matching(repo, f"test_{family}*", ".py"), 2)
    modules = {m: t for m, t in modules.items() if (repo / "tests" / (m + ".py")).is_file()}
    scripts = {s: t for s, t in scripts.items() if (repo / s).is_file()}
    if not modules and (repo / "tests/test_app.py").is_file():
        modules = {"test_app": 1}
    python = sorted(modules, key=lambda m: (modules[m], m))
    javascript = sorted(scripts, key=lambda s: (scripts[s], s))
    return {"python": sorted(python[:PYTHON_CAP]), "javascript": sorted(javascript[:SCRIPT_CAP]),
            "not_run": python[PYTHON_CAP:] + javascript[SCRIPT_CAP:]}


def selection(repo, changed):
    """A deterministic selection from the task's actual files, not its report: (Python modules, Node scripts)."""
    chosen = plan(repo, changed)
    return chosen["python"], chosen["javascript"]


def run(repo, runtime, changed, head, base, progress=None):
    """Use the trusted environment builder, fresh NVMe scratch, and no inherited credentials. `progress(done, total)` is called
    before each command (Apply's progress line)."""
    repo, runtime = Path(repo), Path(runtime)
    chosen = plan(repo, changed)
    modules, scripts = chosen["python"], chosen["javascript"]
    commands = []
    interpreter = runtime / '.venv/bin/python'
    for start in range(0, len(modules), CHUNK):
        commands.append([str(interpreter) if interpreter.is_file() else sys.executable, '-B', '-m', 'unittest',
                         *modules[start:start + CHUNK], '-q'])
    node = runtime / '.tools/node22/bin/node'
    if scripts:
        if not node.is_file():
            raise ValueError('ASSISTANT: Node is unavailable; the required JavaScript checks cannot run.')
        commands.append([str(node), '--test', *scripts])
    with tempfile.TemporaryDirectory(prefix='faculty-code-check-') as scratch:
        work = Path(scratch)
        if Path('/mnt') not in work.resolve().parents:
            raise ValueError('STORAGE: coding checks need service TMPDIR on /mnt.')
        env = environment(work)
        env['PYTHONPATH'] = str(repo) + ':' + str(repo / 'tests')
        results = []
        for number, command in enumerate(commands):
            if progress:
                progress(number, len(commands))
            result = subprocess.run(command, cwd=repo, env=env, capture_output=True, text=True, timeout=900)
            results.append({'command': command[1:], 'exit': result.returncode,
                            'output': (result.stdout + result.stderr)[-8000:]})
            if result.returncode:
                raise ValueError('ASSISTANT: required checks failed; nothing was applied.\n' + results[-1]['output'])
    return {'head': head, 'base': base, 'python': modules, 'javascript': scripts, 'not_run': chosen['not_run'], 'results': results,
            'selection_sha256': hashlib.sha256(json.dumps([changed, modules, scripts, chosen['not_run']]).encode()).hexdigest()}
