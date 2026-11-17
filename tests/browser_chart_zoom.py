"""Shared chart gesture zoom on room and exam views, with isolated geometry.

Spreadsheet fixtures use the retained parser; floor-plan geometry is a browser
response fixture, not a PDF parser or physical-print qualification test.
Run: .venv/bin/python tests/browser_chart_zoom.py
"""
import copy
import sys
import threading
from pathlib import Path

from playwright.sync_api import expect, sync_playwright
from waitress.server import create_server

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_seating import SeatingTests


STATE = """grid => {
    const bounds = grid.getBoundingClientRect();
    const holder = grid.closest('.seat-chart');
    return {scale: new DOMMatrix(getComputedStyle(grid).transform).a,
        x: bounds.left, y: bounds.top, width: bounds.width, height: bounds.height,
        scrollLeft: holder.scrollLeft, scrollTop: holder.scrollTop,
        pageY: window.scrollY, browserScale: window.visualViewport.scale,
        devicePixelRatio: window.devicePixelRatio};
}"""


def state(grid):
    return grid.evaluate(STATE)


def chart_point(stage):
    return stage.evaluate("""stage => {
        const box = stage.getBoundingClientRect();
        const holder = stage.closest('.seat-chart').getBoundingClientRect();
        const left = Math.max(box.left, holder.left + 12), right = Math.min(box.right, holder.right - 12, innerWidth - 12);
        const top = Math.max(box.top, holder.top + 12, 100), bottom = Math.min(box.bottom, holder.bottom - 12, innerHeight - 24);
        if (!(right > left && bottom > top)) throw new Error('Chart stage has no visible gesture target');
        return {x: left + (right - left) * .45, y: top + (bottom - top) * .4};
    }""")


def wheel(target, point, delta=-90, control=True, mode=0, repeats=1):
    return target.evaluate("""async (target, arg) => {
        const prevented = [];
        for (let index = 0; index < arg.repeats; index++) {
            const event = new WheelEvent('wheel', {bubbles: true, cancelable: true,
                ctrlKey: arg.control, deltaY: arg.delta, deltaMode: arg.mode,
                clientX: arg.point.x, clientY: arg.point.y});
            target.dispatchEvent(event); prevented.push(event.defaultPrevented);
        }
        await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
        return prevented;
    }""", {"point": point, "delta": delta, "control": control, "mode": mode, "repeats": repeats})


def gesture(target, point, kind, scale):
    # Chromium has no native GestureEvent constructor. Dispatch its Safari
    # event contract without claiming a physical Mac trackpad qualification.
    return target.evaluate("""async (target, arg) => {
        const event = new Event(arg.kind, {bubbles: true, cancelable: true});
        for (const [key, value] of Object.entries({scale: arg.scale, clientX: arg.point.x, clientY: arg.point.y}))
            Object.defineProperty(event, key, {value});
        target.dispatchEvent(event);
        await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
        return event.defaultPrevented;
    }""", {"point": point, "kind": kind, "scale": scale})


def anchored(before, after, point):
    for axis in ("x", "y"):
        original_content = (point[axis] - before[axis]) / before["scale"]
        projected = after[axis] + original_content * after["scale"]
        assert abs(projected - point[axis]) <= 2.5, (axis, before, after, point, projected)


def exercise(page, address, fixture, chart, view, floorplan):
    room = copy.deepcopy(fixture.hall)
    if floorplan:
        room["layout"] = {"kind": "floorplan", "width": 10000, "height": 5000,
            "seats": {seat["label"]: {"x": 80 + index % 14 * 300, "y": 80 + index // 14 * 180,
                                        "width": 20, "height": 20}
                      for index, seat in enumerate(room["seats"])},
            "features": [{"kind": "rect", "x": 10, "y": 10, "width": 9980, "height": 4980}]}
    payload = copy.deepcopy(chart) if view == "seating" else room
    if view == "seating":
        # Keep seated occupants from the actual chart while replacing only its
        # display geometry. No catalog records or assignments are changed.
        payload["room"]["layout"] = room.get("layout")
    owner = chart["id"] if view == "seating" else room["id"]
    endpoint = f"**/api/{view if view == 'seating' else 'rooms'}/{owner}"
    page.route(endpoint, lambda request: request.fulfill(json=payload))
    page.goto(address + f"/#{view if view == 'seating' else 'rooms'}/{owner}")
    grid, holder = page.locator(".seat-grid:visible"), page.locator(".seat-chart:visible")
    stage = page.locator(".seat-plan-stage:visible")
    expect(grid).to_be_visible()
    expect(grid.locator("[data-seat]")).to_have_count(len(room["seats"]))
    assert grid.evaluate("node => node.classList.contains('seat-grid-floorplan')") is floorplan

    # Small fitted plans increase continuously rather than jumping to 10%.
    if floorplan:
        expect(holder.locator('[data-plan-action="fit"]')).to_have_attribute("aria-pressed", "true")
        fitted = state(grid)["scale"]
        assert 0.05 <= fitted < 0.1, fitted
        holder.locator('[data-plan-action="in"]').click()
        assert fitted < state(grid)["scale"] < 0.1

    holder.locator('[data-plan-action="actual"]').click()
    expect(holder.locator("output")).to_have_text("100%")
    holder.evaluate("node => { node.scrollLeft = 160; node.scrollTop = 80; window.scrollBy(0, node.getBoundingClientRect().top - 130); }")
    point = chart_point(stage)
    before = state(grid)
    # The anchor checks need a plan wider than its panel at 100%: a plan that only just overflows once zoomed has too
    # little scroll range to hold the pointer's point, whatever the zoom does.
    assert before["scrollLeft"] > 0, ("the plan must overflow its panel at 100% for the anchor checks", before)
    assert wheel(stage, point) == [True]
    after = state(grid)
    assert after["scale"] > before["scale"]
    anchored(before, after, point)
    assert after["browserScale"] == before["browserScale"] == 1
    assert after["devicePixelRatio"] == before["devicePixelRatio"]
    expect(holder.locator('[data-plan-action="fit"]')).to_have_attribute("aria-pressed", "false")

    # Plain wheel still scrolls; it cannot silently become chart zoom.
    point = chart_point(stage)
    before = state(grid)
    assert wheel(stage, point, delta=80, control=False) == [False]
    assert state(grid)["scale"] == before["scale"]
    page.mouse.move(point["x"], point["y"])
    page.mouse.wheel(100, 0)
    page.evaluate("""async initial => {
        for (let tick = 0; tick < 120; tick++) {
            if (document.querySelector('.seat-chart').scrollLeft > initial + 1) return;
            await new Promise(requestAnimationFrame);
        }
        throw new Error('Native horizontal wheel did not scroll the chart');
    }""", before["scrollLeft"])
    assert state(grid)["scale"] == before["scale"]

    # Keyboard Ctrl + actual browser wheel takes the same local zoom path.
    point = chart_point(stage)
    page.mouse.move(point["x"], point["y"])
    before = state(grid)
    page.keyboard.down("Control")
    page.mouse.wheel(0, -45)
    page.keyboard.up("Control")
    page.evaluate("""async initial => {
        for (let tick = 0; tick < 120; tick++) {
            if (new DOMMatrix(getComputedStyle(document.querySelector('.seat-grid')).transform).a > initial) return;
            await new Promise(requestAnimationFrame);
        }
        throw new Error('Ctrl-wheel did not zoom the chart');
    }""", before["scale"])
    after = state(grid)
    anchored(before, after, point)
    assert after["browserScale"] == before["browserScale"]
    assert after["devicePixelRatio"] == before["devicePixelRatio"]

    # Controls and the rest of the app retain the browser's native Ctrl-wheel.
    before = state(grid)
    assert wheel(holder.locator('[data-plan-action="actual"]'), chart_point(stage)) == [False]
    assert wheel(page.locator("#page-title"), {"x": 300, "y": 50}) == [False]
    assert state(grid)["scale"] == before["scale"]

    # Line/page delta units and sustained trackpad gestures stay bounded.
    holder.locator('[data-plan-action="actual"]').click()
    point = chart_point(stage)
    assert wheel(stage, point, delta=-2, mode=1) == [True]
    assert state(grid)["scale"] > 1
    assert all(wheel(stage, chart_point(stage), delta=-10000, mode=2, repeats=20))
    assert abs(state(grid)["scale"] - 2) < 0.00001
    expect(holder.locator('[data-plan-action="in"]')).to_be_disabled()
    assert all(wheel(stage, chart_point(stage), delta=10000, mode=2, repeats=30))
    assert abs(state(grid)["scale"] - 0.05) < 0.00001
    expect(holder.locator('[data-plan-action="out"]')).to_be_disabled()
    assert "NaN" not in holder.locator("output").inner_text()

    # Safari's incremental gesture scale is relative to its start; Ctrl-wheel
    # emitted during that same gesture must not apply a second scale factor.
    holder.locator('[data-plan-action="actual"]').click()
    holder.evaluate("node => { node.scrollLeft = 160; window.scrollBy(0, node.getBoundingClientRect().top - 130); }")
    point = chart_point(stage)
    before = state(grid)
    assert gesture(stage, point, "gesturestart", 1)
    assert gesture(stage, point, "gesturechange", 1.2)
    after = state(grid)
    assert abs(after["scale"] - 1.2) < 0.00001
    anchored(before, after, point)
    assert wheel(stage, point, delta=-100) == [True]
    assert abs(state(grid)["scale"] - 1.2) < 0.00001
    assert gesture(stage, point, "gesturechange", 1.4)
    assert abs(state(grid)["scale"] - 1.4) < 0.00001
    assert gesture(stage, point, "gestureend", 1.4)
    assert wheel(stage, chart_point(stage), delta=25) == [True]
    assert state(grid)["scale"] < 1.4

    # Buttons and ordinary seat interactions still work after gestures.
    holder.locator('[data-plan-action="actual"]').click()
    expect(holder.locator("output")).to_have_text("100%")
    holder.locator('[data-plan-action="fit"]').click()
    expect(holder.locator('[data-plan-action="fit"]')).to_have_attribute("aria-pressed", "true")
    if view == "rooms":
        holder.locator('[data-plan-action="actual"]').click()
        candidate = grid.locator("[data-seat]").first
        candidate.click()
        expect(candidate).to_have_attribute("aria-pressed", "true")
        expect(page.locator("#seat-selection-count")).to_have_text("1 seat selected")
    else:
        candidate = grid.locator(".seat.seated").first
        candidate.click()
        expect(candidate).to_have_attribute("aria-pressed", "true")
        expect(grid.locator(".seat.move-source")).to_have_count(1)
        expect(page.locator(".seating-move-selection")).to_be_visible()
        expect(page.locator("#seating-move")).to_have_count(0)
        # Selecting a destination remains possible while adjusting zoom. The
        # controls and pinch events must never count as destination clicks.
        holder.locator('[data-plan-action="in"]').click()
        expect(candidate).to_have_attribute("aria-pressed", "true")
        holder.evaluate("node => window.scrollBy(0, node.getBoundingClientRect().top - 130)")
        assert wheel(stage, chart_point(stage), delta=-25) == [True]
        expect(candidate).to_have_attribute("aria-pressed", "true")

    # Navigation retains the chosen scale, and replacing bindings on the same
    # grid must not multiply one physical gesture into several zoom operations.
    holder.locator('[data-plan-action="actual"]').click()
    holder.evaluate("node => window.scrollBy(0, node.getBoundingClientRect().top - 130)")
    assert wheel(stage, chart_point(stage), delta=-25) == [True]
    saved_scale = state(grid)["scale"]
    section = "seating" if view == "seating" else "rooms"
    page.evaluate("hash => { location.hash = hash; }", f"#{section}")
    expect(grid).to_have_count(0)
    page.evaluate("hash => { location.hash = hash; }", f"#{section}/{owner}")
    expect(grid).to_be_visible()
    if view == "seating":
        expect(grid.locator(".seat.move-source")).to_have_count(0)
        expect(page.locator(".seating-move-selection")).to_be_hidden()
    assert abs(state(grid)["scale"] - saved_scale) < 0.00001
    display_room = payload["room"] if view == "seating" else payload
    page.evaluate("""async room => {
        const entry = document.querySelector('script[type=module][src]').src;
        const {placeSeatGrid} = await import(new URL('seat-grid.js', entry).href);
        const root = document.querySelector('.seat-grid').closest('.room-chart-panel');
        placeSeatGrid(root, room);
        placeSeatGrid(root, room);
    }""", display_room)
    holder.evaluate("node => window.scrollBy(0, node.getBoundingClientRect().top - 130)")
    assert wheel(stage, chart_point(stage), delta=-25) == [True]
    assert abs(state(grid)["scale"] - saved_scale * saved_scale) < 0.0001
    assert page.evaluate("window.chartCspViolations") == []
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")


def main():
    fixture = SeatingTests("test_generation_skips_dr_booked_students_and_uses_usable_seats")
    fixture.setUp()
    chart = fixture.create().json
    server = create_server(fixture.app, host="127.0.0.1", port=0)
    threading.Thread(target=server.run, daemon=True).start()
    address = f"http://127.0.0.1:{server.effective_port}"
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            errors, writes = [], []
            for view in ("rooms", "seating"):
                for floorplan in (False, True):
                    # The narrowest desktop window (DESIGN-LANGUAGE: desktop only, html min-width 1180px): at 1440 the
                    # Magazine page column is wide enough to show the whole spreadsheet plan at 100%, which leaves no
                    # scroll range for the pointer-anchor checks.
                    page = browser.new_page(viewport={"width": 1280, "height": 1000})
                    page.set_default_timeout(10000)
                    page.add_init_script("""window.chartCspViolations = [];
                        document.addEventListener('securitypolicyviolation', event =>
                            window.chartCspViolations.push(event.violatedDirective));""")
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    page.on("console", lambda message: errors.append(message.text)
                            if message.type == "error" and not message.text.startswith("Failed to load resource") else None)
                    page.on("request", lambda request: writes.append(request.url)
                            if request.method in ("POST", "PATCH", "DELETE", "PUT") else None)
                    exercise(page, address, fixture, chart, view, floorplan)
                    page.close()
            assert not errors, errors
            assert not writes, writes
            browser.close()
        print("PASS: room/seating spreadsheet/floor-plan gesture zoom, stable pointer anchors, plain-wheel scrolling, local Ctrl-wheel, bounded zoom, Safari gesture contract, existing controls/seat clicks, CSP; no writes or real email.")
    finally:
        server.close()
        fixture.doCleanups()


if __name__ == "__main__":
    main()
