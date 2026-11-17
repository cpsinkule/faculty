"""The app shell's DESIGN-LANGUAGE checks (Ledger, §5 shell), shared by browser_theme.py and browser_shell_routes.py.

shell_state(page) measures the shell in the open page; check_shell(page, label) asserts what every route and every open
dialog must keep:
- the rail: fixed, black (`--sidebar-bg`) from top to bottom (no mask), nothing drawn over its edge, the footer (Workspace
  settings, the server status) inside the window and never covered;
- no arc and no reprint of the menu: Ledger has none (the Magazine edition's dusk arc is removed, §7);
- the header band (unless the route has none open): white, from the rail's edge to the window's right edge, a 2 px ink rule
  under it, at most 90 px tall, and no photograph or grain drawn in it.
"""

INK = "rgb(16, 16, 16)"
BAND_MAX = 90

STATE = """() => {
  const rail = document.querySelector('body > .sidebar');
  const rect = node => { const b = node.getBoundingClientRect(); return [b.left, b.top, b.width, b.height].map(v => Math.round(v * 100) / 100); };
  const railBox = rail.getBoundingClientRect(), railStyle = getComputedStyle(rail);
  const shown = node => { if (!node) return false; const s = getComputedStyle(node), b = node.getBoundingClientRect();
    return s.display !== 'none' && s.visibility !== 'hidden' && b.width > 0 && b.height > 0; };
  // What is drawn over the rail's edge: hit-test just inside it at the top, middle and bottom.
  const edgeX = railBox.right - 0.5;
  const edge = [8, innerHeight / 2, innerHeight - 8].map(y => { const hit = document.elementFromPoint(edgeX, y); return !!hit && rail.contains(hit); });
  const footer = [...rail.querySelectorAll(':scope > .sidebar-bottom :is(.nav-item, .server-status)')].map(node => {
    const b = node.getBoundingClientRect();
    const hit = document.elementFromPoint(b.left + Math.min(20, b.width / 2), b.top + b.height / 2);
    return {text: node.textContent.trim().slice(0, 30), inside: b.top >= 0 && b.bottom <= innerHeight + 0.5, own: !!hit && node.contains(hit)};
  });
  const plate = document.querySelector('.page-plate');
  const plateBox = plate ? plate.getBoundingClientRect() : null, plateStyle = plate ? getComputedStyle(plate) : null;
  return {
    view: [innerWidth, innerHeight],
    rail: {box: rect(rail), position: railStyle.position, mask: railStyle.maskImage, background: railStyle.backgroundColor,
           image: railStyle.backgroundImage, edgeClear: edge},
    arc: shown(document.querySelector('.sidebar-arc')), reprint: [...document.querySelectorAll('.sidebar-arc-text')].filter(shown).length,
    footer,
    plate: plate ? {left: plateBox.left, right: plateBox.right, height: plateBox.height, visible: plateBox.width > 0 && plateBox.height > 0,
                    background: plateStyle.backgroundColor, rule: [plateStyle.borderBottomWidth, plateStyle.borderBottomStyle, plateStyle.borderBottomColor],
                    pictures: [...plate.querySelectorAll('.plate-image, .plate-grain, img')].filter(shown).length} : null,
    layers: document.querySelectorAll('.plate-backdrop, .plate-rail, .sidebar-plate').length,
    page: Math.round(document.documentElement.getBoundingClientRect().width),
  };
}"""


def shell_state(page):
    return page.evaluate(STATE)


def check_shell(page, label, *, dialog=False, plate=True):
    """Assert the shell's contract on the open page. With `dialog`, a modal is open over the page: the rail must be
    unchanged behind it (hit tests are skipped: the modal answers them)."""
    state = shell_state(page)
    width, height = state["view"]
    rail = state["rail"]
    assert rail["position"] == "fixed" and rail["box"][0] == 0 and rail["box"][1] == 0 and rail["box"][3] == height, (label, "rail box", state)
    assert rail["mask"] == "none" and rail["background"] == INK and rail["image"] == "none", (label, "the black rail", state)
    if not dialog:
        assert all(rail["edgeClear"]), (label, "something covers the rail's edge", state)
    assert not state["arc"] and not state["reprint"] and state["layers"] == 0, (label, "no arc, reprint or plate layers", state)
    if not dialog:
        assert state["footer"] and all(item["inside"] and item["own"] for item in state["footer"]), (label, "footer", state)
    if plate:
        box = state["plate"]
        assert box and box["visible"], (label, "header band", state)
        assert abs(box["left"] - rail["box"][2]) <= 0.5 and abs(box["right"] - state["page"]) <= 1, (label, "band edges", state)
        assert box["background"] == "rgb(255, 255, 255)" and box["pictures"] == 0, (label, "white band, no photograph", state)
        assert box["rule"] == ["2px", "solid", INK], (label, "band rule", state)
        assert box["height"] <= BAND_MAX, (label, "band height", state)
    return state


# SENTENCE-CASE ACTIONS (DESIGN-LANGUAGE §1.12; requirement, 2026-10-04): small capitals are labels only. Every visible clickable thing with no
# frame (no border on all four sides, no fill, no ring) is written as it reads. Its own text-transform is what counts: a label
# inside a clickable record (a card's eyebrow, a fold's count hint, a keyboard key) stays a label. Exempt by contract: a
# table's column headings (they may sort), the seat chart's drawn table titles and the room's stat tiles (records with a
# count), and exam-version colours.
CAPITALS = """() => {
  const sel = 'a[href], button, summary, [role=button], [role=tab], [role=link], [role=menuitem], [role=option], input[type=submit], input[type=button]';
  const framed = s => ['Top', 'Right', 'Bottom', 'Left'].every(k => parseFloat(s['border' + k + 'Width']) >= 1 && s['border' + k + 'Style'] !== 'none'
      && s['border' + k + 'Color'] !== 'rgba(0, 0, 0, 0)')
    || !['rgba(0, 0, 0, 0)', 'transparent'].includes(s.backgroundColor) || /(^|,)\\s*rgb[^)]*\\)\\s+0px\\s+0px\\s+0px\\s+[1-9]/.test(s.boxShadow) || s.boxShadow.includes('inset');
  return [...document.querySelectorAll(sel)].filter(el => {
    if (el.closest('[aria-hidden="true"], [inert], th, kbd, .seat-table-title, .room-stat, [data-exam-version]')) return false;
    const box = el.getBoundingClientRect(), s = getComputedStyle(el);
    return box.width && box.height && s.visibility !== 'hidden' && s.textTransform === 'uppercase' && !framed(s)
      && /[a-z]/i.test(el.innerText || el.value || '');
  }).map(el => `${el.tagName.toLowerCase()}.${String(el.className || '').trim().split(/\\s+/).join('.')}: ${(el.innerText || el.value || '').trim().slice(0, 40)}`);
}"""


def check_sentence_case(page, label):
    """No unframed clickable on the open page is drawn in capitals (SENTENCE-CASE ACTIONS)."""
    found = page.evaluate(CAPITALS)
    assert not found, (label, "clickable text in small capitals (labels only)", found)
