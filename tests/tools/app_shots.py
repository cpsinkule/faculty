"""Screenshot Faculty routes of a demo server (serve_port.sh): one appearance, desktop widths (DESIGN-LANGUAGE).

Usage: python3 app_shots.py OUTDIR [--port N] [--width N] [--height N] [--full] [--only a,b,c] [--extra name=route ...]
Route placeholders {group} {term} {plan} {room} {chart} {job} {event} are filled from the server's routes file.
CHROMIUM_PATH selects a browser binary when Playwright's own build is not installed.
"""
import asyncio
import os
import sys
import tempfile
from pathlib import Path

from playwright.async_api import async_playwright

args = sys.argv[1:]
out = Path(args[0]); out.mkdir(parents=True, exist_ok=True)
width = int(args[args.index("--width") + 1]) if "--width" in args else 1440
height = int(args[args.index("--height") + 1]) if "--height" in args else 900
port = int(args[args.index("--port") + 1]) if "--port" in args else 8765
full = "--full" in args
only = args[args.index("--only") + 1].split(",") if "--only" in args else None
scratch = Path(os.environ.get("FACULTY_DEMO_SCRATCH") or Path(tempfile.gettempdir()) / "faculty-demo")
ids = dict(line.split("=", 1) for line in (scratch / f"routes-{port}.txt").read_text().splitlines())
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
for i, a in enumerate(args):
    if a == "--extra":
        name, route = args[i + 1].split("=", 1)
        ROUTES[name] = route
ROUTES = {name: route.format(**ids) for name, route in ROUTES.items()}


async def main():
    async with async_playwright() as p:
        launch = {"executable_path": os.environ["CHROMIUM_PATH"]} if os.environ.get("CHROMIUM_PATH") else {}
        browser = await p.chromium.launch(**launch)
        context = await browser.new_context(viewport={"width": width, "height": height}, reduced_motion="reduce")
        page = await context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        for name, route in ROUTES.items():
            if only and name not in only:
                continue
            await page.goto(f"http://127.0.0.1:{port}/#{route}")
            await page.wait_for_timeout(1800)
            await page.screenshot(path=str(out / f"{name}-{width}.png"), full_page=full)
        if not only or "settings" in only:
            await page.goto(f"http://127.0.0.1:{port}/#overview")
            await page.wait_for_timeout(1200)
            await page.locator('.sidebar [data-action="settings"]').click()
            await page.wait_for_timeout(800)
            await page.screenshot(path=str(out / f"settings-{width}.png"))
            await page.keyboard.press("Escape")
            await page.keyboard.press("Control+k")
            await page.wait_for_timeout(800)
            await page.screenshot(path=str(out / f"palette-{width}.png"))
        print("page errors:", errors[:5])
        await context.close()
        await browser.close()

asyncio.run(main())
