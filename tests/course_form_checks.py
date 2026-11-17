"""Course-form browser checks, using the smoke test's isolated workspace."""

from playwright.sync_api import expect
from course_code_checks import fill_course_code, expect_course_code


def check_course_form(page, output):
    original_count = page.locator(".course-card").count()
    page.locator("#primary-action").click()
    form = page.locator("#course-form")
    previous = page.get_by_role("combobox", name="Previous course", exact=True)
    previous.select_option(label="BIO 101 — Introduction to Biology")
    expect(page.get_by_label("Course name", exact=True)).to_have_value("Introduction to Biology")
    expect_course_code(page, "BIO 101")
    expect(page.get_by_role("combobox", name="Term", exact=True)).to_have_value("")
    year = page.get_by_role("spinbutton", name="Year", exact=True)
    expect(year).to_have_value("")
    expect(page.get_by_label("CRN", exact=False)).to_have_value("")
    expect(page.get_by_label("Multiple Sections?", exact=True)).not_to_be_checked()
    expect(form.locator('[name="section"]')).to_have_count(1)
    expect(page.get_by_role("spinbutton", name="Section 1", exact=True)).to_be_visible()
    expect(page.get_by_role("spinbutton", name="Section 1", exact=True)).to_have_value("")
    expect(page.get_by_role("button", name="Add section", exact=True)).not_to_be_visible()
    assert not form.evaluate("form => form.checkValidity()")
    page.get_by_role("button", name="Add course", exact=True).click()
    expect(page.get_by_role("dialog")).to_be_visible()

    # Every requested term is present, and Other conditionally requires text.
    term = page.get_by_role("combobox", name="Term", exact=True)
    assert term.locator("option").all_text_contents()[1:] == [
        "Fall", "Spring", "Summer I", "Summer II", "Summer (Full)", "Other",
    ]
    term.select_option("Other")
    custom_term = page.get_by_label("Term identifier", exact=True)
    expect(custom_term).to_be_visible()
    expect(custom_term).to_have_attribute("required", "")
    custom_term.fill("Winter intensive")
    page.get_by_label("CRN", exact=False).fill("00123")
    current_year = page.evaluate("new Date().getFullYear()")
    page.get_by_role("button", name="Increase year", exact=True).click()
    expect(year).to_have_value(str(current_year))
    page.get_by_role("button", name="Increase year", exact=True).click()
    expect(year).to_have_value(str(current_year + 1))
    page.get_by_role("button", name="Decrease year", exact=True).click()
    expect(year).to_have_value(str(current_year))
    year.focus()
    year.press("ArrowUp")
    expect(year).to_have_value(str(current_year + 1))
    year.hover()
    page.mouse.wheel(0, -100)
    expect(year).to_have_value(str(current_year + 2))
    year.fill("2029")

    # Changing the previous-course choice never overwrites the other entered fields.
    previous.select_option(label="BIO 201 — Ecology")
    expect(page.get_by_label("Course name", exact=True)).to_have_value("Ecology")
    expect_course_code(page, "BIO 201")
    expect(term).to_have_value("Other")
    expect(custom_term).to_have_value("Winter intensive")
    expect(year).to_have_value("2029")
    expect(page.get_by_label("CRN", exact=False)).to_have_value("00123")
    page.get_by_label("Course name", exact=True).fill("Course Form Fixture")

    # The initial required field survives expansion to two required numeric inputs.
    assert not form.evaluate("form => form.checkValidity()"), "A single section number is required"
    page.get_by_role("spinbutton", name="Section 1", exact=True).fill("11")
    assert form.evaluate("form => form.checkValidity()")
    page.get_by_label("Multiple Sections?", exact=True).check()
    expect(form.locator('[name="section"]')).to_have_count(2)
    expect(page.get_by_role("spinbutton", name="Section 1", exact=True)).to_have_value("11")
    assert not form.evaluate("form => form.checkValidity()")
    page.get_by_role("spinbutton", name="Section 1", exact=True).fill("11")
    page.get_by_role("spinbutton", name="Section 2", exact=True).fill("11")
    assert not form.evaluate("form => form.checkValidity()"), "Duplicate sections must fail validation"
    page.get_by_role("spinbutton", name="Section 2", exact=True).fill("12")
    page.get_by_role("button", name="Add section", exact=True).click()
    expect(form.locator('[name="section"]')).to_have_count(3)
    assert not form.evaluate("form => form.checkValidity()"), "An added section is required"
    page.get_by_role("spinbutton", name="Section 3", exact=True).fill("13")
    page.get_by_role("button", name="Remove section 3", exact=True).click()
    expect(form.locator('[name="section"]')).to_have_count(2)
    page.get_by_role("button", name="Add section", exact=True).click()
    page.get_by_role("spinbutton", name="Section 3", exact=True).fill("13")
    assert form.evaluate("form => form.checkValidity()")
    page.locator("#dialog").evaluate("dialog => dialog.scrollTop = 0")
    page.screenshot(path=str(output / "add-course-desktop.png"), full_page=True)
    with page.expect_response(lambda response: response.url.endswith('/api/courses') and response.request.method == 'POST') as saved:
        page.get_by_role("button", name="Add course", exact=True).click()
    course = saved.value.json()
    assert course["sections"] == [11, 12, 13]
    assert course["crn"] == "00123"
    assert course["term"] == "Other" and course["term_other"] == "Winter intensive"
    assert course["year"] == 2029
    expect(page.locator(".course-card")).to_have_count(original_count + 1)
    page.get_by_role("link", name="Current courses", exact=True).click()
    expect(page.locator(".course-card").get_by_text("Course Form Fixture", exact=True)).to_be_visible()
    page.get_by_role("link", name="All courses", exact=True).click()
    expect(page.locator(".course-card").get_by_text("Course Form Fixture", exact=True)).to_be_visible()

    page.reload()
    page.get_by_role("button", name="Edit Course Form Fixture", exact=True).click()
    expect(page.get_by_role("combobox", name="Term", exact=True)).to_have_value("Other")
    expect(page.get_by_label("Term identifier", exact=True)).to_have_value("Winter intensive")
    expect(page.get_by_role("spinbutton", name="Year", exact=True)).to_have_value("2029")
    expect(page.get_by_label("CRN", exact=False)).to_have_value("00123")
    expect(page.get_by_role("spinbutton", name="Section 3", exact=True)).to_have_value("13")
    # The narrowest supported window (desktop only, 2026-10-03) keeps the dialog on screen without sideways scrolling.
    page.set_viewport_size({"width": 1280, "height": 800})
    page.locator("#dialog").evaluate("dialog => dialog.scrollTop = 0")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    assert page.locator("#dialog").evaluate("dialog => dialog.scrollWidth <= dialog.clientWidth")
    page.screenshot(path=str(output / "course-form-1280.png"), full_page=True)
    page.get_by_role("button", name="Cancel", exact=True).click()
    page.set_viewport_size({"width": 1440, "height": 1080})

    # Reusing a course that has every field populated copies only name and code.
    page.locator("#primary-action").click()
    page.get_by_role("combobox", name="Previous course", exact=True).select_option(label="BIO 201 — Course Form Fixture")
    expect(page.get_by_label("Course name", exact=True)).to_have_value("Course Form Fixture")
    expect_course_code(page, "BIO 201")
    expect(page.get_by_role("combobox", name="Term", exact=True)).to_have_value("")
    expect(page.get_by_role("spinbutton", name="Year", exact=True)).to_have_value("")
    expect(page.get_by_label("CRN", exact=False)).to_have_value("")
    expect(page.get_by_label("Multiple Sections?", exact=True)).not_to_be_checked()
    expect(page.locator('[name="term_other"]')).to_have_value("")
    expect(page.locator('[name="section"]').nth(0)).to_have_value("")
    page.get_by_label("Course name", exact=True).fill("Course Form Reuse")
    page.get_by_role("combobox", name="Term", exact=True).select_option("Other")
    page.get_by_label("Term identifier", exact=True).fill("Discarded custom term")
    page.get_by_role("combobox", name="Term", exact=True).select_option("Summer (Full)")
    expect(page.locator('[name="term_other"]')).to_be_disabled()
    expect(page.get_by_label("Term identifier", exact=True)).not_to_be_visible()
    page.get_by_role("spinbutton", name="Year", exact=True).fill("2030")
    page.get_by_role("spinbutton", name="Section 1", exact=True).fill("21")
    page.get_by_label("Multiple Sections?", exact=True).check()
    page.get_by_role("spinbutton", name="Section 2", exact=True).fill("22")
    page.get_by_label("Multiple Sections?", exact=True).uncheck()
    expect(page.locator('#course-form [name="section"]')).to_have_count(1)
    expect(page.get_by_role("spinbutton", name="Section 1", exact=True)).to_have_value("21")
    # Toggling again restores the extra field without overwriting the first.
    page.get_by_label("Multiple Sections?", exact=True).check()
    expect(page.get_by_role("spinbutton", name="Section 2", exact=True)).to_have_value("22")
    page.get_by_label("Multiple Sections?", exact=True).uncheck()
    assert page.locator('#course-form').evaluate("form => form.checkValidity()"), "One valid section allows saving"
    with page.expect_response(lambda response: response.url.endswith('/api/courses') and response.request.method == 'POST') as saved:
        page.get_by_role("button", name="Add course", exact=True).click()
    course = saved.value.json()
    assert course["crn"] == "" and course["sections"] == [21] and course["term_other"] == ""
    assert course["multiple_sections"] is False
    expect(page.locator(".course-card")).to_have_count(original_count + 2)
    page.reload()
    page.get_by_role("button", name="Edit Course Form Reuse", exact=True).click()
    expect(page.locator('#course-form [name="section"]')).to_have_count(1)
    expect(page.get_by_role("spinbutton", name="Section 1", exact=True)).to_have_value("21")
    page.get_by_role("button", name="Cancel", exact=True).click()

    # A non-current course added from Current courses is immediately visible in All courses.
    page.get_by_role("link", name="Current courses", exact=True).click()
    page.locator("#primary-action").click()
    page.get_by_label("Course name", exact=True).fill("Non-current course fixture")
    fill_course_code(page, "BIO 301")
    page.get_by_role("combobox", name="Term", exact=True).select_option("Fall")
    page.get_by_role("spinbutton", name="Year", exact=True).fill("2025")
    page.get_by_role("spinbutton", name="Section 1", exact=True).fill("7")
    page.get_by_label("Current course", exact=True).uncheck()
    page.get_by_role("button", name="Add course", exact=True).click()
    expect(page.get_by_role("heading", name="All courses", exact=True)).to_be_visible()
    # It sits in the folded "Terms not current" group, which opens to show where the new course went.
    expect(page.locator("details.course-other-terms")).to_have_attribute("open", "")
    expect(page.locator(".course-card").get_by_text("Non-current course fixture", exact=True)).to_be_visible()
    page.get_by_role("link", name="Current courses", exact=True).click()
    expect(page.locator(".course-card").get_by_text("Non-current course fixture", exact=True)).to_have_count(0)
    page.get_by_role("link", name="All courses", exact=True).click()
    page.reload()
    expect(page.locator(".course-card").get_by_text("Non-current course fixture", exact=True)).to_be_visible()

    # Leave the surrounding smoke test with its original course fixture.
    for name in ("Course Form Fixture", "Course Form Reuse", "Non-current course fixture"):
        page.get_by_role("button", name=f"Edit {name}", exact=True).click()
        page.get_by_role("button", name="Remove", exact=True).click()
        page.get_by_role("button", name="Remove course", exact=True).click()
        expect(page.get_by_role("dialog")).not_to_be_visible()
        expect(page.locator(".course-card").get_by_text(name, exact=True)).to_have_count(0)
    expect(page.locator(".course-card")).to_have_count(original_count)
