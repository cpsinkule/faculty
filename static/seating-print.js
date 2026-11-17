import { savedExamVersionList } from "./seating-versions.js";
import { roomRosterMarkup } from "./seating-roster.js";
import { packetMarkup } from "./exam-day-packet.js";
import { captureDialogIntent, dialogSession, runFormAction } from "./ui.js";

/**
 * Ephemeral print controller for an existing term-owned chart. Printing reads the
 * current chart through the host; it creates no owner or saved artifact. All
 * preview revalidation, geometry and browser print cleanup stay in this module.
 */
export function createSeatingPrint({ api, escape, toast, showDialog, dialogHeader, getChart, isVersionView, setVersionView = null, chartId, scope, reloadChart = null }) {
  // Letter paper inside 10 mm margins is 980 × 741 px landscape or 741 × 979 px portrait; about 180 px of
  // title and count strip sit above the chart on the first page.
  const PAPER = { landscape: [980, 741], portrait: [741, 979] };
  const PRINT_HEADER = 180;
  const TILE_SEAT = 58;  // wide enough for a two-line name at the screen font
  const TILE_GAP = 3;
  const FLOOR_PRINT_SEAT = 64;
  const FLOOR_PRINT_HEADER = 62;
  let printSheet = null;
  let printTiles = null;
  let printSection = null;
  let printPacket = null;

  // `packet` adds the exam-day packet's portrait pages (seating.css `page: packet-portrait`). Chromium lets a later unnamed @page
  // rule override an earlier named one, so the named page is declared here, after the chart's own rule.
  function setPrintPaper(orientation, packet = false) {
    if (typeof CSSStyleSheet === "function" && "adoptedStyleSheets" in document) {
      try {
        printSheet = printSheet || new CSSStyleSheet();
        printSheet.replaceSync(`@page { size: letter ${orientation}; margin: 10mm; }${packet ? " @page packet-portrait { size: letter portrait; margin: 10mm; }" : ""}`);
        if (!document.adoptedStyleSheets.includes(printSheet)) document.adoptedStyleSheets = [...document.adoptedStyleSheets, printSheet];
      } catch {
        printSheet = null;
      }
    }
  }

  function versionListMarkup(current, list) {
    const counts = list.counts.map((item) => item.count);
    const imbalanced = !current.allow_unequal_versions && Math.max(...counts) - Math.min(...counts) > 1;
    const rows = list.rows.map((row) => {
      // Summarize the saved letters, including manual choices, not a newly
      // calculated alternating pattern. Include zeroes for unused versions.
      const totals = new Map(list.counts.map((item) => [item.version, 0]));
      row.students.forEach((student) => totals.set(student.version, (totals.get(student.version) || 0) + 1));
      const note = [...totals].map(([letter, count]) => `${escape(letter)}: ${count}`).join(" · ");
      return `<tbody><tr class="seating-version-row-summary"><th scope="rowgroup" colspan="5"><strong>${escape(row.label)}</strong> · ${row.students.length} student${row.students.length === 1 ? "" : "s"} · ${note}</th></tr>${row.students.map((student) => `<tr class="seating-version-student"><td>${escape(row.label)}</td><td>${escape(student.seat)}</td><td>${escape(student.name)}</td><td>${escape(String(student.section_number))}</td><td><strong>${student.version}</strong></td></tr>`).join("")}</tbody>`;
    }).join("");
    return `<header><h2>Exam version list · ${escape(current.title)}</h2><p>${escape(current.room_name)} · ${escape(scope(current))}</p>${current.exam_date ? `<p>${escape(current.exam_date)}${current.exam_time ? ` · ${escape(current.exam_time)}` : ""}</p>` : ""}<p class="seating-version-totals"><strong>${list.total} students</strong> · ${list.counts.map((item) => `${item.version}: ${item.count}`).join(" · ")}</p><p class="seating-version-note">Match each student’s label to the exam version below. Saved exam versions stay with their seats. Reprint after changing seating or versions.</p>${imbalanced ? '<p class="seating-version-warning"><strong>Review available exam copies:</strong> saved totals now differ by more than one.</p>' : ""}${list.conflicts?.length ? `<p class="seating-version-warning"><strong>Review same-version neighbors:</strong> ${list.conflicts.map((item) => `${escape(item.row)} · ${escape(item.seats.join(" / "))} (${escape(item.version)})`).join("; ")}</p>` : ""}</header><table class="seating-version-table"><thead><tr><th scope="col">Row</th><th scope="col">Seat</th><th scope="col">Student</th><th scope="col">Section</th><th scope="col">Exam version</th></tr></thead>${rows}</table>`;
  }

  async function openVersionPrint() {
    const intended = captureDialogIntent(document.querySelector("#dialog"));
    const chart = getChart();
    const id = chart.id;
    // Reuse the chart endpoint's locked, verified roster read. Opening this view
    // always obtains current seats, even in a tab that has been open for a while.
    let current = await api(`/api/seating/${encodeURIComponent(id)}`);
    if (chartId() !== id || !intended()) return;
    showDialog(`<form id="seating-version-print">${dialogHeader("Print exam version list", escape(`${current.title} · ${current.room_name}`))}<div class="dialog-body"><p class="field-hint">Print on Letter paper in portrait orientation at 100%.</p><p class="form-error" role="alert" hidden></p><div id="seating-version-preview" class="seating-version-preview" role="region" aria-label="Exam version list preview" tabindex="0"></div></div><footer class="dialog-footer"><button type="button" class="button" data-action="close">Cancel</button><button type="submit" class="button primary">Print</button></footer></form>`);
    const form = document.querySelector("#seating-version-print");
    const active = dialogSession(form, document.querySelector("#dialog"));
    const preview = form.querySelector("#seating-version-preview");
    const error = form.querySelector('[role="alert"]');
    const updatePreview = () => {
      try {
        const list = savedExamVersionList(current);
        preview.innerHTML = versionListMarkup(current, list);
        error.hidden = true;
        form.querySelector('[type="submit"]').disabled = false;
        return preview.innerHTML;
      } catch (cause) {
        preview.replaceChildren();
        error.textContent = cause.message;
        error.hidden = false;
        form.querySelector('[type="submit"]').disabled = true;
        return null;
      }
    };
    updatePreview();
    form.onsubmit = (event) => {
      event.preventDefault();
      const previous = preview.innerHTML;
      runFormAction(form, async () => {
        // If another tab changed the chart while the preview was open, show the
        // revised list and let the instructor print that exact preview.
        current = await api(`/api/seating/${encodeURIComponent(id)}`);
        if (!active() || chartId() !== id) return;
        const markup = updatePreview();
        if (!markup) return;
        if (markup !== previous) {
          error.textContent = "The seating, roster, or exam versions changed. Review the updated list, then click Print again.";
          error.hidden = false;
          return;
        }
        printDocument("seating-version-list", "print-version-list", markup);
      }).finally(() => {
        try {
          savedExamVersionList(current);
        } catch {
          form.querySelector('[type="submit"]').disabled = true;
        }
      });
    };
  }

  async function openRoomRosterPrint() {
    const intended = captureDialogIntent(document.querySelector("#dialog"));
    const chart = getChart();
    const id = chart.id;
    const endpoint = `/api/seating/${encodeURIComponent(id)}/room-roster`;
    let current = await api(endpoint);
    if (chartId() !== id || !intended()) return;
    showDialog(`<form id="seating-roster-print">${dialogHeader("Print room roster", escape(`${current.title} · ${current.room_name}`))}<div class="dialog-body"><p class="field-hint">Print on Letter paper in portrait orientation at 100%.</p><p class="form-error" role="alert" hidden></p><div id="seating-roster-preview" class="seating-roster-preview" role="region" aria-label="Room roster preview" tabindex="0"></div></div><footer class="dialog-footer"><button type="button" class="button" data-action="close">Cancel</button><button type="submit" class="button primary">Print</button></footer></form>`);
    const form = document.querySelector("#seating-roster-print");
    const active = dialogSession(form, document.querySelector("#dialog"));
    const preview = form.querySelector("#seating-roster-preview");
    preview.innerHTML = roomRosterMarkup(current, escape);
    form.onsubmit = (event) => {
      event.preventDefault();
      void runFormAction(form, async () => {
        const updated = await api(endpoint);
        if (!active() || chartId() !== id) return;
        const changed = updated.fingerprint !== current.fingerprint;
        current = updated;
        preview.innerHTML = roomRosterMarkup(current, escape);
        if (changed) {
          const error = form.querySelector('[role="alert"]');
          error.textContent = "The roster, seats, DR bookings, or response cards changed. Review the updated roster, then click Print again.";
          error.hidden = false;
          return;
        }
        printDocument("seating-room-roster", "print-room-roster", preview.innerHTML);
      });
    };
  }

  function gridFacts() {
    const chart = getChart();
    const grid = document.querySelector(".seating-view .seat-chart .seat-grid");
    if (!grid || !chart?.room) return null;
    const layout = chart.room.layout;
    const floorplan = grid.classList.contains("seat-grid-floorplan") && layout?.width > 0 && layout?.height > 0;
    const style = getComputedStyle(grid);
    const base = parseFloat(style.getPropertyValue("--seat")) || 92;
    const tracks = floorplan ? [] : style.gridTemplateRows.split(" ").map(parseFloat).filter(Number.isFinite);
    return { grid, floorplan, columns: floorplan ? layout.width : Math.max(1, chart.room.grid_columns), rows: floorplan ? layout.height : Math.max(1, chart.room.grid_rows),
             extra: tracks.reduce((sum, track) => sum + Math.max(0, track - base), 0), sticky: grid.querySelector('.seat-rowlabel[data-column="1"]') ? 1 : 0,
             planWidth: floorplan ? parseFloat(style.getPropertyValue("--plan-width")) || grid.offsetWidth : 0 };
  }

  function fitPlan(facts) {
    if (facts.floorplan) return floorPrintPlan("fit");
    // One page: the largest square seat that fits both page dimensions, in the better orientation.
    const seatIf = (width, height) => Math.min(width / facts.columns, ((height - PRINT_HEADER - facts.extra) * 0.95 - (facts.rows - 1) * 3) / facts.rows);
    const landscape = seatIf(...PAPER.landscape), portrait = seatIf(...PAPER.portrait);
    const wide = landscape > portrait * 1.05;
    return { mode: "fit", orientation: wide ? "landscape" : "portrait", seat: wide ? landscape : portrait, pages: 1 };
  }

  function tilePlan(facts) {
    if (facts.floorplan) return floorPrintPlan("tiles");
    // Several pages: seats stay readable and the room is cut into column bands, row labels on each.
    const unit = TILE_SEAT + TILE_GAP;
    const plans = Object.entries(PAPER).map(([orientation, [width, height]]) => {
      const perBand = Math.max(1, Math.floor((width + TILE_GAP) / unit) - facts.sticky);
      const bands = columnBands(facts, perBand);
      const rowsFirst = Math.max(1, Math.floor((height - PRINT_HEADER - facts.extra + TILE_GAP) / unit));
      const rowsNext = Math.max(1, Math.floor((height - facts.extra + TILE_GAP) / unit));
      const pages = 1 + Math.max(0, Math.ceil((facts.rows - rowsFirst) / rowsNext)) + (bands.length - 1) * Math.ceil(facts.rows / rowsNext);
      return { orientation, bands, pages };
    });
    plans.sort((a, b) => a.pages - b.pages || a.bands.length - b.bands.length || (a.orientation === "portrait" ? -1 : 1));
    return { mode: "tiles", ...plans[0] };
  }

  function floorPrintPlan(mode) {
    const chart = getChart();
    const versionView = isVersionView();
    // Print uses retained source coordinates, never the screen's zoom or stage
    // dimensions. Every chair is wholly assigned to one two-dimensional tile.
    const layout = chart.room.layout;
    const chairs = chart.room.seats.map((seat) => ({id: seat.id, ...layout.seats[seat.label]}));
    const widths = chairs.map((seat) => seat.width).sort((a, b) => a - b);
    const median = widths[Math.floor(widths.length / 2)] || 1;
    const plans = Object.entries(PAPER).map(([orientation, [pageWidth, pageHeight]]) => {
      const width = pageWidth - 4;
      const heading = document.createElement("p");
      heading.className = "seating-print-measure";
      Object.assign(heading.style, {position: "absolute", left: "-100000px", top: "0", visibility: "hidden", width: `${width}px`, whiteSpace: "pre-line", overflowWrap: "anywhere", fontSize: "11px", fontWeight: "600", lineHeight: "16px", margin: "0"});
      // Reserve the actual wrapped heading, including a conservative page
      // locator, so a long exam title or many sections never creates a blank
      // page or pushes the bottom chairs outside the printed page.
      heading.textContent = `${chart.title} · ${chart.room_name} · ${scope(chart)}\n${mode === "fit" ? `Whole room overview · ${versionView ? "exam versions and " : ""}seat labels` : "Page 2000 of 2000 · row 2000, column 2000 of the room plan · 2000 seats"}`;
      document.body.appendChild(heading);
      const header = Math.max(FLOOR_PRINT_HEADER, heading.offsetHeight + 14);
      heading.remove();
      const height = Math.max(100, pageHeight - header - 4);
      if (mode === "fit") {
        const scale = Math.min(width / layout.width, height / layout.height);
        return {mode, orientation, scale, width, height, pages: 1, seat: median * scale,
          tiles: [{x: 0, y: 0, row: 0, column: 0, seats: new Set(chairs.map((seat) => seat.id))}]};
      }
      const scale = FLOOR_PRINT_SEAT / median;
      const boxes = chairs.map((seat) => {
        const angle = (seat.rotation || 0) * Math.PI / 180;
        return {...seat, cx: (seat.x + seat.width / 2) * scale, cy: (seat.y + seat.height / 2) * scale,
          halfWidth: (Math.abs(Math.cos(angle)) * seat.width + Math.abs(Math.sin(angle)) * seat.height) * scale / 2,
          halfHeight: (Math.abs(Math.sin(angle)) * seat.width + Math.abs(Math.cos(angle)) * seat.height) * scale / 2};
      });
      const padX = Math.max(...boxes.map((seat) => Math.max(seat.halfWidth, seat.width * scale / 2))) + 5;
      const padY = Math.max(...boxes.map((seat) => Math.max(seat.halfHeight, seat.height * scale / 2))) + 5;
      const coreWidth = Math.max(1, width - 2 * padX), coreHeight = Math.max(1, height - 2 * padY);
      const minX = Math.min(...boxes.map((seat) => seat.cx)), minY = Math.min(...boxes.map((seat) => seat.cy));
      const tiles = new Map();
      for (const seat of boxes) {
        const column = Math.floor((seat.cx - minX) / coreWidth), row = Math.floor((seat.cy - minY) / coreHeight);
        const key = `${row}:${column}`;
        if (!tiles.has(key)) tiles.set(key, {column, row, x: minX + column * coreWidth - padX, y: minY + row * coreHeight - padY, seats: new Set()});
        tiles.get(key).seats.add(seat.id);
      }
      return {mode, orientation, scale, width, height, seat: FLOOR_PRINT_SEAT, pages: tiles.size,
        tiles: [...tiles.values()].sort((a, b) => a.row - b.row || a.column - b.column)};
    });
    plans.sort((a, b) => mode === "fit" ? b.scale - a.scale : a.pages - b.pages || (a.orientation === "portrait" ? -1 : 1));
    return plans[0];
  }

  function buildFloorTiles(facts, plan) {
    const chart = getChart();
    const versionView = isVersionView();
    const layout = chart.room.layout;
    const seats = new Map(chart.room.seats.map((seat) => [seat.id, layout.seats[seat.label]]));
    const holder = document.createElement("div");
    holder.className = `seating-print-tiles seating-print-floorplan${plan.mode === "fit" ? " seating-print-overview" : ""}`;
    const measure = document.createElement("div");
    measure.className = "seating-print-measure";
    Object.assign(measure.style, {position: "absolute", left: "-100000px", top: "0", visibility: "hidden", whiteSpace: "normal", overflowWrap: "anywhere", lineHeight: "1.05"});
    document.body.appendChild(measure);
    plan.tiles.forEach((part, index) => {
      const tile = document.createElement("section");
      tile.className = "seating-print-tile";
      const caption = document.createElement("p");
      caption.className = "seating-tile-caption";
      caption.textContent = `${chart.title} · ${chart.room_name} · ${scope(chart)}\n${plan.mode === "fit" ? `Whole room overview · ${versionView ? "exam versions and " : ""}seat labels` : `Page ${index + 1} of ${plan.pages} · row ${part.row + 1}, column ${part.column + 1} of the room plan · ${part.seats.size} seats`}`;
      tile.appendChild(caption);
      const crop = document.createElement("div");
      crop.className = "seating-print-crop";
      // An absolute child cannot establish its parent's height. A zero-height
      // overflow crop was the cause of the earlier blank floor-plan printouts.
      crop.style.width = `${plan.width}px`;
      crop.style.height = `${plan.height}px`;
      const copy = facts.grid.cloneNode(true);
      copy.removeAttribute("style");
      copy.removeAttribute("aria-label");
      copy.style.width = `${layout.width * plan.scale}px`;
      copy.style.height = `${layout.height * plan.scale}px`;
      copy.style.left = `${-part.x}px`;
      copy.style.top = `${-part.y}px`;
      copy.querySelectorAll("[data-seat]").forEach((node) => {
        if (!part.seats.has(node.dataset.seat)) { node.remove(); return; }
        const box = seats.get(node.dataset.seat);
        node.removeAttribute("style");
        node.removeAttribute("aria-expanded");
        node.style.left = `${box.x * plan.scale}px`;
        node.style.top = `${box.y * plan.scale}px`;
        node.style.width = `${box.width * plan.scale}px`;
        node.style.height = `${box.height * plan.scale}px`;
        // Name labels stay upright at the chair's measured position; the
        // architectural drawing retains the room and table orientation.
        if (plan.mode === "fit") {
          node.style.setProperty("--print-tag", `${Math.max(1, Math.min(8, box.width * plan.scale * (versionView ? 0.2 : 0.25), box.height * plan.scale * 0.28))}px`);
          node.style.setProperty("--print-version", `${Math.max(1, Math.min(22, box.height * plan.scale * 0.42, box.width * plan.scale * 0.6))}px`);
        }
        else if (node.querySelector(".seat-name")) {
          // A long name gets a modest font reduction instead of the screen's
          // two-line ellipsis. Measure real browser wrapping at the print size.
          const name = node.querySelector(".seat-name"), tag = node.querySelector(".seat-tag");
          measure.style.width = `${box.width * plan.scale - 6}px`;
          measure.style.fontWeight = "500"; measure.style.fontSize = "8px"; measure.textContent = tag.textContent;
          const available = box.height * plan.scale - 8 - measure.offsetHeight;
          measure.style.fontWeight = "600"; measure.textContent = name.textContent;
          let font = 10;
          measure.style.fontSize = `${font}px`;
          while (font > 8 && measure.offsetHeight > available) { font -= 0.5; measure.style.fontSize = `${font}px`; }
          name.style.fontSize = `${font}px`;
        }
      });
      crop.appendChild(copy);
      tile.appendChild(crop);
      holder.appendChild(tile);
    });
    measure.remove();
    return holder;
  }

  function columnBands(facts, perBand) {
    // Contiguous column ranges of at most perBand seats each; a table is never cut in two, so a band
    // ends before a table that would straddle its edge (or grows to hold a table wider than a band).
    const tables = [...facts.grid.querySelectorAll(".seat-table[data-column]")]
      .map((node) => [Number(node.dataset.column), Number(node.dataset.column) + Number(node.dataset.span || 1) - 1]).sort((a, b) => a[0] - b[0]);
    const bands = [];
    let start = facts.sticky + 1;
    while (start <= facts.columns) {
      let end = Math.min(facts.columns, start + perBand - 1);
      let settled = false;
      while (!settled) {
        settled = true;
        for (const [first, last] of tables) {
          if (first > start && first <= end && last > end) { end = first - 1; settled = false; }
          if (first === start && last > end) { end = Math.min(facts.columns, last); settled = false; }
        }
      }
      bands.push([start, end]);
      start = end + 1;
    }
    return bands.length ? bands : [[facts.sticky + 1, facts.columns]];
  }

  function buildTiles(facts, plan) {
    const chart = getChart();
    if (facts.floorplan) return buildFloorTiles(facts, plan);
    const holder = document.createElement("div");
    holder.className = "seating-print-tiles";
    const count = plan.bands.length;
    for (let band = 0; band < count; band += 1) {
      const tile = document.createElement("section");
      tile.className = "seating-print-tile";
      const caption = document.createElement("p");
      caption.className = "seating-tile-caption";
      caption.textContent = count > 1 ? `${chart.title} · ${chart.room_name} · part ${band + 1} of ${count}` : `${chart.title} · ${chart.room_name}`;
      tile.appendChild(caption);
      const copy = facts.grid.cloneNode(true);
      copy.removeAttribute("aria-label");
      copy.querySelectorAll('[aria-expanded]').forEach((node) => node.removeAttribute("aria-expanded"));
      {
        const [start, end] = plan.bands[band];
        copy.querySelectorAll("[data-column]").forEach((node) => {
          const column = Number(node.dataset.column), span = Number(node.dataset.span || 1);
          if (node.classList.contains("seat-rowlabel") && column === 1) {
            node.style.gridColumn = "1";
            return;
          }
          const first = Math.max(column, start), last = Math.min(column + span - 1, end);
          if (first > last) {
            node.remove();
            return;
          }
          const at = first - start + 1 + facts.sticky, width = last - first + 1;
          node.style.gridColumn = width > 1 ? `${at} / span ${width}` : String(at);
        });
        copy.style.gridTemplateColumns = `repeat(${facts.sticky + end - start + 1}, var(--seat))`;
        copy.style.setProperty("--seat", `${TILE_SEAT}px`);
        tile.appendChild(copy);
      }
      holder.appendChild(tile);
    }
    return holder;
  }

  function preparePrint(mode = "fit") {
    clearPrintTiles();
    const facts = gridFacts();
    if (!facts) return null;
    const plan = mode === "tiles" ? tilePlan(facts) : fitPlan(facts);
    if (plan.mode === "tiles" || facts.floorplan) {
      printTiles = buildTiles(facts, plan);
      facts.grid.closest(".room-chart-panel").appendChild(printTiles);
      document.body.classList.add("print-tiles");
      if (facts.floorplan) document.body.classList.add("print-floorplan");
    } else {
      const [, height] = PAPER[plan.orientation];
      document.querySelectorAll(".seating-view .seat-chart .seat-grid").forEach((item) => {
        item.style.setProperty("--print-height", `${Math.round((height - PRINT_HEADER - facts.extra) * 0.95)}px`);
      });
    }
    setPrintPaper(plan.orientation);
    return plan;
  }

  function clearPrintTiles() {
    printTiles?.remove();
    printTiles = null;
    printSection?.remove();
    printSection = null;
    printPacket?.remove();
    printPacket = null;
    document.body.classList.remove("print-tiles", "print-floorplan", "print-version-list", "print-room-roster", "print-packet");
    if (printSheet) {
      document.adoptedStyleSheets = document.adoptedStyleSheets.filter((sheet) => sheet !== printSheet);
      printSheet = null;
    }
  }

  // A portrait document of its own (the version list, the room roster): the previewed markup is printed as it stands.
  function printDocument(className, bodyClass, markup) {
    clearPrintTiles();
    printSection = document.createElement("section");
    printSection.className = className;
    printSection.innerHTML = markup;
    document.body.appendChild(printSection);
    document.body.classList.add(bodyClass);
    setPrintPaper("portrait");
    document.querySelector("#dialog").close();
    printing = true;
    try {
      window.print();
    } catch (cause) {
      printing = false;
      clearPrintTiles();
      toast(cause.message, true);
    }
  }

  let printing = false;
  window.addEventListener("beforeprint", () => {
    const chart = getChart();
    // Cmd+P or Ctrl+P without the dialog prints the one-page chart; the dialog's own print has already prepared the page.
    if (!printing && chart && !chart.missing && document.querySelector("#seating-section:not([hidden]) .seating-view")) preparePrint("fit");
  });
  window.addEventListener("afterprint", () => { printing = false; clearPrintTiles(); });

  /** The seat chart's print choices, shared by Print chart and the exam-day packet: one page (Fit) or readable tiles, and with exam
   * versions what the printed seats show. */
  function chartChoice(chart, facts) {
    const fit = fitPlan(facts), tiles = tilePlan(facts);
    const namesFit = !facts.floorplan && fit.seat - 6 > 34;
    // With exam versions, say what the printed seats show; the chart returns to the view on screen after printing.
    const versions = Boolean(chart.exam_version_count && setVersionView);
    const onScreen = isVersionView() ? "versions" : "names";
    const showChoice = versions ? `<fieldset class="seating-print-show"><legend>Show on the chart</legend><label class="checkbox-field"><input type="radio" name="show" value="names" ${onScreen === "names" ? "checked" : ""}>Student names</label><label class="checkbox-field"><input type="radio" name="show" value="versions" ${onScreen === "versions" ? "checked" : ""}>Exam versions</label></fieldset>` : "";
    const markup = `<label class="checkbox-field"><input type="radio" name="layout" value="fit" ${namesFit ? "checked" : ""}>Fit the whole room on one page${facts.floorplan ? " (overview with seat labels only)" : namesFit ? "" : " (seats too small for names; seat labels only)"}</label><label class="checkbox-field"><input type="radio" name="layout" value="tiles" ${namesFit ? "" : "checked"}>Readable, with names, across about ${tiles.pages} page${tiles.pages === 1 ? "" : "s"}${facts.floorplan ? " (whole seats on each page, ordered across the room and then down)" : tiles.bands.length > 1 ? ` (the room is cut into ${tiles.bands.length} column bands, row labels on each)` : ""}</label><p class="field-hint" data-print-paper></p>${showChoice}`;
    return { fit, tiles, versions, markup };
  }

  function bindPaperHint(form, choice, lead = "") {
    const paperHint = () => {
      const plan = form.elements.layout.value === "tiles" ? choice.tiles : choice.fit;
      form.querySelector("[data-print-paper]").textContent = `${lead}Use Letter paper, ${plan.orientation} orientation, at 100%. Chrome and Edge select this orientation automatically; choose it in Safari’s print dialog.`;
    };
    form.addEventListener("change", paperHint); paperHint();
  }

  /** Close the dialog, show the chart as chosen, lay it out for paper, add what `extra` prints before it, and print. */
  function startPrint(chart, choice, data, extra = null) {
    const mode = data.get("layout") === "tiles" ? "tiles" : "fit";
    const wanted = choice.versions ? data.get("show") === "versions" : isVersionView();
    const restore = isVersionView();
    document.querySelector("#dialog").close();
    if (wanted !== restore) {
      setVersionView(wanted);
      window.addEventListener("afterprint", () => { if (getChart()?.id === chart.id && isVersionView() === wanted) setVersionView(restore); }, { once: true });
    }
    printing = true;
    const plan = preparePrint(mode);
    extra?.(plan);
    window.print();
  }

  function openPrint() {
    const chart = getChart();
    const facts = gridFacts();
    if (!facts) {
      toast("Open a seating chart first.", true);
      return;
    }
    const choice = chartChoice(chart, facts);
    showDialog(`<form id="seating-print">${dialogHeader("Print the seating chart", escape(`${chart.title} · ${chart.room_name}`))}<div class="dialog-body">${choice.markup}<p class="form-error" role="alert" hidden></p></div><footer class="dialog-footer"><button type="button" class="button" data-action="close">Cancel</button><button type="submit" class="button primary">Print</button></footer></form>`);
    const form = document.querySelector("#seating-print");
    bindPaperHint(form, choice);
    form.onsubmit = (event) => {
      event.preventDefault();
      startPrint(chart, choice, new FormData(form));
    };
  }

  /**
   * One room's exam-day packet (AGENTS.md "Exam-day packet"): the cover, the proctor instructions and the room roster from
   * GET /api/exam-day/charts/<id>/packet, in portrait (the named page `packet-portrait` in seating.css), then this page's seat
   * chart laid out as Print chart lays it out, in its own orientation. Print reads the packet again and prints only the exact
   * preview; a changed packet (or a chart changed since this page read it) is shown and needs another click. Nothing is stored.
   */
  async function openPacketPrint() {
    const intended = captureDialogIntent(document.querySelector("#dialog"));
    const chart = getChart();
    if (!chart || chart.missing || !gridFacts()) {
      toast("Open a seating chart first.", true);
      return;
    }
    const id = chart.id;
    const endpoint = `/api/exam-day/charts/${encodeURIComponent(id)}/packet`;
    let current = await api(endpoint);
    if (chartId() !== id || !intended()) return;
    // The seat chart prints from this page: bring the page to the chart the packet was read with first.
    if (reloadChart && current.chart_version !== getChart()?.version) {
      await reloadChart();
      if (chartId() !== id || getChart()?.id !== id || !intended()) return;
    }
    const facts = gridFacts();
    if (!facts) return;
    const choice = chartChoice(getChart(), facts);
    showDialog(`<form id="seating-packet-print">${dialogHeader("Print exam-day packet", escape(`${current.title} · ${current.room_name}`))}<div class="dialog-body"><p class="field-hint">The cover, the proctor instructions and the room roster print in portrait; the seating chart follows them.</p><fieldset class="seating-packet-chart"><legend>Seating chart</legend>${choice.markup}</fieldset><p class="form-error" role="alert" hidden></p><div id="seating-packet-preview" class="seating-roster-preview seating-packet-preview" role="region" aria-label="Exam-day packet preview" tabindex="0"></div><p class="seating-packet-plan"><a class="text-button" href="${escape(current.plan_href)}">Edit proctors and instructions</a></p></div><footer class="dialog-footer"><button type="button" class="button" data-action="close">Cancel</button><button type="submit" class="button primary">Print</button></footer></form>`);
    const form = document.querySelector("#seating-packet-print");
    const active = dialogSession(form, document.querySelector("#dialog"));
    const preview = form.querySelector("#seating-packet-preview");
    preview.innerHTML = packetMarkup(current, escape);
    bindPaperHint(form, choice, "Seating chart: ");
    form.onsubmit = (event) => {
      event.preventDefault();
      const data = new FormData(form);
      void runFormAction(form, async () => {
        const updated = await api(endpoint);
        if (!active() || chartId() !== id) return;
        const changed = updated.fingerprint !== current.fingerprint;
        const moved = updated.chart_version !== getChart()?.version;
        current = updated;
        preview.innerHTML = packetMarkup(current, escape);
        if (moved && reloadChart) await reloadChart();
        if (!active() || chartId() !== id) return;
        if (changed || moved) {
          const error = form.querySelector('[role="alert"]');
          error.textContent = "The seating, roster, print jobs or exam-day plan changed. Review the updated packet, then click Print again.";
          error.hidden = false;
          return;
        }
        const printed = packetMarkup(current, escape, { print: true });
        try {
          startPrint(getChart(), choice, data, (plan) => {
            if (plan) setPrintPaper(plan.orientation, true);
            printPacket = document.createElement("section");
            printPacket.className = "seating-exam-packet";
            printPacket.innerHTML = printed;
            document.body.prepend(printPacket);
            document.body.classList.add("print-packet");
          });
        } catch (cause) {
          printing = false;
          clearPrintTiles();
          toast(cause.message, true);
        }
      });
    };
  }

  return {
    openPrint, openVersionPrint, openRoomRosterPrint, openPacketPrint,
    reset() { printing = false; clearPrintTiles(); },
  };
}
