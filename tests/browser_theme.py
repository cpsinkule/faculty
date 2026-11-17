"""One appearance (DESIGN-LANGUAGE, Ledger) and screen/print separation in an isolated workspace.

The instructor removed the dark appearance and the phone layout (2026-10-03) and chose Ledger (2026-10-04): this checks
that the app has exactly one appearance whatever the OS prefers, that every page opens on the white header band with the
top bar and its one-line title in it and the black rail beside it (shell_checks.check_shell), the Ledger components (the
frame and hard shadow on buttons, Manrope 800 titles, the slate focus ring), that seats and version palettes keep their
contracts, that print stays plain ink on paper, and the browser-local clock. Run with TMPDIR on NVMe, from an NVMe scratch
directory for screenshots.
"""
import sys
import tempfile
import threading
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from playwright.sync_api import expect, sync_playwright
from waitress.server import create_server

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import create_app
from browser_seating import seed
from test_seating import HEADERS
from shell_checks import check_shell


def contrast(page, selector):
    """Measure rendered text against its nearest opaque background."""
    return page.locator(selector).first.evaluate("""element => {
      const canvas = document.createElement('canvas');
      canvas.width = canvas.height = 1;
      const context = canvas.getContext('2d', {willReadFrequently: true});
      const rgb = value => {
        context.clearRect(0, 0, 1, 1); context.fillStyle = value; context.fillRect(0, 0, 1, 1);
        const color = [...context.getImageData(0, 0, 1, 1).data];
        color[3] /= 255; return color;
      };
      const luminance = c => c.slice(0, 3).map(x => x / 255)
        .map(x => x <= .04045 ? x / 12.92 : ((x + .055) / 1.055) ** 2.4)
        .reduce((sum, x, i) => sum + x * [.2126, .7152, .0722][i], 0);
      const fg = luminance(rgb(getComputedStyle(element).color));
      let node = element, bg;
      while (node) {
        const color = rgb(getComputedStyle(node).backgroundColor);
        if (color.length === 3 || color[3] === 1) { bg = luminance(color); break; }
        node = node.parentElement;
      }
      if (bg === undefined) throw new Error('No opaque background');
      return (Math.max(fg, bg) + .05) / (Math.min(fg, bg) + .05);
    }""")


def poll(page, script, label, timeout=6000):
    """Evaluate `script` until it is truthy (the app's CSP refuses wait_for_function's string predicates)."""
    for _ in range(timeout // 100):
        if page.evaluate(script):
            return
        page.wait_for_timeout(100)
    raise AssertionError(label)


INK = "rgb(16, 16, 16)"


def check_band(page, name):
    """The route's header band: the top bar and the title in it, the title on one line in Manrope 800; the black rail beside
    it and no photograph or arc (shell_checks.check_shell)."""
    plate = page.locator(".page-plate")
    expect(plate).to_be_visible()
    expect(page.locator(".page-plate > .topbar")).to_have_count(1)
    expect(page.locator(".page-plate #page-title")).to_have_count(1)
    state = page.evaluate("""() => {
      const plate = document.querySelector('.page-plate'), title = document.querySelector('#page-title');
      const box = plate.getBoundingClientRect(), text = title.getBoundingClientRect(), style = getComputedStyle(title);
      return {
        inside: text.left >= box.left - 1 && text.right <= box.right + 1 && text.top >= box.top && text.bottom <= box.bottom,
        fits: title.scrollWidth <= title.clientWidth + 1 || plate.dataset.titleWrap === 'true',
        font: style.fontFamily, weight: style.fontWeight, color: style.color, fill: style.webkitTextFillColor,
        bar: getComputedStyle(document.querySelector('.page-plate > .topbar')).backgroundColor,
      };
    }""")
    assert state["inside"] and state["fits"], (name, state)
    assert state["font"].split(",")[0].strip().strip('"') == "Manrope" and state["weight"] == "800", (name, "title type", state)
    assert state["color"] == INK and state["fill"] == INK, (name, "title ink", state)
    assert state["bar"] in ("rgba(0, 0, 0, 0)", "transparent"), (name, "the top bar sits on the white band", state)
    check_shell(page, name)


def check_scroll_quiet(page, name):
    """Scrolling the page costs nothing in the shell (requirement, 2026-10-03: a script-driven effect on the rail made
    scrolling choppy once): nothing writes to the root while the page scrolls, the rail stays put and the band scrolls away
    with the page."""
    # A short window, so the page has room to scroll.
    size = page.viewport_size
    page.set_viewport_size({"width": size["width"], "height": 600})
    room = page.evaluate("document.documentElement.scrollHeight - innerHeight")
    assert room >= 100, (name, "room to scroll", room)
    page.evaluate("""() => { window.__rootWrites = 0;
      new MutationObserver(records => { window.__rootWrites += records.length; })
        .observe(document.documentElement, {attributes: true, attributeFilter: ['style']}); }""")
    for y in (min(150, room), min(330, room), 0):
        page.evaluate(f"window.scrollTo(0, {y})")
        poll(page, f"() => Math.round(scrollY) === {y}", (name, "scrolled", y))
        page.wait_for_timeout(120)
        state = page.evaluate("""() => ({
          writes: window.__rootWrites,
          rail: Math.round(document.querySelector('body > .sidebar').getBoundingClientRect().top),
          plate: Math.round(document.querySelector('.page-plate').getBoundingClientRect().top)})""")
        assert state == {"writes": 0, "rail": 0, "plate": -y}, (name, y, state)
    page.set_viewport_size(size)


def check_components(page, name):
    """Ledger's components (DESIGN-LANGUAGE §4): a secondary button is white in the 2 px ink frame with the 3 px hard
    shadow, square; a focused field takes the slate ring, never the needs-you orange."""
    # (The header's secondaries and Home's quick links are drawn as text actions on purpose; a disabled button casts no shadow.)
    button = page.locator("main .button:not(.primary, [data-exam-version], .seating-card-button, .text-button, .home-shortcuts > .button, .page-heading-secondary > .button):not(:disabled):visible").first
    if button.count():
        style = button.evaluate("""el => { const s = getComputedStyle(el);
          return [s.borderTopWidth, s.borderTopStyle, s.borderTopColor, s.boxShadow, s.borderTopLeftRadius, s.textTransform, el.className, el.textContent.trim().slice(0, 40)]; }""")
        assert style[:3] == ["2px", "solid", INK] and style[3].startswith(f"{INK} 3px 3px 0px") and style[4] == "0px" \
            and style[5] == "none", (name, "button", style)


def check_frame_fonts(page):
    """The requested box glyphs must render in Berkeley, not an OS fallback."""
    session = page.context.new_cdp_session(page)
    session.send('DOM.enable')
    session.send('CSS.enable')
    root = session.send('DOM.getDocument')['root']['nodeId']
    page.evaluate('document.fonts.ready')
    for selector in ('.seat.seated .seat-frame-tl', '.seat.seated .seat-frame-top',
                     '.seat.seated .seat-frame-left', '.seat.free .seat-frame-top',
                     '.seat.free .seat-frame-left'):
        node = session.send('DOM.querySelector', {'nodeId': root, 'selector': '#seating-section ' + selector})['nodeId']
        pseudo = session.send('DOM.describeNode', {'nodeId': node, 'depth': 1})['node']['pseudoElements'][0]
        pseudo_id = session.send('DOM.pushNodesByBackendIdsToFrontend', {'backendNodeIds': [pseudo['backendNodeId']]})['nodeIds'][0]
        fonts = session.send('CSS.getPlatformFontsForNode', {'nodeId': pseudo_id})['fonts']
        rendered = [font for font in fonts if font['glyphCount']]
        assert rendered and all(font['isCustomFont'] and font['familyName'] == 'Berkeley Mono' for font in rendered), (selector, fonts)
    session.detach()


def main():
    output = Path("test-results")
    output.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as directory:
        app = create_app(directory)
        client = seed(app)
        group = client.get("/api/state").json["course_groups"][0]
        room = client.get("/api/rooms").json[0]
        catalog = client.get("/api/seating/catalog").json
        created = client.post("/api/seating", json={
            "assignment_id": catalog["assignments"][0]["id"],
            "section_ids": [section["id"] for section in catalog["sections"]],
            "room_id": room["id"], "exam_version_count": 2,
        }, headers=HEADERS)
        assert created.status_code == 201, created.json
        chart = created.json
        # name: route
        routes = {
            "overview": "overview", "courses": "current-courses",
            "home": f"course/{group['id']}/home", "roster": f"course/{group['id']}/section/507/roster",
            "inbox": "inbox", "documents": "documents", "accommodations": "accommodations",
            "rooms": "rooms", "room": f"rooms/{room['id']}", "seating": "seating",
            "chart": f"seating/{chart['id']}",
            "response-cards": "response-cards", "exams": "exams",
        }
        server = create_server(app, host="127.0.0.1", port=0)
        threading.Thread(target=server.run, daemon=True).start()
        address = f"http://127.0.0.1:{server.effective_port}"
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                # An OS that prefers dark still gets the one appearance.
                context = browser.new_context(color_scheme="dark", reduced_motion="reduce", viewport={"width": 1440, "height": 900})
                page = context.new_page()
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
                page.goto(address)
                expect(page.locator(".server-status")).to_have_class("server-status connected")
                assert page.evaluate("document.documentElement.dataset.theme") is None
                assert page.locator("html").evaluate("el => getComputedStyle(el).colorScheme") == "light"
                expect(page.locator("body")).to_have_css("color", "rgb(30, 30, 30)")
                assert page.locator("html").evaluate("el => getComputedStyle(el).backgroundColor") == "rgb(243, 242, 236)"
                # Flat: no paper grain or other texture (DESIGN-LANGUAGE §2, §7).
                assert page.locator("html").evaluate("el => getComputedStyle(el).backgroundImage") == "none"
                # Settings keep no appearance controls, and the palette offers no theme.
                page.locator('.sidebar [data-action="settings"]').click()
                expect(page.locator("#settings-form")).to_be_visible()
                expect(page.locator("#settings-form .settings-appearance, [data-theme-choice], .theme-toggle, .shimmer-toggle")).to_have_count(0)
                page.keyboard.press("Escape")
                page.keyboard.press("Control+k")
                page.keyboard.type("theme")
                expect(page.locator(".palette-list")).not_to_contain_text("dark theme")
                page.keyboard.press("Escape")

                for width, height in ((1440, 900), (1280, 800)):
                    page.set_viewport_size({"width": width, "height": height})
                    for name, route in routes.items():
                        page.goto(address + "/#" + route)
                        expect(page.locator(".server-status")).to_have_class("server-status connected")
                        view = "courses" if route.startswith("course/") else route.split("/")[0]
                        # Assignments & printing, Exams and Grading open inside the Assignments area (ASSIGNMENT-WORKSPACE).
                        view = {"documents": "assignments", "exams": "assignments"}.get(view, view)
                        expect(page.locator("body")).to_have_attribute("data-workspace-view", view)
                        check_band(page, name)
                        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), (name, width)
                        if width == 1440:
                            check_components(page, name)
                            if name == "overview":
                                check_scroll_quiet(page, name)
                            if page.locator("#page-subtitle").is_visible():
                                assert contrast(page, "#page-subtitle") >= 4.5, name
                            primary = page.locator("#primary-action")
                            if primary.is_visible():
                                assert contrast(page, "#primary-action") >= 4.5, (name, "primary")
                            # The rail's own words, light on the black rail.
                            for selector in ("#workspace-nav .nav-item:not(.active) .nav-label-text", ".sidebar > .nav-label", "#workspace-nav .nav-section-label"):
                                assert contrast(page, selector) >= 4.5, (name, selector)
                            if name in ("room", "chart"):
                                scope = '#rooms-section' if name == 'room' else '#seating-section'
                                expect(page.locator(f'{scope} .seat-chart .seat')).not_to_have_count(0)
                                for selector in ('.seat-plan-caption-label', '.seat-plan-cue', '.seat-rowlabel', '.seat'):
                                    assert contrast(page, f'{scope} {selector}') >= 4.5, (name, selector)
                                frames = page.locator(f'{scope} .seat-frame')
                                assert frames.count() == page.locator(f'{scope} .seat').count()
                                expect(frames.first).to_have_attribute('aria-hidden', 'true')
                                assert frames.first.evaluate("el => getComputedStyle(el).display === 'grid'")
                                if name == 'chart':
                                    check_frame_fonts(page)
                            page.screenshot(path=str(output / f"design-{name}-{width}.png"), full_page=True)

                # Seats, counts and editor choices keep the version palette.
                page.set_viewport_size({"width": 1440, "height": 900})
                page.goto(address + f"/#seating/{chart['id']}")
                expect(page.locator('[data-seating-action="version-view"]')).to_be_visible()
                if page.locator(".seating-action-group .button").count():
                    assert contrast(page, ".seating-action-group .button") >= 4.5
                if not page.locator(".seating-view.version-view").count():
                    page.locator('[data-seating-action="version-view"]').click()
                page.locator(".seat.version-seat").first.click()
                expect(page.locator(".seat-version-popover")).to_be_visible()
                assert contrast(page, ".seat-popover-head strong") >= 4.5
                palette = page.locator(".seating-version-choices .button").evaluate_all(
                    "buttons => buttons.map(button => [getComputedStyle(button).color, getComputedStyle(button).backgroundColor])")
                assert palette[0] != palette[1]
                for letter in ('A', 'B'):
                    selectors = [f'.seat.version-seat[data-exam-version="{letter}"]',
                                 f'.seating-version-key[data-exam-version="{letter}"]',
                                 f'.seating-version-choices [data-exam-version="{letter}"]']
                    colors = []
                    for selector in selectors:
                        assert contrast(page, selector) >= 4.5, selector
                        colors.append(page.locator(selector).first.evaluate('el => [getComputedStyle(el).color, getComputedStyle(el).backgroundColor]'))
                    assert colors[0] == colors[1] == colors[2], (letter, colors)
                # Printed seats drop their screen frames.
                page.emulate_media(media='print')
                assert page.locator('#seating-section .seat-frame').first.evaluate("el => getComputedStyle(el).display === 'none'")
                page.emulate_media(media='screen')
                page.screenshot(path=str(output / "design-version-editor.png"), full_page=True)

                page.goto(address + "/#current-courses")
                page.locator("#primary-action").click()
                expect(page.get_by_role("dialog")).to_be_visible()
                assert contrast(page, ".dialog-header h2") >= 4.5
                assert contrast(page, '.field input[name="name"]') >= 4.5
                dialog = page.locator("dialog[open]").evaluate("""el => { const s = getComputedStyle(el);
                  return [s.backgroundColor, s.borderTopWidth, s.borderTopColor, s.boxShadow]; }""")
                assert dialog[:3] == ["rgb(255, 255, 255)", "2px", INK] and dialog[3].startswith(f"{INK} 6px 6px 0px"), dialog
                # Focus is the slate ring (requirement, 2026-10-04), never the needs-you orange.
                page.keyboard.press("Tab")
                ring = page.evaluate("() => { const s = getComputedStyle(document.activeElement); return [s.outlineStyle, s.outlineColor]; }")
                assert ring == ["solid", "rgb(62, 90, 102)"], ring
                page.screenshot(path=str(output / "design-dialog.png"))
                page.keyboard.press("Escape")

                # Print is plain ink on paper: no top bar, the title in black.
                page.goto(address + "/#exams")
                page.mouse.move(0, 0)
                page.emulate_media(media="print")
                # The Magazine edition's photograph plates and sidebar arc are gone from the markup altogether (2026-10-04).
                expect(page.locator(".plate-image, .plate-grain, .sidebar-arc")).to_have_count(0)
                assert page.locator(".page-plate > .topbar").evaluate("el => getComputedStyle(el).display") == "none"
                assert page.locator("#page-title").evaluate("el => getComputedStyle(el).webkitTextFillColor") == "rgb(0, 0, 0)"
                assert page.locator("#page-title").evaluate("el => getComputedStyle(el).filter") == "none"
                page.emulate_media(media="screen")
                # Forced colours: the band and its title stay readable in the system's colours.
                page.emulate_media(forced_colors="active")
                expect(page.locator("#page-title")).to_be_visible()
                page.emulate_media(forced_colors="none")
                context.close()

                # Storage-disabled browsers still load the workspace without an error.
                restricted = browser.new_context(viewport={"width": 1440, "height": 900})
                restricted.add_init_script("""Object.defineProperty(window, 'localStorage', {
                  get() { throw new DOMException('Blocked', 'SecurityError'); }
                });""")
                page = restricted.new_page()
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(address)
                expect(page.locator(".server-status")).to_have_class("server-status connected")
                expect(page.locator(".page-plate")).to_be_visible()
                restricted.close()

                # A browser-local clock crosses midnight without a route change; the Today label is the date.
                clock_context = browser.new_context(locale="en-US", timezone_id="America/Chicago",
                    reduced_motion="no-preference", viewport={"width": 1440, "height": 900})
                page = clock_context.new_page()
                page.on("pageerror", lambda error: errors.append(str(error)))
                instant = datetime(2026, 9, 22, 23, 59, 58, tzinfo=ZoneInfo("America/Chicago"))
                page.clock.install(time=instant)
                page.clock.pause_at(instant)
                page.goto(address)
                expect(page.locator("#workspace-date")).to_have_attribute("datetime", "2026-09-22")
                # Hours and minutes only: no seconds, year or time-zone name (Ledger, the instructor: "change all of those").
                expect(page.locator("#workspace-time")).to_have_text("11:59 PM")
                expect(page.locator("#workspace-date")).to_have_text("Tue, Sep 22")
                # The Faculty assistant's two launchers are the top bar's only controls: the area button (ASSISTANT "Selected-area
                # conversations": the toolbar button stays) and the Assistant. No other button, badge or avatar.
                expect(page.locator(".topbar button:not([data-assistant-open], [data-assistant-capture]), .topbar .private-badge, .topbar .avatar")).to_have_count(0)
                expect(page.locator(".topbar [data-assistant-open]")).to_have_count(1)
                expect(page.locator(".topbar [data-assistant-capture]")).to_have_count(1)
                page.clock.fast_forward(3000)
                expect(page.locator("#workspace-date")).to_have_attribute("datetime", "2026-09-23")
                expect(page.locator("#workspace-time")).to_have_text("12:00 AM")
                expect(page.locator("#workspace-date")).to_have_text("Wed, Sep 23")
                # The Today label's date stays current, though the band shows the clock's instead (Ledger).
                expect(page.locator("#today")).to_contain_text("WEDNESDAY")
                page.clock.resume()
                clock_context.close()
                browser.close()
                assert not errors, errors
                print("Design checks passed: one light appearance on a dark OS (flat, no texture), no appearance controls, 13 screens at 1440 and 1280 on the white header band (top bar, one-line Manrope 800 title, the black rail, no photograph or arc), framed buttons with the hard shadow, a framed dialog with the strong shadow, the slate focus ring, nothing written while the page scrolls, contrast, seat palettes and frames, print as plain ink, forced colours, blocked storage and the local clock's midnight rollover.")
        finally:
            server.close()


if __name__ == "__main__":
    main()
