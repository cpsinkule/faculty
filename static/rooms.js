import { conditionsOf, renderSeatGrid, placeSeatGrid, seatFrame } from "./seat-grid.js";
import { disclosureMarkup, emptyMarkup, filterChipsMarkup, setBusy, runFormAction, dialogSession, scrollBehavior } from "./ui.js";
import { buildingOf, recordedTime, roomStatus, seatsNamed } from "./seating-state.js";


const STATUS_FILTERS = [["all", "All"], ["ready", "Ready"], ["review", "Needs review"], ["empty", "No assignable seats"]];
const SORTS = { name: "Name", usable: "Most usable seats", spaced: "Most spaced seats" };

export function createRooms({ api, icon, escape, toast, showDialog, dialogHeader, header, getRooms, refresh }) {
  let route = "";
  let room = null;
  let generation = 0;
  let anchor = null;
  // Browser memory only: the list's search, status filter, building and sort.
  let roomSearch = "";
  let roomFilter = "all";
  let roomBuilding = "";
  let roomSort = "name";
  let listRoute = "";
  let usedBy = null;
  const selection = new Set();

  function roomId() {
    const target = location.hash.slice(1).split("?")[0];
    const match = target.match(/^rooms\/(.+)$/);
    try {
      return match ? decodeURIComponent(match[1]) : "";
    } catch {
      return match[1];
    }
  }

  function roomHref(item) {
    return `#rooms/${encodeURIComponent(item.id)}`;
  }

  function when(value) {
    return recordedTime(value);
  }

  async function perform(form, operation) {
    const active = dialogSession(form, document.querySelector("#dialog"));
    return runFormAction(form, async () => { if (active()) await operation(active); });
  }

  function card(item) {
    // Only conditions a room actually has are listed; its readiness is one chip.
    const status = roomStatus(item);
    const facts = [`<span><strong>${item.usable_count}</strong> usable of ${item.seat_count}</span>`,
      item.broken_count ? `<span><strong>${item.broken_count}</strong> broken</span>` : "",
      item.blocked_count ? `<span><strong>${item.blocked_count}</strong> blocked</span>` : "",
      item.left_handed_count ? `<span><strong>${item.left_handed_count}</strong> left-handed</span>` : ""].join("");
    const chip = status === "review" ? '<span class="chip room-card-status" data-tone="warning">Needs review</span>' : status === "empty" ? '<span class="chip room-card-status" data-tone="neutral">No assignable seats</span>' : "";
    return `<a class="room-card" href="${roomHref(item)}" aria-label="Open ${escape(item.name)}"><span class="room-card-top"><span class="room-card-icon">${icon("seat")}</span>${chip}</span><h3>${escape(item.name)}</h3>${item.notes ? `<p class="supporting-copy">${escape(item.notes)}</p>` : ""}<div class="room-facts">${facts}</div></a>`;
  }

  function listOptions(rooms) {
    const counts = { all: rooms.length, ready: 0, review: 0, empty: 0 };
    rooms.forEach((item) => { counts[roomStatus(item)] += 1; });
    return STATUS_FILTERS.map(([value, label]) => ({ value, label, count: counts[value] }));
  }

  function renderList(root) {
    const rooms = getRooms();
    const review = rooms.filter((item) => roomStatus(item) === "review").length;
    header?.set({ route: location.hash, subtitle: rooms.length ? [`${rooms.length} room${rooms.length === 1 ? "" : "s"}`, review ? `${review} need${review === 1 ? "s" : ""} review` : ""].filter(Boolean).join(" · ") : "See the room before you seat the class." });
    // #rooms?filter=review (from the New seating chart dialog) opens on that filter.
    if (listRoute !== location.hash) {
      listRoute = location.hash;
      const wanted = new URLSearchParams(location.hash.split("?")[1] || "").get("filter");
      // The address decides: "All rooms" (#rooms) after a filtered visit shows every room again.
      roomFilter = STATUS_FILTERS.some(([value]) => value === wanted) ? String(wanted) : "all";
    }
    if (!rooms.length) {
      root.innerHTML = emptyMarkup({ icon: "seat", title: "No rooms yet.", body: "Upload a room's seating chart to seat exams there.", action: { label: "Add a room", data: { action: "add-room" } } });
      return;
    }
    const buildings = [...new Set(rooms.map((item) => buildingOf(item.name)).filter(Boolean))].sort();
    if (roomBuilding && !buildings.includes(roomBuilding)) roomBuilding = "";
    // A background refresh keeps the toolbar (and whatever is focused in it); only the counts and results change.
    const signature = `${buildings.join("|")}`;
    if (root.querySelector(".rooms-toolbar")?.dataset.signature !== signature) {
      root.innerHTML = `<div class="toolbar rooms-toolbar rooms-search" data-signature="${escape(signature)}"><label class="field rooms-search-field">Search rooms<input id="room-search" type="search" data-page-search value="${escape(roomSearch)}" placeholder="Room name or notes" autocomplete="off" aria-controls="room-results"></label>${buildings.length > 1 ? `<label class="field">Building<select id="room-building" aria-controls="room-results"><option value="">All buildings</option>${buildings.map((name) => `<option value="${escape(name)}"${name === roomBuilding ? " selected" : ""}>${escape(name)}</option>`).join("")}</select></label>` : ""}<label class="field">Sort<select id="room-sort" aria-controls="room-results">${Object.entries(SORTS).map(([value, label]) => `<option value="${value}"${value === roomSort ? " selected" : ""}>${label}</option>`).join("")}</select></label><p id="room-result-count" class="toolbar-count" role="status"></p></div><div class="rooms-status-host"></div><div id="room-results"></div>`;
      const input = root.querySelector("#room-search");
      input.oninput = () => { roomSearch = input.value; filterRooms(root); };
      root.querySelector("#room-building")?.addEventListener("change", (event) => { roomBuilding = event.target.value; filterRooms(root); });
      root.querySelector("#room-sort").addEventListener("change", (event) => { roomSort = event.target.value; filterRooms(root); });
      root.querySelector(".rooms-status-host").addEventListener("click", (event) => {
        const chip = event.target.closest("[data-filter]");
        if (!chip) return;
        roomFilter = chip.dataset.filter;
        // Keep the address in step so the filter survives a reload or can be linked.
        const query = roomFilter === "all" ? "" : `?filter=${roomFilter}`;
        history.replaceState(history.state, "", `#rooms${query}`);
        listRoute = location.hash;
        filterRooms(root);
      });
      if (!root.dataset.clearFiltersBound) root.addEventListener("click", (event) => {
        if (!event.target.closest("[data-clear-room-filters]")) return;
        roomSearch = ""; roomFilter = "all"; roomBuilding = "";
        const input = root.querySelector("#room-search");
        if (input) input.value = "";
        const select = root.querySelector("#room-building");
        if (select) select.value = "";
        history.replaceState(history.state, "", "#rooms");
        listRoute = location.hash;
        filterRooms(root);
      });
      root.dataset.clearFiltersBound = "1";
    }
    filterRooms(root);
  }

  // The markup last written into the list's chip and result hosts: a poll with unchanged rooms rewrites nothing, so focus and
  // hover on a room card survive it. Keyed by element, so hosts a toolbar rebuild makes are always written.
  const written = new WeakMap();
  const put = (node, markup) => { if (written.get(node) !== markup) { node.innerHTML = markup; written.set(node, markup); } };

  function filterRooms(root) {
    const rooms = getRooms();
    const words = roomSearch.trim().toLowerCase().split(/\s+/).filter(Boolean);
    const matches = rooms.filter((item) => {
      const text = `${item.name} ${item.notes || ""}`.toLowerCase();
      return words.every((word) => text.includes(word)) && (roomFilter === "all" || roomStatus(item) === roomFilter) && (!roomBuilding || buildingOf(item.name) === roomBuilding);
    });
    const by = { usable: (a, b) => b.usable_count - a.usable_count, spaced: (a, b) => (b.spaced_count || 0) - (a.spaced_count || 0) }[roomSort];
    matches.sort((a, b) => (by ? by(a, b) : 0) || String(a.name).localeCompare(String(b.name), undefined, { numeric: true }));
    put(root.querySelector(".rooms-status-host"), filterChipsMarkup("room-status", listOptions(rooms), roomFilter, { label: "Room readiness" }));
    const count = root.querySelector("#room-result-count"), countText = `Showing ${matches.length} of ${rooms.length} room${rooms.length === 1 ? "" : "s"}`;
    if (count.textContent !== countText) count.textContent = countText;
    const filtered = roomFilter !== "all" || roomBuilding;
    put(root.querySelector("#room-results"), matches.length
      ? `<div class="rooms-grid" aria-label="Rooms">${matches.map(card).join("")}</div>`
      : `<div class="room-search-empty">${emptyMarkup({ icon: "search", title: "No rooms match your search.", body: words.length ? "Try a room code or a word from its notes." : "No room has this status.", kind: "filtered", action: words.length || filtered ? { label: "Clear filters", data: { clearRoomFilters: "" } } : null })}</div>`);
  }

  function seatButton(seat, mini, attributes) {
    const conditions = conditionsOf(seat);
    const detail = [seat.label, ...conditions, seat.notes].filter(Boolean).join(" · ");
    return `<button type="button" class="seat${mini ? " seat-mini" : ""} ${conditions.join(" ")}" data-seat="${seat.id}"${attributes} aria-pressed="${selection.has(seat.id)}" aria-label="Seat ${escape(seat.label)}${conditions.map((item) => `, ${item}`).join("")}${seat.notes ? `, ${escape(seat.notes)}` : ""}" title="${escape(detail)}">${escape(seat.display || seat.label)}${seatFrame}</button>`;
  }

  function tableTitle(group, size, full = false) {
    return `<button type="button" class="seat-table-title" data-group="${escape(group)}" aria-label="Select all seats at ${escape(group)}" title="${escape(group)} · ${size} seats">${escape(full ? group : group.replace(/^\S+\s+/, ""))}</button>`;
  }

  function chart() {
    return renderSeatGrid(room, { escape, seatButton, tableTitle, tableControls: true });
  }

  function place(root) {
    placeSeatGrid(root, room);
  }

  function stat(value, label, className = "") {
    // Zero counts are muted: they are facts, not warnings.
    return `<div class="room-stat${className ? ` ${className}` : ""}${value ? "" : " is-zero"}"><strong>${value}</strong>${label}</div>`;
  }

  function reviewMarkup() {
    const reasons = room.review_required || [];
    if (!reasons.length) return "";
    const items = reasons.map((reason, index) => {
      const seats = seatsNamed(reason, room.seats);
      return `<li>${escape(reason)}${seats.length ? ` <button type="button" class="text-button" data-review-seats="${index}">${seats.length > 4 ? `Show ${seats.length} seats` : `Show seat${seats.length === 1 ? "" : "s"} (${seats.map((seat) => escape(seat.label)).join(", ")})`}</button>` : ""}</li>`;
    }).join("");
    return `<section class="room-review" role="alert"><strong>${reasons.length === 1 ? "1 thing" : `${reasons.length} things`} to check before seating an exam here</strong><ul>${items}</ul><button type="button" class="text-button" data-room-review>Confirm the room is ready…</button></section>`;
  }

  function usedByMarkup() {
    // Exam charts in this room (GET /api/seating, read when the room opens); each links to its chart.
    if (!usedBy) return "";
    const charts = usedBy.filter((item) => item.room_id === room.id);
    return `<section class="course-panel room-used-by" aria-labelledby="room-used-by-title"><h3 id="room-used-by-title">Used by exam charts</h3>${charts.length ? `<ul class="room-used-by-list">${charts.map((item) => `<li><a class="link-arrow" href="#seating/${encodeURIComponent(item.id)}">${escape(item.title)} · ${escape(item.code)} · ${escape(item.term_label)}</a> <span class="room-used-by-scope">Section${item.sections.length === 1 ? "" : "s"} ${item.sections.join(", ")} · ${item.seated_count} seated</span>${item.check && !item.check.ok ? ' <span class="chip" data-tone="warning">Needs attention</span>' : ""}</li>`).join("")}</ul>` : '<p class="field-hint">No exam chart uses this room yet.</p>'}</section>`;
  }

  async function loadUsedBy(root) {
    try {
      usedBy = await api("/api/seating");
    } catch {
      usedBy = null;
      return null;
    }
    const host = root.querySelector(".room-used-by-host");
    if (host && root.isConnected && room) host.innerHTML = usedByMarkup();
    return usedBy;
  }

  function importsMarkup() {
    return (room.imports || []).map((item) => {
      const notes = item.warnings || [];
      return `<p><strong>${escape(when(item.imported_at))}</strong> · ${escape(item.filename)} · ${item.seat_count} seats (${item.added} added, ${item.kept} kept, ${item.removed} removed${item.chart_broken ? `, ${item.chart_broken} marked broken by the chart` : ""})</p>${notes.length ? disclosureMarkup({ label: `Notes (${notes.length})`, body: `<ul>${notes.map((note) => `<li class="room-import-note">${escape(note)}</li>`).join("")}</ul>`, className: "room-import-notes" }) : ""}`;
    }).join("") || "<p>No imports recorded.</p>";
  }

  function renderDetail(root) {
    const source = room.layout?.kind === "floorplan" ? "PDF floor plan" : `sheet “${escape(room.sheet)}”`;
    // One settings entry: the header's Room settings. A room that must be confirmed before an exam is seated in it makes that
    // the one primary (goal first; the confirmation is still Room settings' own tick and Save), with Room settings beside it.
    const review = Boolean(room.review_required?.length);
    header?.set({
      route: location.hash, title: room.name, subtitle: room.notes || "",
      crumbs: [{ label: "Home", href: "#overview" }, { label: "Rooms", href: "#rooms" }, { label: room.name }],
      action: review ? { label: "Confirm the room is ready…", icon: "check", run: () => openSettings({ focusReview: true }) }
        : { label: "Room settings", icon: "edit", run: () => openSettings() },
      secondary: review ? [{ label: "Room settings", run: () => openSettings() }] : [],
    });
    const tips = `<p>Blocked seats are physically usable but should not be assigned. The usable count excludes both broken and blocked seats.</p><p><strong>Select faster:</strong> Shift-click to select a block from the last seat you clicked, or click a table’s title to select every seat at that table.</p>`;
    root.innerHTML = `<nav class="room-breadcrumbs" aria-label="Rooms navigation"><a class="text-button" href="#rooms">${icon("arrow-left")}All rooms</a></nav>
      <section class="room-summary" aria-label="Room summary">${stat(room.seat_count, "seats")}${stat(room.usable_count, "usable")}${stat(room.broken_count, "broken", "broken")}${stat(room.blocked_count, "blocked", "blocked")}${stat(room.left_handed_count, "left-handed", "left")}<div class="room-source">Chart from <strong>${escape(room.source_filename)}</strong> · ${source} · imported ${escape(when(room.imported_at))}<br><button class="text-button" data-room-action="replace">${icon("refresh")}Replace chart file</button></div></section>
      ${reviewMarkup()}
      <div class="room-used-by-host">${usedByMarkup()}</div>
      <section class="course-panel room-chart-panel" aria-labelledby="room-chart-title"><h3 id="room-chart-title">Room plan</h3><p class="section-lead">Select seats, then mark the conditions exam seating should respect.</p><div class="seat-toolbar" role="toolbar" aria-label="Seat conditions"><span id="seat-selection-count" aria-live="polite"></span><button type="button" class="button" data-seat-action="broken"></button><button type="button" class="button" data-seat-action="left_handed"></button><button type="button" class="button" data-seat-action="blocked"></button><button type="button" class="button" data-seat-action="clear">Clear conditions</button><span class="seat-toolbar-gap"></span><button type="button" class="text-button" data-seat-action="select-all">Select all</button><button type="button" class="text-button" data-seat-action="select-none">Clear selection</button></div>${disclosureMarkup({ label: "Tips for selecting seats", body: tips, key: "rooms:tips", className: "room-tips" })}<div class="seat-legend" aria-label="Legend"><span><i></i>Seat</span><span><i class="broken"></i>Broken</span><span><i class="blocked"></i>Blocked · do not assign</span><span><i class="left-handed"></i>Left-handed</span><span><i class="selected"></i>Selected</span></div><div class="seat-chart">${chart()}</div></section>
      <section class="course-panel"><h3>Chart imports</h3><div class="room-imports detail-group">${importsMarkup()}</div></section>`;
    place(root);
    root.querySelector('[data-room-action="replace"]').onclick = openReplace;
    root.querySelector("[data-room-review]")?.addEventListener("click", () => openSettings({ focusReview: true }));
    root.querySelectorAll("[data-review-seats]").forEach((button) => {
      button.onclick = () => {
        // Select exactly the seats the reason names, and bring the first into view.
        const seats = seatsNamed(room.review_required[Number(button.dataset.reviewSeats)], room.seats);
        selection.clear();
        seats.forEach((seat) => selection.add(seat.id));
        anchor = seats[0]?.id || anchor;
        updateSelection(root);
        root.querySelector(`.seat-chart [data-seat="${CSS.escape(seats[0]?.id || "")}"]`)?.scrollIntoView({ block: "center", inline: "center", behavior: scrollBehavior() });
      };
    });
    if (!usedBy) void loadUsedBy(root);
    root.querySelector(".seat-chart").addEventListener("click", (event) => {
      const title = event.target.closest("[data-group]");
      if (title) {
        const members = room.seats.filter((seat) => seat.group === title.dataset.group);
        const every = members.every((seat) => selection.has(seat.id));
        members.forEach((seat) => (every ? selection.delete(seat.id) : selection.add(seat.id)));
        anchor = members[0]?.id || anchor;
        updateSelection(root);
        return;
      }
      const button = event.target.closest("[data-seat]");
      if (!button) return;
      const id = button.dataset.seat;
      const target = room.seats.find((seat) => seat.id === id);
      const origin = room.seats.find((seat) => seat.id === anchor);
      if (event.shiftKey && origin && target) {
        const rows = [origin.row, target.row].sort((a, b) => a - b);
        const columns = [origin.column, target.column].sort((a, b) => a - b);
        room.seats.filter((seat) => seat.row >= rows[0] && seat.row <= rows[1] && seat.column >= columns[0] && seat.column <= columns[1])
          .forEach((seat) => selection.add(seat.id));
      } else if (selection.has(id)) selection.delete(id);
      else selection.add(id);
      anchor = id;
      updateSelection(root);
    });
    root.querySelectorAll("[data-seat-action]").forEach((button) => {
      button.onclick = () => seatAction(root, button.dataset.seatAction).catch((cause) => toast(cause.message, true));
    });
    updateSelection(root);
  }

  function updateSelection(root) {
    const present = new Set(room.seats.map((seat) => seat.id));
    [...selection].filter((id) => !present.has(id)).forEach((id) => selection.delete(id));
    root.querySelectorAll("[data-seat]").forEach((button) => {
      button.setAttribute("aria-pressed", String(selection.has(button.dataset.seat)));
    });
    const selected = room.seats.filter((seat) => selection.has(seat.id));
    const count = selected.length;
    root.querySelector("#seat-selection-count").textContent = count ? `${count} seat${count === 1 ? "" : "s"} selected` : "No seats selected";
    for (const [action, word] of [["broken", "broken"], ["left_handed", "left-handed"], ["blocked", "blocked"]]) {
      const every = count > 0 && selected.every((seat) => seat[action]);
      const button = root.querySelector(`[data-seat-action="${action}"]`);
      button.textContent = `${every ? "Unmark" : "Mark"} ${word}`;
      button.dataset.value = String(!every);
      button.disabled = count === 0;
    }
    root.querySelector('[data-seat-action="clear"]').disabled = !selected.some((seat) => seat.broken || seat.left_handed || seat.blocked);
    root.querySelector('[data-seat-action="select-none"]').disabled = count === 0;
    root.querySelector('[data-seat-action="select-all"]').disabled = count === room.seats.length;
  }

  async function seatAction(root, action) {
    if (action === "select-all") {
      room.seats.forEach((seat) => selection.add(seat.id));
      updateSelection(root);
      return;
    }
    if (action === "select-none") {
      selection.clear();
      updateSelection(root);
      return;
    }
    const seatIds = [...selection];
    if (!seatIds.length) return;
    const conditions = action === "clear"
      ? { broken: false, left_handed: false, blocked: false }
      : { [action]: root.querySelector(`[data-seat-action="${action}"]`).dataset.value === "true" };
    root.querySelectorAll("[data-seat-action]").forEach((button) => { setBusy(button, true); });
    const original = room;
    try {
      const saved = await api(`/api/rooms/${encodeURIComponent(original.id)}/seats`, "POST", { version: original.version, seat_ids: seatIds, ...conditions });
      if (roomId() !== original.id) { void refresh(); return; }
      room = saved;
    } finally {
      if (root.isConnected && room && roomId() === original.id) renderDetail(root);
    }
    const words = { broken: "broken", left_handed: "left-handed", blocked: "blocked" };
    const label = action === "clear" ? "Seat conditions cleared." : `${seatIds.length} seat${seatIds.length === 1 ? "" : "s"} ${conditions[action] ? "marked" : "unmarked"} ${words[action]}.`;
    toast(label);
    void refresh();
    void flagChartsAfterChange(root);
  }

  async function flagChartsAfterChange(root) {
    // A seat change can put a seated student on an unusable seat: say which exam charts now need attention.
    const before = new Map((usedBy || []).map((item) => [item.id, Boolean(item.check?.ok)]));
    const current = room;
    const after = await loadUsedBy(root);
    if (!after || room !== current) return;
    const flagged = after.filter((item) => item.room_id === current.id && item.check && !item.check.ok && before.get(item.id) !== false);
    if (!flagged.length) return;
    const first = flagged[0];
    // Name the cause only when the chart's own check says so (another change may have flagged it in the meantime).
    const unusable = flagged.every((item) => Number(item.check.counts?.seat_unusable) > 0);
    toast(`${flagged.length === 1 ? `${first.title} (${first.code} · ${first.term_label})` : `${flagged.length} exam charts in this room`} now need${flagged.length === 1 ? "s" : ""} attention${unusable ? ": a seated student is on a seat that can no longer be used" : ""}.`,
      { tone: "warning", persist: true, action: { label: "Open chart", run: () => { location.hash = `#seating/${encodeURIComponent(first.id)}`; } } });
  }

  async function render() {
    const root = document.querySelector("#rooms-section");
    const id = roomId();
    if (!id) {
      generation++;
      route = location.hash;
      room = null;
      renderList(root);
      return;
    }
    const summary = getRooms().find((item) => item.id === id);
    if (route === location.hash && room?.id === id && (!summary || summary.version === room.version)) return;
    if (room?.id !== id) {
      selection.clear();
      anchor = null;
      usedBy = null;
      root.innerHTML = '<p class="loading-state">Loading the seating chart…</p>';
    }
    route = location.hash;
    const current = ++generation;
    try {
      const loaded = await api(`/api/rooms/${encodeURIComponent(id)}`);
      if (current !== generation || root.hidden || roomId() !== id) return;
      room = loaded;
      renderDetail(root);
    } catch (cause) {
      if (current !== generation) return;
      room = { id, version: summary?.version, missing: true, seats: [], landmarks: [] };
      root.innerHTML = `<div class="empty-state">${icon("seat")}<h3>This room isn’t available.</h3><p>${escape(cause.message)}</p><a class="button" href="#rooms">All rooms</a></div>`;
    }
  }

  function uploadFields() {
    return `<label class="field">Seating chart file<input type="file" name="file" accept=".xlsx,.pdf,application/pdf,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" required aria-describedby="room-upload-hint"></label><p id="room-upload-hint" class="field-hint supporting-copy">Department Excel (.xlsx) chart or supported ILCB PDF floor plan, up to 10 MB.</p><div class="detail-group"><p class="supporting-copy">For workbooks, the first sheet supplies the plan: row letters and numbers identify seats, blank cells preserve aisles, and captions mark room landmarks. Paste values before uploading a workbook with formulas.</p></div><label class="field">Seat issues document<span class="field-optional">optional</span><input type="file" name="issues_file" accept=".docx,application/vnd.openxmlformats-officedocument.wordprocessingml.document"></label><p class="field-hint supporting-copy">Add the accompanying Word (.docx) document to bring in reported seat conditions. Issues without an exact seat location are kept for room review.</p><p class="field-hint supporting-copy">Broken and blocked seats are never used for exam seating; replacing a chart later keeps the conditions of seats whose labels match.</p>`;
  }

  function openAdd() {
    showDialog(`<form id="room-add">${dialogHeader("Add a room", "Upload a seating chart and any accompanying seat-issues document.")}<div class="dialog-body"><label class="field">Room name<input name="name" required maxlength="80" placeholder="e.g. BLOC 117" autofocus></label><label class="field">Notes<span class="field-optional">optional</span><input name="notes" maxlength="280" placeholder="e.g. Lecture hall with tablet arms"></label>${uploadFields()}<p class="form-error" role="alert" hidden></p></div><footer class="dialog-footer"><button type="button" class="button" data-action="close">Cancel</button><button type="submit" class="button primary">Add room</button></footer></form>`);
    const form = document.querySelector("#room-add");
    form.onsubmit = (event) => {
      event.preventDefault();
      perform(form, async (active) => {
        const created = await api("/api/rooms", "POST", new FormData(form));
        if (active()) {
          document.querySelector("#dialog").close();
          location.hash = roomHref(created);
        }
        toast(`${created.name} added with ${created.seat_count} seats.${created.warnings?.length ? ` ${created.warnings.length} chart note${created.warnings.length === 1 ? "" : "s"} listed under Chart imports.` : ""}`);
        await refresh(true);
      });
    };
  }

  function openReplace() {
    if (!room || room.missing) return;
    const current = room;
    showDialog(`<form id="room-chart">${dialogHeader("Replace seating chart", escape(current.name))}<div class="dialog-body">${uploadFields()}<p class="field-hint">Seats keep their broken, left-handed and blocked conditions when their labels match the new chart. Seats missing from the new chart are removed.</p><p class="form-error" role="alert" hidden></p></div><footer class="dialog-footer"><button type="button" class="button" data-action="close">Cancel</button><button type="submit" class="button primary">Replace chart</button></footer></form>`);
    const form = document.querySelector("#room-chart");
    form.onsubmit = (event) => {
      event.preventDefault();
      perform(form, async (active) => {
        const data = new FormData(form);
        data.append("version", String(current.version));
        const saved = await api(`/api/rooms/${encodeURIComponent(current.id)}/chart`, "POST", data);
        if (active()) document.querySelector("#dialog").close();
        toast(`Seating chart replaced: ${saved.seat_count} seats.${saved.warnings?.length ? ` ${saved.warnings.length} chart note${saved.warnings.length === 1 ? "" : "s"} listed under Chart imports.` : ""}`);
        const root = document.querySelector("#rooms-section");
        if (!root.hidden && roomId() === current.id) { room = saved; renderDetail(root); }
        await refresh(true);
      });
    };
  }

  function openSettings({ focusReview = false } = {}) {
    if (!room || room.missing) {
      toast("Open a room first.", true);
      return;
    }
    const current = room;
    showDialog(`<form id="room-settings">${dialogHeader("Room settings", escape(current.name))}<div class="dialog-body"><label class="field">Room name<input name="name" required maxlength="80" value="${escape(current.name)}" autofocus></label><label class="field">Notes<span class="field-optional">optional</span><textarea name="notes" maxlength="280">${escape(current.notes)}</textarea></label>${current.review_required?.length ? `<div class="room-review"><strong>Check this room before exam seating</strong><ul>${current.review_required.map((reason) => `<li>${escape(reason)}</li>`).join("")}</ul><label class="checkbox-field room-review-option"><input type="checkbox" name="resolve_review">I checked the seat labels and chair locations; this room is ready for exam seating</label></div>` : ""}<p class="form-error" role="alert" hidden></p></div><footer class="dialog-footer"><button type="button" class="danger-button" data-room-action="delete">${icon("trash")}Remove room</button><button type="button" class="button" data-action="close">Cancel</button><button type="submit" class="button primary">Save changes</button></footer></form>`);
    const form = document.querySelector("#room-settings");
    const resolve = form.elements.resolve_review;
    if (focusReview && resolve) {
      resolve.focus();
      resolve.scrollIntoView({ block: "center" });
    }
    form.onsubmit = (event) => {
      event.preventDefault();
      perform(form, async (active) => {
        const data = new FormData(form);
        const saved = await api(`/api/rooms/${encodeURIComponent(current.id)}`, "PATCH", { version: current.version, name: data.get("name"), notes: data.get("notes"), ...(data.get("resolve_review") ? { resolve_review: true } : {}) });
        if (active()) document.querySelector("#dialog").close();
        toast("Room settings saved.");
        const root = document.querySelector("#rooms-section");
        if (!root.hidden && roomId() === current.id) { room = saved; renderDetail(root); }
        await refresh(true);
      });
    };
    form.querySelector('[data-room-action="delete"]').onclick = () => {
      showDialog(`<form id="room-delete">${dialogHeader("Remove this room?", escape(current.name))}<div class="dialog-body"><p>The seating chart and its seat conditions will be removed from your workspace. You can add the room again by uploading its chart.</p><p class="form-error" role="alert" hidden></p></div><footer class="dialog-footer"><button type="button" class="button" data-action="close">Keep room</button><button type="submit" class="button danger">Remove room</button></footer></form>`);
      const confirmation = document.querySelector("#room-delete");
      confirmation.onsubmit = (event) => {
        event.preventDefault();
        perform(confirmation, async (active) => {
          await api(`/api/rooms/${encodeURIComponent(current.id)}`, "DELETE", { version: current.version });
          if (active()) document.querySelector("#dialog").close();
          if (roomId() === current.id) { room = null; location.hash = "rooms"; }
          toast("Room removed.");
          await refresh(true);
        });
      };
    };
  }

  return { render, openAdd, openSettings };
}
