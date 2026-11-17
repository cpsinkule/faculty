"""AIM FORM CAPTURE (AGENTS.md "ACCOMMODATIONS"): a <label> that wraps its field gives FORM_SCAN its own words only, never
the prefilled text of a textarea or the options of a select inside it. Reads a local `set_content` page only; never AIM."""
import json
import unittest

import aim_import

PAGE = """<!doctype html><html><body><form>
<label>Notes for DR <textarea name="ctl00$Notes" id="notes">PREFILLED-NOTES needs a calculator, UIN 12-345-6789</textarea></label>
<label>Exam type <select name="ctl00$Type" id="type"><option>OPTION-QUIZ</option><option selected>OPTION-FINAL</option></select></label>
<h2>Heading <textarea id="inside">PREFILLED-IN-HEADING</textarea></h2>
<button type="button" id="go">Go <select id="pick"><option>OPTION-IN-BUTTON</option></select></button>
</form></body></html>"""


class WrappedLabelTests(unittest.TestCase):
    def test_a_wrapping_label_keeps_none_of_its_fields_content(self):
        from playwright.sync_api import sync_playwright
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.set_content(PAGE)
                raw = page.main_frame.evaluate(aim_import.FORM_SCAN)
            finally:
                browser.close()
        controls = {c["id"]: c for c in raw["controls"]}
        self.assertEqual(" ".join(controls["notes"]["label"].split()), "Notes for DR")
        self.assertEqual(" ".join(controls["type"]["label"].split()), "Exam type")
        self.assertEqual([" ".join(o.split()) for o in controls["type"]["options"]], ["OPTION-QUIZ", "OPTION-FINAL"], "a select's own options stay")
        self.assertEqual([" ".join(h.split()) for h in raw["headings"]], ["Heading"])
        self.assertEqual(" ".join(next(b for b in raw["buttons"] if b["id"] == "go")["text"].split()), "Go")
        dumped = json.dumps({key: raw[key] for key in ("headings", "buttons")}) + json.dumps([c["label"] for c in raw["controls"]])
        for content in ("PREFILLED", "12-345-6789", "OPTION-"):
            self.assertNotIn(content, dumped, content)


if __name__ == "__main__":
    unittest.main()
