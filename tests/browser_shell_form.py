"""FRONTEND: a late shell form result belongs to its original dialog, using synthetic settings writes."""
import json
import sys
import tempfile
import threading
from pathlib import Path

from playwright.sync_api import expect, sync_playwright
from waitress.server import create_server

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import create_app


def main():
    with tempfile.TemporaryDirectory() as directory:
        app = create_app(directory)
        server = create_server(app, host="127.0.0.1", port=0)
        threading.Thread(target=server.run, daemon=True).start()
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                page = browser.new_page(viewport={"width": 1440, "height": 1000})
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                writes = []

                def settings(route):
                    if route.request.method == "PATCH":
                        writes.append(route)
                    else:
                        route.continue_()

                page.route("**/api/settings", settings)
                page.goto(f"http://127.0.0.1:{server.effective_port}/")
                expect(page.locator(".server-status")).to_have_class("server-status connected")

                def open_settings():
                    # The sidebar action also models another flow replacing a modal while its old save is outstanding.
                    page.evaluate("document.querySelector('.sidebar [data-action=settings]').click()")
                    expect(page.locator("#settings-form")).to_be_visible()

                open_settings()
                page.locator("[name=workspace_name]").fill("First pending name")
                page.locator("#settings-form [type=submit]").click()
                expect(page.locator("#settings-form")).to_have_attribute("data-saving", "true")
                open_settings()
                page.locator("[name=workspace_name]").fill("Unsent replacement")
                assert len(writes) == 1
                writes[0].fulfill(status=200, content_type="application/json", body=json.dumps({"workspace_name": "First pending name"}))
                expect(page.locator(".toast-message")).to_contain_text("Workspace settings saved.")
                expect(page.locator("#settings-form")).to_be_visible()
                expect(page.locator("[name=workspace_name]")).to_have_value("Unsent replacement")

                page.locator("#settings-form [type=submit]").click()
                expect(page.locator("#settings-form")).to_have_attribute("data-saving", "true")
                open_settings()
                assert len(writes) == 2
                writes[1].fulfill(status=500, content_type="application/json", body=json.dumps({"error": "Synthetic save refused"}))
                expect(page.locator(".toast-message").last).to_have_text("Synthetic save refused")
                expect(page.locator("#settings-form #form-error")).to_be_hidden()
                expect(page.locator("#settings-form")).to_be_visible()
                assert not errors, errors
                browser.close()
        finally:
            server.close()
    print("Shell form dialog ownership passed")


if __name__ == "__main__":
    main()
