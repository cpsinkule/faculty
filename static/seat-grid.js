// Shared room plans. Workbook charts keep their cell positions and aisle gaps;
// supplied floor plans keep their measured seat and architectural geometry.
// Both views use the same seat buttons, conditions and exam interactions.

const views = new Map();
const activePlans = new Set();
const MIN_ZOOM = 0.05;
const MAX_ZOOM = 2;
// Empty, aria-hidden spans receive Berkeley box-drawing glyphs from the screen
// stylesheet. They add no spoken/text content and are absent from print layouts.
export const seatFrame = `<span class="seat-frame" aria-hidden="true">${
  ["tl", "top", "tr", "left", "right", "bl", "bottom", "br"]
    .map(part => `<span class="seat-frame-${part}"></span>`).join("")
}</span>`;
window.addEventListener("resize", () => {
  for (const plan of activePlans) {
    if (plan.grid.isConnected) plan.update();
    else {
      plan.dispose();
      activePlans.delete(plan);
    }
  }
});

export function conditionsOf(seat) {
  return [seat.broken ? "broken" : "", seat.left_handed ? "left-handed" : "", seat.blocked ? "blocked" : ""].filter(Boolean);
}

function geometry(item) {
  return ` data-row="${item.row}" data-column="${item.column}" data-rowspan="${item.row_span || 1}" data-span="${item.column_span || 1}"`;
}

function landmarkSpan(room, landmark) {
  if ((landmark.column_span || 1) > 1 || (landmark.row_span || 1) > 1) return landmark.column_span || 1;
  const occupied = [...room.seats, ...room.landmarks]
    .filter((item) => item !== landmark && item.row === landmark.row && item.column > landmark.column)
    .map((item) => item.column);
  const next = occupied.length ? Math.min(...occupied) : room.grid_columns + 1;
  return Math.max(1, next - landmark.column);
}

function physicalLayout(room) {
  const layout = room.layout;
  if (layout?.kind !== "floorplan" || !(layout.width > 0) || !(layout.height > 0)) return null;
  if (!room.seats.every((seat) => {
    const box = layout.seats?.[seat.label];
    return box && [box.x, box.y, box.width, box.height].every(Number.isFinite) && box.width > 0 && box.height > 0;
  })) return null;
  return layout;
}

function floorplanFeatures(layout, escape) {
  const features = (layout.features || []).map((item) => {
    if (item.kind === "path" && typeof item.d === "string") {
      const role = item.role === "label" ? " seat-plan-label" : item.role === "table" ? " seat-plan-table" : "";
      return `<path class="seat-plan-wall${role}" d="${escape(item.d)}"/>`;
    }
    if (item.kind === "rect" && [item.x, item.y, item.width, item.height].every(Number.isFinite)) {
      const rotation = Number.isFinite(item.rotation) ? item.rotation : 0;
      return `<rect class="seat-plan-wall${item.role === "legend" ? " seat-plan-legend" : ""}" x="${item.x}" y="${item.y}" width="${item.width}" height="${item.height}" transform="rotate(${rotation} ${item.x + item.width / 2} ${item.y + item.height / 2})"/>`;
    }
    if (item.kind === "text" && [item.x, item.y].every(Number.isFinite)) {
      const size = Number.isFinite(item.font_size) ? item.font_size : 12;
      const rotation = Number.isFinite(item.rotation) ? item.rotation : 0;
      return `<text class="seat-plan-caption" x="${item.x}" y="${item.y}" font-size="${size}" transform="rotate(${rotation} ${item.x} ${item.y})" text-anchor="middle">${escape(item.text || "")}</text>`;
    }
    return "";
  }).join("");
  return `<svg class="seat-plan-drawing" viewBox="0 0 ${layout.width} ${layout.height}" aria-hidden="true">${features}</svg>`;
}

const ZOOM_TIP = "Pinch on your trackpad or hold Ctrl and scroll over the chart to zoom.";
// Spreadsheet plans open fitted when that keeps seats readable (at least 70%); otherwise at actual size with a cue.
const FIT_READABLE = 0.7;

function planFrame(markup, label, tables = "") {
  // The pinch tip is the controls' title. `.seat-plan-zoom-hint` now marks the "room continues" cue, shown only while the
  // plan is wider than the panel at the chosen zoom.
  return `<div class="seat-plan-controls" role="group" aria-label="Chart zoom" title="${ZOOM_TIP}"><span class="seat-plan-caption-label">Room plan</span><span class="seat-plan-control-gap"></span><button type="button" class="text-button" data-plan-action="fit" aria-pressed="false">Fit room</button><button type="button" class="text-button" data-plan-action="out" aria-label="Zoom out">−</button><output class="seat-plan-zoom" aria-live="polite">100%</output><button type="button" class="text-button" data-plan-action="in" aria-label="Zoom in">+</button><button type="button" class="text-button" data-plan-action="actual" title="Show seats at readable size (100%)">Actual size</button><button type="button" class="text-button" data-plan-full aria-pressed="false" title="Use the whole window for the plan; Escape returns">Full width</button></div><p class="seat-plan-zoom-hint seat-plan-cue" hidden><span>Room continues →</span><button type="button" class="text-button" data-plan-cue-fit>Show whole room</button></p>${tables}<div class="seat-plan-stage" role="region" aria-label="${label}">${markup}</div>`;
}

// Full width: the plan's panel takes the window (browser memory only). Escape, navigation or the button again return.
function setFullWidth(on) {
  document.body.classList.toggle("seat-plan-full", on);
  document.querySelectorAll("[data-plan-full]").forEach((button) => button.setAttribute("aria-pressed", String(on)));
}
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !event.defaultPrevented && document.body.classList.contains("seat-plan-full") && !document.querySelector("dialog[open]")) {
    setFullWidth(false);
  }
});
window.addEventListener("hashchange", () => setFullWidth(false));

// seatButton(seat, mini, attributes) returns the markup for one seat; `attributes`
// carries the grid placement for single seats and is empty inside a table box.
// tableTitle(group, size) returns the markup for a table's title element.
export function renderSeatGrid(room, { escape, seatButton, tableTitle, tableControls = false, label = "Seating chart" }) {
  const layout = physicalLayout(room);
  if (layout) {
    const groups = new Map();
    if (tableControls) room.seats.filter((seat) => seat.group).forEach((seat) => groups.set(seat.group, (groups.get(seat.group) || 0) + 1));
    const tables = groups.size ? `<details class="seat-plan-groups"><summary>Select a table · ${groups.size} tables</summary><div role="group" aria-label="Table selections">${[...groups].map(([group, size]) => tableTitle(group, size, true)).join("")}</div></details>` : "";
    return planFrame(`<div class="seat-grid seat-grid-floorplan" role="group" aria-label="${escape(label)}">${floorplanFeatures(layout, escape)}${room.seats.map((seat) => seatButton(seat, false, "")).join("")}</div>`, escape(label), tables);
  }
  const groups = new Map();
  const singles = [];
  room.seats.forEach((seat) => {
    if (seat.group) {
      if (!groups.has(seat.group)) groups.set(seat.group, []);
      groups.get(seat.group).push(seat);
    } else singles.push(seat);
  });
  const landmarks = room.landmarks.map((item) => {
    const rows = item.row_span || 1;
    if (item.kind === "row") return `<span class="seat-rowlabel"${geometry(item)} title="Row ${escape(item.text)}">${escape(item.text)}</span>`;
    const vertical = rows >= 3 && (item.column_span || 1) === 1;
    return `<span class="seat-landmark${vertical ? " vertical" : ""}" data-row="${item.row}" data-column="${item.column}" data-rowspan="${rows}" data-span="${landmarkSpan(room, item)}" title="${escape(item.text)}">${escape(item.text)}</span>`;
  });
  const tables = [...groups].map(([group, seats]) => {
    const first = seats[0];
    const size = first.group_size || seats.length;
    return `<div class="seat-table"${geometry(first)}>${tableTitle(group, size)}<div class="seat-table-seats">${seats.map((seat) => seatButton(seat, true, "")).join("")}</div></div>`;
  });
  return planFrame(`<div class="seat-grid" role="group" aria-label="${escape(label)}">${landmarks.join("")}${tables.join("")}${singles.map((seat) => seatButton(seat, false, geometry(seat))).join("")}</div>`, escape(label));
}

// The page's Content Security Policy forbids inline style attributes, so grid
// placement is applied through the CSSOM after the markup is rendered.
export function placeSeatGrid(root, room) {
  const grid = root.querySelector(".seat-grid");
  if (!grid) return;
  const layout = physicalLayout(room);
  if (layout) {
    const widths = room.seats.map((seat) => layout.seats[seat.label].width).sort((a, b) => a - b);
    const unit = parseFloat(getComputedStyle(grid).getPropertyValue("--seat")) / (widths[Math.floor(widths.length / 2)] || 1);
    grid.style.width = `${layout.width * unit}px`;
    grid.style.height = `${layout.height * unit}px`;
    grid.style.setProperty("--plan-width", `${layout.width * unit}px`);  // the print stylesheet scales the plan to the page from this
    const seats = new Map(room.seats.map((seat) => [seat.id, seat]));
    grid.querySelectorAll("[data-seat]").forEach((node) => {
      const box = layout.seats[seats.get(node.dataset.seat).label];
      node.style.left = `${box.x * unit}px`;
      node.style.top = `${box.y * unit}px`;
      node.style.width = `${box.width * unit}px`;
      node.style.height = `${box.height * unit}px`;
      if (Number.isFinite(box.rotation)) node.style.transform = `rotate(${box.rotation}deg)`;
    });
  } else {
    grid.style.gridTemplateColumns = `repeat(${room.grid_columns}, var(--seat))`;
    grid.style.gridTemplateRows = `repeat(${room.grid_rows}, minmax(var(--seat), auto))`;
    // The print stylesheet re-flows the same grid to the page width from these counts.
    grid.style.setProperty("--columns", String(room.grid_columns));
    grid.style.setProperty("--rows", String(room.grid_rows));
    grid.querySelectorAll("[data-row]").forEach((node) => {
      const rows = Number(node.dataset.rowspan || 1);
      const columns = Number(node.dataset.span || 1);
      node.style.gridRow = rows > 1 ? `${node.dataset.row} / span ${rows}` : node.dataset.row;
      node.style.gridColumn = columns > 1 ? `${node.dataset.column} / span ${columns}` : node.dataset.column;
    });
  }
  const holder = grid.closest(".seat-chart");
  const stage = grid.parentElement;
  const controls = holder.querySelector(".seat-plan-controls");
  const key = `${room.id || room.name}:${Boolean(grid.closest(".seating-view"))}`;
  const view = views.get(key) || { fit: Boolean(layout), zoom: 1, auto: !layout };
  views.set(key, view);
  const cue = holder.querySelector(".seat-plan-cue");
  function update() {
    if (window.matchMedia("print").matches) return;
    const width = grid.offsetWidth, height = grid.offsetHeight;
    // Navigation hides an existing page without removing its DOM. Keep the
    // saved zoom until its chart is visible and measurable again.
    if (!width || !height || !holder.clientWidth) return;
    const available = holder.clientWidth - parseFloat(getComputedStyle(holder).paddingLeft) - parseFloat(getComputedStyle(holder).paddingRight);
    if (view.auto) {
      // First sight of a spreadsheet plan: fit it when the fitted seats stay readable.
      view.auto = false;
      view.fit = available / width >= FIT_READABLE;
    }
    const scale = view.fit ? Math.min(1, Math.max(MIN_ZOOM, available / width)) : view.zoom;
    view.zoom = scale;
    grid.style.transform = `scale(${scale})`;
    if (layout) {
      // Keep thin architectural lines and seat numbers visible in the overview;
      // 100% still measures a chair at the normal room/exam button size.
      grid.style.setProperty("--plan-line-scale", String(Math.max(1, 0.85 / scale)));
      grid.style.setProperty("--plan-seat-border", `${Math.max(1.5, 0.8 / scale)}px`);
      grid.style.setProperty("--plan-seat-font", `${Math.max(12, Math.min(20, 5 / scale))}px`);
    }
    stage.style.width = `${width * scale}px`;
    stage.style.height = `${height * scale}px`;
    controls.querySelector("output").textContent = `${Math.round(scale * 100)}%`;
    controls.querySelector('[data-plan-action="fit"]').setAttribute("aria-pressed", String(view.fit));
    controls.querySelector('[data-plan-action="in"]').disabled = scale >= MAX_ZOOM;
    controls.querySelector('[data-plan-action="out"]').disabled = scale <= MIN_ZOOM;
    if (cue) cue.hidden = view.fit || width * scale <= available + 1;
  }
  const full = controls.querySelector("[data-plan-full]");
  if (full) {
    full.setAttribute("aria-pressed", String(document.body.classList.contains("seat-plan-full")));
    full.onclick = () => setFullWidth(!document.body.classList.contains("seat-plan-full"));
  }
  const cueFit = cue?.querySelector("[data-plan-cue-fit]");
  if (cueFit) cueFit.onclick = () => { view.fit = true; view.auto = false; update(); holder.scrollLeft = 0; };
  controls.querySelectorAll("[data-plan-action]").forEach((button) => {
    button.onclick = () => {
      const action = button.dataset.planAction;
      view.auto = false;
      view.fit = action === "fit";
      if (action === "actual") view.zoom = 1;
      if (action === "in" || action === "out") view.zoom = Math.max(MIN_ZOOM, Math.min(MAX_ZOOM, view.zoom * (action === "in" ? 1.25 : 0.8)));
      update();
      if (view.fit) holder.scrollLeft = 0;
    };
  });
  for (const plan of activePlans) if (!plan.grid.isConnected || plan.grid === grid) {
    plan.dispose();
    activePlans.delete(plan);
  }
  const events = new AbortController();
  const listenerOptions = { passive: false, signal: events.signal };
  let gesture = null;
  const chartTarget = (event) => !event.target.closest?.(".seat-plan-controls, .seat-plan-groups, .seat-plan-zoom-hint")
    && !window.matchMedia("print").matches && holder.clientWidth && grid.offsetWidth;

  function zoomAt(scale, clientX, clientY) {
    const next = Math.max(MIN_ZOOM, Math.min(MAX_ZOOM, scale));
    if (!Number.isFinite(next) || next === view.zoom) return;
    const before = grid.getBoundingClientRect();
    const x = (clientX - before.left) / view.zoom;
    const y = (clientY - before.top) / view.zoom;
    view.fit = false;
    view.auto = false;
    view.zoom = next;
    update();
    // Hold the room point under the pointer still. The chart scrolls sideways;
    // tall charts grow with the page, so the remaining vertical correction
    // belongs to a scrollable ancestor or the document rather than the grid.
    const shift = () => {
      const box = grid.getBoundingClientRect();
      return [box.left + x * next - clientX, box.top + y * next - clientY];
    };
    for (let parent = holder; parent && parent !== document.body; parent = parent.parentElement) {
      const [dx, dy] = shift();
      const style = getComputedStyle(parent);
      if (/(auto|scroll)/.test(style.overflowX)) parent.scrollLeft += dx;
      if (/(auto|scroll)/.test(style.overflowY)) parent.scrollTop += dy;
    }
    const [dx, dy] = shift();
    if (Math.abs(dx) > 0.5 || Math.abs(dy) > 0.5) window.scrollBy(dx, dy);
  }

  holder.addEventListener("wheel", (event) => {
    // Chromium and Firefox expose trackpad pinch as Ctrl+wheel. Ordinary wheel
    // and two-finger scrolling retain the browser's scrolling behavior.
    if (!event.ctrlKey || !chartTarget(event) || !event.cancelable) return;
    event.preventDefault();
    event.stopPropagation();
    if (gesture) return;  // Safari may also emit wheel events during its native gesture.
    const unit = event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? holder.clientHeight : 1;
    const delta = Math.max(-240, Math.min(240, event.deltaY * unit));
    zoomAt(view.zoom * Math.exp(-delta * 0.002), event.clientX, event.clientY);
  }, listenerOptions);

  // Safari on macOS supplies GestureEvents instead of Ctrl+wheel for a pinch.
  holder.addEventListener("gesturestart", (event) => {
    if (!chartTarget(event)) return;
    event.preventDefault();
    gesture = { zoom: view.zoom, x: event.clientX, y: event.clientY };
  }, listenerOptions);
  holder.addEventListener("gesturechange", (event) => {
    if (!gesture) return;
    event.preventDefault();
    if (Number.isFinite(event.scale) && event.scale > 0) {
      zoomAt(gesture.zoom * event.scale, gesture.x, gesture.y);
    }
  }, listenerOptions);
  holder.addEventListener("gestureend", (event) => {
    if (!gesture) return;
    event.preventDefault();
    gesture = null;
  }, listenerOptions);
  const observer = new ResizeObserver(update);
  observer.observe(holder);
  activePlans.add({ grid, update, dispose: () => { observer.disconnect(); events.abort(); } });
  update();
}
