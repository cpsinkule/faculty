"""Compare computed styles of a demo server's pages with baseline CSS from git (before/after, screen or print).

Usage: python3 style_diff.py --port N [--base REV] [--files a.css,b.css] [--only home,chart]
                             [--widths 1440,1280] [--media screen|print] [--extra name=route ...] [--limit 40] [--json OUT]

Each route is loaded twice in the same browser context: once with the chosen stylesheets replaced (through request
interception) by their content at REV (default HEAD), once as served. Every rendered element's computed style (and
its ::before/::after) is compared, keyed by its DOM path, and the differences are listed per property with examples
("rendered" counts elements rendered in only one of the two loads). Hover/focus states are not covered.
--files limits the swapped sheets (default: every static/*.css that differs between REV and the working tree), so an
agent can measure the effect of its own sheets alone. With --media print, the page is emulated as printed: a sheet
change that should not reach print must show 0 differences there.
Route names are app_shots.py's (placeholders filled from the server's routes file). Exit status 0; read the report.
"""
import argparse
import asyncio
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from playwright.async_api import async_playwright

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))

parser = argparse.ArgumentParser()
parser.add_argument("--port", type=int, default=8765)
parser.add_argument("--base", default="HEAD")
parser.add_argument("--files", default="")
parser.add_argument("--only", default="")
parser.add_argument("--widths", default="1440,1280")
parser.add_argument("--media", default="screen", choices=["screen", "print"])
parser.add_argument("--extra", action="append", default=[])
parser.add_argument("--limit", type=int, default=25)
parser.add_argument("--json", default="")
parser.add_argument("--wait", type=int, default=1600)
args = parser.parse_args()

ROUTES = {
    "home": "overview", "calendar": "calendar", "courses": "current-courses",
    "course-home": "course/{group}/home", "section": "course/{group}/section/501",
    "accommodations": "accommodations", "documents": "documents", "seating": "seating", "chart": "seating/{chart}",
    "response-cards": "response-cards", "inbox": "inbox", "inbox-history": "inbox?status=history",
    "scan": "inbox/job/{job}", "scan-sheets": "inbox/job/{job}/sheets", "exams": "exams", "exam": "exams/{event}",
    "grading": "grading", "grading-plan": "grading/{plan}", "bubble-key": "grading/{plan}?tab=bubble",
    "gradebook": "gradebook", "gradebook-term": "gradebook/{term}", "gradebook-setup": "gradebook/{term}/setup",
    "rooms": "rooms", "room": "rooms/{room}",
}
for item in args.extra:
    name, route = item.split("=", 1)
    ROUTES[name] = route
scratch = Path(os.environ.get("FACULTY_DEMO_SCRATCH") or Path(tempfile.gettempdir()) / "faculty-demo")
ids = dict(line.split("=", 1) for line in (scratch / f"routes-{args.port}.txt").read_text().splitlines())
ROUTES = {name: route.format(**ids) for name, route in ROUTES.items()}
only = [n for n in args.only.split(",") if n]
if only:
    missing = [n for n in only if n not in ROUTES]
    if missing:
        sys.exit(f"unknown routes: {missing}")
    ROUTES = {n: ROUTES[n] for n in only}


def git(*cmd):
    return subprocess.run(["git", *cmd], cwd=REPO, check=True, capture_output=True).stdout


if args.files:
    files = [f if f.startswith("static/") else f"static/{f}" for f in args.files.split(",") if f]
else:
    changed = git("diff", "--name-only", args.base, "--", "static").decode().split()
    untracked = git("ls-files", "--others", "--exclude-standard", "--", "static").decode().split()
    files = [f for f in changed + untracked if f.endswith(".css")]
BASE = {}
for f in files:
    try:
        BASE[Path(f).name] = git("show", f"{args.base}:{f}")
    except subprocess.CalledProcessError:
        BASE[Path(f).name] = b""  # new file: absent at REV
if not BASE:
    sys.exit("no stylesheets differ from the base; nothing to compare")

PROPS = ["display", "position", "visibility", "opacity", "color", "background-color", "background-image",
         "font-family", "font-size", "font-weight", "font-style", "letter-spacing", "line-height", "text-transform",
         "text-decoration-line", "text-align", "white-space", "border-top-width", "border-right-width",
         "border-bottom-width", "border-left-width", "border-top-style", "border-right-style", "border-bottom-style",
         "border-left-style", "border-top-color", "border-right-color", "border-bottom-color", "border-left-color",
         "border-top-left-radius", "box-shadow", "outline-style", "outline-width", "outline-color", "padding-top",
         "padding-right", "padding-bottom", "padding-left", "margin-top", "margin-right", "margin-bottom",
         "margin-left", "gap", "width", "height", "text-shadow", "content"]
COLLECT = """(props) => {
  const out = {};
  const path = (el) => { const parts = [];
    for (let n = el; n && n.nodeType === 1 && n !== document.documentElement; n = n.parentElement) {
      let p = n.tagName.toLowerCase();
      if (n.id && !/[0-9a-f]{8}-/.test(n.id)) p += '#' + n.id;
      const cls = Array.from(n.classList).filter(c => !/^(is-|has-)?(active|hover|focus|busy|loading|entering|leaving|visible)$/.test(c)).slice(0, 3);
      if (cls.length) p += '.' + cls.join('.');
      const parent = n.parentElement;
      if (parent) p += ':' + (Array.prototype.indexOf.call(parent.children, n) + 1);
      parts.push(p); }
    return parts.reverse().join('>'); };
  const round = (prop, v) => (prop === 'width' || prop === 'height') ? String(Math.round(parseFloat(v) || 0)) : v;
  for (const el of document.querySelectorAll('body *')) {
    if (el.closest('svg') && el.tagName.toLowerCase() !== 'svg') continue;
    if (!el.checkVisibility()) continue;  // not rendered (itself or an ancestor display: none)
    const s = getComputedStyle(el);
    const key = path(el);
    const row = {};
    for (const p of props) { if (p !== 'content') row[p] = round(p, s.getPropertyValue(p)); }
    for (const pseudo of ['::before', '::after']) {
      const ps = getComputedStyle(el, pseudo);
      const c = ps.getPropertyValue('content');
      if (c && c !== 'none' && c !== 'normal') {
        for (const p of ['content', 'color', 'font-family', 'font-size', 'font-weight', 'background-color',
                         'border-top-color', 'border-left-color', 'text-shadow', 'margin-left', 'display'])
          row[pseudo + ' ' + p] = ps.getPropertyValue(p);
      }
    }
    out[key] = row;
  }
  return out;
}"""


async def snapshot(page, url, swap):
    async def handle(route):
        name = route.request.url.split("?")[0].rsplit("/", 1)[-1]
        if swap and name in BASE:
            await route.fulfill(status=200, body=BASE[name], headers={"content-type": "text/css; charset=utf-8"})
        else:
            await route.continue_()
    await page.unroute("**/*.css")
    await page.route("**/*.css", handle)
    await page.goto("about:blank")
    await page.goto(url)
    await page.wait_for_timeout(args.wait)
    await page.evaluate("document.fonts.ready.then(() => true)")
    return await page.evaluate(COLLECT, PROPS)


async def main():
    totals, report = {}, []
    async with async_playwright() as p:
        launch = {"executable_path": os.environ["CHROMIUM_PATH"]} if os.environ.get("CHROMIUM_PATH") else {}
        browser = await p.chromium.launch(**launch)
        for width in [int(w) for w in args.widths.split(",") if w]:
            context = await browser.new_context(viewport={"width": width, "height": 900}, reduced_motion="reduce")
            page = await context.new_page()
            await page.emulate_media(media=args.media)
            for name, route in ROUTES.items():
                url = f"http://127.0.0.1:{args.port}/#{route}"
                before = await snapshot(page, url, True)
                after = await snapshot(page, url, False)
                diffs = []
                for key in sorted(set(before) | set(after)):
                    a, b = before.get(key), after.get(key)
                    if a is None or b is None:  # rendered in one load only (a visibility change, or a poll)
                        diffs.append(("rendered", key, "yes" if a else "no", "yes" if b else "no"))
                        continue
                    for prop in sorted(set(a) | set(b)):
                        if a.get(prop) != b.get(prop):
                            diffs.append((prop, key, a.get(prop), b.get(prop)))
                label = f"{name} {width} {args.media}"
                totals[label] = len(diffs)
                report.append({"page": label, "differences": len(diffs),
                               "by_property": _count(diffs), "examples": [
                                   {"property": d[0], "element": d[1][-160:], "before": d[2], "after": d[3]}
                                   for d in diffs[:args.limit]]})
            await context.close()
        await browser.close()
    print("swapped to", args.base + ":", ", ".join(sorted(BASE)))
    for entry in report:
        print(f"\n== {entry['page']}: {entry['differences']} differences")
        if entry["differences"]:
            print("   by property:", ", ".join(f"{k} {v}" for k, v in entry["by_property"].items()))
            for ex in entry["examples"]:
                print(f"   - {ex['property']}: {ex['before']!s:.60} -> {ex['after']!s:.60}\n       {ex['element']}")
    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=1))
    print("\nTOTAL differences:", sum(totals.values()))


def _count(diffs):
    counts = {}
    for d in diffs:
        counts[d[0]] = counts.get(d[0], 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: -kv[1]))


asyncio.run(main())
