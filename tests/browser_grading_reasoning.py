"""Compute settings: a reasoning choice beside each role's model where the model offers one, a plain statement where it does not."""
import sys
import threading
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO), str(REPO / "tests")]
from playwright.sync_api import sync_playwright, expect
from waitress.server import create_server
from grading import config
from test_frq_grading import FrqWorkflowTests
from test_grading_model_catalog import model

OUTPUT = Path("test-results")
OUTPUT.mkdir(exist_ok=True)
fixture = FrqWorkflowTests("test_end_to_end_idempotency_and_export")
fixture.setUp()
# A reviewer added from the catalogue before reasoning options were recorded: exactly the connection that failed live on 2026-09-20.
current = fixture.frq.settings()
older = {k: v for k, v in {**config.ENDPOINT, "name": "glm-flash", "base_url": config.AGGREGATOR, "key_env": "OPENROUTER_API_KEY", "model": "z-ai/glm-5.3-flash",
                           "lineage": "glm", "aggregator": True}.items() if k != "reasoning_options"}
settings = {**current["settings"], "endpoints": [*current["settings"]["endpoints"], older], "recent_models": ["deepseek-v4.1-flash"]}  # Role dropdowns list recent models.
settings["tiers"] = {**settings["tiers"], "cloud_standard": {**settings["tiers"]["cloud_standard"], "verifier": "glm-flash", "flag_reviewer": "kimi-k2.5"}}
settings["default_tier"] = "cloud_standard"  # The picked scheme decides which team stands under it.
fixture.frq.save_settings({"version": current["version"], "settings": settings})
catalogue = {"data": [model("z-ai/glm-5.3-flash", supported_parameters=["structured_outputs", "reasoning"],
                            reasoning={"mandatory": True, "default_enabled": True, "supported_efforts": ["max", "high", "low"], "default_effort": "max"})]}
fetch = patch("grading.model_catalog.request_json", return_value=catalogue).start()
server = create_server(fixture.app, host="127.0.0.1", port=0)
threading.Thread(target=server.run, daemon=True).start()
address = f"http://127.0.0.1:{server.effective_port}"
try:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1500, "height": 1100})
        page.set_default_timeout(15000)
        errors, bad, reads = [], [], []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
        page.on("response", lambda response: bad.append((response.status, response.url)) if response.status >= 400 else None)
        page.on("request", lambda request: reads.append(request.url) if "/model-catalog" in request.url else None)
        page.goto(f"{address}/#grading/{fixture.plan['id']}?tab=jobs")

        def open_teams():
            page.get_by_role("button", name="Compute settings", exact=True).click()
            popup = page.locator("#grading-settings-dialog")
            return popup

        popup = open_teams()
        slot = lambda tier, role: popup.locator(f'[data-reasoning-slot="tier-{tier}-{role}"]')
        choice = lambda tier, role: popup.locator(f'[name="reasoning-tier-{tier}-{role}"]')
        # One possible configuration is simply used, and said. The reader's model never thinks.
        expect(slot("cloud_standard", "reader")).to_have_text("Never thinks")
        expect(choice("cloud_standard", "reader")).to_have_count(0)
        # A model that must think offers its efforts and never "No reasoning"; it starts where it ran before the choice existed.
        second = choice("cloud_standard", "second")
        expect(second.locator("option")).to_have_text(["Low effort (least)", "Medium effort", "High effort"])
        expect(second).to_have_value("medium")
        # Optional thinking without effort levels is on or off.
        reviewer = choice("cloud_standard", "flag_reviewer")
        expect(reviewer.locator("option")).to_have_text(["No reasoning", "Reasoning on"])
        expect(reviewer).to_have_value("off")
        # A connection saved before the facts existed says so; nothing was fetched to draw this screen.
        expect(slot("cloud_standard", "verifier")).to_have_text("Reasoning follows this connection’s own setting.")
        assert not reads, reads
        expect(popup.locator("[data-reasoning-facts]")).to_contain_text("not recorded yet for glm-flash")
        popup.get_by_role("button", name="Read reasoning options from providers", exact=True).click()
        expect(page.locator("#toast")).to_contain_text("Read for glm-flash")
        glm = choice("cloud_standard", "verifier")
        expect(glm.locator("option")).to_have_text(["Low effort (least)", "High effort", "Maximum effort"])  # The host's own default is Maximum.
        expect(glm).to_have_value("low")
        expect(popup.locator("[data-reasoning-facts]")).to_be_empty()
        assert len(reads) == 1, reads
        # One appearance, desktop only (DESIGN-LANGUAGE, 2026-10-03).
        for width in (1500, 1280):
            page.set_viewport_size({"width": width, "height": 1100})
            slot("cloud_standard", "second").scroll_into_view_if_needed()
            page.screenshot(path=str(OUTPUT / f"reasoning-choice-{width}.png"))
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), width
        page.set_viewport_size({"width": 1500, "height": 1100})
        # The choice belongs to the role: the reviewer thinks harder, the second grader less.
        glm.select_option("high")
        second.select_option("low")
        save = popup.get_by_role("button", name="Save compute settings", exact=True)
        expect(save).to_be_enabled()
        put = lambda response: response.request.method == "PUT" and response.url.endswith("/api/grading/settings")
        with page.expect_response(put) as answer:  # The toast keeps its words between saves, so wait for the save itself.
            save.click()
        assert answer.value.status == 200, answer.value.status
        expect(page.locator("#toast")).to_contain_text("saved")
        saved = fixture.frq.settings()["settings"]
        # Only the two choices that were made are stored; every untouched role keeps following its connection's default.
        assert saved["role_reasoning"] == {"cloud_standard": {"second": "low", "verifier": "high"}}, saved["role_reasoning"]
        facts = next(e for e in saved["endpoints"] if e["name"] == "glm-flash")["reasoning_options"]
        assert facts == {"supported": True, "mandatory": True, "efforts": ["low", "high", "max"], "default_effort": "max"}, facts
        roles = config.roles(saved, "cloud_standard")
        assert (roles["verifier"]["reasoning_choice"], roles["second"]["reasoning_choice"], roles["reader"]["reasoning_choice"]) == ("high", "low", "off"), roles
        # Reopened: what was saved is what is shown, and another model for the role starts from that model's own default.
        page.keyboard.press("Escape")
        popup = open_teams()
        expect(choice("cloud_standard", "verifier")).to_have_value("high")
        expect(choice("cloud_standard", "second")).to_have_value("low")
        popup.locator('[name="tier-cloud_standard-second"]').select_option("deepseek-v4.1-flash")
        expect(choice("cloud_standard", "second").locator("option")).to_have_text(["No reasoning", "Low effort", "High effort", "Maximum effort"])
        expect(choice("cloud_standard", "second")).to_have_value("off")
        expect(choice("cloud_standard", "verifier")).to_have_value("high")  # Another role's choice is untouched.
        # The rubric reviewer is no team role, yet it reads a whole exam at once: it has the same chooser, stored under its own key.
        popup.get_by_role("tab", name="Other models", exact=True).click()
        popup.get_by_label("Rubric drafting and review model", exact=True).select_option("glm-flash")
        review = popup.locator('[name="reasoning-rubric_reviewer"]')
        expect(review.locator("option")).to_have_text(["Low effort (least)", "High effort", "Maximum effort"])
        expect(review).to_have_value("low")
        review.select_option("max")
        with page.expect_response(put) as answer:
            popup.get_by_role("button", name="Save compute settings", exact=True).click()
        assert answer.value.status == 200, answer.value.status
        kept = fixture.frq.settings()["settings"]
        assert (kept["rubric_reviewer"], kept["rubric_reviewer_reasoning"], config.rubric_reviewer(kept)["reasoning_choice"]) == ("glm-flash", "max", "max"), kept["rubric_reviewer_reasoning"]
        # Another reviewer model starts from its own default, and an unset reviewer shows Standard's primary grader, which never thinks.
        popup.get_by_label("Rubric drafting and review model", exact=True).select_option("")
        expect(popup.locator('[data-reasoning-slot="rubric_reviewer"]')).to_have_text("Never thinks")
        popup.get_by_label("Rubric drafting and review model", exact=True).select_option("glm-flash")
        expect(popup.locator('[name="reasoning-rubric_reviewer"]')).to_have_value("low")
        # Review rules: one switch for every check of a model-reported confidence number. Off, the values stay and are still saved.
        popup.get_by_role("tab", name="What holds a response back", exact=True).click()
        popup.locator('details[data-compute-fold="thresholds"] > summary').click()
        switch = popup.get_by_label("Use model-reported confidence to flag responses", exact=True)
        expect(switch).to_be_checked()
        grader, agreement = popup.get_by_label("Reported grading confidence for review", exact=True), popup.get_by_label("Reader agreement for acceptance", exact=True)
        expect(grader).to_be_editable()
        grader.fill("0.8")
        switch.uncheck()
        expect(popup.locator("[data-confidence-field][readonly]")).to_have_count(5)
        expect(agreement).to_be_editable()  # Reader agreement is not a confidence a model reports about itself.
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
        page.screenshot(path=str(OUTPUT / "confidence-checks-off.png"))
        with page.expect_response(put) as answer:
            popup.get_by_role("button", name="Save compute settings", exact=True).click()
        assert answer.value.status == 200, answer.value.status
        kept = fixture.frq.settings()["settings"]
        assert (kept["risk"]["confidence_checks"], kept["grading_threshold"], kept["visual_threshold"], kept["risk"]["min_confidence"]) == (False, .8, .85, .9), kept["risk"]
        page.keyboard.press("Escape")
        page.get_by_role("button", name="Compute settings", exact=True).click()
        popup.get_by_role("tab", name="What holds a response back", exact=True).click()
        expect(popup.get_by_label("Use model-reported confidence to flag responses", exact=True)).not_to_be_checked()
        expect(popup.get_by_label("Reported grading confidence for review", exact=True)).to_have_value("0.8")
        assert not errors, errors
        assert not bad, bad
        browser.close()
        print("Reasoning choice browser passed: fixed statements, per-role choices from saved facts, no fetch to draw the screen, one explicit read for an older "
              "connection, saved per role and tier, reset on another model and desktop widths.")
except Exception:
    print("Browser errors:", errors if "errors" in globals() else [])
    print("HTTP errors:", bad if "bad" in globals() else [])
    if "popup" in globals():
        try:
            print("Settings says:", popup.locator("[role=alert]").all_inner_texts())
        except Exception:
            pass
    raise
finally:
    patch.stopall()
    server.close()
    fixture.doCleanups()
