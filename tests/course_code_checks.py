"""Browser helpers and behavioral checks for the shared course-code form."""

from playwright.sync_api import expect


def fill_course_code(page, code):
    prefix, number = code.split(" ", 1)
    choice = page.get_by_role("combobox", name="Course prefix", exact=True)
    if prefix in choice.locator("option").evaluate_all("options => options.map(option => option.value)"):
        choice.select_option(prefix)
    else:
        choice.select_option("__other__")
        page.get_by_label("Custom course prefix", exact=True).fill(prefix)
    page.get_by_role("spinbutton", name="Course number", exact=True).fill(number)


def expect_course_code(page, code):
    prefix, number = code.split(" ", 1)
    expect(page.get_by_role("combobox", name="Course prefix", exact=True)).to_have_value(prefix)
    expect(page.get_by_role("spinbutton", name="Course number", exact=True)).to_have_value(number)


def check_course_codes(page, output):
    original_count = page.locator(".course-card").count()
    page.locator("#primary-action").click()
    prefix = page.get_by_role("combobox", name="Course prefix", exact=True)
    number = page.get_by_role("spinbutton", name="Course number", exact=True)
    custom = page.get_by_label("Custom course prefix", exact=True)
    expect(prefix).to_have_value("")
    expect(prefix).to_have_attribute("required", "")
    expect(number).to_have_attribute("required", "")
    assert {"MATH", "BIO", "Other"} <= set(prefix.locator("option").all_text_contents())
    expect(custom).not_to_be_visible()
    page.get_by_label("Course name", exact=True).fill("Physics code fixture")
    page.get_by_label("Term", exact=True).select_option("Fall")
    page.get_by_role("spinbutton", name="Year", exact=True).fill("2026")
    page.get_by_role("spinbutton", name="Section 1", exact=True).fill("1")
    number.fill("00101")
    assert not page.locator("#course-form").evaluate("form => form.checkValidity()"), "Prefix is required"
    prefix.select_option("__other__")
    expect(custom).to_be_visible()
    expect(custom).to_have_attribute("required", "")
    for invalid in ("", "   ", "P4YS"):
        custom.fill(invalid)
        assert not page.locator("#course-form").evaluate("form => form.checkValidity()")
    custom.fill("phys")
    number.fill("")
    assert not page.locator("#course-form").evaluate("form => form.checkValidity()"), "Suffix is required"
    for invalid in ("1.5", "-1", "1e3"):
        number.fill(invalid)
        assert not page.locator("#course-form").evaluate("form => form.checkValidity()")
    number.fill("00101")
    prefix.select_option("MATH")
    expect(custom).not_to_be_visible()
    expect(custom).to_be_disabled()
    assert page.locator("#course-form").evaluate("form => form.checkValidity()")
    prefix.select_option("__other__")
    expect(custom).to_have_value("PHYS")
    page.locator("#dialog").evaluate("dialog => dialog.scrollTop = 0")
    page.screenshot(path=str(output / "course-code-desktop.png"), full_page=True)
    page.set_viewport_size({"width": 390, "height": 844})
    assert page.locator("#dialog").evaluate("dialog => dialog.scrollWidth <= dialog.clientWidth")
    page.screenshot(path=str(output / "course-code-mobile.png"), full_page=True)
    page.set_viewport_size({"width": 1440, "height": 1080})
    with page.expect_response(lambda response: response.url.endswith('/api/courses') and response.request.method == 'POST') as saved:
        page.get_by_role("button", name="Add course", exact=True).click()
    first = saved.value.json()
    assert first["code"] == "PHYS 00101"
    expect(page.get_by_role("dialog")).not_to_be_visible()
    page.reload()
    page.locator("#primary-action").click()
    prefix = page.get_by_role("combobox", name="Course prefix", exact=True)
    assert prefix.locator('option[value="PHYS"]').count() == 1
    page.get_by_role("combobox", name="Previous course", exact=True).select_option(label="PHYS 00101 — Physics code fixture")
    expect_course_code(page, "PHYS 00101")
    expect(custom).not_to_be_visible()
    expect(page.get_by_label("Course name", exact=True)).to_have_value("Physics code fixture")
    expect(page.get_by_label("Term", exact=True)).to_have_value("")
    expect(page.get_by_role("spinbutton", name="Year", exact=True)).to_have_value("")
    expect(page.get_by_role("spinbutton", name="Section 1", exact=True)).to_have_value("")
    page.get_by_label("Course name", exact=True).fill("Physics next term")
    page.get_by_label("Term", exact=True).select_option("Spring")
    page.get_by_role("spinbutton", name="Year", exact=True).fill("2027")
    page.get_by_role("spinbutton", name="Section 1", exact=True).fill("2")
    with page.expect_response(lambda response: response.url.endswith('/api/courses') and response.request.method == 'POST') as saved:
        page.get_by_role("button", name="Add course", exact=True).click()
    second = saved.value.json()
    assert first["definition_id"] == second["definition_id"] and first["id"] != second["id"]
    expect(page.get_by_role("dialog")).not_to_be_visible()
    page.locator("#primary-action").click()
    previous = page.get_by_role("combobox", name="Previous course", exact=True)
    assert len([name for name in previous.locator("option").all_text_contents() if name.startswith("PHYS 00101 —")]) == 1
    page.get_by_role("button", name="Cancel", exact=True).click()
    for name in ("Physics code fixture", "Physics next term"):
        page.get_by_role("button", name=f"Edit {name}", exact=True).click()
        expect_course_code(page, "PHYS 00101")
        page.get_by_role("button", name="Remove", exact=True).click()
        page.get_by_role("button", name="Remove course", exact=True).click()
        expect(page.get_by_role("dialog")).not_to_be_visible()
    page.reload()
    page.locator("#primary-action").click()
    assert page.get_by_role("combobox", name="Course prefix", exact=True).locator('option[value="PHYS"]').count() == 1
    page.get_by_role("button", name="Cancel", exact=True).click()
    expect(page.locator(".course-card")).to_have_count(original_count)
