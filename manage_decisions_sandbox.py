#!/usr/bin/env python3
"""SANDBOX (requirement, 2026-10-07): compare OpenAI Decisions with Jev on Faculty's own checks, outside the app. Since 2026-10-13
(the instructor: "test a new decision model against Jev and Decision API") SubQ Snap is a third model, and Microsoft-Decision-1
on OpenRouter ("let's test it against jev") a fourth; each is asked only when `--modes` names it:
`--modes jev,decisions,snap,microsoft`. Jev 1.14 and 1.13 with its opt-in features (`jev114`, `jev113x`, 2026-10-15) and Liquid
d1-ultra (`liquid`, 2026-10-18) and Gemini Decision 1 (`gemini`, 2026-11-11; `google` and `googleultra`, Google's own API, 2026-11-12)
are asked the same way.

    python3 manage_decisions_sandbox.py keys                     # which of the models' keys the sandbox would hold (never values)
    python3 manage_decisions_sandbox.py copy     --run NAME      # read-only copy of the live catalog into the run folder
    python3 manage_decisions_sandbox.py probe    --run NAME      # invented requests to Decisions (and Snap, when named): limits, rounding, latency
    python3 manage_decisions_sandbox.py invented --run NAME      # invented cases with known answers, both models
    python3 manage_decisions_sandbox.py real     --run NAME      # cases rebuilt from the copy, both models
    python3 manage_decisions_sandbox.py images   --run NAME      # scan and grade images (Decisions), via the live app's GET routes
    python3 manage_decisions_sandbox.py papers   --run NAME --modes jev114,gemini  # whole scanned booklets as one PDF: version, student
    python3 manage_decisions_sandbox.py students --run NAME --modes google         # the app's own student match, replayed on kept first pages
    python3 manage_decisions_sandbox.py report   --run NAME      # report.md, disagreements.csv, summary.json
    python3 manage_decisions_sandbox.py triage-calibrate --run NAME  # every student message this term, both models: proposed gates

Every run folder is under /mnt/nvme/Teaching/tmp/decisions-sandbox (0700). Nothing here imports app.py, opens the live catalog
writable, writes a record, or sends mail; the only outward requests are the models' (and, for images, read-only GETs to the
live app on 127.0.0.1). `--fake` swaps every model for scripted answers: no request leaves the machine.
"""
import argparse
import importlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from decisions_sandbox import safety  # noqa: E402

CHECKS = {'followups': ('triage', 'same_matter', 'record_check', 'todo_thanks', 'followup_accuracy', 'card_status'),
          'messages': ('send_check', 'draft_warnings', 'message_routing'),
          'other': ('absence', 'evidence', 'palette', 'canvas_match', 'bank_tag', 'rubric_feedback'),
          'long_context': ('long_context_0020k', 'long_context_0100k', 'long_context_0400k', 'long_context_1000k'),
          'media': ('spoken_routing', 'document_text', 'document_picture', 'document_pdf', 'document_scan'),
          'images': ('identity_band', 'cut_off'),
          'mc': ('bubble_row', 'bubble_sheet', 'quiz_choice'),
          'papers': ('paper_version_page', 'paper_version_pdf', 'paper_student_page', 'paper_student_pdf'),
          'students': ('student_match', 'student_absent')}


def scripted(folder):
    """Whether the run folder's recorded answers are a dry run's (None when it has none yet)."""
    path = folder / 'calls.jsonl'
    if not path.exists() or not path.stat().st_size:
        return None
    with path.open() as calls:
        return any(str(json.loads(line).get('model') or '').startswith('fake-') for line in calls if line.strip())


def store_for(folder, fake, seed=None):
    from decisions_sandbox.pair import Store
    # A run folder's answers are reused by every later command in it, so a dry run's scripted answers and a model's real ones
    # never share one (2026-10-13: a dry run in a real run's folder would have stood in for 1,305 real answers).
    if scripted(folder) not in (None, fake):
        raise SystemExit('This run folder holds ' + ('real answers; a --fake dry run takes its own --run name.' if fake else
                                                     'a --fake dry run\'s scripted answers; a real run takes its own --run name.'))
    if fake:
        from decisions_sandbox import fakes
        return Store(folder, seed=seed, ask_jev=fakes.model('jev'), ask_decisions=fakes.model('decisions'), ask_snap=fakes.model('snap'),
                     ask_microsoft=fakes.model('microsoft'), available=lambda mode: True, ask_preview=fakes.model,
                     ask_liquid=fakes.model('liquid'), ask_gemini=fakes.model('gemini'))
    return Store(folder, seed=seed)


def cases(kind, ctx, wanted):
    found = []
    for module in ('followups', 'messages', 'other', 'long_context', 'media'):
        if wanted and not set(CHECKS[module]) & wanted:
            continue
        try:
            built = importlib.import_module('decisions_sandbox.checks.' + module)
        except ModuleNotFoundError as error:
            if error.name == 'decisions_sandbox.checks.' + module:
                continue
            raise
        found += built.invented() if kind == 'invented' else built.real(ctx)
    return [c for c in found if not wanted or c.check in wanted]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('command', choices=('keys', 'copy', 'probe', 'invented', 'real', 'images', 'mc', 'identity', 'papers', 'students', 'report',
                                            'triage-calibrate', 'mc-calibrate'))
    parser.add_argument('--run', default='run-scratch')
    parser.add_argument('--checks', default='', help='comma-separated check names (default: all)')
    parser.add_argument('--modes', default='jev,decisions',
                        help='comma-separated models: jev, decisions, snap, microsoft, jev114, jev113x, liquid, gemini, google, googleultra (the last eight only when named)')
    parser.add_argument('--fake', action='store_true', help='scripted answers for every model; nothing is sent')
    parser.add_argument('--workers', type=int, default=6)
    args = parser.parse_args(argv)
    folder = safety.run_folder(args.run)
    held = safety.scrub_environment(folder)
    if args.command == 'keys':
        print('keys held by the sandbox:', ', '.join(f"{k} {'yes' if v else 'no'}" for k, v in held.items()))
        return 0
    if args.command == 'copy':
        from decisions_sandbox import catalog_copy
        print(catalog_copy.capture(folder / 'catalog.sqlite3'))
        return 0
    if args.command == 'report':
        from decisions_sandbox import report
        print(report.write(folder))
        return 0
    wanted = {name for name in args.checks.split(',') if name}
    modes = tuple(m for m in args.modes.split(',') if m)
    from decisions_sandbox.pair import MODES
    if not modes or set(modes) - set(MODES):
        parser.error('--modes names ' + ', '.join(MODES))
    if args.command == 'probe':
        from decisions_sandbox import probe
        return probe.run(folder, fake=args.fake, modes=modes)
    from decisions_sandbox import runner
    from decisions_sandbox.pair import seed_from
    copy = folder / 'catalog.sqlite3'
    ctx = runner.Context(folder, copy) if copy.exists() else None
    if args.command in ('real', 'images', 'mc', 'identity', 'papers', 'students', 'triage-calibrate', 'mc-calibrate') and ctx is None:
        print('Take the copy first: manage_decisions_sandbox.py copy --run ' + args.run)
        return 2
    store = store_for(folder, args.fake, seed=seed_from(ctx.db) if ctx and not args.fake else None)
    if args.command in ('images', 'mc', 'identity', 'papers', 'students'):
        from decisions_sandbox.checks import identity, images, mc, papers, student_match
        if args.fake:  # a dry run reads nothing from the live app either
            from decisions_sandbox.probe import _png
            images.fetch = mc.fetch = papers.fetch = student_match.fetch = lambda path, sha256=None, whole=False: _png()
            synthetic = [{'enrollment_id': e, 'name': 'Student ' + e[:6], 'section_number': n} for e, n in ctx.db.execute(
                'SELECT m.enrollment_id,s.number FROM roster_memberships m JOIN course_sections s ON s.id=m.section_id')]
            identity.roster = lambda path: synthetic
        built = {'images': images.real, 'mc': mc.real, 'identity': identity.real, 'papers': papers.real,
                 'students': student_match.real}[args.command](ctx)
    elif args.command == 'mc-calibrate':
        # GRADING MULTIPLE-CHOICE READER calibration (2026-10-08): every quiz multiple-choice answer area, as the app asks.
        from decisions_sandbox.checks import images, mc
        if args.fake:
            from decisions_sandbox.probe import _png
            images.fetch = mc.fetch = lambda path, sha256=None: _png()
        built = mc.calibration(ctx)
    elif args.command == 'triage-calibrate':
        # TRIAGE CALIBRATION (requirement, 2026-10-08): every student message this term plus the wellbeing/urgency set, both models.
        from decisions_sandbox.checks import triage_calibration
        built = triage_calibration.real(ctx) + triage_calibration.invented()
    else:
        built = cases(args.command, ctx, wanted)
    built = [c for c in built if not wanted or c.check in wanted]
    print(f'{len(built)} {args.command} cases: ' + ', '.join(sorted({c.check for c in built})))
    results = folder / f'results-{args.command}.jsonl'
    if wanted and results.exists():  # a partial rerun replaces only the checks it ran
        kept = [line for line in results.read_text().splitlines() if line and json.loads(line)['check'] not in wanted]
        results.write_text(''.join(line + '\n' for line in kept))
    elif results.exists():
        results.unlink()
    runner.run(store, built, results, modes=modes, workers=args.workers)
    if args.command == 'triage-calibrate':
        from decisions_sandbox import calibrate
        print(calibrate.write(folder))
        return 0
    from decisions_sandbox import report
    print(report.write(folder))
    return 0


if __name__ == '__main__':
    sys.exit(main())
